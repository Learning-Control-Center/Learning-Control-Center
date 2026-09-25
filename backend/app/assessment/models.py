from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models import new_id
from app.time_utils import utc_now_ms


class AssessmentExecution(Base):
    __tablename__ = "assessment_executions"
    __table_args__ = (
        UniqueConstraint("today_suggestion_id", name="uq_assessment_execution_suggestion"),
        UniqueConstraint("session_id", name="uq_assessment_execution_session"),
        UniqueConstraint("idempotency_key", name="uq_assessment_execution_command"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    today_suggestion_id: Mapped[str] = mapped_column(
        ForeignKey("today_suggestions.id", ondelete="RESTRICT"), nullable=False
    )
    recommendation_run_id: Mapped[str] = mapped_column(
        ForeignKey("recommendation_v2_runs.id", ondelete="RESTRICT"), nullable=False
    )
    recommendation_candidate_id: Mapped[str] = mapped_column(
        ForeignKey("recommendation_v2_candidates.id", ondelete="RESTRICT"), nullable=False
    )
    rubric_identity_id: Mapped[str] = mapped_column(
        ForeignKey("assessment_rubric_identities.id", ondelete="RESTRICT"), nullable=False
    )
    rubric_definition_id: Mapped[str] = mapped_column(
        ForeignKey("assessment_rubric_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    curriculum_version_id: Mapped[str] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"), nullable=False
    )
    unit_definition_id: Mapped[str] = mapped_column(
        ForeignKey("learning_unit_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    opportunity_id: Mapped[str] = mapped_column(
        ForeignKey("curriculum_evidence_opportunities.id", ondelete="RESTRICT"), nullable=False
    )
    competency_identity_id: Mapped[str] = mapped_column(
        ForeignKey("competency_identities.id", ondelete="RESTRICT"), nullable=False
    )
    target_identity_id: Mapped[str | None] = mapped_column(
        ForeignKey("profile_target_identities.id", ondelete="RESTRICT")
    )
    activity_id: Mapped[str] = mapped_column(
        ForeignKey("activities.id", ondelete="RESTRICT"), nullable=False
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("learning_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    assistance_mode_at_start: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)


class AssessmentArtifact(Base):
    __tablename__ = "assessment_artifacts"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_assessment_artifact_command"),
        CheckConstraint(
            "size_bytes > 0 AND size_bytes <= 262144", name="ck_assessment_artifact_size"
        ),
        CheckConstraint(
            "capture_method = 'server_received_bytes/v1'", name="ck_assessment_artifact_capture"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("assessment_executions.id", ondelete="RESTRICT"), nullable=False
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("learning_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    unit_definition_id: Mapped[str] = mapped_column(
        ForeignKey("learning_unit_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    opportunity_id: Mapped[str] = mapped_column(
        ForeignKey("curriculum_evidence_opportunities.id", ondelete="RESTRICT"), nullable=False
    )
    criterion_definition_id: Mapped[str] = mapped_column(
        ForeignKey("criterion_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_base64: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    capture_method: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)


class AssessmentReview(Base):
    __tablename__ = "assessment_reviews"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_assessment_review_command"),
        UniqueConstraint(
            "execution_id", "review_sequence", name="uq_assessment_review_execution_sequence"
        ),
        CheckConstraint("reviewer_kind = 'self'", name="ck_assessment_review_self"),
        CheckConstraint("review_sequence > 0", name="ck_assessment_review_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("assessment_executions.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    review_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_review_id: Mapped[str | None] = mapped_column(
        ForeignKey("assessment_reviews.id", ondelete="RESTRICT")
    )
    # Portable recovery intentionally excludes authentication accounts. Preserve the
    # original reviewer ID as audit provenance without requiring that account to exist.
    reviewer_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    reviewer_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="self")
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)


def _reject_assessment_history_mutation(*_args: object) -> None:
    raise ValueError("Assessment execution and review history is immutable.")


for _immutable_model in (AssessmentExecution, AssessmentArtifact, AssessmentReview):
    event.listen(_immutable_model, "before_update", _reject_assessment_history_mutation)
    event.listen(_immutable_model, "before_delete", _reject_assessment_history_mutation)
