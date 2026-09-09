"""Add immutable analysis lineage and projection invalidation."""

import sqlalchemy as sa
from alembic import op

revision = "0004_analysis_projection_foundation"
down_revision = "0003_auth_security_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("scope_json", sa.Text(), nullable=False),
        sa.Column("generated_at", sa.Integer(), nullable=False),
        sa.Column("cutoff_at", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("algorithm_version", sa.String(64), nullable=False),
        sa.Column("configuration_reference", sa.String(255), nullable=False),
        sa.Column("configuration_hash", sa.String(64), nullable=False),
        sa.Column("input_lineage_json", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("application_version", sa.String(64), nullable=False),
        sa.Column("failure_metadata_json", sa.Text(), nullable=True),
        sa.Column("completeness_metadata_json", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('completed','failed','partial')", name="ck_analysis_run_status"
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_analysis_run_idempotency"),
    )
    op.create_index("ix_analysis_run_purpose_time", "analysis_runs", ["purpose", "generated_at"])
    op.create_table(
        "analysis_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("generated_at", sa.Integer(), nullable=False),
        sa.Column("cutoff_at", sa.Integer(), nullable=False),
        sa.Column("cutoff_semantics", sa.String(16), nullable=False),
        sa.Column("timezone", sa.String(128), nullable=False),
        sa.Column("completed_through_date", sa.String(10), nullable=False),
        sa.Column("target_profile_id", sa.String(36), nullable=True),
        sa.Column("target_profile_version_id", sa.String(36), nullable=True),
        sa.Column("capability_scale_version_references_json", sa.Text(), nullable=False),
        sa.Column("learning_graph_reference", sa.String(255), nullable=True),
        sa.Column("curriculum_reference", sa.String(255), nullable=True),
        sa.Column("semantic_definition_references_json", sa.Text(), nullable=False),
        sa.Column("policy_versions_json", sa.Text(), nullable=False),
        sa.Column("discipline_configuration_reference", sa.String(255), nullable=False),
        sa.Column("configuration_hash", sa.String(64), nullable=False),
        sa.Column("application_version", sa.String(64), nullable=False),
        sa.Column("input_lineage_json", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("normalized_facts_json", sa.Text(), nullable=False),
        sa.Column("signals_json", sa.Text(), nullable=False),
        sa.Column("completeness", sa.String(16), nullable=False),
        sa.Column("unknown_markers_json", sa.Text(), nullable=False),
        sa.Column("output_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("cutoff_semantics = 'exclusive'", name="ck_analysis_cutoff_exclusive"),
        sa.CheckConstraint(
            "completeness IN ('complete','partial')", name="ck_analysis_completeness"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("run_id", name="uq_analysis_snapshot_run"),
    )
    op.create_index(
        "ix_analysis_snapshot_purpose_time", "analysis_snapshots", ["purpose", "generated_at"]
    )
    op.create_table(
        "projection_invalidations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("projection_kind", sa.String(64), nullable=False),
        sa.Column("subject_type", sa.String(64), nullable=False),
        sa.Column("subject_id", sa.String(255), nullable=False),
        sa.Column("source_fact_id", sa.String(255), nullable=False),
        sa.Column("target_policy_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.Integer(), nullable=True),
        sa.Column("result_run_id", sa.String(36), nullable=True),
        sa.Column("error_json", sa.Text(), nullable=True),
        sa.CheckConstraint("attempt_count >= 0", name="ck_projection_attempt_count"),
        sa.CheckConstraint(
            "status IN ('pending','running','completed','permanent_failure')",
            name="ck_projection_invalidation_status",
        ),
        sa.UniqueConstraint(
            "projection_kind",
            "subject_type",
            "subject_id",
            "source_fact_id",
            "target_policy_version",
            name="uq_projection_invalidation_source_target",
        ),
    )
    op.create_index(
        "ix_projection_invalidation_drain",
        "projection_invalidations",
        ["status", "requested_at"],
    )
    with op.batch_alter_table("recommendation_snapshots") as batch:
        batch.add_column(sa.Column("analysis_snapshot_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_recommendation_analysis_snapshot",
            "analysis_snapshots",
            ["analysis_snapshot_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_recommendation_analysis_snapshot", ["analysis_snapshot_id"])


def downgrade() -> None:
    with op.batch_alter_table("recommendation_snapshots") as batch:
        batch.drop_index("ix_recommendation_analysis_snapshot")
        batch.drop_constraint("fk_recommendation_analysis_snapshot", type_="foreignkey")
        batch.drop_column("analysis_snapshot_id")
    op.drop_index("ix_projection_invalidation_drain", table_name="projection_invalidations")
    op.drop_table("projection_invalidations")
    op.drop_index("ix_analysis_snapshot_purpose_time", table_name="analysis_snapshots")
    op.drop_table("analysis_snapshots")
    op.drop_index("ix_analysis_run_purpose_time", table_name="analysis_runs")
    op.drop_table("analysis_runs")
