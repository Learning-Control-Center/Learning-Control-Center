from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.recommendation.v2.models import (
    RecommendationV2Candidate,
    RecommendationV2Reason,
    RecommendationV2Recommendation,
    RecommendationV2Run,
)
from app.recommendation.v2.policy import POLICY_REGISTRY_VERSION
from app.recommendation.v2.service import (
    RecommendationGenerationFailure,
    generate_recommendations,
    record_failed_recommendation_run,
)


@dataclass(frozen=True)
class FrozenJsonObject:
    items: tuple[tuple[str, FrozenPublicJson], ...]


@dataclass(frozen=True)
class FrozenJsonArray:
    items: tuple[FrozenPublicJson, ...]


type FrozenPublicJson = str | int | float | bool | None | FrozenJsonObject | FrozenJsonArray


def freeze_public_json(value: Any) -> FrozenPublicJson:
    if isinstance(value, dict):
        return FrozenJsonObject(
            tuple((str(key), freeze_public_json(item)) for key, item in sorted(value.items()))
        )
    if isinstance(value, list):
        return FrozenJsonArray(tuple(freeze_public_json(item) for item in value))
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"Unsupported public JSON value: {type(value).__name__}")


def thaw_public_json(value: FrozenPublicJson) -> Any:
    if isinstance(value, FrozenJsonObject):
        return {key: thaw_public_json(item) for key, item in value.items}
    if isinstance(value, FrozenJsonArray):
        return [thaw_public_json(item) for item in value.items]
    return value


@dataclass(frozen=True)
class PublicRecommendationReasonDTO:
    code: str
    title: str
    text: str
    facts: FrozenPublicJson


@dataclass(frozen=True)
class PublicRecommendationItemDTO:
    recommendation_id: str
    candidate_id: str
    candidate_type: str
    source_type: str
    source_entity_id: str
    source_version_id: str | None
    candidate_stable_id: str
    title: str
    description: str
    project_id: str | None
    competency_identity_id: str | None
    portfolio_role: str
    rank: int
    score: int
    reason_summary: str
    advisory_duration_ms: int | None
    duration_minimum_ms: int | None
    duration_preferred_ms: int | None
    duration_maximum_ms: int | None
    reasons: tuple[PublicRecommendationReasonDTO, ...]


@dataclass(frozen=True)
class PublicRecommendationRunDTO:
    id: str
    status: str
    analysis_snapshot_id: str
    policy_registry_version: str
    items: tuple[PublicRecommendationItemDTO, ...]


def load_public_recommendation_run(
    db: Session, run_id: str
) -> PublicRecommendationRunDTO:
    run = db.get(RecommendationV2Run, run_id)
    if run is None:
        raise AppError(404, "RECOMMENDATION_RUN_NOT_FOUND", "Recommendation run not found.")
    recommendations = db.scalars(
        select(RecommendationV2Recommendation)
        .where(RecommendationV2Recommendation.run_id == run.id)
        .order_by(RecommendationV2Recommendation.rank_ordinal)
    ).all()
    items: list[PublicRecommendationItemDTO] = []
    for recommendation in recommendations:
        candidate = db.get(RecommendationV2Candidate, recommendation.candidate_id)
        if candidate is None:
            raise AppError(
                409,
                "RECOMMENDATION_HISTORY_INVALID",
                "A selected Recommendation candidate is unavailable.",
            )
        reasons = db.scalars(
            select(RecommendationV2Reason)
            .where(RecommendationV2Reason.candidate_id == candidate.id)
            .order_by(RecommendationV2Reason.ordinal)
        ).all()
        items.append(
            PublicRecommendationItemDTO(
                recommendation_id=recommendation.id,
                candidate_id=candidate.id,
                candidate_type=candidate.candidate_type,
                source_type=candidate.source_type,
                source_entity_id=candidate.source_entity_id,
                source_version_id=candidate.source_version_id,
                candidate_stable_id=candidate.stable_id,
                title=candidate.title,
                description=candidate.description,
                project_id=candidate.project_id,
                competency_identity_id=candidate.competency_identity_id,
                portfolio_role=recommendation.portfolio_role,
                rank=recommendation.rank_ordinal,
                score=recommendation.score_total,
                reason_summary=recommendation.reason_summary,
                advisory_duration_ms=recommendation.advisory_duration_ms,
                duration_minimum_ms=recommendation.duration_minimum_ms,
                duration_preferred_ms=recommendation.duration_preferred_ms,
                duration_maximum_ms=recommendation.duration_maximum_ms,
                reasons=tuple(
                    PublicRecommendationReasonDTO(
                        code=reason.reason_code,
                        title=reason.title,
                        text=reason.rendered_text,
                        facts=freeze_public_json(json.loads(reason.explanation_facts_json)),
                    )
                    for reason in reasons
                ),
            )
        )
    return PublicRecommendationRunDTO(
        id=run.id,
        status=run.status,
        analysis_snapshot_id=run.analysis_snapshot_id,
        policy_registry_version=run.policy_registry_version,
        items=tuple(items),
    )


def generate_public_recommendation_run(
    db: Session,
    *,
    idempotency_key: str,
    analysis_snapshot_id: str,
    available_time_ms: int | None,
    context_costs: tuple[tuple[str, str, str], ...] = (),
) -> PublicRecommendationRunDTO:
    try:
        run = generate_recommendations(
            db,
            idempotency_key=idempotency_key,
            analysis_snapshot_id=analysis_snapshot_id,
            available_time_ms=available_time_ms,
            context_costs=context_costs,
        )
    except Exception as exc:
        db.rollback()
        failure = exc if isinstance(exc, RecommendationGenerationFailure) else None
        record_failed_recommendation_run(
            db,
            idempotency_key=idempotency_key,
            analysis_snapshot_id=analysis_snapshot_id,
            available_time_ms=available_time_ms,
            replay_of_run_id=None,
            error=failure.original_error if failure else exc,
            frozen_input=failure.frozen_input if failure else None,
            context_costs=context_costs,
            policy_registry_version=(
                failure.policy_registry_version if failure else POLICY_REGISTRY_VERSION
            ),
        )
        db.commit()
        if failure:
            raise failure.original_error from failure
        raise
    return load_public_recommendation_run(db, run.id)


def load_public_recommendation_item(
    db: Session, *, run_id: str, candidate_id: str
) -> PublicRecommendationItemDTO:
    run = load_public_recommendation_run(db, run_id)
    item = next((value for value in run.items if value.candidate_id == candidate_id), None)
    if item is None:
        raise AppError(
            409,
            "RECOMMENDATION_HISTORY_INVALID",
            "The selected Recommendation item is unavailable.",
        )
    return item
