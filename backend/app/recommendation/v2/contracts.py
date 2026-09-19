from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CandidateType = Literal[
    "curriculum_unit",
    "practice_task",
    "verification",
    "assessment",
    "review",
    "maintenance",
    "project_task",
    "unblock_task",
]
PrimaryNeedKind = Literal[
    "capability_gap",
    "assessment_unknown",
    "readiness_unblock",
    "prerequisite_unblock",
    "multi_target_unblock",
    "review_due",
    "maintenance_aging_critical",
]
PortfolioRole = Literal["primary", "complementary", "maintenance"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecommendationContextCost(StrictModel):
    source_type: str = Field(min_length=1, max_length=64)
    source_entity_id: str = Field(min_length=1, max_length=128)
    cost: Literal["none", "low", "moderate", "high", "unavailable"]


class RecommendationRunRequest(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=128)
    analysis_snapshot_id: str = Field(min_length=1, max_length=36)
    available_time_ms: int | None = Field(default=None, ge=0)
    context_costs: list[RecommendationContextCost] = Field(default_factory=list)

    @field_validator("idempotency_key")
    @classmethod
    def reject_internal_idempotency_namespace(cls, value: str) -> str:
        if value.startswith("internal:"):
            raise ValueError("idempotency_key uses a reserved internal namespace")
        return value

    @model_validator(mode="after")
    def unique_context_cost_sources(self) -> RecommendationRunRequest:
        source_keys = [(item.source_type, item.source_entity_id) for item in self.context_costs]
        if len(source_keys) != len(set(source_keys)):
            raise ValueError("context_costs must contain each source exactly once")
        return self


class RecommendationReplayRequest(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("idempotency_key")
    @classmethod
    def reject_internal_idempotency_namespace(cls, value: str) -> str:
        if value.startswith("internal:"):
            raise ValueError("idempotency_key uses a reserved internal namespace")
        return value


@dataclass(frozen=True)
class CandidateInputDTO:
    candidate_type: CandidateType
    candidate_key: str
    stable_id: str
    stable_tie_key: str
    source_type: str
    source_entity_id: str
    source_version_id: str | None
    title: str
    description: str
    target_identity_id: str | None
    served_target_identity_ids: tuple[str, ...]
    primary_outcome_kind: str
    primary_outcome_id: str
    primary_need_identity: str
    competency_identity_id: str | None
    criterion_definition_id: str | None
    project_id: str | None
    target_priority: str
    gap_severity: str
    deadline_status: str
    allocation_miss_basis_points: int | None
    neglect_or_stall_severity: str | None
    maintenance_severity: str | None
    context_cost: str
    context_available: bool
    last_meaningful_activity_at: int | None
    review_due: bool | None
    assessment_unknown: bool
    primary_need_kind: PrimaryNeedKind
    hard_blocker_count: int
    multi_target_unblock: bool
    missing_independence: bool
    missing_evidence_mode: bool
    supports_transfer: bool
    addresses_unmet_required_criterion: bool
    supplies_missing_required_mode: bool
    supplies_missing_independent_mode: bool
    supports_transfer_target_ids: tuple[str, ...]
    assessment_blocks_critical_planning: bool
    repeated_without_new_evidence: bool
    recently_saturated: bool
    active_target: bool
    prerequisites_satisfied: bool | None
    hard_readiness_satisfied: bool | None
    prerequisite_reference_ids: tuple[str, ...]
    readiness_reference_ids: tuple[str, ...]
    availability_requirement_ids: tuple[str, ...]
    availability_satisfied: bool | None
    capability_suitability_satisfied: bool | None
    blocker_clear: bool | None
    active_source: bool
    target_reached: bool
    blocker_actionable: bool
    blocker_reference_id: str | None
    unblock_action_available: bool | None
    verification_due: bool
    verification_template_present: bool
    assessment_rubric_present: bool
    assessment_scope_valid: bool | None
    intended_evidence_modes: tuple[str, ...]
    duration_range_ms: tuple[int, int, int] | None
    usefulness: bool
    explanation_facts: tuple[tuple[str, str | int | bool | None], ...]


def candidate_from_payload(payload: dict[str, Any]) -> CandidateInputDTO:
    values = dict(payload)
    for key in (
        "served_target_identity_ids",
        "supports_transfer_target_ids",
        "intended_evidence_modes",
        "prerequisite_reference_ids",
        "readiness_reference_ids",
        "availability_requirement_ids",
    ):
        values[key] = tuple(values[key])
    values["duration_range_ms"] = (
        tuple(values["duration_range_ms"]) if values["duration_range_ms"] is not None else None
    )
    values["explanation_facts"] = tuple(tuple(item) for item in values["explanation_facts"])
    return CandidateInputDTO(**values)


@dataclass(frozen=True)
class EligibilityRuleResultDTO:
    code: str
    outcome: Literal["pass", "fail", "unknown", "not_applicable"]
    decisive: bool
    facts: tuple[tuple[str, str | int | bool | None], ...] = ()
    subject_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScoreComponentDTO:
    code: str
    value: int
    allowed_minimum: int
    allowed_maximum: int
    decisive_facts: tuple[tuple[str, str | int | bool | None], ...]
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class EvaluatedCandidateDTO:
    candidate: CandidateInputDTO
    eligible: bool
    eligibility_reason: str
    eligibility_rules: tuple[EligibilityRuleResultDTO, ...]
    expected_learning_value: Literal["unknown", "low", "normal", "high", "very_high"]
    expected_learning_value_reasons: tuple[str, ...]
    expected_learning_value_facts: tuple[tuple[str, str | int | bool | None], ...]
    expected_learning_value_source_ids: tuple[str, ...]
    score_components: tuple[ScoreComponentDTO, ...]
    score_total: int | None
    rank_ordinal: int | None


@dataclass(frozen=True)
class SelectionDecisionDTO:
    candidate_stable_id: str
    decision: Literal["selected", "not_selected", "ineligible"]
    portfolio_role: PortfolioRole | None
    reason_code: str
    advisory_duration_ms: int | None
    duration_reason_code: str | None
    admission_ordinal: int | None
    displaced_by_candidate_stable_id: str | None = None
    decisive_facts: tuple[tuple[str, str | int | bool | None], ...] = ()


@dataclass(frozen=True)
class RecommendationReasonDTO:
    candidate_stable_id: str
    ordinal: int
    reason_code: str
    title: str
    facts: tuple[tuple[str, str | int | bool | None], ...]
    score_contribution: int | None
    template_key: str
    template_version: str
    rendered_text: str
    source_ids: tuple[str, ...]
    policy_version: str


@dataclass(frozen=True)
class SelectedRecommendationDTO:
    candidate_stable_id: str
    portfolio_role: PortfolioRole
    advisory_duration_ms: int | None
    duration_range_ms: tuple[int, int, int] | None
    rank_ordinal: int
    score_total: int
    score_breakdown_hash: str
    selection_reason_code: str


@dataclass(frozen=True)
class RecommendationPolicyOutputDTO:
    candidates: tuple[EvaluatedCandidateDTO, ...]
    decisions: tuple[SelectionDecisionDTO, ...]
    reasons: tuple[RecommendationReasonDTO, ...]
    recommendations: tuple[SelectedRecommendationDTO, ...]
    output_hash: str
