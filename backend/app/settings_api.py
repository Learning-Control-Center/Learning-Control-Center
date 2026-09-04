from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import AuthContext, get_auth_context, require_csrf
from app.database import get_db
from app.models import DisciplineProfile
from app.schemas import DisciplineUpdate
from app.time_utils import epoch_ms_to_rfc3339

router = APIRouter(prefix="/settings", tags=["settings"])
logger = logging.getLogger(__name__)


def get_or_create_profile(db: Session) -> DisciplineProfile:
    profile = db.get(DisciplineProfile, 1)
    if profile is None:
        profile = DisciplineProfile(id=1)
        db.add(profile)
        db.commit()
    return profile


@router.get("/discipline")
async def get_discipline(
    _auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
) -> dict[str, object]:
    profile = get_or_create_profile(db)
    return {
        "weeklyTargetActiveDays": profile.weekly_target_active_days,
        "targetDurationMsPerActiveDay": profile.target_duration_ms_per_active_day,
        "timezone": profile.timezone,
        "updatedAt": epoch_ms_to_rfc3339(profile.updated_at),
    }


@router.put("/discipline")
async def update_discipline(
    payload: DisciplineUpdate,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    profile = get_or_create_profile(db)
    profile.weekly_target_active_days = payload.weekly_target_active_days
    profile.target_duration_ms_per_active_day = payload.target_duration_ms_per_active_day
    profile.timezone = payload.timezone
    db.commit()
    logger.info("Discipline settings updated")
    return {
        "weeklyTargetActiveDays": profile.weekly_target_active_days,
        "targetDurationMsPerActiveDay": profile.target_duration_ms_per_active_day,
        "timezone": profile.timezone,
        "updatedAt": epoch_ms_to_rfc3339(profile.updated_at),
    }
