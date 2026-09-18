from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.determinism import content_hash
from app.models import (
    CapabilityEvaluationRun,
    CapabilityScaleDimension,
    CapabilityScaleLevel,
    CapabilityScaleVersion,
    CapabilityStateEvent,
    CriterionDefinition,
    CriterionEvaluationResult,
    Evidence,
    EvidenceInvalidation,
    EvidenceLink,
    EvidenceLinkRetraction,
    EvidenceRetraction,
    ReviewEvent,
    SemanticCompetencyDefinition,
)


@dataclass(frozen=True)
class CapabilityPublicDTO:
    semantic_definition_id: str
    scale_version_id: str
    dimension_id: str | None
    assessment_status: str
    selected_level_id: str | None
    selected_level_ordinal: int | None
    aggregate_confidence: str
    evaluation_run_id: str | None
    evaluated_cutoff_at: int
    recorded_at: int
    capability_policy_version: str
    criterion_policy_version: str
    evidence_policy_version: str
    downgrade_policy_version: str
    input_hash: str


@dataclass(frozen=True)
class CriterionEvaluationPublicDTO:
    criterion_definition_id: str
    state: str
    evaluation_run_id: str
    evaluated_cutoff_at: int
    recorded_at: int
    evidence_set_hash: str
    decisive_evidence_ids: tuple[str, ...]
    facts: dict[str, object]
    criterion_policy_version: str
    evidence_policy_version: str


@dataclass(frozen=True)
class ReviewPublicDTO:
    semantic_definition_id: str
    scope_key: str
    dimension_id: str | None
    freshness: str
    review_due: bool
    review_event_id: str | None
    evaluation_run_id: str | None
    recorded_at: int
    freshness_policy_version: str


@dataclass(frozen=True)
class CapabilityScopeProjectionPublicDTO:
    scope_key: str
    evaluation_run_id: str
    evaluation_output_hash: str
    assessment_status: str
    selected_level_id: str | None
    aggregate_confidence: str
    freshness: str
    review_due: bool | None
    review_event_id: str | None


@dataclass(frozen=True)
class CriterionStateProjectionPublicDTO:
    criterion_definition_id: str
    criterion_identity_id: str
    requirement_type: str
    level_id: str
    state: str
    evaluation_run_id: str | None
    recorded_at: int | None
    evidence_set_hash: str | None
    decisive_evidence_ids: tuple[str, ...]
    evaluation_facts: dict[str, object]
    demonstration_rule: str
    criterion_policy_version: str
    evidence_policy_version: str


@dataclass(frozen=True)
class CapabilityEvaluationAsOfPublicDTO:
    capability: CapabilityPublicDTO
    criteria: tuple[CriterionStateProjectionPublicDTO, ...]
    review: ReviewPublicDTO
    input_payload: dict[str, object]
    input_hash: str


def capability_policy_bundle() -> dict[str, str]:
    """Expose the exact public policy identities used by pure capability evaluation."""
    from app.capability import (
        CAPABILITY_POLICY,
        CRITERION_POLICY,
        DOWNGRADE_POLICY,
        EVIDENCE_POLICY,
        FRESHNESS_POLICY,
    )

    return {
        "capabilityEvaluation": CAPABILITY_POLICY,
        "criterionEvaluation": CRITERION_POLICY,
        "evidenceQualification": EVIDENCE_POLICY,
        "capabilityDowngrade": DOWNGRADE_POLICY,
        "freshness": FRESHNESS_POLICY,
    }


def evidence_independence_satisfies_rules(
    independence: str, demonstration_rules: tuple[str, ...]
) -> bool:
    """Apply the strictest public performance-independence rule in a target scope."""
    if any(
        rule
        in {
            "independent_performance",
            "repeated_independent_performance",
            "authoritative_assessment",
        }
        for rule in demonstration_rules
    ):
        return independence == "independent"
    if "guided_performance" in demonstration_rules:
        return independence in {"guided", "assisted", "independent"}
    return independence in {"guided", "assisted", "independent", "not_applicable"}


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
        capability_policy_version=run.capability_policy_version,
        criterion_policy_version=run.criterion_policy_version,
        evidence_policy_version=run.evidence_policy_version,
        downgrade_policy_version=run.downgrade_policy_version,
        input_hash=run.input_hash,
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
        evidence_set_hash=result.evidence_set_hash,
        decisive_evidence_ids=tuple(json.loads(result.decisive_evidence_ids_json)),
        facts=json.loads(result.facts_json),
        criterion_policy_version=run.criterion_policy_version,
        evidence_policy_version=run.evidence_policy_version,
    )


