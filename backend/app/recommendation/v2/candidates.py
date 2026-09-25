from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from app.analysis.v3.contracts import (
    PublicAllocationFactDTO,
    PublicAnalysisSnapshotDTO,
    PublicTargetStateFactDTO,
)
from app.curriculum.contracts import (
    AssessmentRubricPublicDTO,
    CurriculumAvailabilityPublicDTO,
    CurriculumCatalogPublicDTO,
)
from app.errors import AppError
from app.learning_graph.contracts import ActiveLearningGraphProjectionPublicDTO
from app.projects.contracts import ProjectCatalogPublicDTO
from app.recommendation.v2.contracts import CandidateInputDTO, CandidateType, PrimaryNeedKind

if TYPE_CHECKING:
    from app.profile_views import ActiveProfileProjectionPublicDTO

_SEVERITY_RANK = {None: 0, "info": 1, "attention": 2, "high": 3, "critical": 4}
_RECENT_SATURATION_MAX_DAYS = 2
_RECENT_SATURATION_MIN_EXPOSURE_DAYS = 3


def _frozen_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, tuple) or not all(
        isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str) for item in value
    ):
        return None
    return {item[0]: item[1] for item in value}


def _criterion_activity_at(
    snapshot: PublicAnalysisSnapshotDTO,
    target: PublicTargetStateFactDTO,
    criterion_definition_id: str,
) -> int | None:
    return max(
        (
            session.ended_at
            for session in snapshot.actual_activity_summaries
            if (
                any(
                    qualification.competency_identity_id == target.competency_identity_id
                    and qualification.dimension_id == target.dimension_id
                    and qualification.criterion_definition_id == criterion_definition_id
                    for qualification in session.active_evidence_qualifications
                )
                or any(
                    attribution.competency_identity_id == target.competency_identity_id
                    and attribution.dimension_id == target.dimension_id
                    and attribution.criterion_definition_id == criterion_definition_id
                    for attribution in session.active_contribution_attributions
                )
            )
        ),
        default=None,
    )


def _assessment_blocks_due_hard_unknown(
    target: PublicTargetStateFactDTO,
    criterion_definition_id: str | None,
) -> bool:
    criterion_identity_ids = {
        item.criterion_identity_id
        for item in target.criterion_evaluations
        if criterion_definition_id is not None
        and item.criterion_definition_id == criterion_definition_id
    }
    for gate in target.readiness_gates:
        if (
            gate.effect != "hard_eligibility"
            or gate.state != "unknown"
            or gate.deadline_status not in {"due", "overdue"}
        ):
            continue
        for predicate in gate.predicates:
            if predicate.state != "unknown" or predicate.requirement_type != "required":
                continue
            subject = _frozen_object(predicate.subject)
            if subject is None:
                continue
            if (
                predicate.predicate_type == "capability_at_least"
                and subject.get("competencyIdentityId") == target.competency_identity_id
                and subject.get("dimensionKey") == target.dimension_key
                and subject.get("scaleStableKey") == target.scale_stable_key
                and subject.get("scaleVersion") == target.scale_version
            ):
                return True
            if (
                predicate.predicate_type == "criterion_demonstrated"
                and subject.get("criterionIdentityId") in criterion_identity_ids
            ):
                return True
    return False


def _target_facts(snapshot: PublicAnalysisSnapshotDTO) -> tuple[PublicTargetStateFactDTO, ...]:
    return tuple(item for item in snapshot.facts if isinstance(item, PublicTargetStateFactDTO))


def _target_for_semantic(
    targets: tuple[PublicTargetStateFactDTO, ...], semantic_definition_id: str
) -> PublicTargetStateFactDTO | None:
    matches = [item for item in targets if item.semantic_definition_id == semantic_definition_id]
    priority = {"critical": 0, "core": 1, "important": 2, "supporting": 3, "optional": 4}
    return min(
        matches, key=lambda item: (priority[item.priority], item.target_identity_id), default=None
    )


def _target_for_scope(
    targets: tuple[PublicTargetStateFactDTO, ...],
    profile: ActiveProfileProjectionPublicDTO,
    *,
    semantic_definition_id: str,
    scale_version_id: str,
    dimension_id: str | None,
    criterion_definition_id: str | None,
) -> PublicTargetStateFactDTO | None:
    profile_targets = {
        item.target_identity_id: item
        for item in profile.targets
        if item.scale_version_id == scale_version_id and item.dimension_id == dimension_id
    }
    matches = [
        item
        for item in targets
        if item.semantic_definition_id == semantic_definition_id
        and item.target_identity_id in profile_targets
        and (
            criterion_definition_id is None
            or any(
                result.criterion_definition_id == criterion_definition_id
                for result in item.criterion_evaluations
            )
        )
    ]
    priority = {"critical": 0, "core": 1, "important": 2, "supporting": 3, "optional": 4}
    return min(
        matches, key=lambda item: (priority[item.priority], item.target_identity_id), default=None
    )


def _signal_severity(
    snapshot: PublicAnalysisSnapshotDTO,
    target: PublicTargetStateFactDTO | None,
    signal_types: set[str] | None = None,
) -> str | None:
    if target is None:
        return None
    relevant = [
        signal.severity
        for signal in snapshot.signals
        if signal.subject_id == target.target_identity_id
        and signal.signal_type in (signal_types or {"NEGLECT", "PROGRESSION_STALL"})
    ]
    return max(relevant, key=lambda value: _SEVERITY_RANK[value], default=None)


