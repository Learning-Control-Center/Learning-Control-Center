from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import AuthContext, get_auth_context, require_csrf
from app.database import get_db
from app.domain import active_timed_session, apply_session_promotion
from app.errors import AppError
from app.models import LearningSession
from app.schemas import ManualSessionCreate, SessionUpdate, TimedSessionComplete, TimedSessionStart
from app.time_utils import datetime_to_epoch_ms, epoch_ms_to_rfc3339, utc_now_ms

router = APIRouter(prefix="/sessions", tags=["sessions"])


def serialize_session(item: LearningSession, now_ms: int | None = None) -> dict[str, Any]:
    current_duration = item.duration_ms
    if item.timed_state == "running" and item.active_since is not None:
        current_duration = item.accumulated_duration_ms + (
            (now_ms or utc_now_ms()) - item.active_since
        )
    elif item.timed_state == "paused":
        current_duration = item.accumulated_duration_ms
    return {
        "id": item.id,
        "competencyIdentityId": item.competency_identity_id,
        "trackId": item.track_id,
        "sessionMode": item.session_mode,
        "timedState": item.timed_state,
        "activityType": item.activity_type,
        "assistanceMode": item.assistance_mode,
        "startedAt": epoch_ms_to_rfc3339(item.started_at),
        "endedAt": epoch_ms_to_rfc3339(item.ended_at) if item.ended_at is not None else None,
        "accumulatedDurationMs": item.accumulated_duration_ms,
        "activeSince": epoch_ms_to_rfc3339(item.active_since)
        if item.active_since is not None
        else None,
        "durationMs": current_duration,
        "difficulty": item.difficulty,
        "outcome": item.outcome,
        "notes": item.notes,
        "createdAt": epoch_ms_to_rfc3339(item.created_at),
        "updatedAt": epoch_ms_to_rfc3339(item.updated_at),
    }


def _get_timed(db: Session, session_id: str) -> LearningSession:
    item = db.get(LearningSession, session_id)
    if item is None or item.session_mode != "timed":
        raise AppError(404, "TIMED_SESSION_NOT_FOUND", "The timed session does not exist.")
    return item


