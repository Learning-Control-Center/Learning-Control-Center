from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.activity_views import activity_actuality_as_of
from app.analysis.contracts import content_hash
from app.capability_views import (
    capability_policy_bundle,
    evaluate_capability_as_of,
    evidence_independence_satisfies_rules,
)
from app.curriculum.models import CurriculumActivationEvent
from app.learning_graph.models import LearningGraphActivationEvent
from app.models import (
    Activity,
    CapabilityEvaluationRun,
    CapabilityScaleDimension,
    CapabilityScaleLevel,
    CapabilityScaleVersion,
    CapabilityStateEvent,
    CompetencyDefinitionActivationEvent,
    ContributionRetraction,
    CriterionDefinition,
    DisciplineConfigurationEvent,
    Evidence,
    EvidenceInvalidation,
    EvidenceLink,
    EvidenceLinkRetraction,
    EvidenceRetraction,
    LearningSession,
    ReviewEvent,
    SemanticCompetencyDefinition,
    SessionContribution,
    SessionCorrection,
    TargetProfileActivationEvent,
)
from app.profile_views import (
    ActiveProfileProjectionPublicDTO,
    ProfileTargetProjectionPublicDTO,
    active_semantic_definition_ids_as_of,
)
from app.projects.models import (
    ProjectCriterionDefinition,
    ProjectCriterionEvaluation,
    ProjectCriterionIdentity,
    ProjectEvent,
    ProjectVersionActivationEvent,
)
from app.projects.service import (
    active_project_versions_as_of,
    project_criterion_state_as_of,
)
from app.session_views import session_actuality_as_of


@dataclass(frozen=True)
class DisciplineConfigurationPublicDTO:
    event_id: str
    event_sequence: int
    recorded_at: int
    configuration: dict[str, object]
    configuration_hash: str


@dataclass(frozen=True)
class ActualSessionSummaryPublicDTO:
    session_id: str
    activity_id: str
    primary_competency_identity_id: str | None
    competency_identity_ids: tuple[str, ...]
    local_date: str
    duration_ms: int
    outcome: str
    assistance_mode: str
    started_at: int
    ended_at: int
    recorded_at: int
    produced_active_evidence: bool
    active_evidence_competency_ids: tuple[str, ...]
    active_evidence_qualifications: tuple[EvidenceQualificationPublicDTO, ...]


@dataclass(frozen=True)
class EvidenceQualificationPublicDTO:
    evidence_id: str
    evidence_link_id: str
    competency_identity_id: str
    dimension_id: str | None
    criterion_definition_id: str | None
    strength: str
    independence: str
    source_confidence: str
    policy_version: str


@dataclass(frozen=True)
class ActualContributionAttributionPublicDTO:
    session_id: str
    competency_identity_id: str
    dimension_id: str | None
    criterion_definition_id: str
    relevance: str


@dataclass(frozen=True)
class EvidenceCoveragePublicDTO:
    competency_identity_id: str
    evidence_ids: tuple[str, ...]
    supporting_ids: tuple[str, ...]
    contradicting_ids: tuple[str, ...]
    independent_ids: tuple[str, ...]
    qualifying_readiness: tuple[EvidenceQualificationPublicDTO, ...]
    last_meaningful_at: int | None
    input_hash: str


@dataclass(frozen=True)
class ReadinessPredicateEvaluationPublicDTO:
    predicate_id: str
    predicate_type: str
    requirement_type: str
    state: str
    reason_code: str
    subject: dict[str, object]


@dataclass(frozen=True)
class ReadinessGateEvaluationPublicDTO:
    gate_id: str
    stable_key: str
    effect: str
    target_ids: tuple[str, ...]
    state: str
    predicates: tuple[ReadinessPredicateEvaluationPublicDTO, ...]


READINESS_EVIDENCE_POLICY = capability_policy_bundle()["evidenceQualification"]


