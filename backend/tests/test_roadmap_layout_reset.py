from __future__ import annotations

from copy import deepcopy

import pytest
from app.models import (
    CompetencyDefinition,
    CompetencyIdentity,
    CompetencyState,
    LearningSession,
    VerificationRecord,
)
from app.roadmap_projection.models import LegacyRoadmapActiveState
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _competencies(roadmap: dict[str, object]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for phase in roadmap["phases"]:
        for track in phase["tracks"]:
            for competency in track["competencies"]:
                result[competency["stableKey"]] = competency
    return result


async def _put_position(
    client: AsyncClient, csrf: str, roadmap: dict[str, object], stable_key: str, x: int, y: int
) -> None:
    definition_id = _competencies(roadmap)[stable_key]["definitionId"]
    response = await client.put(
        f"/api/v1/roadmap/competencies/{definition_id}/position",
        json={"x": x, "y": y},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text


def _stored_positions_by_key(
    db: Session, roadmap_version_id: str
) -> dict[str, tuple[int | None, int | None]]:
    rows = db.execute(
        select(
            CompetencyIdentity.stable_key,
            CompetencyDefinition.position_x,
            CompetencyDefinition.position_y,
        )
        .join(
            CompetencyDefinition,
            CompetencyDefinition.competency_identity_id == CompetencyIdentity.id,
        )
        .where(CompetencyDefinition.roadmap_version_id == roadmap_version_id)
    ).all()
    return {key: (x, y) for key, x, y in rows}


def _expected_all_cleared(roadmap: dict[str, object]) -> dict[str, tuple[None, None]]:
    return {key: (None, None) for key in _competencies(roadmap)}


async def test_reset_clears_positions_for_the_active_version_only(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, roadmap = configured_client
    await _put_position(client, csrf, roadmap, "python.basics", 168, 76)
    await _put_position(client, csrf, roadmap, "python.functions", 300, 202)

    response = await client.post("/api/v1/roadmap/layout/reset", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    assert response.json() == {"cleared": 2}

    assert _stored_positions_by_key(db, roadmap["activeVersion"]["id"]) == _expected_all_cleared(
        roadmap
    )


async def test_reset_never_touches_learning_state(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, roadmap = configured_client
    roadmap_id = roadmap["id"]
    basics = _competencies(roadmap)["python.basics"]

    status = await client.put(
        f"/api/v1/roadmap/competencies/{basics['identityId']}/status",
        json={"status": "learning", "reason": "Started learning"},
        headers={"X-CSRF-Token": csrf},
    )
    assert status.status_code == 200
    verification = await client.post(
        "/api/v1/verification",
        json={
            "competency_identity_id": basics["identityId"],
            "verification_source": "self",
            "method": "Reviewed exercises",
            "result": "passed",
            "evidence_summary": "Sample evidence",
            "evidence": [],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert verification.status_code == 201
    session = await client.post(
        "/api/v1/sessions/timed",
        json={
            "competency_identity_id": basics["identityId"],
            "activity_type": "practice",
            "assistance_mode": "none",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert session.status_code == 201
    session_id = session.json()["id"]

    phase_two = next(phase for phase in roadmap["phases"] if phase["stableKey"] == "phase-2")
    switched = await client.put(
        f"/api/v1/roadmap/current-phase/{phase_two['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert switched.status_code == 200

    await _put_position(client, csrf, roadmap, "python.basics", 200, 150)

    before_sessions = db.scalar(select(func.count(LearningSession.id)))
    before_verifications = db.scalar(select(func.count(VerificationRecord.id)))
    before_status = db.scalar(
        select(CompetencyState.current_status).where(
            CompetencyState.competency_identity_id == basics["identityId"]
        )
    )
    active_state = db.get(LegacyRoadmapActiveState, roadmap_id)
    assert active_state is not None
    before_current_phase = active_state.current_phase_id
    assert before_status == "verified"
    assert before_current_phase == phase_two["id"]

    response = await client.post("/api/v1/roadmap/layout/reset", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert response.json() == {"cleared": 1}

    assert db.scalar(select(func.count(LearningSession.id))) == before_sessions
    stored_session = db.get(LearningSession, session_id)
    assert stored_session is not None
    assert stored_session.timed_state == "running"
    assert db.scalar(select(func.count(VerificationRecord.id))) == before_verifications
    assert (
        db.scalar(
            select(CompetencyState.current_status).where(
                CompetencyState.competency_identity_id == basics["identityId"]
            )
        )
        == before_status
    )
    active_state = db.get(LegacyRoadmapActiveState, roadmap_id)
    assert active_state is not None and active_state.current_phase_id == before_current_phase
    assert _stored_positions_by_key(db, roadmap["activeVersion"]["id"]) == _expected_all_cleared(
        roadmap
    )


async def test_reset_keeps_inactive_roadmap_versions_untouched(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
    roadmap_payload: dict[str, object],
    db: Session,
) -> None:
    client, csrf, roadmap = configured_client
    version_one_id = roadmap["activeVersion"]["id"]
    await _put_position(client, csrf, roadmap, "python.basics", 168, 76)

    version_two_payload = deepcopy(roadmap_payload)
    version_two_payload["version"] = "2.0.0"
    bumped = await client.post(
        "/api/v1/roadmap", json=version_two_payload, headers={"X-CSRF-Token": csrf}
    )
    assert bumped.status_code == 201
    version_two_roadmap = bumped.json()
    version_two_id = version_two_roadmap["activeVersion"]["id"]
    assert version_two_id != version_one_id
    await _put_position(client, csrf, version_two_roadmap, "python.basics", 320, 240)

    response = await client.post("/api/v1/roadmap/layout/reset", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert response.json() == {"cleared": 1}

    version_one_positions = _stored_positions_by_key(db, version_one_id)
    assert version_one_positions["python.basics"] == (168, 76)
    assert all(
        position == (None, None)
        for key, position in version_one_positions.items()
        if key != "python.basics"
    )
    assert _stored_positions_by_key(db, version_two_id) == _expected_all_cleared(
        version_two_roadmap
    )


async def test_reset_requires_an_authenticated_session(client: AsyncClient) -> None:
    response = await client.post("/api/v1/roadmap/layout/reset")
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("headers",),
    [
        ({},),
        ({"X-CSRF-Token": "forged-token"},),
    ],
)
async def test_reset_requires_a_valid_csrf_token(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
    headers: dict[str, str],
) -> None:
    client, _csrf, _roadmap = configured_client
    response = await client.post("/api/v1/roadmap/layout/reset", headers=headers)
    assert response.status_code == 403


async def test_position_put_requires_an_authenticated_session(client: AsyncClient) -> None:
    response = await client.put(
        "/api/v1/roadmap/competencies/does-not-exist/position",
        json={"x": 1, "y": 2},
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("headers",),
    [
        ({},),
        ({"X-CSRF-Token": "forged-token"},),
    ],
)
async def test_position_put_requires_a_valid_csrf_token(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
    headers: dict[str, str],
) -> None:
    client, _csrf, roadmap = configured_client
    definition_id = _competencies(roadmap)["python.basics"]["definitionId"]
    response = await client.put(
        f"/api/v1/roadmap/competencies/{definition_id}/position",
        json={"x": 1, "y": 2},
        headers=headers,
    )
    assert response.status_code == 403


async def test_reset_commit_is_all_or_nothing(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, csrf, roadmap = configured_client
    version_id = roadmap["activeVersion"]["id"]
    await _put_position(client, csrf, roadmap, "python.basics", 168, 76)
    await _put_position(client, csrf, roadmap, "python.functions", 200, 150)

    original_commit = db.commit

    def failing_commit() -> None:
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(db, "commit", failing_commit)
    with pytest.raises(RuntimeError):
        await client.post("/api/v1/roadmap/layout/reset", headers={"X-CSRF-Token": csrf})
    monkeypatch.setattr(db, "commit", original_commit)
    db.rollback()

    positions = _stored_positions_by_key(db, version_id)
    assert positions["python.basics"] == (168, 76)
    assert positions["python.functions"] == (200, 150)