@router.post("/manual", status_code=201)
async def create_manual_session(
    payload: ManualSessionCreate,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    started_at = datetime_to_epoch_ms(payload.started_at)
    ended_at = started_at + payload.duration_ms
    item = LearningSession(
        competency_identity_id=payload.competency_identity_id,
        track_id=payload.track_id,
        session_mode="manual",
        timed_state=None,
        activity_type=payload.activity_type,
        assistance_mode=payload.assistance_mode,
        started_at=started_at,
        ended_at=ended_at,
        accumulated_duration_ms=payload.duration_ms,
        duration_ms=payload.duration_ms,
        difficulty=payload.difficulty,
        outcome=payload.outcome,
        notes=payload.notes,
    )
    db.add(item)
    db.flush()
    apply_session_promotion(db, item)
    db.commit()
    return serialize_session(item)


@router.post("/timed", status_code=201)
async def start_timed_session(
    payload: TimedSessionStart,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if active_timed_session(db) is not None:
        raise AppError(
            409, "ACTIVE_SESSION_EXISTS", "Complete or cancel the active timed session first."
        )
    now = utc_now_ms()
    item = LearningSession(
        competency_identity_id=payload.competency_identity_id,
        track_id=payload.track_id,
        session_mode="timed",
        timed_state="running",
        activity_type=payload.activity_type,
        assistance_mode=payload.assistance_mode,
        started_at=now,
        active_since=now,
        accumulated_duration_ms=0,
        notes=payload.notes,
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            409, "ACTIVE_SESSION_EXISTS", "Complete or cancel the active timed session first."
        ) from exc
    return serialize_session(item, now)


@router.get("/active")
async def get_active_session(
    _auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
) -> dict[str, Any]:
    item = active_timed_session(db)
    return {"active": item is not None, "session": serialize_session(item) if item else None}


@router.post("/{session_id}/pause")
async def pause_timed_session(
    session_id: str,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    item = _get_timed(db, session_id)
    if item.timed_state != "running" or item.active_since is None:
        raise AppError(409, "SESSION_NOT_RUNNING", "Only a running session can be paused.")
    now = utc_now_ms()
    item.accumulated_duration_ms += now - item.active_since
    item.active_since = None
    item.timed_state = "paused"
    db.commit()
    return serialize_session(item, now)


@router.post("/{session_id}/resume")
async def resume_timed_session(
    session_id: str,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    item = _get_timed(db, session_id)
    if item.timed_state != "paused" or item.active_since is not None:
        raise AppError(409, "SESSION_NOT_PAUSED", "Only a paused session can be resumed.")
    now = utc_now_ms()
    item.active_since = now
    item.timed_state = "running"
    db.commit()
    return serialize_session(item, now)


def _finalize_timed(
    item: LearningSession, *, cancel: bool, payload: TimedSessionComplete | None
) -> int:
    if item.timed_state not in {"running", "paused"}:
        raise AppError(409, "SESSION_ALREADY_FINALIZED", "The timed session is already finalized.")
    now = utc_now_ms()
    duration = item.accumulated_duration_ms
    if item.timed_state == "running" and item.active_since is not None:
        duration += now - item.active_since
    item.active_since = None
    item.accumulated_duration_ms = duration
    item.duration_ms = duration
    item.ended_at = now
    if cancel:
        item.timed_state = "cancelled"
        item.outcome = "cancelled"
    else:
        if payload is None:
            raise AppError(422, "SESSION_COMPLETION_INVALID", "Completion details are required.")
        item.timed_state = "completed"
        item.outcome = payload.outcome
        item.difficulty = payload.difficulty
        item.notes = payload.notes
    return now


@router.post("/{session_id}/complete")
async def complete_timed_session(
    session_id: str,
    payload: TimedSessionComplete,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    item = _get_timed(db, session_id)
    now = _finalize_timed(item, cancel=False, payload=payload)
    apply_session_promotion(db, item)
    db.commit()
    return serialize_session(item, now)


@router.post("/{session_id}/cancel")
async def cancel_timed_session(
    session_id: str,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    item = _get_timed(db, session_id)
    now = _finalize_timed(item, cancel=True, payload=None)
    db.commit()
    return serialize_session(item, now)


@router.get("")
async def list_sessions(
    competency_identity_id: str | None = None,
    track_id: str | None = None,
    activity_type: str | None = None,
    assistance_mode: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    query = select(LearningSession).order_by(LearningSession.started_at.desc())
    filters = {
        LearningSession.competency_identity_id: competency_identity_id,
        LearningSession.track_id: track_id,
        LearningSession.activity_type: activity_type,
        LearningSession.assistance_mode: assistance_mode,
    }
    for column, value in filters.items():
        if value is not None:
            query = query.where(column == value)
    if start_at is not None:
        if start_at.tzinfo is None or start_at.utcoffset() is None:
            raise AppError(422, "SESSION_RANGE_INVALID", "start_at must include a timezone offset.")
        query = query.where(LearningSession.started_at >= datetime_to_epoch_ms(start_at))
    if end_at is not None:
        if end_at.tzinfo is None or end_at.utcoffset() is None:
            raise AppError(422, "SESSION_RANGE_INVALID", "end_at must include a timezone offset.")
        query = query.where(LearningSession.started_at < datetime_to_epoch_ms(end_at))
    items = db.scalars(query.offset(offset).limit(limit)).all()
    return {"items": [serialize_session(item) for item in items], "limit": limit, "offset": offset}


@router.patch("/{session_id}")
async def update_session(
    session_id: str,
    payload: SessionUpdate,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    item = db.get(LearningSession, session_id)
    if item is None:
        raise AppError(404, "SESSION_NOT_FOUND", "The session does not exist.")
    if item.timed_state in {"running", "paused"}:
        raise AppError(
            409, "ACTIVE_SESSION_EDIT_FORBIDDEN", "An active timed session cannot be edited."
        )
    changes = payload.model_dump(exclude_unset=True)
    if item.outcome == "cancelled" and "outcome" in changes:
        raise AppError(
            422, "CANCELLED_SESSION_IMMUTABLE", "A cancelled session cannot become learning work."
        )
    for key, value in changes.items():
        setattr(item, key, value)
    if "duration_ms" in changes:
        item.accumulated_duration_ms = changes["duration_ms"]
        item.ended_at = item.started_at + changes["duration_ms"]
    apply_session_promotion(db, item)
    db.commit()
    return serialize_session(item)


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    confirm: bool = False,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    if not confirm:
        raise AppError(
            422, "DELETE_CONFIRMATION_REQUIRED", "Session deletion requires confirmation."
        )
    item = db.get(LearningSession, session_id)
    if item is None:
        raise AppError(404, "SESSION_NOT_FOUND", "The session does not exist.")
    if item.timed_state in {"running", "paused"}:
        raise AppError(
            409, "ACTIVE_SESSION_DELETE_FORBIDDEN", "An active session cannot be deleted."
        )
    db.delete(item)
    db.commit()
    return {"deleted": True}
