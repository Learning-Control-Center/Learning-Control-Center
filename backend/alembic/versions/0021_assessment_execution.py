"""Bind authored assessments to actual work and preserve append-only reviews."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_assessment_execution"
down_revision = "0020_master_import_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assessment_executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column(
            "today_suggestion_id",
            sa.String(36),
            sa.ForeignKey("today_suggestions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "recommendation_run_id",
            sa.String(36),
            sa.ForeignKey("recommendation_v2_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "recommendation_candidate_id",
            sa.String(36),
            sa.ForeignKey("recommendation_v2_candidates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "rubric_identity_id",
            sa.String(36),
            sa.ForeignKey("assessment_rubric_identities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "rubric_definition_id",
            sa.String(36),
            sa.ForeignKey("assessment_rubric_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "curriculum_version_id",
            sa.String(36),
            sa.ForeignKey("curriculum_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "unit_definition_id",
            sa.String(36),
            sa.ForeignKey("learning_unit_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "opportunity_id",
            sa.String(36),
            sa.ForeignKey("curriculum_evidence_opportunities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "competency_identity_id",
            sa.String(36),
            sa.ForeignKey("competency_identities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "target_identity_id",
            sa.String(36),
            sa.ForeignKey("profile_target_identities.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "activity_id",
            sa.String(36),
            sa.ForeignKey("activities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("learning_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("assistance_mode_at_start", sa.String(32), nullable=False),
        sa.Column("started_at", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.UniqueConstraint("today_suggestion_id", name="uq_assessment_execution_suggestion"),
        sa.UniqueConstraint("session_id", name="uq_assessment_execution_session"),
        sa.UniqueConstraint("idempotency_key", name="uq_assessment_execution_command"),
    )
    op.create_table(
        "assessment_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column(
            "execution_id",
            sa.String(36),
            sa.ForeignKey("assessment_executions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("learning_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "unit_definition_id",
            sa.String(36),
            sa.ForeignKey("learning_unit_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "opportunity_id",
            sa.String(36),
            sa.ForeignKey("curriculum_evidence_opportunities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "criterion_definition_id",
            sa.String(36),
            sa.ForeignKey("criterion_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_base64", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("capture_method", sa.String(32), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_assessment_artifact_command"),
        sa.CheckConstraint(
            "size_bytes > 0 AND size_bytes <= 262144", name="ck_assessment_artifact_size"
        ),
        sa.CheckConstraint(
            "capture_method = 'server_received_bytes/v1'", name="ck_assessment_artifact_capture"
        ),
    )
    op.create_table(
        "assessment_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "execution_id",
            sa.String(36),
            sa.ForeignKey("assessment_executions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("review_sequence", sa.Integer(), nullable=False),
        sa.Column(
            "supersedes_review_id",
            sa.String(36),
            sa.ForeignKey("assessment_reviews.id", ondelete="RESTRICT"),
        ),
        sa.Column("reviewer_user_id", sa.String(36), nullable=False),
        sa.Column("reviewer_kind", sa.String(16), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_assessment_review_command"),
        sa.UniqueConstraint(
            "execution_id", "review_sequence", name="uq_assessment_review_execution_sequence"
        ),
        sa.CheckConstraint("reviewer_kind = 'self'", name="ck_assessment_review_self"),
        sa.CheckConstraint("review_sequence > 0", name="ck_assessment_review_sequence"),
    )


def downgrade() -> None:
    op.drop_table("assessment_reviews")
    op.drop_table("assessment_artifacts")
    op.drop_table("assessment_executions")
