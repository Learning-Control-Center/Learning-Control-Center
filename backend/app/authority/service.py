from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.analysis.v3.service import current_analysis
from app.authority.models import LearningControlAuthorityEvent, LearningControlAuthorityState
from app.authority.semantics import V2_DEFAULT, is_valid_transition
from app.config import Settings
from app.curriculum.models import ActiveCurriculumVersionState
from app.determinism import canonical_json, content_hash
from app.domain_integrity import validate_domain_integrity
from app.errors import AppError
from app.import_export import create_operational_backup
from app.learning_graph.models import ActiveLearningGraphState
from app.models import ActiveTargetProfileState, new_id
from app.portability.registry import PORTABLE_SCHEMA_CURRENT
from app.recommendation.v2.policy import (
    OWNER_APPROVED_POLICY_REGISTRY_VERSION,
    POLICY_REGISTRY_VERSION,
)
from app.roadmap_projection.service import build_projection
from app.time_utils import epoch_ms_to_rfc3339, utc_now_ms

AUTHORITY_POLICY_VERSION = "learning-control-authority-policy/v1"


def _begin_immediate(db: Session) -> None:
    if not db.in_transaction():
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")


def _state_payload(state: LearningControlAuthorityState) -> dict[str, str]:
    return {
        "canonicalLearningAuthority": state.canonical_learning_authority,
        "recommendationPresentation": state.recommendation_presentation,
        "roadmapPresentation": state.roadmap_presentation,
        "todayPresentation": state.today_presentation,
    }


def _state(db: Session) -> LearningControlAuthorityState:
    state = db.get(LearningControlAuthorityState, 1)
    if state is None or state.state_hash != content_hash(_state_payload(state)):
        raise AppError(
            409,
            "AUTHORITY_STATE_INVALID",
            "The learning-control authority state is missing or invalid.",
        )
    events = db.scalars(
        select(LearningControlAuthorityEvent).order_by(LearningControlAuthorityEvent.event_sequence)
    ).all()
    previous: dict[str, str] | None = None
    canonical_v2 = False
    valid = bool(events)
    for expected_sequence, event in enumerate(events, start=1):
        try:
            resulting = json.loads(event.resulting_state_json)
            prior = json.loads(event.prior_state_json) if event.prior_state_json else None
        except (json.JSONDecodeError, TypeError):
            valid = False
            break
        payload: dict[str, Any] = {
            "commandType": event.command_type,
            "reason": event.reason,
            "resultingState": resulting,
        }
        if prior is not None:
            payload["priorState"] = prior
        if (
            event.event_sequence != expected_sequence
            or prior != previous
            or event.payload_hash != content_hash(payload)
            or not is_valid_transition(
                sequence=event.event_sequence,
                command_type=event.command_type,
                prior=prior,
                resulting=resulting,
                actor=event.actor,
                source=event.source,
            )
            or canonical_v2
            and resulting.get("canonicalLearningAuthority") != "v2"
        ):
            valid = False
            break
        canonical_v2 = canonical_v2 or resulting.get("canonicalLearningAuthority") == "v2"
        previous = resulting
    last_event = events[-1] if events else None
    if (
        not valid
        or last_event is None
        or last_event.id != state.last_event_id
        or last_event.event_sequence != state.event_sequence
        or previous != _state_payload(state)
    ):
        raise AppError(
            409,
            "AUTHORITY_HISTORY_INVALID",
            "The learning-control authority history is inconsistent.",
        )
    return state


def authority_state(db: Session) -> dict[str, Any]:
    state = _state(db)
    return {
        **_state_payload(state),
        "eventSequence": state.event_sequence,
        "stateHash": state.state_hash,
        "updatedAt": epoch_ms_to_rfc3339(state.updated_at),
        "policyVersion": AUTHORITY_POLICY_VERSION,
    }


def authority_history(db: Session) -> list[dict[str, Any]]:
    _state(db)
    events = db.scalars(
        select(LearningControlAuthorityEvent).order_by(
            LearningControlAuthorityEvent.event_sequence.desc()
        )
    ).all()
    return [
        {
            "id": event.id,
            "eventSequence": event.event_sequence,
            "commandType": event.command_type,
            "priorState": json.loads(event.prior_state_json) if event.prior_state_json else None,
            "resultingState": json.loads(event.resulting_state_json),
            "reason": event.reason,
            "actor": event.actor,
            "source": event.source,
            "payloadHash": event.payload_hash,
            "occurredAt": epoch_ms_to_rfc3339(event.occurred_at),
        }
        for event in events
    ]


