from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from app.compatibility.v1.portable import (
    UnsupportedV1PortableSchema,
    read_v1_portable_package,
    upgrade_v1_profile_competency_tables,
)
from app.portability.registry import PORTABLE_V2_MANIFEST
from httpx import AsyncClient


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
