from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.determinism import content_hash
from app.requirements.contracts import RequirementEvaluationDTO
from app.schemas import validate_external_reference


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCreate(StrictModel):
    stable_key: str = Field(min_length=1, max_length=255)
    creation_source: str = Field(default="user", min_length=1, max_length=64)


class OrderedDefinitionInput(StrictModel):
    stable_key: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    order_index: int = Field(ge=0)


class ProjectTaskInput(OrderedDefinitionInput):
    milestone_stable_key: str | None = Field(default=None, max_length=255)
    instructions: str = Field(min_length=1, max_length=20_000)
    status: Literal["active", "archived"] = "active"
    minimum_useful_duration_ms: int | None = None
    preferred_duration_ms: int | None = None
    maximum_useful_duration_ms: int | None = None

    @model_validator(mode="after")
    def duration_range_is_all_or_none(self) -> ProjectTaskInput:
        values = (
            self.minimum_useful_duration_ms,
            self.preferred_duration_ms,
            self.maximum_useful_duration_ms,
        )
        if all(value is None for value in values):
            return self
        if any(value is None for value in values):
            raise ValueError("Project task duration range must be all known or all unknown")
        minimum, preferred, maximum = values
        assert minimum is not None and preferred is not None and maximum is not None
        if not (0 < minimum <= preferred <= maximum) or any(
            value % 300_000 for value in (minimum, preferred, maximum)
        ):
            raise ValueError("Project task duration range must use ordered five-minute quanta")
        return self


class ProjectCriterionInput(OrderedDefinitionInput):
    milestone_stable_key: str | None = Field(default=None, max_length=255)
    evaluation_policy_version: str = Field(
        default="project-criterion-policy/v1", min_length=1, max_length=64
    )


class ProjectTargetInput(StrictModel):
    task_stable_key: str | None = Field(default=None, max_length=255)
    project_criterion_stable_key: str | None = Field(default=None, max_length=255)
    semantic_definition_id: str = Field(min_length=1, max_length=36)
    criterion_definition_id: str | None = Field(default=None, max_length=36)
    scale_version_id: str = Field(min_length=1, max_length=36)
    dimension_id: str | None = Field(default=None, max_length=36)
    level_id: str | None = Field(default=None, max_length=36)
    intended_outcome: str = Field(min_length=1, max_length=2000)
    role: Literal["primary", "secondary", "supporting"] = "primary"
    order_index: int = Field(ge=0)

    @model_validator(mode="after")
    def one_optional_owner(self) -> ProjectTargetInput:
        if self.task_stable_key and self.project_criterion_stable_key:
            raise ValueError("A Project target cannot belong to both a task and criterion")
        return self


class ProjectRequirementInput(StrictModel):
    task_stable_key: str | None = Field(default=None, max_length=255)
    stable_key: str = Field(min_length=1, max_length=255)
    requirement_type: Literal[
        "capability_at_least",
        "criterion_demonstrated",
        "project_criterion_demonstrated",
        "project_task_completed",
        "resource_available",
        "user_constraint",
    ]
    effect: Literal["hard", "soft"] = "hard"
    scope: Literal["learner", "project", "environment", "user"]
    subject: dict[str, Any]
    order_index: int = Field(ge=0)


class ProjectTaskDependencyInput(StrictModel):
    dependent_task_stable_key: str = Field(min_length=1, max_length=255)
    prerequisite_task_stable_key: str = Field(min_length=1, max_length=255)
    dependency_type: Literal["hard", "recommended_before"] = "hard"


class ProjectEvidenceOpportunityInput(StrictModel):
    stable_key: str = Field(min_length=1, max_length=255)
    task_stable_key: str | None = Field(default=None, max_length=255)
    project_criterion_stable_key: str | None = Field(default=None, max_length=255)
    evidence_kind: Literal["project", "code", "assessment", "manual", "review"] = "project"
    intended_strengths: list[Literal["weak", "moderate", "strong"]] = Field(default_factory=list)
    intended_independence_modes: list[Literal["guided", "assisted", "independent"]] = Field(
        default_factory=list
    )
    requires_artifact: bool = False
    order_index: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_owner_and_unique_values(self) -> ProjectEvidenceOpportunityInput:
        if self.task_stable_key is None and self.project_criterion_stable_key is None:
            raise ValueError("A Project Evidence opportunity must name a task or criterion")
        if len(self.intended_strengths) != len(set(self.intended_strengths)) or len(
            self.intended_independence_modes
        ) != len(set(self.intended_independence_modes)):
            raise ValueError("Intended Project Evidence characteristics must be unique")
        return self


class ProjectVersionInput(StrictModel):
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    effective_at: datetime
    creation_source: str = Field(default="user", min_length=1, max_length=64)
    goals: list[OrderedDefinitionInput] = Field(default_factory=list)
    milestones: list[OrderedDefinitionInput] = Field(default_factory=list)
    tasks: list[ProjectTaskInput] = Field(default_factory=list)
    criteria: list[ProjectCriterionInput] = Field(default_factory=list)
    targets: list[ProjectTargetInput] = Field(default_factory=list)
    requirements: list[ProjectRequirementInput] = Field(default_factory=list)
    dependencies: list[ProjectTaskDependencyInput] = Field(default_factory=list)
    evidence_opportunities: list[ProjectEvidenceOpportunityInput] = Field(default_factory=list)

    @field_validator("effective_at")
    @classmethod
    def aware_effective_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("effective_at must include a timezone offset")
        return value


class ProjectActivationInput(StrictModel):
    reason: str = Field(min_length=1, max_length=1000)
    source: str = Field(default="user", min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)


