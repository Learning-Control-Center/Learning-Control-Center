"""Require every Session to reference exactly one Activity."""

from __future__ import annotations

import hashlib
import json
import re

import sqlalchemy as sa
from alembic import op

revision = "0008_activity_session_constraint"
down_revision = "0007_activity_session_backfill"
branch_labels = None
depends_on = None

SESSION_COLUMNS = (
    "id,activity_id,competency_identity_id,track_id,session_mode,timed_state,activity_type,"
    "assistance_mode,started_at,ended_at,accumulated_duration_ms,active_since,duration_ms,"
    "difficulty,outcome,notes,tombstoned_at,tombstone_reason,created_at,updated_at"
)


def _rows_hash(rows: list[dict[str, object]]) -> str:
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _table_rows(
    connection: sa.Connection, table: str, columns: str = "*"
) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in connection.execute(
            sa.text(f'SELECT {columns} FROM "{table}" ORDER BY id')
        ).mappings()
    ]


def _session_schema_signature(connection: sa.Connection) -> dict[str, object]:
    table_sql = connection.scalar(
        sa.text("SELECT sql FROM sqlite_master WHERE type='table' AND name='learning_sessions'")
    )
    assert isinstance(table_sql, str)
    checks = sorted(
        value.upper().replace(" ", "")
        for value in re.findall(r"CONSTRAINT\s+\w+\s+CHECK\s*\([^)]*\)", table_sql, re.I)
    )
    indexes = sorted(
        tuple(row)
        for row in connection.execute(
            sa.text(
                "SELECT name,sql FROM sqlite_master WHERE type='index' "
                "AND tbl_name='learning_sessions' AND sql IS NOT NULL"
            )
        )
    )
    foreign_keys = sorted(
        tuple(row)[2:]
        for row in connection.execute(sa.text("PRAGMA foreign_key_list(learning_sessions)"))
    )
    columns = [
        tuple(row) for row in connection.execute(sa.text("PRAGMA table_info(learning_sessions)"))
    ]
    return {"checks": checks, "indexes": indexes, "foreign_keys": foreign_keys, "columns": columns}


def upgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT COUNT(*) FROM learning_sessions WHERE activity_id IS NULL")
    ):
        raise RuntimeError("Cannot require Activity while an unassigned Session exists.")
    sessions_before = _table_rows(connection, "learning_sessions", SESSION_COLUMNS)
    schema_before = _session_schema_signature(connection)
    contributions = _table_rows(connection, "session_contributions")
    retractions = _table_rows(connection, "contribution_retractions")
    corrections = _table_rows(connection, "session_corrections")
    history_hashes = {
        "session_contributions": _rows_hash(contributions),
        "contribution_retractions": _rows_hash(retractions),
        "session_corrections": _rows_hash(corrections),
    }
    op.drop_table("contribution_retractions")
    op.drop_table("session_corrections")
    op.drop_table("session_contributions")
    with op.batch_alter_table("learning_sessions", recreate="always") as batch:
        batch.alter_column("activity_id", existing_type=sa.String(36), nullable=False)
    _create_history_tables()
    _insert_rows(connection, "session_contributions", contributions)
    _insert_rows(connection, "session_corrections", corrections)
    _insert_rows(connection, "contribution_retractions", retractions)
    sessions_after = _table_rows(connection, "learning_sessions", SESSION_COLUMNS)
    schema_after = _session_schema_signature(connection)
    before_columns = list(schema_before["columns"])
    after_columns = list(schema_after["columns"])
    for index, column in enumerate(before_columns):
        if column[1] == "activity_id":
            updated = list(column)
            updated[3] = 1
            before_columns[index] = tuple(updated)
    failures: list[str] = []
    if _rows_hash(sessions_before) != _rows_hash(sessions_after):
        failures.append("session rows")
    if before_columns != after_columns:
        failures.append("columns")
    for signature in ("checks", "indexes", "foreign_keys"):
        if schema_before[signature] != schema_after[signature]:
            failures.append(signature)
    for table, digest in history_hashes.items():
        if digest != _rows_hash(_table_rows(connection, table)):
            failures.append(f"{table} rows")
    if connection.execute(sa.text("PRAGMA foreign_key_check")).fetchone() is not None:
        failures.append("foreign key integrity")
    if failures:
        raise RuntimeError(
            "Activity/Session constraint migration reconciliation failed: " + ", ".join(failures)
        )


def downgrade() -> None:
    connection = op.get_bind()
    contributions = [
        dict(row)
        for row in connection.execute(sa.text("SELECT * FROM session_contributions")).mappings()
    ]
    retractions = [
        dict(row)
        for row in connection.execute(sa.text("SELECT * FROM contribution_retractions")).mappings()
    ]
    corrections = [
        dict(row)
        for row in connection.execute(sa.text("SELECT * FROM session_corrections")).mappings()
    ]
    op.drop_table("contribution_retractions")
    op.drop_table("session_corrections")
    op.drop_table("session_contributions")
    with op.batch_alter_table("learning_sessions", recreate="always") as batch:
        batch.alter_column("activity_id", existing_type=sa.String(36), nullable=True)
    _create_history_tables()
    _insert_rows(connection, "session_contributions", contributions)
    _insert_rows(connection, "session_corrections", corrections)
    _insert_rows(connection, "contribution_retractions", retractions)


def _insert_rows(connection: sa.Connection, table_name: str, rows: list[dict[str, object]]) -> None:
    if rows:
        table = sa.Table(table_name, sa.MetaData(), autoload_with=connection)
        connection.execute(table.insert(), rows)


def _create_history_tables() -> None:
    op.create_table(
        "session_contributions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("competency_identity_id", sa.String(36), nullable=False),
        sa.Column("criterion_identity_id", sa.String(36), nullable=True),
        sa.Column("relevance", sa.String(16), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("provenance", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "relevance IN ('primary','secondary','supporting')",
            name="ck_session_contribution_relevance",
        ),
        sa.CheckConstraint(
            "provenance IN ('user_selected','user_confirmed',"
            "'deterministic_legacy_backfill','imported_asserted')",
            name="ck_session_contribution_provenance",
        ),
        sa.ForeignKeyConstraint(["session_id"], ["learning_sessions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["competency_identity_id"], ["competency_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["criterion_identity_id"], ["criterion_identities.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "contribution_retractions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("contribution_id", sa.String(36), nullable=False, unique=True),
        sa.Column("replacement_contribution_id", sa.String(36), nullable=True),
        sa.Column("retracted_at", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["contribution_id"], ["session_contributions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["replacement_contribution_id"], ["session_contributions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "session_corrections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("corrected_at", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("changed_fields_json", sa.Text(), nullable=False),
        sa.Column("before_json", sa.Text(), nullable=False),
        sa.Column("after_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["learning_sessions.id"], ondelete="RESTRICT"),
    )