def _qualifies_readiness_evidence(evidence: Evidence, link: EvidenceLink) -> bool:
    return (
        link.effect == "supports"
        and evidence.strength in {"moderate", "strong"}
        and evidence.source_confidence in {"medium", "high"}
        and evidence.independence in {"guided", "assisted", "independent"}
    )


def _target_demonstration_rules(
    db: Session,
    *,
    target: ProfileTargetProjectionPublicDTO,
    active_semantics: dict[str, str],
) -> tuple[str, ...]:
    semantic_id = active_semantics.get(target.competency_identity_id)
    if semantic_id is None:
        return ()
    rows = db.scalars(
        select(CriterionDefinition)
        .join(CapabilityScaleLevel, CapabilityScaleLevel.id == CriterionDefinition.level_id)
        .where(
            CriterionDefinition.semantic_definition_id == semantic_id,
            CriterionDefinition.requirement_type.in_(["required", "important"]),
            CapabilityScaleLevel.scale_version_id == target.scale_version_id,
            CapabilityScaleLevel.ordinal_rank <= target.target_level_ordinal,
        )
        .order_by(CapabilityScaleLevel.ordinal_rank, CriterionDefinition.id)
    ).all()
    return tuple(
        str(json.loads(item.demonstration_rule_json)["rule"])
        for item in rows
        if item.dimension_id == target.dimension_id
    )


def _target_criterion_rules(
    db: Session,
    *,
    target: ProfileTargetProjectionPublicDTO,
    active_semantics: dict[str, str],
) -> dict[str, str]:
    semantic_id = active_semantics.get(target.competency_identity_id)
    if semantic_id is None:
        return {}
    rows = db.scalars(
        select(CriterionDefinition)
        .join(CapabilityScaleLevel, CapabilityScaleLevel.id == CriterionDefinition.level_id)
        .where(
            CriterionDefinition.semantic_definition_id == semantic_id,
            CriterionDefinition.requirement_type.in_(["required", "important"]),
            CriterionDefinition.dimension_id == target.dimension_id,
            CapabilityScaleLevel.scale_version_id == target.scale_version_id,
            CapabilityScaleLevel.ordinal_rank <= target.target_level_ordinal,
        )
        .order_by(CapabilityScaleLevel.ordinal_rank, CriterionDefinition.id)
    ).all()
    return {item.id: str(json.loads(item.demonstration_rule_json)["rule"]) for item in rows}


def evidence_qualification_matches_target(
    db: Session,
    *,
    qualification: EvidenceQualificationPublicDTO,
    target: ProfileTargetProjectionPublicDTO,
    active_semantics: dict[str, str],
) -> bool:
    """Check exact target scope and demonstration-rule-compatible independence."""
    rules: tuple[str, ...]
    if qualification.competency_identity_id != target.competency_identity_id:
        return False
    if qualification.dimension_id != target.dimension_id:
        return False
    criterion_rules = _target_criterion_rules(db, target=target, active_semantics=active_semantics)
    if qualification.criterion_definition_id:
        rule = criterion_rules.get(qualification.criterion_definition_id)
        rules = (rule,) if rule is not None else ()
    else:
        rules = _target_demonstration_rules(db, target=target, active_semantics=active_semantics)
    return bool(rules) and evidence_independence_satisfies_rules(qualification.independence, rules)


def analysis_source_generation(db: Session) -> int:
    """Return a portable monotonic generation over Analysis-visible append-only sources."""
    source_models = (
        TargetProfileActivationEvent,
        CompetencyDefinitionActivationEvent,
        LearningGraphActivationEvent,
        CurriculumActivationEvent,
        ProjectVersionActivationEvent,
        ProjectEvent,
        ProjectCriterionEvaluation,
        CapabilityEvaluationRun,
        CapabilityStateEvent,
        ReviewEvent,
        Activity,
        LearningSession,
        SessionContribution,
        SessionCorrection,
        ContributionRetraction,
        Evidence,
        EvidenceLink,
        EvidenceRetraction,
        EvidenceInvalidation,
        EvidenceLinkRetraction,
        DisciplineConfigurationEvent,
    )
    return sum(
        int(db.scalar(select(func.count()).select_from(model)) or 0) for model in source_models
    )