def _three_valued_and(*values: bool | None) -> bool | None:
    if False in values:
        return False
    if None in values:
        return None
    return True


def _supplies_demonstration_mode(
    candidate_type: CandidateType,
    demonstration_rule: str | None,
    intended_modes: tuple[str, ...],
) -> bool:
    if demonstration_rule is None:
        return False
    if demonstration_rule in {"independent_performance", "repeated_independent_performance"}:
        return "independent" in intended_modes
    if demonstration_rule == "guided_performance":
        return bool({"guided", "assisted", "independent"} & set(intended_modes))
    if demonstration_rule == "exposure":
        return bool(intended_modes)
    if demonstration_rule == "authoritative_assessment":
        return candidate_type in {"assessment", "verification"}
    return False


def _allocation_miss(
    snapshot: PublicAnalysisSnapshotDTO,
    profile: ActiveProfileProjectionPublicDTO,
    target: PublicTargetStateFactDTO | None,
) -> int | None:
    if profile is None or target is None:
        return None
    projected = next(
        (item for item in profile.targets if item.target_identity_id == target.target_identity_id),
        None,
    )
    if projected is None:
        return None
    fact = next(
        (
            item
            for item in snapshot.facts
            if isinstance(item, PublicAllocationFactDTO)
            and item.profile_domain_id == projected.profile_domain_id
        ),
        None,
    )
    return -fact.miss_basis_points if fact and fact.sufficient_data else None


