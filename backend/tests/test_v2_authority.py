from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest
from app.analysis.v3.service import run_analysis, snapshot_detail
from app.authority import service as authority_service
from app.authority.models import LearningControlAuthorityEvent, LearningControlAuthorityState
from app.capability import evaluate_capability
from app.determinism import canonical_json, content_hash
from app.domain_integrity import validate_domain_integrity
from app.errors import AppError
from app.import_export import (
    _apply_portable_restore,
    _portable_payload,
    _validate_portable_payload,
)
from app.models import (
    CriterionDefinition,
    CriterionIdentity,
    Evidence,
    EvidenceLink,
    OperationalBackup,
    RecommendationSnapshot,
    SemanticCompetencyDefinition,
)
from app.portability.registry import (
    PORTABLE_V8_MANIFEST,
    PORTABLE_V9_AUTHORITY_TABLES,
    upgrade_v8_to_v9_tables,
)
from app.time_utils import utc_now_ms
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session


def test_authority_bootstraps_legacy_and_readiness_is_explicit(db: Session) -> None:
    state = authority_service.authority_state(db)
    assert state == {
        "canonicalLearningAuthority": "legacy_v1",
        "recommendationPresentation": "legacy_v1",
        "roadmapPresentation": "legacy_v1",
        "todayPresentation": "legacy_v1",
        "eventSequence": 1,
        "stateHash": content_hash(
            {
                "canonicalLearningAuthority": "legacy_v1",
                "recommendationPresentation": "legacy_v1",
                "roadmapPresentation": "legacy_v1",
                "todayPresentation": "legacy_v1",
            }
        ),
        "updatedAt": "1970-01-01T00:00:00.000Z",
        "policyVersion": "learning-control-authority-policy/v1",
    }
    report = authority_service.readiness_report(db)
    assert report["ready"] is False
    assert {item["code"] for item in report["checks"]} == {
        "ACTIVE_TARGET_PROFILE",
        "ACTIVE_LEARNING_GRAPH",
        "ACTIVE_CURRICULUM",
        "ROADMAP_PROJECTION",
        "ANALYSIS_V3_CURRENT",
        "RECOMMENDATION_POLICY_APPROVED",
        "PORTABLE_SCHEMA_V9",
        "DOMAIN_INTEGRITY",
    }
    assert (
        next(item for item in report["checks"] if item["code"] == "PORTABLE_SCHEMA_V9")["ready"]
        is True
    )
    assert next(
        item for item in report["checks"] if item["code"] == "RECOMMENDATION_POLICY_APPROVED"
    ) == {
        "code": "RECOMMENDATION_POLICY_APPROVED",
        "ready": True,
        "detail": "recommendation-policy-registry/v2",
    }


def test_authority_readiness_rejects_a_persisted_analysis_pointer_that_is_stale(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        authority_service,
        "current_analysis",
        lambda _db, *, purpose: {
            "configured": True,
            "status": "stale",
            "validity": {
                "sourceGeneration": 10,
                "currentSourceGeneration": 11,
                "policyBundleHash": "stale",
                "expectedCompletedThroughDate": "2026-09-18",
            },
            "snapshot": {"id": "stale-snapshot", "completeness": "complete"},
        },
    )
    report = authority_service.readiness_report(db)
    check = next(item for item in report["checks"] if item["code"] == "ANALYSIS_V3_CURRENT")
    assert check == {
        "code": "ANALYSIS_V3_CURRENT",
        "ready": False,
        "detail": {
            "snapshotId": "stale-snapshot",
            "completeness": "complete",
            "unknownCount": 0,
        },
    }


