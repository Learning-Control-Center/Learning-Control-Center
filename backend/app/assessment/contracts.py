from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas import AssistanceMode, validate_external_reference


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssessmentCorroborationSelection(StrictModel):
    kind: Literal["server_stored_artifact"]
    artifact_id: str = Field(min_length=1, max_length=36)


class AssessmentObservationInput(StrictModel):
    criterion_definition_id: str = Field(min_length=1, max_length=36)
    state: Literal["demonstrated", "partial", "contradicted", "unobserved"]
    task_setup: str | None = Field(default=None, max_length=4000)
    expected_result: str | None = Field(default=None, max_length=4000)
    observed_output: str | None = Field(default=None, max_length=12000)
    comparison: str | None = Field(default=None, max_length=4000)
    artifact_content: str | None = Field(default=None, max_length=20000)
    artifact_reference: str | None = Field(default=None, max_length=2000)
    corroboration: AssessmentCorroborationSelection | None = None
    additional_assistance_mode: AssistanceMode | None = None

    _reference_is_safe = field_validator("artifact_reference")(validate_external_reference)


class AssessmentAttestation(StrictModel):
    actual_session: bool
    assistance_complete: bool
    outputs_authentic: bool
    review_truthful: bool


class AssessmentReviewRequest(StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    supersedes_review_id: str | None = Field(default=None, max_length=36)
    correction_reason: str | None = Field(default=None, max_length=2000)
    observations: list[AssessmentObservationInput] = Field(min_length=1)
    attestation: AssessmentAttestation
