from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from app.import_export import PORTABLE_BY_TABLE
from app.schemas import (
    CompetencyInput,
    ExitCriterionInput,
    ImportEnvelope,
    PhaseInput,
    RoadmapCreate,
    RoadmapPackagePayload,
    TrackInput,
)
from httpx import AsyncClient

GUIDE_PATH = Path(__file__).parents[2] / "docs" / "IMPORT_EXPORT_FORMAT.md"


def _guide_text() -> str:
    return GUIDE_PATH.read_text(encoding="utf-8")


def _json_after_heading(heading: str) -> dict[str, Any]:
    match = re.search(
        rf"^#{{2,3}} {re.escape(heading)}\s.*?^```json\s*\n(.*?)^```$",
        _guide_text(),
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"Missing JSON example after {heading!r}"
    value = json.loads(match.group(1))
    assert isinstance(value, dict)
    return value


def test_every_json_fence_is_valid_json() -> None:
    blocks = re.findall(r"^```json\s*\n(.*?)^```$", _guide_text(), re.MULTILINE | re.DOTALL)
    assert blocks
    for block in blocks:
        json.loads(block)


@pytest.mark.parametrize(
    ("heading", "expected_phases", "expected_competencies"),
    [
        ("Minimal valid roadmap package", 1, 1),
        ("Realistic disposable roadmap package", 3, 12),
    ],
)
async def test_documented_roadmaps_pass_real_inspect_and_apply(
    authenticated_client: tuple[AsyncClient, str],
    heading: str,
    expected_phases: int,
    expected_competencies: int,
) -> None:
    client, csrf = authenticated_client
    package = _json_after_heading(heading)

    inspection = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "format-guide.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )
    assert inspection.status_code == 200, inspection.text
    assert inspection.json()["dryRun"] is True

    applied = await client.post(
        "/api/v1/import-export/import/apply",
        json={
            "filename": "format-guide.json",
            "package": package,
            "confirmation_token": inspection.json()["confirmationToken"],
            "replace_existing": False,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert applied.status_code == 200, applied.text

    current = await client.get("/api/v1/roadmap/current")
    assert current.status_code == 200
    roadmap = current.json()["roadmap"]
    competencies = [
        competency
        for phase in roadmap["phases"]
        for track in phase["tracks"]
        for competency in track["competencies"]
    ]
    assert len(roadmap["phases"]) == expected_phases
    assert len(competencies) == expected_competencies
    assert all(competency["identityId"] for competency in competencies)


async def test_documented_empty_portable_package_passes_inspect_and_apply(
    authenticated_client: tuple[AsyncClient, str],
) -> None:
    client, csrf = authenticated_client
    package = _json_after_heading("Representative empty portable package")
    assert set(package["payload"]["tables"]) == set(PORTABLE_BY_TABLE)
    inspection = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "empty-portable.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )
    assert inspection.status_code == 200, inspection.text
    assert inspection.json()["summary"]["authenticationPreserved"] is True

    applied = await client.post(
        "/api/v1/import-export/import/apply",
        json={
            "filename": "empty-portable.json",
            "package": package,
            "confirmation_token": inspection.json()["confirmationToken"],
            "replace_existing": False,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["authenticationPreserved"] is True


def test_documented_contract_taxonomy_and_examples_are_secret_free() -> None:
    guide = _guide_text()
    for package_type in (
        "roadmap_update",
        "roadmap_replace",
        "verification_update",
        "state_update",
        "portable_logical_backup",
        "restore",
        "analysis_snapshot",
    ):
        assert f"`{package_type}`" in guide

    roadmap_examples = [
        _json_after_heading("Minimal valid roadmap package"),
        _json_after_heading("Realistic disposable roadmap package"),
    ]
    serialized = json.dumps(roadmap_examples)
    minimal = roadmap_examples[0]
    assert set(minimal) == set(ImportEnvelope.model_fields)
    assert set(minimal["payload"]) == set(RoadmapPackagePayload.model_fields)
    roadmap = minimal["payload"]["roadmap"]
    assert set(roadmap) == set(RoadmapCreate.model_fields)
    phase = roadmap["phases"][0]
    assert set(phase) == set(PhaseInput.model_fields)
    track = phase["tracks"][0]
    assert set(track) == set(TrackInput.model_fields)
    competency = track["competencies"][0]
    assert set(competency) == set(CompetencyInput.model_fields)
    assert set(competency["exit_criteria"][0]) == set(ExitCriterionInput.model_fields)
    for forbidden in (
        "password_hash",
        "token_lookup_hash",
        "csrf_secret_hash",
        "auth_sessions",
        "users",
    ):
        assert forbidden not in serialized
