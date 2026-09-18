from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import AuthContext, get_auth_context, require_csrf
from app.database import get_db
from app.recommendation.v2.contracts import (
    RecommendationReplayRequest,
    RecommendationRunRequest,
)
from app.recommendation.v2.models import RecommendationV2Run
from app.recommendation.v2.policy import POLICY_REGISTRY_VERSION
from app.recommendation.v2.service import (
    RecommendationGenerationFailure,
    generate_recommendations,
    recommendation_detail,
    recommendation_history,
    record_failed_recommendation_run,
    replay_recommendations,
)

router = APIRouter(prefix="/recommendations", tags=["v2 recommendations"])


@router.post("/runs", status_code=201)
async def create_recommendation_run(
    payload: RecommendationRunRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        run = generate_recommendations(
            db,
            idempotency_key=payload.idempotency_key,
            analysis_snapshot_id=payload.analysis_snapshot_id,
            available_time_ms=payload.available_time_ms,
            context_costs=tuple(
                (item.source_type, item.source_entity_id, item.cost)
                for item in payload.context_costs
            ),
        )
    except Exception as exc:
        db.rollback()
        failure = exc if isinstance(exc, RecommendationGenerationFailure) else None
        record_failed_recommendation_run(
            db,
            idempotency_key=payload.idempotency_key,
            analysis_snapshot_id=payload.analysis_snapshot_id,
            available_time_ms=payload.available_time_ms,
            replay_of_run_id=None,
            error=failure.original_error if failure else exc,
            frozen_input=failure.frozen_input if failure else None,
            policy_registry_version=(
                failure.policy_registry_version if failure else POLICY_REGISTRY_VERSION
            ),
        )
        db.commit()
        if failure:
            raise failure.original_error from failure
        raise
    db.commit()
    return recommendation_detail(db, run.id)


@router.post("/runs/{run_id}/replay", status_code=201)
async def replay_recommendation_run(
    run_id: str,
    payload: RecommendationReplayRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    original = db.get(RecommendationV2Run, run_id)
    try:
        run = replay_recommendations(db, run_id=run_id, idempotency_key=payload.idempotency_key)
    except Exception as exc:
        db.rollback()
        failure = exc if isinstance(exc, RecommendationGenerationFailure) else None
        if original is not None:
            record_failed_recommendation_run(
                db,
                idempotency_key=payload.idempotency_key,
                analysis_snapshot_id=original.analysis_snapshot_id,
                available_time_ms=original.available_time_ms,
                replay_of_run_id=original.id,
                error=failure.original_error if failure else exc,
                frozen_input=failure.frozen_input if failure else None,
                policy_registry_version=(
                    failure.policy_registry_version if failure else original.policy_registry_version
                ),
            )
            db.commit()
        if failure:
            raise failure.original_error from failure
        raise
    db.commit()
    return recommendation_detail(db, run.id)


@router.get("/history")
async def get_recommendation_history(
    _auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return recommendation_history(db)


@router.get("/runs/{run_id}")
async def get_recommendation_run(
    run_id: str,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return recommendation_detail(db, run_id)