async def test_activation_is_backed_up_monotonic_and_contracts_legacy_writes(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, csrf, roadmap = configured_client
    not_ready = await client.post(
        "/api/v2/authority/activate-v2",
        json={"idempotency_key": "authority-not-ready", "reason": "Readiness proof"},
        headers={"X-CSRF-Token": csrf},
    )
    assert not_ready.status_code == 409
    assert not_ready.json()["error"]["code"] == "AUTHORITY_NOT_READY"
    assert db.scalar(select(func.count(OperationalBackup.id))) == 0
    assert db.scalar(select(func.count(LearningControlAuthorityEvent.id))) == 1

    surface_before_activation = await client.put(
        "/api/v2/authority/surfaces",
        json={
            "idempotency_key": "surface-before-v2",
            "roadmap_presentation": "v1_read_only",
            "recommendation_presentation": "v1_read_only",
            "today_presentation": "v1_read_only",
            "reason": "Should be refused",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert surface_before_activation.status_code == 409
    assert surface_before_activation.json()["error"]["code"] == "AUTHORITY_V2_NOT_ACTIVE"

    monkeypatch.setattr(
        authority_service,
        "readiness_report",
        lambda _db: {
            "ready": True,
            "checks": [],
            "authorityPolicyVersion": authority_service.AUTHORITY_POLICY_VERSION,
        },
    )
    activation = {
        "idempotency_key": "activate-v2-authority",
        "reason": "Validated Checkpoint 7 readiness",
    }
    activated = await client.post(
        "/api/v2/authority/activate-v2",
        json=activation,
        headers={"X-CSRF-Token": csrf},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["canonicalLearningAuthority"] == "v2"
    assert activated.json()["recommendationPresentation"] == "v2"
    backups = db.scalars(select(OperationalBackup)).all()
    assert len(backups) == 1
    assert backups[0].purpose == "pre-v2-authority-activation"
    assert Path(backups[0].path).parent == tmp_path / "backups"
    assert Path(backups[0].path).is_file()
    backup_connection = sqlite3.connect(backups[0].path)
    try:
        assert backup_connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert backup_connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        backup_connection.close()

    repeated = await client.post(
        "/api/v2/authority/activate-v2",
        json=activation,
        headers={"X-CSRF-Token": csrf},
    )
    assert repeated.status_code == 200
    assert db.scalar(select(func.count(OperationalBackup.id))) == 1
    assert db.scalar(select(func.count(LearningControlAuthorityEvent.id))) == 2
    conflict = await client.post(
        "/api/v2/authority/activate-v2",
        json={**activation, "reason": "Different command payload"},
        headers={"X-CSRF-Token": csrf},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "AUTHORITY_IDEMPOTENCY_CONFLICT"

    fallback = await client.put(
        "/api/v2/authority/surfaces",
        json={
            "idempotency_key": "read-only-fallback",
            "roadmap_presentation": "v1_read_only",
            "recommendation_presentation": "v1_read_only",
            "today_presentation": "v1_read_only",
            "reason": "Temporary labeled presentation fallback",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert fallback.status_code == 200, fallback.text
    assert fallback.json()["canonicalLearningAuthority"] == "v2"
    assert fallback.json()["roadmapPresentation"] == "v1_read_only"
    assert fallback.json()["recommendationPresentation"] == "v1_read_only"

    current_legacy = await client.get("/api/v1/roadmap/current")
    assert current_legacy.status_code == 200
    assert current_legacy.json()["roadmap"]["id"] == roadmap["id"]
    phase_id = current_legacy.json()["roadmap"]["phases"][0]["id"]
    blocked_roadmap = await client.put(
        f"/api/v1/roadmap/current-phase/{phase_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert blocked_roadmap.status_code == 409
    assert blocked_roadmap.json()["error"]["code"] == "LEGACY_AUTHORITY_READ_ONLY"
    blocked_today = await client.get("/api/v1/recommendations/today")
    assert blocked_today.status_code == 409
    assert blocked_today.json()["error"]["code"] == "LEGACY_AUTHORITY_READ_ONLY"
    assert (await client.get("/api/v1/recommendations/history")).status_code == 200
    assert (await client.get("/api/v2/today/current")).status_code == 200


async def test_real_readiness_activates_v2_and_closes_legacy_writes(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
    db: Session,
) -> None:
    client, csrf, roadmap = configured_client
    phases = roadmap["phases"]
    assert isinstance(phases, list)
    first_phase = phases[0]
    assert isinstance(first_phase, dict)
    competency = await client.post(
        "/api/v2/competencies",
        json={"stable_key": "authority.ready", "creation_source": "test"},
        headers={"X-CSRF-Token": csrf},
    )
    assert competency.status_code == 201, competency.text
    competency_id = str(competency.json()["id"])
    definition = await client.post(
        f"/api/v2/competencies/{competency_id}/definitions",
        json={
            "title": "Authority readiness competency",
            "description": "Real readiness fixture",
            "scope": "Explicit cutover competency",
            "scale_stable_key": "technical",
            "scale_version": "v1",
            "dimension_keys": [],
            "effective_at": "2026-01-01T00:00:00Z",
            "creation_source": "test",
            "criteria": [
                {
                    "stable_key": "authority.ready.independent",
                    "level_stable_key": "independent",
                    "dimension_key": None,
                    "requirement_type": "required",
                    "demonstration_rule": "independent_performance",
                    "description": "Demonstrate the readiness competency independently.",
                }
            ],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert definition.status_code == 201, definition.text
    definition_activation = await client.post(
        f"/api/v2/competencies/{competency_id}/definitions/{definition.json()['id']}/activate",
        json={
            "reason": "Authority readiness",
            "source": "test",
            "idempotency_key": "authority-definition-active",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert definition_activation.status_code == 200, definition_activation.text

    profile = await client.post(
        "/api/v2/target-profiles",
        json={
            "stable_key": "authority-ready-profile",
            "creation_source": "test",
            "version": {
                "title": "Authority readiness profile",
                "description": "Real cutover readiness fixture",
                "creation_source": "test",
                "effective_at": "2026-01-01T00:00:00Z",
                "domains": [
                    {
                        "stable_key": "learning",
                        "title": "Learning",
                        "minimum_percent": 100,
                        "maximum_percent": 100,
                        "order_index": 0,
                    }
                ],
                "targets": [
                    {
                        "stable_key": "authority-target",
                        "competency_identity_id": competency_id,
                        "dimension_key": None,
                        "domain_stable_key": "learning",
                        "scale_stable_key": "technical",
                        "scale_version": "v1",
                        "target_level_stable_key": "independent",
                        "priority": "core",
                    }
                ],
                "milestones": [],
                "readiness_gates": [],
            },
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert profile.status_code == 201, profile.text
    profile_payload = profile.json()
    profile_activation = await client.post(
        f"/api/v2/target-profiles/{profile_payload['profileId']}/versions/"
        f"{profile_payload['versionId']}/activate",
        json={
            "reason": "Authority readiness",
            "source": "test",
            "idempotency_key": "authority-profile-active",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert profile_activation.status_code == 200, profile_activation.text

    graph = await client.post(
        "/api/v2/learning-graphs",
        json={"stable_key": "authority-ready-graph", "creation_source": "test"},
        headers={"X-CSRF-Token": csrf},
    )
    assert graph.status_code == 201, graph.text
    graph_version = await client.post(
        f"/api/v2/learning-graphs/{graph.json()['id']}/versions",
        json={
            "title": "Authority graph",
            "description": "No prerequisite edges required",
            "effective_at": "2026-01-01T00:00:00Z",
            "creation_source": "test",
            "edges": [],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert graph_version.status_code == 201, graph_version.text
    graph_activation = await client.post(
        f"/api/v2/learning-graphs/{graph.json()['id']}/versions/"
        f"{graph_version.json()['id']}/activate",
        json={
            "reason": "Authority readiness",
            "source": "test",
            "idempotency_key": "authority-graph-active",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert graph_activation.status_code == 201, graph_activation.text

    curriculum = await client.post(
        "/api/v2/curricula",
        json={"stable_key": "authority-ready-curriculum", "creation_source": "test"},
        headers={"X-CSRF-Token": csrf},
    )
    assert curriculum.status_code == 201, curriculum.text
    curriculum_version = await client.post(
        f"/api/v2/curricula/{curriculum.json()['id']}/versions",
        json={
            "title": "Authority curriculum",
            "description": "Empty authored catalog is an explicit canonical version",
            "effective_at": "2026-01-01T00:00:00Z",
            "creation_source": "test",
            "objectives": [],
            "units": [],
            "assessment_rubrics": [],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert curriculum_version.status_code == 201, curriculum_version.text
    curriculum_activation = await client.post(
        f"/api/v2/curricula/{curriculum.json()['id']}/versions/"
        f"{curriculum_version.json()['id']}/activate",
        json={
            "reason": "Authority readiness",
            "source": "test",
            "idempotency_key": "authority-curriculum-active",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert curriculum_activation.status_code == 200, curriculum_activation.text

    discipline = await client.get("/api/v1/settings/discipline")
    assert discipline.status_code == 200, discipline.text
    criterion = db.get(CriterionDefinition, definition.json()["criteria"][0]["id"])
    assert criterion is not None
    criterion_identity = db.get(CriterionIdentity, criterion.criterion_identity_id)
    semantic_definition = db.get(SemanticCompetencyDefinition, criterion.semantic_definition_id)
    assert criterion_identity is not None and semantic_definition is not None
    evidence_base = utc_now_ms() - 1_000
    for index in range(2):
        evidence = Evidence(
            evidence_type="assessment",
            source_type="authority_test",
            source_id=f"authority-readiness-evidence-{index}",
            source_role="fact",
            title=f"Authority readiness evidence {index}",
            strength="moderate",
            independence="independent",
            source_confidence="high",
            occurred_at=evidence_base + index,
            created_at=evidence_base + index,
            provenance_json=canonical_json(
                {
                    "origin_kind": "local",
                    "creator_kind": "test",
                    "capture_method": "test_fixture",
                    "source_record_type": "authority_test",
                    "source_record_id": f"authority-readiness-evidence-{index}",
                    "context_id": f"authority-context-{index}",
                    "policy_version": "evidence-policy/v1",
                }
            ),
            policy_version="evidence-policy/v1",
            schema_version=1,
            authoritative_for_downgrade=False,
        )
        db.add(evidence)
        db.flush()
        db.add(
            EvidenceLink(
                evidence_id=evidence.id,
                competency_identity_id=competency_id,
                criterion_identity_id=criterion_identity.id,
                criterion_definition_id=criterion.id,
                scale_version_id=semantic_definition.scale_version_id,
                level_id=criterion.level_id,
                effect="supports",
                relevance="primary",
                provenance_json=canonical_json(
                    {
                        "capture_method": "test_fixture",
                        "policy_version": "evidence-policy/v1",
                        "evidence_id": evidence.id,
                    }
                ),
                created_at=evidence_base + index,
            )
        )
    db.flush()
    evaluate_capability(db, competency_id, cutoff_at=evidence_base + 3)
    db.commit()

    snapshot = run_analysis(
        db,
        idempotency_key="authority-real-readiness-analysis",
        purpose="learning_control",
        update_current=True,
    )
    db.commit()
    assert snapshot.completeness in {"complete", "partial"}
    assert all(
        marker["reasonCode"] for marker in snapshot_detail(db, snapshot.id)["unknownMarkers"]
    )
    report = authority_service.readiness_report(db)
    failed_checks = [item for item in report["checks"] if not item["ready"]]
    assert failed_checks == [], failed_checks
    assert report["ready"] is True, report
    assert all(item["ready"] for item in report["checks"])

    activated = await client.post(
        "/api/v2/authority/activate-v2",
        json={
            "idempotency_key": "authority-real-readiness-activate",
            "reason": "All runtime readiness checks passed",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["canonicalLearningAuthority"] == "v2"
    backup = db.scalar(
        select(OperationalBackup).where(OperationalBackup.purpose == "pre-v2-authority-activation")
    )
    assert backup is not None and Path(backup.path).is_file()
    blocked = await client.put(
        f"/api/v1/roadmap/current-phase/{first_phase['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "LEGACY_AUTHORITY_READ_ONLY"


async def test_legacy_recommendation_history_remains_readable_but_not_mutable(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    snapshot = RecommendationSnapshot(
        local_date="2026-09-19",
        engine_version=1,
        structured_payload_json=canonical_json(
            {
                "generatedAt": "2026-09-19T00:00:00.000Z",
                "localDate": "2026-09-19",
                "primary": None,
                "secondary": None,
                "setupRequired": False,
            }
        ),
    )
    db.add(snapshot)
    db.flush()
    state = db.get(LearningControlAuthorityState, 1)
    assert state is not None
    authority_service._append_event(
        db,
        state,
        idempotency_key="test-direct-activation",
        command_type="activate_v2",
        resulting={
            "canonicalLearningAuthority": "v2",
            "recommendationPresentation": "v2",
            "roadmapPresentation": "v2",
            "todayPresentation": "v2",
        },
        reason="Test-only activation without backup",
        now_ms=1,
    )
    db.commit()

    history = await client.get("/api/v1/recommendations/history")
    assert history.status_code == 200
    assert history.json()[0]["id"] == snapshot.id
    assert history.json()[0]["authority"] == "legacy_v1_history"
    decision = await client.post(
        f"/api/v1/recommendations/{snapshot.id}/decision",
        json={"accepted_primary": True, "chosen_competency_identity_id": None},
        headers={"X-CSRF-Token": csrf},
    )
    assert decision.status_code == 409
    assert decision.json()["error"]["code"] == "LEGACY_AUTHORITY_READ_ONLY"


def test_authority_history_is_immutable_and_portable_v9_is_exact(db: Session) -> None:
    event = db.scalar(select(LearningControlAuthorityEvent))
    assert event is not None
    event.reason = "tampered"
    with pytest.raises(ValueError, match="authority history is immutable"):
        db.flush()
    db.rollback()

    payload = _portable_payload(db)
    tables, summary = _validate_portable_payload(payload, "authority-v9", 9)
    assert summary["compatibilityConversions"] == {}
    assert len(tables["learning_control_authority_state"]) == 1
    assert len(tables["learning_control_authority_events"]) == 1

    tampered = deepcopy(payload)
    tampered["tables"]["learning_control_authority_events"][0]["reason"] = "tampered"
    with pytest.raises(AppError, match="Authority event hash is invalid"):
        _validate_portable_payload(tampered, "authority-v9-tampered", 9)

    malformed = deepcopy(payload)
    malformed["tables"]["learning_control_authority_events"][0]["resulting_state_json"] = (
        "{not-json"
    )
    with pytest.raises(AppError, match="Authority event JSON is invalid"):
        _validate_portable_payload(malformed, "authority-v9-malformed-json", 9)

    v8 = deepcopy(payload)
    v8["manifest"] = PORTABLE_V8_MANIFEST
    v8.pop("authorityCheckpoint")
    for table_name in PORTABLE_V9_AUTHORITY_TABLES:
        v8["tables"].pop(table_name)
    converted, conversion = _validate_portable_payload(v8, "authority-v8-adapter", 8)
    assert (
        converted["learning_control_authority_state"][0]["canonical_learning_authority"]
        == "legacy_v1"
    )
    assert conversion["compatibilityConversions"] == {
        "initializedAuthorityTables": len(PORTABLE_V9_AUTHORITY_TABLES),
        "nativeAuthorityHistoryInferred": 0,
    }


def test_authority_rejects_validly_rehashed_but_impossible_history(db: Session) -> None:
    event = db.scalar(select(LearningControlAuthorityEvent))
    assert event is not None
    invalid_payload = {
        "commandType": "surface_change",
        "reason": event.reason,
        "resultingState": json.loads(event.resulting_state_json),
    }
    db.execute(
        text(
            "UPDATE learning_control_authority_events "
            "SET command_type='surface_change', payload_hash=:payload_hash WHERE id=:event_id"
        ),
        {"payload_hash": content_hash(invalid_payload), "event_id": event.id},
    )
    db.commit()
    db.expire_all()
    with pytest.raises(AppError, match="authority history is inconsistent"):
        authority_service.authority_state(db)


def test_v8_pointer_contraction_requires_exact_adapter_parity() -> None:
    pointer_state = {
        "activeVersionId": "version-1",
        "currentPhaseId": "phase-1",
        "isCurrent": True,
        "roadmapId": "roadmap-1",
    }
    tables: dict[str, list[dict[str, object]]] = {
        "roadmaps": [
            {
                "id": "roadmap-1",
                "is_current": True,
                "active_version_id": "version-1",
                "current_phase_id": "phase-1",
            }
        ],
        "legacy_roadmap_active_states": [
            {
                "roadmap_id": "roadmap-1",
                "is_current": True,
                "active_version_id": "version-1",
                "current_phase_id": "phase-1",
                "state_hash": content_hash(pointer_state),
            }
        ],
    }
    conversion = upgrade_v8_to_v9_tables(tables)
    assert conversion["removedLegacyRoadmapPointers"] == 3
    assert set(tables["roadmaps"][0]) == {"id"}

    mismatched = deepcopy(tables)
    mismatched["roadmaps"][0].update(
        {
            "is_current": True,
            "active_version_id": "version-2",
            "current_phase_id": "phase-1",
        }
    )
    with pytest.raises(ValueError, match="pointer parity"):
        upgrade_v8_to_v9_tables(mismatched)


def test_head_schema_contracts_legacy_pointer_cycle(db: Session) -> None:
    roadmap_columns = {row[1] for row in db.execute(text("PRAGMA table_info(roadmaps)")).all()}
    assert {"is_current", "active_version_id", "current_phase_id"}.isdisjoint(roadmap_columns)
    assert db.execute(text("PRAGMA foreign_key_check")).all() == []
    assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
        "0019_remove_legacy_roadmap_pointer_cycle"
    )


def test_portable_restore_cannot_demote_or_rewrite_v2_authority(db: Session) -> None:
    legacy_payload = _portable_payload(db)
    state = db.get(LearningControlAuthorityState, 1)
    assert state is not None
    authority_service._append_event(
        db,
        state,
        idempotency_key="restore-transition-activation",
        command_type="activate_v2",
        resulting={
            "canonicalLearningAuthority": "v2",
            "recommendationPresentation": "v2",
            "roadmapPresentation": "v2",
            "todayPresentation": "v2",
        },
        reason="Test restore transition",
        now_ms=1,
    )
    db.commit()
    with pytest.raises(AppError, match="cannot demote canonical V2"):
        _apply_portable_restore(
            db,
            legacy_payload,
            True,
            package_id="legacy-after-v2",
            schema_version=9,
        )

    earlier_v2_payload = _portable_payload(db)
    _validated_tables, _summary = _validate_portable_payload(
        earlier_v2_payload, "activated-v2-authority-round-trip", 9
    )
    _apply_portable_restore(
        db,
        earlier_v2_payload,
        True,
        package_id="activated-v2-authority-round-trip",
        schema_version=9,
    )
    validate_domain_integrity(db.connection())
    round_trip = _portable_payload(db)
    for table_name in PORTABLE_V9_AUTHORITY_TABLES:
        assert round_trip["tables"][table_name] == earlier_v2_payload["tables"][table_name]
    assert round_trip["authorityCheckpoint"] == earlier_v2_payload["authorityCheckpoint"]
    state = db.get(LearningControlAuthorityState, 1)
    assert state is not None
    authority_service._append_event(
        db,
        state,
        idempotency_key="restore-transition-surface",
        command_type="surface_change",
        resulting={
            "canonicalLearningAuthority": "v2",
            "recommendationPresentation": "v1_read_only",
            "roadmapPresentation": "v1_read_only",
            "todayPresentation": "v1_read_only",
        },
        reason="Test later surface event",
        now_ms=2,
    )
    db.commit()
    with pytest.raises(AppError, match="cannot remove or rewrite"):
        _apply_portable_restore(
            db,
            earlier_v2_payload,
            True,
            package_id="older-v2-history",
            schema_version=9,
        )
