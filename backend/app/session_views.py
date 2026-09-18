from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LearningSession, SessionCorrection


@dataclass(frozen=True)
class SessionActualityPublicDTO:
    session_id: str
    activity_id: str
    assistance_mode: str
    started_at: int
    ended_at: int
    duration_ms: int
    outcome: str
    recorded_at: int


def session_actuality_as_of(
    db: Session, *, session_id: str, exclusive_cutoff_at: int
) -> SessionActualityPublicDTO | None:
    """Replay a physical Session as known strictly before the requested cutoff."""
    item = db.get(LearningSession, session_id)
    if item is None or item.created_at >= exclusive_cutoff_at:
        return None
    values: dict[str, Any] = {
        "activity_id": item.activity_id,
        "assistance_mode": item.assistance_mode,
        "started_at": item.started_at,
        "ended_at": item.ended_at,
        "duration_ms": item.duration_ms,
        "outcome": item.outcome,
    }
    corrections = db.scalars(
        select(SessionCorrection)
        .where(
            SessionCorrection.session_id == item.id,
            SessionCorrection.corrected_at >= exclusive_cutoff_at,
        )
        .order_by(SessionCorrection.corrected_at.desc(), SessionCorrection.id.desc())
    ).all()
    for correction in corrections:
        before = json.loads(correction.before_json)
        for key, value in before.items():
            if key in values:
                values[key] = value
    if item.tombstoned_at is not None and item.tombstoned_at < exclusive_cutoff_at:
        return None
    ended_at = values["ended_at"]
    duration_ms = values["duration_ms"]
    outcome = values["outcome"]
    if (
        not isinstance(ended_at, int)
        or ended_at >= exclusive_cutoff_at
        or not isinstance(duration_ms, int)
        or duration_ms < 0
        or outcome not in {"completed", "partial"}
    ):
        return None
    return SessionActualityPublicDTO(
        session_id=item.id,
        activity_id=str(values["activity_id"]),
        assistance_mode=str(values["assistance_mode"]),
        started_at=int(values["started_at"]),
        ended_at=ended_at,
        duration_ms=duration_ms,
        outcome=outcome,
        recorded_at=item.created_at,
    )
