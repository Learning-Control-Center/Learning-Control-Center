"""Add prospective Today V2 advisory history."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_today_v2"
down_revision = "0016_recommendation_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "today_generations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("local_date", sa.String(10), nullable=False),
        sa.Column("timezone", sa.String(128), nullable=False),
        sa.Column("recommendation_run_id", sa.String(36), nullable=False),
        sa.Column("recommendation_policy_version", sa.String(64), nullable=False),
        sa.Column("today_policy_version", sa.String(64), nullable=False),
        sa.Column("explicit_generation_sequence", sa.Integer(), nullable=False),
        sa.Column("generation_key", sa.String(255), nullable=False, unique=True),
        sa.Column("generated_at", sa.Integer(), nullable=False),
        sa.Column("output_hash", sa.String(64), nullable=False),
        sa.Column("is_regeneration", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["recommendation_run_id"], ["recommendation_v2_runs.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "local_date",
            "explicit_generation_sequence",
            name="uq_today_generation_key",
        ),
        sa.CheckConstraint(
            "explicit_generation_sequence > 0", name="ck_today_generation_sequence"
        ),
        sa.CheckConstraint(
            "length(request_hash) = 64 AND length(output_hash) = 64",
            name="ck_today_generation_hashes",
        ),
    )
    op.create_index(
        "ix_today_generation_date_sequence",
        "today_generations",
        ["local_date", "explicit_generation_sequence"],
    )
    op.create_table(
        "today_suggestions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("generation_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("local_date", sa.String(10), nullable=False),
        sa.Column("timezone", sa.String(128), nullable=False),
        sa.Column("recommendation_run_id", sa.String(36), nullable=False),
        sa.Column("recommendation_id", sa.String(36), nullable=False),
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("portfolio_role", sa.String(16), nullable=False),
        sa.Column("advisory_duration_ms", sa.Integer()),
        sa.Column("duration_minimum_ms", sa.Integer()),
        sa.Column("duration_preferred_ms", sa.Integer()),
        sa.Column("duration_maximum_ms", sa.Integer()),
        sa.Column("presentation_json", sa.Text(), nullable=False),
        sa.Column("presentation_hash", sa.String(64), nullable=False),
        sa.Column("presentation_version", sa.String(64), nullable=False),
        sa.Column("today_policy_version", sa.String(64), nullable=False),
        sa.Column("generation_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("replaces_suggestion_id", sa.String(36)),
        sa.ForeignKeyConstraint(["generation_id"], ["today_generations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["recommendation_run_id"], ["recommendation_v2_runs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["recommendation_id"], ["recommendation_v2_recommendations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["recommendation_v2_candidates.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["replaces_suggestion_id"], ["today_suggestions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("generation_id", "ordinal", name="uq_today_suggestion_ordinal"),
        sa.UniqueConstraint(
            "generation_id", "recommendation_id", name="uq_today_suggestion_recommendation"
        ),
        sa.CheckConstraint("ordinal > 0", name="ck_today_suggestion_ordinal"),
        sa.CheckConstraint(
            "portfolio_role IN ('primary','complementary','maintenance')",
            name="ck_today_suggestion_portfolio_role",
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_today_suggestion_expiration_order"
        ),
        sa.CheckConstraint(
            "advisory_duration_ms IS NULL OR advisory_duration_ms > 0",
            name="ck_today_suggestion_advisory_duration",
        ),
        sa.CheckConstraint(
            "(duration_minimum_ms IS NULL AND duration_preferred_ms IS NULL "
            "AND duration_maximum_ms IS NULL AND advisory_duration_ms IS NULL) OR "
            "(duration_minimum_ms > 0 AND duration_preferred_ms >= duration_minimum_ms "
            "AND duration_maximum_ms >= duration_preferred_ms "
            "AND advisory_duration_ms >= duration_minimum_ms "
            "AND advisory_duration_ms <= duration_maximum_ms)",
            name="ck_today_suggestion_duration_shape",
        ),
        sa.CheckConstraint(
            "length(presentation_hash) = 64", name="ck_today_suggestion_presentation_hash"
        ),
        sa.CheckConstraint(
            "replaces_suggestion_id IS NULL OR replaces_suggestion_id != id",
            name="ck_today_suggestion_replacement_distinct",
        ),
    )
    op.create_index(
        "ix_today_suggestion_expiry", "today_suggestions", ["expires_at", "id"]
    )
    op.create_table(
        "today_interactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("suggestion_id", sa.String(36), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("interaction_type", sa.String(32), nullable=False),
        sa.Column("prior_status", sa.String(32), nullable=False),
        sa.Column("resulting_status", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(16), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("activity_id", sa.String(36)),
        sa.Column("session_id", sa.String(36)),
        sa.Column("replacement_suggestion_id", sa.String(36)),
        sa.Column("reason_code", sa.String(128)),
        sa.Column("structured_reason_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["suggestion_id"], ["today_suggestions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["session_id"], ["learning_sessions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["replacement_suggestion_id"], ["today_suggestions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "suggestion_id", "event_sequence", name="uq_today_interaction_sequence"
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_today_interaction_idempotency"),
        sa.CheckConstraint("event_sequence > 0", name="ck_today_interaction_sequence"),
        sa.CheckConstraint(
            "interaction_type IN ('viewed','accepted','started','completed',"
            "'partially_completed','skipped','replaced','expired')",
            name="ck_today_interaction_type",
        ),
        sa.CheckConstraint("actor IN ('user','system')", name="ck_today_interaction_actor"),
        sa.CheckConstraint(
            "length(payload_hash) = 64", name="ck_today_interaction_payload_hash"
        ),
        sa.CheckConstraint(
            "(interaction_type IN ('started','completed','partially_completed') "
            "AND activity_id IS NOT NULL AND session_id IS NOT NULL) OR "
            "(interaction_type = 'replaced' AND activity_id IS NOT NULL "
            "AND session_id IS NULL) OR "
            "(interaction_type IN ('viewed','accepted','skipped','expired') "
            "AND activity_id IS NULL AND session_id IS NULL)",
            name="ck_today_interaction_actuality_shape",
        ),
        sa.CheckConstraint(
            "replacement_suggestion_id IS NULL OR replacement_suggestion_id != suggestion_id",
            name="ck_today_interaction_replacement_distinct",
        ),
    )
    op.create_index(
        "ix_today_interaction_suggestion_time",
        "today_interactions",
        ["suggestion_id", "occurred_at"],
    )
    op.create_table(
        "today_interaction_corrections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("interaction_id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(16), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("resulting_status", sa.String(32), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("corrected_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["interaction_id"], ["today_interactions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("interaction_id", name="uq_today_interaction_correction_target"),
        sa.UniqueConstraint("idempotency_key", name="uq_today_interaction_correction_key"),
        sa.CheckConstraint(
            "length(payload_hash) = 64", name="ck_today_correction_payload_hash"
        ),
        sa.CheckConstraint("length(reason) > 0", name="ck_today_correction_reason"),
        sa.CheckConstraint(
            "actor IN ('user','system')", name="ck_today_interaction_correction_actor"
        ),
        sa.CheckConstraint(
            "resulting_status IN ('suggested','viewed','accepted','started','completed',"
            "'partially_completed','skipped','replaced','expired')",
            name="ck_today_correction_resulting_status",
        ),
    )
    op.create_table(
        "suggestion_activity_relations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("suggestion_id", sa.String(36), nullable=False),
        sa.Column("activity_id", sa.String(36), nullable=False),
        sa.Column("relation_type", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(16), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["suggestion_id"], ["today_suggestions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("idempotency_key", name="uq_suggestion_relation_idempotency"),
        sa.CheckConstraint(
            "relation_type IN ('matched','partially_matched','replaced')",
            name="ck_suggestion_activity_relation_type",
        ),
        sa.CheckConstraint(
            "actor IN ('user','system')", name="ck_suggestion_activity_relation_actor"
        ),
        sa.CheckConstraint(
            "length(payload_hash) = 64", name="ck_suggestion_relation_payload_hash"
        ),
    )
    op.create_index(
        "ix_suggestion_relation_suggestion",
        "suggestion_activity_relations",
        ["suggestion_id", "created_at"],
    )
    op.create_index(
        "ix_suggestion_relation_activity",
        "suggestion_activity_relations",
        ["activity_id", "created_at"],
    )
    op.create_table(
        "suggestion_activity_relation_corrections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("relation_id", sa.String(36), nullable=False),
        sa.Column("correction_type", sa.String(16), nullable=False),
        sa.Column("replacement_relation_id", sa.String(36)),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("corrected_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["relation_id"], ["suggestion_activity_relations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["replacement_relation_id"],
            ["suggestion_activity_relations.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_suggestion_relation_correction_key"),
        sa.UniqueConstraint("relation_id", name="uq_suggestion_relation_correction_target"),
        sa.CheckConstraint(
            "correction_type IN ('retracted','replaced')",
            name="ck_suggestion_relation_correction_type",
        ),
        sa.CheckConstraint(
            "(correction_type = 'retracted' AND replacement_relation_id IS NULL) OR "
            "(correction_type = 'replaced' AND replacement_relation_id IS NOT NULL)",
            name="ck_suggestion_relation_correction_shape",
        ),
        sa.CheckConstraint(
            "replacement_relation_id IS NULL OR replacement_relation_id != relation_id",
            name="ck_suggestion_relation_correction_distinct",
        ),
    )
    op.create_table(
        "today_suggestion_current_states",
        sa.Column("suggestion_id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("latest_interaction_id", sa.String(36)),
        sa.Column("terminal", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["suggestion_id"], ["today_suggestions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["latest_interaction_id"], ["today_interactions.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "status IN ('suggested','viewed','accepted','started','completed',"
            "'partially_completed','skipped','replaced','expired')",
            name="ck_today_current_status",
        ),
        sa.CheckConstraint("event_sequence >= 0", name="ck_today_current_sequence"),
        sa.CheckConstraint(
            "(event_sequence = 0 AND latest_interaction_id IS NULL AND status = 'suggested') OR "
            "(event_sequence > 0 AND latest_interaction_id IS NOT NULL)",
            name="ck_today_current_interaction_shape",
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    history_tables = (
        "today_generations",
        "today_suggestions",
        "today_interactions",
        "today_interaction_corrections",
        "suggestion_activity_relations",
        "suggestion_activity_relation_corrections",
    )
    populated = [
        table_name
        for table_name in history_tables
        if connection.execute(sa.text(f'SELECT 1 FROM "{table_name}" LIMIT 1')).first()
    ]
    if populated:
        raise RuntimeError(
            "Refusing to downgrade immutable Today V2 history; populated tables: "
            + ", ".join(populated)
        )
    op.drop_table("today_suggestion_current_states")
    op.drop_table("suggestion_activity_relation_corrections")
    op.drop_index("ix_suggestion_relation_activity", table_name="suggestion_activity_relations")
    op.drop_index("ix_suggestion_relation_suggestion", table_name="suggestion_activity_relations")
    op.drop_table("suggestion_activity_relations")
    op.drop_table("today_interaction_corrections")
    op.drop_index("ix_today_interaction_suggestion_time", table_name="today_interactions")
    op.drop_table("today_interactions")
    op.drop_index("ix_today_suggestion_expiry", table_name="today_suggestions")
    op.drop_table("today_suggestions")
    op.drop_index("ix_today_generation_date_sequence", table_name="today_generations")
    op.drop_table("today_generations")