def _base_candidate(
    *,
    snapshot: PublicAnalysisSnapshotDTO,
    profile: ActiveProfileProjectionPublicDTO,
    target: PublicTargetStateFactDTO | None,
    candidate_type: CandidateType,
    stable_id: str,
    source_type: str,
    source_entity_id: str,
    source_version_id: str | None,
    title: str,
    description: str,
    criterion_definition_id: str | None,
    project_id: str | None,
    duration_range_ms: tuple[int, int, int] | None,
    active_source: bool,
    availability: bool | None,
    readiness: bool | None,
    intended_modes: tuple[str, ...],
    assessment_rubric_present: bool = False,
    blocker_actionable: bool = False,
    primary_need_kind: PrimaryNeedKind | None = None,
    blocker_clear: bool | None = True,
    capability_suitability: bool | None = True,
    hard_blocker_count: int = 0,
    multi_target_unblock: bool = False,
    supports_transfer_target_ids: tuple[str, ...] = (),
    served_targets: tuple[PublicTargetStateFactDTO, ...] = (),
    prerequisite_reference_ids: tuple[str, ...] = (),
    readiness_reference_ids: tuple[str, ...] = (),
    availability_requirement_ids: tuple[str, ...] = (),
    blocker_reference_id: str | None = None,
    unblock_action_available: bool | None = None,
    assessment_scope_valid: bool | None = None,
    context_cost: str = "unknown",
) -> CandidateInputDTO:
    prerequisite_states = (
        [item.aggregate_state for item in target.prerequisites] if target is not None else []
    )
    prerequisites: bool | None = (
        False
        if "not_met" in prerequisite_states
        else (None if "unknown" in prerequisite_states else True)
    )
    hard_gate_states = (
        [gate.state for gate in target.readiness_gates if gate.effect == "hard_eligibility"]
        if target is not None
        else []
    )
    gates: bool | None = (
        False
        if "not_met" in hard_gate_states
        else (None if "unknown" in hard_gate_states else True)
    )
    gap = next(
        (
            item
            for item in snapshot.gaps
            if target is not None
            and item.competency_identity_id == target.competency_identity_id
            and item.dimension_key == target.dimension_key
        ),
        None,
    )
    gap_severity = gap.severity if gap is not None else "unknown"
    target_reached = target is not None and target.comparison_status in {
        "at_target",
        "above_target",
    }
    review_due = target.review_due if target is not None else None
    assessment_unknown = target is not None and target.assessment_status == "unknown"
    verification_due = bool(
        target
        and (
            target.review_due
            or target.missing_evidence_requirement_ids
            or target.missing_independent_criterion_ids
            or gap_severity in {"medium", "high", "critical"}
        )
    )
    if primary_need_kind is None:
        if candidate_type == "assessment":
            primary_need_kind = "assessment_unknown"
        elif candidate_type == "unblock_task":
            primary_need_kind = (
                "multi_target_unblock" if multi_target_unblock else "readiness_unblock"
            )
        elif candidate_type in {"review", "maintenance"}:
            primary_need_kind = "review_due" if review_due else "maintenance_aging_critical"
        else:
            primary_need_kind = "capability_gap"
    served_target_ids = tuple(
        sorted(
            {
                item.target_identity_id
                for item in ((*served_targets, target) if target is not None else served_targets)
            }
        )
    )
    addresses_required = bool(
        target
        and criterion_definition_id
        and criterion_definition_id
        in {
            *target.unmet_required_criterion_ids,
            *target.partial_required_criterion_ids,
            *target.contradicted_required_criterion_ids,
        }
    )
    matched_criterion = next(
        (
            item
            for item in (target.criterion_evaluations if target is not None else ())
            if item.criterion_definition_id == criterion_definition_id
        ),
        None,
    )
    supplies_demonstration_mode = _supplies_demonstration_mode(
        candidate_type,
        matched_criterion.demonstration_rule if matched_criterion is not None else None,
        intended_modes,
    )
    supplies_missing_independent_mode = bool(
        target
        and criterion_definition_id
        and criterion_definition_id in target.missing_independent_criterion_ids
        and supplies_demonstration_mode
    )
    supplies_missing_mode = bool(
        target
        and criterion_definition_id
        and criterion_definition_id in target.missing_evidence_requirement_ids
        and supplies_demonstration_mode
    )
    primary_outcome_kind = "criterion" if criterion_definition_id else "target"
    primary_outcome_id = criterion_definition_id or (
        target.target_identity_id if target else source_entity_id
    )
    primary_need_identity = "|".join(
        (
            primary_need_kind,
            primary_outcome_id,
        )
    )
    evidence_objective = criterion_definition_id or ",".join(sorted(intended_modes)) or "none"
    candidate_key = "|".join(
        (
            "candidate-key/v1",
            candidate_type,
            source_type,
            stable_id,
            source_version_id or "unversioned",
            primary_outcome_kind,
            primary_outcome_id,
            evidence_objective,
        )
    )
    useful = bool(
        target
        and (
            assessment_unknown
            or review_due
            or not target_reached
            or blocker_actionable
            or candidate_type == "unblock_task"
            or primary_need_kind == "maintenance_aging_critical"
        )
    )
    direct_targets = tuple(
        sorted(
            {
                item.target_identity_id: item
                for item in (served_targets + ((target,) if target is not None else ()))
            }.values(),
            key=lambda item: item.target_identity_id,
        )
    )
    prerequisite_reference_ids = (
        prerequisite_reference_ids or tuple(item.edge_id for item in target.prerequisites)
        if target is not None
        else prerequisite_reference_ids
    )
    readiness_reference_ids = (
        readiness_reference_ids
        or tuple(
            sorted(
                {gate.gate_id for gate in target.readiness_gates}
                | {
                    predicate.predicate_id
                    for gate in target.readiness_gates
                    for predicate in gate.predicates
                }
            )
        )
        if target is not None
        else readiness_reference_ids
    )
    priority_rank = {"optional": 0, "supporting": 1, "important": 2, "core": 3, "critical": 4}
    deadline_rank = {"none": 0, "later": 1, "due_soon": 2, "due": 3, "overdue": 4}
    aggregate_priority = max(
        (item.priority for item in direct_targets),
        key=lambda value: priority_rank[value],
        default="optional",
    )
    aggregate_deadline = max(
        (item.deadline_status for item in direct_targets),
        key=lambda value: deadline_rank[value],
        default="none",
    )
    aggregate_neglect = max(
        (_signal_severity(snapshot, item) for item in direct_targets),
        key=lambda value: _SEVERITY_RANK[value],
        default=None,
    )
    primary_allocation = _allocation_miss(snapshot, profile, target)
    primary_activity_at = (
        _criterion_activity_at(snapshot, target, criterion_definition_id)
        if target is not None and criterion_definition_id is not None
        else (target.last_meaningful_activity_at if target is not None else None)
    )
    learning_action = candidate_type in {
        "curriculum_unit",
        "practice_task",
        "project_task",
    }
    repeated_without_new_evidence = bool(
        learning_action
        and matched_criterion is not None
        and matched_criterion.state == "demonstrated"
        and not supplies_missing_mode
        and not supplies_missing_independent_mode
        and not review_due
    )
    recently_saturated = bool(
        learning_action
        and target is not None
        and target.days_since_meaningful_activity <= _RECENT_SATURATION_MAX_DAYS
        and target.exposure_days_42 >= _RECENT_SATURATION_MIN_EXPOSURE_DAYS
        and not review_due
    )
    explanation = (
        ("analysisSnapshotId", snapshot.snapshot_id),
        ("targetIdentityId", target.target_identity_id if target else None),
        ("gapSeverity", gap_severity),
        ("deadlineStatus", aggregate_deadline),
        ("primaryOutcomeKind", primary_outcome_kind),
        ("primaryOutcomeId", primary_outcome_id),
        (
            "daysSinceMeaningfulActivity",
            target.days_since_meaningful_activity if target else None,
        ),
        ("exposureDays42", target.exposure_days_42 if target else None),
        ("recentSaturationMaximumDays", _RECENT_SATURATION_MAX_DAYS),
        ("recentSaturationMinimumExposureDays", _RECENT_SATURATION_MIN_EXPOSURE_DAYS),
    )
    return CandidateInputDTO(
        candidate_type=candidate_type,
        candidate_key=candidate_key,
        stable_id=candidate_key,
        stable_tie_key=candidate_key,
        source_type=source_type,
        source_entity_id=source_entity_id,
        source_version_id=source_version_id,
        title=title,
        description=description,
        target_identity_id=target.target_identity_id if target else None,
        served_target_identity_ids=served_target_ids,
        primary_outcome_kind=primary_outcome_kind,
        primary_outcome_id=primary_outcome_id,
        primary_need_identity=primary_need_identity,
        competency_identity_id=target.competency_identity_id if target else None,
        criterion_definition_id=criterion_definition_id,
        project_id=project_id,
        target_priority=aggregate_priority,
        gap_severity=gap_severity,
        deadline_status=aggregate_deadline,
        allocation_miss_basis_points=primary_allocation,
        neglect_or_stall_severity=aggregate_neglect,
        maintenance_severity=_signal_severity(snapshot, target, {"MAINTENANCE_DUE", "REVIEW_DUE"}),
        context_cost="unknown" if context_cost == "unavailable" else context_cost,
        context_available=context_cost != "unavailable",
        last_meaningful_activity_at=primary_activity_at,
        review_due=review_due,
        assessment_unknown=assessment_unknown,
        primary_need_kind=primary_need_kind,
        hard_blocker_count=hard_blocker_count,
        multi_target_unblock=multi_target_unblock,
        missing_independence=bool(target and target.missing_independent_criterion_ids),
        missing_evidence_mode=bool(
            target
            and (
                target.missing_evidence_requirement_ids or target.missing_independent_criterion_ids
            )
        ),
        supports_transfer=len(supports_transfer_target_ids) > 1,
        addresses_unmet_required_criterion=addresses_required,
        supplies_missing_required_mode=supplies_missing_mode,
        supplies_missing_independent_mode=supplies_missing_independent_mode,
        supports_transfer_target_ids=tuple(sorted(set(supports_transfer_target_ids))),
        assessment_blocks_critical_planning=bool(
            candidate_type == "assessment"
            and target
            and _assessment_blocks_due_hard_unknown(target, criterion_definition_id)
            and target.assessment_status == "unknown"
        ),
        repeated_without_new_evidence=repeated_without_new_evidence,
        recently_saturated=recently_saturated,
        active_target=target is not None,
        prerequisites_satisfied=prerequisites,
        hard_readiness_satisfied=_three_valued_and(gates, readiness),
        prerequisite_reference_ids=tuple(sorted(set(prerequisite_reference_ids))),
        readiness_reference_ids=tuple(sorted(set(readiness_reference_ids))),
        availability_requirement_ids=tuple(sorted(set(availability_requirement_ids))),
        availability_satisfied=availability,
        capability_suitability_satisfied=capability_suitability,
        blocker_clear=blocker_clear,
        active_source=active_source,
        target_reached=target_reached,
        blocker_actionable=blocker_actionable,
        blocker_reference_id=blocker_reference_id,
        unblock_action_available=unblock_action_available,
        verification_due=verification_due,
        verification_template_present=candidate_type == "verification",
        assessment_rubric_present=assessment_rubric_present,
        assessment_scope_valid=assessment_scope_valid,
        intended_evidence_modes=intended_modes,
        duration_range_ms=duration_range_ms,
        usefulness=useful,
        explanation_facts=explanation,
    )


