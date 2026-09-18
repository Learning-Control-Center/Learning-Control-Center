"""Add immutable deterministic Recommendation V2 history."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_recommendation_v2"
down_revision = "0015_analysis_v3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recommendation_v2_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("analysis_snapshot_id", sa.String(36), nullable=False),
        sa.Column("generated_at", sa.Integer(), nullable=False),
        sa.Column("cutoff_at", sa.Integer(), nullable=False),
        sa.Column("local_date", sa.String(10), nullable=False),
        sa.Column("available_time_ms", sa.Integer()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("algorithm_version", sa.String(64), nullable=False),
        sa.Column("policy_registry_version", sa.String(64), nullable=False),
        sa.Column("policy_bundle_json", sa.Text(), nullable=False),
        sa.Column("policy_bundle_hash", sa.String(64), nullable=False),
        sa.Column("frozen_input_json", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("output_hash", sa.String(64)),
        sa.Column("target_profile_version_id", sa.String(36)),
        sa.Column("learning_graph_version_id", sa.String(36)),
        sa.Column("curriculum_reference", sa.String(64)),
        sa.Column("project_reference", sa.String(64)),
        sa.Column("user_constraints_hash", sa.String(64), nullable=False),
        sa.Column("semantic_definition_references_json", sa.Text(), nullable=False),
        sa.Column("capability_scale_version_references_json", sa.Text(), nullable=False),
        sa.Column("completeness", sa.String(16), nullable=False),
        sa.Column("application_version", sa.String(32), nullable=False),
        sa.Column("replay_of_run_id", sa.String(36)),
        sa.Column("failure_metadata_json", sa.Text()),
        sa.ForeignKeyConstraint(
            ["analysis_snapshot_id"], ["analysis_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["replay_of_run_id"], ["recommendation_v2_runs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_profile_version_id"], ["target_profile_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["learning_graph_version_id"], ["learning_graph_versions.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "status IN ('completed','failed')", name="ck_recommendation_v2_run_status"
        ),
        sa.CheckConstraint(
            "available_time_ms IS NULL OR available_time_ms >= 0",
            name="ck_recommendation_v2_available_time",
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND output_hash IS NOT NULL "
            "AND failure_metadata_json IS NULL) OR "
            "(status = 'failed' AND output_hash IS NULL "
            "AND failure_metadata_json IS NOT NULL)",
            name="ck_recommendation_v2_run_outcome",
        ),
        sa.CheckConstraint(
            "completeness IN ('complete','partial')",
            name="ck_recommendation_v2_run_completeness",
        ),
        sa.CheckConstraint(
            "length(policy_bundle_hash) = 64 AND length(input_hash) = 64 "
            "AND (output_hash IS NULL OR length(output_hash) = 64) "
            "AND length(user_constraints_hash) = 64",
            name="ck_recommendation_v2_run_hashes",
        ),
    )
    op.create_index(
        "ix_recommendation_v2_run_generated",
        "recommendation_v2_runs",
        ["generated_at", "id"],
    )
    op.create_table(
        "recommendation_v2_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("stable_id", sa.String(512), nullable=False),
        sa.Column("candidate_key", sa.String(512), nullable=False),
        sa.Column("stable_tie_key", sa.String(512), nullable=False),
        sa.Column("candidate_type", sa.String(32), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("source_entity_id", sa.String(255), nullable=False),
        sa.Column("source_version_id", sa.String(255)),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("target_identity_id", sa.String(36)),
        sa.Column("served_target_identity_ids_json", sa.Text(), nullable=False),
        sa.Column("primary_need_kind", sa.String(32), nullable=False),
        sa.Column("primary_need_identity", sa.String(512), nullable=False),
        sa.Column("competency_identity_id", sa.String(36)),
        sa.Column("criterion_definition_id", sa.String(36)),
        sa.Column("project_id", sa.String(36)),
        sa.Column("duration_minimum_ms", sa.Integer()),
        sa.Column("duration_preferred_ms", sa.Integer()),
        sa.Column("duration_maximum_ms", sa.Integer()),
        sa.Column("candidate_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["recommendation_v2_runs.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("run_id", "ordinal", name="uq_recommendation_v2_candidate_ordinal"),
        sa.UniqueConstraint("run_id", "stable_id", name="uq_recommendation_v2_candidate_stable"),
        sa.CheckConstraint("ordinal >= 0", name="ck_recommendation_v2_candidate_ordinal"),
        sa.CheckConstraint(
            "candidate_type IN ('curriculum_unit','practice_task','verification','assessment',"
            "'review','maintenance','project_task','unblock_task')",
            name="ck_recommendation_v2_candidate_type",
        ),
        sa.CheckConstraint(
            "(duration_minimum_ms IS NULL AND duration_preferred_ms IS NULL "
            "AND duration_maximum_ms IS NULL) OR "
            "(duration_minimum_ms > 0 AND duration_minimum_ms <= duration_preferred_ms "
            "AND duration_preferred_ms <= duration_maximum_ms)",
            name="ck_recommendation_v2_candidate_duration",
        ),
    )
    op.create_index(
        "ix_recommendation_v2_candidate_run_type",
        "recommendation_v2_candidates",
        ["run_id", "candidate_type"],
    )
    op.create_table(
        "recommendation_v2_eligibility_decisions",
        sa.Column("candidate_id", sa.String(36), primary_key=True),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("reason_code", sa.String(128), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["recommendation_v2_candidates.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "recommendation_v2_eligibility_rule_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("rule_code", sa.String(128), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("decisive", sa.Boolean(), nullable=False),
        sa.Column("facts_json", sa.Text(), nullable=False),
        sa.Column("subject_ids_json", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["recommendation_v2_candidates.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("candidate_id", "ordinal", name="uq_recommendation_v2_rule_ordinal"),
        sa.UniqueConstraint("candidate_id", "rule_code", name="uq_recommendation_v2_rule_code"),
        sa.CheckConstraint("ordinal >= 0", name="ck_recommendation_v2_rule_ordinal"),
        sa.CheckConstraint(
            "outcome IN ('pass','fail','unknown','not_applicable')",
            name="ck_recommendation_v2_rule_outcome",
        ),
    )
    op.create_table(
        "recommendation_v2_expected_values",
        sa.Column("candidate_id", sa.String(36), primary_key=True),
        sa.Column("value", sa.String(16), nullable=False),
        sa.Column("reason_codes_json", sa.Text(), nullable=False),
        sa.Column("matched_facts_json", sa.Text(), nullable=False),
        sa.Column("deciding_rule_code", sa.String(128), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["recommendation_v2_candidates.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "value IN ('unknown','low','normal','high','very_high')",
            name="ck_recommendation_v2_elv",
        ),
    )
    op.create_table(
        "recommendation_v2_score_components",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("component_code", sa.String(64), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("allowed_minimum", sa.Integer(), nullable=False),
        sa.Column("allowed_maximum", sa.Integer(), nullable=False),
        sa.Column("decisive_facts_json", sa.Text(), nullable=False),
        sa.Column("source_ids_json", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["recommendation_v2_candidates.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "candidate_id", "component_code", name="uq_recommendation_v2_component"
        ),
        sa.CheckConstraint(
            "value >= -8 AND value <= 24", name="ck_recommendation_v2_component_value"
        ),
        sa.CheckConstraint(
            "component_code IN ('TARGET_PRIORITY','PRIMARY_NEED','DEADLINE_PRESSURE',"
            "'ALLOCATION_BALANCE','NEGLECT_OR_STALL','EXPECTED_LEARNING_VALUE','CONTEXT_COST')",
            name="ck_recommendation_v2_component_code",
        ),
        sa.CheckConstraint(
            "(component_code = 'TARGET_PRIORITY' AND value BETWEEN 2 AND 20) OR "
            "(component_code = 'PRIMARY_NEED' AND value BETWEEN 0 AND 24) OR "
            "(component_code = 'DEADLINE_PRESSURE' AND value BETWEEN 0 AND 12) OR "
            "(component_code = 'ALLOCATION_BALANCE' AND value BETWEEN -8 AND 8) OR "
            "(component_code = 'NEGLECT_OR_STALL' AND value BETWEEN 0 AND 10) OR "
            "(component_code = 'EXPECTED_LEARNING_VALUE' AND value BETWEEN 0 AND 12) OR "
            "(component_code = 'CONTEXT_COST' AND value BETWEEN -4 AND 0)",
            name="ck_recommendation_v2_component_code_value",
        ),
    )
    op.create_table(
        "recommendation_v2_selection_decisions",
        sa.Column("candidate_id", sa.String(36), primary_key=True),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("portfolio_role", sa.String(16)),
        sa.Column("reason_code", sa.String(128), nullable=False),
        sa.Column("rank_ordinal", sa.Integer()),
        sa.Column("score_total", sa.Integer()),
        sa.Column("advisory_duration_ms", sa.Integer()),
        sa.Column("duration_reason_code", sa.String(128)),
        sa.Column("admission_ordinal", sa.Integer()),
        sa.Column("displaced_by_candidate_stable_id", sa.String(512)),
        sa.Column("decisive_facts_json", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["recommendation_v2_candidates.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "decision IN ('selected','not_selected','ineligible')",
            name="ck_recommendation_v2_selection_decision",
        ),
        sa.CheckConstraint(
            "portfolio_role IS NULL OR portfolio_role IN ('primary','complementary','maintenance')",
            name="ck_recommendation_v2_portfolio_role",
        ),
        sa.CheckConstraint(
            "(decision = 'selected' AND portfolio_role IS NOT NULL AND rank_ordinal > 0 "
            "AND score_total IS NOT NULL AND admission_ordinal > 0) OR "
            "(decision = 'not_selected' AND portfolio_role IS NULL AND rank_ordinal > 0 "
            "AND score_total IS NOT NULL AND advisory_duration_ms IS NULL "
            "AND admission_ordinal IS NULL) OR "
            "(decision = 'ineligible' AND portfolio_role IS NULL AND rank_ordinal IS NULL "
            "AND score_total IS NULL AND advisory_duration_ms IS NULL "
            "AND admission_ordinal IS NULL)",
            name="ck_recommendation_v2_selection_shape",
        ),
        sa.CheckConstraint(
            "score_total IS NULL OR score_total BETWEEN -10 AND 86",
            name="ck_recommendation_v2_selection_score",
        ),
    )
    op.create_table(
        "recommendation_v2_recommendations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("candidate_id", sa.String(36), nullable=False, unique=True),
        sa.Column("portfolio_role", sa.String(16), nullable=False),
        sa.Column("advisory_duration_ms", sa.Integer()),
        sa.Column("duration_minimum_ms", sa.Integer()),
        sa.Column("duration_preferred_ms", sa.Integer()),
        sa.Column("duration_maximum_ms", sa.Integer()),
        sa.Column("rank_ordinal", sa.Integer(), nullable=False),
        sa.Column("score_total", sa.Integer(), nullable=False),
        sa.Column("score_breakdown_hash", sa.String(64), nullable=False),
        sa.Column("selection_reason_code", sa.String(128), nullable=False),
        sa.Column("algorithm_version", sa.String(64), nullable=False),
        sa.Column("analysis_snapshot_id", sa.String(36), nullable=False),
        sa.Column("presentation_version", sa.String(64), nullable=False),
        sa.Column("reason_summary", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["recommendation_v2_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["recommendation_v2_candidates.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["analysis_snapshot_id"], ["analysis_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("run_id", "portfolio_role", name="uq_recommendation_v2_run_role"),
        sa.CheckConstraint(
            "portfolio_role IN ('primary','complementary','maintenance')",
            name="ck_recommendation_v2_recommendation_role",
        ),
        sa.CheckConstraint("rank_ordinal > 0", name="ck_recommendation_v2_recommendation_rank"),
        sa.CheckConstraint(
            "score_total BETWEEN -10 AND 86",
            name="ck_recommendation_v2_recommendation_score",
        ),
        sa.CheckConstraint(
            "(duration_minimum_ms IS NULL AND duration_preferred_ms IS NULL "
            "AND duration_maximum_ms IS NULL) OR "
            "(duration_minimum_ms > 0 AND duration_minimum_ms <= duration_preferred_ms "
            "AND duration_preferred_ms <= duration_maximum_ms)",
            name="ck_recommendation_v2_recommendation_duration",
        ),
    )
    op.create_table(
        "recommendation_v2_reasons",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(128), nullable=False),
        sa.Column("explanation_facts_json", sa.Text(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("score_contribution", sa.Integer()),
        sa.Column("template_key", sa.String(128), nullable=False),
        sa.Column("template_version", sa.String(64), nullable=False),
        sa.Column("rendered_text", sa.Text(), nullable=False),
        sa.Column("source_ids_json", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["recommendation_v2_candidates.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("candidate_id", "ordinal", name="uq_recommendation_v2_reason_ordinal"),
        sa.CheckConstraint("ordinal >= 0", name="ck_recommendation_v2_reason_ordinal"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    history_tables = (
        "recommendation_v2_runs",
        "recommendation_v2_candidates",
        "recommendation_v2_eligibility_decisions",
        "recommendation_v2_eligibility_rule_results",
        "recommendation_v2_expected_values",
        "recommendation_v2_score_components",
        "recommendation_v2_selection_decisions",
        "recommendation_v2_recommendations",
        "recommendation_v2_reasons",
    )
    populated = [
        table_name
        for table_name in history_tables
        if connection.execute(sa.text(f'SELECT 1 FROM "{table_name}" LIMIT 1')).first()
    ]
    if populated:
        raise RuntimeError(
            "Refusing to downgrade immutable Recommendation V2 history; populated tables: "
            + ", ".join(populated)
        )
    op.drop_table("recommendation_v2_reasons")
    op.drop_table("recommendation_v2_recommendations")
    op.drop_table("recommendation_v2_selection_decisions")
    op.drop_table("recommendation_v2_score_components")
    op.drop_table("recommendation_v2_expected_values")
    op.drop_table("recommendation_v2_eligibility_rule_results")
    op.drop_table("recommendation_v2_eligibility_decisions")
    op.drop_index(
        "ix_recommendation_v2_candidate_run_type", table_name="recommendation_v2_candidates"
    )
    op.drop_table("recommendation_v2_candidates")
    op.drop_index("ix_recommendation_v2_run_generated", table_name="recommendation_v2_runs")
    op.drop_table("recommendation_v2_runs")
