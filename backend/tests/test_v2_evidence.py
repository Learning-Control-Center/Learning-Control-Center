from __future__ import annotations

import copy
import json

import pytest
from app import import_export as transfer
from app.compatibility.v1.evidence_backfill import result_rows_hash
from app.errors import AppError
from app.import_export import _portable_payload, _validate_portable_payload
from app.models import (
    Evidence,
    EvidenceLink,
    EvidenceLinkRetraction,
    EvidenceRedaction,
    EvidenceRetraction,
    LearningSession,
    ProjectionInvalidation,
    VerificationEvidence,
    VerificationRecord,
)
from httpx import AsyncClient
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session


def _competencies(roadmap: dict[str, object]) -> list[dict[str, object]]:
    return roadmap["phases"][0]["tracks"][0]["competencies"]


async def _create_manual_session(client: AsyncClient, csrf: str, competency_id: str) -> str:
    response = await client.post(
        "/api/v1/sessions/manual",
        json={
            "competency_identity_id": competency_id,
            "activity_type": "coding",
            "assistance_mode": "ai_hint",
            "started_at": "2026-09-10T10:00:00Z",
            "duration_ms": 600_000,
            "outcome": "completed",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def test_one_session_occurrence_has_many_links_without_multiplying_evidence(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, roadmap = configured_client
    competencies = _competencies(roadmap)
    session_id = await _create_manual_session(client, csrf, str(competencies[0]["identityId"]))
    evidence = db.scalar(
        select(Evidence).where(
            Evidence.source_type == "learning_session", Evidence.source_id == session_id
        )
    )
    assert evidence is not None
    assert evidence.independence == "assisted"
    assert evidence.strength == "unknown"
    assert evidence.source_confidence == "unknown"
    assert (
        db.scalar(
            select(func.count()).select_from(Evidence).where(Evidence.source_id == session_id)
        )
        == 1
    )
    evidence_constraints = {
        item["name"] for item in inspect(db.get_bind()).get_unique_constraints("evidence")
    }
    assert "uq_evidence_supersedes" in evidence_constraints

    added = await client.post(
        f"/api/v2/sessions/{session_id}/contributions",
        json={
            "competency_identity_id": competencies[1]["identityId"],
            "relevance": "secondary",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert added.status_code == 201, added.text
    assert (
        db.scalar(
            select(func.count()).select_from(Evidence).where(Evidence.source_id == session_id)
        )
        == 1
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(EvidenceLink)
            .where(EvidenceLink.evidence_id == evidence.id)
        )
        == 2
    )

    retracted = await client.delete(
        f"/api/v2/sessions/{session_id}/contributions/{added.json()['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert retracted.status_code == 200, retracted.text
    assert db.scalar(select(func.count()).select_from(EvidenceLinkRetraction)) == 1


async def test_session_correction_and_tombstone_preserve_evidence_history(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, roadmap = configured_client
    session_id = await _create_manual_session(
        client, csrf, str(_competencies(roadmap)[0]["identityId"])
    )
    original = db.scalar(
        select(Evidence).where(
            Evidence.source_type == "learning_session", Evidence.source_id == session_id
        )
    )
    assert original is not None
    corrected = await client.patch(
        f"/api/v1/sessions/{session_id}",
        json={"duration_ms": 900_000},
        headers={"X-CSRF-Token": csrf},
    )
    assert corrected.status_code == 200, corrected.text
    replacement = db.scalar(select(Evidence).where(Evidence.supersedes_evidence_id == original.id))
    assert replacement is not None
    original_retraction = db.scalar(
        select(EvidenceRetraction).where(EvidenceRetraction.evidence_id == original.id)
    )
    assert original_retraction is not None
    assert original_retraction.replacement_evidence_id == replacement.id

    deleted = await client.delete(
        f"/api/v1/sessions/{session_id}?confirm=true",
        headers={"X-CSRF-Token": csrf},
    )
    assert deleted.status_code == 200
    assert db.get(LearningSession, session_id) is not None
    assert db.get(Evidence, original.id) is not None
    assert (
        db.scalar(
            select(EvidenceRetraction.id).where(EvidenceRetraction.evidence_id == replacement.id)
        )
        is not None
    )


async def test_verification_application_service_creates_result_and_context_evidence(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, roadmap = configured_client
    competency_id = str(_competencies(roadmap)[0]["identityId"])
    response = await client.post(
        "/api/v2/verification-attempts",
        json={
            "competency_identity_id": competency_id,
            "verification_source": "self",
            "method": "Independent exercise",
            "result": "failed",
            "confidence": 5,
            "evidence": [
                {
                    "kind": "repository",
                    "reference": "local://exercise",
                    "description": "Preserved context",
                }
            ],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    record_id = str(response.json()["id"])
    record = db.get(VerificationRecord, record_id)
    assert record is not None and record.result == "failed"
    attachment = db.scalar(
        select(VerificationEvidence).where(VerificationEvidence.verification_record_id == record_id)
    )
    assert attachment is not None
    result = db.scalar(
        select(Evidence).where(
            Evidence.source_type == "verification_record", Evidence.source_id == record_id
        )
    )
    context = db.scalar(
        select(Evidence).where(
            Evidence.source_type == "verification_evidence",
            Evidence.source_id == attachment.id,
        )
    )
    assert result is not None and context is not None
    assert result.occurred_at is None and result.occurred_at_unknown_reason == "source_unspecified"
    assert result.authoritative_for_downgrade is False
    assert context.external_reference == "local://exercise"
    assert response.json()["resultEvidence"]["links"][0]["effect"] == "contradicts"
    assert (
        db.scalar(
            select(func.count())
            .select_from(ProjectionInvalidation)
            .where(ProjectionInvalidation.projection_kind == "capability")
        )
        == 2
    )
    for evidence_id in (result.id, context.id):
        redacted = await client.post(
            f"/api/v2/evidence/{evidence_id}/redact",
            json={
                "reason": "Remove verification payload from portable output",
                "redacted_fields": ["description", "external_reference"],
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert redacted.status_code == 200
    repeated_redaction = await client.post(
        f"/api/v2/evidence/{context.id}/redact",
        json={
            "reason": "Repeated redaction",
            "redacted_fields": ["description"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert repeated_redaction.status_code == 200
    portable = _portable_payload(db)
    portable_context = next(
        row for row in portable["tables"]["evidence"] if row["id"] == context.id
    )
    portable_source = next(
        row for row in portable["tables"]["verification_evidence"] if row["id"] == attachment.id
    )
    assert portable_context["description"] is None
    assert portable_context["external_reference"] is None
    assert portable_source["description"] == ""
    assert portable_source["reference"] == "[redacted]"
    db.delete(attachment)
    db.commit()
    assert db.get(Evidence, context.id) is not None
    _validate_portable_payload(_portable_payload(db), "deleted-source-retained", schema_version=3)


async def test_verification_import_uses_same_evidence_path_and_records_package_provenance(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, roadmap = configured_client
    competency_id = str(_competencies(roadmap)[0]["identityId"])
    package = {
        "schemaVersion": 1,
        "packageType": "verification_update",
        "packageId": "verification-evidence-import-1",
        "appVersion": "1.0.0",
        "createdAt": "2026-09-10T10:00:00.000Z",
        "payload": {
            "verifications": [
                {
                    "competency_identity_id": competency_id,
                    "verification_source": "external",
                    "method": "Reviewed exercise",
                    "result": "passed",
                    "evidence": [
                        {
                            "kind": "repository",
                            "reference": "local://reviewed",
                            "description": "Reviewer context",
                        }
                    ],
                }
            ]
        },
    }
    preview = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "verification.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )
    assert preview.status_code == 200, preview.text
    assert db.scalar(select(func.count()).select_from(VerificationRecord)) == 0
    applied = await client.post(
        "/api/v1/import-export/import/apply",
        json={
            "filename": "verification.json",
            "package": package,
            "confirmation_token": preview.json()["confirmationToken"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert applied.status_code == 200, applied.text
    result = db.scalar(
        select(Evidence).where(
            Evidence.source_type == "verification_record", Evidence.source_role == "result"
        )
    )
    assert result is not None
    provenance = json.loads(result.provenance_json)
    assert provenance["origin_kind"] == "import"
    assert provenance["import_package_id"] == package["packageId"]
    assert db.scalar(select(func.count()).select_from(VerificationRecord)) == 1
    assert db.scalar(select(func.count()).select_from(Evidence)) == 2


async def test_verification_import_rolls_back_source_and_evidence_on_final_validation_failure(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, csrf, roadmap = configured_client
    package = {
        "schemaVersion": 1,
        "packageType": "verification_update",
        "packageId": "verification-evidence-rollback",
        "appVersion": "1.0.0",
        "createdAt": "2026-09-10T10:00:00.000Z",
        "payload": {
            "verifications": [
                {
                    "competency_identity_id": _competencies(roadmap)[0]["identityId"],
                    "verification_source": "self",
                    "method": "Rollback exercise",
                    "result": "partial",
                }
            ]
        },
    }
    preview = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "rollback.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )
    assert preview.status_code == 200, preview.text
    original_validate = transfer.validate_domain_integrity
    calls = 0

    def fail_final_validation(connection: object) -> None:
        nonlocal calls
        calls += 1
        original_validate(connection)
        if calls == 2:
            raise AppError(422, "INJECTED_FAILURE", "Injected final validation failure.")

    monkeypatch.setattr(transfer, "validate_domain_integrity", fail_final_validation)
    applied = await client.post(
        "/api/v1/import-export/import/apply",
        json={
            "filename": "rollback.json",
            "package": package,
            "confirmation_token": preview.json()["confirmationToken"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert applied.status_code == 422
    assert applied.json()["error"]["code"] == "INJECTED_FAILURE"
    assert db.scalar(select(func.count()).select_from(VerificationRecord)) == 0
    assert db.scalar(select(func.count()).select_from(Evidence)) == 0


async def test_native_evidence_enforces_unknowns_authority_idempotency_and_redaction(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, roadmap = configured_client
    competency_id = str(_competencies(roadmap)[0]["identityId"])
    payload = {
        "idempotency_key": "manual-evidence-0001",
        "evidence_type": "code",
        "title": "Local implementation",
        "description": "Sensitive detail",
        "strength": "unknown",
        "strength_unknown_reason": "user_unspecified",
        "independence": "unknown",
        "independence_unknown_reason": "user_unspecified",
        "occurred_at_unknown_reason": "user_unspecified",
        "capture_method": "manual entry",
        "external_reference": "local://private",
        "links": [{"competency_identity_id": competency_id}],
    }
    created = await client.post("/api/v2/evidence", json=payload, headers={"X-CSRF-Token": csrf})
    assert created.status_code == 201, created.text
    evidence_id = str(created.json()["id"])
    assert created.json()["sourceConfidence"] == "low"
    assert created.json()["authoritativeForDowngrade"] is False
    repeated = await client.post("/api/v2/evidence", json=payload, headers={"X-CSRF-Token": csrf})
    assert repeated.status_code == 201
    assert repeated.json()["id"] == evidence_id
    conflict_payload = {**payload, "title": "Different fact"}
    conflict = await client.post(
        "/api/v2/evidence", json=conflict_payload, headers={"X-CSRF-Token": csrf}
    )
    assert conflict.status_code == 409
    asserted_authority = await client.post(
        "/api/v2/evidence",
        json={**payload, "idempotency_key": "manual-evidence-0002", "source_confidence": "high"},
        headers={"X-CSRF-Token": csrf},
    )
    assert asserted_authority.status_code == 422

    redacted = await client.post(
        f"/api/v2/evidence/{evidence_id}/redact",
        json={
            "reason": "Remove sensitive payload from ordinary reads",
            "redacted_fields": ["description", "external_reference"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert redacted.status_code == 200
    read = await client.get(f"/api/v2/evidence/{evidence_id}")
    assert read.json()["description"] is None
    assert read.json()["externalReference"] is None
    assert db.get(Evidence, evidence_id) is not None
    assert db.scalar(select(func.count()).select_from(EvidenceRedaction)) == 1
    assert "Sensitive detail" not in read.text
    assert "Sensitive detail" not in json.dumps(_portable_payload(db))
    stored_provenance = json.loads(db.get(Evidence, evidence_id).provenance_json)
    assert "command_payload" not in stored_provenance
    assert len(stored_provenance["command_hash"]) == 64

    credential_reference = await client.post(
        "/api/v2/evidence",
        json={
            **payload,
            "idempotency_key": "manual-evidence-0003",
            "external_reference": "https://example.test/artifact?token=secret",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert credential_reference.status_code == 422


async def test_native_evidence_link_and_lifecycle_commands_are_append_only(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, roadmap = configured_client
    competencies = _competencies(roadmap)
    base = {
        "evidence_type": "manual",
        "title": "Observed exercise",
        "strength": "moderate",
        "independence": "independent",
        "occurred_at": "2026-09-10T10:00:00Z",
        "capture_method": "manual entry",
        "links": [{"competency_identity_id": competencies[0]["identityId"]}],
    }
    created = await client.post(
        "/api/v2/evidence",
        json={**base, "idempotency_key": "evidence-lifecycle-0001"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    evidence_id = str(created.json()["id"])
    added_link = await client.post(
        f"/api/v2/evidence/{evidence_id}/links",
        json={
            "idempotency_key": "evidence-link-command-0001",
            "competency_identity_id": competencies[1]["identityId"],
            "effect": "context_only",
            "relevance": "supporting",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert added_link.status_code == 201, added_link.text
    link_id = str(added_link.json()["id"])
    repeated_link = await client.post(
        f"/api/v2/evidence/{evidence_id}/links",
        json={
            "idempotency_key": "evidence-link-command-0001",
            "competency_identity_id": competencies[1]["identityId"],
            "effect": "context_only",
            "relevance": "supporting",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert repeated_link.status_code == 201
    assert repeated_link.json()["id"] == link_id
    conflicting_link = await client.post(
        f"/api/v2/evidence/{evidence_id}/links",
        json={
            "idempotency_key": "evidence-link-command-0001",
            "competency_identity_id": competencies[0]["identityId"],
            "effect": "context_only",
            "relevance": "supporting",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert conflicting_link.status_code == 409
    assert conflicting_link.json()["error"]["code"] == "EVIDENCE_LINK_IDEMPOTENCY_CONFLICT"
    link_retraction = await client.post(
        f"/api/v2/evidence-links/{link_id}/retract",
        json={"reason": "Attribution was incorrect"},
        headers={"X-CSRF-Token": csrf},
    )
    assert link_retraction.status_code == 200
    assert db.scalar(select(func.count()).select_from(EvidenceLinkRetraction)) == 1

    replacement_payload = {
        **base,
        "idempotency_key": "evidence-lifecycle-0002",
        "title": "Corrected observed exercise",
    }
    superseded = await client.post(
        f"/api/v2/evidence/{evidence_id}/supersede",
        json=replacement_payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert superseded.status_code == 201, superseded.text
    replacement_id = str(superseded.json()["id"])
    assert superseded.json()["supersedesEvidenceId"] == evidence_id
    assert (
        db.scalar(
            select(EvidenceRetraction.replacement_evidence_id).where(
                EvidenceRetraction.evidence_id == evidence_id
            )
        )
        == replacement_id
    )
    replayed_supersession = await client.post(
        f"/api/v2/evidence/{evidence_id}/supersede",
        json=replacement_payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert replayed_supersession.status_code == 201
    assert replayed_supersession.json()["id"] == replacement_id
    fork = await client.post(
        f"/api/v2/evidence/{evidence_id}/supersede",
        json={
            **replacement_payload,
            "idempotency_key": "evidence-lifecycle-0003",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert fork.status_code == 409
    assert fork.json()["error"]["code"] == "EVIDENCE_ALREADY_SUPERSEDED"
    assert (
        db.scalar(
            select(func.count())
            .select_from(Evidence)
            .where(Evidence.supersedes_evidence_id == evidence_id)
        )
        == 1
    )

    invalidated = await client.post(
        f"/api/v2/evidence/{replacement_id}/invalidate",
        json={"reason": "Artifact could not be reproduced"},
        headers={"X-CSRF-Token": csrf},
    )
    assert invalidated.status_code == 200
    assert (
        await client.post(
            f"/api/v2/evidence/{replacement_id}/invalidate",
            json={"reason": "Repeated command"},
            headers={"X-CSRF-Token": csrf},
        )
    ).status_code == 200
    retracted = await client.post(
        f"/api/v2/evidence/{replacement_id}/retract",
        json={"reason": "User withdrew the record"},
        headers={"X-CSRF-Token": csrf},
    )
    assert retracted.status_code == 200
    listing = await client.get(
        f"/api/v2/evidence?competency_identity_id={competencies[0]['identityId']}"
    )
    assert listing.status_code == 200
    by_id = {item["id"]: item for item in listing.json()["items"]}
    assert by_id[evidence_id]["retracted"] is True
    assert by_id[replacement_id]["invalidated"] is True

    project = await client.post(
        "/api/v2/evidence",
        json={
            **base,
            "idempotency_key": "project-evidence-0001",
            "evidence_type": "project",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert project.status_code == 422
    assert project.json()["error"]["code"] == "FEATURE_NOT_AVAILABLE"
    invalid_scope = await client.post(
        f"/api/v2/evidence/{replacement_id}/links",
        json={
            "idempotency_key": "invalid-evidence-link-0001",
            "competency_identity_id": competencies[0]["identityId"],
            "criterion_definition_id": "missing-definition",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert invalid_scope.status_code == 422

    semantic = await client.post(
        f"/api/v2/competencies/{competencies[0]['identityId']}/definitions",
        json={
            "title": "Evidence scope test",
            "description": "Definition used to test exact EvidenceLink scope.",
            "scope": "Bounded test scope.",
            "scale_stable_key": "technical",
            "scale_version": "v1",
            "dimension_keys": [],
            "effective_at": "2026-09-10T00:00:00Z",
            "creation_source": "user",
            "criteria": [
                {
                    "stable_key": "evidence.scope.test",
                    "level_stable_key": "independent",
                    "dimension_key": None,
                    "requirement_type": "required",
                    "demonstration_rule": "independent_performance",
                    "description": "Demonstrate the scoped behavior.",
                }
            ],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert semantic.status_code == 201, semantic.text
    definition = semantic.json()["criteria"][0]
    mismatched_definition_scope = await client.post(
        f"/api/v2/evidence/{replacement_id}/links",
        json={
            "idempotency_key": "invalid-evidence-link-0002",
            "competency_identity_id": competencies[0]["identityId"],
            "criterion_identity_id": definition["identityId"],
            "criterion_definition_id": definition["id"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert mismatched_definition_scope.status_code == 422


async def test_portable_evidence_history_round_trips_and_rejects_lineage_tampering(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, roadmap = configured_client
    session_id = await _create_manual_session(
        client, csrf, str(_competencies(roadmap)[0]["identityId"])
    )
    updated = await client.patch(
        f"/api/v1/sessions/{session_id}",
        json={"duration_ms": 900_000},
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200
    portable = _portable_payload(db)
    tables, _summary = _validate_portable_payload(portable, "evidence-round-trip", schema_version=3)
    assert len(tables["evidence"]) == 2
    assert len(tables["evidence_retractions"]) == 1
    assert json.loads(tables["evidence"][0]["provenance_json"])["origin_kind"] == "local"

    tampered = copy.deepcopy(portable)
    run = next(
        row
        for row in tampered["tables"]["migration_backfill_runs"]
        if row["source_kind"] == "v1_evidence_sources"
    )
    run["result_hash"] = "0" * 64
    with pytest.raises(AppError, match="Evidence backfill lineage"):
        _validate_portable_payload(tampered, "evidence-tampered", schema_version=3)
    invalid_policy = copy.deepcopy(portable)
    invalid_policy["tables"]["evidence"][0]["policy_version"] = "future-policy/v99"
    with pytest.raises(AppError, match="Evidence provenance is inconsistent"):
        _validate_portable_payload(invalid_policy, "evidence-policy-tampered", schema_version=3)
    invalid_source = copy.deepcopy(portable)
    provenance = json.loads(invalid_source["tables"]["evidence"][0]["provenance_json"])
    provenance["source_record_id"] = "different-source"
    invalid_source["tables"]["evidence"][0]["provenance_json"] = json.dumps(provenance)
    with pytest.raises(AppError, match="Evidence provenance is inconsistent"):
        _validate_portable_payload(invalid_source, "evidence-source-tampered", schema_version=3)
    invalid_origin = copy.deepcopy(portable)
    provenance = json.loads(invalid_origin["tables"]["evidence"][0]["provenance_json"])
    provenance["origin_kind"] = "import"
    provenance.pop("import_package_id", None)
    invalid_origin["tables"]["evidence"][0]["provenance_json"] = json.dumps(provenance)
    with pytest.raises(AppError, match="Evidence provenance is inconsistent"):
        _validate_portable_payload(invalid_origin, "evidence-origin-tampered", schema_version=3)
    unsafe_reference = copy.deepcopy(portable)
    unsafe_reference["tables"]["evidence"][0]["external_reference"] = (
        "https://example.test/result?access_token=secret"
    )
    with pytest.raises(AppError, match="credential-bearing external reference"):
        _validate_portable_payload(unsafe_reference, "unsafe-evidence-reference", schema_version=3)
    invalid_link_provenance = copy.deepcopy(portable)
    invalid_link_provenance["tables"]["evidence_links"][0]["provenance_json"] = "{}"
    with pytest.raises(AppError, match="EvidenceLink is inconsistent"):
        _validate_portable_payload(
            invalid_link_provenance, "evidence-link-provenance-tampered", schema_version=3
        )


def test_evidence_backfill_audit_hash_ignores_only_redactable_payload() -> None:
    row = {
        "id": "evidence-1",
        "evidence_type": "verification",
        "description": "private",
        "external_reference": "local://private",
        "strength": "unknown",
    }
    redacted = {**row, "description": None, "external_reference": None}
    tampered = {**redacted, "strength": "strong"}
    assert result_rows_hash([row]) == result_rows_hash([redacted])
    assert result_rows_hash([row]) != result_rows_hash([tampered])


async def test_evidence_missing_resource_commands_return_not_found(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
) -> None:
    client, csrf, _roadmap = configured_client
    headers = {"X-CSRF-Token": csrf}
    assert (await client.get("/api/v2/evidence/missing")).status_code == 404
    assert (
        await client.post(
            "/api/v2/evidence/missing/retract", json={"reason": "missing"}, headers=headers
        )
    ).status_code == 404
    assert (
        await client.post(
            "/api/v2/evidence/missing/invalidate", json={"reason": "missing"}, headers=headers
        )
    ).status_code == 404
    assert (
        await client.post(
            "/api/v2/evidence/missing/redact",
            json={"reason": "missing", "redacted_fields": ["description"]},
            headers=headers,
        )
    ).status_code == 404
    assert (
        await client.post(
            "/api/v2/evidence-links/missing/retract",
            json={"reason": "missing"},
            headers=headers,
        )
    ).status_code == 404