def canonicalize_candidates(
    candidates: tuple[CandidateInputDTO, ...],
) -> tuple[CandidateInputDTO, ...]:
    canonical: dict[str, CandidateInputDTO] = {}
    for candidate_item in sorted(candidates, key=lambda candidate: candidate.candidate_key):
        existing = canonical.get(candidate_item.candidate_key)
        if existing is None:
            canonical[candidate_item.candidate_key] = candidate_item
            continue
        merged = replace(
            existing,
            served_target_identity_ids=tuple(
                sorted(
                    set(existing.served_target_identity_ids)
                    | set(candidate_item.served_target_identity_ids)
                )
            ),
            supports_transfer_target_ids=tuple(
                sorted(
                    set(existing.supports_transfer_target_ids)
                    | set(candidate_item.supports_transfer_target_ids)
                )
            ),
            prerequisite_reference_ids=tuple(
                sorted(
                    set(existing.prerequisite_reference_ids)
                    | set(candidate_item.prerequisite_reference_ids)
                )
            ),
            readiness_reference_ids=tuple(
                sorted(
                    set(existing.readiness_reference_ids)
                    | set(candidate_item.readiness_reference_ids)
                )
            ),
            availability_requirement_ids=tuple(
                sorted(
                    set(existing.availability_requirement_ids)
                    | set(candidate_item.availability_requirement_ids)
                )
            ),
            explanation_facts=tuple(
                sorted(set(existing.explanation_facts) | set(candidate_item.explanation_facts))
            ),
        )
        normalized_new = replace(
            candidate_item,
            served_target_identity_ids=merged.served_target_identity_ids,
            supports_transfer_target_ids=merged.supports_transfer_target_ids,
            prerequisite_reference_ids=merged.prerequisite_reference_ids,
            readiness_reference_ids=merged.readiness_reference_ids,
            availability_requirement_ids=merged.availability_requirement_ids,
            explanation_facts=merged.explanation_facts,
        )
        if merged != normalized_new:
            raise AppError(
                409,
                "RECOMMENDATION_CANDIDATE_KEY_CONFLICT",
                "Two candidate facts assigned different meanings to one canonical key.",
                {"candidateKey": candidate_item.candidate_key},
            )
        canonical[candidate_item.candidate_key] = merged
    return tuple(canonical.values())