def discipline_configuration_as_of(
    db: Session, *, exclusive_cutoff_at: int
) -> DisciplineConfigurationPublicDTO | None:
    row = db.scalar(
        select(DisciplineConfigurationEvent)
        .where(DisciplineConfigurationEvent.recorded_at < exclusive_cutoff_at)
        .order_by(
            DisciplineConfigurationEvent.recorded_at.desc(),
            DisciplineConfigurationEvent.event_sequence.desc(),
        )
        .limit(1)
    )
    if row is None:
        return None
    return DisciplineConfigurationPublicDTO(
        row.id,
        row.event_sequence,
        row.recorded_at,
        json.loads(row.configuration_json),
        row.configuration_hash,
    )


def actual_session_summaries_as_of(
    db: Session, *, exclusive_cutoff_at: int, timezone_name: str
) -> tuple[ActualSessionSummaryPublicDTO, ...]:
    from app.time_utils import local_date_for_ms

    result: list[ActualSessionSummaryPublicDTO] = []
    for session_id in db.scalars(
        select(LearningSession.id)
        .where(LearningSession.created_at < exclusive_cutoff_at)
        .order_by(LearningSession.started_at, LearningSession.id)
    ).all():
        fact = session_actuality_as_of(
            db, session_id=session_id, exclusive_cutoff_at=exclusive_cutoff_at
        )
        if fact is None:
            continue
        if (
            activity_actuality_as_of(
                db, activity_id=fact.activity_id, exclusive_cutoff_at=exclusive_cutoff_at
            )
            is None
        ):
            continue
        contributions = db.scalars(
            select(SessionContribution)
            .where(
                SessionContribution.session_id == session_id,
                SessionContribution.created_at < exclusive_cutoff_at,
            )
            .order_by(SessionContribution.created_at, SessionContribution.id)
        ).all()
        active: list[SessionContribution] = []
        for contribution in contributions:
            retracted = db.scalar(
                select(ContributionRetraction.id).where(
                    ContributionRetraction.contribution_id == contribution.id,
                    ContributionRetraction.retracted_at < exclusive_cutoff_at,
                )
            )
            if retracted is None:
                active.append(contribution)
        primary = next((item for item in active if item.relevance == "primary"), None)
        source_evidence = db.scalars(
            select(Evidence).where(
                Evidence.source_type == "learning_session",
                Evidence.source_id == session_id,
                Evidence.created_at < exclusive_cutoff_at,
                Evidence.occurred_at < exclusive_cutoff_at,
            )
        ).all()
        active_evidence_ids = {
            item.id
            for item in source_evidence
            if db.scalar(
                select(EvidenceRetraction.id).where(
                    EvidenceRetraction.evidence_id == item.id,
                    EvidenceRetraction.created_at < exclusive_cutoff_at,
                )
            )
            is None
            and db.scalar(
                select(EvidenceInvalidation.id).where(
                    EvidenceInvalidation.evidence_id == item.id,
                    EvidenceInvalidation.created_at < exclusive_cutoff_at,
                )
            )
            is None
        }
        qualifications: list[EvidenceQualificationPublicDTO] = []
        if active_evidence_ids:
            evidence_by_id = {item.id: item for item in source_evidence}
            for link in db.scalars(
                select(EvidenceLink).where(
                    EvidenceLink.evidence_id.in_(active_evidence_ids),
                    EvidenceLink.created_at < exclusive_cutoff_at,
                )
            ).all():
                link_retracted = db.scalar(
                    select(EvidenceLinkRetraction.id).where(
                        EvidenceLinkRetraction.evidence_link_id == link.id,
                        EvidenceLinkRetraction.created_at < exclusive_cutoff_at,
                    )
                )
                if link_retracted is None and _qualifies_readiness_evidence(
                    evidence_by_id[link.evidence_id], link
                ):
                    evidence = evidence_by_id[link.evidence_id]
                    qualifications.append(
                        EvidenceQualificationPublicDTO(
                            evidence.id,
                            link.id,
                            link.competency_identity_id,
                            link.dimension_id,
                            link.criterion_definition_id,
                            evidence.strength,
                            evidence.independence,
                            evidence.source_confidence,
                            READINESS_EVIDENCE_POLICY,
                        )
                    )
        ordered_qualifications = tuple(
            sorted(qualifications, key=lambda item: (item.evidence_id, item.evidence_link_id))
        )
        active_evidence_competency_ids = tuple(
            sorted({item.competency_identity_id for item in ordered_qualifications})
        )
        result.append(
            ActualSessionSummaryPublicDTO(
                session_id=fact.session_id,
                activity_id=fact.activity_id,
                primary_competency_identity_id=(
                    primary.competency_identity_id if primary else None
                ),
                competency_identity_ids=tuple(
                    sorted({item.competency_identity_id for item in active})
                ),
                local_date=local_date_for_ms(fact.started_at, timezone_name).isoformat(),
                duration_ms=fact.duration_ms,
                outcome=fact.outcome,
                assistance_mode=fact.assistance_mode,
                started_at=fact.started_at,
                ended_at=fact.ended_at,
                recorded_at=fact.recorded_at,
                produced_active_evidence=bool(active_evidence_ids),
                active_evidence_competency_ids=active_evidence_competency_ids,
                active_evidence_qualifications=ordered_qualifications,
            )
        )
    return tuple(result)


