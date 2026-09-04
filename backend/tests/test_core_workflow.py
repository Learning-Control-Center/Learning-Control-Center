from __future__ import annotations

from copy import deepcopy

from app.models import CompetencyIdentity, LearningSession, RoadmapVersion
from app.time_utils import epoch_ms_to_rfc3339, utc_now_ms
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session


def _competencies(roadmap: dict[str, object]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for phase in roadmap["phases"]:
        for track in phase["tracks"]:
            for competency in track["competencies"]:
                result[competency["stableKey"]] = competency
    return result


async def test_status_sessions_and_verification_follow_canonical_transitions(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, roadmap = configured_client
    basics = _competencies(roadmap)["python.basics"]
    identity_id = basics["identityId"]

    forbidden = await client.put(
        f"/api/v1/roadmap/competencies/{identity_id}/status",
        json={"status": "verified", "reason": "Direct flag"},
        headers={"X-CSRF-Token": csrf},
    )
    assert forbidden.status_code == 422

    conceptual = await client.post(
        "/api/v1/sessions/manual",
        json={
            "competency_identity_id": identity_id,
            "track_id": roadmap["phases"][0]["tracks"][0]["id"],
            "activity_type": "learning",
            "assistance_mode": "docs_only",
            "started_at": epoch_ms_to_rfc3339(utc_now_ms() - 3_600_000),
            "duration_ms": 1_800_000,
            "outcome": "completed",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert conceptual.status_code == 201
    current = (await client.get("/api/v1/roadmap/current")).json()["roadmap"]
    assert _competencies(current)["python.basics"]["status"] == "learning"

    practical = await client.post(
        "/api/v1/sessions/manual",
        json={
            "competency_identity_id": identity_id,
            "activity_type": "coding",
            "assistance_mode": "none",
            "started_at": epoch_ms_to_rfc3339(utc_now_ms() - 1_700_000),
            "duration_ms": 1_800_000,
            "outcome": "partial",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert practical.status_code == 201
    current = (await client.get("/api/v1/roadmap/current")).json()["roadmap"]
    assert _competencies(current)["python.basics"]["status"] == "practicing"

    verification = await client.post(
        "/api/v1/verification",
        json={
            "competency_identity_id": identity_id,
            "verification_source": "self",
            "method": "Independent exercise",
            "result": "passed",
            "evidence": [
                {
                    "kind": "repository",
                    "reference": "local://exercise",
                    "description": "Completed work",
                }
            ],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert verification.status_code == 201
    assert verification.json()["status"] == "verified"

    failed = await client.post(
        "/api/v1/verification",
        json={
            "competency_identity_id": identity_id,
            "verification_source": "self",
            "method": "Retention check",
            "result": "failed",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert failed.status_code == 201
    assert failed.json()["status"] == "needs_review"

    reviewed = await client.post(
        "/api/v1/sessions/manual",
        json={
            "competency_identity_id": identity_id,
            "activity_type": "review",
            "assistance_mode": "none",
            "started_at": epoch_ms_to_rfc3339(utc_now_ms()),
            "duration_ms": 600_000,
            "outcome": "completed",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert reviewed.status_code == 201
    history = (await client.get(f"/api/v1/roadmap/competencies/{identity_id}/history")).json()
    assert [item["result"] for item in history["verificationRecords"]] == ["failed", "passed"]
    assert history["statusEvents"][0]["toStatus"] == "practicing"


async def test_timed_session_is_server_authoritative_and_cancelled_work_is_excluded(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, roadmap = configured_client
    identity_id = _competencies(roadmap)["python.basics"]["identityId"]
    start = await client.post(
        "/api/v1/sessions/timed",
        json={
            "competency_identity_id": identity_id,
            "activity_type": "practice",
            "assistance_mode": "none",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert start.status_code == 201
    session_id = start.json()["id"]
    conflict = await client.post(
        "/api/v1/sessions/timed",
        json={"activity_type": "reading", "assistance_mode": "docs_only"},
        headers={"X-CSRF-Token": csrf},
    )
    assert conflict.status_code == 409
    item = db.get(LearningSession, session_id)
    item.active_since -= 2_000
    db.commit()
    assert (
        await client.post(f"/api/v1/sessions/{session_id}/pause", headers={"X-CSRF-Token": csrf})
    ).status_code == 200
    assert (
        await client.post(f"/api/v1/sessions/{session_id}/resume", headers={"X-CSRF-Token": csrf})
    ).status_code == 200
    item = db.get(LearningSession, session_id)
    item.active_since -= 2_000
    db.commit()
    completed = await client.post(
        f"/api/v1/sessions/{session_id}/complete",
        json={"outcome": "completed"},
        headers={"X-CSRF-Token": csrf},
    )
    assert completed.status_code == 200
    assert completed.json()["durationMs"] >= 4_000

    cancelled_start = await client.post(
        "/api/v1/sessions/timed",
        json={"activity_type": "reading", "assistance_mode": "docs_only"},
        headers={"X-CSRF-Token": csrf},
    )
    cancelled_id = cancelled_start.json()["id"]
    item = db.get(LearningSession, cancelled_id)
    item.active_since -= 60_000
    db.commit()
    cancelled = await client.post(
        f"/api/v1/sessions/{cancelled_id}/cancel", headers={"X-CSRF-Token": csrf}
    )
    assert cancelled.json()["durationMs"] >= 60_000
    analytics = (await client.get("/api/v1/analytics?range=all")).json()
    assert analytics["totalDurationMs"] == completed.json()["durationMs"]


async def test_roadmap_update_preserves_stable_identity_and_criterion_state(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
    roadmap_payload: dict[str, object],
    db: Session,
) -> None:
    client, csrf, roadmap = configured_client
    original = _competencies(roadmap)["python.basics"]
    criterion = original["exitCriteria"][0]
    updated_state = await client.put(
        f"/api/v1/roadmap/exit-criteria/{criterion['id']}",
        json={"state": "met"},
        headers={"X-CSRF-Token": csrf},
    )
    assert updated_state.status_code == 200

    version_two = deepcopy(roadmap_payload)
    version_two["version"] = "2.0.0"
    version_two["phases"][0]["tracks"][0]["competencies"][0]["title"] = "Python essentials"
    version_two["phases"][0]["tracks"][0]["competencies"][0]["exit_criteria"].append(
        {
            "stable_key": "python.basics.new-criterion",
            "text": "Complete the new criterion",
            "required": True,
        }
    )
    response = await client.post(
        "/api/v1/roadmap", json=version_two, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 201, response.text
    updated = _competencies(response.json())["python.basics"]
    assert updated["identityId"] == original["identityId"]
    criterion_states = {item["stableKey"]: item["state"] for item in updated["exitCriteria"]}
    assert criterion_states["python.basics.program"] == "met"
    assert criterion_states["python.basics.new-criterion"] == "not_met"
    assert db.scalars(select(RoadmapVersion)).all().__len__() == 2
    assert db.scalars(select(CompetencyIdentity)).all().__len__() == 3


async def test_required_dependency_cycle_is_rejected(
    authenticated_client: tuple[AsyncClient, str], roadmap_payload: dict[str, object]
) -> None:
    client, csrf = authenticated_client
    invalid = deepcopy(roadmap_payload)
    invalid["phases"][0]["tracks"][0]["competencies"][0]["prerequisite_stable_keys"] = [
        "python.functions"
    ]
    response = await client.post("/api/v1/roadmap", json=invalid, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUIRED_DEPENDENCY_CYCLE"