def build_candidates(
    snapshot: PublicAnalysisSnapshotDTO,
    profile: ActiveProfileProjectionPublicDTO,
    curriculum: CurriculumCatalogPublicDTO,
    curriculum_availability: tuple[CurriculumAvailabilityPublicDTO, ...],
    projects: ProjectCatalogPublicDTO,
    graph: ActiveLearningGraphProjectionPublicDTO | None,
    context_costs: tuple[tuple[str, str, str], ...] = (),
    *,
    semantic_assessment_target_order: bool = False,
) -> tuple[CandidateInputDTO, ...]:
    targets = _target_facts(snapshot)
    profile_target_keys = {item.target_identity_id: item.stable_key for item in profile.targets}
    candidates: list[CandidateInputDTO] = []
    priority_order = {
        "critical": 0,
        "core": 1,
        "important": 2,
        "supporting": 3,
        "optional": 4,
    }
    context_cost_by_source = {
        (source_type, source_entity_id): cost
        for source_type, source_entity_id, cost in context_costs
    }
    if len(context_cost_by_source) != len(context_costs):
        raise AppError(
            422,
            "RECOMMENDATION_CONTEXT_COST_CONFLICT",
            "Each Recommendation context-cost source must be supplied exactly once.",
        )

    def supports_transfer_targets(
        target: PublicTargetStateFactDTO | None,
    ) -> tuple[str, ...]:
        if target is None or graph is None:
            return ()
        supported_competencies = {
            edge.target_competency_identity_id
            for edge in graph.edges
            if edge.edge_type == "supports"
            and edge.source_competency_identity_id == target.competency_identity_id
        }
        return tuple(
            sorted(
                item.target_identity_id
                for item in targets
                if item.competency_identity_id in supported_competencies
                and item.priority in {"core", "important"}
            )
        )

    for unit in curriculum.units:
        matched_targets = [
            (
                unit_target,
                _target_for_scope(
                    targets,
                    profile,
                    semantic_definition_id=unit_target.semantic_definition_id,
                    scale_version_id=unit_target.scale_version_id,
                    dimension_id=unit_target.dimension_id,
                    criterion_definition_id=unit_target.criterion_definition_id,
                ),
            )
            for unit_target in unit.targets
        ]
        primary_unit_target, target = min(
            matched_targets,
            key=lambda pair: (
                priority_order[pair[1].priority] if pair[1] else 5,
                {"overdue": 0, "due": 1, "due_soon": 2, "later": 3, "none": 4}[
                    pair[1].deadline_status
                ]
                if pair[1]
                else 5,
                pair[0].order_index,
                pair[0].target_id,
            ),
            default=(None, None),
        )
        availability = next(
            (
                item
                for item in curriculum_availability
                if item.learning_unit_definition_id == unit.unit_definition_id
            ),
            None,
        )
        if availability is None:
            raise AppError(
                500,
                "RECOMMENDATION_CURRICULUM_LINEAGE_INVALID",
                "The public Curriculum catalog lacks unit availability lineage.",
            )
        modes = tuple(
            sorted(
                {
                    mode
                    for opportunity in unit.evidence_opportunities
                    for mode in opportunity.intended_independence_modes
                }
            )
        )
        candidates.append(
            _base_candidate(
                snapshot=snapshot,
                profile=profile,
                target=target,
                candidate_type=cast(CandidateType, unit.candidate_type),
                stable_id=f"curriculum|{unit.unit_identity_id}",
                source_type="curriculum_unit",
                source_entity_id=unit.unit_definition_id,
                source_version_id=unit.curriculum_version_id,
                title=unit.title,
                description=unit.description,
                criterion_definition_id=(
                    primary_unit_target.criterion_definition_id if primary_unit_target else None
                ),
                project_id=None,
                duration_range_ms=unit.duration_range_ms,
                active_source=unit.status == "active",
                availability={"met": True, "not_met": False}.get(availability.availability_state),
                readiness={"met": True, "not_met": False}.get(availability.readiness_state),
                capability_suitability={"met": True, "not_met": False}.get(
                    availability.candidate_usability_state
                ),
                intended_modes=modes,
                supports_transfer_target_ids=supports_transfer_targets(target),
                served_targets=tuple(item for _source, item in matched_targets if item is not None),
                availability_requirement_ids=tuple(
                    item.stable_key for item in availability.requirements
                )
                + tuple(item.target_id for item in availability.target_suitability),
                context_cost=context_cost_by_source.get(
                    ("curriculum_unit", unit.unit_definition_id), "unknown"
                ),
            )
        )
    for rubric in curriculum.assessment_rubrics:
        target = min(
            (
                item
                for item in targets
                if item.semantic_definition_id == rubric.semantic_definition_id
                and (
                    rubric.criterion_definition_id is None
                    or any(
                        result.criterion_definition_id == rubric.criterion_definition_id
                        for result in item.criterion_evaluations
                    )
                )
            ),
            key=lambda item: (
                {"critical": 0, "core": 1, "important": 2, "supporting": 3, "optional": 4}[
                    item.priority
                ],
                (
                    profile_target_keys[item.target_identity_id]
                    if semantic_assessment_target_order
                    else item.target_identity_id
                ),
            ),
            default=None,
        )
        if target is None or target.assessment_status != "unknown":
            continue
        candidates.append(
            _base_candidate(
                snapshot=snapshot,
                profile=profile,
                target=target,
                candidate_type="assessment",
                stable_id=f"assessment|{rubric.identity_id}",
                source_type="assessment_rubric",
                source_entity_id=rubric.definition_id,
                source_version_id=rubric.curriculum_version_id,
                title=rubric.title,
                description=rubric.instructions,
                criterion_definition_id=rubric.criterion_definition_id,
                project_id=None,
                duration_range_ms=None,
                active_source=True,
                availability=True,
                readiness=True,
                intended_modes=("assessment",),
                assessment_rubric_present=True,
                assessment_scope_valid=True,
                supports_transfer_target_ids=supports_transfer_targets(target),
                served_targets=((target,) if target is not None else ()),
                context_cost=context_cost_by_source.get(
                    ("assessment_rubric", rubric.definition_id), "unknown"
                ),
            )
        )
    for project in projects.candidates:
        project_targets = [
            (
                fact,
                _target_for_scope(
                    targets,
                    profile,
                    semantic_definition_id=fact.semantic_definition_id,
                    scale_version_id=fact.scale_version_id,
                    dimension_id=fact.dimension_id,
                    criterion_definition_id=fact.criterion_definition_id,
                ),
            )
            for fact in project.target_facts
        ]
        primary_project_target, target = min(
            project_targets,
            key=lambda pair: (
                priority_order[pair[1].priority] if pair[1] else 5,
                {"overdue": 0, "due": 1, "due_soon": 2, "later": 3, "none": 4}[
                    pair[1].deadline_status
                ]
                if pair[1]
                else 5,
                pair[0].target_id,
            ),
            default=(None, None),
        )
        project_served_targets = tuple(
            matched for _fact, matched in project_targets if matched is not None
        )
        project_modes = tuple(
            sorted(
                {
                    mode
                    for opportunity in project.evidence_opportunities
                    if opportunity.task_definition_id == project.task_definition_id
                    for mode in opportunity.intended_independence_modes
                }
            )
        )
        candidates.append(
            _base_candidate(
                snapshot=snapshot,
                profile=profile,
                target=target,
                candidate_type="project_task",
                stable_id=f"project|{project.task_identity_id}",
                source_type="project_task",
                source_entity_id=project.task_definition_id,
                source_version_id=project.project_version_id,
                title=project.title,
                description=project.description,
                criterion_definition_id=(
                    primary_project_target.criterion_definition_id
                    if primary_project_target
                    else None
                ),
                project_id=project.project_id,
                duration_range_ms=project.duration_range_ms,
                active_source=project.lifecycle_state not in {"completed", "cancelled"},
                availability={"met": True, "not_met": False}.get(project.availability_state),
                readiness={"met": True, "not_met": False}.get(project.readiness_state),
                capability_suitability={"met": True, "not_met": False}.get(
                    project.candidate_usability_state
                ),
                blocker_clear=not project.blocker_facts,
                intended_modes=project_modes,
                supports_transfer_target_ids=supports_transfer_targets(target),
                served_targets=project_served_targets,
                availability_requirement_ids=tuple(item.stable_key for item in project.requirements)
                + tuple(item.target_id for item in project.target_facts),
                context_cost=context_cost_by_source.get(
                    ("project_task", project.task_definition_id), "unknown"
                ),
            )
        )
        for blocker in project.blocker_facts:
            unblock_action = blocker.details.get("action")
            if (
                not blocker.actionable
                or not isinstance(unblock_action, str)
                or not unblock_action.strip()
            ):
                continue
            candidates.append(
                _base_candidate(
                    snapshot=snapshot,
                    profile=profile,
                    target=target,
                    candidate_type="unblock_task",
                    stable_id=f"unblock|{project.task_identity_id}|{blocker.blocker_key}",
                    source_type="project_blocker",
                    source_entity_id=blocker.event_id,
                    source_version_id=project.project_version_id,
                    title=f"Unblock {project.title}",
                    description=unblock_action.strip(),
                    criterion_definition_id=(
                        primary_project_target.criterion_definition_id
                        if primary_project_target
                        else None
                    ),
                    project_id=project.project_id,
                    duration_range_ms=None,
                    active_source=True,
                    availability=None,
                    readiness=None,
                    intended_modes=(),
                    blocker_actionable=True,
                    blocker_reference_id=blocker.event_id,
                    unblock_action_available=True,
                    blocker_clear=None,
                    hard_blocker_count=1,
                    multi_target_unblock=(
                        len({matched.target_identity_id for matched in project_served_targets}) > 1
                    ),
                    supports_transfer_target_ids=supports_transfer_targets(target),
                    served_targets=project_served_targets,
                    context_cost=context_cost_by_source.get(
                        ("project_blocker", blocker.event_id), "unknown"
                    ),
                )
            )
    for target in targets:
        aging_critical = target.freshness == "aging" and target.priority == "critical"
        if target.review_due or aging_critical:
            candidates.append(
                _base_candidate(
                    snapshot=snapshot,
                    profile=profile,
                    target=target,
                    candidate_type="maintenance" if aging_critical else "review",
                    stable_id=f"maintenance|{target.target_identity_id}",
                    source_type="analysis_target",
                    source_entity_id=target.target_identity_id,
                    source_version_id=snapshot.snapshot_id,
                    title="Review current capability",
                    description="Refresh or review the evidence supporting this target.",
                    criterion_definition_id=None,
                    project_id=None,
                    duration_range_ms=None,
                    active_source=True,
                    availability=True,
                    readiness=True,
                    intended_modes=("review",),
                    context_cost=context_cost_by_source.get(
                        ("analysis_target", target.target_identity_id), "unknown"
                    ),
                )
            )
    return canonicalize_candidates(tuple(candidates))


