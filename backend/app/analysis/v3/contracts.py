from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalysisRunRequest(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=128)
    purpose: Literal["learning_control", "candidate_readiness"] = "learning_control"
    cutoff_at: datetime | None = None

    @field_validator("cutoff_at")
    @classmethod
    def aware_cutoff(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("cutoff_at must include a timezone offset")
        return value


class AnalysisReplayRequest(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=128)


@dataclass(frozen=True)
class UnknownMarkerDTO:
    field_path: str
    subject_type: str
    subject_id: str
    reason_code: str


@dataclass(frozen=True)
class NormalizedFactDTO:
    stable_key: str
    fact_type: str
    subject_type: str
    subject_id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class CompetencyGapDTO:
    stable_key: str
    competency_identity_id: str
    dimension_key: str | None
    severity: Literal["unknown", "none", "low", "medium", "high", "critical"]
    comparison_status: Literal[
        "unknown", "below_target", "at_target", "above_target", "incomparable"
    ]
    payload: dict[str, Any]
    input_lineage: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class AnalysisSignalDTO:
    stable_key: str
    signal_type: str
    subject_type: str
    subject_id: str
    dimension_key: str | None
    severity: Literal["info", "attention", "high", "critical"]
    reason_codes: tuple[str, ...]
    decisive_facts: dict[str, Any]
    analyzer_policy_version: str
    generated_cutoff_at: int


@dataclass(frozen=True)
class AnalysisComputationDTO:
    facts: tuple[NormalizedFactDTO, ...]
    gaps: tuple[CompetencyGapDTO, ...]
    signals: tuple[AnalysisSignalDTO, ...]
    unknown_markers: tuple[UnknownMarkerDTO, ...]
    completeness: Literal["complete", "partial"]


type FrozenJson = (
    str | int | bool | None | tuple["FrozenJson", ...] | tuple[tuple[str, "FrozenJson"], ...]
)


def freeze_json(value: Any) -> FrozenJson:
    if isinstance(value, dict):
        return tuple((str(key), freeze_json(item)) for key, item in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"Unsupported public Analysis JSON value: {type(value).__name__}")


@dataclass(frozen=True)
class PublicCriterionEvaluationFactDTO:
    criterion_definition_id: str
    criterion_identity_id: str
    requirement_type: Literal["required", "important", "supporting"]
    level_id: str
    state: Literal[
        "unknown",
        "not_demonstrated",
        "partially_demonstrated",
        "demonstrated",
        "contradicted",
    ]
    demonstration_rule: str
    decisive_evidence_ids: tuple[str, ...]
    evaluation_facts: FrozenJson


@dataclass(frozen=True)
class PublicReadinessPredicateFactDTO:
    predicate_id: str
    predicate_type: Literal[
        "capability_at_least",
        "criterion_demonstrated",
        "project_criterion_demonstrated",
        "evidence_present",
    ]
    requirement_type: Literal["required", "supporting"]
    state: Literal["met", "not_met", "unknown"]
    reason_code: str
    subject: FrozenJson


@dataclass(frozen=True)
class PublicReadinessGateFactDTO:
    gate_id: str
    state: Literal["met", "not_met", "unknown"]
    effect: Literal["hard_eligibility", "urgency", "display_only"]
    deadline_status: Literal["none", "later", "due_soon", "due", "overdue"]
    deadline_date: str | None
    predicates: tuple[PublicReadinessPredicateFactDTO, ...]


@dataclass(frozen=True)
class PublicPrerequisiteFactDTO:
    edge_id: str
    aggregate_state: Literal["met", "not_met", "unknown"]
    eligibility_satisfied: bool
    unknown_reasons: tuple[str, ...]


@dataclass(frozen=True)
class PublicTargetStateFactDTO:
    stable_key: str
    fact_type: Literal["target_state"]
    target_identity_id: str
    competency_identity_id: str
    dimension_key: str | None
    profile_target_id: str
    semantic_definition_id: str | None
    scale_version_id: str
    target_level_id: str
    target_level_ordinal: int
    current_level_id: str | None
    current_level_ordinal: int | None
    assessment_status: Literal["unknown", "evaluated"]
    priority: Literal["optional", "supporting", "important", "core", "critical"]
    comparison_status: Literal[
        "unknown", "below_target", "at_target", "above_target", "incomparable"
    ]
    confidence: Literal["unknown", "low", "medium", "high"]
    freshness: Literal["unknown", "current", "aging", "stale"]
    review_due: bool | None
    deadline_status: Literal["none", "later", "due_soon", "due", "overdue"]
    deadline_date: str | None
    unmet_required_criterion_ids: tuple[str, ...]
    partial_required_criterion_ids: tuple[str, ...]
    contradicted_required_criterion_ids: tuple[str, ...]
    contradicted_important_criterion_ids: tuple[str, ...]
    open_lesser_criterion_ids: tuple[str, ...]
    missing_evidence_requirement_ids: tuple[str, ...]
    missing_independent_criterion_ids: tuple[str, ...]
    important_supporting_missing_criterion_ids: tuple[str, ...]
    supporting_evidence_ids: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...]
    independent_evidence_ids: tuple[str, ...]
    criterion_evaluations: tuple[PublicCriterionEvaluationFactDTO, ...]
    readiness_gates: tuple[PublicReadinessGateFactDTO, ...]
    prerequisites: tuple[PublicPrerequisiteFactDTO, ...]
    days_since_meaningful_activity: int
    last_meaningful_activity_at: int | None
    exposure_days_42: int
    stall_eligible: bool
    days_since_positive_transition: int | None
    critical_gate_due: bool
    input_lineage: FrozenJson
    payload: FrozenJson


