"""Record historical roadmap scope changes."""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0002_roadmap_scope_events"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    if not sa.inspect(connection).has_table("roadmap_scope_events"):
        op.create_table(
            "roadmap_scope_events",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("roadmap_id", sa.String(length=36), nullable=False),
            sa.Column("roadmap_version_id", sa.String(length=36), nullable=False),
            sa.Column("phase_id", sa.String(length=36), nullable=False),
            sa.Column("source", sa.String(length=64), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("occurred_at", sa.Integer(), nullable=False),
            sa.Column("event_sequence", sa.Integer(), nullable=False),
            sa.CheckConstraint(
                "event_sequence > 0",
                name="ck_roadmap_scope_event_sequence_positive",
            ),
            sa.ForeignKeyConstraint(
                ["phase_id"],
                ["phases.id"],
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["roadmap_id"],
                ["roadmaps.id"],
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["roadmap_version_id"],
                ["roadmap_versions.id"],
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "event_sequence",
                name="uq_roadmap_scope_event_sequence",
            ),
        )
        op.create_index(
            "ix_roadmap_scope_event_time_sequence",
            "roadmap_scope_events",
            ["occurred_at", "event_sequence"],
            unique=False,
        )

    event_count = connection.scalar(sa.text("SELECT COUNT(*) FROM roadmap_scope_events"))
    current = (
        connection.execute(
            sa.text(
                "SELECT id, active_version_id, current_phase_id, updated_at "
                "FROM roadmaps WHERE is_current = 1"
            )
        )
        .mappings()
        .all()
    )
    if event_count == 0 and len(current) == 1:
        roadmap = current[0]
        if roadmap["active_version_id"] is not None and roadmap["current_phase_id"] is not None:
            connection.execute(
                sa.text(
                    "INSERT INTO roadmap_scope_events "
                    "(id, roadmap_id, roadmap_version_id, phase_id, source, reason, "
                    "occurred_at, event_sequence) "
                    "VALUES (:id, :roadmap_id, :roadmap_version_id, :phase_id, "
                    ":source, :reason, :occurred_at, 1)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "roadmap_id": roadmap["id"],
                    "roadmap_version_id": roadmap["active_version_id"],
                    "phase_id": roadmap["current_phase_id"],
                    "source": "migration_baseline",
                    "reason": "Current scope baseline at history migration",
                    "occurred_at": roadmap["updated_at"],
                },
            )


def downgrade() -> None:
    connection = op.get_bind()
    if sa.inspect(connection).has_table("roadmap_scope_events"):
        op.drop_index(
            "ix_roadmap_scope_event_time_sequence",
            table_name="roadmap_scope_events",
        )
        op.drop_table("roadmap_scope_events")
