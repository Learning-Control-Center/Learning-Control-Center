"""Add immutable diagnostic Analysis V3 history and current validity state."""

from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision = "0015_analysis_v3"
down_revision = "0014_roadmap_projection_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("projection_invalidations") as batch:
        batch.drop_constraint("ck_projection_invalidation_status", type_="check")
        batch.create_check_constraint(
            "ck_projection_invalidation_status",
            "status IN ('pending','running','completed','permanent_failure',"
            "'superseded_no_handler')",
        )
    op.create_table(
        "discipline_configuration_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("configuration_json", sa.Text(), nullable=False),
        sa.Column("configuration_hash", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.UniqueConstraint("event_sequence", name="uq_discipline_config_sequence"),
        sa.UniqueConstraint("idempotency_key", name="uq_discipline_config_idempotency"),
        sa.CheckConstraint("event_sequence > 0", name="ck_discipline_config_sequence"),
    )
    profile = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT weekly_target_active_days, target_duration_ms_per_active_day, timezone, "
                "adaptation_phase_config_json, updated_at FROM discipline_profiles WHERE id = 1"
            )
        )
        .mappings()
        .first()
    )
    if profile is not None:
        payload = {
            "adaptationPhaseConfig": json.loads(profile["adaptation_phase_config_json"]),
            "targetDurationMsPerActiveDay": profile["target_duration_ms_per_active_day"],
            "timezone": profile["timezone"],
            "weeklyTargetActiveDays": profile["weekly_target_active_days"],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        op.get_bind().execute(
            sa.text(
                "INSERT INTO discipline_configuration_events "
                "(id, event_sequence, idempotency_key, configuration_json, "
                "configuration_hash, recorded_at, source) "
                "VALUES ('analysis-v3-config-baseline', 1, 'analysis-v3-config-baseline', "
                ":payload, :hash, :recorded_at, 'migration_baseline')"
            ),
            {
                "payload": encoded,
                "hash": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                "recorded_at": profile["updated_at"],
            },
        )
    op.create_table(
        "analysis_v3_run_lineages",
        sa.Column("run_id", sa.String(36), primary_key=True),
        sa.Column("analysis_algorithm_version", sa.String(64), nullable=False),
        sa.Column("analysis_policy_version", sa.String(64), nullable=False),
        sa.Column("normalization_schema_version", sa.String(64), nullable=False),
        sa.Column("analyzer_bundle_json", sa.Text(), nullable=False),
        sa.Column("policy_bundle_hash", sa.String(64), nullable=False),
        sa.Column("source_generation", sa.Integer(), nullable=False),
        sa.Column("replay_of_run_id", sa.String(36)),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["replay_of_run_id"], ["analysis_runs.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("source_generation >= 0", name="ck_analysis_v3_source_generation"),
    )
    op.create_table(
        "analysis_v3_snapshot_details",
        sa.Column("snapshot_id", sa.String(36), primary_key=True),
        sa.Column("purpose_matrix_version", sa.String(64), nullable=False),
        sa.Column("facts_hash", sa.String(64), nullable=False),
        sa.Column("gaps_hash", sa.String(64), nullable=False),
        sa.Column("signals_hash", sa.String(64), nullable=False),
        sa.Column("unknowns_hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["analysis_snapshots.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "analysis_v3_normalized_facts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("stable_key", sa.String(512), nullable=False),
        sa.Column("fact_type", sa.String(64), nullable=False),
        sa.Column("subject_type", sa.String(64), nullable=False),
        sa.Column("subject_id", sa.String(255), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["analysis_snapshots.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("snapshot_id", "ordinal", name="uq_analysis_v3_fact_ordinal"),
        sa.UniqueConstraint("snapshot_id", "stable_key", name="uq_analysis_v3_fact_key"),
        sa.CheckConstraint("ordinal >= 0", name="ck_analysis_v3_fact_ordinal"),
    )
    op.create_index(
        "ix_analysis_v3_fact_type",
        "analysis_v3_normalized_facts",
        ["snapshot_id", "fact_type"],
    )
    op.create_table(
        "analysis_v3_competency_gaps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("stable_key", sa.String(512), nullable=False),
        sa.Column("competency_identity_id", sa.String(36), nullable=False),
        sa.Column("dimension_key", sa.String(64)),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("comparison_status", sa.String(32), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("input_lineage_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["analysis_snapshots.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("snapshot_id", "ordinal", name="uq_analysis_v3_gap_ordinal"),
        sa.UniqueConstraint("snapshot_id", "stable_key", name="uq_analysis_v3_gap_key"),
        sa.CheckConstraint("ordinal >= 0", name="ck_analysis_v3_gap_ordinal"),
        sa.CheckConstraint(
            "severity IN ('unknown','none','low','medium','high','critical')",
            name="ck_analysis_v3_gap_severity",
        ),
    )
    op.create_table(
        "analysis_v3_signals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("stable_key", sa.String(512), nullable=False),
        sa.Column("signal_type", sa.String(64), nullable=False),
        sa.Column("subject_type", sa.String(64), nullable=False),
        sa.Column("subject_id", sa.String(255), nullable=False),
        sa.Column("dimension_key", sa.String(64)),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("reason_codes_json", sa.Text(), nullable=False),
        sa.Column("decisive_facts_json", sa.Text(), nullable=False),
        sa.Column("analyzer_policy_version", sa.String(64), nullable=False),
        sa.Column("generated_cutoff_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["analysis_snapshots.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("snapshot_id", "ordinal", name="uq_analysis_v3_signal_ordinal"),
        sa.UniqueConstraint("snapshot_id", "stable_key", name="uq_analysis_v3_signal_key"),
        sa.CheckConstraint("ordinal >= 0", name="ck_analysis_v3_signal_ordinal"),
        sa.CheckConstraint(
            "severity IN ('info','attention','high','critical')",
            name="ck_analysis_v3_signal_severity",
        ),
    )
    op.create_index(
        "ix_analysis_v3_signal_type", "analysis_v3_signals", ["snapshot_id", "signal_type"]
    )
    op.create_table(
        "analysis_v3_unknown_markers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("field_path", sa.String(512), nullable=False),
        sa.Column("subject_type", sa.String(64), nullable=False),
        sa.Column("subject_id", sa.String(255), nullable=False),
        sa.Column("reason_code", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["analysis_snapshots.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("snapshot_id", "ordinal", name="uq_analysis_v3_unknown_ordinal"),
        sa.CheckConstraint("ordinal >= 0", name="ck_analysis_v3_unknown_ordinal"),
    )
    op.create_table(
        "analysis_v3_current_states",
        sa.Column("scope_key", sa.String(255), primary_key=True),
        sa.Column("purpose", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("exclusive_cutoff_at", sa.Integer(), nullable=False),
        sa.Column("source_generation", sa.Integer(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("policy_bundle_hash", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["analysis_snapshots.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "status IN ('current','stale','pending','failed')",
            name="ck_analysis_v3_current_status",
        ),
        sa.CheckConstraint("source_generation >= 0", name="ck_analysis_v3_current_generation"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    for table in (
        "analysis_v3_normalized_facts",
        "analysis_v3_competency_gaps",
        "analysis_v3_signals",
        "analysis_v3_unknown_markers",
        "analysis_v3_snapshot_details",
        "analysis_v3_run_lineages",
    ):
        if connection.execute(sa.text(f'SELECT 1 FROM "{table}" LIMIT 1')).first():
            raise RuntimeError("Refusing to downgrade immutable Analysis V3 history.")
    configuration_rows = (
        connection.execute(
            sa.text("SELECT source FROM discipline_configuration_events ORDER BY event_sequence")
        )
        .scalars()
        .all()
    )
    if len(configuration_rows) > 1 or any(
        source != "migration_baseline" for source in configuration_rows
    ):
        raise RuntimeError("Refusing to downgrade immutable discipline configuration history.")
    op.drop_table("analysis_v3_current_states")
    op.drop_table("analysis_v3_unknown_markers")
    op.drop_index("ix_analysis_v3_signal_type", table_name="analysis_v3_signals")
    op.drop_table("analysis_v3_signals")
    op.drop_table("analysis_v3_competency_gaps")
    op.drop_index("ix_analysis_v3_fact_type", table_name="analysis_v3_normalized_facts")
    op.drop_table("analysis_v3_normalized_facts")
    op.drop_table("analysis_v3_snapshot_details")
    op.drop_table("analysis_v3_run_lineages")
    op.drop_table("discipline_configuration_events")
    connection.execute(
        sa.text(
            "UPDATE projection_invalidations SET status = 'completed' "
            "WHERE status = 'superseded_no_handler'"
        )
    )
    with op.batch_alter_table("projection_invalidations") as batch:
        batch.drop_constraint("ck_projection_invalidation_status", type_="check")
        batch.create_check_constraint(
            "ck_projection_invalidation_status",
            "status IN ('pending','running','completed','permanent_failure')",
        )