class ProjectEventInput(StrictModel):
    event_type: Literal["project_lifecycle", "task_lifecycle", "blocker_opened", "blocker_resolved"]
    project_lifecycle_state: Literal["planned", "active", "completed", "archived"] | None = None
    task_identity_id: str | None = Field(default=None, max_length=36)
    task_lifecycle_state: Literal["not_started", "started", "completed", "cancelled"] | None = None
    blocker_key: str | None = Field(default=None, max_length=255)
    blocker_actionable: bool | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    source: str = Field(default="user", min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)


class ProjectBlockerCorrectionInput(StrictModel):
    corrects_event_id: str = Field(min_length=1, max_length=36)
    blocker_key: str = Field(min_length=1, max_length=255)
    actionable: bool
    details: dict[str, Any] = Field(default_factory=dict)
    source: str = Field(default="user", min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)


class ActivityProjectLinkInput(StrictModel):
    activity_id: str = Field(min_length=1, max_length=36)
    task_definition_id: str = Field(min_length=1, max_length=36)
    provenance: Literal["user_selected", "user_confirmed"]
    idempotency_key: str = Field(min_length=1, max_length=128)


class ActivityProjectLinkCorrectionInput(StrictModel):
    replacement_link_id: str | None = Field(default=None, max_length=36)
    reason: str = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=1, max_length=128)


class SessionProjectContributionInput(StrictModel):
    session_id: str = Field(min_length=1, max_length=36)
    project_version_id: str = Field(min_length=1, max_length=36)
    task_definition_id: str | None = Field(default=None, max_length=36)
    relevance: Literal["primary", "secondary", "supporting"]
    provenance: Literal["user_selected", "user_confirmed"]
    idempotency_key: str = Field(min_length=1, max_length=128)


class ProjectEvidenceInput(StrictModel):
    activity_project_task_link_id: str = Field(min_length=1, max_length=36)
    opportunity_id: str = Field(min_length=1, max_length=36)
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    occurred_at: datetime
    artifact_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    external_reference: str | None = Field(default=None, max_length=4000)
    rubric_result: Literal["passed", "partially_met", "not_met"] | None = None
    idempotency_key: str = Field(min_length=1, max_length=128)

    _reference_is_safe = field_validator("external_reference")(validate_external_reference)

    @field_validator("occurred_at")
    @classmethod
    def aware_occurred_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone offset")
        return value


class ProjectCriterionEvaluationInput(StrictModel):
    evidence_ids: list[str] = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=128)


@dataclass(frozen=True)
class ProjectTargetFactPublicDTO:
    target_id: str
    semantic_definition_id: str
    criterion_definition_id: str | None
    scale_version_id: str
    dimension_id: str | None
    level_id: str | None
    intended_outcome: str
    role: str


@dataclass(frozen=True)
class ProjectBlockerFactPublicDTO:
    event_id: str
    blocker_key: str
    actionable: bool
    details: dict[str, Any]
    opened_at: int
    corrected_at: int | None


@dataclass(frozen=True)
class ProjectEvidenceOpportunityPublicDTO:
    opportunity_id: str
    task_definition_id: str | None
    project_criterion_definition_id: str | None
    intended_independence_modes: tuple[str, ...]


@dataclass(frozen=True)
class ProjectCandidatePublicDTO:
    candidate_type: str
    project_id: str
    project_stable_key: str
    project_version_id: str
    task_identity_id: str
    task_definition_id: str
    task_stable_key: str
    title: str
    description: str
    instructions: str
    duration_range_ms: tuple[int, int, int] | None
    lifecycle_state: str
    availability_state: str
    readiness_state: str
    candidate_usability_state: str
    actionable_blocker_keys: tuple[str, ...]
    blocker_facts: tuple[ProjectBlockerFactPublicDTO, ...]
    requirements: tuple[RequirementEvaluationDTO, ...]
    target_ids: tuple[str, ...]
    target_facts: tuple[ProjectTargetFactPublicDTO, ...]
    input_hash: str
    evidence_opportunity_ids: tuple[str, ...] = ()
    intended_independence_modes: tuple[str, ...] = ()
    evidence_opportunities: tuple[ProjectEvidenceOpportunityPublicDTO, ...] = ()


@dataclass(frozen=True)
class ActiveProjectVersionReferencePublicDTO:
    project_id: str
    stable_key: str
    version_id: str
    version: int
    content_hash: str
    activation_event_id: str
    activation_sequence: int
    latest_project_event_id: str | None
    project_event_sequence_cutoff: int


@dataclass(frozen=True)
class ProjectCatalogPublicDTO:
    cutoff_at: int
    cutoff_semantics: str
    active_version_references: tuple[ActiveProjectVersionReferencePublicDTO, ...]
    candidates: tuple[ProjectCandidatePublicDTO, ...]
    input_hash: str

    @classmethod
    def build(
        cls,
        *,
        cutoff_at: int,
        active_version_references: tuple[ActiveProjectVersionReferencePublicDTO, ...],
        candidates: tuple[ProjectCandidatePublicDTO, ...],
    ) -> ProjectCatalogPublicDTO:
        references = tuple(sorted(active_version_references, key=lambda item: item.stable_key))
        ordered = tuple(
            sorted(candidates, key=lambda item: (item.project_stable_key, item.task_stable_key))
        )
        payload = {
            "cutoffAt": cutoff_at,
            "cutoffSemantics": "exclusive",
            "activeVersionReferences": [asdict(item) for item in references],
            "candidates": [asdict(item) for item in ordered],
        }
        return cls(cutoff_at, "exclusive", references, ordered, content_hash(payload))
