from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas import AssistanceMode, SessionContributionCreate

TODAY_POLICY_VERSION = "today-policy/v2.0"
LEGACY_TODAY_PRESENTATION_VERSION = "today-presentation/v2.0"
TODAY_PRESENTATION_VERSION = "today-presentation/v2.1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TodayContextCost(StrictModel):
    source_type: str = Field(min_length=1, max_length=64)
    source_entity_id: str = Field(min_length=1, max_length=128)
    cost: Literal["none", "low", "medium", "high"]


class TodayGenerationRequest(StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    analysis_snapshot_id: str = Field(min_length=1, max_length=36)
    available_time_ms: int | None = Field(default=None, ge=0)
    context_costs: list[TodayContextCost] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_context_cost_sources(self) -> TodayGenerationRequest:
        keys = [(item.source_type, item.source_entity_id) for item in self.context_costs]
        if len(keys) != len(set(keys)):
            raise ValueError("context_costs must contain each source exactly once")
        return self


class TodayInteractionRequest(StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    reason_code: str | None = Field(default=None, max_length=128)
    feedback: str | None = Field(default=None, max_length=2000)


class TodayStartRequest(StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    assistance_mode: AssistanceMode | None = None
    notes: str | None = Field(default=None, max_length=4000)
    contributions: list[SessionContributionCreate] = Field(default_factory=list)
    assessment_unit_definition_id: str | None = Field(default=None, max_length=36)
    assessment_opportunity_id: str | None = Field(default=None, max_length=36)


class TodayCompletionRequest(StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    session_id: str = Field(min_length=1, max_length=36)
    feedback: str | None = Field(default=None, max_length=2000)


class TodayReplaceRequest(StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    activity_id: str = Field(min_length=1, max_length=36)
    replacement_suggestion_id: str | None = Field(default=None, max_length=36)
    reason_code: str = Field(default="worked_on_something_else", min_length=1, max_length=128)
    feedback: str | None = Field(default=None, max_length=2000)


class SuggestionActivityRelationRequest(StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    activity_id: str = Field(min_length=1, max_length=36)
    relation_type: Literal["matched", "partially_matched"]


class RelationCorrectionRequest(StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    correction_type: Literal["retracted", "replaced"]
    replacement_activity_id: str | None = Field(default=None, max_length=36)
    replacement_relation_type: Literal["matched", "partially_matched", "replaced"] | None = None
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def replacement_shape(self) -> RelationCorrectionRequest:
        replacement = self.replacement_activity_id is not None
        if self.correction_type == "replaced" and (
            not replacement or self.replacement_relation_type is None
        ):
            raise ValueError("A replacement correction requires a replacement Activity relation")
        if self.correction_type == "retracted" and (
            replacement or self.replacement_relation_type is not None
        ):
            raise ValueError("A retraction cannot include a replacement relation")
        return self


class InteractionCorrectionRequest(StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    reason: str = Field(min_length=1, max_length=2000)


class TodayExpirationRequest(StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=80)
