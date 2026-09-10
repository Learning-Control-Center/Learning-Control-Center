from __future__ import annotations

import json
from datetime import UTC, datetime

from app.analytics import session_facts
from app.models import (
    Activity,
    ContributionRetraction,
    LearningSession,
    SessionContribution,
    SessionCorrection,
)
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _competencies(roadmap: dict[str, object]) -> list[dict[str, object]]:
    return roadmap["phases"][0]["tracks"][0]["competencies"]


async def test_v1_session_dual_write_and_multiple_contributions_count_time_once(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, roadmap = configured_client
    competencies = _competencies(roadmap)
    primary_id = str(competencies[0]["identityId"])
    secondary_id = str(competencies[1]["identityId"])
    response = await client.post(
        "/api/v1/sessions/manual",
        json={
            "competency_identity_id": primary_id,
            "activity_type": "coding",
            "assistance_mode": "none",
            "started_at": "2026-09-10T10:00:00Z",
            "duration_ms": 7_200_000,
            "outcome": "completed",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    session_id = response.json()["id"]
    item = db.get(LearningSession, session_id)
    assert item is not None and db.get(Activity, item.activity_id) is not None
    assert db.scalar(select(func.count()).select_from(SessionContribution)) == 1
    secondary = await client.post(
        f"/api/v2/sessions/{session_id}/contributions",
        json={
            "competency_identity_id": secondary_id,
            "relevance": "secondary",
            "provenance": "user_selected",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert secondary.status_code == 201, secondary.text
    assert db.scalar(select(func.count()).select_from(SessionContribution)) == 2
    facts = session_facts(db, "UTC")
    assert sum(fact.duration_ms for fact in facts if fact.id == session_id) == 7_200_000


async def test_session_edit_and_delete_append_history(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, roadmap = configured_client
    competencies = _competencies(roadmap)
    created = await client.post(
        "/api/v1/sessions/manual",
        json={
            "competency_identity_id": competencies[0]["identityId"],
            "activity_type": "practice",
            "assistance_mode": "none",
            "started_at": "2026-09-10T10:00:00Z",
            "duration_ms": 600_000,
            "outcome": "completed",
        },
        headers={"X-CSRF-Token": csrf},
    )
    session_id = created.json()["id"]
    old_activity_id = db.get(LearningSession, session_id).activity_id
    updated = await client.patch(
        f"/api/v1/sessions/{session_id}",
        json={
            "competency_identity_id": competencies[1]["identityId"],
            "activity_type": "debugging",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200, updated.text
    item = db.get(LearningSession, session_id)
    assert item.activity_id != old_activity_id
    assert db.get(Activity, item.activity_id).supersedes_activity_id == old_activity_id
    assert db.scalar(select(func.count()).select_from(SessionCorrection)) == 1
    assert db.scalar(select(func.count()).select_from(ContributionRetraction)) == 1
    duration_update = await client.patch(
        f"/api/v1/sessions/{session_id}",
        json={"duration_ms": 900_000},
        headers={"X-CSRF-Token": csrf},
    )
    assert duration_update.status_code == 200, duration_update.text
    duration_correction = db.scalars(
        select(SessionCorrection).order_by(SessionCorrection.corrected_at.desc())
    ).first()
    assert set(json.loads(duration_correction.changed_fields_json)) == {
        "duration_ms",
        "accumulated_duration_ms",
        "ended_at",
        "activity_id",
    }
    correction_count = db.scalar(select(func.count()).select_from(SessionCorrection))
    unchanged = await client.patch(
        f"/api/v1/sessions/{session_id}",
        json={"duration_ms": 900_000},
        headers={"X-CSRF-Token": csrf},
    )
    assert unchanged.status_code == 200
    assert db.scalar(select(func.count()).select_from(SessionCorrection)) == correction_count
    deleted = await client.delete(
        f"/api/v1/sessions/{session_id}?confirm=true", headers={"X-CSRF-Token": csrf}
    )
    assert deleted.status_code == 200 and deleted.json() == {"deleted": True}
    assert db.get(LearningSession, session_id).tombstoned_at is not None
    tombstoned_at = db.get(LearningSession, session_id).tombstoned_at
    repeated = await client.delete(
        f"/api/v1/sessions/{session_id}?confirm=true", headers={"X-CSRF-Token": csrf}
    )
    assert repeated.status_code == 404
    assert db.get(LearningSession, session_id).tombstoned_at == tombstoned_at
    listing = await client.get("/api/v1/sessions")
    assert all(row["id"] != session_id for row in listing.json()["items"])
    assert all(fact.id != session_id for fact in session_facts(db, "UTC"))


async def test_non_time_activity_is_valid_reality(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    response = await client.post(
        "/api/v2/activities",
        json={
            "title": "Reviewed architecture notes",
            "category_stable_key": "review",
            "occurred_at": datetime(2026, 9, 10, 12, tzinfo=UTC).isoformat(),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    assert db.scalar(select(func.count()).select_from(Activity)) == 1
    assert db.scalar(select(func.count()).select_from(LearningSession)) == 0


async def test_project_contribution_is_explicitly_deferred_and_primary_mirrors_legacy_field(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, roadmap = configured_client
    competencies = _competencies(roadmap)
    created = await client.post(
        "/api/v1/sessions/manual",
        json={
            "activity_type": "practice",
            "assistance_mode": "none",
            "started_at": "2026-09-10T10:00:00Z",
            "duration_ms": 600_000,
            "outcome": "completed",
        },
        headers={"X-CSRF-Token": csrf},
    )
    session_id = created.json()["id"]
    project = await client.post(
        f"/api/v2/sessions/{session_id}/contributions",
        json={"target_type": "project", "project_id": "future-project", "relevance": "primary"},
        headers={"X-CSRF-Token": csrf},
    )
    assert project.status_code == 422
    assert project.json()["error"]["code"] == "FEATURE_NOT_AVAILABLE"

    added = await client.post(
        f"/api/v2/sessions/{session_id}/contributions",
        json={"competency_identity_id": competencies[0]["identityId"], "relevance": "primary"},
        headers={"X-CSRF-Token": csrf},
    )
    assert added.status_code == 201, added.text
    assert (
        db.get(LearningSession, session_id).competency_identity_id == competencies[0]["identityId"]
    )
    retracted = await client.delete(
        f"/api/v2/sessions/{session_id}/contributions/{added.json()['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert retracted.status_code == 200, retracted.text
    assert db.get(LearningSession, session_id).competency_identity_id is None


async def test_native_sessions_share_activity_without_multiplying_time(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, roadmap = configured_client
    competency_id = str(_competencies(roadmap)[0]["identityId"])
    activity = await client.post(
        "/api/v2/activities",
        json={"title": "Practice set", "category_stable_key": "practice"},
        headers={"X-CSRF-Token": csrf},
    )
    activity_id = activity.json()["id"]
    for started_at in ("2026-09-10T10:00:00Z", "2026-09-10T11:00:00Z"):
        created = await client.post(
            "/api/v2/sessions/manual",
            json={
                "activity_id": activity_id,
                "assistance_mode": "none",
                "started_at": started_at,
                "duration_ms": 600_000,
                "outcome": "completed",
                "contributions": [
                    {"competency_identity_id": competency_id, "relevance": "primary"}
                ],
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert created.status_code == 201, created.text
        assert created.json()["activityId"] == activity_id
        assert created.json()["contributions"][0]["competencyIdentityId"] == competency_id
    sessions = db.scalars(select(LearningSession)).all()
    assert len(sessions) == 2
    assert {item.activity_id for item in sessions} == {activity_id}
    assert sum(fact.duration_ms for fact in session_facts(db, "UTC")) == 1_200_000


async def test_v1_timer_completion_supersedes_implicit_activity(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    started = await client.post(
        "/api/v1/sessions/timed",
        json={"activity_type": "coding", "assistance_mode": "none"},
        headers={"X-CSRF-Token": csrf},
    )
    assert started.status_code == 201, started.text
    session_id = started.json()["id"]
    original_activity_id = db.get(LearningSession, session_id).activity_id
    completed = await client.post(
        f"/api/v1/sessions/{session_id}/complete",
        json={"outcome": "completed", "notes": "Finished"},
        headers={"X-CSRF-Token": csrf},
    )
    assert completed.status_code == 200, completed.text
    session = db.get(LearningSession, session_id)
    replacement = db.get(Activity, session.activity_id)
    assert replacement.supersedes_activity_id == original_activity_id
    assert replacement.context_ended_at == session.ended_at
    assert replacement.outcome_classification == "completed"
