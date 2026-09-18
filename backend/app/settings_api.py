from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.contracts import canonical_json, content_hash
from app.auth import AuthContext, get_auth_context, require_csrf
from app.database import get_db
from app.models import DisciplineConfigurationEvent, DisciplineProfile, ProjectionInvalidation
from app.schemas import DisciplineUpdate
from app.time_utils import epoch_ms_to_rfc3339, utc_now_ms

router = APIRouter(prefix="/settings", tags=["settings"])
logger = logging.getLogger(__name__)


def _configuration_payload(profile: DisciplineProfile) -> dict[str, object]:
    return {
        "adaptationPhaseConfig": json.loads(profile.adaptation_phase_config_json),
        "targetDurationMsPerActiveDay": profile.target_duration_ms_per_active_day,
        "timezone": profile.timezone,
        "weeklyTargetActiveDays": profile.weekly_target_active_days,
    }


def _append_configuration_event(
    db: Session, profile: DisciplineProfile, *, source: str, idempotency_key: str
) -> DisciplineConfigurationEvent:
    existing = db.scalar(
        select(DisciplineConfigurationEvent).where(
            DisciplineConfigurationEvent.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        return existing
    payload = _configuration_payload(profile)
    event = DisciplineConfigurationEvent(
        event_sequence=int(
            db.scalar(select(func.max(DisciplineConfigurationEvent.event_sequence))) or 0
        )
        + 1,
        idempotency_key=idempotency_key,
        configuration_json=canonical_json(payload),
        configuration_hash=content_hash(payload),
        recorded_at=profile.updated_at,
        source=source,
    )
    db.add(event)
    db.flush()
    return event


def get_or_create_profile(db: Session) -> DisciplineProfile:
    profile = db.get(DisciplineProfile, 1)
    if profile is None:
        profile = DisciplineProfile(id=1)
        db.add(profile)
        db.flush()
        _append_configuration_event(
            db, profile, source="bootstrap", idempotency_key="discipline-bootstrap-v1"
        )
        db.commit()
    elif db.scalar(select(func.count(DisciplineConfigurationEvent.id))) == 0:
        _append_configuration_event(
            db, profile, source="compatibility_baseline", idempotency_key="discipline-baseline-v1"
        )
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
    if (
        profile.weekly_target_active_days == payload.weekly_target_active_days
        and profile.target_duration_ms_per_active_day == payload.target_duration_ms_per_active_day
        and profile.timezone == payload.timezone
    ):
        return {
            "weeklyTargetActiveDays": profile.weekly_target_active_days,
            "targetDurationMsPerActiveDay": profile.target_duration_ms_per_active_day,
            "timezone": profile.timezone,
            "updatedAt": epoch_ms_to_rfc3339(profile.updated_at),
        }
    profile.weekly_target_active_days = payload.weekly_target_active_days
    profile.target_duration_ms_per_active_day = payload.target_duration_ms_per_active_day
    profile.timezone = payload.timezone
    profile.updated_at = utc_now_ms()
    event = _append_configuration_event(
        db, profile, source="user", idempotency_key=str(uuid.uuid4())
    )
    db.add(
        ProjectionInvalidation(
            projection_kind="analysis",
            subject_type="discipline_configuration",
            subject_id="global",
            source_fact_id=event.id,
            target_policy_version="analysis-policy/v3.0",
            status="pending",
            attempt_count=0,
            requested_at=event.recorded_at,
        )
    )
    db.commit()
    logger.info("Discipline settings updated")
    return {
        "weeklyTargetActiveDays": profile.weekly_target_active_days,
        "targetDurationMsPerActiveDay": profile.target_duration_ms_per_active_day,
        "timezone": profile.timezone,
        "updatedAt": epoch_ms_to_rfc3339(profile.updated_at),
    }
