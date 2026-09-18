"""Add V2 Roadmap Projection state and expand legacy active-pointer compatibility."""

from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision = "0014_roadmap_projection_state"
down_revision = "0013_learning_graph"
branch_labels = None
depends_on = None


def _pointer_hash(
    roadmap_id: str, active_version_id: str | None, current_phase_id: str | None, is_current: bool
) -> str:
    payload = {
        "activeVersionId": active_version_id,
        "currentPhaseId": current_phase_id,
        "isCurrent": bool(is_current),
        "roadmapId": roadmap_id,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def upgrade() -> None:
    op.create_table(
        "legacy_roadmap_active_states",
        sa.Column("roadmap_id", sa.String(36), primary_key=True),
        sa.Column("active_version_id", sa.String(36)),
        sa.Column("current_phase_id", sa.String(36)),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["roadmap_id"], ["roadmaps.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["active_version_id"], ["roadmap_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["current_phase_id"], ["phases.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "uq_legacy_one_current_roadmap",
        "legacy_roadmap_active_states",
        ["is_current"],
        unique=True,
        sqlite_where=sa.text("is_current = 1"),
    )
    op.create_index(
        "ix_legacy_roadmap_active_version",
        "legacy_roadmap_active_states",
        ["active_version_id"],
    )
    op.create_index(
        "ix_legacy_roadmap_current_phase",
        "legacy_roadmap_active_states",
        ["current_phase_id"],
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, active_version_id, current_phase_id, is_current, updated_at "
            "FROM roadmaps ORDER BY id"
        )
    ).mappings()
    for row in rows:
        connection.execute(
            sa.text(
                "INSERT INTO legacy_roadmap_active_states "
                "(roadmap_id, active_version_id, current_phase_id, is_current, "
                "state_hash, updated_at) "
                "VALUES (:roadmap_id, :active_version_id, :current_phase_id, :is_current, "
                ":state_hash, :updated_at)"
            ),
            {
                **row,
                "state_hash": _pointer_hash(
                    row["id"],
                    row["active_version_id"],
                    row["current_phase_id"],
                    bool(row["is_current"]),
                ),
                "roadmap_id": row["id"],
            },
        )
    parity_rows = connection.execute(
        sa.text(
            "SELECT r.id, r.active_version_id, r.current_phase_id, r.is_current, "
            "s.active_version_id AS state_active_version_id, "
            "s.current_phase_id AS state_current_phase_id, "
            "s.is_current AS state_is_current, s.state_hash "
            "FROM roadmaps r LEFT JOIN legacy_roadmap_active_states s ON s.roadmap_id = r.id "
            "ORDER BY r.id"
        )
    ).mappings()
    for row in parity_rows:
        expected_hash = _pointer_hash(
            row["id"],
            row["active_version_id"],
            row["current_phase_id"],
            bool(row["is_current"]),
        )
        if (
            row["state_hash"] != expected_hash
            or row["state_active_version_id"] != row["active_version_id"]
            or row["state_current_phase_id"] != row["current_phase_id"]
            or bool(row["state_is_current"]) != bool(row["is_current"])
        ):
            raise RuntimeError("Legacy Roadmap active-state backfill parity failed.")
    op.create_table(
        "roadmap_node_position_overrides",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scope_key", sa.String(255), nullable=False),
        sa.Column("node_key", sa.String(255), nullable=False),
        sa.Column("position_x", sa.Integer(), nullable=False),
        sa.Column("position_y", sa.Integer(), nullable=False),
        sa.Column("provenance", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("position_x BETWEEN -1000000 AND 1000000", name="ck_roadmap_v2_x"),
        sa.CheckConstraint("position_y BETWEEN -1000000 AND 1000000", name="ck_roadmap_v2_y"),
        sa.UniqueConstraint("scope_key", "node_key", name="uq_roadmap_v2_position_scope_node"),
    )
    op.create_table(
        "roadmap_projection_preferences",
        sa.Column("scope_key", sa.String(255), primary_key=True),
        sa.Column("show_prerequisites", sa.Boolean(), nullable=False),
        sa.Column("show_recommended_before", sa.Boolean(), nullable=False),
        sa.Column("show_supports", sa.Boolean(), nullable=False),
        sa.Column("show_related", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
    )
    op.create_table(
        "roadmap_projection_caches",
        sa.Column("scope_key", sa.String(255), primary_key=True),
        sa.Column("projection_policy_version", sa.String(64), nullable=False),
        sa.Column("layout_policy_version", sa.String(64), nullable=False),
        sa.Column("source_lineage_json", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("output_json", sa.Text(), nullable=False),
        sa.Column("output_hash", sa.String(64), nullable=False),
        sa.Column("built_at", sa.Integer(), nullable=False),
    )
    op.create_table(
        "roadmap_projection_checkpoints",
        sa.Column("scope_key", sa.String(255), primary_key=True),
        sa.Column("source_generation", sa.Integer(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("output_hash", sa.String(64), nullable=False),
        sa.Column("policy_bundle_hash", sa.String(64), nullable=False),
        sa.Column("rebuilt_at", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    connection = op.get_bind()
    for table in ("roadmap_node_position_overrides", "roadmap_projection_preferences"):
        if connection.execute(sa.text(f'SELECT 1 FROM "{table}" LIMIT 1')).first():
            raise RuntimeError("Refusing to downgrade canonical Roadmap Projection input state.")
    op.drop_table("roadmap_projection_checkpoints")
    op.drop_table("roadmap_projection_caches")
    op.drop_table("roadmap_projection_preferences")
    op.drop_table("roadmap_node_position_overrides")
    op.drop_table("legacy_roadmap_active_states")
