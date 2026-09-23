"""Add authoritative append-only Master Import lineage and stable-key ownership."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_master_import_ledger"
down_revision = "0019_remove_legacy_roadmap_pointer_cycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "master_import_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "import_record_id",
            sa.String(36),
            sa.ForeignKey("import_records.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("lineage_key", sa.String(255), nullable=False),
        sa.Column("owner_key", sa.String(255), nullable=False),
        sa.Column("content_revision", sa.Integer(), nullable=False),
        sa.Column("package_digest", sa.String(128), nullable=False, unique=True),
        sa.Column("content_digest", sa.String(128), nullable=False),
        sa.Column("previous_content_digest", sa.String(128)),
        sa.Column("canonicalization_version", sa.String(32), nullable=False),
        sa.Column("provenance_json", sa.Text(), nullable=False),
        sa.Column("selected_versions_json", sa.Text(), nullable=False),
        sa.Column("activation_json", sa.Text(), nullable=False),
        sa.Column("applied_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint("lineage_key", "content_revision", name="uq_master_revision_order"),
        sa.UniqueConstraint("lineage_key", "content_digest", name="uq_master_revision_content"),
        sa.CheckConstraint("content_revision > 0", name="ck_master_revision_positive"),
        sa.CheckConstraint("canonicalization_version = 'mi-canon-v1'", name="ck_master_canon_v1"),
    )
    op.create_table(
        "master_import_owned_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "first_revision_id",
            sa.String(36),
            sa.ForeignKey("master_import_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("lineage_key", sa.String(255), nullable=False),
        sa.Column("owner_key", sa.String(255), nullable=False),
        sa.Column("entity_kind", sa.String(64), nullable=False),
        sa.Column("scope_key", sa.String(255), nullable=False),
        sa.Column("stable_key", sa.String(255), nullable=False),
        sa.Column("canonical_id", sa.String(36), nullable=False),
        sa.Column("meaning_digest", sa.String(128), nullable=False),
        sa.UniqueConstraint("entity_kind", "scope_key", "stable_key", name="uq_master_owned_key"),
    )


def downgrade() -> None:
    op.drop_table("master_import_owned_keys")
    op.drop_table("master_import_revisions")
