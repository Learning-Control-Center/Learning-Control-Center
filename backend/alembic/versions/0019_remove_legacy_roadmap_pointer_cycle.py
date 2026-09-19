"""Contract legacy Roadmap pointers onto the acyclic compatibility state."""

from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision = "0019_remove_legacy_roadmap_pointer_cycle"
down_revision = "0018_v2_authority_state"
branch_labels = None
depends_on = None


def _hash(
    roadmap_id: str,
    active_version_id: str | None,
    current_phase_id: str | None,
    is_current: bool,
) -> str:
    payload = {
        "activeVersionId": active_version_id,
        "currentPhaseId": current_phase_id,
        "isCurrent": bool(is_current),
        "roadmapId": roadmap_id,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def upgrade() -> None:
    connection = op.get_bind()
    roadmaps = (
        connection.execute(
            sa.text(
                "SELECT id,active_version_id,current_phase_id,is_current FROM roadmaps ORDER BY id"
            )
        )
        .mappings()
        .all()
    )
    states = {
        row["roadmap_id"]: row
        for row in connection.execute(
            sa.text(
                "SELECT roadmap_id,active_version_id,current_phase_id,is_current,state_hash "
                "FROM legacy_roadmap_active_states ORDER BY roadmap_id"
            )
        ).mappings()
    }
    if len(roadmaps) != len(states):
        raise RuntimeError("Legacy Roadmap pointer contraction requires exact active-state parity.")
    for roadmap in roadmaps:
        state = states.get(roadmap["id"])
        expected = _hash(
            roadmap["id"],
            roadmap["active_version_id"],
            roadmap["current_phase_id"],
            bool(roadmap["is_current"]),
        )
        if state is None or (
            state["active_version_id"] != roadmap["active_version_id"]
            or state["current_phase_id"] != roadmap["current_phase_id"]
            or bool(state["is_current"]) != bool(roadmap["is_current"])
            or state["state_hash"] != expected
        ):
            raise RuntimeError(
                "Legacy Roadmap pointer contraction requires exact active-state parity."
            )
    # SQLite batch recreation drops the parent table temporarily. Foreign-key
    # enforcement must be disabled for that DDL step because populated child
    # tables correctly reference Roadmap identities that the replacement table
    # preserves. Alembic's environment restores enforcement after the revision.
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    with op.batch_alter_table("roadmaps") as batch:
        batch.drop_index("uq_one_current_roadmap")
        batch.drop_column("current_phase_id")
        batch.drop_column("active_version_id")
        batch.drop_column("is_current")
    violations = connection.execute(sa.text("PRAGMA foreign_key_check")).fetchall()
    if violations:
        raise RuntimeError("Legacy Roadmap pointer contraction produced foreign-key violations.")


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT 1 FROM roadmaps LIMIT 1")).first() is not None:
        raise RuntimeError(
            "Populated legacy Roadmap pointer contraction cannot be downgraded; restore the "
            "verified pre-migration backup with the matching prior application."
        )
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    with op.batch_alter_table("roadmaps") as batch:
        batch.add_column(sa.Column("is_current", sa.Boolean(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("active_version_id", sa.String(36)))
        batch.add_column(sa.Column("current_phase_id", sa.String(36)))
        batch.create_foreign_key(
            "fk_roadmaps_active_version_id_roadmap_versions",
            "roadmap_versions",
            ["active_version_id"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_roadmaps_current_phase_id_phases",
            "phases",
            ["current_phase_id"],
            ["id"],
        )
        batch.create_index(
            "uq_one_current_roadmap",
            ["is_current"],
            unique=True,
            sqlite_where=sa.text("is_current = 1"),
        )
