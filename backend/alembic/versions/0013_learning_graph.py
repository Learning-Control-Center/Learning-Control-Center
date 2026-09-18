"""Add canonical immutable native V2 Learning Graph facts."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_learning_graph"
down_revision = "0012_project_core"
branch_labels = None
depends_on = None


def _id() -> sa.Column[str]:
    return sa.Column("id", sa.String(36), primary_key=True)


def upgrade() -> None:
    op.create_table(
        "learning_graphs",
        _id(),
        sa.Column("stable_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("creation_source", sa.String(64), nullable=False),
        sa.UniqueConstraint("stable_key", name="uq_learning_graph_stable_key"),
    )
    op.create_table(
        "learning_graph_versions",
        _id(),
        sa.Column("learning_graph_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("satisfaction_policy_version", sa.String(64), nullable=False),
        sa.Column("definition_payload_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("effective_at", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("creation_source", sa.String(64), nullable=False),
        sa.Column("supersedes_version_id", sa.String(36)),
        sa.CheckConstraint("version > 0", name="ck_learning_graph_version_positive"),
        sa.UniqueConstraint("learning_graph_id", "version", name="uq_learning_graph_version"),
        sa.ForeignKeyConstraint(["learning_graph_id"], ["learning_graphs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["supersedes_version_id"], ["learning_graph_versions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_index(
        "ix_learning_graph_version_supersedes",
        "learning_graph_versions",
        ["supersedes_version_id"],
    )
    op.create_table(
        "competency_edge_identities",
        _id(),
        sa.Column("learning_graph_id", sa.String(36), nullable=False),
        sa.Column("stable_key", sa.String(255), nullable=False),
        sa.Column("edge_type", sa.String(32), nullable=False),
        sa.Column("source_competency_identity_id", sa.String(36), nullable=False),
        sa.Column("target_competency_identity_id", sa.String(36), nullable=False),
        sa.Column("meaning_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint("learning_graph_id", "stable_key", name="uq_competency_edge_key"),
        sa.ForeignKeyConstraint(["learning_graph_id"], ["learning_graphs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["source_competency_identity_id"],
            ["competency_identities.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_competency_identity_id"],
            ["competency_identities.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_competency_edge_identity_source",
        "competency_edge_identities",
        ["source_competency_identity_id"],
    )
    op.create_index(
        "ix_competency_edge_identity_target",
        "competency_edge_identities",
        ["target_competency_identity_id"],
    )
    op.create_table(
        "competency_edge_definitions",
        _id(),
        sa.Column("learning_graph_version_id", sa.String(36), nullable=False),
        sa.Column("edge_identity_id", sa.String(36), nullable=False),
        sa.Column("edge_type", sa.String(32), nullable=False),
        sa.Column("source_competency_identity_id", sa.String(36), nullable=False),
        sa.Column("target_competency_identity_id", sa.String(36), nullable=False),
        sa.Column("source_semantic_definition_id", sa.String(36), nullable=False),
        sa.Column("target_semantic_definition_id", sa.String(36), nullable=False),
        sa.Column("satisfaction_scope_key", sa.String(255), nullable=False),
        sa.Column("requirement_kind", sa.String(40)),
        sa.Column("requirement_json", sa.Text(), nullable=False),
        sa.Column("provenance", sa.String(64), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "edge_type IN ('prerequisite','recommended_before','supports',"
            "'specialization','related')",
            name="ck_competency_edge_type",
        ),
        sa.CheckConstraint("order_index >= 0", name="ck_competency_edge_order"),
        sa.CheckConstraint(
            "requirement_kind IS NULL OR requirement_kind IN "
            "('capability_at_least','criterion_set_demonstrated')",
            name="ck_competency_edge_requirement_kind",
        ),
        sa.CheckConstraint(
            "edge_type != 'prerequisite' OR requirement_kind IS NOT NULL",
            name="ck_native_prerequisite_requirement",
        ),
        sa.UniqueConstraint(
            "learning_graph_version_id", "edge_identity_id", name="uq_competency_edge_definition"
        ),
        sa.UniqueConstraint(
            "learning_graph_version_id",
            "edge_type",
            "source_competency_identity_id",
            "target_competency_identity_id",
            "satisfaction_scope_key",
            name="uq_competency_edge_semantics",
        ),
        sa.UniqueConstraint(
            "learning_graph_version_id", "order_index", name="uq_competency_edge_order"
        ),
        sa.ForeignKeyConstraint(
            ["learning_graph_version_id"], ["learning_graph_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["edge_identity_id"], ["competency_edge_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_competency_identity_id"], ["competency_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_competency_identity_id"], ["competency_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_semantic_definition_id"],
            ["semantic_competency_definitions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_semantic_definition_id"],
            ["semantic_competency_definitions.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_competency_edge_source",
        "competency_edge_definitions",
        ["source_competency_identity_id"],
    )
    op.create_index(
        "ix_competency_edge_target",
        "competency_edge_definitions",
        ["target_competency_identity_id"],
    )
    op.create_index(
        "ix_competency_edge_definition_identity",
        "competency_edge_definitions",
        ["edge_identity_id"],
    )
    op.create_index(
        "ix_competency_edge_source_definition",
        "competency_edge_definitions",
        ["source_semantic_definition_id"],
    )
    op.create_index(
        "ix_competency_edge_target_definition",
        "competency_edge_definitions",
        ["target_semantic_definition_id"],
    )
    op.create_table(
        "active_learning_graph_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("learning_graph_id", sa.String(36), nullable=False, unique=True),
        sa.Column("learning_graph_version_id", sa.String(36), nullable=False, unique=True),
        sa.Column("activated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_active_learning_graph_singleton"),
        sa.ForeignKeyConstraint(["learning_graph_id"], ["learning_graphs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["learning_graph_version_id"], ["learning_graph_versions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "learning_graph_activation_events",
        _id(),
        sa.Column("learning_graph_id", sa.String(36), nullable=False),
        sa.Column("from_learning_graph_version_id", sa.String(36)),
        sa.Column("to_learning_graph_version_id", sa.String(36), nullable=False),
        sa.Column("activated_at", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.CheckConstraint("event_sequence > 0", name="ck_graph_activation_sequence"),
        sa.UniqueConstraint("event_sequence", name="uq_graph_activation_seq"),
        sa.UniqueConstraint("idempotency_key", name="uq_graph_activation_idempotency"),
        sa.ForeignKeyConstraint(["learning_graph_id"], ["learning_graphs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["from_learning_graph_version_id"],
            ["learning_graph_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["to_learning_graph_version_id"],
            ["learning_graph_versions.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_graph_activation_cutoff",
        "learning_graph_activation_events",
        ["activated_at", "event_sequence"],
    )
    op.create_index(
        "ix_graph_activation_from",
        "learning_graph_activation_events",
        ["from_learning_graph_version_id"],
    )
    op.create_index(
        "ix_graph_activation_to",
        "learning_graph_activation_events",
        ["to_learning_graph_version_id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    for table in (
        "learning_graph_activation_events",
        "active_learning_graph_states",
        "competency_edge_definitions",
        "competency_edge_identities",
        "learning_graph_versions",
        "learning_graphs",
    ):
        if connection.execute(sa.text(f'SELECT 1 FROM "{table}" LIMIT 1')).first():
            raise RuntimeError("Refusing to downgrade a data-bearing Learning Graph migration.")
    op.drop_table("learning_graph_activation_events")
    op.drop_table("active_learning_graph_states")
    op.drop_table("competency_edge_definitions")
    op.drop_table("competency_edge_identities")
    op.drop_table("learning_graph_versions")
    op.drop_table("learning_graphs")
