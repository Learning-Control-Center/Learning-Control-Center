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
    __table_args__ = (CheckConstraint("singleton_key = 1", name="ck_single_user"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    singleton_key: Mapped[int] = mapped_column(Integer, unique=True, default=1, nullable=False)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_lookup_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    last_seen_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    absolute_expires_at: Mapped[int] = mapped_column(Integer, nullable=False)
    revoked_at: Mapped[int | None] = mapped_column(Integer)


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


class CompetencyIdentity(Base):
    __tablename__ = "competency_identities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    stable_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)


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


class RecommendationSnapshot(Base):
    __tablename__ = "recommendation_snapshots"

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
