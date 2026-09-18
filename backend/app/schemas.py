from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal
from urllib.parse import parse_qsl, urlsplit

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


_SENSITIVE_REFERENCE_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "credential",
    "key",
    "password",
    "secret",
    "signature",
    "token",
}


def validate_external_reference(value: str | None) -> str | None:
    if value is None:
        return None
    if "bearer " in value.lower():
        raise ValueError("External references must not contain credentials.")
    parsed = urlsplit(value)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("External references must not contain URL user information.")
    if any(key.lower() in _SENSITIVE_REFERENCE_KEYS for key, _value in parse_qsl(parsed.query)):
        raise ValueError("External references must not contain credential query parameters.")
    return value


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


class CompetencyIdentityCreate(StrictModel):
    stable_key: str = Field(min_length=1, max_length=255)
    creation_source: str = Field(min_length=1, max_length=64)


DemonstrationRule = Literal[
    "exposure",
    "guided_performance",
    "independent_performance",
    "repeated_independent_performance",
    "authoritative_assessment",
]


class CriterionDefinitionInput(StrictModel):
    stable_key: str = Field(min_length=1, max_length=255)
    level_stable_key: str = Field(min_length=1, max_length=64)
    dimension_key: str | None
    requirement_type: Literal["required", "important", "supporting"]
    demonstration_rule: DemonstrationRule
    description: str = Field(min_length=1)
    verification_rubric: str | None = None
    importance_weight: int | None = Field(default=None, ge=1, le=5)


class SemanticCompetencyDefinitionCreate(StrictModel):
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    scope: str = Field(min_length=1)
    scale_stable_key: Literal["technical", "cefr"]
    scale_version: str = Field(min_length=1, max_length=64)
    dimension_keys: list[str] = Field(default_factory=list)
    effective_at: datetime
    creation_source: str = Field(min_length=1, max_length=64)
    freshness_current_through_days: int | None = Field(default=None, ge=0)
    freshness_stale_after_days: int | None = Field(default=None, ge=0)
    criteria: list[CriterionDefinitionInput] = Field(min_length=1)

    @model_validator(mode="after")
    def freshness_override_is_complete(self) -> SemanticCompetencyDefinitionCreate:
        current = self.freshness_current_through_days
        stale = self.freshness_stale_after_days
        if (current is None) != (stale is None) or (
            current is not None and stale is not None and stale < current
        ):
            raise ValueError(
                "Freshness overrides require an ordered current-through and stale-after pair"
            )
        return self

    @field_validator("effective_at")
    @classmethod
    def effective_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("effective_at must include a timezone offset")
        return value


class ActivationRequest(StrictModel):
    reason: str = Field(min_length=1, max_length=1000)
    source: str = Field(default="user", min_length=1, max_length=64)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class ProfileDomainInput(StrictModel):
    stable_key: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    minimum_percent: int | None = Field(default=None, ge=0, le=100)
    maximum_percent: int | None = Field(default=None, ge=0, le=100)
    order_index: int = Field(ge=0)

    @model_validator(mode="after")
    def range_is_ordered(self) -> ProfileDomainInput:
        if (
            self.minimum_percent is not None
            and self.maximum_percent is not None
            and self.minimum_percent > self.maximum_percent
        ):
            raise ValueError("minimum_percent cannot exceed maximum_percent")
        return self


class ProfileTargetInput(StrictModel):
    stable_key: str = Field(min_length=1, max_length=255)
    competency_identity_id: str
    dimension_key: str | None
    domain_stable_key: str
    scale_stable_key: Literal["technical", "cefr"]
    scale_version: str = Field(min_length=1, max_length=64)
    target_level_stable_key: str = Field(min_length=1, max_length=64)
    priority: Literal["critical", "core", "important", "supporting", "optional"]
    target_date: str | None = None
    target_month: str | None = None
    date_interpretation: str | None = None
    freshness_override_days: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def date_precision_is_explicit(self) -> ProfileTargetInput:
        if self.target_date is not None and self.target_month is not None:
            raise ValueError("Specify target_date or target_month, not both")
        if self.target_date is not None:
            try:
                date.fromisoformat(self.target_date)
            except ValueError as exc:
                raise ValueError("target_date must be a valid YYYY-MM-DD date") from exc
        if self.target_month is not None and not re.fullmatch(
            r"\d{4}-(0[1-9]|1[0-2])", self.target_month
        ):
            raise ValueError("target_month must be a valid YYYY-MM month")
        dated = self.target_date is not None or self.target_month is not None
        if (dated and not self.date_interpretation) or (
            not dated and self.date_interpretation is not None
        ):
            raise ValueError(
                "Dated targets require date_interpretation, and undated targets omit it"
            )
        return self


