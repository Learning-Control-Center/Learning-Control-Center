"""Add nullable Activity/session attribution schema."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_activity_session_schema"
down_revision = "0005_profile_competency_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "activity_category_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("stable_key", sa.String(64), nullable=False),
        sa.Column("vocabulary_version", sa.String(32), nullable=False),
        sa.Column("display_label", sa.String(128), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "stable_key", "vocabulary_version", name="uq_activity_category_version"
        ),
    )
    op.create_table(
        "activities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category_stable_key", sa.String(64), nullable=False),
        sa.Column("category_version", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.Integer(), nullable=True),
        sa.Column("context_started_at", sa.Integer(), nullable=True),
        sa.Column("context_ended_at", sa.Integer(), nullable=True),
        sa.Column("creator_source", sa.String(64), nullable=False),
        sa.Column("provenance", sa.String(128), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("supersedes_activity_id", sa.String(36), nullable=True),
        sa.Column("outcome_classification", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(
            ["category_stable_key", "category_version"],
            [
                "activity_category_versions.stable_key",
                "activity_category_versions.vocabulary_version",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["supersedes_activity_id"], ["activities.id"], ondelete="RESTRICT"),
    )
    with op.batch_alter_table("learning_sessions") as batch:
        batch.add_column(sa.Column("activity_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("tombstoned_at", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("tombstone_reason", sa.Text(), nullable=True))
        batch.create_foreign_key(
            "fk_learning_session_activity",
            "activities",
            ["activity_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_table(
        "session_contributions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("competency_identity_id", sa.String(36), nullable=False),
        sa.Column("criterion_identity_id", sa.String(36), nullable=True),
        sa.Column("relevance", sa.String(16), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("provenance", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "relevance IN ('primary','secondary','supporting')",
            name="ck_session_contribution_relevance",
        ),
        sa.CheckConstraint(
            "provenance IN ('user_selected','user_confirmed',"
            "'deterministic_legacy_backfill','imported_asserted')",
            name="ck_session_contribution_provenance",
        ),
        sa.ForeignKeyConstraint(["session_id"], ["learning_sessions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["competency_identity_id"], ["competency_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["criterion_identity_id"], ["criterion_identities.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "contribution_retractions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("contribution_id", sa.String(36), nullable=False, unique=True),
        sa.Column("replacement_contribution_id", sa.String(36), nullable=True),
        sa.Column("retracted_at", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["contribution_id"], ["session_contributions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["replacement_contribution_id"], ["session_contributions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "session_corrections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("corrected_at", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("changed_fields_json", sa.Text(), nullable=False),
        sa.Column("before_json", sa.Text(), nullable=False),
        sa.Column("after_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["learning_sessions.id"], ondelete="RESTRICT"),
    )


def downgrade() -> None:
    op.drop_table("session_corrections")
    op.drop_table("contribution_retractions")
    op.drop_table("session_contributions")
    with op.batch_alter_table("learning_sessions") as batch:
        batch.drop_constraint("fk_learning_session_activity", type_="foreignkey")
        batch.drop_column("tombstone_reason")
        batch.drop_column("tombstoned_at")
        batch.drop_column("activity_id")
    op.drop_table("activities")
    op.drop_table("activity_category_versions")
