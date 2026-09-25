from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.assessment.contracts import AssessmentReviewRequest
from app.assessment.models import AssessmentExecution
from app.assessment.service import (
    MAX_ASSESSMENT_ARTIFACT_BYTES,
    assessment_execution_detail,
    assessment_review_result,
    capture_assessment_artifact,
    submit_assessment_review,
)
from app.auth import AuthContext, get_auth_context, require_csrf
from app.database import get_db
from app.errors import AppError
from app.recommendation.v2.public import load_public_recommendation_item
from app.today.models import TodayInteraction, TodaySuggestion, TodaySuggestionCurrentState

router = APIRouter(prefix="/assessment-executions", tags=["v2 assessment"])


@router.get("/by-suggestion/{suggestion_id}")
async def get_execution_by_suggestion(
    suggestion_id: str,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    execution = db.scalar(
        select(AssessmentExecution).where(AssessmentExecution.today_suggestion_id == suggestion_id)
    )
    if execution is None:
        suggestion = db.get(TodaySuggestion, suggestion_id)
        state = db.get(TodaySuggestionCurrentState, suggestion_id)
        interaction = (
            db.get(TodayInteraction, state.latest_interaction_id)
            if state is not None and state.latest_interaction_id
            else None
        )
        candidate = (
            load_public_recommendation_item(
                db,
                run_id=suggestion.recommendation_run_id,
                candidate_id=suggestion.candidate_id,
            )
            if suggestion is not None
            else None
        )
        command = (
            json.loads(interaction.structured_reason_json).get("command") or {}
            if interaction is not None
            else {}
        )
        if (
            candidate is not None
            and candidate.candidate_type == "assessment"
            and state is not None
            and state.status == "started"
            and interaction is not None
            and interaction.interaction_type == "started"
            and not command.get("assessmentUnitDefinitionId")
            and not command.get("assessmentOpportunityId")
        ):
            return {
                "kind": "legacy_started",
                "suggestionId": suggestion_id,
                "sessionId": interaction.session_id,
                "message": (
                    "This earlier Session remains actual work history, but it has no exact "
                    "authored task binding and cannot become reviewed assessment Evidence. "
                    "Finish it, then start a new bound attempt."
                ),
            }
        raise AppError(
            404,
            "ASSESSMENT_EXECUTION_NOT_FOUND",
            "No assessment execution is bound to this suggestion.",
        )
    return assessment_execution_detail(db, execution.id)


@router.get("")
async def list_executions(
    activity_id: str | None = None,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    statement = select(AssessmentExecution).order_by(
        AssessmentExecution.started_at.desc(), AssessmentExecution.id.desc()
    )
    if activity_id is not None:
        statement = statement.where(AssessmentExecution.activity_id == activity_id)
    return {
        "items": [assessment_execution_detail(db, item.id) for item in db.scalars(statement).all()]
    }


@router.get("/{execution_id}")
async def get_execution(
    execution_id: str,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return assessment_execution_detail(db, execution_id)


@router.post("/{execution_id}/reviews", status_code=201)
async def create_review(
    execution_id: str,
    payload: AssessmentReviewRequest,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        review = submit_assessment_review(db, execution_id, payload, auth.user.id)
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        raise AppError(
            409,
            "ASSESSMENT_REVIEW_CONFLICT",
            "A concurrent assessment review changed this execution; reload before retrying.",
        ) from exc
    return assessment_review_result(db, review)


@router.post("/{execution_id}/artifacts", status_code=201)
async def upload_artifact(
    execution_id: str,
    request: Request,
    criterion_definition_id: str = Query(min_length=1, max_length=36),
    filename: str = Query(min_length=1, max_length=255),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if request.headers.get("content-type") != "application/octet-stream":
        raise AppError(
            415, "ASSESSMENT_ARTIFACT_TYPE", "Upload artifact bytes as application/octet-stream."
        )
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_ASSESSMENT_ARTIFACT_BYTES:
            raise AppError(413, "ASSESSMENT_ARTIFACT_SIZE", "Artifact exceeds 262144 bytes.")
    try:
        artifact = capture_assessment_artifact(
            db, execution_id, criterion_definition_id, idempotency_key, filename, bytes(content)
        )
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        raise AppError(
            409,
            "ASSESSMENT_ARTIFACT_CONFLICT",
            "A concurrent artifact upload changed this assessment; reload before retrying.",
        ) from exc
    return {
        "id": artifact.id,
        "criterionDefinitionId": artifact.criterion_definition_id,
        "filename": artifact.filename,
        "sizeBytes": artifact.size_bytes,
        "sha256": artifact.sha256,
        "captureMethod": artifact.capture_method,
    }
