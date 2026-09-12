from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from app.compatibility.v1.portable import (
    UnsupportedV1PortableSchema,
    read_v1_portable_package,
    upgrade_v1_activity_session_tables,
    upgrade_v1_evidence_tables,
    upgrade_v1_profile_competency_tables,
)
from app.models import Activity, CriterionIdentity
from app.portability.registry import PORTABLE_V2_MANIFEST
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session


def test_frozen_v1_reader_accepts_v1_and_rejects_v2() -> None:
    fixture = Path(__file__).parent / "fixtures" / "v1" / "portable-empty-schema-v1.json"
    package = json.loads(fixture.read_text())
    assert read_v1_portable_package(package) is package
    package["schemaVersion"] = 2
    with pytest.raises(UnsupportedV1PortableSchema):
        read_v1_portable_package(package)


def test_v1_upgrade_uses_database_migration_tie_break_for_legacy_criteria() -> None:
    tables = {
        "competency_identities": [{"id": "competency", "stable_key": "competency"}],
        "roadmaps": [{"id": "roadmap", "is_current": True, "active_version_id": "version"}],
        "roadmap_versions": [{"id": "version", "roadmap_id": "roadmap"}],
        "competency_definitions": [{"id": "definition", "roadmap_version_id": "version"}],
        "exit_criterion_identities": [
            {
                "id": "criterion",
                "competency_identity_id": "competency",
                "stable_key": "criterion",
                "current_state": "partial",
                "updated_at": 123,
            }
        ],
        "exit_criterion_definitions": [
            {
                "id": "definition-a",
                "exit_criterion_identity_id": "criterion",
                "competency_definition_id": "definition",
                "created_at": 456,
                "required": False,
            },
            {
                "id": "definition-z",
                "exit_criterion_identity_id": "criterion",
                "competency_definition_id": "definition",
                "created_at": 456,
                "required": True,
            },
        ],
    }

    first_report = upgrade_v1_profile_competency_tables(tables)
    first_result = copy.deepcopy(tables)
    second_report = upgrade_v1_profile_competency_tables(tables)

    assertion = tables["legacy_criterion_assertions"][0]
    assert assertion["source_exit_criterion_definition_id"] == "definition-z"
    assert assertion["requirement_type"] == "required"
    assert tables == first_result
    assert second_report == first_report
    assert len(first_report["legacyCriterionSourceHash"]) == 64
    assert len(first_report["legacyCriterionResultHash"]) == 64


def test_v1_activity_upgrade_is_deterministic_and_idempotent() -> None:
    tables = {
        "learning_sessions": [
            {
                "id": "session-with-target",
                "competency_identity_id": "competency",
                "activity_type": "coding",
                "started_at": 10,
                "ended_at": 20,
                "outcome": "completed",
                "created_at": 30,
            },
            {
                "id": "session-without-target",
                "competency_identity_id": None,
                "activity_type": "review",
                "started_at": 40,
                "ended_at": 50,
                "outcome": "partial",
                "created_at": 60,
            },
        ],
        "migration_backfill_runs": [{"id": "unrelated"}],
    }
    first_report = upgrade_v1_activity_session_tables(tables)
    first_result = copy.deepcopy(tables)
    second_report = upgrade_v1_activity_session_tables(tables)

    assert tables == first_result
    assert second_report == first_report
    assert len(tables["activities"]) == 2
    assert len(tables["session_contributions"]) == 1
    assert set(tables["session_contributions"][0]) == {
        "id",
        "session_id",
        "competency_identity_id",
        "criterion_identity_id",
        "relevance",
        "created_at",
        "provenance",
    }
    assert len(first_report["legacySessionSourceHash"]) == 64
    assert len(first_report["legacySessionResultHash"]) == 64


