from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from app import import_export
from app.analysis.v3.service import run_analysis
from app.auth import AuthContext
from app.config import Settings
from app.domain_integrity import validate_domain_integrity
from app.errors import AppError
from app.import_export import (
    _apply_portable_restore,
    _inspect_package,
    _portable_payload,
    _validate_portable_payload,
)
from app.main import app
from app.master_import import canonical as master_canonical
from app.master_import import contracts as master_contracts
from app.master_import import service as master_service
from app.master_import.canonical import (
    canonical_bytes,
    package_digest,
    parse_transport_text,
    transport_text_digest,
)
from app.master_import.canonical import content_digest as master_content_digest
from app.models import (
    Activity,
    CompetencyCapabilityState,
    CompetencyIdentity,
    Evidence,
    ImportRecord,
    MasterImportOwnedKey,
    MasterImportRevision,
    OperationalBackup,
)
from app.recommendation.v2.service import generate_recommendations, recommendation_detail
from app.request_limits import ImportBodyLimit
from app.schemas import ImportApplyRequest, ImportInspectRequest
from app.today.models import TodaySuggestion
from app.today.service import generate_today
from app.v2_activities import create_activity_in_uow
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker


def package() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "packageType": "master_import",
        "packageId": "test-master-1",
        "appVersion": "test",
        "createdAt": "2026-01-01T00:00:00Z",
        "payload": {
            "lineageKey": "test.learning",
            "ownerKey": "test.owner",
            "contentRevision": 1,
            "previousContentDigest": None,
            "canonicalizationVersion": "mi-canon-v1",
            "provenance": {"sourceName": "test", "authoredBy": "test", "sourceRevision": "1"},
            "effectiveAt": "2026-01-01T00:00:00Z",
            "activationIntent": {
                "competencies": True,
                "targetProfile": True,
                "curriculum": True,
                "learningGraph": True,
            },
            "competencies": [
                {
                    "stableKey": "test.comp",
                    "title": "Test competency",
                    "scope": "Test scope",
                    "criteria": [
                        {
                            "stableKey": "test.criterion",
                            "levelKey": "guided",
                            "requirementType": "required",
                            "demonstrationRule": "guided_performance",
                            "description": "Can perform the test task",
                        }
                    ],
                }
            ],
            "targetProfile": {
                "stableKey": "test.profile",
                "title": "Test profile",
                "domains": [{"stableKey": "test.domain", "title": "Test domain", "orderIndex": 0}],
                "targets": [
                    {
                        "stableKey": "test.target",
                        "competencyRef": "test.comp",
                        "domainRef": "test.domain",
                        "levelKey": "guided",
                        "priority": "core",
                    }
                ],
            },
            "curriculum": {
                "stableKey": "test.curriculum",
                "title": "Test curriculum",
                "objectives": [
                    {"stableKey": "test.objective", "title": "Test objective", "orderIndex": 0}
                ],
                "units": [
                    {
                        "stableKey": "test.entry",
                        "objectiveRef": "test.objective",
                        "kind": "exercise",
                        "title": "First action",
                        "action": {"kind": "exercise", "instructions": "Do the test task."},
                        "orderIndex": 0,
                        "minimumUsefulDurationMs": 900000,
                        "preferredDurationMs": 900000,
                        "maximumUsefulDurationMs": 1800000,
                        "targets": [
                            {
                                "competencyRef": "test.comp",
                                "intendedLearningOutcome": "Learn the test task",
                                "supportsUnassessed": True,
                                "orderIndex": 0,
                            }
                        ],
                    }
                ],
            },
            "learningGraph": {"stableKey": "test.graph", "title": "Test graph", "edges": []},
            "initialSpine": ["test.entry"],
            "removedFromActiveVersion": [],
        },
    }


def inspect(db: Session, value: dict[str, object]) -> dict[str, object]:
    raw = json.dumps(value, ensure_ascii=False)
    return _inspect_package(
        ImportInspectRequest.model_validate(
            {"filename": "test.json", "package": value, "rawText": raw}
        ),
        Settings(environment="test", database_url="sqlite:///:memory:"),
        db,
        session_id="test-direct-session",
    )


def test_master_inspect_is_pure_and_has_semantic_diff(db: Session) -> None:
    before = db.scalar(select(func.count()).select_from(CompetencyIdentity))
    result = inspect(db, package())
    assert result["valid"] is True
    assert result["diff"]["stableIdentitiesCreated"]["total"] > 0
    assert result["diff"]["coldStartImpact"]["entryUnitKeys"]["items"] == ["test.entry"]
    assert db.scalar(select(func.count()).select_from(CompetencyIdentity)) == before