class ProfileMilestoneInput(StrictModel):
    stable_key: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    target_date: str | None = None
    order_index: int = Field(ge=0)
    target_stable_keys: list[str] = Field(default_factory=list)

    @field_validator("target_date")
    @classmethod
    def target_date_is_valid(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("target_date must be a valid YYYY-MM-DD date") from exc
        return value


class ReadinessPredicateInput(StrictModel):
    predicate_type: Literal[
        "capability_at_least",
        "criterion_demonstrated",
        "project_criterion_demonstrated",
        "evidence_present",
    ]
    requirement_type: Literal["required", "supporting"]
    order_index: int = Field(ge=0)
    subject: dict[str, Any]


class ReadinessGateInput(StrictModel):
    stable_key: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    effect: Literal["hard_eligibility", "urgency", "display_only"] = "urgency"
    order_index: int = Field(ge=0)
    milestone_stable_key: str | None = None
    target_stable_keys: list[str] = Field(default_factory=list)
    predicates: list[ReadinessPredicateInput] = Field(default_factory=list)


class TargetProfileVersionCreate(StrictModel):
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    creation_source: str = Field(min_length=1, max_length=64)
    effective_at: datetime
    domains: list[ProfileDomainInput] = Field(min_length=1)
    targets: list[ProfileTargetInput] = Field(min_length=1)
    milestones: list[ProfileMilestoneInput] = Field(default_factory=list)
    readiness_gates: list[ReadinessGateInput] = Field(default_factory=list)

    @field_validator("effective_at")
    @classmethod
    def profile_effective_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("effective_at must include a timezone offset")
        return value


class TargetProfileCreate(StrictModel):
    stable_key: str = Field(min_length=1, max_length=255)
    creation_source: str = Field(min_length=1, max_length=64)
    version: TargetProfileVersionCreate


class VerificationEvidenceInput(StrictModel):
    kind: str = Field(min_length=1, max_length=64)
    reference: str = Field(min_length=1)
    description: str = ""

    _reference_is_safe = field_validator("reference")(validate_external_reference)


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


class ActivityCreate(StrictModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    category_stable_key: ActivityType
    occurred_at: datetime | None = None
    outcome_classification: str | None = Field(default=None, max_length=64)

    @field_validator("occurred_at")
    @classmethod
    def activity_instant_is_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("occurred_at must include a timezone offset")
        return value


class SessionContributionCreate(StrictModel):
    target_type: Literal["competency", "project"] = "competency"
    competency_identity_id: str | None = None
    project_id: str | None = None
    criterion_identity_id: str | None = None
    relevance: Literal["primary", "secondary", "supporting"]
    provenance: Literal["user_selected", "user_confirmed"] = "user_selected"

    @model_validator(mode="after")
    def contribution_target_is_explicit(self) -> SessionContributionCreate:
        if self.target_type == "project":
            if self.project_id is None or self.competency_identity_id is not None:
                raise ValueError("A project contribution requires only project_id.")
        elif self.competency_identity_id is None or self.project_id is not None:
            raise ValueError("A competency contribution requires only competency_identity_id.")
        return self


class V2ManualSessionCreate(StrictModel):
    activity_id: str
    assistance_mode: AssistanceMode
    started_at: datetime
    duration_ms: int = Field(gt=0)
    difficulty: int | None = Field(default=None, ge=1, le=5)
    outcome: SessionOutcome
    notes: str | None = None
    contributions: list[SessionContributionCreate] = Field(default_factory=list)

    @field_validator("started_at")
    @classmethod
    def exact_instant_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("started_at must include a timezone offset")
        return value


class V2TimedSessionStart(StrictModel):
    activity_id: str
    assistance_mode: AssistanceMode
    notes: str | None = None
    contributions: list[SessionContributionCreate] = Field(default_factory=list)


class EvidenceLinkCreate(StrictModel):
    competency_identity_id: str
    criterion_identity_id: str | None = None
    criterion_definition_id: str | None = None
    scale_version_id: str | None = None
    dimension_id: str | None = None
    level_id: str | None = None
    effect: Literal["supports", "contradicts", "context_only"] = "supports"
    relevance: Literal["primary", "secondary", "supporting"] = "primary"


class EvidenceLinkCommand(EvidenceLinkCreate):
    idempotency_key: str = Field(min_length=8, max_length=255)


class EvidenceCreate(StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=255)
    evidence_type: Literal["code", "assessment", "manual", "review", "project"]
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    strength: Literal["unknown", "weak", "moderate", "strong"]
    strength_unknown_reason: Literal["user_unspecified"] | None = None
    independence: Literal["unknown", "guided", "assisted", "independent", "not_applicable"]
    independence_unknown_reason: Literal["user_unspecified"] | None = None
    occurred_at: datetime | None = None
    occurred_at_unknown_reason: Literal["user_unspecified"] | None = None
    capture_method: str = Field(min_length=1, max_length=128)
    artifact_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    external_reference: str | None = None
    authoritative_reassessment: bool = False
    maximum_supported_level_id: str | None = None
    links: list[EvidenceLinkCreate] = Field(min_length=1)

    _external_reference_is_safe = field_validator("external_reference")(validate_external_reference)

    @model_validator(mode="after")
    def explicit_unknowns_and_occurrence(self) -> EvidenceCreate:
        if (self.strength == "unknown") != (self.strength_unknown_reason is not None):
            raise ValueError("Unknown strength requires exactly one unknown reason.")
        if (self.independence == "unknown") != (self.independence_unknown_reason is not None):
            raise ValueError("Unknown independence requires exactly one unknown reason.")
        if (self.occurred_at is None) != (self.occurred_at_unknown_reason is not None):
            raise ValueError("Unknown occurrence time requires exactly one unknown reason.")
        if self.occurred_at is not None and (
            self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None
        ):
            raise ValueError("occurred_at must include a timezone offset")
        if self.authoritative_reassessment != (self.maximum_supported_level_id is not None):
            raise ValueError(
                "Authoritative reassessment requires exactly one maximum supported level."
            )
        return self


class EvidenceLifecycleRequest(StrictModel):
    reason: str = Field(min_length=1, max_length=10_000)


class EvidenceRedactionRequest(EvidenceLifecycleRequest):
    redacted_fields: list[Literal["description", "external_reference"]] = Field(min_length=1)


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
    manifest: dict[str, Any] | None = None
    capabilityProjectionCheckpoints: list[dict[str, Any]] = Field(default_factory=list)
    curriculumCatalogCheckpoint: dict[str, Any] | None = None


class ImportInspectRequest(StrictModel):
    filename: str = Field(min_length=1, max_length=512)
    package: ImportEnvelope


class ImportApplyRequest(ImportInspectRequest):
    confirmation_token: str
    replace_existing: bool = False


class IdResponse(StrictModel):
    id: str
