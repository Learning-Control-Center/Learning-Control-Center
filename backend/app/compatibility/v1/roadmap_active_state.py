from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.determinism import content_hash
from app.errors import AppError
from app.models import Roadmap
from app.roadmap_projection.models import LegacyRoadmapActiveState
from app.time_utils import utc_now_ms


def legacy_pointer_hash(roadmap: Roadmap) -> str:
    return content_hash(
        {
            "roadmapId": roadmap.id,
            "activeVersionId": roadmap.active_version_id,
            "currentPhaseId": roadmap.current_phase_id,
            "isCurrent": roadmap.is_current,
        }
    )


def synchronize_legacy_roadmap_state(db: Session, roadmap: Roadmap) -> LegacyRoadmapActiveState:
    state = db.get(LegacyRoadmapActiveState, roadmap.id)
    if state is None:
        state = LegacyRoadmapActiveState(
            roadmap_id=roadmap.id,
            active_version_id=roadmap.active_version_id,
            current_phase_id=roadmap.current_phase_id,
            is_current=roadmap.is_current,
            state_hash=legacy_pointer_hash(roadmap),
            updated_at=utc_now_ms(),
        )
        db.add(state)
    else:
        state.active_version_id = roadmap.active_version_id
        state.current_phase_id = roadmap.current_phase_id
        state.is_current = roadmap.is_current
        state.state_hash = legacy_pointer_hash(roadmap)
        state.updated_at = utc_now_ms()
    db.flush()
    return state


def assert_legacy_roadmap_state_parity(db: Session, roadmap: Roadmap) -> LegacyRoadmapActiveState:
    state = db.get(LegacyRoadmapActiveState, roadmap.id)
    if state is None or (
        state.active_version_id,
        state.current_phase_id,
        state.is_current,
        state.state_hash,
    ) != (
        roadmap.active_version_id,
        roadmap.current_phase_id,
        roadmap.is_current,
        legacy_pointer_hash(roadmap),
    ):
        raise AppError(
            409,
            "LEGACY_ROADMAP_ACTIVE_STATE_MISMATCH",
            "Legacy Roadmap active-state parity is not satisfied.",
        )
    return state


def current_legacy_roadmap(db: Session) -> Roadmap | None:
    state = db.scalar(
        select(LegacyRoadmapActiveState).where(LegacyRoadmapActiveState.is_current.is_(True))
    )
    if state is None:
        # Empty/pre-expand databases may legitimately have no roadmap.
        roadmap = db.scalar(select(Roadmap).where(Roadmap.is_current.is_(True)))
        if roadmap is not None:
            raise AppError(
                409,
                "LEGACY_ROADMAP_ACTIVE_STATE_MISSING",
                "The legacy Roadmap active-state adapter is missing.",
            )
        return None
    roadmap = db.get(Roadmap, state.roadmap_id)
    if roadmap is None:
        raise AppError(409, "ROADMAP_STATE_INVALID", "The active Roadmap is missing.")
    assert_legacy_roadmap_state_parity(db, roadmap)
    return roadmap
