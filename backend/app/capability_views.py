from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CapabilityEvaluationRun, CapabilityScaleLevel, CriterionEvaluationResult


@dataclass(frozen=True)
class CapabilityPublicDTO:
    semantic_definition_id: str
    scale_version_id: str
    dimension_id: str | None
    assessment_status: str
    selected_level_id: str | None
    selected_level_ordinal: int | None
    aggregate_confidence: str
    evaluation_run_id: str
    evaluated_cutoff_at: int
    recorded_at: int


@dataclass(frozen=True)
class CriterionEvaluationPublicDTO:
    criterion_definition_id: str
    state: str
    evaluation_run_id: str
    evaluated_cutoff_at: int
    recorded_at: int


def capability_as_of(
    db: Session,
    *,
    semantic_definition_id: str,
    scale_version_id: str,
    dimension_id: str | None,
    exclusive_cutoff_at: int,
) -> CapabilityPublicDTO | None:
    """Return the latest immutable capability fact known strictly before a cutoff."""
    run = db.scalar(
        select(CapabilityEvaluationRun)
        .where(
            CapabilityEvaluationRun.semantic_definition_id == semantic_definition_id,
            CapabilityEvaluationRun.scale_version_id == scale_version_id,
            CapabilityEvaluationRun.dimension_id == dimension_id,
            CapabilityEvaluationRun.generated_at < exclusive_cutoff_at,
            CapabilityEvaluationRun.cutoff_at < exclusive_cutoff_at,
        )
        .order_by(
            CapabilityEvaluationRun.cutoff_at.desc(),
            CapabilityEvaluationRun.generated_at.desc(),
            CapabilityEvaluationRun.id.desc(),
        )
        .limit(1)
    )
    if run is None:
        return None
    level = db.get(CapabilityScaleLevel, run.selected_level_id) if run.selected_level_id else None
    return CapabilityPublicDTO(
        semantic_definition_id=run.semantic_definition_id,
        scale_version_id=run.scale_version_id,
        dimension_id=run.dimension_id,
        assessment_status=run.assessment_status,
        selected_level_id=run.selected_level_id,
        selected_level_ordinal=level.ordinal_rank if level is not None else None,
        aggregate_confidence=run.aggregate_confidence,
        evaluation_run_id=run.id,
        evaluated_cutoff_at=run.cutoff_at,
        recorded_at=run.generated_at,
    )


def criterion_evaluation_as_of(
    db: Session, *, criterion_definition_id: str, exclusive_cutoff_at: int
) -> CriterionEvaluationPublicDTO | None:
    """Return the latest immutable criterion result known strictly before a cutoff."""
    row = db.execute(
        select(CriterionEvaluationResult, CapabilityEvaluationRun)
        .join(
            CapabilityEvaluationRun, CapabilityEvaluationRun.id == CriterionEvaluationResult.run_id
        )
        .where(
            CriterionEvaluationResult.criterion_definition_id == criterion_definition_id,
            CapabilityEvaluationRun.generated_at < exclusive_cutoff_at,
            CapabilityEvaluationRun.cutoff_at < exclusive_cutoff_at,
        )
        .order_by(
            CapabilityEvaluationRun.cutoff_at.desc(),
            CapabilityEvaluationRun.generated_at.desc(),
            CapabilityEvaluationRun.id.desc(),
        )
        .limit(1)
    ).first()
    if row is None:
        return None
    result, run = row
    return CriterionEvaluationPublicDTO(
        criterion_definition_id=result.criterion_definition_id,
        state=result.state,
        evaluation_run_id=run.id,
        evaluated_cutoff_at=run.cutoff_at,
        recorded_at=run.generated_at,
    )
