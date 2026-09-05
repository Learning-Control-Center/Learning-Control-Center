from __future__ import annotations

from copy import deepcopy

from app.models import AuthSession, CompetencyIdentity, ImportRecord, LearningSession, Track, User
from app.time_utils import utc_now_ms
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _package(package_type: str, package_id: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "packageType": package_type,
        "packageId": package_id,
        "appVersion": "1.0.0",
        "createdAt": "2026-09-04T18:30:00.000Z",
        "payload": payload,
    }


async def _portable_export(client: AsyncClient, csrf: str) -> dict[str, object]:
    response = await client.post(
        "/api/v1/import-export/export",
        json={"purpose": "portable_logical_backup", "format": "json"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    return response.json()["content"]


async def test_export_taxonomy_and_secret_exclusion(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, _roadmap = configured_client
    invalid = await client.post(
        "/api/v1/import-export/export",
        json={"purpose": "portable_logical_backup", "format": "markdown"},
        headers={"X-CSRF-Token": csrf},
    )
    assert invalid.status_code == 422

    package = await _portable_export(client, csrf)
    tables = package["payload"]["tables"]
    assert "users" not in tables
    assert "auth_sessions" not in tables
    serialized = str(package)
    assert "password_hash" not in serialized
    assert "token_lookup_hash" not in serialized
    assert "csrf_secret_hash" not in serialized

    analysis = await client.post(
        "/api/v1/import-export/export",
        json={
            "purpose": "analysis_snapshot",
            "format": "json",
            "range": "30d",
            "categories": ["analytics"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert analysis.status_code == 200
    assert set(analysis.json()["content"]["payload"]) == {"scope", "analytics"}

    basics = db.scalar(
        select(CompetencyIdentity).where(CompetencyIdentity.stable_key == "python.basics")
    )
    functions = db.scalar(
        select(CompetencyIdentity).where(CompetencyIdentity.stable_key == "python.functions")
    )
    python_track = db.scalar(select(Track).where(Track.stable_key == "python"))
    started_at = utc_now_ms() - 60_000
    db.add(
        LearningSession(
            competency_identity_id=basics.id,
            track_id=python_track.id,
            session_mode="manual",
            activity_type="learning",
            assistance_mode="none",
            started_at=started_at,
            ended_at=started_at + 60_000,
            accumulated_duration_ms=60_000,
            duration_ms=60_000,
            outcome="completed",
        )
    )
    db.commit()
    selective = await client.post(
        "/api/v1/import-export/export",
        json={
            "purpose": "analysis_snapshot",
            "format": "json",
            "range": "7d",
            "categories": ["analytics", "sessions"],
            "competency_identity_ids": [basics.id],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert selective.status_code == 200, selective.text
    payload = selective.json()["content"]["payload"]
    assert set(payload["analytics"]["competencies"]) == {basics.id}
    assert functions.id not in payload["analytics"]["competencies"]
    assert payload["analytics"]["totalDurationMs"] == 60_000
    assert len(payload["sessions"]) == 1
    assert payload["sessions"][0]["competency_identity_id"] == basics.id

    human = await client.post(
        "/api/v1/import-export/export",
        json={"purpose": "human_report", "format": "markdown", "range": "7d"},
        headers={"X-CSRF-Token": csrf},
    )
    assert human.status_code == 200
    assert human.json()["content"].startswith("# Learning-Control-Center export")


async def test_dry_run_does_not_mutate_and_full_restore_preserves_authentication(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, _roadmap = configured_client
    package = await _portable_export(client, csrf)
    identity_count = db.scalar(select(func.count(CompetencyIdentity.id)))
    user_id = db.scalar(select(User.id))
    session_count = db.scalar(select(func.count(AuthSession.id)))
    inspect = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "backup.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )
    assert inspect.status_code == 200, inspect.text
    assert inspect.json()["dryRun"] is True
    assert db.scalar(select(func.count(CompetencyIdentity.id))) == identity_count
    assert db.scalar(select(func.count(ImportRecord.id))) == 0

    no_replace = await client.post(
        "/api/v1/import-export/import/apply",
        json={
            "filename": "backup.json",
            "package": package,
            "confirmation_token": inspect.json()["confirmationToken"],
            "replace_existing": False,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert no_replace.status_code == 409

    inspect_again = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "backup.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )
    applied = await client.post(
        "/api/v1/import-export/import/apply",
        json={
            "filename": "backup.json",
            "package": package,
            "confirmation_token": inspect_again.json()["confirmationToken"],
            "replace_existing": True,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert applied.status_code == 200, applied.text
    assert db.scalar(select(User.id)) == user_id
    assert db.scalar(select(func.count(AuthSession.id))) == session_count
    assert db.scalar(select(func.count(CompetencyIdentity.id))) == identity_count
    assert db.scalar(select(func.count(ImportRecord.id))) == 1


async def test_invalid_portable_package_is_rejected_without_mutation(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, _roadmap = configured_client
    package = await _portable_export(client, csrf)
    corrupt = deepcopy(package)
    corrupt["packageId"] = "corrupt-package"
    del corrupt["payload"]["tables"]["learning_sessions"]
    before = db.scalar(select(func.count(CompetencyIdentity.id)))
    response = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "corrupt.json", "package": corrupt},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422
    assert db.scalar(select(func.count(CompetencyIdentity.id))) == before


async def test_analysis_snapshot_includes_filtered_roadmap_and_resolved_scope(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
) -> None:
    client, csrf, roadmap = configured_client
    current_phase = roadmap["phases"][0]
    selected_track = current_phase["tracks"][0]
    selected_competency = selected_track["competencies"][0]
    response = await client.post(
        "/api/v1/import-export/export",
        json={
            "purpose": "analysis_snapshot",
            "format": "json",
            "range": "custom",
            "start_date": "2026-09-01",
            "end_date": "2026-09-04",
            "current_phase_only": True,
            "track_ids": [selected_track["id"]],
            "competency_identity_ids": [selected_competency["identityId"]],
            "categories": ["roadmap"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    payload = response.json()["content"]["payload"]
    assert set(payload) == {"scope", "roadmap"}
    assert payload["scope"]["resolved_start_date"] == "2026-09-01"
    assert payload["scope"]["resolved_end_date"] == "2026-09-04"
    assert payload["scope"]["timezone"] == "UTC"
    assert payload["scope"]["resolved_categories"] == ["roadmap"]
    assert payload["scope"]["selected_tracks"][0]["stable_key"] == "python"
    assert payload["scope"]["selected_competencies"][0]["stable_key"] == "python.basics"
    exported = payload["roadmap"]
    assert [phase["stableKey"] for phase in exported["phases"]] == ["phase-1"]
    assert [item["stableKey"] for item in exported["phases"][0]["tracks"][0]["competencies"]] == [
        "python.basics"
    ]


async def test_human_report_identifies_resolved_scope(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
) -> None:
    client, csrf, roadmap = configured_client
    track = roadmap["phases"][0]["tracks"][0]
    competency = track["competencies"][0]
    response = await client.post(
        "/api/v1/import-export/export",
        json={
            "purpose": "human_report",
            "format": "markdown",
            "range": "custom",
            "start_date": "2026-09-01",
            "end_date": "2026-09-04",
            "current_phase_only": True,
            "track_ids": [track["id"]],
            "competency_identity_ids": [competency["identityId"]],
            "categories": ["roadmap", "analytics"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    report = response.json()["content"]
    assert "Range preset: custom" in report
    assert "Resolved period: 2026-09-01 to 2026-09-04 (UTC)" in report
    assert "Included categories: roadmap, analytics" in report
    assert "Current phase only: yes" in report
    assert "Tracks: Python (python)" in report
    assert "Competencies: Python basics (python.basics)" in report


async def test_package_specific_payloads_reject_unknown_fields(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
    roadmap_payload: dict[str, object],
) -> None:
    client, csrf, roadmap = configured_client
    identity_id = roadmap["phases"][0]["tracks"][0]["competencies"][0]["identityId"]
    packages = [
        _package(
            "roadmap_update",
            "strict-roadmap",
            {"roadmap": {**roadmap_payload, "version": "2.0.0"}, "unexpected": True},
        ),
        _package(
            "verification_update",
            "strict-verification",
            {"verifications": [], "unexpected": True},
        ),
        _package(
            "state_update",
            "strict-state",
            {
                "states": [
                    {
                        "competency_identity_id": identity_id,
                        "status": "learning",
                        "unexpected": True,
                    }
                ]
            },
        ),
    ]
    for index, package in enumerate(packages):
        response = await client.post(
            "/api/v1/import-export/import/inspect",
            json={"filename": f"strict-{index}.json", "package": package},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 422, response.text

    portable = await _portable_export(client, csrf)
    portable["packageId"] = "strict-portable"
    portable["payload"]["unexpected"] = True
    response = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "strict-portable.json", "package": portable},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PORTABLE_SCHEMA_INVALID"


async def test_roadmap_conflicts_are_rejected_during_non_mutating_inspection(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
    roadmap_payload: dict[str, object],
    db: Session,
) -> None:
    client, csrf, _roadmap = configured_client
    version_count = db.scalar(select(func.count()).select_from(CompetencyIdentity))
    duplicate_version = await client.post(
        "/api/v1/import-export/import/inspect",
        json={
            "filename": "duplicate-version.json",
            "package": _package(
                "roadmap_update", "duplicate-version", {"roadmap": roadmap_payload}
            ),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert duplicate_version.status_code == 409
    assert duplicate_version.json()["error"]["code"] == "ROADMAP_VERSION_DUPLICATE"
    assert db.scalar(select(func.count()).select_from(CompetencyIdentity)) == version_count

    invalid_order = deepcopy(roadmap_payload)
    invalid_order["version"] = "2.0.0"
    invalid_order["phases"][1]["order_index"] = 0
    duplicate_order = await client.post(
        "/api/v1/import-export/import/inspect",
        json={
            "filename": "duplicate-order.json",
            "package": _package("roadmap_replace", "duplicate-order", {"roadmap": invalid_order}),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert duplicate_order.status_code == 422
    assert duplicate_order.json()["error"]["code"] == "ROADMAP_PACKAGE_INVALID"
    assert db.scalar(select(func.count()).select_from(CompetencyIdentity)) == version_count


async def test_roadmap_update_and_replace_are_complete_version_aliases(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
    roadmap_payload: dict[str, object],
) -> None:
    client, csrf, _roadmap = configured_client
    for package_type, version, title in (
        ("roadmap_update", "2.0.0", "Updated foundations"),
        ("roadmap_replace", "3.0.0", "Replacement-labelled foundations"),
    ):
        incoming = deepcopy(roadmap_payload)
        incoming["version"] = version
        incoming["title"] = title
        package = _package(package_type, f"alias-{version}", {"roadmap": incoming})
        preview = await client.post(
            "/api/v1/import-export/import/inspect",
            json={"filename": f"{package_type}.json", "package": package},
            headers={"X-CSRF-Token": csrf},
        )
        assert preview.status_code == 200, preview.text
        applied = await client.post(
            "/api/v1/import-export/import/apply",
            json={
                "filename": f"{package_type}.json",
                "package": package,
                "confirmation_token": preview.json()["confirmationToken"],
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert applied.status_code == 200, applied.text
        current = await client.get("/api/v1/roadmap/current")
        assert current.json()["roadmap"]["activeVersion"]["version"] == version
        assert current.json()["roadmap"]["title"] == title


async def test_dependency_cycles_are_validated_within_each_retained_version(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
    roadmap_payload: dict[str, object],
) -> None:
    client, csrf, _roadmap = configured_client
    incoming = deepcopy(roadmap_payload)
    incoming["version"] = "2.0.0"
    competencies = incoming["phases"][0]["tracks"][0]["competencies"]
    basics = next(item for item in competencies if item["stable_key"] == "python.basics")
    functions = next(item for item in competencies if item["stable_key"] == "python.functions")
    basics["prerequisite_stable_keys"] = ["python.functions"]
    functions["prerequisite_stable_keys"] = []
    package = _package("roadmap_update", "opposite-valid-edges", {"roadmap": incoming})

    preview = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "roadmap-v2.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )
    assert preview.status_code == 200, preview.text
    applied = await client.post(
        "/api/v1/import-export/import/apply",
        json={
            "filename": "roadmap-v2.json",
            "package": package,
            "confirmation_token": preview.json()["confirmationToken"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert applied.status_code == 200, applied.text
    current = await client.get("/api/v1/roadmap/current")
    assert current.json()["roadmap"]["activeVersion"]["version"] == "2.0.0"
