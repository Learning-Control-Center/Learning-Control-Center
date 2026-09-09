"""Add credential generations and durable authentication security state."""

import sqlalchemy as sa
from alembic import op

revision = "0003_auth_security_foundation"
down_revision = "0002_roadmap_scope_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    with op.batch_alter_table(
        "users",
        recreate="always",
        partial_reordering=[
            (
                "id",
                "singleton_key",
                "username",
                "password_hash",
                "credential_generation",
                "password_changed_at",
                "created_at",
                "updated_at",
            )
        ],
    ) as batch:
        batch.add_column(
            sa.Column("credential_generation", sa.Integer(), nullable=False, server_default="1"),
            insert_after="password_hash",
        )
        batch.add_column(
            sa.Column("password_changed_at", sa.Integer(), nullable=True),
            insert_after="credential_generation",
        )
        batch.create_check_constraint(
            "ck_user_credential_generation_positive", "credential_generation > 0"
        )
    with op.batch_alter_table("users") as batch:
        batch.alter_column("credential_generation", server_default=None)
    with op.batch_alter_table(
        "auth_sessions",
        recreate="always",
        partial_reordering=[
            (
                "id",
                "user_id",
                "token_lookup_hash",
                "csrf_secret_hash",
                "created_at",
                "last_seen_at",
                "absolute_expires_at",
                "revoked_at",
                "credential_generation",
            )
        ],
    ) as batch:
        batch.add_column(
            sa.Column("credential_generation", sa.Integer(), nullable=False, server_default="1"),
            insert_after="revoked_at",
        )
        batch.create_check_constraint(
            "ck_auth_session_credential_generation_positive", "credential_generation > 0"
        )
    with op.batch_alter_table("auth_sessions") as batch:
        batch.alter_column("credential_generation", server_default=None)

    op.create_table(
        "security_audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=True),
        sa.Column("auth_session_id", sa.String(36), nullable=True),
        sa.Column("actor_kind", sa.String(32), nullable=False),
        sa.Column("client_key_hash", sa.String(64), nullable=True),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["auth_session_id"], ["auth_sessions.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("idempotency_key", name="uq_security_audit_idempotency"),
    )
    op.create_index(
        "ix_security_audit_event_time", "security_audit_events", ["occurred_at"], unique=False
    )
    op.create_table(
        "auth_rate_limit_buckets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("namespace", sa.String(64), nullable=False),
        sa.Column("client_key_hash", sa.String(64), nullable=False),
        sa.Column("key_id", sa.String(32), nullable=False),
        sa.Column("window_started_at", sa.Integer(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("blocked_until", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("failure_count >= 0", name="ck_auth_rate_failure_count"),
        sa.UniqueConstraint(
            "namespace", "client_key_hash", "window_started_at", name="uq_auth_rate_bucket"
        ),
    )
    op.create_index(
        "ix_auth_rate_bucket_cleanup",
        "auth_rate_limit_buckets",
        ["updated_at"],
        unique=False,
    )
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    if violations:
        raise RuntimeError(f"Authentication migration produced FK violations: {violations!r}")


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    op.drop_index("ix_auth_rate_bucket_cleanup", table_name="auth_rate_limit_buckets")
    op.drop_table("auth_rate_limit_buckets")
    op.drop_index("ix_security_audit_event_time", table_name="security_audit_events")
    op.drop_table("security_audit_events")
    with op.batch_alter_table("auth_sessions") as batch:
        batch.drop_constraint("ck_auth_session_credential_generation_positive", type_="check")
        batch.drop_column("credential_generation")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_user_credential_generation_positive", type_="check")
        batch.drop_column("password_changed_at")
        batch.drop_column("credential_generation")
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    if violations:
        raise RuntimeError(f"Authentication downgrade produced FK violations: {violations!r}")
