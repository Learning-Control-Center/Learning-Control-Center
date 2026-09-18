from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.analysis.v3.contracts import AnalysisReplayRequest, AnalysisRunRequest
from app.analysis.v3.service import (
    current_analysis,
    cutoff_from_request,
    history,
    record_failed_analysis_run,
    replay_analysis,
    run_analysis,
    snapshot_detail,
)
from app.auth import AuthContext, get_auth_context, require_csrf
from app.database import get_db
from app.models import AnalysisRun

router = APIRouter(prefix="/analysis", tags=["v2 analysis"])


@router.post("/runs", status_code=201)
async def create_analysis_run(
    payload: AnalysisRunRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    cutoff = cutoff_from_request(payload.cutoff_at)
    try:
        snapshot = run_analysis(
            db,
            idempotency_key=payload.idempotency_key,
            purpose=payload.purpose,
            cutoff_at=cutoff,
        )
    except Exception as exc:
        db.rollback()
        record_failed_analysis_run(
            db,
            idempotency_key=payload.idempotency_key,
            purpose=payload.purpose,
            cutoff_at=cutoff,
            replay_of_run_id=None,
            error=exc,
        )
        db.commit()
        raise
    db.commit()
    return snapshot_detail(db, snapshot.id)


@router.post("/runs/{run_id}/replay", status_code=201)
async def replay_analysis_run(
    run_id: str,
    payload: AnalysisReplayRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    original = db.get(AnalysisRun, run_id)
    try:
        snapshot = replay_analysis(db, run_id=run_id, idempotency_key=payload.idempotency_key)
    except Exception as exc:
        db.rollback()
        record_failed_analysis_run(
            db,
            idempotency_key=payload.idempotency_key,
            purpose=original.purpose if original else "learning_control",
            cutoff_at=original.cutoff_at if original else None,
            replay_of_run_id=run_id if original else None,
            error=exc,
        )
        db.commit()
        raise
    db.commit()
    return snapshot_detail(db, snapshot.id)


@router.get("/current")
async def get_current_analysis(
    purpose: str = Query(
        default="learning_control", pattern="^(learning_control|candidate_readiness)$"
    ),
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return current_analysis(db, purpose=purpose)


@router.get("/history")
async def get_analysis_history(
    _auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return history(db)


@router.get("/snapshots/{snapshot_id}")
async def get_analysis_snapshot(
    snapshot_id: str,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return snapshot_detail(db, snapshot_id)
