from __future__ import annotations

import ast
import hashlib
import json
import shutil
import sqlite3
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app import database
from app.analysis.contracts import content_hash
from app.curriculum.contracts import CurriculumVersionInput
from app.curriculum.models import (
    ActivityCurriculumLinkCorrection,
    ActivityCurriculumUnitLink,
    Curriculum,
    CurriculumActivationEvent,
    CurriculumVersion,
)
from app.curriculum.service import build_catalog
from app.errors import AppError
from app.import_export import (
    _apply_portable_restore,
    _portable_payload,
    _validate_portable_payload,
)
from app.models import CompetencyCapabilityState, Evidence, ProjectionInvalidation
from app.portability.registry import (
    PORTABLE_V2_MANIFEST,
    PORTABLE_V3_CURRICULUM_TABLES,
    PORTABLE_V4_PROJECT_TABLES,
)
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session


async def _semantic_target(
    client: AsyncClient, csrf: str
) -> tuple[dict[str, object], dict[str, object]]:
    scales = await client.get("/api/v2/capability-scales")
    technical = next(item for item in scales.json() if item["stableKey"] == "technical")
    competency = await client.post(
        "/api/v2/competencies",
        json={"stable_key": "python.curriculum", "creation_source": "test"},
        headers={"X-CSRF-Token": csrf},
    )
    assert competency.status_code == 201, competency.text
    definition = await client.post(
        f"/api/v2/competencies/{competency.json()['id']}/definitions",
        json={
            "title": "Python Curriculum",
            "description": "Exact target",
            "scope": "Python learning",
            "scale_stable_key": "technical",
            "scale_version": technical["version"],
            "dimension_keys": [],
            "effective_at": "2026-01-01T00:00:00Z",
            "creation_source": "test",
            "criteria": [
                {
                    "stable_key": "write-program",
                    "level_stable_key": "guided",
                    "dimension_key": None,
                    "requirement_type": "required",
                    "demonstration_rule": "guided_performance",
                    "description": "Write a guided program",
                }
            ],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert definition.status_code == 201, definition.text
    return technical, definition.json()


def _version_payload(
    technical: dict[str, object], definition: dict[str, object]
) -> dict[str, object]:
    levels = {item["stableKey"]: item for item in technical["levels"]}  # type: ignore[index]
    criterion = definition["criteria"][0]  # type: ignore[index]
    return {
        "title": "Python Foundations",
        "description": "Curated explicit content",
        "effective_at": "2026-01-02T00:00:00Z",
        "creation_source": "test",
        "objectives": [
            {
                "stable_key": "fundamentals",
                "title": "Fundamentals",
                "description": "Build foundations",
                "order_index": 0,
            }
        ],
        "units": [
            {
                "stable_key": "variables-practice",
                "objective_stable_key": "fundamentals",
                "kind": "practice_task",
                "title": "Practice variables",
                "description": "Write a small program",
                "action": {
                    "kind": "practice_task",
                    "instructions": "Implement a small variables exercise.",
                },
                "status": "active",
                "provenance": "curated",
                "order_index": 0,
                "minimum_useful_duration_ms": 1_800_000,
                "preferred_duration_ms": 2_700_000,
                "maximum_useful_duration_ms": 3_600_000,
                "targets": [
                    {
                        "semantic_definition_id": definition["id"],
                        "criterion_definition_id": criterion["id"],  # type: ignore[index]
                        "scale_version_id": technical["id"],
                        "dimension_id": None,
                        "intended_learning_outcome": "Use variables in a working program.",
                        "minimum_level_id": levels["unexposed"]["id"],
                        "maximum_level_id": levels["guided"]["id"],
                        "supports_unassessed": True,
                        "role": "primary",
                        "order_index": 0,
                    }
                ],
                "requirements": [
                    {
                        "stable_key": "editor-ready",
                        "requirement_type": "resource_available",
                        "effect": "hard",
                        "scope": "environment",
                        "subject": {"resourceKey": "local-editor"},
                        "order_index": 0,
                    }
                ],
                "evidence_opportunities": [
                    {
                        "stable_key": "guided-output",
                        "evidence_kind": "code",
                        "intended_strengths": ["moderate"],
                        "intended_independence_modes": ["guided"],
                        "requires_actual_activity": True,
                        "requires_artifact": True,
                        "order_index": 0,
                    }
                ],
            }
        ],
        "assessment_rubrics": [
            {
                "stable_key": "guided-rubric",
                "title": "Guided rubric",
                "instructions": "Assess the concrete result.",
                "rubric": {"criteria": ["program_runs"]},
                "semantic_definition_id": definition["id"],
                "criterion_definition_id": criterion["id"],  # type: ignore[index]
            }
        ],
    }


@pytest.mark.parametrize(
    ("kind", "action"),
    [
        ("resource", {"kind": "resource", "resource_reference": "https://example.test/book"}),
        ("exercise", {"kind": "exercise", "instructions": "Complete the exercise."}),
        (
            "practice_task",
            {"kind": "practice_task", "instructions": "Complete the practice task."},
        ),
        (
            "verification_template",
            {"kind": "verification_template", "verification_method": "Review the artifact."},
        ),
    ],
)
def test_learning_unit_actions_are_closed_discriminated_contracts(
    kind: str,
    action: dict[str, str],
) -> None:
    payload = {
        "title": "Typed actions",
        "effective_at": "2026-01-01T00:00:00Z",
        "units": [
            {
                "stable_key": "typed-action",
                "kind": kind,
                "title": "Typed action",
                "action": action,
                "provenance": "test",
                "order_index": 0,
            }
        ],
    }
    assert CurriculumVersionInput.model_validate(payload).units[0].action.kind == kind
    invalid = deepcopy(payload)
    invalid["units"][0]["action"]["unexpected"] = "not allowed"  # type: ignore[index]
    with pytest.raises(ValidationError):
        CurriculumVersionInput.model_validate(invalid)


def test_resource_action_rejects_embedded_credentials() -> None:
    payload = {
        "title": "Unsafe resource",
        "effective_at": "2026-01-01T00:00:00Z",
        "units": [
            {
                "stable_key": "unsafe-resource",
                "kind": "resource",
                "title": "Unsafe resource",
                "action": {
                    "kind": "resource",
                    "resource_reference": "https://user:secret@example.test/material",
                },
                "provenance": "test",
                "order_index": 0,
            }
        ],
    }
    with pytest.raises(ValidationError, match="credentials|user information"):
        CurriculumVersionInput.model_validate(payload)


async def test_curriculum_is_immutable_cutoff_correct_and_not_capability_truth(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    technical, definition = await _semantic_target(client, csrf)
    created = await client.post(
        "/api/v2/curricula",
        json={"stable_key": "python-foundations", "creation_source": "test"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    curriculum_id = created.json()["id"]
    capability_before = db.scalar(
        select(func.count(CompetencyCapabilityState.competency_identity_id))
    )
    evidence_before = db.scalar(select(func.count(Evidence.id)))
    version = await client.post(
        f"/api/v2/curricula/{curriculum_id}/versions",
        json=_version_payload(technical, definition),
        headers={"X-CSRF-Token": csrf},
    )
    assert version.status_code == 201, version.text
    first_id = version.json()["id"]
    activation = await client.post(
        f"/api/v2/curricula/{curriculum_id}/versions/{first_id}/activate",
        json={"reason": "Use curated material", "source": "test", "idempotency_key": "activate-1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert activation.status_code == 200, activation.text
    event = db.scalar(select(CurriculumActivationEvent))
    assert event is not None
    assert build_catalog(db, event.activated_at).units == ()
    first_catalog = build_catalog(db, event.activated_at + 1)
    assert [item.unit_stable_key for item in first_catalog.units] == ["variables-practice"]
    assert first_catalog == build_catalog(db, event.activated_at + 1)
    with pytest.raises(FrozenInstanceError):
        first_catalog.active_version_references[0].version = 2  # type: ignore[misc]
    availability = await client.get(
        f"/api/v2/curricula/units/{first_catalog.units[0].unit_definition_id}/availability",
        params={"cutoff_at": event.activated_at + 1},
    )
    assert availability.status_code == 200, availability.text
    assert availability.json()["availabilityState"] == "unknown"
    assert availability.json()["readinessState"] == "met"
    assert availability.json()["candidateUsabilityState"] == "unknown"
    suitability = availability.json()["targetSuitability"]
    assert len(suitability) == 1
    assert suitability[0] | {"targetId": "ignored"} == {
        "targetId": "ignored",
        "semanticDefinitionId": definition["id"],
        "criterionDefinitionId": definition["criteria"][0]["id"],  # type: ignore[index]
        "scaleVersionId": technical["id"],
        "dimensionId": None,
        "role": "primary",
        "state": "met",
        "reasonCode": "UNASSESSED_SUPPORTED",
        "selectedLevelId": None,
        "supportsUnassessed": True,
        "profileRelevanceState": "unknown",
        "targetProfileVersionId": None,
        "profileTargetIdentityIds": [],
        "candidateUsabilityState": "unknown",
    }
    assert (
        db.scalar(
            select(func.count(ProjectionInvalidation.id)).where(
                ProjectionInvalidation.source_fact_id == event.id
            )
        )
        == 3
    )
    assert (
        db.scalar(select(func.count(CompetencyCapabilityState.competency_identity_id)))
        == capability_before
    )
    assert db.scalar(select(func.count(Evidence.id))) == evidence_before

    replacement_payload = _version_payload(technical, definition)
    replacement_payload["title"] = "Python Foundations Revised"
    replacement = await client.post(
        f"/api/v2/curricula/{curriculum_id}/versions",
        json=replacement_payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert replacement.status_code == 201, replacement.text
    assert replacement.json()["supersedesVersionId"] == first_id

    incompatible = _version_payload(technical, definition)
    incompatible["units"][0]["kind"] = "resource"  # type: ignore[index]
    incompatible["units"][0]["action"] = {  # type: ignore[index]
        "kind": "resource",
        "resource_reference": "https://example.test/material",
    }
    rejected = await client.post(
        f"/api/v2/curricula/{curriculum_id}/versions",
        json=incompatible,
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "LEARNING_UNIT_IDENTITY_INCOMPATIBLE"

    future_payload = _version_payload(technical, definition)
    future_payload["title"] = "Future version"
    future_payload["effective_at"] = "2100-01-01T00:00:00Z"
    future = await client.post(
        f"/api/v2/curricula/{curriculum_id}/versions",
        json=future_payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert future.status_code == 201, future.text
    future_activation = await client.post(
        f"/api/v2/curricula/{curriculum_id}/versions/{future.json()['id']}/activate",
        json={"reason": "Too early", "source": "test", "idempotency_key": "activate-future"},
        headers={"X-CSRF-Token": csrf},
    )
    assert future_activation.status_code == 409
    assert future_activation.json()["error"]["code"] == "CURRICULUM_VERSION_NOT_EFFECTIVE"

    cyclic = _version_payload(technical, definition)
    first_unit = cyclic["units"][0]  # type: ignore[index]
    first_unit["requirements"] = [  # type: ignore[index]
        {
            "stable_key": "requires-second",
            "requirement_type": "learning_unit_completed",
            "effect": "hard",
            "scope": "curriculum",
            "subject": {"learningUnitStableKey": "second-unit"},
            "order_index": 0,
        }
    ]
    second_unit = deepcopy(first_unit)
    second_unit["stable_key"] = "second-unit"
    second_unit["order_index"] = 1
    second_unit["requirements"] = [
        {
            "stable_key": "requires-first",
            "requirement_type": "learning_unit_completed",
            "effect": "hard",
            "scope": "curriculum",
            "subject": {"learningUnitStableKey": "variables-practice"},
            "order_index": 0,
        }
    ]
    cyclic["units"].append(second_unit)  # type: ignore[union-attr]
    cycle_response = await client.post(
        f"/api/v2/curricula/{curriculum_id}/versions/validate",
        json=cyclic,
        headers={"X-CSRF-Token": csrf},
    )
    assert cycle_response.status_code == 422
    assert cycle_response.json()["error"]["code"] == "CURRICULUM_REQUIREMENT_CYCLE"
    with pytest.raises(ValueError, match="immutable"):
        first = db.get(CurriculumVersion, first_id)
        assert first is not None
        first.title = "Forbidden mutation"
        db.flush()
    db.rollback()


async def test_duration_validation_and_activity_link_correction(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    technical, definition = await _semantic_target(client, csrf)
    created = await client.post(
        "/api/v2/curricula",
        json={"stable_key": "portable-curriculum", "creation_source": "test"},
        headers={"X-CSRF-Token": csrf},
    )
    curriculum_id = created.json()["id"]
    invalid = _version_payload(technical, definition)
    invalid["units"][0]["preferred_duration_ms"] = None  # type: ignore[index]
    response = await client.post(
        f"/api/v2/curricula/{curriculum_id}/versions",
        json=invalid,
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "DURATION_RANGE_INVALID"
    valid = await client.post(
        f"/api/v2/curricula/{curriculum_id}/versions",
        json=_version_payload(technical, definition),
        headers={"X-CSRF-Token": csrf},
    )
    unit_id = valid.json()["units"][0]["unitDefinitionId"]
    activated = await client.post(
        f"/api/v2/curricula/{curriculum_id}/versions/{valid.json()['id']}/activate",
        json={
            "reason": "Portable active Curriculum",
            "source": "test",
            "idempotency_key": "portable-curriculum-active",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert activated.status_code == 200, activated.text
    activity = await client.post(
        "/api/v2/activities",
        json={
            "title": "Actual work",
            "description": None,
            "category_stable_key": "practice",
            "occurred_at": "2026-01-03T00:00:00Z",
            "outcome_classification": "completed",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert activity.status_code == 201, activity.text
    link = await client.post(
        "/api/v2/curricula/activity-links",
        json={
            "activity_id": activity.json()["id"],
            "learning_unit_definition_id": unit_id,
            "provenance": "user_selected",
            "idempotency_key": "unit-link-1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert link.status_code == 201, link.text
    correction = await client.post(
        f"/api/v2/curricula/activity-links/{link.json()['id']}/correct",
        json={
            "replacement_link_id": None,
            "reason": "The unit attribution was incorrect",
            "idempotency_key": "unit-link-correction-1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert correction.status_code == 201, correction.text
    assert db.scalar(select(func.count(ActivityCurriculumUnitLink.id))) == 1
    assert db.scalar(select(func.count(ActivityCurriculumLinkCorrection.id))) == 1


async def test_learning_unit_completion_is_bitemporal_cutoff_exclusive_and_portable(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    technical, definition = await _semantic_target(client, csrf)
    created = await client.post(
        "/api/v2/curricula",
        json={"stable_key": "completion-cutoff", "creation_source": "test"},
        headers={"X-CSRF-Token": csrf},
    )
    payload = _version_payload(technical, definition)
    prerequisite = payload["units"][0]  # type: ignore[index]
    prerequisite["requirements"] = []  # type: ignore[index]
    follow_up = deepcopy(prerequisite)
    follow_up["stable_key"] = "follow-up"
    follow_up["order_index"] = 1
    follow_up["requirements"] = [
        {
            "stable_key": "prior-completed",
            "requirement_type": "learning_unit_completed",
            "effect": "hard",
            "scope": "curriculum",
            "subject": {"learningUnitStableKey": "variables-practice"},
            "order_index": 0,
        }
    ]
    payload["units"].append(follow_up)  # type: ignore[union-attr]
    version = await client.post(
        f"/api/v2/curricula/{created.json()['id']}/versions",
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert version.status_code == 201, version.text
    units = {item["unitStableKey"]: item for item in version.json()["units"]}
    activated = await client.post(
        f"/api/v2/curricula/{created.json()['id']}/versions/{version.json()['id']}/activate",
        json={
            "reason": "Cutoff test",
            "source": "test",
            "idempotency_key": "cutoff-curriculum-active",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert activated.status_code == 200, activated.text

    future_activity = await client.post(
        "/api/v2/activities",
        json={
            "title": "Future completed work",
            "category_stable_key": "practice",
            "occurred_at": "2100-01-01T00:00:00Z",
            "outcome_classification": "completed",
        },
        headers={"X-CSRF-Token": csrf},
    )
    future_link = await client.post(
        "/api/v2/curricula/activity-links",
        json={
            "activity_id": future_activity.json()["id"],
            "learning_unit_definition_id": units["variables-practice"]["unitDefinitionId"],
            "provenance": "user_selected",
            "idempotency_key": "future-completion-link",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert future_link.status_code == 201, future_link.text
    future_row = db.get(ActivityCurriculumUnitLink, future_link.json()["id"])
    assert future_row is not None
    before_occurrence = await client.get(
        f"/api/v2/curricula/units/{units['follow-up']['unitDefinitionId']}/availability",
        params={"cutoff_at": future_row.created_at + 1},
    )
    assert before_occurrence.json()["readinessState"] == "not_met"

    actual_activity = await client.post(
        "/api/v2/activities",
        json={
            "title": "Actual completed work",
            "category_stable_key": "practice",
            "occurred_at": "2026-01-03T00:00:00Z",
            "outcome_classification": "completed",
        },
        headers={"X-CSRF-Token": csrf},
    )
    actual_link = await client.post(
        "/api/v2/curricula/activity-links",
        json={
            "activity_id": actual_activity.json()["id"],
            "learning_unit_definition_id": units["variables-practice"]["unitDefinitionId"],
            "provenance": "user_selected",
            "idempotency_key": "actual-completion-link",
        },
        headers={"X-CSRF-Token": csrf},
    )
    actual_row = db.get(ActivityCurriculumUnitLink, actual_link.json()["id"])
    assert actual_row is not None
    exact = await client.get(
        f"/api/v2/curricula/units/{units['follow-up']['unitDefinitionId']}/availability",
        params={"cutoff_at": actual_row.created_at},
    )
    after = await client.get(
        f"/api/v2/curricula/units/{units['follow-up']['unitDefinitionId']}/availability",
        params={"cutoff_at": actual_row.created_at + 1},
    )
    assert exact.json()["readinessState"] == "not_met"
    assert after.json()["readinessState"] == "met"

    exported = await client.post(
        "/api/v1/import-export/export",
        json={"purpose": "portable_logical_backup", "format": "json"},
        headers={"X-CSRF-Token": csrf},
    )
    assert exported.status_code == 200, exported.text
    package = exported.json()["content"]
    assert package["schemaVersion"] == 4
    assert len(package["payload"]["tables"]["curricula"]) == 1
    assert len(package["payload"]["tables"]["activity_curriculum_link_corrections"]) == 0
    checkpoint = package["payload"]["curriculumCatalogCheckpoint"]
    assert checkpoint["cutoffSemantics"] == "exclusive"
    assert checkpoint["policyVersion"] == "curriculum-availability-policy/v1"
    assert len(checkpoint["availabilityHashes"]) == 2

    tampered_checkpoint = deepcopy(package["payload"])
    tampered_checkpoint["curriculumCatalogCheckpoint"]["catalogHash"] = "0" * 64
    tampered_body = tampered_checkpoint["curriculumCatalogCheckpoint"]
    tampered_body["inputHash"] = content_hash(
        {key: value for key, value in tampered_body.items() if key != "inputHash"}
    )
    with pytest.raises(AppError, match="rebuilt Curriculum catalog"):
        _validate_portable_payload(
            tampered_checkpoint, "curriculum-checkpoint-tampered", schema_version=4
        )
    tables_before_failed_restore = deepcopy(_portable_payload(db)["tables"])
    with pytest.raises(AppError, match="rebuilt Curriculum catalog"):
        _apply_portable_restore(
            db,
            tampered_checkpoint,
            True,
            package_id="curriculum-checkpoint-tampered",
            schema_version=4,
        )
    db.rollback()
    db.expire_all()
    assert _portable_payload(db)["tables"] == tables_before_failed_restore

    broken_lineage = deepcopy(package["payload"])
    broken_lineage["tables"]["curriculum_versions"][0]["version"] = 2
    with pytest.raises(AppError, match="versions must be contiguous"):
        _validate_portable_payload(broken_lineage, "curriculum-lineage-tampered", schema_version=4)

    unsafe_resource = deepcopy(package["payload"])
    unit_row = unsafe_resource["tables"]["learning_unit_definitions"][0]
    unit_row["kind"] = "resource"
    unit_row["action_payload_json"] = (
        '{"kind":"resource","resource_reference":"https://user:secret@example.test/material"}'
    )
    version_row = unsafe_resource["tables"]["curriculum_versions"][0]
    envelope = json.loads(version_row["definition_payload_json"])
    envelope["units"][0]["kind"] = "resource"
    envelope["units"][0]["action"] = {
        "kind": "resource",
        "resource_reference": "https://user:secret@example.test/material",
    }
    version_row["definition_payload_json"] = json.dumps(
        envelope, sort_keys=True, separators=(",", ":")
    )
    version_row["content_hash"] = content_hash(envelope)
    with pytest.raises(AppError, match="definition envelope is malformed"):
        _validate_portable_payload(unsafe_resource, "unsafe-resource", schema_version=4)

    future_activation = deepcopy(package["payload"])
    activation_row = future_activation["tables"]["curriculum_activation_events"][0]
    activation_row["activated_at"] = 1
    future_activation["tables"]["active_curriculum_version_states"][0]["activated_at"] = 1
    with pytest.raises(AppError, match="activation history is inconsistent"):
        _validate_portable_payload(future_activation, "future-activation", schema_version=4)

    early_link = deepcopy(package["payload"])
    link_row = early_link["tables"]["activity_curriculum_unit_links"][0]
    activity_row = next(
        item for item in early_link["tables"]["activities"] if item["id"] == link_row["activity_id"]
    )
    link_row["created_at"] = activity_row["created_at"] - 1
    with pytest.raises(AppError, match="invalid provenance or chronology"):
        _validate_portable_payload(early_link, "early-activity-link", schema_version=4)
    preview = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "curriculum-v3.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )
    assert preview.status_code == 200, preview.text
    restored = await client.post(
        "/api/v1/import-export/import/apply",
        json={
            "filename": "curriculum-v3.json",
            "package": package,
            "confirmation_token": preview.json()["confirmationToken"],
            "replace_existing": True,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert restored.status_code == 200, restored.text
    db.expire_all()
    assert db.scalar(select(func.count(Curriculum.id))) == 1
    assert db.scalar(select(func.count(ActivityCurriculumUnitLink.id))) == 2
    assert db.scalar(select(func.count(ActivityCurriculumLinkCorrection.id))) == 0


def test_no_v1_curriculum_backfill(db: Session) -> None:
    assert db.scalar(select(func.count(Curriculum.id))) == 0


def test_curriculum_fact_dependencies_use_public_views() -> None:
    app_root = Path(__file__).parents[1] / "app"
    service = (app_root / "curriculum" / "service.py").read_text()
    requirements = (app_root / "requirements" / "contracts.py").read_text()
    assert "from app.capability_views import" in service
    assert "from app.activity_views import" in service
    assert "from app.profile_views import" in service
    tree = ast.parse(service)
    model_imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "app.models"
        for alias in node.names
    }
    forbidden = {
        "Activity",
        "CapabilityEvaluationRun",
        "CriterionEvaluationResult",
        "ProfileTarget",
        "TargetProfile",
    }
    assert model_imports.isdisjoint(forbidden)
    assert "sqlalchemy" not in requirements.lower()


def test_portable_v2_to_v3_adapter_is_empty_and_deterministic(db: Session) -> None:
    payload = deepcopy(_portable_payload(db))
    payload["manifest"] = PORTABLE_V2_MANIFEST
    payload.pop("curriculumCatalogCheckpoint")
    payload.pop("projectCatalogCheckpoint")
    payload.pop("portableScope")
    for table_name in PORTABLE_V3_CURRICULUM_TABLES:
        payload["tables"].pop(table_name)
    for table_name in PORTABLE_V4_PROJECT_TABLES:
        payload["tables"].pop(table_name)
    first, _summary = _validate_portable_payload(payload, "v2-adapter", schema_version=2)
    second, _summary = _validate_portable_payload(payload, "v2-adapter", schema_version=2)
    assert first == second
    assert all(first[table_name] == [] for table_name in PORTABLE_V3_CURRICULUM_TABLES)


def test_populated_0010_fixture_is_pinned_and_curriculum_upgrade_invents_nothing(
    tmp_path: Path,
) -> None:
    fixture_root = Path(__file__).parent / "fixtures" / "v2"
    expected = {
        line.split()[1]: line.split()[0]
        for line in (fixture_root / "SHA256SUMS").read_text().splitlines()
    }
    source = fixture_root / "populated-0010.sqlite3"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == expected[source.name]
    database_path = tmp_path / source.name
    shutil.copyfile(source, database_path)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    url = f"sqlite:///{database_path}"
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["database_url"] = url
    connection = sqlite3.connect(database_path)
    phase_one_tables = (
        "roadmaps",
        "target_profiles",
        "target_profile_versions",
        "semantic_competency_definitions",
        "activities",
        "learning_sessions",
        "evidence",
        "capability_evaluation_runs",
        "competency_capability_states",
        "review_events",
        "analysis_snapshots",
        "recommendation_snapshots",
    )
    before = {
        table: connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
        for table in phase_one_tables
    }
    assert all(before.values())
    connection.close()
    command.upgrade(config, "0011_curriculum_core")
    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0011_curriculum_core",
        )
        assert connection.execute("SELECT COUNT(*) FROM curricula").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM learning_unit_definitions").fetchone() == (
            0,
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert before == {
            table: connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            for table in before
        }
    finally:
        connection.close()
    command.downgrade(config, "0010_capability_evaluation")
    command.upgrade(config, "0011_curriculum_core")


def test_populated_curriculum_downgrade_fails_closed(tmp_path: Path) -> None:
    fixture_root = Path(__file__).parent / "fixtures" / "v2"
    database_path = tmp_path / "populated-curriculum.sqlite3"
    shutil.copyfile(fixture_root / "populated-0010.sqlite3", database_path)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    url = f"sqlite:///{database_path}"
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["database_url"] = url
    command.upgrade(config, "0011_curriculum_core")
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "INSERT INTO curricula "
            "(id, stable_key, created_at, creation_source, retired_at, retirement_reason) "
            "VALUES ('curriculum-fixture', 'fixture', 1, 'test', NULL, NULL)"
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(RuntimeError, match="Refusing to downgrade populated canonical Curriculum"):
        command.downgrade(config, "0010_capability_evaluation")
    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0011_curriculum_core",
        )
        assert connection.execute("SELECT COUNT(*) FROM curricula").fetchone() == (1,)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "partial_sql",
    [
        "CREATE TABLE curricula (id TEXT PRIMARY KEY)",
        "CREATE TABLE _alembic_tmp_learning_unit_targets (id TEXT PRIMARY KEY)",
    ],
)
def test_partial_curriculum_migration_is_refused(tmp_path: Path, partial_sql: str) -> None:
    fixture_root = Path(__file__).parent / "fixtures" / "v2"
    database_path = tmp_path / "partial-curriculum.sqlite3"
    shutil.copyfile(fixture_root / "populated-0010.sqlite3", database_path)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(partial_sql)
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(RuntimeError, match="ambiguously partial Curriculum migration"):
        database.run_migrations(f"sqlite:///{database_path}")
