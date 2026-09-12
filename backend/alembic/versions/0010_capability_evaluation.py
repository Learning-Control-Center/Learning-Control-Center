"""Add deterministic capability, criterion, and review projections."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_capability_evaluation"
down_revision = "0009_unified_evidence_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("semantic_competency_definitions") as batch:
        batch.add_column(sa.Column("freshness_current_through_days", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("freshness_stale_after_days", sa.Integer(), nullable=True))
        batch.create_check_constraint(
            "ck_semantic_definition_freshness_override",
            "(freshness_current_through_days IS NULL) = "
            "(freshness_stale_after_days IS NULL) AND "
            "(freshness_current_through_days IS NULL OR "
            "(freshness_current_through_days >= 0 AND "
            "freshness_stale_after_days >= freshness_current_through_days))",
        )
    op.create_table(
        "capability_evaluation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("competency_identity_id", sa.String(36), nullable=False),
        sa.Column("semantic_definition_id", sa.String(36), nullable=False),
        sa.Column("scale_version_id", sa.String(36), nullable=False),
        sa.Column("dimension_id", sa.String(36), nullable=True),
        sa.Column("scope_key", sa.String(255), nullable=False),
        sa.Column("cutoff_at", sa.Integer(), nullable=False),
        sa.Column("generated_at", sa.Integer(), nullable=False),
        sa.Column("criterion_policy_version", sa.String(64), nullable=False),
        sa.Column("capability_policy_version", sa.String(64), nullable=False),
        sa.Column("evidence_policy_version", sa.String(64), nullable=False),
        sa.Column("downgrade_policy_version", sa.String(64), nullable=False),
        sa.Column("evidence_set_hash", sa.String(64), nullable=False),
        sa.Column("input_payload_json", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("selected_level_id", sa.String(36), nullable=True),
        sa.Column("assessment_status", sa.String(16), nullable=False),
        sa.Column("aggregate_confidence", sa.String(16), nullable=False),
        sa.Column("confidence_facts_json", sa.Text(), nullable=False),
        sa.Column("downgrade_cause", sa.String(64), nullable=True),
        sa.Column("decisive_evidence_ids_json", sa.Text(), nullable=False),
        sa.Column("passed_level_ids_json", sa.Text(), nullable=False),
        sa.Column("reasons_json", sa.Text(), nullable=False),
        sa.Column("output_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_capability_run_idempotency"),
        sa.CheckConstraint(
            "assessment_status IN ('unknown','evaluated')", name="ck_run_assessment"
        ),
        sa.CheckConstraint(
            "aggregate_confidence IN ('unknown','low','medium','high')",
            name="ck_run_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["competency_identity_id"], ["competency_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["semantic_definition_id"],
            ["semantic_competency_definitions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scale_version_id"], ["capability_scale_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["dimension_id"], ["capability_scale_dimensions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["selected_level_id"], ["capability_scale_levels.id"], ondelete="RESTRICT"
        ),
    )
    op.create_index(
        "ix_capability_run_subject",
        "capability_evaluation_runs",
        ["competency_identity_id", "scope_key", "generated_at"],
    )
    op.create_table(
        "criterion_evaluation_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("criterion_definition_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("evidence_set_hash", sa.String(64), nullable=False),
        sa.Column("decisive_evidence_ids_json", sa.Text(), nullable=False),
        sa.Column("facts_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("run_id", "criterion_definition_id", name="uq_criterion_result_run"),
        sa.CheckConstraint(
            "state IN ('unknown','not_demonstrated','partially_demonstrated','demonstrated',"
            "'contradicted')",
            name="ck_criterion_result_state",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["capability_evaluation_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["criterion_definition_id"], ["criterion_definitions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "competency_capability_states",
        sa.Column("competency_identity_id", sa.String(36), primary_key=True),
        sa.Column("scope_key", sa.String(255), primary_key=True),
        sa.Column("semantic_definition_id", sa.String(36), nullable=False),
        sa.Column("scale_version_id", sa.String(36), nullable=False),
        sa.Column("dimension_id", sa.String(36), nullable=True),
        sa.Column("capability_level_id", sa.String(36), nullable=True),
        sa.Column("assessment_status", sa.String(16), nullable=False),
        sa.Column("aggregate_confidence", sa.String(16), nullable=False),
        sa.Column("evaluation_run_id", sa.String(36), nullable=False),
        sa.Column("capability_policy_version", sa.String(64), nullable=False),
        sa.Column("criterion_policy_version", sa.String(64), nullable=False),
        sa.Column("evidence_policy_version", sa.String(64), nullable=False),
        sa.Column("evidence_set_hash", sa.String(64), nullable=False),
        sa.Column("last_evaluated_at", sa.Integer(), nullable=False),
        sa.Column("last_meaningful_evidence_at", sa.Integer(), nullable=True),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("confidence_facts_json", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "assessment_status IN ('unknown','evaluated')", name="ck_state_assessment"
        ),
        sa.CheckConstraint(
            "aggregate_confidence IN ('unknown','low','medium','high')",
            name="ck_state_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["competency_identity_id"], ["competency_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["semantic_definition_id"],
            ["semantic_competency_definitions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scale_version_id"], ["capability_scale_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["dimension_id"], ["capability_scale_dimensions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["capability_level_id"], ["capability_scale_levels.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"], ["capability_evaluation_runs.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "capability_state_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("competency_identity_id", sa.String(36), nullable=False),
        sa.Column("semantic_definition_id", sa.String(36), nullable=False),
        sa.Column("dimension_id", sa.String(36), nullable=True),
        sa.Column("scope_key", sa.String(255), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("previous_level_id", sa.String(36), nullable=True),
        sa.Column("new_level_id", sa.String(36), nullable=True),
        sa.Column("previous_assessment_status", sa.String(16), nullable=True),
        sa.Column("new_assessment_status", sa.String(16), nullable=False),
        sa.Column("previous_confidence", sa.String(16), nullable=True),
        sa.Column("new_confidence", sa.String(16), nullable=False),
        sa.Column("cause_code", sa.String(64), nullable=False),
        sa.Column("decisive_evidence_ids_json", sa.Text(), nullable=False),
        sa.Column("evaluation_run_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "competency_identity_id", "scope_key", "event_sequence", name="uq_capability_event_seq"
        ),
        sa.CheckConstraint("event_sequence > 0", name="ck_capability_event_sequence"),
        sa.ForeignKeyConstraint(
            ["competency_identity_id"], ["competency_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["semantic_definition_id"],
            ["semantic_competency_definitions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dimension_id"], ["capability_scale_dimensions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["previous_level_id"], ["capability_scale_levels.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["new_level_id"], ["capability_scale_levels.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"], ["capability_evaluation_runs.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "competency_review_states",
        sa.Column("competency_identity_id", sa.String(36), primary_key=True),
        sa.Column("scope_key", sa.String(255), primary_key=True),
        sa.Column("semantic_definition_id", sa.String(36), nullable=False),
        sa.Column("scale_version_id", sa.String(36), nullable=False),
        sa.Column("dimension_id", sa.String(36), nullable=True),
        sa.Column("freshness", sa.String(16), nullable=False),
        sa.Column("review_due", sa.Boolean(), nullable=False),
        sa.Column("reason_codes_json", sa.Text(), nullable=False),
        sa.Column("last_meaningful_evidence_at", sa.Integer(), nullable=True),
        sa.Column("current_through_days", sa.Integer(), nullable=True),
        sa.Column("stale_after_days", sa.Integer(), nullable=True),
        sa.Column("threshold_source", sa.String(64), nullable=False),
        sa.Column("freshness_policy_version", sa.String(64), nullable=False),
        sa.Column("evaluated_at", sa.Integer(), nullable=False),
        sa.Column("evaluation_run_id", sa.String(36), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["competency_identity_id"], ["competency_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["semantic_definition_id"],
            ["semantic_competency_definitions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scale_version_id"], ["capability_scale_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["dimension_id"], ["capability_scale_dimensions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"], ["capability_evaluation_runs.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "review_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("competency_identity_id", sa.String(36), nullable=False),
        sa.Column("semantic_definition_id", sa.String(36), nullable=False),
        sa.Column("dimension_id", sa.String(36), nullable=True),
        sa.Column("scope_key", sa.String(255), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("previous_freshness", sa.String(16), nullable=True),
        sa.Column("new_freshness", sa.String(16), nullable=False),
        sa.Column("previous_review_due", sa.Boolean(), nullable=True),
        sa.Column("new_review_due", sa.Boolean(), nullable=False),
        sa.Column("reason_codes_json", sa.Text(), nullable=False),
        sa.Column("last_meaningful_evidence_at", sa.Integer(), nullable=True),
        sa.Column("current_through_days", sa.Integer(), nullable=True),
        sa.Column("stale_after_days", sa.Integer(), nullable=True),
        sa.Column("threshold_source", sa.String(64), nullable=False),
        sa.Column("freshness_policy_version", sa.String(64), nullable=False),
        sa.Column("evaluation_run_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "competency_identity_id", "scope_key", "event_sequence", name="uq_review_event_seq"
        ),
        sa.CheckConstraint("event_sequence > 0", name="ck_review_event_sequence"),
        sa.ForeignKeyConstraint(
            ["competency_identity_id"], ["competency_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["semantic_definition_id"],
            ["semantic_competency_definitions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dimension_id"], ["capability_scale_dimensions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"], ["capability_evaluation_runs.id"], ondelete="RESTRICT"
        ),
    )

    with op.batch_alter_table("projection_invalidations") as batch:
        batch.add_column(sa.Column("subject_sequence", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("attempt_run_id", sa.String(36), nullable=True))
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id,projection_kind,subject_type,subject_id FROM projection_invalidations "
            "ORDER BY requested_at,id"
        )
    ).mappings()
    sequences: dict[tuple[str, str, str], int] = {}
    for row in rows:
        key = (row["projection_kind"], row["subject_type"], row["subject_id"])
        sequences[key] = sequences.get(key, 0) + 1
        connection.execute(
            sa.text("UPDATE projection_invalidations SET subject_sequence=:sequence WHERE id=:id"),
            {"sequence": sequences[key], "id": row["id"]},
        )
    with op.batch_alter_table("projection_invalidations") as batch:
        batch.alter_column("subject_sequence", nullable=False)
        batch.create_check_constraint("ck_projection_subject_sequence", "subject_sequence > 0")
        batch.create_unique_constraint(
            "uq_projection_invalidation_subject_sequence",
            ["projection_kind", "subject_type", "subject_id", "subject_sequence"],
        )


def downgrade() -> None:
    with op.batch_alter_table("projection_invalidations") as batch:
        batch.drop_constraint("uq_projection_invalidation_subject_sequence", type_="unique")
        batch.drop_constraint("ck_projection_subject_sequence", type_="check")
        batch.drop_column("attempt_run_id")
        batch.drop_column("subject_sequence")
    op.drop_table("review_events")
    op.drop_table("competency_review_states")
    op.drop_table("capability_state_events")
    op.drop_table("competency_capability_states")
    op.drop_table("criterion_evaluation_results")
    op.drop_index("ix_capability_run_subject", table_name="capability_evaluation_runs")
    op.drop_table("capability_evaluation_runs")
    with op.batch_alter_table("semantic_competency_definitions") as batch:
        batch.drop_constraint("ck_semantic_definition_freshness_override", type_="check")
        batch.drop_column("freshness_stale_after_days")
        batch.drop_column("freshness_current_through_days")