def actual_contribution_attributions_as_of(
    db: Session, *, exclusive_cutoff_at: int
) -> tuple[ActualContributionAttributionPublicDTO, ...]:
    """Resolve cutoff-visible SessionContribution criteria to exact active definitions."""
    active_semantics = active_semantic_definition_ids_as_of(
        db, exclusive_cutoff_at=exclusive_cutoff_at
    )
    result: list[ActualContributionAttributionPublicDTO] = []
    contributions = db.scalars(
        select(SessionContribution)
        .where(
            SessionContribution.criterion_identity_id.is_not(None),
            SessionContribution.created_at < exclusive_cutoff_at,
        )
        .order_by(SessionContribution.created_at, SessionContribution.id)
    ).all()
    for contribution in contributions:
        if db.scalar(
            select(ContributionRetraction.id).where(
                ContributionRetraction.contribution_id == contribution.id,
                ContributionRetraction.retracted_at < exclusive_cutoff_at,
            )
        ) is not None:
            continue
        semantic_definition_id = active_semantics.get(contribution.competency_identity_id)
        if semantic_definition_id is None or contribution.criterion_identity_id is None:
            continue
        definition = db.scalar(
            select(CriterionDefinition)
            .where(
                CriterionDefinition.criterion_identity_id
                == contribution.criterion_identity_id,
                CriterionDefinition.semantic_definition_id == semantic_definition_id,
                CriterionDefinition.created_at < exclusive_cutoff_at,
            )
            .order_by(CriterionDefinition.created_at.desc(), CriterionDefinition.id.desc())
            .limit(1)
        )
        if definition is None:
            continue
        result.append(
            ActualContributionAttributionPublicDTO(
                contribution.session_id,
                contribution.competency_identity_id,
                definition.dimension_id,
                definition.id,
                contribution.relevance,
            )
        )
    return tuple(result)


