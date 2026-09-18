"""Add canonical immutable Project facts, events, attribution, and evaluations."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_project_core"
down_revision = "0011_curriculum_core"
branch_labels = None
depends_on = None


def _id() -> sa.Column[str]:
    return sa.Column("id", sa.String(36), primary_key=True)


def upgrade() -> None:
    op.create_table(
        "projects",
        _id(),
        sa.Column("stable_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("creation_source", sa.String(64), nullable=False),
        sa.UniqueConstraint("stable_key", name="uq_project_stable_key"),
    )
    op.create_table(
        "project_versions",
        _id(),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("definition_payload_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("effective_at", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("creation_source", sa.String(64), nullable=False),
        sa.Column("supersedes_version_id", sa.String(36)),
        sa.CheckConstraint("version > 0", name="ck_project_version_positive"),
        sa.UniqueConstraint("project_id", "version", name="uq_project_version"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["supersedes_version_id"], ["project_versions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_index("ix_project_version_supersedes", "project_versions", ["supersedes_version_id"])
    for table_name, unique_name in (
        ("project_goal_identities", "uq_project_goal_key"),
        ("project_task_identities", "uq_project_task_key"),
        ("project_criterion_identities", "uq_project_criterion_key"),
        ("project_milestone_identities", "uq_project_milestone_key"),
    ):
        op.create_table(
            table_name,
            _id(),
            sa.Column("project_id", sa.String(36), nullable=False),
            sa.Column("stable_key", sa.String(255), nullable=False),
            sa.Column("created_at", sa.Integer(), nullable=False),
            sa.UniqueConstraint("project_id", "stable_key", name=unique_name),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        )
    op.create_table(
        "project_goal_definitions",
        _id(),
        sa.Column("project_version_id", sa.String(36), nullable=False),
        sa.Column("goal_identity_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.CheckConstraint("order_index >= 0", name="ck_project_goal_order"),
        sa.UniqueConstraint("project_version_id", "goal_identity_id", name="uq_project_goal_def"),
        sa.UniqueConstraint("project_version_id", "order_index", name="uq_project_goal_order"),
        sa.ForeignKeyConstraint(
            ["project_version_id"], ["project_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["goal_identity_id"], ["project_goal_identities.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "project_milestone_definitions",
        _id(),
        sa.Column("project_version_id", sa.String(36), nullable=False),
        sa.Column("milestone_identity_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.CheckConstraint("order_index >= 0", name="ck_project_milestone_order"),
        sa.UniqueConstraint(
            "project_version_id", "milestone_identity_id", name="uq_project_milestone_def"
        ),
        sa.UniqueConstraint("project_version_id", "order_index", name="uq_project_milestone_order"),
        sa.ForeignKeyConstraint(
            ["project_version_id"], ["project_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["milestone_identity_id"], ["project_milestone_identities.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "project_task_definitions",
        _id(),
        sa.Column("project_version_id", sa.String(36), nullable=False),
        sa.Column("task_identity_id", sa.String(36), nullable=False),
        sa.Column("milestone_identity_id", sa.String(36)),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("minimum_useful_duration_ms", sa.Integer()),
        sa.Column("preferred_duration_ms", sa.Integer()),
        sa.Column("maximum_useful_duration_ms", sa.Integer()),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_project_task_status"),
        sa.CheckConstraint("order_index >= 0", name="ck_project_task_order"),
        sa.CheckConstraint(
            "(minimum_useful_duration_ms IS NULL "
            "AND preferred_duration_ms IS NULL "
            "AND maximum_useful_duration_ms IS NULL) OR "
            "(minimum_useful_duration_ms > 0 "
            "AND preferred_duration_ms > 0 "
            "AND maximum_useful_duration_ms > 0 "
            "AND minimum_useful_duration_ms <= preferred_duration_ms "
            "AND preferred_duration_ms <= maximum_useful_duration_ms "
            "AND minimum_useful_duration_ms % 300000 = 0 "
            "AND preferred_duration_ms % 300000 = 0 "
            "AND maximum_useful_duration_ms % 300000 = 0)",
            name="ck_project_task_duration_range",
        ),
        sa.UniqueConstraint("project_version_id", "task_identity_id", name="uq_project_task_def"),
        sa.UniqueConstraint("project_version_id", "order_index", name="uq_project_task_order"),
        sa.ForeignKeyConstraint(
            ["project_version_id"], ["project_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["task_identity_id"], ["project_task_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["milestone_identity_id"], ["project_milestone_identities.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "project_criterion_definitions",
        _id(),
        sa.Column("project_version_id", sa.String(36), nullable=False),
        sa.Column("criterion_identity_id", sa.String(36), nullable=False),
        sa.Column("milestone_identity_id", sa.String(36)),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("evaluation_policy_version", sa.String(64), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.CheckConstraint("order_index >= 0", name="ck_project_criterion_order"),
        sa.UniqueConstraint(
            "project_version_id", "criterion_identity_id", name="uq_project_criterion_def"
        ),
        sa.UniqueConstraint("project_version_id", "order_index", name="uq_project_criterion_order"),
        sa.ForeignKeyConstraint(
            ["project_version_id"], ["project_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["criterion_identity_id"], ["project_criterion_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["milestone_identity_id"], ["project_milestone_identities.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "project_targets",
        _id(),
        sa.Column("project_version_id", sa.String(36), nullable=False),
        sa.Column("task_definition_id", sa.String(36)),
        sa.Column("project_criterion_definition_id", sa.String(36)),
        sa.Column("semantic_definition_id", sa.String(36), nullable=False),
        sa.Column("criterion_definition_id", sa.String(36)),
        sa.Column("scale_version_id", sa.String(36), nullable=False),
        sa.Column("dimension_id", sa.String(36)),
        sa.Column("level_id", sa.String(36)),
        sa.Column("intended_outcome", sa.Text(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "role IN ('primary','secondary','supporting')", name="ck_project_target_role"
        ),
        sa.CheckConstraint("order_index >= 0", name="ck_project_target_order"),
        sa.CheckConstraint(
            "NOT (task_definition_id IS NOT NULL AND project_criterion_definition_id IS NOT NULL)",
            name="ck_project_target_owner",
        ),
        sa.UniqueConstraint("project_version_id", "order_index", name="uq_project_target_order"),
        sa.ForeignKeyConstraint(
            ["project_version_id"], ["project_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["task_definition_id"], ["project_task_definitions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["project_criterion_definition_id"],
            ["project_criterion_definitions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["semantic_definition_id"], ["semantic_competency_definitions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["criterion_definition_id"], ["criterion_definitions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["scale_version_id"], ["capability_scale_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["dimension_id"], ["capability_scale_dimensions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["level_id"], ["capability_scale_levels.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "project_requirements",
        _id(),
        sa.Column("project_version_id", sa.String(36), nullable=False),
        sa.Column("task_definition_id", sa.String(36)),
        sa.Column("stable_key", sa.String(255), nullable=False),
        sa.Column("requirement_type", sa.String(64), nullable=False),
        sa.Column("effect", sa.String(16), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("subject_json", sa.Text(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "requirement_type IN ('capability_at_least','criterion_demonstrated',"
            "'project_criterion_demonstrated','project_task_completed',"
            "'resource_available','user_constraint')",
            name="ck_project_requirement_type",
        ),
        sa.CheckConstraint("effect IN ('hard','soft')", name="ck_project_requirement_effect"),
        sa.CheckConstraint(
            "scope IN ('learner','project','environment','user')",
            name="ck_project_requirement_scope",
        ),
        sa.CheckConstraint("order_index >= 0", name="ck_project_requirement_order"),
        sa.UniqueConstraint("project_version_id", "stable_key", name="uq_project_requirement_key"),
        sa.UniqueConstraint(
            "project_version_id", "order_index", name="uq_project_requirement_order"
        ),
        sa.ForeignKeyConstraint(
            ["project_version_id"], ["project_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["task_definition_id"], ["project_task_definitions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "project_task_dependencies",
        _id(),
        sa.Column("project_version_id", sa.String(36), nullable=False),
        sa.Column("dependent_task_identity_id", sa.String(36), nullable=False),
        sa.Column("prerequisite_task_identity_id", sa.String(36), nullable=False),
        sa.Column("dependency_type", sa.String(32), nullable=False),
        sa.CheckConstraint(
            "dependency_type IN ('hard','recommended_before')", name="ck_project_dependency_type"
        ),
        sa.CheckConstraint(
            "dependent_task_identity_id != prerequisite_task_identity_id",
            name="ck_project_dependency_self",
        ),
        sa.UniqueConstraint(
            "project_version_id",
            "dependent_task_identity_id",
            "prerequisite_task_identity_id",
            "dependency_type",
            name="uq_project_task_dependency",
        ),
        sa.ForeignKeyConstraint(
            ["project_version_id"], ["project_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["dependent_task_identity_id"], ["project_task_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["prerequisite_task_identity_id"], ["project_task_identities.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "project_evidence_opportunities",
        _id(),
        sa.Column("project_version_id", sa.String(36), nullable=False),
        sa.Column("task_definition_id", sa.String(36)),
        sa.Column("project_criterion_definition_id", sa.String(36)),
        sa.Column("stable_key", sa.String(255), nullable=False),
        sa.Column("evidence_kind", sa.String(64), nullable=False),
        sa.Column("intended_characteristics_json", sa.Text(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.CheckConstraint("order_index >= 0", name="ck_project_opportunity_order"),
        sa.UniqueConstraint("project_version_id", "stable_key", name="uq_project_opportunity_key"),
        sa.UniqueConstraint(
            "project_version_id", "order_index", name="uq_project_opportunity_order"
        ),
        sa.ForeignKeyConstraint(
            ["project_version_id"], ["project_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["task_definition_id"], ["project_task_definitions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["project_criterion_definition_id"],
            ["project_criterion_definitions.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "active_project_version_states",
        sa.Column("project_id", sa.String(36), primary_key=True),
        sa.Column("project_version_id", sa.String(36), nullable=False, unique=True),
        sa.Column("activated_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["project_version_id"], ["project_versions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "project_version_activation_events",
        _id(),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("from_project_version_id", sa.String(36)),
        sa.Column("to_project_version_id", sa.String(36), nullable=False),
        sa.Column("activated_at", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.CheckConstraint("event_sequence > 0", name="ck_project_activation_sequence"),
        sa.UniqueConstraint("project_id", "event_sequence", name="uq_project_activation_seq"),
        sa.UniqueConstraint("idempotency_key", name="uq_project_activation_idempotency"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["from_project_version_id"], ["project_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["to_project_version_id"], ["project_versions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_index(
        "ix_project_activation_cutoff",
        "project_version_activation_events",
        ["project_id", "activated_at", "event_sequence"],
    )
    op.create_table(
        "project_events",
        _id(),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("project_version_id", sa.String(36), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("task_identity_id", sa.String(36)),
        sa.Column("project_lifecycle_state", sa.String(16)),
        sa.Column("task_lifecycle_state", sa.String(16)),
        sa.Column("blocker_key", sa.String(255)),
        sa.Column("blocker_actionable", sa.Boolean()),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("command_hash", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.Integer(), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("corrects_event_id", sa.String(36)),
        sa.CheckConstraint("event_sequence > 0", name="ck_project_event_sequence"),
        sa.CheckConstraint(
            "event_type IN ('project_lifecycle','task_lifecycle','blocker_opened',"
            "'blocker_resolved','blocker_corrected')",
            name="ck_project_event_type",
        ),
        sa.CheckConstraint(
            "project_lifecycle_state IS NULL OR project_lifecycle_state IN "
            "('planned','active','completed','archived')",
            name="ck_project_lifecycle_state",
        ),
        sa.CheckConstraint(
            "task_lifecycle_state IS NULL OR task_lifecycle_state IN "
            "('not_started','started','completed','cancelled')",
            name="ck_project_task_state",
        ),
        sa.UniqueConstraint("project_id", "event_sequence", name="uq_project_event_sequence"),
        sa.UniqueConstraint("idempotency_key", name="uq_project_event_idempotency"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["project_version_id"], ["project_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["task_identity_id"], ["project_task_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["corrects_event_id"], ["project_events.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "ix_project_event_cutoff", "project_events", ["project_id", "occurred_at", "event_sequence"]
    )
    op.create_table(
        "activity_project_task_links",
        _id(),
        sa.Column("activity_id", sa.String(36), nullable=False),
        sa.Column("task_definition_id", sa.String(36), nullable=False),
        sa.Column("provenance", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.CheckConstraint(
            "provenance IN ('user_selected','user_confirmed','imported_asserted')",
            name="ck_activity_project_link_provenance",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_activity_project_link_idempotency"),
        sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["task_definition_id"], ["project_task_definitions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_index(
        "ix_activity_project_link_activity",
        "activity_project_task_links",
        ["activity_id", "created_at"],
    )
    op.create_index(
        "ix_activity_project_link_task",
        "activity_project_task_links",
        ["task_definition_id", "created_at"],
    )
    op.create_table(
        "activity_project_task_link_corrections",
        _id(),
        sa.Column("activity_project_task_link_id", sa.String(36), nullable=False),
        sa.Column("replacement_link_id", sa.String(36)),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("corrected_at", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.UniqueConstraint("activity_project_task_link_id", name="uq_activity_project_correction"),
        sa.UniqueConstraint("idempotency_key", name="uq_activity_project_correction_idempotency"),
        sa.ForeignKeyConstraint(
            ["activity_project_task_link_id"],
            ["activity_project_task_links.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["replacement_link_id"], ["activity_project_task_links.id"], ondelete="RESTRICT"
        ),
    )
    op.create_index(
        "ix_activity_project_correction_replacement",
        "activity_project_task_link_corrections",
        ["replacement_link_id"],
    )
    op.create_table(
        "session_project_contributions",
        _id(),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("project_version_id", sa.String(36), nullable=False),
        sa.Column("task_definition_id", sa.String(36)),
        sa.Column("relevance", sa.String(16), nullable=False),
        sa.Column("provenance", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("command_hash", sa.String(64)),
        sa.CheckConstraint(
            "relevance IN ('primary','secondary','supporting')", name="ck_session_project_relevance"
        ),
        sa.CheckConstraint(
            "provenance IN ('user_selected','user_confirmed','imported_asserted')",
            name="ck_session_project_provenance",
        ),
        sa.CheckConstraint(
            "(idempotency_key IS NULL) = (command_hash IS NULL)",
            name="ck_session_project_command_identity",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_session_project_idempotency"),
        sa.ForeignKeyConstraint(["session_id"], ["learning_sessions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["project_version_id"], ["project_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["task_definition_id"], ["project_task_definitions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "session_project_contribution_retractions",
        _id(),
        sa.Column("contribution_id", sa.String(36), nullable=False),
        sa.Column("replacement_contribution_id", sa.String(36)),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("retracted_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint("contribution_id", name="uq_session_project_retraction"),
        sa.ForeignKeyConstraint(
            ["contribution_id"], ["session_project_contributions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["replacement_contribution_id"],
            ["session_project_contributions.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_session_project_retraction_replacement",
        "session_project_contribution_retractions",
        ["replacement_contribution_id"],
    )
    op.create_table(
        "project_criterion_evaluations",
        _id(),
        sa.Column("project_criterion_definition_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("evaluated_at", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("facts_json", sa.Text(), nullable=False),
        sa.Column("evidence_set_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.CheckConstraint(
            "state IN ('unknown','not_demonstrated','partially_demonstrated','demonstrated')",
            name="ck_project_criterion_eval_state",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_project_criterion_eval_idempotency"),
        sa.ForeignKeyConstraint(
            ["project_criterion_definition_id"],
            ["project_criterion_definitions.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_project_criterion_eval_cutoff",
        "project_criterion_evaluations",
        ["project_criterion_definition_id", "evaluated_at"],
    )
    op.create_table(
        "project_criterion_evaluation_evidence",
        sa.Column("project_criterion_evaluation_id", sa.String(36), primary_key=True),
        sa.Column("evidence_id", sa.String(36), primary_key=True),
        sa.ForeignKeyConstraint(
            ["project_criterion_evaluation_id"],
            ["project_criterion_evaluations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"], ondelete="RESTRICT"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    populated = [
        name
        for name in (
            "projects",
            "project_events",
            "activity_project_task_links",
            "project_criterion_evaluations",
        )
        if connection.execute(sa.text(f'SELECT COUNT(*) FROM "{name}"')).scalar_one()
    ]
    if populated:
        raise RuntimeError(
            "Refusing to downgrade populated canonical Project data: " + ", ".join(populated)
        )
    for table in (
        "project_criterion_evaluation_evidence",
        "project_criterion_evaluations",
        "session_project_contribution_retractions",
        "session_project_contributions",
        "activity_project_task_link_corrections",
        "activity_project_task_links",
        "project_events",
        "project_version_activation_events",
        "active_project_version_states",
        "project_evidence_opportunities",
        "project_task_dependencies",
        "project_requirements",
        "project_targets",
        "project_criterion_definitions",
        "project_task_definitions",
        "project_milestone_definitions",
        "project_goal_definitions",
        "project_milestone_identities",
        "project_criterion_identities",
        "project_task_identities",
        "project_goal_identities",
        "project_versions",
        "projects",
    ):
        op.drop_table(table)
