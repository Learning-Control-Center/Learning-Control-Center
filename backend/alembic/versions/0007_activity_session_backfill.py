"""Seed activity vocabulary and backfill one Activity per V1 Session."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0007_activity_session_backfill"
down_revision = "0006_activity_session_schema"
branch_labels = None
depends_on = None

NAMESPACE = uuid.UUID("c20b6799-bc36-5db4-92af-3eae1f16bcb3")
BUILTIN_CREATED_AT = 1_788_912_000_000
POLICY_KEY = "activity-legacy-backfill-policy/v1"
RUN_ID = str(
    uuid.uuid5(NAMESPACE, "backfill-run:activity-session:activity-legacy-backfill-policy/v1")
)
ACTIVITY_TYPES = (
    "learning",
    "reading",
    "practice",
    "coding",
    "debugging",
    "project",
    "review",
    "verification",
    "research",
)
SESSION_COLUMNS = (
    "id,competency_identity_id,track_id,session_mode,timed_state,activity_type,"
    "assistance_mode,started_at,ended_at,accumulated_duration_ms,active_since,duration_ms,"
    "difficulty,outcome,notes,created_at,updated_at"
)


def _stable_id(value: str) -> str:
    return str(uuid.uuid5(NAMESPACE, value))


def _rows_hash(rows: list[dict[str, Any]]) -> str:
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _insert_or_verify(connection: sa.Connection, table: sa.Table, row: dict[str, Any]) -> bool:
    primary_key = list(table.primary_key.columns)
    predicate = sa.and_(*(column == row[column.name] for column in primary_key))
    existing = connection.execute(sa.select(table).where(predicate)).mappings().one_or_none()
    if existing is None:
        connection.execute(table.insert(), row)
        return True
    if dict(existing) != row:
        raise RuntimeError(f"Conflicting partial Activity backfill row in {table.name}.")
    return False


def _category_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": _stable_id(f"activity-category:{key}:v1"),
            "stable_key": key,
            "vocabulary_version": "v1",
            "display_label": key.replace("_", " ").title(),
            "created_at": BUILTIN_CREATED_AT,
        }
        for key in ACTIVITY_TYPES
    ]


def _backfill_rows(
    sessions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str], str, str]:
    source_rows = sorted(sessions, key=lambda row: str(row["id"]))
    activities: list[dict[str, Any]] = []
    contributions: list[dict[str, Any]] = []
    links: dict[str, str] = {}
    for session in source_rows:
        session_id = str(session["id"])
        activity_id = _stable_id(f"activity:learning_sessions:{session_id}:{POLICY_KEY}")
        links[session_id] = activity_id
        activities.append(
            {
                "id": activity_id,
                "title": "Legacy learning session",
                "description": None,
                "category_stable_key": session["activity_type"],
                "category_version": "v1",
                "occurred_at": session["started_at"],
                "context_started_at": session["started_at"],
                "context_ended_at": session["ended_at"],
                "creator_source": "migration",
                "provenance": POLICY_KEY,
                "created_at": session["created_at"],
                "supersedes_activity_id": None,
                "outcome_classification": session["outcome"],
            }
        )
        competency_id = session["competency_identity_id"]
        if competency_id is not None:
            contributions.append(
                {
                    "id": _stable_id(
                        f"session-contribution:{session_id}:{competency_id}:{POLICY_KEY}"
                    ),
                    "session_id": session_id,
                    "competency_identity_id": competency_id,
                    "criterion_identity_id": None,
                    "relevance": "primary",
                    "created_at": session["created_at"],
                    "provenance": "deterministic_legacy_backfill",
                }
            )
    result_rows = sorted(activities + contributions, key=lambda row: (str(row["id"]), len(row)))
    return activities, contributions, links, _rows_hash(source_rows), _rows_hash(result_rows)


def upgrade() -> None:
    connection = op.get_bind()
    metadata = sa.MetaData()
    category_table = sa.Table("activity_category_versions", metadata, autoload_with=connection)
    activity_table = sa.Table("activities", metadata, autoload_with=connection)
    contribution_table = sa.Table("session_contributions", metadata, autoload_with=connection)
    run_table = sa.Table("migration_backfill_runs", metadata, autoload_with=connection)
    for row in _category_rows():
        _insert_or_verify(connection, category_table, row)
    sessions = [
        dict(row)
        for row in connection.execute(
            sa.text(f"SELECT {SESSION_COLUMNS} FROM learning_sessions ORDER BY id")
        ).mappings()
    ]
    activities, contributions, links, source_hash, result_hash = _backfill_rows(sessions)
    for row in activities:
        _insert_or_verify(connection, activity_table, row)
    for session_id, activity_id in links.items():
        current = connection.scalar(
            sa.text("SELECT activity_id FROM learning_sessions WHERE id=:session"),
            {"session": session_id},
        )
        if current is None:
            connection.execute(
                sa.text("UPDATE learning_sessions SET activity_id=:activity WHERE id=:session"),
                {"activity": activity_id, "session": session_id},
            )
        elif current != activity_id:
            raise RuntimeError("Conflicting partial Session Activity assignment.")
    for row in contributions:
        _insert_or_verify(connection, contribution_table, row)
    if connection.scalar(
        sa.text("SELECT COUNT(*) FROM learning_sessions WHERE activity_id IS NULL")
    ):
        raise RuntimeError("Activity backfill left an unassigned Session.")
    if connection.scalar(sa.text("SELECT COUNT(*) FROM activities")) != len(sessions):
        raise RuntimeError("Activity backfill row reconciliation failed.")
    if connection.scalar(sa.text("SELECT COUNT(*) FROM session_contributions")) != len(
        contributions
    ):
        raise RuntimeError("Session contribution backfill reconciliation failed.")
    _insert_or_verify(
        connection,
        run_table,
        {
            "id": RUN_ID,
            "policy_key": POLICY_KEY,
            "source_kind": "v1_learning_sessions",
            "source_row_count": len(sessions),
            "result_row_count": len(activities) + len(contributions),
            "source_hash": source_hash,
            "result_hash": result_hash,
            "recorded_at": BUILTIN_CREATED_AT,
        },
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("DELETE FROM migration_backfill_runs WHERE id=:id"), {"id": RUN_ID})
    connection.execute(sa.text("DELETE FROM contribution_retractions"))
    connection.execute(sa.text("DELETE FROM session_contributions"))
    connection.execute(sa.text("UPDATE learning_sessions SET activity_id=NULL"))
    connection.execute(sa.text("DELETE FROM activities"))
    connection.execute(sa.text("DELETE FROM activity_category_versions"))