def readiness_report(db: Session) -> dict[str, Any]:
    active_profile = db.get(ActiveTargetProfileState, 1)
    active_graph = db.get(ActiveLearningGraphState, 1)
    active_curricula = (
        db.scalar(select(func.count(ActiveCurriculumVersionState.curriculum_id))) or 0
    )
    analysis = current_analysis(db, purpose="learning_control")
    analysis_snapshot = analysis.get("snapshot")
    try:
        projection = build_projection(db)
        projection_ready = bool(projection.get("configured"))
        projection_detail = projection.get("scopeKey")
    except AppError as exc:
        projection_ready = False
        projection_detail = exc.code
    try:
        validate_domain_integrity(db.connection())
        integrity_ready = True
        integrity_detail = "valid"
    except (AppError, ValueError) as exc:
        integrity_ready = False
        integrity_detail = exc.code if isinstance(exc, AppError) else type(exc).__name__
    checks = [
        {
            "code": "ACTIVE_TARGET_PROFILE",
            "ready": active_profile is not None,
            "detail": active_profile.target_profile_version_id if active_profile else None,
        },
        {
            "code": "ACTIVE_LEARNING_GRAPH",
            "ready": active_graph is not None,
            "detail": active_graph.learning_graph_version_id if active_graph else None,
        },
        {
            "code": "ACTIVE_CURRICULUM",
            "ready": active_curricula > 0,
            "detail": active_curricula,
        },
        {
            "code": "ROADMAP_PROJECTION",
            "ready": projection_ready,
            "detail": projection_detail,
        },
        {
            "code": "ANALYSIS_V3_CURRENT",
            "ready": bool(
                analysis.get("status") == "current"
                and isinstance(analysis_snapshot, dict)
                and analysis_snapshot.get("completeness") in {"complete", "partial"}
            ),
            "detail": (
                {
                    "snapshotId": analysis_snapshot.get("id"),
                    "completeness": analysis_snapshot.get("completeness"),
                    "unknownCount": len(analysis_snapshot.get("unknownMarkers", [])),
                }
                if isinstance(analysis_snapshot, dict)
                else analysis.get("status")
            ),
        },
        {
            "code": "RECOMMENDATION_POLICY_APPROVED",
            "ready": POLICY_REGISTRY_VERSION == OWNER_APPROVED_POLICY_REGISTRY_VERSION,
            "detail": POLICY_REGISTRY_VERSION,
        },
        {
            "code": "PORTABLE_SCHEMA_V10",
            "ready": PORTABLE_SCHEMA_CURRENT >= 10,
            "detail": PORTABLE_SCHEMA_CURRENT,
        },
        {
            "code": "DOMAIN_INTEGRITY",
            "ready": integrity_ready,
            "detail": integrity_detail,
        },
    ]
    return {
        "ready": all(item["ready"] for item in checks),
        "checks": checks,
        "authorityPolicyVersion": AUTHORITY_POLICY_VERSION,
    }


