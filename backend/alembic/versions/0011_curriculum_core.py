"""Add canonical immutable Curriculum facts and Activity links."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_curriculum_core"
down_revision = "0010_capability_evaluation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "curricula",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("stable_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("creation_source", sa.String(64), nullable=False),
        sa.Column("retired_at", sa.Integer(), nullable=True),
        sa.Column("retirement_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "(retired_at IS NULL) = (retirement_reason IS NULL)",
            name="ck_curriculum_retirement_pair",
        ),
        sa.UniqueConstraint("stable_key", name="uq_curriculum_stable_key"),
    )
    op.create_table(
        "curriculum_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("curriculum_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("definition_payload_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("effective_at", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("creation_source", sa.String(64), nullable=False),
        sa.Column("supersedes_version_id", sa.String(36), nullable=True),
        sa.CheckConstraint("version > 0", name="ck_curriculum_version_positive"),
        sa.UniqueConstraint("curriculum_id", "version", name="uq_curriculum_version"),
        sa.ForeignKeyConstraint(["curriculum_id"], ["curricula.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["supersedes_version_id"], ["curriculum_versions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_index(
        "ix_curriculum_version_supersedes", "curriculum_versions", ["supersedes_version_id"]
    )
    op.create_table(
        "curriculum_objective_identities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("curriculum_id", sa.String(36), nullable=False),
        sa.Column("stable_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint("curriculum_id", "stable_key", name="uq_curriculum_objective_key"),
        sa.ForeignKeyConstraint(["curriculum_id"], ["curricula.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "learning_unit_identities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("curriculum_id", sa.String(36), nullable=False),
        sa.Column("stable_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint("curriculum_id", "stable_key", name="uq_learning_unit_key"),
        sa.ForeignKeyConstraint(["curriculum_id"], ["curricula.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "assessment_rubric_identities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("curriculum_id", sa.String(36), nullable=False),
        sa.Column("stable_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint("curriculum_id", "stable_key", name="uq_assessment_rubric_key"),
        sa.ForeignKeyConstraint(["curriculum_id"], ["curricula.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "curriculum_objective_definitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("curriculum_version_id", sa.String(36), nullable=False),
        sa.Column("objective_identity_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.CheckConstraint("order_index >= 0", name="ck_curriculum_objective_order"),
        sa.UniqueConstraint(
            "curriculum_version_id", "objective_identity_id", name="uq_curriculum_objective_def"
        ),
        sa.UniqueConstraint(
            "curriculum_version_id", "order_index", name="uq_curriculum_objective_order"
        ),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id"], ["curriculum_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["objective_identity_id"], ["curriculum_objective_identities.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "learning_unit_definitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("curriculum_version_id", sa.String(36), nullable=False),
        sa.Column("unit_identity_id", sa.String(36), nullable=False),
        sa.Column("objective_identity_id", sa.String(36), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("action_payload_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("provenance", sa.String(64), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("minimum_useful_duration_ms", sa.Integer(), nullable=True),
        sa.Column("preferred_duration_ms", sa.Integer(), nullable=True),
        sa.Column("maximum_useful_duration_ms", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('resource','exercise','practice_task','verification_template')",
            name="ck_learning_unit_kind",
        ),
        sa.CheckConstraint("order_index >= 0", name="ck_learning_unit_order"),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_learning_unit_status"),
        sa.CheckConstraint(
            "(minimum_useful_duration_ms IS NULL AND preferred_duration_ms IS NULL "
            "AND maximum_useful_duration_ms IS NULL) OR "
            "(minimum_useful_duration_ms > 0 AND preferred_duration_ms > 0 "
            "AND maximum_useful_duration_ms > 0 "
            "AND minimum_useful_duration_ms <= preferred_duration_ms "
            "AND preferred_duration_ms <= maximum_useful_duration_ms "
            "AND minimum_useful_duration_ms % 300000 = 0 "
            "AND preferred_duration_ms % 300000 = 0 "
            "AND maximum_useful_duration_ms % 300000 = 0)",
            name="ck_learning_unit_duration_range",
        ),
        sa.UniqueConstraint(
            "curriculum_version_id", "unit_identity_id", name="uq_learning_unit_definition"
        ),
        sa.UniqueConstraint("curriculum_version_id", "order_index", name="uq_learning_unit_order"),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id"], ["curriculum_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["unit_identity_id"], ["learning_unit_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["objective_identity_id"],
            ["curriculum_objective_identities.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_learning_unit_identity", "learning_unit_definitions", ["unit_identity_id"])
    op.create_index(
        "ix_learning_unit_objective", "learning_unit_definitions", ["objective_identity_id"]
    )
    op.create_table(
        "learning_unit_targets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("learning_unit_definition_id", sa.String(36), nullable=False),
        sa.Column("semantic_definition_id", sa.String(36), nullable=False),
        sa.Column("criterion_definition_id", sa.String(36), nullable=True),
        sa.Column("scale_version_id", sa.String(36), nullable=False),
        sa.Column("dimension_id", sa.String(36), nullable=True),
        sa.Column("intended_learning_outcome", sa.Text(), nullable=False),
        sa.Column("minimum_level_id", sa.String(36), nullable=True),
        sa.Column("maximum_level_id", sa.String(36), nullable=True),
        sa.Column("supports_unassessed", sa.Boolean(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.CheckConstraint("order_index >= 0", name="ck_unit_target_order"),
        sa.CheckConstraint(
            "supports_unassessed IN (0,1)", name="ck_unit_target_supports_unassessed"
        ),
        sa.CheckConstraint(
            "role IN ('primary','secondary','supporting')", name="ck_unit_target_role"
        ),
        sa.UniqueConstraint(
            "learning_unit_definition_id", "order_index", name="uq_unit_target_order"
        ),
        sa.ForeignKeyConstraint(
            ["learning_unit_definition_id"], ["learning_unit_definitions.id"], ondelete="RESTRICT"
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
        sa.ForeignKeyConstraint(
            ["minimum_level_id"], ["capability_scale_levels.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["maximum_level_id"], ["capability_scale_levels.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "learning_unit_requirements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("learning_unit_definition_id", sa.String(36), nullable=False),
        sa.Column("stable_key", sa.String(255), nullable=False),
        sa.Column("requirement_type", sa.String(64), nullable=False),
        sa.Column("effect", sa.String(16), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("subject_json", sa.Text(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "requirement_type IN ('capability_at_least','criterion_demonstrated',"
            "'learning_unit_completed','resource_available','user_constraint')",
            name="ck_unit_requirement_type",
        ),
        sa.CheckConstraint("effect IN ('hard','soft')", name="ck_unit_requirement_effect"),
        sa.CheckConstraint(
            "scope IN ('learner','curriculum','environment','user')",
            name="ck_unit_requirement_scope",
        ),
        sa.CheckConstraint("order_index >= 0", name="ck_unit_requirement_order"),
        sa.UniqueConstraint(
            "learning_unit_definition_id", "stable_key", name="uq_unit_requirement_key"
        ),
        sa.UniqueConstraint(
            "learning_unit_definition_id", "order_index", name="uq_unit_requirement_order"
        ),
        sa.ForeignKeyConstraint(
            ["learning_unit_definition_id"], ["learning_unit_definitions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "curriculum_evidence_opportunities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("learning_unit_definition_id", sa.String(36), nullable=False),
        sa.Column("stable_key", sa.String(255), nullable=False),
        sa.Column("evidence_kind", sa.String(64), nullable=False),
        sa.Column("possible_characteristics_json", sa.Text(), nullable=False),
        sa.Column("required_characteristics_json", sa.Text(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.CheckConstraint("order_index >= 0", name="ck_curriculum_opportunity_order"),
        sa.UniqueConstraint(
            "learning_unit_definition_id", "stable_key", name="uq_curriculum_opportunity_key"
        ),
        sa.UniqueConstraint(
            "learning_unit_definition_id", "order_index", name="uq_curriculum_opportunity_order"
        ),
        sa.ForeignKeyConstraint(
            ["learning_unit_definition_id"], ["learning_unit_definitions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "assessment_rubric_definitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("curriculum_version_id", sa.String(36), nullable=False),
        sa.Column("rubric_identity_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("rubric_json", sa.Text(), nullable=False),
        sa.Column("semantic_definition_id", sa.String(36), nullable=False),
        sa.Column("criterion_definition_id", sa.String(36), nullable=True),
        sa.UniqueConstraint(
            "curriculum_version_id", "rubric_identity_id", name="uq_assessment_rubric_definition"
        ),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id"], ["curriculum_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["rubric_identity_id"], ["assessment_rubric_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["semantic_definition_id"], ["semantic_competency_definitions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["criterion_definition_id"], ["criterion_definitions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "active_curriculum_version_states",
        sa.Column("curriculum_id", sa.String(36), primary_key=True),
        sa.Column("curriculum_version_id", sa.String(36), nullable=False),
        sa.Column("activated_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint("curriculum_version_id"),
        sa.ForeignKeyConstraint(["curriculum_id"], ["curricula.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id"], ["curriculum_versions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "curriculum_activation_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("curriculum_id", sa.String(36), nullable=False),
        sa.Column("from_curriculum_version_id", sa.String(36), nullable=True),
        sa.Column("to_curriculum_version_id", sa.String(36), nullable=False),
        sa.Column("activated_at", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.CheckConstraint("event_sequence > 0", name="ck_curriculum_activation_sequence"),
        sa.UniqueConstraint("curriculum_id", "event_sequence", name="uq_curriculum_activation_seq"),
        sa.UniqueConstraint("idempotency_key", name="uq_curriculum_activation_idempotency"),
        sa.ForeignKeyConstraint(["curriculum_id"], ["curricula.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["from_curriculum_version_id"], ["curriculum_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["to_curriculum_version_id"], ["curriculum_versions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_index(
        "ix_curriculum_activation_cutoff",
        "curriculum_activation_events",
        ["curriculum_id", "activated_at", "event_sequence"],
    )
    op.create_index(
        "ix_curriculum_activation_to_version",
        "curriculum_activation_events",
        ["to_curriculum_version_id"],
    )
    op.create_table(
        "activity_curriculum_unit_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("activity_id", sa.String(36), nullable=False),
        sa.Column("learning_unit_definition_id", sa.String(36), nullable=False),
        sa.Column("provenance", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.CheckConstraint(
            "provenance IN ('user_selected','user_confirmed','imported_asserted')",
            name="ck_activity_curriculum_link_provenance",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_activity_curriculum_link_idempotency"),
        sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["learning_unit_definition_id"], ["learning_unit_definitions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_index(
        "ix_activity_curriculum_link_activity",
        "activity_curriculum_unit_links",
        ["activity_id", "created_at"],
    )
    op.create_index(
        "ix_activity_curriculum_link_unit",
        "activity_curriculum_unit_links",
        ["learning_unit_definition_id", "created_at"],
    )
    op.create_table(
        "activity_curriculum_link_corrections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("activity_curriculum_unit_link_id", sa.String(36), nullable=False),
        sa.Column("replacement_link_id", sa.String(36), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("corrected_at", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.UniqueConstraint(
            "activity_curriculum_unit_link_id", name="uq_curriculum_link_correction"
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_curriculum_link_correction_idempotency"),
        sa.ForeignKeyConstraint(
            ["activity_curriculum_unit_link_id"],
            ["activity_curriculum_unit_links.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["replacement_link_id"], ["activity_curriculum_unit_links.id"], ondelete="RESTRICT"
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    populated = [
        table_name
        for table_name in (
            "curricula",
            "activity_curriculum_unit_links",
            "activity_curriculum_link_corrections",
        )
        if connection.execute(sa.text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar_one()
    ]
    if populated:
        raise RuntimeError(
            "Refusing to downgrade populated canonical Curriculum data: " + ", ".join(populated)
        )
    for table in (
        "activity_curriculum_link_corrections",
        "activity_curriculum_unit_links",
        "curriculum_activation_events",
        "active_curriculum_version_states",
        "assessment_rubric_definitions",
        "curriculum_evidence_opportunities",
        "learning_unit_requirements",
        "learning_unit_targets",
        "learning_unit_definitions",
        "curriculum_objective_definitions",
        "assessment_rubric_identities",
        "learning_unit_identities",
        "curriculum_objective_identities",
        "curriculum_versions",
        "curricula",
    ):
        op.drop_table(table)