def review_as_of(
    db: Session,
    *,
    semantic_definition_id: str,
    scope_key: str,
    dimension_id: str | None,
    exclusive_cutoff_at: int,
) -> ReviewPublicDTO | None:
    row = db.scalar(
        select(ReviewEvent)
        .where(
            ReviewEvent.semantic_definition_id == semantic_definition_id,
            ReviewEvent.scope_key == scope_key,
            ReviewEvent.dimension_id == dimension_id,
            ReviewEvent.created_at < exclusive_cutoff_at,
        )
        .order_by(ReviewEvent.created_at.desc(), ReviewEvent.event_sequence.desc())
        .limit(1)
    )
    if row is None:
        return None
    return ReviewPublicDTO(
        semantic_definition_id=row.semantic_definition_id,
        scope_key=row.scope_key,
        dimension_id=row.dimension_id,
        freshness=row.new_freshness,
        review_due=row.new_review_due,
        review_event_id=row.id,
        evaluation_run_id=row.evaluation_run_id,
        recorded_at=row.created_at,
        freshness_policy_version=row.freshness_policy_version,
    )


def capability_projection_as_of(
    db: Session, *, semantic_definition_id: str, exclusive_cutoff_at: int
) -> tuple[CapabilityScopeProjectionPublicDTO, ...]:
    """Return the latest immutable capability and review facts per scope at a cutoff."""
    runs = db.scalars(
        select(CapabilityEvaluationRun)
        .where(
            CapabilityEvaluationRun.semantic_definition_id == semantic_definition_id,
            CapabilityEvaluationRun.generated_at < exclusive_cutoff_at,
            CapabilityEvaluationRun.cutoff_at < exclusive_cutoff_at,
        )
        .order_by(
            CapabilityEvaluationRun.scope_key,
            CapabilityEvaluationRun.cutoff_at.desc(),
            CapabilityEvaluationRun.generated_at.desc(),
            CapabilityEvaluationRun.id.desc(),
        )
    ).all()
    latest_runs: dict[str, CapabilityEvaluationRun] = {}
    for run in runs:
        latest_runs.setdefault(run.scope_key, run)
    result: list[CapabilityScopeProjectionPublicDTO] = []
    for run in sorted(latest_runs.values(), key=lambda item: item.scope_key):
        review = review_as_of(
            db,
            semantic_definition_id=run.semantic_definition_id,
            scope_key=run.scope_key,
            dimension_id=run.dimension_id,
            exclusive_cutoff_at=exclusive_cutoff_at,
        )
        result.append(
            CapabilityScopeProjectionPublicDTO(
                scope_key=run.scope_key,
                evaluation_run_id=run.id,
                evaluation_output_hash=run.output_hash,
                assessment_status=run.assessment_status,
                selected_level_id=run.selected_level_id,
                aggregate_confidence=run.aggregate_confidence,
                freshness=review.freshness if review else "unknown",
                review_due=review.review_due if review else None,
                review_event_id=review.review_event_id if review else None,
            )
        )
    return tuple(result)


def criterion_states_for_semantic_as_of(
    db: Session, *, semantic_definition_id: str, exclusive_cutoff_at: int
) -> tuple[CriterionStateProjectionPublicDTO, ...]:
    result: list[CriterionStateProjectionPublicDTO] = []
    for definition in db.scalars(
        select(CriterionDefinition)
        .where(CriterionDefinition.semantic_definition_id == semantic_definition_id)
        .order_by(CriterionDefinition.requirement_type, CriterionDefinition.id)
    ).all():
        evaluation = criterion_evaluation_as_of(
            db,
            criterion_definition_id=definition.id,
            exclusive_cutoff_at=exclusive_cutoff_at,
        )
        result.append(
            CriterionStateProjectionPublicDTO(
                definition.id,
                definition.criterion_identity_id,
                definition.requirement_type,
                definition.level_id,
                evaluation.state if evaluation else "unknown",
                evaluation.evaluation_run_id if evaluation else None,
                evaluation.recorded_at if evaluation else None,
                evaluation.evidence_set_hash if evaluation else None,
                evaluation.decisive_evidence_ids if evaluation else (),
                evaluation.facts if evaluation else {},
                str(json.loads(definition.demonstration_rule_json)["rule"]),
                evaluation.criterion_policy_version
                if evaluation
                else "criterion-evaluation-policy/v1",
                evaluation.evidence_policy_version if evaluation else "evidence-policy/v1",
            )
        )
    return tuple(result)


