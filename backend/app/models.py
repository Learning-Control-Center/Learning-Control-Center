from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.time_utils import utc_now_ms


def new_id() -> str:
    return str(uuid.uuid4())


class TimestampMixin:
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    updated_at: Mapped[int] = mapped_column(
        Integer, default=utc_now_ms, onupdate=utc_now_ms, nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("singleton_key = 1", name="ck_single_user"),
        CheckConstraint("credential_generation > 0", name="ck_user_credential_generation_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    singleton_key: Mapped[int] = mapped_column(Integer, unique=True, default=1, nullable=False)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    credential_generation: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    password_changed_at: Mapped[int | None] = mapped_column(Integer)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint(
            "credential_generation > 0",
            name="ck_auth_session_credential_generation_positive",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_lookup_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    last_seen_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    absolute_expires_at: Mapped[int] = mapped_column(Integer, nullable=False)
    revoked_at: Mapped[int | None] = mapped_column(Integer)
    credential_generation: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class SecurityAuditEvent(Base):
    __tablename__ = "security_audit_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_security_audit_idempotency"),
        Index("ix_security_audit_event_time", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    auth_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="SET NULL")
    )
    actor_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    client_key_hash: Mapped[str | None] = mapped_column(String(64))
    details_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    occurred_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))


class AuthRateLimitBucket(Base):
    __tablename__ = "auth_rate_limit_buckets"
    __table_args__ = (
        UniqueConstraint(
            "namespace", "client_key_hash", "window_started_at", name="uq_auth_rate_bucket"
        ),
        CheckConstraint("failure_count >= 0", name="ck_auth_rate_failure_count"),
        Index("ix_auth_rate_bucket_cleanup", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    namespace: Mapped[str] = mapped_column(String(64), nullable=False)
    client_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    window_started_at: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    blocked_until: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)


class Roadmap(Base, TimestampMixin):
    __tablename__ = "roadmaps"
    __table_args__ = (
        Index(
            "uq_one_current_roadmap", "is_current", unique=True, sqlite_where=text("is_current = 1")
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    stable_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active_version_id: Mapped[str | None] = mapped_column(ForeignKey("roadmap_versions.id"))
    current_phase_id: Mapped[str | None] = mapped_column(ForeignKey("phases.id"))


class RoadmapVersion(Base):
    __tablename__ = "roadmap_versions"
    __table_args__ = (UniqueConstraint("roadmap_id", "version", name="uq_roadmap_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    roadmap_id: Mapped[str] = mapped_column(
        ForeignKey("roadmaps.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    changelog: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="manual", nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)


class Phase(Base):
    __tablename__ = "phases"
    __table_args__ = (
        UniqueConstraint("roadmap_version_id", "stable_key", name="uq_phase_stable_key_version"),
        UniqueConstraint("roadmap_version_id", "order_index", name="uq_phase_order_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    roadmap_version_id: Mapped[str] = mapped_column(
        ForeignKey("roadmap_versions.id", ondelete="RESTRICT"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Track(Base):
    __tablename__ = "tracks"
    __table_args__ = (
        UniqueConstraint("roadmap_version_id", "stable_key", name="uq_track_stable_key_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    roadmap_version_id: Mapped[str] = mapped_column(
        ForeignKey("roadmap_versions.id", ondelete="RESTRICT"), nullable=False
    )
    phase_id: Mapped[str] = mapped_column(
        ForeignKey("phases.id", ondelete="RESTRICT"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)


class RoadmapScopeEvent(Base):
    __tablename__ = "roadmap_scope_events"
    __table_args__ = (
        UniqueConstraint("event_sequence", name="uq_roadmap_scope_event_sequence"),
        CheckConstraint("event_sequence > 0", name="ck_roadmap_scope_event_sequence_positive"),
        Index(
            "ix_roadmap_scope_event_time_sequence",
            "occurred_at",
            "event_sequence",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    roadmap_id: Mapped[str] = mapped_column(
        ForeignKey("roadmaps.id", ondelete="RESTRICT"), nullable=False
    )
    roadmap_version_id: Mapped[str] = mapped_column(
        ForeignKey("roadmap_versions.id", ondelete="RESTRICT"), nullable=False
    )
    phase_id: Mapped[str] = mapped_column(
        ForeignKey("phases.id", ondelete="RESTRICT"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False)


class CompetencyIdentity(Base):
    __tablename__ = "competency_identities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    stable_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    identity_created_at: Mapped[int | None] = mapped_column(Integer)
    creation_source: Mapped[str | None] = mapped_column(String(64))
    legacy_unspecified_reason: Mapped[str | None] = mapped_column(Text)
    retired_at: Mapped[int | None] = mapped_column(Integer)
    retirement_reason: Mapped[str | None] = mapped_column(Text)


class CapabilityScaleVersion(Base):
    __tablename__ = "capability_scale_versions"
    __table_args__ = (
        UniqueConstraint("scale_stable_key", "scale_version", name="uq_capability_scale_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scale_stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    scale_version: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)


class CapabilityScaleDimension(Base):
    __tablename__ = "capability_scale_dimensions"
    __table_args__ = (
        UniqueConstraint("scale_version_id", "stable_key", name="uq_scale_dimension_key"),
        UniqueConstraint("scale_version_id", "order_index", name="uq_scale_dimension_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scale_version_id: Mapped[str] = mapped_column(
        ForeignKey("capability_scale_versions.id", ondelete="RESTRICT"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    display_label: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)


class CapabilityScaleLevel(Base):
    __tablename__ = "capability_scale_levels"
    __table_args__ = (
        UniqueConstraint("scale_version_id", "stable_key", name="uq_scale_level_key"),
        UniqueConstraint("scale_version_id", "ordinal_rank", name="uq_scale_level_rank"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scale_version_id: Mapped[str] = mapped_column(
        ForeignKey("capability_scale_versions.id", ondelete="RESTRICT"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    display_label: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    criterion_policy_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_policy_reference: Mapped[str] = mapped_column(String(128), nullable=False)


class TargetProfile(Base):
    __tablename__ = "target_profiles"
    __table_args__ = (UniqueConstraint("stable_key", name="uq_target_profile_stable_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    creation_source: Mapped[str] = mapped_column(String(64), nullable=False)
    retired_at: Mapped[int | None] = mapped_column(Integer)
    retirement_reason: Mapped[str | None] = mapped_column(Text)


class TargetProfileVersion(Base):
    __tablename__ = "target_profile_versions"
    __table_args__ = (
        UniqueConstraint("target_profile_id", "version", name="uq_target_profile_version"),
        CheckConstraint("version > 0", name="ck_target_profile_version_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    target_profile_id: Mapped[str] = mapped_column(
        ForeignKey("target_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    effective_at: Mapped[int] = mapped_column(Integer, nullable=False)
    creation_source: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("target_profile_versions.id", ondelete="RESTRICT")
    )


class ProfileDomain(Base):
    __tablename__ = "profile_domains"
    __table_args__ = (
        UniqueConstraint("profile_version_id", "stable_key", name="uq_profile_domain_key"),
        UniqueConstraint("profile_version_id", "order_index", name="uq_profile_domain_order"),
        CheckConstraint(
            "minimum_percent IS NULL OR minimum_percent BETWEEN 0 AND 100",
            name="ck_profile_domain_minimum",
        ),
        CheckConstraint(
            "maximum_percent IS NULL OR maximum_percent BETWEEN 0 AND 100",
            name="ck_profile_domain_maximum",
        ),
        CheckConstraint(
            "minimum_percent IS NULL OR maximum_percent IS NULL OR "
            "minimum_percent <= maximum_percent",
            name="ck_profile_domain_range",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    profile_version_id: Mapped[str] = mapped_column(
        ForeignKey("target_profile_versions.id", ondelete="RESTRICT"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    minimum_percent: Mapped[int | None] = mapped_column(Integer)
    maximum_percent: Mapped[int | None] = mapped_column(Integer)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)


class ProfileTargetIdentity(Base):
    __tablename__ = "profile_target_identities"
    __table_args__ = (
        UniqueConstraint("target_profile_id", "stable_key", name="uq_profile_target_identity_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    target_profile_id: Mapped[str] = mapped_column(
        ForeignKey("target_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    competency_identity_id: Mapped[str] = mapped_column(
        ForeignKey("competency_identities.id", ondelete="RESTRICT"), nullable=False
    )
    dimension_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)


class ProfileTarget(Base):
    __tablename__ = "profile_targets"
    __table_args__ = (
        UniqueConstraint(
            "profile_version_id", "target_identity_id", name="uq_profile_target_version_identity"
        ),
        CheckConstraint(
            "priority IN ('critical','core','important','supporting','optional')",
            name="ck_profile_target_priority",
        ),
        CheckConstraint(
            "(target_date IS NULL) != (target_month IS NULL) OR "
            "(target_date IS NULL AND target_month IS NULL)",
            name="ck_profile_target_date_precision",
        ),
        CheckConstraint(
            "freshness_override_days IS NULL OR freshness_override_days > 0",
            name="ck_profile_target_freshness",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    profile_version_id: Mapped[str] = mapped_column(
        ForeignKey("target_profile_versions.id", ondelete="RESTRICT"), nullable=False
    )
    target_identity_id: Mapped[str] = mapped_column(
        ForeignKey("profile_target_identities.id", ondelete="RESTRICT"), nullable=False
    )
    profile_domain_id: Mapped[str] = mapped_column(
        ForeignKey("profile_domains.id", ondelete="RESTRICT"), nullable=False
    )
    scale_version_id: Mapped[str] = mapped_column(
        ForeignKey("capability_scale_versions.id", ondelete="RESTRICT"), nullable=False
    )
    target_level_id: Mapped[str] = mapped_column(
        ForeignKey("capability_scale_levels.id", ondelete="RESTRICT"), nullable=False
    )
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    target_date: Mapped[str | None] = mapped_column(String(10))
    target_month: Mapped[str | None] = mapped_column(String(7))
    date_interpretation: Mapped[str | None] = mapped_column(Text)
    freshness_override_days: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    creation_source: Mapped[str] = mapped_column(String(64), nullable=False)


class MilestoneIdentity(Base):
    __tablename__ = "milestone_identities"
    __table_args__ = (
        UniqueConstraint("target_profile_id", "stable_key", name="uq_milestone_identity_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    target_profile_id: Mapped[str] = mapped_column(
        ForeignKey("target_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)


class ProfileMilestone(Base):
    __tablename__ = "profile_milestones"
    __table_args__ = (
        UniqueConstraint(
            "profile_version_id", "milestone_identity_id", name="uq_profile_milestone_identity"
        ),
        UniqueConstraint("profile_version_id", "order_index", name="uq_profile_milestone_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    profile_version_id: Mapped[str] = mapped_column(
        ForeignKey("target_profile_versions.id", ondelete="RESTRICT"), nullable=False
    )
    milestone_identity_id: Mapped[str] = mapped_column(
        ForeignKey("milestone_identities.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    target_date: Mapped[str | None] = mapped_column(String(10))
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)


class ProfileMilestoneTarget(Base):
    __tablename__ = "profile_milestone_targets"

    milestone_id: Mapped[str] = mapped_column(
        ForeignKey("profile_milestones.id", ondelete="RESTRICT"), primary_key=True
    )
    profile_target_id: Mapped[str] = mapped_column(
        ForeignKey("profile_targets.id", ondelete="RESTRICT"), primary_key=True
    )


class ReadinessGateIdentity(Base):
    __tablename__ = "readiness_gate_identities"
    __table_args__ = (
        UniqueConstraint("target_profile_id", "stable_key", name="uq_readiness_gate_identity_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    target_profile_id: Mapped[str] = mapped_column(
        ForeignKey("target_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)


class ReadinessGate(Base):
    __tablename__ = "readiness_gates"
    __table_args__ = (
        UniqueConstraint(
            "profile_version_id", "gate_identity_id", name="uq_readiness_gate_identity"
        ),
        CheckConstraint(
            "effect IN ('hard_eligibility','urgency','display_only')",
            name="ck_readiness_gate_effect",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    profile_version_id: Mapped[str] = mapped_column(
        ForeignKey("target_profile_versions.id", ondelete="RESTRICT"), nullable=False
    )
    milestone_id: Mapped[str | None] = mapped_column(
        ForeignKey("profile_milestones.id", ondelete="RESTRICT")
    )
    gate_identity_id: Mapped[str] = mapped_column(
        ForeignKey("readiness_gate_identities.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    effect: Mapped[str] = mapped_column(String(32), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)


class ReadinessGatePredicate(Base):
    __tablename__ = "readiness_gate_predicates"
    __table_args__ = (
        UniqueConstraint("gate_id", "order_index", name="uq_gate_predicate_order"),
        CheckConstraint(
            "predicate_type IN ('capability_at_least','criterion_demonstrated',"
            "'project_criterion_demonstrated','evidence_present')",
            name="ck_gate_predicate_type",
        ),
        CheckConstraint(
            "requirement_type IN ('required','supporting')",
            name="ck_gate_predicate_requirement",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    gate_id: Mapped[str] = mapped_column(
        ForeignKey("readiness_gates.id", ondelete="RESTRICT"), nullable=False
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    predicate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    requirement_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_json: Mapped[str] = mapped_column(Text, nullable=False)


class ReadinessGateTarget(Base):
    __tablename__ = "readiness_gate_targets"

    gate_id: Mapped[str] = mapped_column(
        ForeignKey("readiness_gates.id", ondelete="RESTRICT"), primary_key=True
    )
    profile_target_id: Mapped[str] = mapped_column(
        ForeignKey("profile_targets.id", ondelete="RESTRICT"), primary_key=True
    )


class ActiveTargetProfileState(Base):
    __tablename__ = "active_target_profile_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_active_target_profile_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    target_profile_id: Mapped[str] = mapped_column(
        ForeignKey("target_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    target_profile_version_id: Mapped[str] = mapped_column(
        ForeignKey("target_profile_versions.id", ondelete="RESTRICT"), nullable=False
    )
    activated_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)


class TargetProfileActivationEvent(Base):
    __tablename__ = "target_profile_activation_events"
    __table_args__ = (
        UniqueConstraint("event_sequence", name="uq_target_profile_activation_sequence"),
        UniqueConstraint("idempotency_key", name="uq_target_profile_activation_idempotency"),
        CheckConstraint("event_sequence > 0", name="ck_target_profile_activation_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    from_profile_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("target_profile_versions.id", ondelete="RESTRICT")
    )
    to_profile_version_id: Mapped[str] = mapped_column(
        ForeignKey("target_profile_versions.id", ondelete="RESTRICT"), nullable=False
    )
    activated_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


class SemanticCompetencyDefinition(Base):
    __tablename__ = "semantic_competency_definitions"
    __table_args__ = (
        UniqueConstraint(
            "competency_identity_id", "definition_version", name="uq_semantic_definition_version"
        ),
        CheckConstraint("definition_version > 0", name="ck_semantic_definition_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    competency_identity_id: Mapped[str] = mapped_column(
        ForeignKey("competency_identities.id", ondelete="RESTRICT"), nullable=False
    )
    definition_version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    scale_version_id: Mapped[str] = mapped_column(
        ForeignKey("capability_scale_versions.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    creation_source: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_at: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("semantic_competency_definitions.id", ondelete="RESTRICT")
    )


class SemanticDefinitionDimension(Base):
    __tablename__ = "semantic_definition_dimensions"

    semantic_definition_id: Mapped[str] = mapped_column(
        ForeignKey("semantic_competency_definitions.id", ondelete="RESTRICT"), primary_key=True
    )
    scale_dimension_id: Mapped[str] = mapped_column(
        ForeignKey("capability_scale_dimensions.id", ondelete="RESTRICT"), primary_key=True
    )


class CriterionIdentity(Base):
    __tablename__ = "criterion_identities"
    __table_args__ = (
        UniqueConstraint("competency_identity_id", "stable_key", name="uq_criterion_identity_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    competency_identity_id: Mapped[str] = mapped_column(
        ForeignKey("competency_identities.id", ondelete="RESTRICT"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[int | None] = mapped_column(Integer)
    creation_source: Mapped[str | None] = mapped_column(String(64))
    legacy_unspecified_reason: Mapped[str | None] = mapped_column(Text)
    retired_at: Mapped[int | None] = mapped_column(Integer)
    retirement_reason: Mapped[str | None] = mapped_column(Text)


class CriterionDefinition(Base):
    __tablename__ = "criterion_definitions"
    __table_args__ = (
        UniqueConstraint(
            "criterion_identity_id", "definition_version", name="uq_criterion_definition_version"
        ),
        CheckConstraint("definition_version > 0", name="ck_criterion_definition_version"),
        CheckConstraint(
            "requirement_type IN ('required','important','supporting')",
            name="ck_criterion_requirement_type",
        ),
        CheckConstraint(
            "importance_weight IS NULL OR importance_weight BETWEEN 1 AND 5",
            name="ck_criterion_importance_weight",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    criterion_identity_id: Mapped[str] = mapped_column(
        ForeignKey("criterion_identities.id", ondelete="RESTRICT"), nullable=False
    )
    semantic_definition_id: Mapped[str] = mapped_column(
        ForeignKey("semantic_competency_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    definition_version: Mapped[int] = mapped_column(Integer, nullable=False)
    level_id: Mapped[str] = mapped_column(
        ForeignKey("capability_scale_levels.id", ondelete="RESTRICT"), nullable=False
    )
    dimension_id: Mapped[str | None] = mapped_column(
        ForeignKey("capability_scale_dimensions.id", ondelete="RESTRICT")
    )
    requirement_type: Mapped[str] = mapped_column(String(16), nullable=False)
    demonstration_rule_json: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    verification_rubric: Mapped[str | None] = mapped_column(Text)
    importance_weight: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    supersedes_definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("criterion_definitions.id", ondelete="RESTRICT")
    )


class ActiveCompetencyDefinitionState(Base):
    __tablename__ = "active_competency_definition_states"

    competency_identity_id: Mapped[str] = mapped_column(
        ForeignKey("competency_identities.id", ondelete="RESTRICT"), primary_key=True
    )
    semantic_definition_id: Mapped[str] = mapped_column(
        ForeignKey("semantic_competency_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    activated_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)


class CompetencyDefinitionActivationEvent(Base):
    __tablename__ = "competency_definition_activation_events"
    __table_args__ = (
        UniqueConstraint("event_sequence", name="uq_competency_definition_activation_sequence"),
        UniqueConstraint("idempotency_key", name="uq_competency_definition_activation_idempotency"),
        CheckConstraint("event_sequence > 0", name="ck_competency_definition_activation_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    competency_identity_id: Mapped[str] = mapped_column(
        ForeignKey("competency_identities.id", ondelete="RESTRICT"), nullable=False
    )
    from_definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("semantic_competency_definitions.id", ondelete="RESTRICT")
    )
    to_definition_id: Mapped[str] = mapped_column(
        ForeignKey("semantic_competency_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    activated_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


class LegacyCriterionAssertion(Base):
    __tablename__ = "legacy_criterion_assertions"
    __table_args__ = (
        CheckConstraint(
            "legacy_state IN ('not_met','partial','met')", name="ck_legacy_criterion_state"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    criterion_identity_id: Mapped[str] = mapped_column(
        ForeignKey("criterion_identities.id", ondelete="RESTRICT"), nullable=False
    )
    source_exit_criterion_identity_id: Mapped[str] = mapped_column(
        ForeignKey("exit_criterion_identities.id", ondelete="RESTRICT"), nullable=False
    )
    source_exit_criterion_definition_id: Mapped[str] = mapped_column(
        ForeignKey("exit_criterion_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    legacy_state: Mapped[str] = mapped_column(String(16), nullable=False)
    requirement_type: Mapped[str | None] = mapped_column(String(16))
    demonstration_rule: Mapped[str | None] = mapped_column(String(64))
    evidence_strength: Mapped[str | None] = mapped_column(String(32))
    independence: Mapped[str | None] = mapped_column(String(32))
    source_confidence: Mapped[str | None] = mapped_column(String(32))
    legacy_unspecified_reason: Mapped[str] = mapped_column(Text, nullable=False)
    asserted_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    cutoff_at: Mapped[int] = mapped_column(Integer, nullable=False)
    provenance: Mapped[str] = mapped_column(String(64), nullable=False)


class MigrationBackfillRun(Base):
    __tablename__ = "migration_backfill_runs"
    __table_args__ = (
        UniqueConstraint("policy_key", "source_kind", name="uq_migration_backfill_policy_source"),
        CheckConstraint("source_row_count >= 0", name="ck_migration_backfill_source_count"),
        CheckConstraint("result_row_count >= 0", name="ck_migration_backfill_result_count"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    policy_key: Mapped[str] = mapped_column(String(128), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(128), nullable=False)
    source_row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    result_row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[int] = mapped_column(Integer, nullable=False)


def _reject_v2_semantic_history_mutation(*_args: object) -> None:
    raise ValueError("V2 semantic version and assertion history is immutable.")


for _immutable_v2_model in (
    CapabilityScaleVersion,
    CapabilityScaleDimension,
    CapabilityScaleLevel,
    TargetProfileVersion,
    ProfileDomain,
    ProfileTarget,
    MilestoneIdentity,
    ProfileMilestone,
    ProfileMilestoneTarget,
    ReadinessGate,
    ReadinessGateIdentity,
    ReadinessGatePredicate,
    ReadinessGateTarget,
    TargetProfileActivationEvent,
    SemanticCompetencyDefinition,
    SemanticDefinitionDimension,
    CriterionDefinition,
    CompetencyDefinitionActivationEvent,
    LegacyCriterionAssertion,
    MigrationBackfillRun,
):
    event.listen(_immutable_v2_model, "before_update", _reject_v2_semantic_history_mutation)
    event.listen(_immutable_v2_model, "before_delete", _reject_v2_semantic_history_mutation)


class CompetencyDefinition(Base, TimestampMixin):
    __tablename__ = "competency_definitions"
    __table_args__ = (
        UniqueConstraint(
            "roadmap_version_id", "competency_identity_id", name="uq_competency_identity_version"
        ),
        CheckConstraint("weight BETWEEN 1 AND 5", name="ck_competency_weight"),
        CheckConstraint(
            "priority IN ('core','important','supporting','optional')",
            name="ck_competency_priority",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    competency_identity_id: Mapped[str] = mapped_column(
        ForeignKey("competency_identities.id", ondelete="RESTRICT"), nullable=False
    )
    roadmap_version_id: Mapped[str] = mapped_column(
        ForeignKey("roadmap_versions.id", ondelete="RESTRICT"), nullable=False
    )
    phase_id: Mapped[str] = mapped_column(
        ForeignKey("phases.id", ondelete="RESTRICT"), nullable=False
    )
    track_id: Mapped[str] = mapped_column(
        ForeignKey("tracks.id", ondelete="RESTRICT"), nullable=False
    )
    parent_definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("competency_definitions.id")
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    goal: Mapped[str] = mapped_column(Text, default="", nullable=False)
    priority: Mapped[str] = mapped_column(String(32), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    position_x: Mapped[int | None] = mapped_column(Integer)
    position_y: Mapped[int | None] = mapped_column(Integer)


class CompetencyPrerequisite(Base):
    __tablename__ = "competency_prerequisites"
    __table_args__ = (
        UniqueConstraint(
            "competency_definition_id",
            "prerequisite_competency_identity_id",
            name="uq_competency_prerequisite",
        ),
        CheckConstraint("kind IN ('required','recommended')", name="ck_prerequisite_kind"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    competency_definition_id: Mapped[str] = mapped_column(
        ForeignKey("competency_definitions.id", ondelete="CASCADE"), nullable=False
    )
    prerequisite_competency_identity_id: Mapped[str] = mapped_column(
        ForeignKey("competency_identities.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)


class CompetencyUnderstandingItem(Base):
    __tablename__ = "competency_understanding_items"
    __table_args__ = (
        UniqueConstraint("competency_definition_id", "order_index", name="uq_understanding_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    competency_definition_id: Mapped[str] = mapped_column(
        ForeignKey("competency_definitions.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)


class CompetencyAbilityItem(Base):
    __tablename__ = "competency_ability_items"
    __table_args__ = (
        UniqueConstraint("competency_definition_id", "order_index", name="uq_ability_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    competency_definition_id: Mapped[str] = mapped_column(
        ForeignKey("competency_definitions.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)


class ExitCriterionIdentity(Base, TimestampMixin):
    __tablename__ = "exit_criterion_identities"
    __table_args__ = (
        UniqueConstraint(
            "competency_identity_id", "stable_key", name="uq_exit_criterion_stable_key"
        ),
        CheckConstraint("current_state IN ('not_met','partial','met')", name="ck_exit_state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    competency_identity_id: Mapped[str] = mapped_column(
        ForeignKey("competency_identities.id", ondelete="RESTRICT"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    current_state: Mapped[str] = mapped_column(String(32), default="not_met", nullable=False)


class ExitCriterionDefinition(Base, TimestampMixin):
    __tablename__ = "exit_criterion_definitions"
    __table_args__ = (
        UniqueConstraint(
            "competency_definition_id", "exit_criterion_identity_id", name="uq_exit_definition"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    exit_criterion_identity_id: Mapped[str] = mapped_column(
        ForeignKey("exit_criterion_identities.id", ondelete="RESTRICT"), nullable=False
    )
    competency_definition_id: Mapped[str] = mapped_column(
        ForeignKey("competency_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    weight: Mapped[int | None] = mapped_column(Integer)


class CompetencyState(Base):
    __tablename__ = "competency_states"
    __table_args__ = (
        CheckConstraint(
            "current_status IN ('not_started','learning','practicing',"
            "'ready_for_verification','verified','needs_review')",
            name="ck_competency_status",
        ),
    )

    competency_identity_id: Mapped[str] = mapped_column(
        ForeignKey("competency_identities.id", ondelete="RESTRICT"), primary_key=True
    )
    current_status: Mapped[str] = mapped_column(String(32), default="not_started", nullable=False)
    updated_at: Mapped[int] = mapped_column(
        Integer, default=utc_now_ms, onupdate=utc_now_ms, nullable=False
    )


class CompetencyStatusEvent(Base):
    __tablename__ = "competency_status_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    competency_identity_id: Mapped[str] = mapped_column(
        ForeignKey("competency_identities.id", ondelete="RESTRICT"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    verification_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("verification_records.id")
    )
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)


class VerificationRecord(Base):
    __tablename__ = "verification_records"
    __table_args__ = (
        CheckConstraint(
            "verification_source IN ('self','automated','external')", name="ck_verification_source"
        ),
        CheckConstraint("result IN ('passed','failed','partial')", name="ck_verification_result"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    competency_identity_id: Mapped[str] = mapped_column(
        ForeignKey("competency_identities.id", ondelete="RESTRICT"), nullable=False
    )
    verification_source: Mapped[str] = mapped_column(String(32), nullable=False)
    method: Mapped[str] = mapped_column(String(255), nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[int | None] = mapped_column(Integer)
    reviewer_label: Mapped[str | None] = mapped_column(String(255))
    evidence_summary: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)


class VerificationEvidence(Base):
    __tablename__ = "verification_evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    verification_record_id: Mapped[str] = mapped_column(
        ForeignKey("verification_records.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)


class LearningSession(Base, TimestampMixin):
    __tablename__ = "learning_sessions"
    __table_args__ = (
        CheckConstraint("session_mode IN ('manual','timed')", name="ck_session_mode"),
        CheckConstraint(
            "timed_state IS NULL OR timed_state IN ('running','paused','completed','cancelled')",
            name="ck_timed_state",
        ),
        CheckConstraint(
            "activity_type IN ('learning','reading','practice','coding','debugging',"
            "'project','review','verification','research')",
            name="ck_activity_type",
        ),
        CheckConstraint(
            "assistance_mode IN ('none','docs_only','ai_hint','ai_assisted','agent_led')",
            name="ck_assistance_mode",
        ),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('completed','partial','blocked','cancelled')",
            name="ck_session_outcome",
        ),
        CheckConstraint("accumulated_duration_ms >= 0", name="ck_accumulated_duration"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_session_duration"),
        CheckConstraint(
            "difficulty IS NULL OR difficulty BETWEEN 1 AND 5", name="ck_session_difficulty"
        ),
        CheckConstraint(
            "(session_mode = 'manual' AND timed_state IS NULL AND active_since IS NULL "
            "AND ended_at IS NOT NULL AND duration_ms > 0 "
            "AND accumulated_duration_ms = duration_ms "
            "AND outcome IN ('completed','partial','blocked')) OR "
            "(session_mode = 'timed' AND timed_state IS NOT NULL AND ("
            "(timed_state = 'running' AND active_since IS NOT NULL AND ended_at IS NULL "
            "AND duration_ms IS NULL AND outcome IS NULL) OR "
            "(timed_state = 'paused' AND active_since IS NULL AND ended_at IS NULL "
            "AND duration_ms IS NULL AND outcome IS NULL) OR "
            "(timed_state = 'completed' AND active_since IS NULL AND ended_at IS NOT NULL "
            "AND duration_ms >= 0 AND accumulated_duration_ms = duration_ms "
            "AND outcome IN ('completed','partial','blocked')) OR "
            "(timed_state = 'cancelled' AND active_since IS NULL AND ended_at IS NOT NULL "
            "AND duration_ms >= 0 AND accumulated_duration_ms = duration_ms "
            "AND outcome = 'cancelled')))",
            name="ck_session_lifecycle",
        ),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at", name="ck_session_instant_order"
        ),
        Index(
            "uq_one_active_timed_session",
            "session_mode",
            unique=True,
            sqlite_where=text("session_mode = 'timed' AND timed_state IN ('running','paused')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    competency_identity_id: Mapped[str | None] = mapped_column(
        ForeignKey("competency_identities.id")
    )
    track_id: Mapped[str | None] = mapped_column(ForeignKey("tracks.id"))
    session_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    timed_state: Mapped[str | None] = mapped_column(String(16))
    activity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    assistance_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[int] = mapped_column(Integer, nullable=False)
    ended_at: Mapped[int | None] = mapped_column(Integer)
    accumulated_duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active_since: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    difficulty: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)


class DailyReflection(Base, TimestampMixin):
    __tablename__ = "daily_reflections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    local_date: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)


class GeneratedReport(Base):
    __tablename__ = "generated_reports"
    __table_args__ = (
        UniqueConstraint(
            "report_type",
            "period_start",
            "period_end",
            "analytics_version",
            name="uq_generated_report",
        ),
        CheckConstraint("report_type IN ('daily','weekly','monthly')", name="ck_report_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    report_type: Mapped[str] = mapped_column(String(16), nullable=False)
    period_start: Mapped[str] = mapped_column(String(10), nullable=False)
    period_end: Mapped[str] = mapped_column(String(10), nullable=False)
    generated_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    analytics_version: Mapped[int] = mapped_column(Integer, nullable=False)
    structured_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    rendered_markdown: Mapped[str | None] = mapped_column(Text)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_analysis_run_idempotency"),
        CheckConstraint(
            "status IN ('completed','failed','partial')", name="ck_analysis_run_status"
        ),
        Index("ix_analysis_run_purpose_time", "purpose", "generated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_json: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[int] = mapped_column(Integer, nullable=False)
    cutoff_at: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_lineage_json: Mapped[str] = mapped_column(Text, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    application_version: Mapped[str] = mapped_column(String(64), nullable=False)
    failure_metadata_json: Mapped[str | None] = mapped_column(Text)
    completeness_metadata_json: Mapped[str] = mapped_column(Text, nullable=False)


class AnalysisSnapshot(Base):
    __tablename__ = "analysis_snapshots"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_analysis_snapshot_run"),
        CheckConstraint("cutoff_semantics = 'exclusive'", name="ck_analysis_cutoff_exclusive"),
        CheckConstraint("completeness IN ('complete','partial')", name="ck_analysis_completeness"),
        Index("ix_analysis_snapshot_purpose_time", "purpose", "generated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="RESTRICT"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    generated_at: Mapped[int] = mapped_column(Integer, nullable=False)
    cutoff_at: Mapped[int] = mapped_column(Integer, nullable=False)
    cutoff_semantics: Mapped[str] = mapped_column(String(16), nullable=False)
    timezone: Mapped[str] = mapped_column(String(128), nullable=False)
    completed_through_date: Mapped[str] = mapped_column(String(10), nullable=False)
    target_profile_id: Mapped[str | None] = mapped_column(String(36))
    target_profile_version_id: Mapped[str | None] = mapped_column(String(36))
    capability_scale_version_references_json: Mapped[str] = mapped_column(Text, nullable=False)
    learning_graph_reference: Mapped[str | None] = mapped_column(String(255))
    curriculum_reference: Mapped[str | None] = mapped_column(String(255))
    semantic_definition_references_json: Mapped[str] = mapped_column(Text, nullable=False)
    policy_versions_json: Mapped[str] = mapped_column(Text, nullable=False)
    discipline_configuration_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    application_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_lineage_json: Mapped[str] = mapped_column(Text, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_facts_json: Mapped[str] = mapped_column(Text, nullable=False)
    signals_json: Mapped[str] = mapped_column(Text, nullable=False)
    completeness: Mapped[str] = mapped_column(String(16), nullable=False)
    unknown_markers_json: Mapped[str] = mapped_column(Text, nullable=False)
    output_hash: Mapped[str] = mapped_column(String(64), nullable=False)


@event.listens_for(AnalysisRun, "before_update")
@event.listens_for(AnalysisRun, "before_delete")
@event.listens_for(AnalysisSnapshot, "before_update")
@event.listens_for(AnalysisSnapshot, "before_delete")
def _reject_analysis_history_mutation(*_args: object) -> None:
    raise ValueError("Analysis runs and snapshots are immutable.")


class ProjectionInvalidation(Base):
    __tablename__ = "projection_invalidations"
    __table_args__ = (
        UniqueConstraint(
            "projection_kind",
            "subject_type",
            "subject_id",
            "source_fact_id",
            "target_policy_version",
            name="uq_projection_invalidation_source_target",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_projection_attempt_count"),
        CheckConstraint(
            "status IN ('pending','running','completed','permanent_failure')",
            name="ck_projection_invalidation_status",
        ),
        Index("ix_projection_invalidation_drain", "status", "requested_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    projection_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_fact_id: Mapped[str] = mapped_column(String(255), nullable=False)
    target_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requested_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    started_at: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[int | None] = mapped_column(Integer)
    result_run_id: Mapped[str | None] = mapped_column(String(36))
    error_json: Mapped[str | None] = mapped_column(Text)


class RecommendationSnapshot(Base):
    __tablename__ = "recommendation_snapshots"
    __table_args__ = (Index("ix_recommendation_analysis_snapshot", "analysis_snapshot_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    generated_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    local_date: Mapped[str] = mapped_column(String(10), nullable=False)
    engine_version: Mapped[int] = mapped_column(Integer, nullable=False)
    primary_competency_identity_id: Mapped[str | None] = mapped_column(
        ForeignKey("competency_identities.id")
    )
    primary_activity_type: Mapped[str | None] = mapped_column(String(32))
    secondary_competency_identity_id: Mapped[str | None] = mapped_column(
        ForeignKey("competency_identities.id")
    )
    structured_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    accepted_primary: Mapped[bool | None] = mapped_column(Boolean)
    chosen_competency_identity_id: Mapped[str | None] = mapped_column(
        ForeignKey("competency_identities.id")
    )
    analysis_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_snapshots.id", ondelete="RESTRICT")
    )


class DisciplineProfile(Base):
    __tablename__ = "discipline_profiles"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_single_discipline_profile"),
        CheckConstraint("weekly_target_active_days BETWEEN 1 AND 7", name="ck_weekly_target_days"),
        CheckConstraint(
            "target_duration_ms_per_active_day IS NULL OR target_duration_ms_per_active_day > 0",
            name="ck_target_duration",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    weekly_target_active_days: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    target_duration_ms_per_active_day: Mapped[int | None] = mapped_column(
        Integer, default=3_600_000
    )
    timezone: Mapped[str] = mapped_column(String(128), default="UTC", nullable=False)
    adaptation_phase_config_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    updated_at: Mapped[int] = mapped_column(
        Integer, default=utc_now_ms, onupdate=utc_now_ms, nullable=False
    )


class ImportRecord(Base):
    __tablename__ = "import_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    package_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    import_type: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    dry_run_summary_json: Mapped[str] = mapped_column(Text, nullable=False)
    applied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    applied_at: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    pre_import_backup_reference: Mapped[str | None] = mapped_column(Text)


class ExportRecord(Base):
    __tablename__ = "export_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    export_type: Mapped[str] = mapped_column(String(64), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_summary_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)


class ApplicationSetting(Base):
    __tablename__ = "application_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[int] = mapped_column(
        Integer, default=utc_now_ms, onupdate=utc_now_ms, nullable=False
    )


class OperationalBackup(Base):
    __tablename__ = "operational_backups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
