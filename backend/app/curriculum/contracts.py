from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.determinism import content_hash
from app.requirements.contracts import RequirementEvaluationDTO
from app.schemas import validate_external_reference

UnitKind = Literal["resource", "exercise", "practice_task", "verification_template"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CurriculumCreate(StrictModel):
    stable_key: str = Field(min_length=1, max_length=255)
    creation_source: str = Field(default="user", min_length=1, max_length=64)


class ObjectiveInput(StrictModel):
    stable_key: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    order_index: int = Field(ge=0)


class UnitTargetInput(StrictModel):
    semantic_definition_id: str = Field(min_length=1, max_length=36)
    criterion_definition_id: str | None = Field(default=None, max_length=36)
    scale_version_id: str = Field(min_length=1, max_length=36)
    dimension_id: str | None = Field(default=None, max_length=36)
    intended_learning_outcome: str = Field(min_length=1, max_length=2000)
    minimum_level_id: str | None = Field(default=None, max_length=36)
    maximum_level_id: str | None = Field(default=None, max_length=36)
    supports_unassessed: bool = False
    role: Literal["primary", "secondary", "supporting"] = "primary"
    order_index: int = Field(ge=0)


class RequirementInput(StrictModel):
    stable_key: str = Field(min_length=1, max_length=255)
    requirement_type: Literal[
        "capability_at_least",
        "criterion_demonstrated",
        "learning_unit_completed",
        "resource_available",
        "user_constraint",
    ]
    effect: Literal["hard", "soft"] = "hard"
    scope: Literal["learner", "curriculum", "environment", "user"]
    subject: dict[str, Any]
    order_index: int = Field(ge=0)


class EvidenceOpportunityInput(StrictModel):
    stable_key: str = Field(min_length=1, max_length=255)
    evidence_kind: Literal[
        "session", "verification", "project", "code", "assessment", "manual", "review"
    ]
    intended_strengths: list[Literal["weak", "moderate", "strong"]] = Field(default_factory=list)
    intended_independence_modes: list[
        Literal["guided", "assisted", "independent", "not_applicable"]
    ] = Field(default_factory=list)
    requires_actual_activity: bool = True
    requires_artifact: bool = False
    order_index: int = Field(ge=0)

    @model_validator(mode="after")
    def intended_values_are_unique(self) -> EvidenceOpportunityInput:
        if len(self.intended_strengths) != len(set(self.intended_strengths)) or len(
            self.intended_independence_modes
        ) != len(set(self.intended_independence_modes)):
            raise ValueError("Intended Evidence characteristics must be unique")
        return self


class ResourceActionInput(StrictModel):
    kind: Literal["resource"]
    resource_reference: str = Field(min_length=1, max_length=4000)

    _reference_is_safe = field_validator("resource_reference")(validate_external_reference)


class ExerciseActionInput(StrictModel):
    kind: Literal["exercise"]
    instructions: str = Field(min_length=1, max_length=20_000)


class PracticeTaskActionInput(StrictModel):
    kind: Literal["practice_task"]
    instructions: str = Field(min_length=1, max_length=20_000)


class VerificationTemplateActionInput(StrictModel):
    kind: Literal["verification_template"]
    verification_method: str = Field(min_length=1, max_length=2000)


UnitActionInput = Annotated[
    ResourceActionInput
    | ExerciseActionInput
    | PracticeTaskActionInput
    | VerificationTemplateActionInput,
    Field(discriminator="kind"),
]


class LearningUnitInput(StrictModel):
    stable_key: str = Field(min_length=1, max_length=255)
    objective_stable_key: str | None = Field(default=None, max_length=255)
    kind: UnitKind
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    action: UnitActionInput
    status: Literal["active", "archived"] = "active"
    provenance: str = Field(min_length=1, max_length=64)
    order_index: int = Field(ge=0)
    minimum_useful_duration_ms: int | None = None
    preferred_duration_ms: int | None = None
    maximum_useful_duration_ms: int | None = None
    targets: list[UnitTargetInput] = Field(default_factory=list)
    requirements: list[RequirementInput] = Field(default_factory=list)
    evidence_opportunities: list[EvidenceOpportunityInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_action(self) -> LearningUnitInput:
        if self.action.kind != self.kind:
            raise ValueError("The action payload does not match the learning-unit kind")
        return self


class AssessmentRubricInput(StrictModel):
    stable_key: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    instructions: str = Field(min_length=1)
    rubric: dict[str, Any]
    semantic_definition_id: str = Field(min_length=1, max_length=36)
    criterion_definition_id: str | None = Field(default=None, max_length=36)


class CurriculumVersionInput(StrictModel):
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    effective_at: datetime
    creation_source: str = Field(default="user", min_length=1, max_length=64)
    objectives: list[ObjectiveInput] = Field(default_factory=list)
    units: list[LearningUnitInput] = Field(default_factory=list)
    assessment_rubrics: list[AssessmentRubricInput] = Field(default_factory=list)

    @field_validator("effective_at")
    @classmethod
    def effective_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("effective_at must include a timezone offset")
        return value


class CurriculumActivationInput(StrictModel):
    reason: str = Field(min_length=1, max_length=1000)
    source: str = Field(default="user", min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)


class ActivityUnitLinkInput(StrictModel):
    activity_id: str = Field(min_length=1, max_length=36)
    learning_unit_definition_id: str = Field(min_length=1, max_length=36)
    provenance: Literal["user_selected", "user_confirmed", "imported_asserted"]
    idempotency_key: str = Field(min_length=1, max_length=128)


class ActivityUnitLinkCorrectionInput(StrictModel):
    replacement_link_id: str | None = Field(default=None, max_length=36)
    reason: str = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=1, max_length=128)


@dataclass(frozen=True)
class CurriculumObjectivePublicDTO:
    curriculum_id: str
    curriculum_version_id: str
    identity_id: str
    stable_key: str
    title: str
    description: str
    order_index: int


@dataclass(frozen=True)
class ActiveCurriculumVersionReferencePublicDTO:
    curriculum_id: str
    stable_key: str
    version_id: str
    version: int
    content_hash: str
    activation_event_id: str
    activation_sequence: int


@dataclass(frozen=True)
class CurriculumActionPublicDTO:
    kind: str
    resource_reference: str | None
    instructions: str | None
    verification_method: str | None


@dataclass(frozen=True)
class CurriculumTargetPublicDTO:
    target_id: str
    semantic_definition_id: str
    criterion_definition_id: str | None
    scale_version_id: str
    dimension_id: str | None
    intended_learning_outcome: str
    minimum_level_id: str | None
    maximum_level_id: str | None
    supports_unassessed: bool
    role: str
    order_index: int


@dataclass(frozen=True)
class CurriculumRequirementPublicDTO:
    stable_key: str
    requirement_type: str
    effect: str
    scope: str
    subject_json: str
    order_index: int
    policy_version: str


@dataclass(frozen=True)
class EvidenceOpportunityPublicDTO:
    stable_key: str
    evidence_kind: str
    intended_strengths: tuple[str, ...]
    intended_independence_modes: tuple[str, ...]
    requires_actual_activity: bool
    requires_artifact: bool
    order_index: int
    policy_version: str


@dataclass(frozen=True)
class AssessmentRubricPublicDTO:
    curriculum_id: str
    curriculum_version_id: str
    definition_id: str
    identity_id: str
    stable_key: str
    title: str
    instructions: str
    rubric_json: str
    semantic_definition_id: str
    criterion_definition_id: str | None


@dataclass(frozen=True)
class TargetSuitabilityPublicDTO:
    target_id: str
    semantic_definition_id: str
    criterion_definition_id: str | None
    scale_version_id: str
    dimension_id: str | None
    role: str
    state: str
    reason_code: str
    selected_level_id: str | None
    supports_unassessed: bool
    profile_relevance_state: str
    target_profile_version_id: str | None
    profile_target_identity_ids: tuple[str, ...]
    candidate_usability_state: str


@dataclass(frozen=True)
class CurriculumAvailabilityPublicDTO:
    learning_unit_definition_id: str
    cutoff_at: int
    cutoff_semantics: str
    availability_state: str
    readiness_state: str
    candidate_usability_state: str
    requirements: tuple[RequirementEvaluationDTO, ...]
    target_suitability: tuple[TargetSuitabilityPublicDTO, ...]
    policy_version: str
    input_hash: str


@dataclass(frozen=True)
class CurriculumUnitPublicDTO:
    curriculum_id: str
    curriculum_stable_key: str
    curriculum_version_id: str
    curriculum_version: int
    unit_identity_id: str
    unit_stable_key: str
    unit_definition_id: str
    kind: str
    candidate_type: str
    title: str
    description: str
    objective_identity_id: str | None
    objective_stable_key: str | None
    status: str
    provenance: str
    action: CurriculumActionPublicDTO
    order_index: int
    duration_range_ms: tuple[int, int, int] | None
    targets: tuple[CurriculumTargetPublicDTO, ...]
    requirements: tuple[CurriculumRequirementPublicDTO, ...]
    evidence_opportunities: tuple[EvidenceOpportunityPublicDTO, ...]


@dataclass(frozen=True)
class CurriculumCatalogPublicDTO:
    cutoff_at: int
    cutoff_semantics: str
    active_version_references: tuple[ActiveCurriculumVersionReferencePublicDTO, ...]
    objectives: tuple[CurriculumObjectivePublicDTO, ...]
    units: tuple[CurriculumUnitPublicDTO, ...]
    assessment_rubrics: tuple[AssessmentRubricPublicDTO, ...]
    input_hash: str

    @classmethod
    def build(
        cls,
        *,
        cutoff_at: int,
        active_version_references: tuple[ActiveCurriculumVersionReferencePublicDTO, ...],
        objectives: tuple[CurriculumObjectivePublicDTO, ...],
        units: tuple[CurriculumUnitPublicDTO, ...],
        assessment_rubrics: tuple[AssessmentRubricPublicDTO, ...],
    ) -> CurriculumCatalogPublicDTO:
        ordered_references = tuple(
            sorted(
                active_version_references,
                key=lambda item: (item.stable_key, item.version_id),
            )
        )
        ordered_units = tuple(
            sorted(
                units,
                key=lambda item: (
                    item.curriculum_stable_key,
                    item.order_index,
                    item.unit_stable_key,
                    item.unit_definition_id,
                ),
            )
        )
        ordered_objectives = tuple(
            sorted(
                objectives,
                key=lambda item: (
                    item.curriculum_id,
                    item.order_index,
                    item.stable_key,
                    item.identity_id,
                ),
            )
        )
        ordered_rubrics = tuple(
            sorted(
                assessment_rubrics,
                key=lambda item: (
                    item.curriculum_id,
                    item.stable_key,
                    item.definition_id,
                ),
            )
        )
        payload = {
            "cutoffAt": cutoff_at,
            "cutoffSemantics": "exclusive",
            "activeVersionReferences": [asdict(item) for item in ordered_references],
            "objectives": [asdict(item) for item in ordered_objectives],
            "units": [asdict(item) for item in ordered_units],
            "assessmentRubrics": [asdict(item) for item in ordered_rubrics],
        }
        return cls(
            cutoff_at=cutoff_at,
            cutoff_semantics="exclusive",
            active_version_references=ordered_references,
            objectives=ordered_objectives,
            units=ordered_units,
            assessment_rubrics=ordered_rubrics,
            input_hash=content_hash(payload),
        )
