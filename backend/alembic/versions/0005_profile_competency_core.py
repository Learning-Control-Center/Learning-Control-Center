"""Add target profiles, semantic competencies, scales, and legacy criteria."""
# ruff: noqa: E501

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from app.compatibility.v1.profile_competency_backfill import (
    POLICY_KEY,
    RUN_ID,
    build_profile_competency_backfill,
)

revision = "0005_profile_competency_core"
down_revision = "0004_analysis_projection_foundation"
branch_labels = None
depends_on = None

NAMESPACE = uuid.UUID("c20b6799-bc36-5db4-92af-3eae1f16bcb3")
BUILTIN_CREATED_AT = 1_788_912_000_000

TECHNICAL_LEVELS = (
    (
        "unexposed",
        0,
        "Unexposed",
        "Explicit current information shows no meaningful encounter or performance; absence of evidence is insufficient.",
    ),
    (
        "familiar",
        1,
        "Familiar",
        "Can recognize the competency, explain its purpose and vocabulary, and follow representative examples without claiming task performance.",
    ),
    (
        "guided",
        2,
        "Guided",
        "Can complete representative bounded tasks when a guide supplies material steps or decisions.",
    ),
    (
        "independent",
        3,
        "Independent",
        "Can select an approach, complete representative tasks with ordinary reference documentation, and handle defined common failures.",
    ),
    (
        "strong",
        4,
        "Strong",
        "Repeatedly performs independently across varied non-trivial contexts, explains trade-offs, handles important failures, and produces maintainable outcomes.",
    ),
    (
        "advanced",
        5,
        "Advanced",
        "Independently handles complex or novel contexts, adapts alternatives, diagnoses systemic failures, and can design, review, or teach within scope.",
    ),
)

CEFR_LEVELS = (
    (
        "a1",
        1,
        "A1",
        "Can understand and use very basic familiar language and interact simply with substantial contextual support.",
    ),
    (
        "a2",
        2,
        "A2",
        "Can handle simple routine communication and describe immediate needs or familiar matters with limited complexity.",
    ),
    (
        "b1",
        3,
        "B1",
        "Can understand clear standard input, manage common independent situations, and produce connected communication on familiar topics.",
    ),
    (
        "b2",
        4,
        "B2",
        "Can understand complex material, interact with practical fluency, and communicate detailed positions across a broad range of topics.",
    ),
    (
        "c1",
        5,
        "C1",
        "Can understand demanding material, communicate fluently and flexibly, and produce well-structured language for complex purposes.",
    ),
    (
        "c2",
        6,
        "C2",
        "Can integrate difficult information from varied sources and communicate precisely with fine distinctions in highly complex contexts.",
    ),
)

CEFR_DIMENSIONS = ("speaking", "listening", "reading", "writing", "grammar", "vocabulary")


def _id(value: str) -> str:
    return str(uuid.uuid5(NAMESPACE, value))


