from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

from app.models import AuthSession, CompetencyIdentity, LearningSession
from app.reports import backfill_reports
from app.time_utils import datetime_to_epoch_ms
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _competencies(roadmap: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        competency["stableKey"]: competency
        for phase in roadmap["phases"]
        for track in phase["tracks"]
        for competency in track["competencies"]
    }


async def test_api_instants_filters_edit_and_confirmed_delete(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, roadmap = configured_client
    identity_id = _competencies(roadmap)["python.basics"]["identityId"]
    started = "2026-09-01T17:41:12.481Z"
    created = await client.post(
        "/api/v1/sessions/manual",
        json={
            "competency_identity_id": identity_id,
            "activity_type": "reading",
            "assistance_mode": "docs_only",
            "started_at": started,
            "duration_ms": 123_456,
            "outcome": "completed",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    assert created.json()["startedAt"] == started
    item = db.get(LearningSession, created.json()["id"])
    assert item and item.started_at == 1_788_284_472_481
    filtered = await client.get(
        "/api/v1/sessions",
        params={
            "start_at": "2026-09-01T17:41:12.000Z",
            "end_at": "2026-09-01T17:41:13.000Z",
        },
    )
    assert [entry["id"] for entry in filtered.json()["items"]] == [item.id]
    edited = await client.patch(
        f"/api/v1/sessions/{item.id}",
        json={"duration_ms": 654_321, "notes": "Corrected exact duration"},
        headers={"X-CSRF-Token": csrf},
    )
    assert edited.status_code == 200
    assert edited.json()["durationMs"] == 654_321
    assert (
        await client.delete(f"/api/v1/sessions/{item.id}", headers={"X-CSRF-Token": csrf})
    ).status_code == 422
    deleted = await client.delete(
        f"/api/v1/sessions/{item.id}?confirm=true", headers={"X-CSRF-Token": csrf}
    )
    assert deleted.json() == {"deleted": True}


async def test_expired_session_is_revoked(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, _csrf = authenticated_client
    session = db.scalar(select(AuthSession))
    assert session is not None
    session.absolute_expires_at = 0
    db.commit()
    response = await client.get("/api/v1/auth/session")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "SESSION_EXPIRED"
    assert session.revoked_at is not None


async def test_unconfigured_guidance_matches_the_import_only_ui(
    authenticated_client: tuple[AsyncClient, str],
) -> None:
    client, _csrf = authenticated_client
    roadmap = await client.get("/api/v1/roadmap/current")
    recommendation = await client.get("/api/v1/recommendations/today")

    assert roadmap.json()["guidance"] == "Import a roadmap package to begin."
    assert recommendation.json()["guidance"] == (
        "Import a roadmap package to receive recommendations."
    )
    assert "create" not in roadmap.json()["guidance"].lower()
    assert "create" not in recommendation.json()["guidance"].lower()


async def test_hierarchy_cycle_and_duplicate_track_keys_are_rejected(
    authenticated_client: tuple[AsyncClient, str], roadmap_payload: dict[str, object]
) -> None:
    client, csrf = authenticated_client
    cyclic = deepcopy(roadmap_payload)
    competencies = cyclic["phases"][0]["tracks"][0]["competencies"]
    competencies[0]["parent_stable_key"] = "python.functions"
    competencies[1]["parent_stable_key"] = "python.basics"
    response = await client.post("/api/v1/roadmap", json=cyclic, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "COMPETENCY_HIERARCHY_CYCLE"

    duplicate_track = deepcopy(roadmap_payload)
    duplicate_track["phases"][1]["tracks"][0]["stable_key"] = "python"
    response = await client.post(
        "/api/v1/roadmap", json=duplicate_track, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "ROADMAP_TRACK_INVALID"


async def test_verification_history_exposes_evidence_and_rfc3339_time(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, roadmap = configured_client
    identity_id = _competencies(roadmap)["python.basics"]["identityId"]
    created = await client.post(
        "/api/v1/verification",
        json={
            "competency_identity_id": identity_id,
            "verification_source": "self",
            "method": "Independent reconstruction",
            "result": "passed",
            "evidence": [
                {
                    "kind": "note",
                    "reference": "journal:2026-09-01",
                    "description": "Rebuilt without prompts",
                }
            ],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    history = await client.get(
        "/api/v1/verification",
        params={"competency_identity_id": identity_id, "source": "self", "result": "passed"},
    )
    record = history.json()["items"][0]
    assert record["createdAt"].endswith("Z")
    assert record["evidence"] == [
        {
            "kind": "note",
            "reference": "journal:2026-09-01",
            "description": "Rebuilt without prompts",
        }
    ]


def _roadmap_package(roadmap_payload: dict[str, object], package_id: str) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "packageType": "roadmap_update",
        "packageId": package_id,
        "appVersion": "1.0.0",
        "createdAt": "2026-09-01T17:41:12.481Z",
        "payload": {"roadmap": roadmap_payload},
    }


async def test_roadmap_import_dry_run_has_meaningful_diff_and_rejects_cycles(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
    roadmap_payload: dict[str, object],
) -> None:
    client, csrf, _roadmap = configured_client
    updated = deepcopy(roadmap_payload)
    updated["version"] = "2.0.0"
    updated["phases"][0]["tracks"][0]["competencies"][0]["weight"] = 4
    updated["phases"][1]["tracks"][0]["competencies"] = []
    preview = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "roadmap.json", "package": _roadmap_package(updated, "roadmap-v2")},
        headers={"X-CSRF-Token": csrf},
    )
    assert preview.status_code == 200, preview.text
    diff = preview.json()["diff"]["roadmap"]
    assert diff["modified"][0]["changes"]["weight"] == {"from": 5, "to": 4}
    assert diff["archivedFromActiveVersion"] == ["project.delivery"]
    assert diff["learningLogsTouched"] is False

    cyclic = deepcopy(updated)
    cyclic["version"] = "3.0.0"
    cyclic["phases"][0]["tracks"][0]["competencies"][0]["prerequisite_stable_keys"] = [
        "python.functions"
    ]
    rejected = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "bad.json", "package": _roadmap_package(cyclic, "roadmap-cycle")},
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "REQUIRED_DEPENDENCY_CYCLE"


async def test_roadmap_import_preview_exposes_all_material_definition_changes(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
    roadmap_payload: dict[str, object],
) -> None:
    client, csrf, _roadmap = configured_client
    updated = deepcopy(roadmap_payload)
    updated["version"] = "2.0.0"
    updated["current_phase_stable_key"] = "phase-2"
    foundation_competencies = updated["phases"][0]["tracks"][0]["competencies"]
    functions = next(
        item for item in foundation_competencies if item["stable_key"] == "python.functions"
    )
    basics = next(item for item in foundation_competencies if item["stable_key"] == "python.basics")
    basics["exit_criteria"] = []
    foundation_competencies.remove(functions)
    updated["phases"][1]["tracks"][0]["competencies"].append(functions)
    functions["parent_stable_key"] = "python.basics"
    functions["prerequisite_stable_keys"] = ["project.delivery"]
    functions["recommended_prerequisite_stable_keys"] = ["python.basics"]
    functions["exit_criteria"][0].update(
        {"text": "Compose and test functions", "required": False, "weight": 2}
    )
    functions["exit_criteria"].append(
        {
            "stable_key": "python.functions.explain",
            "text": "Explain function composition",
            "required": True,
        }
    )

    preview = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "roadmap.json", "package": _roadmap_package(updated, "full-diff")},
        headers={"X-CSRF-Token": csrf},
    )
    assert preview.status_code == 200, preview.text
    diff = preview.json()["diff"]["roadmap"]
    assert diff["currentPhase"] == {"from": "phase-1", "to": "phase-2"}
    modified = next(item for item in diff["modified"] if item["stableKey"] == "python.functions")
    changes = modified["changes"]
    assert changes["phaseStableKey"] == {"from": "phase-1", "to": "phase-2"}
    assert changes["trackStableKey"] == {"from": "python", "to": "projects"}
    assert changes["parentStableKey"] == {"from": None, "to": "python.basics"}
    assert changes["requiredPrerequisites"] == {
        "added": ["project.delivery"],
        "removed": ["python.basics"],
    }
    assert changes["recommendedPrerequisites"] == {
        "added": ["python.basics"],
        "removed": [],
    }
    assert changes["exitCriteria"]["added"] == ["python.functions.explain"]
    assert changes["exitCriteria"]["modified"][0]["changes"] == {
        "text": {"from": "Compose functions", "to": "Compose and test functions"},
        "required": {"from": True, "to": False},
        "weight": {"from": None, "to": 2},
    }
    basics_modified = next(
        item for item in diff["modified"] if item["stableKey"] == "python.basics"
    )
    assert basics_modified["changes"]["exitCriteria"]["removed"] == ["python.basics.program"]


async def test_operation_history_and_backup_are_separate_from_portable_export(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, _roadmap = configured_client
    exported = await client.post(
        "/api/v1/import-export/export",
        json={"purpose": "portable_logical_backup", "format": "json"},
        headers={"X-CSRF-Token": csrf},
    )
    assert exported.status_code == 200
    backup = await client.post(
        "/api/v1/import-export/backups/operational", headers={"X-CSRF-Token": csrf}
    )
    assert backup.status_code == 201
    assert backup.json()["createdAt"].endswith("Z")
    history = await client.get("/api/v1/import-export/history")
    operations = {item["operation"] for item in history.json()["items"]}
    assert {"export", "backup"} <= operations


def test_startup_backfill_uses_completed_local_periods(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    identity = db.scalar(
        select(CompetencyIdentity).where(CompetencyIdentity.stable_key == "python.basics")
    )
    assert identity is not None
    start = datetime_to_epoch_ms(datetime(2026, 8, 20, 12, tzinfo=UTC))
    db.add(
        LearningSession(
            competency_identity_id=identity.id,
            session_mode="manual",
            activity_type="practice",
            assistance_mode="none",
            started_at=start,
            ended_at=start + 600_000,
            accumulated_duration_ms=600_000,
            duration_ms=600_000,
            outcome="completed",
        )
    )
    db.commit()
    now = datetime_to_epoch_ms(datetime(2026, 9, 2, 12, tzinfo=UTC))
    generated = backfill_reports(db, now_ms=now)
    assert generated > 0
    assert backfill_reports(db, now_ms=now) == 0
    daily_count = db.scalar(
        select(func.count()).select_from(LearningSession).where(LearningSession.started_at >= start)
    )
    assert daily_count == 1