@dataclass(frozen=True)
class PublicAllocationFactDTO:
    stable_key: str
    fact_type: Literal["allocation"]
    profile_domain_id: str
    duration_ms: int
    assigned_total_duration_ms: int
    actual_basis_points: int | None
    minimum_basis_points: int | None
    maximum_basis_points: int | None
    miss_basis_points: int
    sufficient_data: bool
    payload: FrozenJson


@dataclass(frozen=True)
class PublicCurriculumCatalogFactDTO:
    stable_key: str
    fact_type: Literal["curriculum_catalog"]
    active_version_references: FrozenJson
    unit_count: int
    assessment_rubric_count: int
    input_hash: str
    payload: FrozenJson


@dataclass(frozen=True)
class PublicProjectTaskStateFactDTO:
    stable_key: str
    fact_type: Literal["project_task_state"]
    task_definition_id: str
    project_id: str
    project_version_id: str
    availability_state: str
    readiness_state: str
    candidate_usability_state: str
    input_hash: str
    payload: FrozenJson


@dataclass(frozen=True)
class PublicDisciplineVarianceFactDTO:
    stable_key: str
    fact_type: Literal["discipline_variance"]
    severity: str | None
    reason_code: str
    payload: FrozenJson


@dataclass(frozen=True)
class PublicWorkloadRiskFactDTO:
    stable_key: str
    fact_type: Literal["workload_risk"]
    severity: str | None
    reason_code: str
    surge_comparison_status: str
    payload: FrozenJson


type PublicAnalysisFactDTO = (
    PublicTargetStateFactDTO
    | PublicAllocationFactDTO
    | PublicCurriculumCatalogFactDTO
    | PublicProjectTaskStateFactDTO
    | PublicDisciplineVarianceFactDTO
    | PublicWorkloadRiskFactDTO
)


@dataclass(frozen=True)
class PublicAnalysisGapDTO:
    stable_key: str
    competency_identity_id: str
    dimension_key: str | None
    severity: Literal["unknown", "none", "low", "medium", "high", "critical"]
    comparison_status: Literal[
        "unknown", "below_target", "at_target", "above_target", "incomparable"
    ]
    reason_codes: tuple[str, ...]
    gap_policy_version: str
    target_fact: PublicTargetStateFactDTO
    payload: FrozenJson
    input_lineage: FrozenJson


@dataclass(frozen=True)
class PublicCompletedWeekDTO:
    week_start: str
    week_end: str
    active_days: int