def test_v1_evidence_upgrade_is_deterministic_and_records_import_provenance() -> None:
    tables = {
        "verification_records": [],
        "verification_evidence": [],
        "learning_sessions": [
            {
                "id": "session",
                "activity_id": "activity",
                "competency_identity_id": "competency",
                "activity_type": "coding",
                "assistance_mode": "none",
                "started_at": 10,
                "duration_ms": 10,
                "outcome": "completed",
                "timed_state": None,
                "tombstoned_at": None,
                "created_at": 20,
            }
        ],
        "session_contributions": [
            {
                "id": "contribution",
                "session_id": "session",
                "competency_identity_id": "competency",
                "criterion_identity_id": None,
                "relevance": "primary",
                "created_at": 20,
                "provenance": "deterministic_legacy_backfill",
            }
        ],
        "contribution_retractions": [],
        "migration_backfill_runs": [{"id": "unrelated"}],
    }
    first = upgrade_v1_evidence_tables(tables, import_package_id="package-a")
    first_result = copy.deepcopy(tables)
    second = upgrade_v1_evidence_tables(tables, import_package_id="package-a")
    assert tables == first_result
    assert first == second
    assert len(tables["evidence"]) == 1
    assert len(tables["evidence_links"]) == 1
    provenance = json.loads(tables["evidence"][0]["provenance_json"])
    assert provenance["origin_kind"] == "import"
    assert provenance["import_package_id"] == "package-a"
    assert tables["evidence"][0]["strength"] == "unknown"
    assert tables["evidence"][0]["source_confidence"] == "unknown"


async def test_runtime_dispatch_rejects_v2_tables_in_v1_package(
    authenticated_client: tuple[AsyncClient, str],
) -> None:
    client, csrf = authenticated_client
    fixture = Path(__file__).parent / "fixtures" / "v1" / "portable-empty-schema-v1.json"
    package = json.loads(fixture.read_text())
    package["packageId"] = "v1-with-v2-table"
    package["payload"]["tables"]["analysis_runs"] = []
    response = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "invalid-v1.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PORTABLE_SCHEMA_INVALID"


async def test_portable_v2_manifest_and_tampered_analysis_hash_rejection(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
) -> None:
    client, csrf, _roadmap = configured_client
    recommendation = await client.get("/api/v1/recommendations/today")
    assert recommendation.status_code == 200
    exported = await client.post(
        "/api/v1/import-export/export",
        json={"purpose": "portable_logical_backup", "format": "json"},
        headers={"X-CSRF-Token": csrf},
    )
    assert exported.status_code == 200
    package = exported.json()["content"]
    assert package["schemaVersion"] == 2
    assert package["payload"]["manifest"] == PORTABLE_V2_MANIFEST
    assert "projection_invalidations" not in package["payload"]["tables"]
    package["packageId"] = "tampered-analysis-history"
    package["payload"]["tables"]["analysis_snapshots"][0]["input_hash"] = "0" * 64
    inspected = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "tampered.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )
    assert inspected.status_code == 422
    assert inspected.json()["error"]["code"] == "PORTABLE_ANALYSIS_INVALID"


async def test_portable_restore_defers_activity_supersession_and_criterion_contribution_fks(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, _roadmap = configured_client
    criterion = db.scalar(select(CriterionIdentity))
    assert criterion is not None
    original = await client.post(
        "/api/v2/activities",
        json={"title": "Original", "category_stable_key": "practice"},
        headers={"X-CSRF-Token": csrf},
    )
    predecessor_id = original.json()["id"]
    successor = Activity(
        title="Corrected",
        category_stable_key="practice",
        category_version="v1",
        creator_source="user",
        provenance="user_recorded",
        supersedes_activity_id=predecessor_id,
    )
    db.add(successor)
    db.commit()
    session = await client.post(
        "/api/v2/sessions/manual",
        json={
            "activity_id": successor.id,
            "assistance_mode": "none",
            "started_at": "2026-09-10T10:00:00Z",
            "duration_ms": 600_000,
            "outcome": "completed",
            "contributions": [
                {
                    "competency_identity_id": criterion.competency_identity_id,
                    "criterion_identity_id": criterion.id,
                    "relevance": "primary",
                }
            ],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert session.status_code == 201, session.text
    exported = await client.post(
        "/api/v1/import-export/export",
        json={"purpose": "portable_logical_backup", "format": "json"},
        headers={"X-CSRF-Token": csrf},
    )
    package = exported.json()["content"]
    package["packageId"] = "activity-order-round-trip"
    package["payload"]["tables"]["activities"].reverse()
    preview = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "round-trip.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )
    assert preview.status_code == 200, preview.text
    restored = await client.post(
        "/api/v1/import-export/import/apply",
        json={
            "filename": "round-trip.json",
            "package": package,
            "confirmation_token": preview.json()["confirmationToken"],
            "replace_existing": True,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert restored.status_code == 200, restored.text
