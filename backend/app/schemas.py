from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Status = Literal[
    "not_started", "learning", "practicing", "ready_for_verification", "verified", "needs_review"
]
ActivityType = Literal[
    "learning",
    "reading",
    "practice",
    "coding",
    "debugging",
    "project",
    "review",
    "verification",
    "research",
]
AssistanceMode = Literal["none", "docs_only", "ai_hint", "ai_assisted", "agent_led"]
SessionOutcome = Literal["completed", "partial", "blocked"]
ExportCategory = Literal["roadmap", "analytics", "sessions", "verification", "reports", "settings"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class AuthCredentials(StrictModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=12, max_length=256)


class BootstrapRequest(AuthCredentials):
    bootstrap_token: str = Field(min_length=1, max_length=512)


class AuthResponse(StrictModel):
    user_id: str
    username: str
    csrf_token: str
    absolute_expires_at: str


class PasswordChangeRequest(StrictModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)
    confirm_new_password: str = Field(min_length=12, max_length=256)

    @model_validator(mode="after")
    def passwords_match(self) -> PasswordChangeRequest:
        if self.new_password != self.confirm_new_password:
            raise ValueError("New password confirmation does not match.")
        return self


class AuthSessionResponse(StrictModel):
    id: str
    created_at: str
    last_seen_at: str
    absolute_expires_at: str
    current: bool
    revoked: bool


class ExitCriterionInput(StrictModel):
    stable_key: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1)
    required: bool = True
    weight: int | None = Field(default=None, ge=1, le=5)


class CompetencyInput(StrictModel):
    stable_key: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    goal: str = ""
    priority: Literal["core", "important", "supporting", "optional"]
    weight: int = Field(ge=1, le=5)
    order_index: int = Field(ge=0)
    parent_stable_key: str | None = None
    prerequisite_stable_keys: list[str] = Field(default_factory=list)
    recommended_prerequisite_stable_keys: list[str] = Field(default_factory=list)
    must_understand: list[str] = Field(default_factory=list)
    must_be_able_to: list[str] = Field(default_factory=list)
    exit_criteria: list[ExitCriterionInput] = Field(default_factory=list)
    position_x: int | None = None
    position_y: int | None = None


class TrackInput(StrictModel):
    stable_key: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    order_index: int = Field(ge=0)
    competencies: list[CompetencyInput] = Field(default_factory=list)


class PhaseInput(StrictModel):
    stable_key: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    order_index: int = Field(ge=0)
    tracks: list[TrackInput] = Field(default_factory=list)


class RoadmapCreate(StrictModel):
    stable_key: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    version: str = Field(min_length=1, max_length=64)
    changelog: str = ""
    source: str = "manual"
    phases: list[PhaseInput] = Field(min_length=1)
    current_phase_stable_key: str


class StatusUpdate(StrictModel):
    status: Status
    reason: str = Field(min_length=1, max_length=1000)


class ExitCriterionStateUpdate(StrictModel):
    state: Literal["not_met", "partial", "met"]


class VerificationEvidenceInput(StrictModel):
    kind: str = Field(min_length=1, max_length=64)
    reference: str = Field(min_length=1)
    description: str = ""


class VerificationCreate(StrictModel):
    competency_identity_id: str
    verification_source: Literal["self", "automated", "external"]
    method: str = Field(min_length=1, max_length=255)
    result: Literal["passed", "failed", "partial"]
    confidence: int | None = Field(default=None, ge=0, le=100)
    reviewer_label: str | None = Field(default=None, max_length=255)
    evidence_summary: str | None = None
    notes: str | None = None
    evidence: list[VerificationEvidenceInput] = Field(default_factory=list)


class ManualSessionCreate(StrictModel):
    competency_identity_id: str | None = None
    track_id: str | None = None
    activity_type: ActivityType
    assistance_mode: AssistanceMode
    started_at: datetime
    duration_ms: int = Field(gt=0)
    difficulty: int | None = Field(default=None, ge=1, le=5)
    outcome: SessionOutcome
    notes: str | None = None

    @field_validator("started_at")
    @classmethod
    def exact_instant_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("started_at must include a timezone offset")
        return value