def evidence_coverage_as_of(
    db: Session, *, exclusive_cutoff_at: int
) -> tuple[EvidenceCoveragePublicDTO, ...]:
    grouped: dict[str, list[tuple[Evidence, EvidenceLink]]] = {}
    rows = db.execute(
        select(Evidence, EvidenceLink)
        .join(EvidenceLink, EvidenceLink.evidence_id == Evidence.id)
        .where(
            Evidence.created_at < exclusive_cutoff_at,
            EvidenceLink.created_at < exclusive_cutoff_at,
        )
        .order_by(EvidenceLink.competency_identity_id, Evidence.created_at, Evidence.id)
    ).all()
    for evidence, link in rows:
        inactive = any(
            db.scalar(query) is not None
            for query in (
                select(EvidenceRetraction.id).where(
                    EvidenceRetraction.evidence_id == evidence.id,
                    EvidenceRetraction.created_at < exclusive_cutoff_at,
                ),
                select(EvidenceInvalidation.id).where(
                    EvidenceInvalidation.evidence_id == evidence.id,
                    EvidenceInvalidation.created_at < exclusive_cutoff_at,
                ),
                select(EvidenceLinkRetraction.id).where(
                    EvidenceLinkRetraction.evidence_link_id == link.id,
                    EvidenceLinkRetraction.created_at < exclusive_cutoff_at,
                ),
            )
        )
        occurred_at = evidence.occurred_at
        if inactive or occurred_at is None or occurred_at >= exclusive_cutoff_at:
            continue
        grouped.setdefault(link.competency_identity_id, []).append((evidence, link))
    result: list[EvidenceCoveragePublicDTO] = []
    for competency_id, items in sorted(grouped.items()):
        evidence_ids = tuple(sorted({evidence.id for evidence, _ in items}))
        supporting = tuple(
            sorted({evidence.id for evidence, link in items if link.effect == "supports"})
        )
        contradicting = tuple(
            sorted({evidence.id for evidence, link in items if link.effect == "contradicts"})
        )
        independent = tuple(
            sorted({evidence.id for evidence, _ in items if evidence.independence == "independent"})
        )
        qualifying = tuple(
            EvidenceQualificationPublicDTO(
                evidence.id,
                link.id,
                link.competency_identity_id,
                link.dimension_id,
                link.criterion_definition_id,
                evidence.strength,
                evidence.independence,
                evidence.source_confidence,
                READINESS_EVIDENCE_POLICY,
            )
            for evidence, link in items
            if _qualifies_readiness_evidence(evidence, link)
        )
        meaningful_times = [
            evidence.occurred_at
            for evidence, link in items
            if evidence.occurred_at is not None and _qualifies_readiness_evidence(evidence, link)
        ]
        last_meaningful_at = max(meaningful_times, default=None)
        payload = {
            "competencyIdentityId": competency_id,
            "evidenceIds": evidence_ids,
            "supportingIds": supporting,
            "contradictingIds": contradicting,
            "independentIds": independent,
            "qualifyingReadiness": [asdict(item) for item in qualifying],
            "lastMeaningfulAt": last_meaningful_at,
        }
        result.append(
            EvidenceCoveragePublicDTO(
                competency_id,
                evidence_ids,
                supporting,
                contradicting,
                independent,
                qualifying,
                last_meaningful_at,
                content_hash(payload),
            )
        )
    return tuple(result)


