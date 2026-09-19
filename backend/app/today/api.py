from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.auth import AuthContext, get_auth_context, require_csrf
from app.database import get_db
from app.errors import AppError
from app.time_utils import utc_now_ms
from app.today.contracts import (
    InteractionCorrectionRequest,
    RelationCorrectionRequest,
    SuggestionActivityRelationRequest,
    TodayCompletionRequest,
    TodayExpirationRequest,
    TodayGenerationRequest,
    TodayInteractionRequest,
    TodayReplaceRequest,
    TodayStartRequest,
)
from app.today.models import TodayGeneration, TodaySuggestion
from app.today.service import (
    complete_suggestion,
    correct_interaction,
    correct_relation,
    current_today,
    expire_due_suggestions,
    generate_today,
    generation_detail,
    link_activity,
    record_interaction,
    replace_suggestion,
    start_suggestion,
    suggestion_detail,
    today_history,
)

router = APIRouter(prefix="/today", tags=["v2 today"])


def _commit_or_conflict(db: Session) -> None:
    try:
        db.commit()
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        raise AppError(
            409,
            "TODAY_CONCURRENT_CONFLICT",
            "Another Today or active-Session command won the concurrent race.",
        ) from exc


def _suggestion_response(db: Session, suggestion_id: str) -> dict[str, Any]:
    suggestion = db.get(TodaySuggestion, suggestion_id)
    assert suggestion is not None
    return suggestion_detail(db, suggestion, now_ms=utc_now_ms())