def upgrade() -> None:
    with op.batch_alter_table("competency_identities") as batch:
        batch.add_column(sa.Column("identity_created_at", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("creation_source", sa.String(64), nullable=True))
        batch.add_column(sa.Column("legacy_unspecified_reason", sa.Text(), nullable=True))
        batch.add_column(sa.Column("retired_at", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("retirement_reason", sa.Text(), nullable=True))
    op.execute(
        "UPDATE competency_identities SET legacy_unspecified_reason = "
        "'Creation metadata predates the V2 identity contract.'"
    )

    op.create_table(
        "capability_scale_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scale_stable_key", sa.String(64), nullable=False),
        sa.Column("scale_version", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "scale_stable_key", "scale_version", name="uq_capability_scale_version"
        ),
    )
    op.create_table(
        "capability_scale_dimensions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scale_version_id", sa.String(36), nullable=False),
        sa.Column("stable_key", sa.String(64), nullable=False),
        sa.Column("display_label", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["scale_version_id"], ["capability_scale_versions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("scale_version_id", "stable_key", name="uq_scale_dimension_key"),
        sa.UniqueConstraint("scale_version_id", "order_index", name="uq_scale_dimension_order"),
    )
    op.create_table(
        "capability_scale_levels",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scale_version_id", sa.String(36), nullable=False),
        sa.Column("stable_key", sa.String(64), nullable=False),
        sa.Column("ordinal_rank", sa.Integer(), nullable=False),
        sa.Column("display_label", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("criterion_policy_reference", sa.String(128), nullable=False),
        sa.Column("evidence_policy_reference", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(
            ["scale_version_id"], ["capability_scale_versions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("scale_version_id", "stable_key", name="uq_scale_level_key"),
        sa.UniqueConstraint("scale_version_id", "ordinal_rank", name="uq_scale_level_rank"),
    )

    op.create_table(
        "target_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("stable_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("creation_source", sa.String(64), nullable=False),
        sa.Column("retired_at", sa.Integer(), nullable=True),
        sa.Column("retirement_reason", sa.Text(), nullable=True),
        sa.UniqueConstraint("stable_key", name="uq_target_profile_stable_key"),
    )
    op.create_table(
        "target_profile_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("target_profile_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("effective_at", sa.Integer(), nullable=False),
        sa.Column("creation_source", sa.String(64), nullable=False),
        sa.Column("supersedes_version_id", sa.String(36), nullable=True),
        sa.CheckConstraint("version > 0", name="ck_target_profile_version_positive"),
        sa.ForeignKeyConstraint(["target_profile_id"], ["target_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["supersedes_version_id"], ["target_profile_versions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("target_profile_id", "version", name="uq_target_profile_version"),
    )
    op.create_table(
        "profile_domains",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("profile_version_id", sa.String(36), nullable=False),
        sa.Column("stable_key", sa.String(255), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("minimum_percent", sa.Integer(), nullable=True),
        sa.Column("maximum_percent", sa.Integer(), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "minimum_percent IS NULL OR minimum_percent BETWEEN 0 AND 100",
            name="ck_profile_domain_minimum",
        ),
        sa.CheckConstraint(
            "maximum_percent IS NULL OR maximum_percent BETWEEN 0 AND 100",
            name="ck_profile_domain_maximum",
        ),
        sa.CheckConstraint(
            "minimum_percent IS NULL OR maximum_percent IS NULL OR minimum_percent <= maximum_percent",
            name="ck_profile_domain_range",
        ),
        sa.ForeignKeyConstraint(
            ["profile_version_id"], ["target_profile_versions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("profile_version_id", "stable_key", name="uq_profile_domain_key"),
        sa.UniqueConstraint("profile_version_id", "order_index", name="uq_profile_domain_order"),
    )
    op.create_table(
        "profile_target_identities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("target_profile_id", sa.String(36), nullable=False),
        sa.Column("stable_key", sa.String(255), nullable=False),
        sa.Column("competency_identity_id", sa.String(36), nullable=False),
        sa.Column("dimension_key", sa.String(64), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["target_profile_id"], ["target_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["competency_identity_id"], ["competency_identities.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "target_profile_id", "stable_key", name="uq_profile_target_identity_key"
        ),
    )
    op.create_table(
        "profile_targets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("profile_version_id", sa.String(36), nullable=False),
        sa.Column("target_identity_id", sa.String(36), nullable=False),
        sa.Column("profile_domain_id", sa.String(36), nullable=False),
        sa.Column("scale_version_id", sa.String(36), nullable=False),
        sa.Column("target_level_id", sa.String(36), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("target_date", sa.String(10), nullable=True),
        sa.Column("target_month", sa.String(7), nullable=True),
        sa.Column("date_interpretation", sa.Text(), nullable=True),
        sa.Column("freshness_override_days", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("creation_source", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "priority IN ('critical','core','important','supporting','optional')",
            name="ck_profile_target_priority",
        ),
        sa.CheckConstraint(
            "(target_date IS NULL) != (target_month IS NULL) OR (target_date IS NULL AND target_month IS NULL)",
            name="ck_profile_target_date_precision",
        ),
        sa.CheckConstraint(
            "freshness_override_days IS NULL OR freshness_override_days > 0",
            name="ck_profile_target_freshness",
        ),
        sa.ForeignKeyConstraint(
            ["profile_version_id"], ["target_profile_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_identity_id"], ["profile_target_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["profile_domain_id"], ["profile_domains.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["scale_version_id"], ["capability_scale_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_level_id"], ["capability_scale_levels.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "profile_version_id", "target_identity_id", name="uq_profile_target_version_identity"
        ),
    )
    op.create_table(
        "milestone_identities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("target_profile_id", sa.String(36), nullable=False),
        sa.Column("stable_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["target_profile_id"], ["target_profiles.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("target_profile_id", "stable_key", name="uq_milestone_identity_key"),
    )
    op.create_table(
        "profile_milestones",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("profile_version_id", sa.String(36), nullable=False),
        sa.Column("milestone_identity_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("target_date", sa.String(10), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_version_id"], ["target_profile_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["milestone_identity_id"], ["milestone_identities.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "profile_version_id",
            "milestone_identity_id",
            name="uq_profile_milestone_identity",
        ),
        sa.UniqueConstraint("profile_version_id", "order_index", name="uq_profile_milestone_order"),
    )
    op.create_table(
        "profile_milestone_targets",
        sa.Column("milestone_id", sa.String(36), primary_key=True),
        sa.Column("profile_target_id", sa.String(36), primary_key=True),
        sa.ForeignKeyConstraint(["milestone_id"], ["profile_milestones.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["profile_target_id"], ["profile_targets.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "readiness_gate_identities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("target_profile_id", sa.String(36), nullable=False),
        sa.Column("stable_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["target_profile_id"], ["target_profiles.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "target_profile_id", "stable_key", name="uq_readiness_gate_identity_key"
        ),
    )
    op.create_table(
        "readiness_gates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("profile_version_id", sa.String(36), nullable=False),
        sa.Column("milestone_id", sa.String(36), nullable=True),
        sa.Column("gate_identity_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("effect", sa.String(32), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "effect IN ('hard_eligibility','urgency','display_only')",
            name="ck_readiness_gate_effect",
        ),
        sa.ForeignKeyConstraint(
            ["profile_version_id"], ["target_profile_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["milestone_id"], ["profile_milestones.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["gate_identity_id"], ["readiness_gate_identities.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "profile_version_id", "gate_identity_id", name="uq_readiness_gate_identity"
        ),
    )
    op.create_table(
        "readiness_gate_predicates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("gate_id", sa.String(36), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("predicate_type", sa.String(64), nullable=False),
        sa.Column("requirement_type", sa.String(16), nullable=False),
        sa.Column("subject_json", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "predicate_type IN ('capability_at_least','criterion_demonstrated','project_criterion_demonstrated','evidence_present')",
            name="ck_gate_predicate_type",
        ),
        sa.CheckConstraint(
            "requirement_type IN ('required','supporting')", name="ck_gate_predicate_requirement"
        ),
        sa.ForeignKeyConstraint(["gate_id"], ["readiness_gates.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("gate_id", "order_index", name="uq_gate_predicate_order"),
    )
    op.create_table(
        "readiness_gate_targets",
        sa.Column("gate_id", sa.String(36), primary_key=True),
        sa.Column("profile_target_id", sa.String(36), primary_key=True),
        sa.ForeignKeyConstraint(["gate_id"], ["readiness_gates.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["profile_target_id"], ["profile_targets.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "active_target_profile_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_profile_id", sa.String(36), nullable=False),
        sa.Column("target_profile_version_id", sa.String(36), nullable=False),
        sa.Column("activated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_active_target_profile_singleton"),
        sa.ForeignKeyConstraint(["target_profile_id"], ["target_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["target_profile_version_id"], ["target_profile_versions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "target_profile_activation_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("from_profile_version_id", sa.String(36), nullable=True),
        sa.Column("to_profile_version_id", sa.String(36), nullable=False),
        sa.Column("activated_at", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.CheckConstraint("event_sequence > 0", name="ck_target_profile_activation_sequence"),
        sa.ForeignKeyConstraint(
            ["from_profile_version_id"], ["target_profile_versions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("event_sequence", name="uq_target_profile_activation_sequence"),
        sa.UniqueConstraint("idempotency_key", name="uq_target_profile_activation_idempotency"),
        sa.ForeignKeyConstraint(
            ["to_profile_version_id"], ["target_profile_versions.id"], ondelete="RESTRICT"
        ),
    )

    op.create_table(
        "semantic_competency_definitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("competency_identity_id", sa.String(36), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("scale_version_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("creation_source", sa.String(64), nullable=False),
        sa.Column("effective_at", sa.Integer(), nullable=False),
        sa.Column("supersedes_definition_id", sa.String(36), nullable=True),
        sa.CheckConstraint("definition_version > 0", name="ck_semantic_definition_version"),
        sa.ForeignKeyConstraint(
            ["competency_identity_id"], ["competency_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["scale_version_id"], ["capability_scale_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_definition_id"],
            ["semantic_competency_definitions.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "competency_identity_id", "definition_version", name="uq_semantic_definition_version"
        ),
    )
    op.create_table(
        "semantic_definition_dimensions",
        sa.Column("semantic_definition_id", sa.String(36), primary_key=True),
        sa.Column("scale_dimension_id", sa.String(36), primary_key=True),
        sa.ForeignKeyConstraint(
            ["semantic_definition_id"], ["semantic_competency_definitions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["scale_dimension_id"], ["capability_scale_dimensions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "criterion_identities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("competency_identity_id", sa.String(36), nullable=False),
        sa.Column("stable_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=True),
        sa.Column("creation_source", sa.String(64), nullable=True),
        sa.Column("legacy_unspecified_reason", sa.Text(), nullable=True),
        sa.Column("retired_at", sa.Integer(), nullable=True),
        sa.Column("retirement_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["competency_identity_id"], ["competency_identities.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "competency_identity_id", "stable_key", name="uq_criterion_identity_key"
        ),
    )
    op.create_table(
        "criterion_definitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("criterion_identity_id", sa.String(36), nullable=False),
        sa.Column("semantic_definition_id", sa.String(36), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("level_id", sa.String(36), nullable=False),
        sa.Column("dimension_id", sa.String(36), nullable=True),
        sa.Column("requirement_type", sa.String(16), nullable=False),
        sa.Column("demonstration_rule_json", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("verification_rubric", sa.Text(), nullable=True),
        sa.Column("importance_weight", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("supersedes_definition_id", sa.String(36), nullable=True),
        sa.CheckConstraint("definition_version > 0", name="ck_criterion_definition_version"),
        sa.CheckConstraint(
            "requirement_type IN ('required','important','supporting')",
            name="ck_criterion_requirement_type",
        ),
        sa.CheckConstraint(
            "importance_weight IS NULL OR importance_weight BETWEEN 1 AND 5",
            name="ck_criterion_importance_weight",
        ),
        sa.ForeignKeyConstraint(
            ["criterion_identity_id"], ["criterion_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["semantic_definition_id"], ["semantic_competency_definitions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["level_id"], ["capability_scale_levels.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["dimension_id"], ["capability_scale_dimensions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_definition_id"], ["criterion_definitions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "criterion_identity_id", "definition_version", name="uq_criterion_definition_version"
        ),
    )
    op.create_table(
        "active_competency_definition_states",
        sa.Column("competency_identity_id", sa.String(36), primary_key=True),
        sa.Column("semantic_definition_id", sa.String(36), nullable=False),
        sa.Column("activated_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["competency_identity_id"], ["competency_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["semantic_definition_id"], ["semantic_competency_definitions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "competency_definition_activation_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("competency_identity_id", sa.String(36), nullable=False),
        sa.Column("from_definition_id", sa.String(36), nullable=True),
        sa.Column("to_definition_id", sa.String(36), nullable=False),
        sa.Column("activated_at", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.CheckConstraint(
            "event_sequence > 0", name="ck_competency_definition_activation_sequence"
        ),
        sa.ForeignKeyConstraint(
            ["competency_identity_id"], ["competency_identities.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("event_sequence", name="uq_competency_definition_activation_sequence"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_competency_definition_activation_idempotency"
        ),
        sa.ForeignKeyConstraint(
            ["from_definition_id"], ["semantic_competency_definitions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["to_definition_id"], ["semantic_competency_definitions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "legacy_criterion_assertions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("criterion_identity_id", sa.String(36), nullable=False),
        sa.Column("source_exit_criterion_identity_id", sa.String(36), nullable=False),
        sa.Column("source_exit_criterion_definition_id", sa.String(36), nullable=False),
        sa.Column("legacy_state", sa.String(16), nullable=False),
        sa.Column("requirement_type", sa.String(16), nullable=True),
        sa.Column("demonstration_rule", sa.String(64), nullable=True),
        sa.Column("evidence_strength", sa.String(32), nullable=True),
        sa.Column("independence", sa.String(32), nullable=True),
        sa.Column("source_confidence", sa.String(32), nullable=True),
        sa.Column("legacy_unspecified_reason", sa.Text(), nullable=False),
        sa.Column("asserted_at", sa.Integer(), nullable=False),
        sa.Column("cutoff_at", sa.Integer(), nullable=False),
        sa.Column("provenance", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "legacy_state IN ('not_met','partial','met')", name="ck_legacy_criterion_state"
        ),
        sa.ForeignKeyConstraint(
            ["criterion_identity_id"], ["criterion_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_exit_criterion_identity_id"],
            ["exit_criterion_identities.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_exit_criterion_definition_id"],
            ["exit_criterion_definitions.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "migration_backfill_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("policy_key", sa.String(128), nullable=False),
        sa.Column("source_kind", sa.String(128), nullable=False),
        sa.Column("source_row_count", sa.Integer(), nullable=False),
        sa.Column("result_row_count", sa.Integer(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("source_row_count >= 0", name="ck_migration_backfill_source_count"),
        sa.CheckConstraint("result_row_count >= 0", name="ck_migration_backfill_result_count"),
        sa.UniqueConstraint(
            "policy_key", "source_kind", name="uq_migration_backfill_policy_source"
        ),
    )

    now = BUILTIN_CREATED_AT
    scales = (
        ("technical", "Technical", "Ordinal technical capability."),
        ("cefr", "CEFR", "CEFR-aligned language capability."),
    )
    for stable_key, label, description in scales:
        op.execute(
            sa.text(
                "INSERT INTO capability_scale_versions (id,scale_stable_key,scale_version,display_name,description,created_at) VALUES (:id,:key,'v1',:label,:description,:created)"
            ).bindparams(
                id=_id(f"scale:{stable_key}:v1"),
                key=stable_key,
                label=label,
                description=description,
                created=now,
            )
        )
    for index, dimension in enumerate(CEFR_DIMENSIONS):
        op.execute(
            sa.text(
                "INSERT INTO capability_scale_dimensions (id,scale_version_id,stable_key,display_label,description,order_index) VALUES (:id,:scale,:key,:label,:description,:order_index)"
            ).bindparams(
                id=_id(f"scale:cefr:v1:dimension:{dimension}"),
                scale=_id("scale:cefr:v1"),
                key=dimension,
                label=dimension.title(),
                description=f"CEFR {dimension} capability evaluated independently.",
                order_index=index,
            )
        )
    for scale_key, levels in (("technical", TECHNICAL_LEVELS), ("cefr", CEFR_LEVELS)):
        for key, rank, label, description in levels:
            op.execute(
                sa.text(
                    "INSERT INTO capability_scale_levels (id,scale_version_id,stable_key,ordinal_rank,display_label,description,criterion_policy_reference,evidence_policy_reference) VALUES (:id,:scale,:key,:rank,:label,:description,'criterion-evaluation-policy/v1','evidence-qualification-policy/v1')"
                ).bindparams(
                    id=_id(f"scale:{scale_key}:v1:level:{key}"),
                    scale=_id(f"scale:{scale_key}:v1"),
                    key=key,
                    rank=rank,
                    label=label,
                    description=description,
                )
            )

    connection = op.get_bind()
    source_tables = {
        "roadmaps": [
            dict(row)
            for row in connection.execute(
                sa.text("SELECT id,is_current,active_version_id FROM roadmaps")
            ).mappings()
        ],
        "roadmap_versions": [
            dict(row)
            for row in connection.execute(
                sa.text("SELECT id,roadmap_id FROM roadmap_versions")
            ).mappings()
        ],
        "competency_definitions": [
            dict(row)
            for row in connection.execute(
                sa.text("SELECT id,roadmap_version_id FROM competency_definitions")
            ).mappings()
        ],
        "exit_criterion_identities": [
            dict(row)
            for row in connection.execute(
                sa.text(
                    "SELECT id,competency_identity_id,stable_key,current_state,updated_at "
                    "FROM exit_criterion_identities"
                )
            ).mappings()
        ],
        "exit_criterion_definitions": [
            dict(row)
            for row in connection.execute(
                sa.text(
                    "SELECT id,exit_criterion_identity_id,competency_definition_id,"
                    "created_at,required FROM exit_criterion_definitions"
                )
            ).mappings()
        ],
    }
    backfill = build_profile_competency_backfill(source_tables)
    for row in backfill.criterion_identities:
        connection.execute(
            sa.text(
                "INSERT INTO criterion_identities (id,competency_identity_id,stable_key,created_at,creation_source,legacy_unspecified_reason) VALUES (:id,:competency,:stable,NULL,NULL,:reason)"
            ),
            {
                "id": row["id"],
                "competency": row["competency_identity_id"],
                "stable": row["stable_key"],
                "reason": row["legacy_unspecified_reason"],
            },
        )
    for row in backfill.legacy_assertions:
        connection.execute(
            sa.text(
                "INSERT INTO legacy_criterion_assertions (id,criterion_identity_id,source_exit_criterion_identity_id,source_exit_criterion_definition_id,legacy_state,requirement_type,demonstration_rule,evidence_strength,independence,source_confidence,legacy_unspecified_reason,asserted_at,cutoff_at,provenance) VALUES (:id,:criterion,:source_identity,:source_definition,:state,:requirement,NULL,NULL,NULL,NULL,:reason,:asserted,:cutoff,'legacy-backfill-policy/v1')"
            ),
            {
                "id": row["id"],
                "criterion": row["criterion_identity_id"],
                "source_identity": row["source_exit_criterion_identity_id"],
                "source_definition": row["source_exit_criterion_definition_id"],
                "state": row["legacy_state"],
                "requirement": row["requirement_type"],
                "reason": row["legacy_unspecified_reason"],
                "asserted": row["asserted_at"],
                "cutoff": row["cutoff_at"],
            },
        )
    if connection.scalar(sa.text("SELECT COUNT(*) FROM criterion_identities")) != len(
        backfill.criterion_identities
    ) or connection.scalar(sa.text("SELECT COUNT(*) FROM legacy_criterion_assertions")) != len(
        backfill.legacy_assertions
    ):
        raise RuntimeError("Legacy criterion backfill reconciliation failed.")
    connection.execute(
        sa.text(
            "INSERT INTO migration_backfill_runs "
            "(id,policy_key,source_kind,source_row_count,result_row_count,source_hash,result_hash,recorded_at) "
            "VALUES (:id,:policy,'v1_exit_criteria',:source_count,:result_count,:source_hash,:result_hash,:recorded_at)"
        ),
        {
            "id": RUN_ID,
            "policy": POLICY_KEY,
            "source_count": backfill.source_row_count,
            "result_count": len(backfill.criterion_identities) + len(backfill.legacy_assertions),
            "source_hash": backfill.source_hash,
            "result_hash": backfill.result_hash,
            "recorded_at": BUILTIN_CREATED_AT,
        },
    )


def downgrade() -> None:
    for table in (
        "migration_backfill_runs",
        "legacy_criterion_assertions",
        "competency_definition_activation_events",
        "active_competency_definition_states",
        "criterion_definitions",
        "criterion_identities",
        "semantic_definition_dimensions",
        "semantic_competency_definitions",
        "target_profile_activation_events",
        "active_target_profile_state",
        "readiness_gate_targets",
        "readiness_gate_predicates",
        "readiness_gates",
        "profile_milestone_targets",
        "profile_milestones",
        "milestone_identities",
        "profile_targets",
        "profile_target_identities",
        "profile_domains",
        "readiness_gate_identities",
        "target_profile_versions",
        "target_profiles",
        "capability_scale_levels",
        "capability_scale_dimensions",
        "capability_scale_versions",
    ):
        op.drop_table(table)
    with op.batch_alter_table("competency_identities") as batch:
        for column in (
            "retirement_reason",
            "retired_at",
            "legacy_unspecified_reason",
            "creation_source",
            "identity_created_at",
        ):
            batch.drop_column(column)