def readiness_evaluations_as_of(
    db: Session,
    *,
    profile: ActiveProfileProjectionPublicDTO,
    exclusive_cutoff_at: int,
    timezone_name: str,
) -> tuple[ReadinessGateEvaluationPublicDTO, ...]:
    active_semantics = active_semantic_definition_ids_as_of(
        db, exclusive_cutoff_at=exclusive_cutoff_at
    )
    active_projects = {
        version.id for _, version, _ in active_project_versions_as_of(db, exclusive_cutoff_at)
    }
    targets_by_id = {target.id: target for target in profile.targets}
    evidence_by_competency = {
        item.competency_identity_id: item
        for item in evidence_coverage_as_of(db, exclusive_cutoff_at=exclusive_cutoff_at)
    }
    evaluations: list[ReadinessGateEvaluationPublicDTO] = []
    for gate in profile.readiness_gates:
        predicate_results: list[ReadinessPredicateEvaluationPublicDTO] = []
        for predicate in gate.predicates:
            subject = json.loads(predicate.subject_json)
            state = "unknown"
            reason = "READINESS_FACT_UNKNOWN"
            if predicate.predicate_type == "capability_at_least":
                scale = db.scalar(
                    select(CapabilityScaleVersion).where(
                        CapabilityScaleVersion.scale_stable_key == subject["scaleStableKey"],
                        CapabilityScaleVersion.scale_version == subject["scaleVersion"],
                    )
                )
                semantic_id = active_semantics.get(subject["competencyIdentityId"])
                level = (
                    db.scalar(
                        select(CapabilityScaleLevel).where(
                            CapabilityScaleLevel.scale_version_id == scale.id,
                            CapabilityScaleLevel.stable_key == subject["levelStableKey"],
                        )
                    )
                    if scale
                    else None
                )
                dimension = (
                    db.scalar(
                        select(CapabilityScaleDimension).where(
                            CapabilityScaleDimension.scale_version_id == scale.id,
                            CapabilityScaleDimension.stable_key == subject["dimensionKey"],
                        )
                    )
                    if scale and subject.get("dimensionKey")
                    else None
                )
                capability_evaluation = (
                    evaluate_capability_as_of(
                        db,
                        competency_identity_id=subject["competencyIdentityId"],
                        semantic_definition_id=semantic_id,
                        dimension_id=dimension.id if dimension else None,
                        exclusive_cutoff_at=exclusive_cutoff_at,
                        timezone_name=timezone_name,
                    )
                    if semantic_id and scale
                    else None
                )
                capability_fact = (
                    capability_evaluation.capability if capability_evaluation else None
                )
                if (
                    capability_fact is None
                    or capability_fact.selected_level_ordinal is None
                    or level is None
                ):
                    reason = "CAPABILITY_UNKNOWN"
                elif capability_fact.selected_level_ordinal >= level.ordinal_rank:
                    state, reason = "met", "CAPABILITY_AT_LEAST_MET"
                else:
                    state, reason = "not_met", "CAPABILITY_BELOW_REQUIRED"
            elif predicate.predicate_type == "criterion_demonstrated":
                active_definition_ids = set(active_semantics.values())
                definition = db.scalar(
                    select(CriterionDefinition)
                    .where(
                        CriterionDefinition.criterion_identity_id == subject["criterionIdentityId"],
                        CriterionDefinition.semantic_definition_id.in_(active_definition_ids),
                        CriterionDefinition.created_at < exclusive_cutoff_at,
                    )
                    .order_by(CriterionDefinition.created_at.desc(), CriterionDefinition.id.desc())
                    .limit(1)
                )
                semantic = (
                    db.get(SemanticCompetencyDefinition, definition.semantic_definition_id)
                    if definition
                    else None
                )
                criterion_evaluation = (
                    evaluate_capability_as_of(
                        db,
                        competency_identity_id=semantic.competency_identity_id,
                        semantic_definition_id=semantic.id,
                        dimension_id=definition.dimension_id,
                        exclusive_cutoff_at=exclusive_cutoff_at,
                        timezone_name=timezone_name,
                    )
                    if definition and semantic
                    else None
                )
                criterion_fact = (
                    next(
                        (
                            item
                            for item in criterion_evaluation.criteria
                            if item.criterion_definition_id == definition.id
                        ),
                        None,
                    )
                    if criterion_evaluation and definition
                    else None
                )
                if criterion_fact is None:
                    reason = "CRITERION_UNKNOWN"
                elif criterion_fact.state == "demonstrated":
                    state, reason = "met", "CRITERION_DEMONSTRATED"
                else:
                    state, reason = "not_met", f"CRITERION_{criterion_fact.state.upper()}"
            elif predicate.predicate_type == "project_criterion_demonstrated":
                project_definition = db.scalar(
                    select(ProjectCriterionDefinition)
                    .join(
                        ProjectCriterionIdentity,
                        ProjectCriterionIdentity.id
                        == ProjectCriterionDefinition.criterion_identity_id,
                    )
                    .where(
                        ProjectCriterionIdentity.id == subject["projectCriterionIdentityId"],
                        ProjectCriterionDefinition.project_version_id.in_(active_projects),
                    )
                    .limit(1)
                )
                criterion_state = (
                    project_criterion_state_as_of(db, project_definition.id, exclusive_cutoff_at)
                    if project_definition
                    else "unknown"
                )
                if criterion_state == "demonstrated":
                    state, reason = "met", "PROJECT_CRITERION_DEMONSTRATED"
                elif criterion_state == "unknown":
                    reason = "PROJECT_CRITERION_UNKNOWN"
                else:
                    state, reason = "not_met", f"PROJECT_CRITERION_{criterion_state.upper()}"
            elif predicate.predicate_type == "evidence_present":
                policy_key = subject["evidencePolicyStableKey"]
                target_scopes = tuple(
                    targets_by_id[target_id]
                    for target_id in gate.target_ids
                    if target_id in targets_by_id
                )
                if policy_key != READINESS_EVIDENCE_POLICY:
                    reason = "EVIDENCE_POLICY_UNAVAILABLE"
                elif not target_scopes:
                    reason = "EVIDENCE_SCOPE_UNKNOWN"
                elif all(
                    (coverage := evidence_by_competency.get(target.competency_identity_id))
                    is not None
                    and any(
                        item.policy_version == policy_key
                        and evidence_qualification_matches_target(
                            db,
                            qualification=item,
                            target=target,
                            active_semantics=active_semantics,
                        )
                        for item in coverage.qualifying_readiness
                    )
                    for target in target_scopes
                ):
                    state, reason = "met", "QUALIFYING_EVIDENCE_PRESENT"
                else:
                    state, reason = "not_met", "QUALIFYING_EVIDENCE_MISSING"
            predicate_results.append(
                ReadinessPredicateEvaluationPublicDTO(
                    predicate.id,
                    predicate.predicate_type,
                    predicate.requirement_type,
                    state,
                    reason,
                    subject,
                )
            )
        required = [item.state for item in predicate_results if item.requirement_type == "required"]
        gate_state = (
            "not_met"
            if "not_met" in required
            else "unknown"
            if not required or "unknown" in required
            else "met"
        )
        evaluations.append(
            ReadinessGateEvaluationPublicDTO(
                gate.id,
                gate.stable_key,
                gate.effect,
                gate.target_ids,
                gate_state,
                tuple(predicate_results),
            )
        )
    return tuple(evaluations)


def last_positive_capability_transition_as_of(
    db: Session,
    *,
    competency_identity_id: str,
    dimension_id: str | None,
    exclusive_cutoff_at: int,
) -> int | None:
    """Return the recorded time of the latest cutoff-visible level increase."""
    rows = db.scalars(
        select(CapabilityStateEvent)
        .where(
            CapabilityStateEvent.competency_identity_id == competency_identity_id,
            CapabilityStateEvent.dimension_id == dimension_id,
            CapabilityStateEvent.created_at < exclusive_cutoff_at,
        )
        .order_by(CapabilityStateEvent.created_at.desc(), CapabilityStateEvent.id.desc())
    ).all()
    level_ordinals = {
        level_id: ordinal
        for level_id, ordinal in db.execute(
            select(CapabilityScaleLevel.id, CapabilityScaleLevel.ordinal_rank)
        ).all()
    }
    for event in rows:
        new_ordinal = level_ordinals.get(event.new_level_id or "")
        prior_ordinal = level_ordinals.get(event.previous_level_id or "")
        if new_ordinal is not None and (prior_ordinal is None or new_ordinal > prior_ordinal):
            return event.created_at
    return None