STRUCTURAL_FACT_KEYS = (
    "soleHardUnitCount",
    "unresolvedHardUnitCount",
    "hardPrerequisiteTargetCount",
    "recommendedBeforeTargetCount",
    "supportsTargetCount",
)


def build_candidates_v3(
    snapshot: PublicAnalysisSnapshotDTO,
    profile: ActiveProfileProjectionPublicDTO,
    curriculum: CurriculumCatalogPublicDTO,
    curriculum_availability: tuple[CurriculumAvailabilityPublicDTO, ...],
    projects: ProjectCatalogPublicDTO,
    graph: ActiveLearningGraphProjectionPublicDTO | None,
    context_costs: tuple[tuple[str, str, str], ...] = (),
    *,
    semantic_assessment_target_order: bool = False,
) -> tuple[CandidateInputDTO, ...]:
    """Add cutoff-bound structural rank facts without changing candidate eligibility or score."""
    candidates = build_candidates(
        snapshot,
        profile,
        curriculum,
        curriculum_availability,
        projects,
        graph,
        context_costs,
        semantic_assessment_target_order=semantic_assessment_target_order,
    )
    rubrics = {item.definition_id: item for item in curriculum.assessment_rubrics}
    targets_by_id = {item.target_identity_id: item for item in _target_facts(snapshot)}
    availability = {item.learning_unit_definition_id: item for item in curriculum_availability}
    active_target_ids = {item.target_identity_id for item in profile.targets}
    unknown_targets = {
        item.competency_identity_id
        for item in _target_facts(snapshot)
        if item.assessment_status == "unknown" and item.target_identity_id in active_target_ids
    }
    output = []
    for candidate in candidates:
        if candidate.candidate_type != "assessment" or not candidate.assessment_unknown:
            output.append(candidate)
            continue
        rubric = rubrics[candidate.source_entity_id]
        assert candidate.target_identity_id is not None
        target = targets_by_id[candidate.target_identity_id]
        sole_units: set[str] = set()
        unresolved_units: set[str] = set()
        for unit in curriculum.units:
            if unit.status != "active":
                continue
            states = {
                item.stable_key: item.state
                for item in availability[unit.unit_definition_id].requirements
            }
            matching = set()
            unresolved = set()
            for requirement in unit.requirements:
                if requirement.effect != "hard" or states[requirement.stable_key] == "met":
                    continue
                unresolved.add(requirement.stable_key)
                if states[requirement.stable_key] != "unknown":
                    continue
                subject = json.loads(requirement.subject_json)
                if (
                    requirement.requirement_type == "capability_at_least"
                    and subject["semanticDefinitionId"] == rubric.semantic_definition_id
                    and subject["scaleVersionId"] == target.scale_version_id
                    and subject["dimensionId"] == target.dimension_id
                ) or (
                    requirement.requirement_type == "criterion_demonstrated"
                    and rubric.criterion_definition_id is not None
                    and subject["criterionDefinitionId"] == rubric.criterion_definition_id
                ):
                    matching.add(requirement.stable_key)
            if matching:
                unresolved_units.add(unit.unit_definition_id)
                if len(unresolved) == len(matching) == 1:
                    sole_units.add(unit.unit_definition_id)
        downstream: dict[str, set[str]] = {
            "prerequisite": set(),
            "recommended_before": set(),
            "supports": set(),
        }
        if graph is not None and candidate.competency_identity_id is not None:
            for edge in graph.edges:
                if (
                    edge.edge_type in downstream
                    and edge.source_competency_identity_id == candidate.competency_identity_id
                    and edge.target_competency_identity_id in unknown_targets
                ):
                    downstream[edge.edge_type].add(edge.target_competency_identity_id)
        values = (
            len(sole_units),
            len(unresolved_units),
            len(downstream["prerequisite"]),
            len(downstream["recommended_before"]),
            len(downstream["supports"]),
        )
        output.append(
            replace(
                candidate,
                explanation_facts=candidate.explanation_facts
                + tuple(zip(STRUCTURAL_FACT_KEYS, values, strict=True)),
            )
        )
    return tuple(output)


