from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Activity


@dataclass(frozen=True)
class ActivityActualityPublicDTO:
    activity_id: str
    outcome_classification: str | None
    actual_at: int
    recorded_at: int


def activity_actuality_as_of(
    db: Session, *, activity_id: str, exclusive_cutoff_at: int
) -> ActivityActualityPublicDTO | None:
    """Return Activity actuality only after both occurrence and recording are visible.

    A bounded context end is the authoritative completion instant when present;
    otherwise the declared occurrence instant is used. Creation is the explicit
    fallback for an Activity whose occurrence is unknown.
    """
    activity = db.get(Activity, activity_id)
    if activity is None:
        return None
    actual_at = (
        activity.context_ended_at
        if activity.context_ended_at is not None
        else activity.occurred_at
        if activity.occurred_at is not None
        else activity.created_at
    )
    if activity.created_at >= exclusive_cutoff_at or actual_at >= exclusive_cutoff_at:
        return None
    return ActivityActualityPublicDTO(
        activity_id=activity.id,
        outcome_classification=activity.outcome_classification,
        actual_at=actual_at,
        recorded_at=activity.created_at,
    )