async def test_master_large_package_preview_and_audit_diff_remain_bounded(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    value = package()
    first_unit = value["payload"]["curriculum"]["units"][0]
    first_unit["description"] = "DO_NOT_ECHO_FULL_CONTENT_" + "x" * 2000
    for index in range(1, 128):
        extra = deepcopy(first_unit)
        extra["stableKey"] = f"test.unit{index}"
        extra["orderIndex"] = index
        value["payload"]["curriculum"]["units"].append(extra)
    raw = "\n" * 30_001 + json.dumps(value)
    assert raw.count("\n") > 30_000
    request = {"filename": "large-master.json", "package": value, "rawText": raw}
    inspected = await client.post(
        "/api/v1/import-export/import/inspect",
        json=request,
        headers={"X-CSRF-Token": csrf},
    )
    assert inspected.status_code == 200, inspected.text
    diff = inspected.json()["diff"]
    unit_detail = diff["curriculumChanges"]["details"]["units"]
    assert unit_detail["total"] == 128
    assert unit_detail["shown"] == 32
    assert unit_detail["omitted"] == 96
    assert unit_detail["truncated"] is True
    assert all(isinstance(item, str) for item in unit_detail["items"])
    response_text = json.dumps(inspected.json())
    assert "DO_NOT_ECHO_FULL_CONTENT_" not in response_text
    assert len(response_text) < 100_000
    applied = await client.post(
        "/api/v1/import-export/import/apply",
        json={**request, "confirmation_token": inspected.json()["confirmationToken"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert applied.status_code == 200, applied.text
    record = db.scalar(select(ImportRecord).where(ImportRecord.package_id == value["packageId"]))
    assert record is not None
    assert "DO_NOT_ECHO_FULL_CONTENT_" not in record.dry_run_summary_json
    assert len(record.dry_run_summary_json) < 50_000


def test_master_unknown_field_rejected_before_write(db: Session) -> None:
    value = deepcopy(package())
    value["payload"]["competencies"][0]["unknown"] = True
    with pytest.raises(AppError):
        inspect(db, value)


@pytest.mark.parametrize(
    "created_at",
    ["not-a-date", "2026-01-01", "2026-01-01T00:00:00", "2026-01-01T01:00:00+01:00"],
)
def test_master_created_at_requires_utc_rfc3339(db: Session, created_at: str) -> None:
    value = package()
    value["createdAt"] = created_at
    with pytest.raises(AppError, match="createdAt"):
        inspect(db, value)
    assert db.scalar(select(func.count()).select_from(MasterImportRevision)) == 0


@pytest.mark.parametrize(
    "text",
    [
        '{"a":1,"a":2}',
        '{"n":1.0}',
        '{"n":1e3}',
        '{"n":-0}',
        '{"n":9007199254740992}',
        '{"x":"e\\u0301"}',
    ],
)
def test_master_canonical_parser_rejects_unsafe_json(text: str) -> None:
    with pytest.raises(AppError):
        parse_transport_text(text, 1024)


def test_master_canonical_json_key_order_and_arrays() -> None:
    assert canonical_bytes({"b": 1, "a": [2, None]}) == b'{"a":[2,null],"b":1}'
    assert canonical_bytes({"a": [2, 1]}) != canonical_bytes({"a": [1, 2]})


def test_master_raw_and_structural_resource_limits(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(AppError, match="too large"):
        parse_transport_text("{}", 1)
    with pytest.raises(AppError, match="field limit"):
        parse_transport_text(json.dumps({"description": "x" * 16_385}), 20_000)
    monkeypatch.setattr(master_canonical, "MAX_JSON_NODES", 4)
    with pytest.raises(AppError, match="too many nodes"):
        parse_transport_text('{"items":[1,2,3]}', 100)
    monkeypatch.setattr(master_canonical, "MAX_JSON_NODES", 500_000)
    monkeypatch.setattr(master_contracts, "MAX_AUTHORED_ENTITIES", 1)
    with pytest.raises(AppError, match="payload is invalid"):
        inspect(db, package())


async def test_import_http_body_limit_rejects_header_and_stream() -> None:
    async def downstream(scope: object, receive: object, send: object) -> None:
        while True:
            message = await receive()  # type: ignore[operator]
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})  # type: ignore[operator]
        await send({"type": "http.response.body", "body": b"ok"})  # type: ignore[operator]

    for headers, chunks in (
        ([(b"content-length", b"11")], []),
        ([], [b"123456", b"78901"]),
    ):
        messages = [
            {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
            for index, chunk in enumerate(chunks)
        ]
        sent: list[dict[str, object]] = []

        async def receive(_messages: list[dict[str, object]] = messages) -> dict[str, object]:
            return _messages.pop(0)

        async def send(message: dict[str, object], _sent: list[dict[str, object]] = sent) -> None:
            _sent.append(message)

        await ImportBodyLimit(downstream, max_body_bytes=10)(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/import-export/import/inspect",
                "headers": headers,
            },
            receive,
            send,
        )
        assert sent[0]["status"] == 413


async def test_import_http_body_limit_is_active_before_envelope_parsing(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/import-export/import/inspect",
        content=b"{}",
        headers={"Content-Length": str(4 * 32 * 1024 * 1024 + 1)},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "IMPORT_TOO_LARGE"


def test_master_frozen_canonicalization_digest_vectors() -> None:
    vector = {"z": None, "a": ["é", "\u0001", 9007199254740991]}
    assert canonical_bytes(vector) == b'{"a":["\xc3\xa9","\\u0001",9007199254740991],"z":null}'
    assert canonical_bytes({"z": None}) != canonical_bytes({})
    assert canonical_bytes({"a": 1, "b": 2}) == canonical_bytes({"b": 2, "a": 1})
    assert parse_transport_text('{ "b": 2, "a": 1 }', 100) == {"a": 1, "b": 2}
    assert parse_transport_text('{"min":-9007199254740991}', 100) == {"min": -9007199254740991}
    assert transport_text_digest('{"a":1}') != transport_text_digest('{ "a": 1 }')
    value = package()
    assert master_content_digest(value) == (
        "mi-content-v1:sha256:23557cb367e4c23900bca7a0e4b8534ef08d330be6fd9c93a598a8ce9a68e1e6"
    )
    assert package_digest(value) == (
        "mi-package-v1:sha256:82386d899b271875389b81b19fdd267b52b725f94a1cecafadc2834baf688331"
    )
    assert package_digest(value) == package_digest(deepcopy(value))
    assert master_content_digest(value) == master_content_digest(deepcopy(value))
    original_content = master_content_digest(value)
    original_package = package_digest(value)
    value["appVersion"] = "different producer metadata"
    value["payload"]["provenance"]["sourceRevision"] = "different source revision"
    assert master_content_digest(value) == original_content
    assert package_digest(value) != original_package


@pytest.mark.parametrize(
    "defect",
    [
        "unsupported_schema",
        "duplicate_competency",
        "missing_reference",
        "invalid_level",
        "criterion_mismatch",
        "invalid_duration",
        "non_utc_time",
        "future_activation",
        "no_unknown_entry",
        "missing_spine_unit",
        "requirement_cycle",
    ],
)
def test_master_semantic_defects_rejected_before_write(db: Session, defect: str) -> None:
    value = deepcopy(package())
    authored = value["payload"]
    if defect == "unsupported_schema":
        value["schemaVersion"] = 2
    elif defect == "duplicate_competency":
        authored["competencies"].append(deepcopy(authored["competencies"][0]))
    elif defect == "missing_reference":
        authored["targetProfile"]["targets"][0]["competencyRef"] = "missing.comp"
    elif defect == "invalid_level":
        authored["competencies"][0]["criteria"][0]["levelKey"] = "unknown.level"
    elif defect == "criterion_mismatch":
        other = deepcopy(authored["competencies"][0])
        other["stableKey"] = "test.other"
        other["criteria"][0]["stableKey"] = "test.othercriterion"
        authored["competencies"].append(other)
        authored["curriculum"]["units"][0]["targets"][0]["criterionRef"] = (
            "test.other::test.othercriterion"
        )
    elif defect == "invalid_duration":
        authored["curriculum"]["units"][0]["maximumUsefulDurationMs"] = 1
    elif defect == "non_utc_time":
        authored["effectiveAt"] = "2026-01-01T03:00:00+03:00"
    elif defect == "future_activation":
        authored["effectiveAt"] = "2999-01-01T00:00:00Z"
    elif defect == "no_unknown_entry":
        authored["curriculum"]["units"][0]["targets"][0]["supportsUnassessed"] = False
    elif defect == "requirement_cycle":
        authored["curriculum"]["units"][0]["requirements"] = [
            {
                "stableKey": "test.selfcycle",
                "requirementType": "learning_unit_completed",
                "effect": "hard",
                "scope": "curriculum",
                "orderIndex": 0,
                "unitRef": "test.entry",
            }
        ]
    else:
        authored["initialSpine"] = ["missing.unit"]
    with pytest.raises((AppError, ValueError)):
        inspect(db, value)
    assert db.scalar(select(func.count()).select_from(CompetencyIdentity)) == 0


async def test_master_assessment_first_entry_is_valid(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    value = deepcopy(package())
    unit = value["payload"]["curriculum"]["units"][0]
    unit["kind"] = "verification_template"
    unit["action"] = {
        "kind": "verification_template",
        "verificationMethod": "Demonstrate the task.",
    }
    unit["targets"][0]["supportsUnassessed"] = False
    value["payload"]["curriculum"]["assessmentRubrics"] = [
        {
            "stableKey": "test.rubric",
            "title": "Entry assessment",
            "instructions": "Demonstrate the task.",
            "competencyRef": "test.comp",
            "rubric": {
                "policyVersion": "master-rubric-v1",
                "criteria": [
                    {
                        "criterionRef": "test.comp::test.criterion",
                        "weight": 100,
                        "description": "Complete the task.",
                    }
                ],
            },
        }
    ]
    result = inspect(db, value)
    assert result["diff"]["coldStartImpact"]["entryUnitKeys"]["items"] == ["test.entry"]
    client, csrf = authenticated_client
    _, applied = await _submit_master(client, csrf, value)
    assert applied.status_code == 200, applied.text
    snapshot = run_analysis(
        db,
        idempotency_key="assessment-first-analysis",
        purpose="learning_control",
        update_current=True,
    )
    db.commit()
    run = generate_recommendations(
        db,
        idempotency_key="assessment-first-recommendation",
        analysis_snapshot_id=snapshot.id,
        available_time_ms=None,
    )
    db.commit()
    assert any(
        candidate["candidateType"] == "assessment"
        for candidate in recommendation_detail(db, run.id)["portfolio"]
    )


def test_master_later_domain_may_be_staged(db: Session) -> None:
    value = deepcopy(package())
    payload = value["payload"]
    later = deepcopy(payload["competencies"][0])
    later["stableKey"] = "test.later"
    later["criteria"][0]["stableKey"] = "test.latercriterion"
    payload["competencies"].append(later)
    payload["targetProfile"]["domains"].append(
        {"stableKey": "test.laterdomain", "title": "Later domain", "orderIndex": 1}
    )
    payload["targetProfile"]["targets"].append(
        {
            "stableKey": "test.latertarget",
            "competencyRef": "test.later",
            "domainRef": "test.laterdomain",
            "levelKey": "guided",
            "priority": "core",
        }
    )
    later_unit = deepcopy(payload["curriculum"]["units"][0])
    later_unit["stableKey"] = "test.laterunit"
    later_unit["orderIndex"] = 1
    later_unit["targets"][0]["competencyRef"] = "test.later"
    later_unit["requirements"] = [
        {
            "stableKey": "test.firstdone",
            "requirementType": "learning_unit_completed",
            "effect": "hard",
            "scope": "curriculum",
            "orderIndex": 0,
            "unitRef": "test.entry",
        }
    ]
    payload["curriculum"]["units"].append(later_unit)
    payload["initialSpine"].append("test.laterunit")
    result = inspect(db, value)
    assert result["diff"]["coldStartImpact"]["entryUnitKeys"]["items"] == ["test.entry"]


def _hard_spine_package(hard_kind: str) -> dict[str, object]:
    value = deepcopy(package())
    payload = value["payload"]
    later = deepcopy(payload["competencies"][0])
    later["stableKey"] = "test.later"
    later["criteria"][0]["stableKey"] = "test.latercriterion"
    payload["competencies"].append(later)
    payload["targetProfile"]["targets"].append(
        {
            "stableKey": "test.latertarget",
            "competencyRef": "test.later",
            "domainRef": "test.domain",
            "levelKey": "guided",
            "priority": "core",
        }
    )
    later_unit = deepcopy(payload["curriculum"]["units"][0])
    later_unit["stableKey"] = "test.laterunit"
    later_unit["orderIndex"] = 1
    later_unit["targets"][0]["competencyRef"] = "test.later"
    if hard_kind in {"capability_at_least", "criterion_demonstrated"}:
        later_unit["requirements"] = [
            {
                "stableKey": "test.hardrequirement",
                "requirementType": hard_kind,
                "effect": "hard",
                "scope": "learner",
                "orderIndex": 0,
                **(
                    {"competencyRef": "test.comp", "levelKey": "guided"}
                    if hard_kind == "capability_at_least"
                    else {"criterionRef": "test.comp::test.criterion"}
                ),
            }
        ]
    else:
        payload["learningGraph"]["edges"] = [
            {
                "stableKey": "test.hardedge",
                "edgeType": "prerequisite",
                "sourceRef": "test.comp",
                "targetRef": "test.later",
                "orderIndex": 0,
                "requirement": (
                    {"kind": "capability_at_least", "minimumLevelKey": "guided"}
                    if hard_kind == "graph_capability"
                    else {
                        "kind": "criterion_set_demonstrated",
                        "criterionRefs": ["test.comp::test.criterion"],
                    }
                ),
            }
        ]
    payload["curriculum"]["units"].append(later_unit)
    payload["initialSpine"].append("test.laterunit")
    return value


@pytest.mark.parametrize(
    "hard_kind",
    [
        "capability_at_least",
        "criterion_demonstrated",
        "graph_capability",
        "graph_criterion_set",
    ],
)
def test_master_initial_spine_rejects_unproducible_hard_prerequisite(
    db: Session, hard_kind: str
) -> None:
    value = _hard_spine_package(hard_kind)
    with pytest.raises(AppError, match="no structural route"):
        inspect(db, value)
    assert db.scalar(select(func.count()).select_from(MasterImportRevision)) == 0


@pytest.mark.parametrize("route", ["opportunity", "rubric"])
def test_master_initial_spine_accepts_authored_evidence_route(db: Session, route: str) -> None:
    value = _hard_spine_package("graph_capability")
    entry = value["payload"]["curriculum"]["units"][0]
    if route == "opportunity":
        entry["targets"][0]["criterionRef"] = "test.comp::test.criterion"
        entry["evidenceOpportunities"] = [
            {
                "stableKey": "test.entryevidence",
                "evidenceKind": "assessment",
                "intendedStrengths": ["moderate"],
                "intendedIndependenceModes": ["guided"],
                "requiresActualActivity": True,
                "requiresArtifact": False,
                "orderIndex": 0,
            }
        ]
    else:
        entry["kind"] = "verification_template"
        entry["action"] = {
            "kind": "verification_template",
            "verificationMethod": "Demonstrate the source task.",
        }
        value["payload"]["curriculum"]["assessmentRubrics"] = [
            {
                "stableKey": "test.sourceassessment",
                "title": "Source assessment",
                "instructions": "Demonstrate the source task.",
                "competencyRef": "test.comp",
                "rubric": {
                    "policyVersion": "master-rubric-v1",
                    "criteria": [
                        {
                            "criterionRef": "test.comp::test.criterion",
                            "weight": 100,
                            "description": "Complete the source task.",
                        }
                    ],
                },
            }
        ]
    result = inspect(db, value)
    assert result["valid"] is True


def test_master_graph_hard_prerequisite_cycle_is_rejected(db: Session) -> None:
    value = deepcopy(package())
    later = deepcopy(value["payload"]["competencies"][0])
    later["stableKey"] = "test.later"
    later["criteria"][0]["stableKey"] = "test.latercriterion"
    value["payload"]["competencies"].append(later)
    latest = deepcopy(later)
    latest["stableKey"] = "test.latest"
    latest["criteria"][0]["stableKey"] = "test.latestcriterion"
    value["payload"]["competencies"].append(latest)
    value["payload"]["learningGraph"]["edges"] = [
        {
            "stableKey": "test.cycleone",
            "edgeType": "prerequisite",
            "sourceRef": "test.later",
            "targetRef": "test.latest",
            "requirement": {"kind": "capability_at_least", "minimumLevelKey": "guided"},
            "orderIndex": 0,
        },
        {
            "stableKey": "test.cycletwo",
            "edgeType": "prerequisite",
            "sourceRef": "test.latest",
            "targetRef": "test.later",
            "requirement": {"kind": "capability_at_least", "minimumLevelKey": "guided"},
            "orderIndex": 1,
        },
    ]
    with pytest.raises(AppError):
        inspect(db, value)
    assert db.scalar(select(func.count()).select_from(CompetencyIdentity)) == 0


async def test_master_existing_import_pipeline_applies_atomically(
    authenticated_client: tuple[AsyncClient, str],
    db: Session,
) -> None:
    client, csrf = authenticated_client
    value = package()
    request = {"filename": "master.json", "package": value, "rawText": json.dumps(value)}
    preview = await client.post(
        "/api/v1/import-export/import/inspect",
        json=request,
        headers={"X-CSRF-Token": csrf},
    )
    assert preview.status_code == 200, preview.text
    applied = await client.post(
        "/api/v1/import-export/import/apply",
        json={**request, "confirmation_token": preview.json()["confirmationToken"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] is True
    assert db.scalar(select(func.count()).select_from(MasterImportRevision)) == 1
    assert db.scalar(select(func.count()).select_from(MasterImportOwnedKey)) >= 1
    assert db.scalar(select(func.count()).select_from(Evidence)) == 0
    assert db.scalar(select(func.count()).select_from(Activity)) == 0


async def test_master_confirmation_binds_transport_and_base_state(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    value = package()
    raw = json.dumps(value)
    request = {"filename": "master.json", "package": value, "rawText": raw}
    preview = await client.post(
        "/api/v1/import-export/import/inspect",
        json=request,
        headers={"X-CSRF-Token": csrf},
    )
    assert preview.status_code == 200, preview.text
    token = preview.json()["confirmationToken"]
    changed_text = await client.post(
        "/api/v1/import-export/import/apply",
        json={**request, "rawText": raw + " ", "confirmation_token": token},
        headers={"X-CSRF-Token": csrf},
    )
    assert changed_text.status_code == 409
    db.add(CompetencyIdentity(stable_key="another.comp", creation_source="test"))
    db.commit()
    stale = await client.post(
        "/api/v1/import-export/import/apply",
        json={**request, "confirmation_token": token},
        headers={"X-CSRF-Token": csrf},
    )
    assert stale.status_code == 409
    assert db.scalar(select(func.count()).select_from(MasterImportRevision)) == 0


async def test_master_second_inspection_invalidates_prior_confirmation(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    value = package()
    request = {"filename": "master.json", "package": value, "rawText": json.dumps(value)}
    first = await client.post(
        "/api/v1/import-export/import/inspect",
        json=request,
        headers={"X-CSRF-Token": csrf},
    )
    second = await client.post(
        "/api/v1/import-export/import/inspect",
        json=request,
        headers={"X-CSRF-Token": csrf},
    )
    assert first.status_code == second.status_code == 200
    rejected = await client.post(
        "/api/v1/import-export/import/apply",
        json={**request, "confirmation_token": first.json()["confirmationToken"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected.status_code == 409
    assert db.scalar(select(func.count()).select_from(MasterImportRevision)) == 0


async def test_master_preview_is_bound_to_authenticated_session(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client_a, csrf_a = authenticated_client
    value = package()
    request = {"filename": "master.json", "package": value, "rawText": json.dumps(value)}
    preview_a = await client_a.post(
        "/api/v1/import-export/import/inspect",
        json=request,
        headers={"X-CSRF-Token": csrf_a},
    )
    assert preview_a.status_code == 200, preview_a.text
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client_b:
        login = await client_b.post(
            "/api/v1/auth/login",
            json={"username": "learner", "password": "correct horse battery staple"},
        )
        assert login.status_code == 200, login.text
        csrf_b = login.json()["csrf_token"]
        cross_apply = await client_b.post(
            "/api/v1/import-export/import/apply",
            json={**request, "confirmation_token": preview_a.json()["confirmationToken"]},
            headers={"X-CSRF-Token": csrf_b},
        )
        assert cross_apply.status_code == 409
        preview_b = await client_b.post(
            "/api/v1/import-export/import/inspect",
            json=request,
            headers={"X-CSRF-Token": csrf_b},
        )
        assert preview_b.status_code == 200, preview_b.text
        cross_apply_a = await client_a.post(
            "/api/v1/import-export/import/apply",
            json={**request, "confirmation_token": preview_b.json()["confirmationToken"]},
            headers={"X-CSRF-Token": csrf_a},
        )
        assert cross_apply_a.status_code == 409
        applied_a = await client_a.post(
            "/api/v1/import-export/import/apply",
            json={**request, "confirmation_token": preview_a.json()["confirmationToken"]},
            headers={"X-CSRF-Token": csrf_a},
        )
        assert applied_a.status_code == 200, applied_a.text
    assert db.scalar(select(func.count()).select_from(MasterImportRevision)) == 1


async def test_master_concurrent_apply_attempts_create_one_revision(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    value = package()
    request = {"filename": "master.json", "package": value, "rawText": json.dumps(value)}
    preview = await client.post(
        "/api/v1/import-export/import/inspect",
        json=request,
        headers={"X-CSRF-Token": csrf},
    )
    assert preview.status_code == 200, preview.text
    apply_request = {**request, "confirmation_token": preview.json()["confirmationToken"]}
    outcomes = await asyncio.gather(
        *(
            client.post(
                "/api/v1/import-export/import/apply",
                json=apply_request,
                headers={"X-CSRF-Token": csrf},
            )
            for _ in range(2)
        )
    )
    assert sorted(result.status_code for result in outcomes) == [200, 409]
    assert db.scalar(select(func.count()).select_from(MasterImportRevision)) == 1


def _threaded_master_request(
    db: Session, tmp_path: Path, session_id: str
) -> tuple[Settings, ImportApplyRequest, sessionmaker[Session]]:
    value = package()
    raw = json.dumps(value)
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'test.sqlite3'}",
        backup_directory=tmp_path / "backups",
    )
    preview = _inspect_package(
        ImportInspectRequest.model_validate(
            {"filename": "master.json", "package": value, "rawText": raw}
        ),
        settings,
        db,
        session_id=session_id,
    )
    db.rollback()
    request = ImportApplyRequest.model_validate(
        {
            "filename": "master.json",
            "package": value,
            "rawText": raw,
            "confirmation_token": preview["confirmationToken"],
        }
    )
    maker = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    return settings, request, maker


def _run_threaded_master(
    maker: sessionmaker[Session],
    settings: Settings,
    payload: ImportApplyRequest,
    session_id: str,
    errors: list[BaseException],
    results: list[dict[str, object]],
) -> None:
    auth = cast(AuthContext, SimpleNamespace(session=SimpleNamespace(id=session_id)))
    try:
        with maker() as worker_db:
            results.append(
                asyncio.run(
                    import_export.apply_import(
                        payload, cast(Request, None), auth, worker_db, settings
                    )
                )
            )
    except BaseException as exc:
        errors.append(exc)


def _write_normal_v2_activity(maker: sessionmaker[Session]) -> None:
    with maker() as writer_db:
        create_activity_in_uow(
            writer_db,
            title="Concurrent ordinary V2 Activity",
            description=None,
            category_stable_key="practice",
            occurred_at=None,
            creator_source="user",
            provenance="user_recorded",
        )
        writer_db.commit()


def test_master_backup_precedes_competing_v2_write_under_write_reservation(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = "master-before-v2-writer"
    settings, payload, maker = _threaded_master_request(db, tmp_path, session_id)
    backup_physical_complete = threading.Event()
    writer_attempted = threading.Event()
    writer_done = threading.Event()
    errors: list[BaseException] = []
    results: list[dict[str, object]] = []
    original_flush = Session.flush

    def pause_before_backup_audit_flush(self: Session, *args: object, **kwargs: object) -> None:
        if any(isinstance(item, OperationalBackup) for item in self.new):
            assert list(settings.backup_directory.glob("*.sqlite3"))
            backup_physical_complete.set()
            assert writer_attempted.wait(5)
            assert not writer_done.wait(0.25), (
                "The competing V2 write committed between the physical backup and Master mutation"
            )
        original_flush(self, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", pause_before_backup_audit_flush)

    def normal_writer() -> None:
        try:
            writer_attempted.set()
            _write_normal_v2_activity(maker)
        except BaseException as exc:
            errors.append(exc)
        finally:
            writer_done.set()

    master = threading.Thread(
        target=_run_threaded_master,
        args=(maker, settings, payload, session_id, errors, results),
        daemon=True,
    )
    master.start()
    assert backup_physical_complete.wait(10)
    writer = threading.Thread(target=normal_writer, daemon=True)
    writer.start()
    master.join(20)
    writer.join(20)
    assert not master.is_alive() and not writer.is_alive()
    assert not errors, errors
    assert results[0]["applied"] is True
    backup = next(settings.backup_directory.glob("*.sqlite3"))
    with sqlite3.connect(backup) as snapshot:
        assert snapshot.execute("SELECT COUNT(*) FROM activities").fetchone() == (0,)
        assert snapshot.execute("SELECT COUNT(*) FROM master_import_revisions").fetchone() == (0,)
    db.rollback()
    assert db.scalar(select(func.count()).select_from(Activity)) == 1
    assert db.scalar(select(func.count()).select_from(MasterImportRevision)) == 1


def test_master_backup_includes_v2_write_that_commits_before_reservation(
    db: Session, tmp_path: Path
) -> None:
    session_id = "v2-writer-before-master"
    settings, payload, maker = _threaded_master_request(db, tmp_path, session_id)
    writer_reserved = threading.Event()
    release_writer = threading.Event()
    errors: list[BaseException] = []
    results: list[dict[str, object]] = []

    def normal_writer() -> None:
        try:
            with maker() as writer_db:
                writer_db.connection().exec_driver_sql("BEGIN IMMEDIATE")
                create_activity_in_uow(
                    writer_db,
                    title="Earlier ordinary V2 Activity",
                    description=None,
                    category_stable_key="practice",
                    occurred_at=None,
                    creator_source="user",
                    provenance="user_recorded",
                )
                writer_reserved.set()
                assert release_writer.wait(5)
                writer_db.commit()
        except BaseException as exc:
            errors.append(exc)

    writer = threading.Thread(target=normal_writer, daemon=True)
    writer.start()
    assert writer_reserved.wait(5)
    master = threading.Thread(
        target=_run_threaded_master,
        args=(maker, settings, payload, session_id, errors, results),
        daemon=True,
    )
    master.start()
    time.sleep(0.2)
    assert not list(settings.backup_directory.glob("*.sqlite3"))
    release_writer.set()
    writer.join(20)
    master.join(20)
    assert not writer.is_alive() and not master.is_alive()
    assert not errors, errors
    assert results[0]["applied"] is True
    backup = next(settings.backup_directory.glob("*.sqlite3"))
    with sqlite3.connect(backup) as snapshot:
        assert snapshot.execute("SELECT COUNT(*) FROM activities").fetchone() == (1,)
        assert snapshot.execute("SELECT COUNT(*) FROM master_import_revisions").fetchone() == (0,)
    db.rollback()
    assert db.scalar(select(func.count()).select_from(Activity)) == 1
    assert db.scalar(select(func.count()).select_from(MasterImportRevision)) == 1


async def test_master_route_rejects_duplicate_transport_keys(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    value = package()
    raw = json.dumps(value).replace(
        '"packageId": "test-master-1"', '"packageId": "duplicate", "packageId": "test-master-1"'
    )
    rejected = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "master.json", "package": value, "rawText": raw},
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected.status_code == 422
    assert db.scalar(select(func.count()).select_from(MasterImportRevision)) == 0


async def test_master_integrity_failure_rolls_back_after_backup(
    authenticated_client: tuple[AsyncClient, str],
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, csrf = authenticated_client
    value = package()
    request = {"filename": "master.json", "package": value, "rawText": json.dumps(value)}
    preview = await client.post(
        "/api/v1/import-export/import/inspect",
        json=request,
        headers={"X-CSRF-Token": csrf},
    )
    assert preview.status_code == 200, preview.text

    def fail_integrity(_db: Session) -> None:
        raise AppError(422, "TEST_INTEGRITY_FAILURE", "Injected integrity failure")

    monkeypatch.setattr(import_export, "validate_domain_integrity", fail_integrity)
    failed = await client.post(
        "/api/v1/import-export/import/apply",
        json={**request, "confirmation_token": preview.json()["confirmationToken"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert failed.status_code == 422, failed.text
    assert db.scalar(select(func.count()).select_from(MasterImportRevision)) == 0
    assert db.scalar(select(func.count()).select_from(CompetencyIdentity)) == 0
    assert list((tmp_path / "backups").glob("*.sqlite3"))


@pytest.mark.parametrize(
    "writer",
    [
        "write_semantic_definition",
        "write_semantic_activation",
        "_create_profile_version",
        "write_target_profile_activation",
        "create_curriculum_version",
        "write_curriculum_activation",
        "create_graph_version",
        "activate_graph_version",
    ],
)
async def test_master_stage_failure_rolls_back_every_canonical_domain(
    authenticated_client: tuple[AsyncClient, str],
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer: str,
) -> None:
    client, csrf = authenticated_client
    value = package()
    request = {"filename": "master.json", "package": value, "rawText": json.dumps(value)}
    preview = await client.post(
        "/api/v1/import-export/import/inspect",
        json=request,
        headers={"X-CSRF-Token": csrf},
    )
    assert preview.status_code == 200, preview.text

    def fail_stage(*_args: object, **_kwargs: object) -> None:
        raise AppError(422, "TEST_STAGE_FAILURE", "Injected canonical stage failure")

    monkeypatch.setattr(master_service, writer, fail_stage)
    failed = await client.post(
        "/api/v1/import-export/import/apply",
        json={**request, "confirmation_token": preview.json()["confirmationToken"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert failed.status_code == 422, failed.text
    assert db.scalar(select(func.count()).select_from(MasterImportRevision)) == 0
    assert db.scalar(select(func.count()).select_from(MasterImportOwnedKey)) == 0
    assert db.scalar(select(func.count()).select_from(CompetencyIdentity)) == 0
    assert list((tmp_path / "backups").glob("*.sqlite3"))


async def test_master_postcommit_derived_failure_keeps_canonical_import(
    authenticated_client: tuple[AsyncClient, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, csrf = authenticated_client
    value = package()
    request = {"filename": "master.json", "package": value, "rawText": json.dumps(value)}
    preview = await client.post(
        "/api/v1/import-export/import/inspect",
        json=request,
        headers={"X-CSRF-Token": csrf},
    )
    assert preview.status_code == 200, preview.text

    def fail_derived(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("Injected post-commit projection failure")

    monkeypatch.setattr("app.analysis.v3.service.drain_analysis_invalidations", fail_derived)
    applied = await client.post(
        "/api/v1/import-export/import/apply",
        json={**request, "confirmation_token": preview.json()["confirmationToken"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] is True
    assert applied.json()["derivedProcessing"]["status"] == "pending_retry"
    assert db.scalar(select(func.count()).select_from(MasterImportRevision)) == 1


async def _submit_master(
    client: AsyncClient,
    csrf: str,
    value: dict[str, object],
) -> tuple[object, object]:
    request = {"filename": "master.json", "package": value, "rawText": json.dumps(value)}
    preview = await client.post(
        "/api/v1/import-export/import/inspect",
        json=request,
        headers={"X-CSRF-Token": csrf},
    )
    if preview.status_code != 200:
        return preview, preview
    applied = await client.post(
        "/api/v1/import-export/import/apply",
        json={**request, "confirmation_token": preview.json()["confirmationToken"]},
        headers={"X-CSRF-Token": csrf},
    )
    return preview, applied


async def test_master_successor_preserves_identity_and_immutable_versions(
    authenticated_client: tuple[AsyncClient, str],
    db: Session,
) -> None:
    client, csrf = authenticated_client
    first = package()
    _, applied = await _submit_master(client, csrf, first)
    assert applied.status_code == 200, applied.text
    first_revision = db.scalar(select(MasterImportRevision))
    original_identity_id = db.scalar(
        select(CompetencyIdentity.id).where(CompetencyIdentity.stable_key == "test.comp")
    )
    activity = await client.post(
        "/api/v2/activities",
        json={"title": "Genuine learner work", "category_stable_key": "practice"},
        headers={"X-CSRF-Token": csrf},
    )
    assert activity.status_code == 201, activity.text
    evidence = await client.post(
        "/api/v2/evidence",
        json={
            "idempotency_key": "master-genuine-evidence-1",
            "evidence_type": "manual",
            "title": "Genuine learner evidence",
            "strength": "unknown",
            "strength_unknown_reason": "user_unspecified",
            "independence": "unknown",
            "independence_unknown_reason": "user_unspecified",
            "occurred_at_unknown_reason": "user_unspecified",
            "capture_method": "manual entry",
            "links": [{"competency_identity_id": original_identity_id}],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert evidence.status_code == 201, evidence.text
    activity_id, evidence_id = activity.json()["id"], evidence.json()["id"]
    successor = deepcopy(first)
    successor["packageId"] = "test-master-2"
    successor["payload"]["contentRevision"] = 2
    successor["payload"]["previousContentDigest"] = first_revision.content_digest
    successor["payload"]["competencies"][0]["title"] = "Revised test competency"
    preview, applied = await _submit_master(client, csrf, successor)
    assert preview.status_code == 200, preview.text
    assert applied.status_code == 200, applied.text
    assert db.scalar(select(func.count()).select_from(MasterImportRevision)) == 2
    assert db.get(Activity, activity_id) is not None
    assert db.get(Evidence, evidence_id) is not None
    assert (
        db.scalar(select(CompetencyIdentity.id).where(CompetencyIdentity.stable_key == "test.comp"))
        == original_identity_id
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(CompetencyIdentity)
            .where(CompetencyIdentity.stable_key == "test.comp")
        )
        == 1
    )
    assert master_content_digest(first) != master_content_digest(successor)
    replay_preview, replay = await _submit_master(client, csrf, successor)
    assert replay_preview.status_code == 200, replay_preview.text
    assert replay.status_code == 200, replay.text
    assert replay.json()["alreadyApplied"] is True
    assert db.scalar(select(func.count()).select_from(MasterImportRevision)) == 2


async def test_master_successor_rejects_omission_and_meaning_drift(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    first = package()
    _, applied = await _submit_master(client, csrf, first)
    assert applied.status_code == 200, applied.text
    prior = db.scalar(select(MasterImportRevision))
    assert prior is not None
    successor = deepcopy(first)
    successor["packageId"] = "test-successor-omission"
    successor["payload"]["contentRevision"] = 2
    successor["payload"]["previousContentDigest"] = prior.content_digest
    successor["payload"]["curriculum"]["units"] = []
    with pytest.raises(AppError):
        inspect(db, successor)
    successor["payload"]["curriculum"]["units"] = deepcopy(first["payload"]["curriculum"]["units"])
    successor["payload"]["competencies"][0]["criteria"][0]["demonstrationRule"] = (
        "independent_performance"
    )
    with pytest.raises(AppError):
        inspect(db, successor)
    assert db.scalar(select(func.count()).select_from(MasterImportRevision)) == 1


async def test_master_package_id_conflict_and_redundant_successor(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    first = package()
    _, applied = await _submit_master(client, csrf, first)
    assert applied.status_code == 200, applied.text
    prior = db.scalar(select(MasterImportRevision))
    changed_same_id = deepcopy(first)
    changed_same_id["payload"]["competencies"][0]["title"] = "Changed title"
    with pytest.raises(AppError, match="package ID"):
        inspect(db, changed_same_id)
    redundant = deepcopy(first)
    redundant["packageId"] = "redundant-successor"
    redundant["payload"]["contentRevision"] = 2
    redundant["payload"]["previousContentDigest"] = prior.content_digest
    redundant["payload"]["provenance"]["sourceRevision"] = "2"
    with pytest.raises(AppError, match="unchanged semantic content"):
        inspect(db, redundant)


async def test_criterion_requirement_classification_is_stable_meaning(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    first = package()
    _, applied = await _submit_master(client, csrf, first)
    assert applied.status_code == 200, applied.text
    prior = db.scalar(select(MasterImportRevision))
    assert prior is not None
    textual_update = deepcopy(first)
    textual_update["packageId"] = "criterion-textual-update"
    textual_update["payload"]["contentRevision"] = 2
    textual_update["payload"]["previousContentDigest"] = prior.content_digest
    textual_update["payload"]["competencies"][0]["criteria"][0]["description"] = (
        "A clearer explanation of the same requirement"
    )
    _, applied_update = await _submit_master(client, csrf, textual_update)
    assert applied_update.status_code == 200, applied_update.text
    latest = db.scalar(
        select(MasterImportRevision).order_by(MasterImportRevision.content_revision.desc())
    )
    assert latest is not None
    drift = deepcopy(textual_update)
    drift["packageId"] = "criterion-meaning-drift"
    drift["payload"]["contentRevision"] = 3
    drift["payload"]["previousContentDigest"] = latest.content_digest
    drift["payload"]["competencies"][0]["criteria"][0]["requirementType"] = "supporting"
    with pytest.raises(AppError, match="meaning"):
        inspect(db, drift)
    assert db.scalar(select(func.count()).select_from(MasterImportRevision)) == 2


async def test_master_other_lineage_cannot_claim_existing_stable_identity(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    first = package()
    _, applied = await _submit_master(client, csrf, first)
    assert applied.status_code == 200, applied.text
    other = deepcopy(first)
    other["packageId"] = "other-lineage-package"
    other["payload"]["lineageKey"] = "test.otherlineage"
    other["payload"]["ownerKey"] = "test.otherowner"
    with pytest.raises(AppError, match="not owned"):
        inspect(db, other)
    assert db.scalar(select(func.count()).select_from(MasterImportRevision)) == 1


async def test_master_child_removal_requires_explicit_successor_declaration(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    first = package()
    second_unit = deepcopy(first["payload"]["curriculum"]["units"][0])
    second_unit["stableKey"] = "test.second"
    second_unit["orderIndex"] = 1
    first["payload"]["curriculum"]["units"].append(second_unit)
    _, applied = await _submit_master(client, csrf, first)
    assert applied.status_code == 200, applied.text
    prior = db.scalar(select(MasterImportRevision))
    successor = deepcopy(first)
    successor["packageId"] = "test-second-removed"
    successor["payload"]["contentRevision"] = 2
    successor["payload"]["previousContentDigest"] = prior.content_digest
    successor["payload"]["curriculum"]["units"].pop()
    with pytest.raises(AppError, match="explicit declaration"):
        inspect(db, successor)
    successor["payload"]["removedFromActiveVersion"] = [
        {"entityKind": "unit", "stableKey": "test.second", "reason": "Superseded by entry unit"}
    ]
    preview, applied = await _submit_master(client, csrf, successor)
    assert preview.status_code == 200, preview.text
    assert applied.status_code == 200, applied.text
    assert db.scalar(select(func.count()).select_from(MasterImportRevision)) == 2
    assert (
        db.scalar(
            select(func.count())
            .select_from(MasterImportOwnedKey)
            .where(
                MasterImportOwnedKey.entity_kind == "unit",
                MasterImportOwnedKey.stable_key == "test.second",
            )
        )
        == 1
    )


async def test_master_successor_cannot_omit_an_owned_root(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    first = package()
    extra = deepcopy(first["payload"]["competencies"][0])
    extra["stableKey"] = "test.extra"
    extra["criteria"][0]["stableKey"] = "test.extracriterion"
    first["payload"]["competencies"].append(extra)
    _, applied = await _submit_master(client, csrf, first)
    assert applied.status_code == 200, applied.text
    prior = db.scalar(select(MasterImportRevision))
    successor = deepcopy(first)
    successor["packageId"] = "test-root-omission"
    successor["payload"]["contentRevision"] = 2
    successor["payload"]["previousContentDigest"] = prior.content_digest
    successor["payload"]["competencies"].pop()
    successor["payload"]["removedFromActiveVersion"] = [
        {
            "entityKind": "criterion",
            "stableKey": "test.extra::test.extracriterion",
            "reason": "Attempted root retirement",
        }
    ]
    with pytest.raises(AppError, match="previously owned root"):
        inspect(db, successor)
    assert db.scalar(select(func.count()).select_from(MasterImportRevision)) == 1


async def test_portable_v10_preserves_master_ledger(
    authenticated_client: tuple[AsyncClient, str],
    db: Session,
) -> None:
    client, csrf = authenticated_client
    _, applied = await _submit_master(client, csrf, package())
    assert applied.status_code == 200, applied.text
    portable = _portable_payload(db)
    assert portable["tables"]["master_import_revisions"]
    assert portable["tables"]["master_import_owned_keys"]
    tables, _ = _validate_portable_payload(portable, "master-ledger-v10", 10)
    assert len(tables["master_import_revisions"]) == 1
    _apply_portable_restore(db, portable, True, package_id="master-ledger-v10", schema_version=10)
    db.commit()
    validate_domain_integrity(db)
    assert db.scalar(select(func.count()).select_from(MasterImportRevision)) == 1
    assert db.scalar(select(func.count()).select_from(MasterImportOwnedKey)) > 1


async def test_master_ledger_chain_tamper_fails_domain_integrity(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    first = package()
    _, applied = await _submit_master(client, csrf, first)
    assert applied.status_code == 200, applied.text
    prior = db.scalar(select(MasterImportRevision))
    successor = deepcopy(first)
    successor["packageId"] = "tamper-chain-successor"
    successor["payload"]["contentRevision"] = 2
    successor["payload"]["previousContentDigest"] = prior.content_digest
    successor["payload"]["competencies"][0]["title"] = "Second revision"
    _, applied = await _submit_master(client, csrf, successor)
    assert applied.status_code == 200, applied.text
    db.execute(
        update(MasterImportRevision)
        .where(MasterImportRevision.content_revision == 2)
        .values(previous_content_digest="mi-content-v1:sha256:" + "0" * 64)
    )
    with pytest.raises(AppError, match="revision chain"):
        validate_domain_integrity(db)
    db.rollback()


async def test_master_all_unknown_first_action_unknown_and_short_time(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    _, applied = await _submit_master(client, csrf, package())
    assert applied.status_code == 200, applied.text
    assert db.scalar(select(func.count()).select_from(Activity)) == 0
    assert db.scalar(select(func.count()).select_from(Evidence)) == 0
    states = db.scalars(select(CompetencyCapabilityState)).all()
    assert all(state.capability_level_id is None for state in states)
    snapshot = run_analysis(
        db, idempotency_key="master-cold-analysis", purpose="learning_control", update_current=True
    )
    db.commit()
    for available, suffix in ((None, "unknown"), (900_000, "short")):
        run = generate_recommendations(
            db,
            idempotency_key=f"master-cold-recommendation-{suffix}",
            analysis_snapshot_id=snapshot.id,
            available_time_ms=available,
        )
        db.commit()
        detail = recommendation_detail(db, run.id)
        assert detail["portfolio"], detail
        generation = generate_today(
            db,
            idempotency_key=f"master-cold-today-{suffix}",
            analysis_snapshot_id=snapshot.id,
            available_time_ms=available,
            context_costs=(),
            regenerate=suffix == "short",
        )
        db.commit()
        assert (
            db.scalar(
                select(func.count())
                .select_from(TodaySuggestion)
                .where(TodaySuggestion.generation_id == generation.id)
            )
            >= 1
        )
