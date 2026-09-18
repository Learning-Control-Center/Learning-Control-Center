from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LearningGraphCreate(StrictModel):
    stable_key: str = Field(min_length=1, max_length=255)
    creation_source: str = Field(default="user", min_length=1, max_length=64)


class CapabilityRequirementInput(StrictModel):
    kind: Literal["capability_at_least"]
    scale_version_id: str = Field(min_length=1, max_length=36)
    dimension_id: str | None = Field(default=None, max_length=36)
    minimum_level_id: str = Field(min_length=1, max_length=36)
    review_requirement: Literal["none", "review_due_false"] = "none"


class CriterionSetRequirementInput(StrictModel):
    kind: Literal["criterion_set_demonstrated"]
    criterion_definition_ids: list[str] = Field(min_length=1)

    @field_validator("criterion_definition_ids")
    @classmethod
    def unique_criteria(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Criterion requirements must be unique")
        return value


EdgeRequirementInput = Annotated[
    CapabilityRequirementInput | CriterionSetRequirementInput,
    Field(discriminator="kind"),
]


class CompetencyEdgeInput(StrictModel):
    stable_key: str = Field(min_length=1, max_length=255)
    edge_type: Literal[
        "prerequisite", "recommended_before", "supports", "specialization", "related"
    ]
    source_semantic_definition_id: str = Field(min_length=1, max_length=36)
    target_semantic_definition_id: str = Field(min_length=1, max_length=36)
    satisfaction_scope_key: str = Field(default="overall", min_length=1, max_length=255)
    requirement: EdgeRequirementInput | None = None
    provenance: str = Field(min_length=1, max_length=64)
    meaning_key: str = Field(default="default", min_length=1, max_length=255)
    order_index: int = Field(ge=0)

    @model_validator(mode="after")
    def exact_edge_shape(self) -> CompetencyEdgeInput:
        if self.source_semantic_definition_id == self.target_semantic_definition_id:
            raise ValueError("Learning Graph self-edges are prohibited")
        if self.edge_type == "prerequisite" and self.requirement is None:
            raise ValueError("Native prerequisites require an explicit satisfaction requirement")
        if self.edge_type != "prerequisite" and self.requirement is not None:
            raise ValueError("Only prerequisite edges define hard satisfaction requirements")
        return self


class LearningGraphVersionInput(StrictModel):
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    effective_at: datetime
    creation_source: str = Field(default="user", min_length=1, max_length=64)
    edges: list[CompetencyEdgeInput] = Field(default_factory=list)

    @field_validator("effective_at")
    @classmethod
    def aware_effective_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("effective_at must include a timezone offset")
        return value


class LearningGraphActivationInput(StrictModel):
    reason: str = Field(min_length=1, max_length=1000)
    source: str = Field(default="user", min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)


@dataclass(frozen=True)
class CompetencyEdgeProjectionPublicDTO:
    id: str
    edge_identity_id: str
    edge_type: str
    source_competency_identity_id: str
    target_competency_identity_id: str
    source_semantic_definition_id: str
    target_semantic_definition_id: str
    order_index: int


@dataclass(frozen=True)
class CriterionSatisfactionProjectionPublicDTO:
    criterion_definition_id: str
    state: str


@dataclass(frozen=True)
class EdgeSatisfactionProjectionPublicDTO:
    edge_definition_id: str
    edge_identity_id: str
    edge_type: str
    source_competency_identity_id: str
    target_competency_identity_id: str
    source_semantic_definition_id: str
    target_semantic_definition_id: str
    aggregate_state: str
    eligibility_satisfied: bool | None
    capability_state: str
    review_state: str
    criterion_states: tuple[CriterionSatisfactionProjectionPublicDTO, ...]
    unknown_reasons: tuple[str, ...]
    policy_version: str
    cutoff_at: int


@dataclass(frozen=True)
class ActiveLearningGraphProjectionPublicDTO:
    learning_graph_id: str
    learning_graph_version_id: str
    activation_event_id: str
    activation_sequence: int
    content_hash: str
    edges: tuple[CompetencyEdgeProjectionPublicDTO, ...]
    satisfactions: tuple[EdgeSatisfactionProjectionPublicDTO, ...]