def _covered_rubric_criteria(
    rubric: AssessmentRubricPublicDTO, target: PublicTargetStateFactDTO
) -> set[str]:
    """Use only explicit rubric subjects that belong to the selected target."""
    rubric_json = json.loads(rubric.rubric_json)
    entries = rubric_json.get("criteria") if isinstance(rubric_json, dict) else None
    covered = (
        {
            item["criterionDefinitionId"]
            for item in entries
            if isinstance(item, dict) and isinstance(item.get("criterionDefinitionId"), str)
        }
        if isinstance(entries, list)
        else set()
    )
    explicit_subject = rubric.criterion_definition_id
    if explicit_subject is not None:
        covered = covered & {explicit_subject} if isinstance(entries, list) else {explicit_subject}
    return covered & {item.criterion_definition_id for item in target.criterion_evaluations}


def build_candidates_v4(
    snapshot: PublicAnalysisSnapshotDTO,
    profile: ActiveProfileProjectionPublicDTO,
    curriculum: CurriculumCatalogPublicDTO,
    curriculum_availability: tuple[CurriculumAvailabilityPublicDTO, ...],
    projects: ProjectCatalogPublicDTO,
    graph: ActiveLearningGraphProjectionPublicDTO | None,
    context_costs: tuple[tuple[str, str, str], ...] = (),
) -> tuple[CandidateInputDTO, ...]:
    """Refine rubric coverage and use portable semantic identities for final ties."""
    candidates = build_candidates_v3(
        snapshot,
        profile,
        curriculum,
        curriculum_availability,
        projects,
        graph,
        context_costs,
        semantic_assessment_target_order=True,
    )
    rubrics = {item.definition_id: item for item in curriculum.assessment_rubrics}
    targets = {item.target_identity_id: item for item in _target_facts(snapshot)}
    target_keys = {item.target_identity_id: item.stable_key for item in profile.targets}
    availability = {item.learning_unit_definition_id: item for item in curriculum_availability}
    curriculum_roots = {
        item.curriculum_id: item.stable_key for item in curriculum.active_version_references
    }
    sources: dict[tuple[str, str], tuple[str, ...]] = {}
    for unit in curriculum.units:
        sources[("curriculum_unit", unit.unit_definition_id)] = (
            unit.curriculum_stable_key,
            unit.unit_stable_key,
        )
    for rubric in curriculum.assessment_rubrics:
        sources[("assessment_rubric", rubric.definition_id)] = (
            curriculum_roots.get(rubric.curriculum_id, ""),
            rubric.stable_key,
        )
    for project in projects.candidates:
        task_key = (project.project_stable_key, project.task_stable_key)
        sources[("project_task", project.task_definition_id)] = task_key
        for blocker in project.blocker_facts:
            sources[("project_blocker", blocker.event_id)] = (*task_key, blocker.blocker_key)
    output = []
    for candidate in candidates:
        source_key: tuple[str, ...]
        if candidate.source_type == "analysis_target":
            assert candidate.target_identity_id is not None
            source_key = (target_keys[candidate.target_identity_id],)
        else:
            source_key = sources[(candidate.source_type, candidate.source_entity_id)]
        semantic_key = "semantic-tie/v1|" + json.dumps(
            (
                candidate.candidate_type,
                candidate.source_type,
                *source_key,
                target_keys.get(candidate.target_identity_id or "", ""),
            ),
            ensure_ascii=True,
            separators=(",", ":"),
        )
        facts = candidate.explanation_facts
        if candidate.candidate_type == "assessment" and candidate.assessment_unknown:
            rubric = rubrics[candidate.source_entity_id]
            assert candidate.target_identity_id is not None
            target = targets[candidate.target_identity_id]
            covered = _covered_rubric_criteria(rubric, target)
            sole_units: set[str] = set()
            unresolved_units: set[str] = set()
            for unit in curriculum.units:
                if unit.status != "active":
                    continue
                states = {
                    item.stable_key: item.state
                    for item in availability[unit.unit_definition_id].requirements
                }
                unresolved = {
                    item.stable_key
                    for item in unit.requirements
                    if item.effect == "hard" and states[item.stable_key] != "met"
                }
                matching = {
                    item.stable_key
                    for item in unit.requirements
                    if item.effect == "hard"
                    and item.scope == "learner"
                    and states[item.stable_key] == "unknown"
                    and item.requirement_type == "criterion_demonstrated"
                    and json.loads(item.subject_json)["criterionDefinitionId"] in covered
                }
                if matching:
                    unresolved_units.add(unit.unit_definition_id)
                    if len(unresolved) == len(matching) == 1:
                        sole_units.add(unit.unit_definition_id)
            updated = dict(facts)
            updated["soleHardUnitCount"] = len(sole_units)
            updated["unresolvedHardUnitCount"] = len(unresolved_units)
            facts = tuple((key, updated[key]) for key, _value in facts)
        output.append(replace(candidate, stable_tie_key=semantic_key, explanation_facts=facts))
    if len({item.stable_tie_key for item in output}) != len(output):
        raise AppError(
            409,
            "RECOMMENDATION_SEMANTIC_TIE_KEY_CONFLICT",
            "Distinct candidates have the same semantic ordering identity.",
        )
    return tuple(output)


def build_candidates_v1(
    snapshot: PublicAnalysisSnapshotDTO,
    profile: ActiveProfileProjectionPublicDTO,
    curriculum: CurriculumCatalogPublicDTO,
    curriculum_availability: tuple[CurriculumAvailabilityPublicDTO, ...],
    projects: ProjectCatalogPublicDTO,
    graph: ActiveLearningGraphProjectionPublicDTO | None,
    context_costs: tuple[tuple[str, str, str], ...] = (),
) -> tuple[CandidateInputDTO, ...]:
    """Replay-only candidate builder retained for registry/v1 historical identity."""
    current_only_facts = {
        "daysSinceMeaningfulActivity",
        "exposureDays42",
        "recentSaturationMaximumDays",
        "recentSaturationMinimumExposureDays",
    }
    return tuple(
        replace(
            item,
            repeated_without_new_evidence=False,
            recently_saturated=False,
            explanation_facts=tuple(
                fact for fact in item.explanation_facts if fact[0] not in current_only_facts
            ),
        )
        for item in build_candidates(
            snapshot,
            profile,
            curriculum,
            curriculum_availability,
            projects,
            graph,
            context_costs,
        )
    )