@router.get("/current")
async def get_current_today(
    _auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return current_today(db)


@router.get("/history")
async def get_today_history(
    _auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return today_history(db)


@router.get("/generations/{generation_id}")
async def get_today_generation(
    generation_id: str,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    generation = db.get(TodayGeneration, generation_id)
    if generation is None:
        raise AppError(404, "TODAY_GENERATION_NOT_FOUND", "The generation does not exist.")
    return generation_detail(db, generation)


def _generate(db: Session, payload: TodayGenerationRequest, *, regenerate: bool) -> TodayGeneration:
    return generate_today(
        db,
        idempotency_key=payload.idempotency_key,
        analysis_snapshot_id=payload.analysis_snapshot_id,
        available_time_ms=payload.available_time_ms,
        context_costs=tuple(
            (item.source_type, item.source_entity_id, item.cost) for item in payload.context_costs
        ),
        regenerate=regenerate,
    )


@router.post("/generations", status_code=201)
async def create_today_generation(
    payload: TodayGenerationRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    generation = _generate(db, payload, regenerate=False)
    _commit_or_conflict(db)
    return generation_detail(db, generation)


@router.post("/regenerations", status_code=201)
async def regenerate_today(
    payload: TodayGenerationRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    generation = _generate(db, payload, regenerate=True)
    _commit_or_conflict(db)
    return generation_detail(db, generation)


@router.post("/suggestions/{suggestion_id}/viewed", status_code=201)
async def mark_viewed(
    suggestion_id: str,
    payload: TodayInteractionRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    record_interaction(
        db,
        suggestion_id=suggestion_id,
        interaction_type="viewed",
        idempotency_key=payload.idempotency_key,
        reason_code=payload.reason_code,
        feedback=payload.feedback,
    )
    _commit_or_conflict(db)
    return _suggestion_response(db, suggestion_id)


@router.post("/suggestions/{suggestion_id}/accepted", status_code=201)
async def mark_accepted(
    suggestion_id: str,
    payload: TodayInteractionRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    record_interaction(
        db,
        suggestion_id=suggestion_id,
        interaction_type="accepted",
        idempotency_key=payload.idempotency_key,
        reason_code=payload.reason_code,
        feedback=payload.feedback,
    )
    _commit_or_conflict(db)
    return _suggestion_response(db, suggestion_id)


@router.post("/suggestions/{suggestion_id}/skipped", status_code=201)
async def mark_skipped(
    suggestion_id: str,
    payload: TodayInteractionRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    record_interaction(
        db,
        suggestion_id=suggestion_id,
        interaction_type="skipped",
        idempotency_key=payload.idempotency_key,
        reason_code=payload.reason_code,
        feedback=payload.feedback,
    )
    _commit_or_conflict(db)
    return _suggestion_response(db, suggestion_id)


@router.post("/suggestions/{suggestion_id}/start", status_code=201)
async def start_today_suggestion(
    suggestion_id: str,
    payload: TodayStartRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    start_suggestion(
        db,
        suggestion_id=suggestion_id,
        idempotency_key=payload.idempotency_key,
        assistance_mode=payload.assistance_mode,
        notes=payload.notes,
        contributions=payload.contributions,
    )
    _commit_or_conflict(db)
    return _suggestion_response(db, suggestion_id)


@router.post("/suggestions/{suggestion_id}/completed", status_code=201)
async def mark_completed(
    suggestion_id: str,
    payload: TodayCompletionRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    complete_suggestion(
        db,
        suggestion_id=suggestion_id,
        interaction_type="completed",
        idempotency_key=payload.idempotency_key,
        session_id=payload.session_id,
        feedback=payload.feedback,
    )
    _commit_or_conflict(db)
    return _suggestion_response(db, suggestion_id)


@router.post("/suggestions/{suggestion_id}/partially-completed", status_code=201)
async def mark_partially_completed(
    suggestion_id: str,
    payload: TodayCompletionRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    complete_suggestion(
        db,
        suggestion_id=suggestion_id,
        interaction_type="partially_completed",
        idempotency_key=payload.idempotency_key,
        session_id=payload.session_id,
        feedback=payload.feedback,
    )
    _commit_or_conflict(db)
    return _suggestion_response(db, suggestion_id)


@router.post("/suggestions/{suggestion_id}/replace", status_code=201)
async def replace_today_suggestion(
    suggestion_id: str,
    payload: TodayReplaceRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    replace_suggestion(
        db,
        suggestion_id=suggestion_id,
        idempotency_key=payload.idempotency_key,
        activity_id=payload.activity_id,
        replacement_suggestion_id=payload.replacement_suggestion_id,
        reason_code=payload.reason_code,
        feedback=payload.feedback,
    )
    _commit_or_conflict(db)
    return _suggestion_response(db, suggestion_id)


@router.post("/suggestions/{suggestion_id}/relations", status_code=201)
async def create_activity_relation(
    suggestion_id: str,
    payload: SuggestionActivityRelationRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    link_activity(
        db,
        suggestion_id=suggestion_id,
        activity_id=payload.activity_id,
        relation_type=payload.relation_type,
        idempotency_key=payload.idempotency_key,
    )
    _commit_or_conflict(db)
    return _suggestion_response(db, suggestion_id)


@router.post("/relations/{relation_id}/corrections", status_code=201)
async def correct_activity_relation(
    relation_id: str,
    payload: RelationCorrectionRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    correction = correct_relation(
        db,
        relation_id=relation_id,
        idempotency_key=payload.idempotency_key,
        correction_type=payload.correction_type,
        replacement_activity_id=payload.replacement_activity_id,
        replacement_relation_type=payload.replacement_relation_type,
        reason=payload.reason,
    )
    _commit_or_conflict(db)
    return {
        "id": correction.id,
        "relationId": correction.relation_id,
        "type": correction.correction_type,
        "replacementRelationId": correction.replacement_relation_id,
        "reason": correction.reason,
    }


@router.post("/interactions/{interaction_id}/corrections", status_code=201)
async def correct_today_interaction(
    interaction_id: str,
    payload: InteractionCorrectionRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    correction = correct_interaction(
        db,
        interaction_id=interaction_id,
        idempotency_key=payload.idempotency_key,
        reason=payload.reason,
    )
    _commit_or_conflict(db)
    return {
        "id": correction.id,
        "interactionId": correction.interaction_id,
        "type": "retracted",
        "resultingStatus": correction.resulting_status,
        "reason": correction.reason,
        "actor": correction.actor,
        "source": correction.source,
    }


@router.post("/maintenance/expire")
async def expire_today_suggestions(
    payload: TodayExpirationRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    expired = expire_due_suggestions(db, idempotency_key=payload.idempotency_key)
    _commit_or_conflict(db)
    return {"expiredCount": len(expired), "interactionIds": [item.id for item in expired]}
