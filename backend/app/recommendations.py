from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.analysis.persistence import persist_envelope
from app.analysis.v1_compat import build_v1_recommendation_envelope
from app.api_serialization import serialize_api_instants
from app.auth import AuthContext, get_auth_context, require_csrf
from app.database import get_db
from app.errors import AppError
from app.models import RecommendationSnapshot
from app.recommendation.v1_policy import RECOMMENDATION_VERSION, evaluate
from app.recommendation.v1_policy import round_half_up as _round_half_up
from app.schemas import RecommendationDecision

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


def round_half_up(value: float) -> int:
    return _round_half_up(value)


def build_recommendation(db: Session, *, now_ms: int | None = None) -> dict[str, Any]:
    """Compatibility entry point; reads persistence once and evaluates an immutable DTO."""
    return evaluate(build_v1_recommendation_envelope(db, now_ms=now_ms))


@router.get("/today")
async def today_recommendation(
    _auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
) -> dict[str, Any]:
    envelope = build_v1_recommendation_envelope(db)
    analysis_snapshot = persist_envelope(db, envelope)
    payload = evaluate(envelope)
    if payload.get("primary") is not None:
        snapshot = RecommendationSnapshot(
            local_date=payload["localDate"],
            engine_version=RECOMMENDATION_VERSION,
            primary_competency_identity_id=payload["primary"]["competencyIdentityId"],
            primary_activity_type=payload["primary"]["activity"],
            secondary_competency_identity_id=(
                payload["secondary"]["competencyIdentityId"] if payload.get("secondary") else None
            ),
            structured_payload_json=json.dumps(payload, separators=(",", ":")),
            analysis_snapshot_id=analysis_snapshot.id,
        )
        db.add(snapshot)
        db.commit()
        payload["snapshotId"] = snapshot.id
    else:
        db.commit()
    return serialize_api_instants(payload)


@router.post("/{snapshot_id}/decision")
async def record_decision(
    snapshot_id: str,
    payload: RecommendationDecision,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    snapshot = db.get(RecommendationSnapshot, snapshot_id)
    if snapshot is None:
        raise AppError(
            404, "RECOMMENDATION_NOT_FOUND", "The recommendation snapshot does not exist."
        )
    snapshot.accepted_primary = payload.accepted_primary
    snapshot.chosen_competency_identity_id = payload.chosen_competency_identity_id
    db.commit()
    return {
        "snapshotId": snapshot.id,
        "acceptedPrimary": snapshot.accepted_primary,
        "chosenCompetencyIdentityId": snapshot.chosen_competency_identity_id,
    }