def _append_event(
    db: Session,
    state: LearningControlAuthorityState,
    *,
    idempotency_key: str,
    command_type: str,
    resulting: dict[str, str],
    reason: str,
    now_ms: int,
) -> LearningControlAuthorityEvent:
    existing = db.scalar(
        select(LearningControlAuthorityEvent).where(
            LearningControlAuthorityEvent.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        if (
            existing.command_type != command_type
            or existing.reason != reason
            or json.loads(existing.resulting_state_json) != resulting
        ):
            raise AppError(409, "AUTHORITY_IDEMPOTENCY_CONFLICT", "The key was already used.")
        return existing
    prior = _state_payload(state)
    if not is_valid_transition(
        sequence=state.event_sequence + 1,
        command_type=command_type,
        prior=prior,
        resulting=resulting,
        actor="user",
        source="authority_api",
    ):
        raise AppError(
            409,
            "AUTHORITY_TRANSITION_INVALID",
            "The requested authority transition is not permitted.",
        )
    payload = {
        "commandType": command_type,
        "priorState": prior,
        "reason": reason,
        "resultingState": resulting,
    }
    payload_hash = content_hash(payload)
    event = LearningControlAuthorityEvent(
        id=new_id(),
        event_sequence=state.event_sequence + 1,
        idempotency_key=idempotency_key,
        command_type=command_type,
        prior_state_json=canonical_json(prior),
        resulting_state_json=canonical_json(resulting),
        reason=reason,
        actor="user",
        source="authority_api",
        payload_hash=payload_hash,
        occurred_at=now_ms,
    )
    db.add(event)
    db.flush()
    state.canonical_learning_authority = resulting["canonicalLearningAuthority"]
    state.roadmap_presentation = resulting["roadmapPresentation"]
    state.recommendation_presentation = resulting["recommendationPresentation"]
    state.today_presentation = resulting["todayPresentation"]
    state.event_sequence = event.event_sequence
    state.last_event_id = event.id
    state.state_hash = content_hash(resulting)
    state.updated_at = now_ms
    db.flush()
    return event


def activate_v2(
    db: Session, *, idempotency_key: str, reason: str, settings: Settings
) -> dict[str, Any]:
    try:
        _begin_immediate(db)
        state = _state(db)
        existing = db.scalar(
            select(LearningControlAuthorityEvent).where(
                LearningControlAuthorityEvent.idempotency_key == idempotency_key
            )
        )
        resulting = V2_DEFAULT.copy()
        if existing is not None:
            expected = content_hash(
                {
                    "commandType": "activate_v2",
                    "priorState": json.loads(existing.prior_state_json or "null"),
                    "reason": reason,
                    "resultingState": resulting,
                }
            )
            if existing.payload_hash != expected:
                raise AppError(409, "AUTHORITY_IDEMPOTENCY_CONFLICT", "The key was already used.")
            return authority_state(db)
        if state.canonical_learning_authority == "v2":
            raise AppError(409, "AUTHORITY_ALREADY_V2", "Canonical V2 authority is already active.")
        readiness = readiness_report(db)
        if not readiness["ready"]:
            raise AppError(
                409,
                "AUTHORITY_NOT_READY",
                "V2 authority readiness checks have not passed.",
                {"checks": readiness["checks"]},
            )
        create_operational_backup(db, settings, "pre-v2-authority-activation")
        _append_event(
            db,
            state,
            idempotency_key=idempotency_key,
            command_type="activate_v2",
            resulting=resulting,
            reason=reason,
            now_ms=utc_now_ms(),
        )
        return authority_state(db)
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        raise AppError(
            409, "AUTHORITY_CONCURRENT_CONFLICT", "Another authority command won the race."
        ) from exc


def change_surfaces(
    db: Session,
    *,
    idempotency_key: str,
    roadmap_presentation: str,
    recommendation_presentation: str,
    today_presentation: str,
    reason: str,
) -> dict[str, Any]:
    try:
        _begin_immediate(db)
        state = _state(db)
        if state.canonical_learning_authority != "v2":
            raise AppError(
                409,
                "AUTHORITY_V2_NOT_ACTIVE",
                "Presentation fallback is available only after canonical V2 activation.",
            )
        resulting = {
            "canonicalLearningAuthority": "v2",
            "recommendationPresentation": recommendation_presentation,
            "roadmapPresentation": roadmap_presentation,
            "todayPresentation": today_presentation,
        }
        _append_event(
            db,
            state,
            idempotency_key=idempotency_key,
            command_type="surface_change",
            resulting=resulting,
            reason=reason,
            now_ms=utc_now_ms(),
        )
        return authority_state(db)
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        raise AppError(
            409, "AUTHORITY_CONCURRENT_CONFLICT", "Another authority command won the race."
        ) from exc


def require_legacy_roadmap_writable(db: Session) -> None:
    if _state(db).canonical_learning_authority == "v2":
        raise AppError(
            409,
            "LEGACY_AUTHORITY_READ_ONLY",
            "Legacy Roadmap is read-only after canonical V2 activation.",
        )


def require_legacy_today_generation(db: Session) -> None:
    if _state(db).canonical_learning_authority == "v2":
        raise AppError(
            409,
            "LEGACY_AUTHORITY_READ_ONLY",
            "Legacy Today generation is disabled after canonical V2 activation.",
        )


def require_legacy_recommendation_writable(db: Session) -> None:
    if _state(db).canonical_learning_authority == "v2":
        raise AppError(
            409,
            "LEGACY_AUTHORITY_READ_ONLY",
            "Legacy Recommendation decisions are read-only after canonical V2 activation.",
        )