class TimedSessionStart(StrictModel):
    competency_identity_id: str | None = None
    track_id: str | None = None
    activity_type: ActivityType
    assistance_mode: AssistanceMode
    notes: str | None = None


class TimedSessionComplete(StrictModel):
    outcome: SessionOutcome
    difficulty: int | None = Field(default=None, ge=1, le=5)
    notes: str | None = None


class SessionUpdate(StrictModel):
    competency_identity_id: str | None = None
    track_id: str | None = None
    activity_type: ActivityType | None = None
    assistance_mode: AssistanceMode | None = None
    duration_ms: int | None = Field(default=None, gt=0)
    difficulty: int | None = Field(default=None, ge=1, le=5)
    outcome: SessionOutcome | None = None
    notes: str | None = None


class ReflectionUpsert(StrictModel):
    text: str = Field(max_length=100_000)


class DisciplineUpdate(StrictModel):
    weekly_target_active_days: int = Field(ge=1, le=7)
    target_duration_ms_per_active_day: int | None = Field(default=None, gt=0)
    timezone: str

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo

        ZoneInfo(value)
        return value


class RecommendationDecision(StrictModel):
    accepted_primary: bool
    chosen_competency_identity_id: str | None = None


class ExportRequest(StrictModel):
    purpose: Literal["analysis_snapshot", "human_report", "portable_logical_backup"]
    format: Literal["json", "markdown"]
    range: Literal["7d", "30d", "90d", "all", "custom"] = "all"
    start_date: str | None = None
    end_date: str | None = None
    current_phase_only: bool = False
    track_ids: list[str] = Field(default_factory=list)
    competency_identity_ids: list[str] = Field(default_factory=list)
    categories: list[ExportCategory] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_combination(self) -> ExportRequest:
        expected = {
            "analysis_snapshot": "json",
            "human_report": "markdown",
            "portable_logical_backup": "json",
        }[self.purpose]
        if self.format != expected:
            raise ValueError(f"{self.purpose} requires {expected} format")
        if self.range == "custom" and (not self.start_date or not self.end_date):
            raise ValueError("A custom range requires start_date and end_date")
        return self


class ImportEnvelope(StrictModel):
    schemaVersion: int
    packageType: Literal[
        "portable_logical_backup",
        "roadmap_update",
        "roadmap_replace",
        "verification_update",
        "state_update",
        "restore",
    ]
    packageId: str = Field(min_length=1, max_length=255)
    appVersion: str
    createdAt: str
    payload: dict[str, Any]


class RoadmapPackagePayload(StrictModel):
    roadmap: RoadmapCreate


class VerificationUpdatePayload(StrictModel):
    verifications: list[VerificationCreate] = Field(default_factory=list)


class StateUpdateItem(StrictModel):
    competency_identity_id: str
    status: Status
    reason: str = Field(default="Imported status update", min_length=1, max_length=1000)
    verification: VerificationCreate | None = None

    @model_validator(mode="after")
    def validate_verification(self) -> StateUpdateItem:
        if self.status == "verified":
            if (
                self.verification is None
                or self.verification.result != "passed"
                or self.verification.competency_identity_id != self.competency_identity_id
            ):
                raise ValueError("verified status requires a matching passed verification record")
        elif self.verification is not None:
            raise ValueError("verification is only supported for verified state updates")
        return self


class StateUpdatePayload(StrictModel):
    states: list[StateUpdateItem] = Field(default_factory=list)


class PortablePackagePayload(StrictModel):
    tables: dict[str, list[dict[str, Any]]]


class ImportInspectRequest(StrictModel):
    filename: str = Field(min_length=1, max_length=512)
    package: ImportEnvelope


class ImportApplyRequest(ImportInspectRequest):
    confirmation_token: str
    replace_existing: bool = False


class IdResponse(StrictModel):
    id: str