def evaluate_capability_as_of(
    db: Session,
    *,
    competency_identity_id: str,
    semantic_definition_id: str,
    dimension_id: str | None,
    exclusive_cutoff_at: int,
    timezone_name: str,
) -> CapabilityEvaluationAsOfPublicDTO:
    """Pure cutoff reconstruction from immutable Evidence; never writes projections/history."""
    from app.capability import (
        CAPABILITY_POLICY,
        CRITERION_POLICY,
        DOWNGRADE_POLICY,
        EVIDENCE_POLICY,
        FRESHNESS_POLICY,
        _active_evidence_facts,
        _apply_downgrade_policy,
        _candidate_level,
        _evaluate_criterion,
        _freshness_thresholds,
        _high_confidence,
        _meaningful_evidence_at,
        _prior_state_at,
        _scope_key,
    )

    cutoff = exclusive_cutoff_at - 1
    definition = db.get(SemanticCompetencyDefinition, semantic_definition_id)
    if definition is None:
        raise ValueError("The semantic definition does not exist at the Analysis cutoff.")
    scale = db.get(CapabilityScaleVersion, definition.scale_version_id)
    if scale is None:
        raise ValueError("The capability scale does not exist at the Analysis cutoff.")
    dimension = db.get(CapabilityScaleDimension, dimension_id) if dimension_id else None
    levels = list(
        db.scalars(
            select(CapabilityScaleLevel)
            .where(CapabilityScaleLevel.scale_version_id == scale.id)
            .order_by(CapabilityScaleLevel.ordinal_rank, CapabilityScaleLevel.id)
        ).all()
    )
    definitions = list(
        db.scalars(
            select(CriterionDefinition)
            .where(
                CriterionDefinition.semantic_definition_id == semantic_definition_id,
                CriterionDefinition.dimension_id == dimension_id,
                CriterionDefinition.created_at < exclusive_cutoff_at,
            )
            .order_by(CriterionDefinition.id)
        ).all()
    )
    all_facts = _active_evidence_facts(db, competency_identity_id, cutoff)
    scope_facts = [item for item in all_facts if item.link.dimension_id == dimension_id]
    results = [_evaluate_criterion(item, scope_facts) for item in definitions]
    candidate, passed_level_ids = _candidate_level(scale, levels, results, scope_facts)
    scope_key = _scope_key(dimension_id)
    previous = _prior_state_at(db, competency_identity_id, scope_key, cutoff)
    if previous is not None and previous.semantic_definition_id != semantic_definition_id:
        previous = None
    selected, downgrade_cause, downgrade_evidence, retained = _apply_downgrade_policy(
        db, previous, candidate, levels, results, scope_facts, cutoff
    )
    cumulative = (
        [
            item
            for item in results
            if next(level for level in levels if level.id == item.definition.level_id).ordinal_rank
            <= selected.ordinal_rank
        ]
        if selected
        else []
    )
    high, _unmet_high = (
        _high_confidence(cumulative, scope_facts, selected.id) if selected else (False, [])
    )
    contradictions = sorted(
        {
            evidence_id
            for item in results
            if item.definition.requirement_type in {"required", "important"}
            for evidence_id in item.contradiction_ids
        }
    )
    confidence = (
        "unknown"
        if selected is None
        else "low"
        if retained or contradictions
        else "high"
        if high
        else "medium"
    )
    decisive_ids = tuple(
        sorted(
            {evidence_id for item in cumulative for evidence_id in item.decisive_ids}
            | set(downgrade_evidence)
        )
    )
    criteria = tuple(
        CriterionStateProjectionPublicDTO(
            item.definition.id,
            item.definition.criterion_identity_id,
            item.definition.requirement_type,
            item.definition.level_id,
            item.state,
            None,
            cutoff,
            content_hash(sorted(fact.evidence.id for fact in scope_facts)),
            item.decisive_ids,
            item.facts,
            str(json.loads(item.definition.demonstration_rule_json)["rule"]),
            CRITERION_POLICY,
            EVIDENCE_POLICY,
        )
        for item in results
    )
    meaningful_at = _meaningful_evidence_at(
        scope_facts, {item.id: item for item in definitions}, selected
    )
    current_days, stale_days = _freshness_thresholds(
        scale.scale_stable_key,
        selected.stable_key if selected else None,
        dimension.stable_key if dimension else None,
    )
    if definition.freshness_current_through_days is not None:
        current_days = definition.freshness_current_through_days
        stale_days = definition.freshness_stale_after_days
    if selected and scale.scale_stable_key == "technical" and selected.stable_key == "unexposed":
        freshness = "unknown"
    elif meaningful_at is None:
        freshness = "unknown"
    else:
        timezone = ZoneInfo(timezone_name)
        evidence_date = (
            datetime.fromtimestamp(meaningful_at / 1000, UTC).astimezone(timezone).date()
        )
        cutoff_date = datetime.fromtimestamp(cutoff / 1000, UTC).astimezone(timezone).date()
        elapsed = (cutoff_date - evidence_date).days
        freshness = (
            "stale"
            if stale_days is not None and elapsed > stale_days
            else "aging"
            if current_days is not None and elapsed > current_days
            else "current"
        )
    review_due = freshness == "stale" or bool(contradictions) or confidence == "low"
    review = ReviewPublicDTO(
        semantic_definition_id,
        scope_key,
        dimension_id,
        freshness,
        review_due,
        None,
        None,
        cutoff,
        FRESHNESS_POLICY,
    )

    def row_payload(row: object) -> dict[str, object]:
        table = cast(Any, row).__table__
        return {column.name: getattr(row, column.name) for column in table.columns}

    evidence_history = list(
        db.execute(
            select(Evidence, EvidenceLink)
            .join(EvidenceLink, EvidenceLink.evidence_id == Evidence.id)
            .where(
                EvidenceLink.competency_identity_id == competency_identity_id,
                Evidence.created_at < exclusive_cutoff_at,
                EvidenceLink.created_at < exclusive_cutoff_at,
            )
            .order_by(Evidence.created_at, Evidence.id, EvidenceLink.id)
        ).all()
    )
    evidence_ids = sorted({evidence.id for evidence, _link in evidence_history})
    link_ids = sorted({link.id for _evidence, link in evidence_history})
    state_event_history = list(
        db.scalars(
            select(CapabilityStateEvent)
            .where(
                CapabilityStateEvent.competency_identity_id == competency_identity_id,
                CapabilityStateEvent.scope_key == scope_key,
                CapabilityStateEvent.created_at < exclusive_cutoff_at,
            )
            .order_by(CapabilityStateEvent.event_sequence, CapabilityStateEvent.id)
        ).all()
    )
    historical_run_ids = sorted({item.evaluation_run_id for item in state_event_history})
    evaluation_run_history = list(
        db.scalars(
            select(CapabilityEvaluationRun)
            .where(CapabilityEvaluationRun.id.in_(historical_run_ids))
            .order_by(
                CapabilityEvaluationRun.cutoff_at,
                CapabilityEvaluationRun.generated_at,
                CapabilityEvaluationRun.id,
            )
        ).all()
    )
    criterion_result_history = list(
        db.scalars(
            select(CriterionEvaluationResult)
            .where(CriterionEvaluationResult.run_id.in_(historical_run_ids))
            .order_by(
                CriterionEvaluationResult.run_id,
                CriterionEvaluationResult.criterion_definition_id,
                CriterionEvaluationResult.id,
            )
        ).all()
    )

    input_payload: dict[str, object] = {
        "competencyIdentityId": competency_identity_id,
        "semanticDefinition": row_payload(definition),
        "scaleVersion": row_payload(scale),
        "dimension": row_payload(dimension) if dimension is not None else None,
        "levels": [row_payload(item) for item in levels],
        "criterionDefinitions": [row_payload(item) for item in definitions],
        "exclusiveCutoffAt": exclusive_cutoff_at,
        "timezone": timezone_name,
        "activeEvidence": [
            {
                "evidence": row_payload(item.evidence),
                "link": row_payload(item.link),
                "occurrenceKey": item.occurrence_key,
                "contextKey": item.context_key,
            }
            for item in scope_facts
        ],
        "evidenceHistory": [
            {"evidence": row_payload(evidence), "link": row_payload(link)}
            for evidence, link in evidence_history
        ],
        "evidenceCorrectionHistory": {
            "retractions": [
                row_payload(item)
                for item in db.scalars(
                    select(EvidenceRetraction)
                    .where(
                        EvidenceRetraction.evidence_id.in_(evidence_ids),
                        EvidenceRetraction.created_at < exclusive_cutoff_at,
                    )
                    .order_by(EvidenceRetraction.created_at, EvidenceRetraction.id)
                ).all()
            ],
            "invalidations": [
                row_payload(item)
                for item in db.scalars(
                    select(EvidenceInvalidation)
                    .where(
                        EvidenceInvalidation.evidence_id.in_(evidence_ids),
                        EvidenceInvalidation.created_at < exclusive_cutoff_at,
                    )
                    .order_by(EvidenceInvalidation.created_at, EvidenceInvalidation.id)
                ).all()
            ],
            "linkRetractions": [
                row_payload(item)
                for item in db.scalars(
                    select(EvidenceLinkRetraction)
                    .where(
                        EvidenceLinkRetraction.evidence_link_id.in_(link_ids),
                        EvidenceLinkRetraction.created_at < exclusive_cutoff_at,
                    )
                    .order_by(EvidenceLinkRetraction.created_at, EvidenceLinkRetraction.id)
                ).all()
            ],
        },
        "criterionResults": [
            {
                "criterionDefinitionId": item.definition.id,
                "state": item.state,
                "decisiveEvidenceIds": item.decisive_ids,
                "supportEvidenceIds": item.support_ids,
                "contradictionEvidenceIds": item.contradiction_ids,
                "facts": item.facts,
            }
            for item in results
        ],
        "priorState": asdict(previous) if previous is not None else None,
        "priorEvaluationRun": (
            row_payload(db.get(CapabilityEvaluationRun, previous.evaluation_run_id))
            if previous is not None
            and db.get(CapabilityEvaluationRun, previous.evaluation_run_id) is not None
            else None
        ),
        "priorStateEvents": (
            [
                row_payload(item)
                for item in db.scalars(
                    select(CapabilityStateEvent)
                    .where(
                        CapabilityStateEvent.evaluation_run_id == previous.evaluation_run_id,
                        CapabilityStateEvent.created_at < exclusive_cutoff_at,
                    )
                    .order_by(CapabilityStateEvent.event_sequence, CapabilityStateEvent.id)
                ).all()
            ]
            if previous is not None
            else []
        ),
        "capabilityStateEventHistory": [row_payload(item) for item in state_event_history],
        "capabilityEvaluationRunHistory": [row_payload(item) for item in evaluation_run_history],
        "criterionEvaluationResultHistory": [
            row_payload(item) for item in criterion_result_history
        ],
        "candidateLevelId": candidate.id if candidate is not None else None,
        "selectedLevelId": selected.id if selected is not None else None,
        "passedLevelIds": passed_level_ids,
        "downgradeCause": downgrade_cause,
        "downgradeEvidenceIds": downgrade_evidence,
        "retainedPriorState": retained,
        "aggregateConfidence": confidence,
        "decisiveEvidenceIds": decisive_ids,
        "meaningfulEvidenceAt": meaningful_at,
        "freshnessThresholds": {
            "currentThroughDays": current_days,
            "staleAfterDays": stale_days,
        },
        "freshness": freshness,
        "reviewDue": review_due,
        "policies": capability_policy_bundle(),
    }
    input_hash = content_hash(input_payload)
    capability = CapabilityPublicDTO(
        semantic_definition_id,
        scale.id,
        dimension_id,
        "evaluated" if selected else "unknown",
        selected.id if selected else None,
        selected.ordinal_rank if selected else None,
        confidence,
        None,
        cutoff,
        cutoff,
        CAPABILITY_POLICY,
        CRITERION_POLICY,
        EVIDENCE_POLICY,
        DOWNGRADE_POLICY,
        input_hash,
    )
    return CapabilityEvaluationAsOfPublicDTO(
        capability, criteria, review, input_payload, input_hash
    )