@dataclass(frozen=True)
class PublicAnalysisSignalFactsDTO:
    """Typed union envelope for every decisive Analysis signal input."""

    signal_type: Literal[
        "CAPABILITY_GAP",
        "NEGLECT",
        "UNDER_ALLOCATION",
        "OVER_ALLOCATION",
        "READINESS_BLOCK",
        "PREREQUISITE_BLOCK",
        "EVIDENCE_WEAKNESS",
        "REVIEW_DUE",
        "LOW_CONFIDENCE",
        "PROGRESSION_STALL",
        "DEADLINE_PRESSURE",
        "MAINTENANCE_DUE",
        "DISCIPLINE_VARIANCE",
        "WORKLOAD_RISK",
    ]
    gap_key: str | None = None
    comparison_status: str | None = None
    deadline_status: str | None = None
    deadline_date: str | None = None
    readiness_gate: PublicReadinessGateFactDTO | None = None
    prerequisite: PublicPrerequisiteFactDTO | None = None
    days: int | None = None
    priority: str | None = None
    freshness: str | None = None
    confidence: str | None = None
    contradicted_required_criterion_ids: tuple[str, ...] = ()
    contradicted_important_criterion_ids: tuple[str, ...] = ()
    missing_required_criterion_ids: tuple[str, ...] = ()
    missing_independent_criterion_ids: tuple[str, ...] = ()
    important_supporting_missing_criterion_ids: tuple[str, ...] = ()
    supporting_evidence_ids: tuple[str, ...] = ()
    contradicting_evidence_ids: tuple[str, ...] = ()
    independent_evidence_ids: tuple[str, ...] = ()
    exposure_days: int | None = None
    allocation_duration_ms: int | None = None
    allocation_assigned_total_duration_ms: int | None = None
    allocation_actual_basis_points: int | None = None
    allocation_minimum_basis_points: int | None = None
    allocation_maximum_basis_points: int | None = None
    allocation_miss_basis_points: int | None = None
    allocation_sufficient_data: bool | None = None
    discipline_reason_code: str | None = None
    discipline_severity: str | None = None
    discipline_completed_weeks: tuple[PublicCompletedWeekDTO, ...] = ()
    discipline_target_active_days: int | None = None
    workload_reason_code: str | None = None
    workload_severity: str | None = None
    workload_target_duration_ms: int | None = None
    workload_active_day_durations_ms: tuple[int, ...] = ()
    workload_median_duration_ms: int | None = None
    workload_median_basis_points_of_target: int | None = None
    workload_current_seven_day_total_duration_ms: int | None = None
    workload_preceding_twenty_one_day_total_duration_ms: int | None = None
    workload_preceding_weekly_equivalent_duration_ms: int | None = None
    workload_surge_basis_points: int | None = None
    surge_comparison_status: str | None = None


@dataclass(frozen=True)
class PublicAnalysisSignalDTO:
    stable_key: str
    signal_type: Literal[
        "CAPABILITY_GAP",
        "NEGLECT",
        "UNDER_ALLOCATION",
        "OVER_ALLOCATION",
        "READINESS_BLOCK",
        "PREREQUISITE_BLOCK",
        "EVIDENCE_WEAKNESS",
        "REVIEW_DUE",
        "LOW_CONFIDENCE",
        "PROGRESSION_STALL",
        "DEADLINE_PRESSURE",
        "MAINTENANCE_DUE",
        "DISCIPLINE_VARIANCE",
        "WORKLOAD_RISK",
    ]
    subject_type: str
    subject_id: str
    dimension_key: str | None
    severity: Literal["info", "attention", "high", "critical"]
    reason_codes: tuple[str, ...]
    decisive_fact_codes: tuple[str, ...]
    facts: PublicAnalysisSignalFactsDTO
    decisive_facts: FrozenJson
    analyzer_policy_version: str
    generated_cutoff_at: int


@dataclass(frozen=True)
class PublicAnalysisSnapshotDTO:
    snapshot_id: str
    run_id: str
    purpose: str
    schema_version: int
    generated_at: int
    cutoff_at: int
    cutoff_semantics: str
    timezone: str
    completed_through_date: str
    completeness: str
    input_hash: str
    output_hash: str
    policy_versions: FrozenJson
    target_profile_id: str | None
    target_profile_version_id: str | None
    learning_graph_reference: str | None
    curriculum_reference: str | None
    semantic_definition_references: tuple[str, ...]
    capability_scale_version_references: tuple[str, ...]
    discipline_configuration_reference: str
    configuration_hash: str
    application_version: str
    input_lineage: FrozenJson
    facts: tuple[PublicAnalysisFactDTO, ...]
    gaps: tuple[PublicAnalysisGapDTO, ...]
    signals: tuple[PublicAnalysisSignalDTO, ...]
    unknown_markers: tuple[UnknownMarkerDTO, ...]
