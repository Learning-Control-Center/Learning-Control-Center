from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_parent(database_url: str) -> None:
    prefix = "sqlite:///"
    if database_url.startswith(prefix) and database_url != "sqlite:///:memory:":
        path = Path(database_url[len(prefix) :]).resolve()
        parent_missing = not path.parent.exists()
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if parent_missing:
            path.parent.chmod(0o700)
        if not path.exists():
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        else:
            path.chmod(0o600)


def create_database_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    _ensure_sqlite_parent(url)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args, future=True)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection: object, _connection_record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)
_engine: Engine | None = None


def initialize_database(database_url: str | None = None) -> Engine:
    global _engine
    if _engine is None:
        _engine = create_database_engine(database_url)
        SessionLocal.configure(bind=_engine)
    return _engine


def run_migrations(database_url: str | None = None) -> Path | None:
    """Upgrade the configured database to the repository's current Alembic revision."""
    repository_root = Path(__file__).resolve().parents[2]
    config = Config(str(repository_root / "alembic.ini"))
    config.set_main_option("script_location", str(repository_root / "backend" / "alembic"))
    target_url = database_url or get_settings().database_url
    _ensure_sqlite_parent(target_url)
    config.set_main_option("sqlalchemy.url", target_url)
    config.attributes["database_url"] = target_url
    backup = _backup_before_migration(target_url, config)
    command.upgrade(config, "head")
    return backup


def _backup_before_migration(database_url: str, config: Config) -> Path | None:
    if not database_url.startswith("sqlite:////"):
        return None
    source_path = Path(database_url.removeprefix("sqlite:///")).resolve()
    if not source_path.exists() or source_path.stat().st_size == 0:
        return None
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        try:
            row = source.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.DatabaseError:
            row = None
        head = ScriptDirectory.from_config(config).get_current_head()
        if row == (head,):
            return None
        _assert_known_migration_source(source, row, config)
        backup_directory = get_settings().backup_directory.resolve()
        backup_directory.mkdir(parents=True, exist_ok=True)
        backup_directory.chmod(0o700)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        destination = backup_directory / f"lcc-pre-migration-{timestamp}.sqlite3"
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise RuntimeError("The pre-migration backup failed integrity validation.")
            if target.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("The pre-migration backup has foreign-key violations.")
        finally:
            target.close()
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        manifest = destination.with_suffix(destination.suffix + ".json")
        manifest_payload = {
            "backupPath": str(destination),
            "checksumSha256": digest,
            "sizeBytes": destination.stat().st_size,
            "sourceRevision": row[0] if row else None,
            "targetRevision": head,
        }
        descriptor = os.open(manifest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(manifest_payload, handle, sort_keys=True, separators=(",", ":"))
        logger.warning("Created verified pre-migration backup at %s", destination)
        return destination
    finally:
        source.close()


def _assert_known_migration_source(
    connection: sqlite3.Connection, revision_row: tuple[str] | None, config: Config
) -> None:
    if revision_row is None:
        raise RuntimeError("Existing database has no recognized Alembic revision.")
    revision = revision_row[0]
    scripts = ScriptDirectory.from_config(config)
    if revision not in {item.revision for item in scripts.walk_revisions()}:
        raise RuntimeError(f"Existing database has unsupported Alembic revision {revision!r}.")
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    user_columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
    session_columns = {row[1] for row in connection.execute("PRAGMA table_info(auth_sessions)")}
    competency_identity_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(competency_identities)")
    }
    security_markers_present = bool(
        any(name.startswith("_alembic_tmp_") for name in tables)
        or {"security_audit_events", "auth_rate_limit_buckets"} & tables
        or {"credential_generation", "password_changed_at"} & user_columns
        or "credential_generation" in session_columns
    )
    profile_markers_present = bool(
        any(name.startswith("_alembic_tmp_") for name in tables)
        or {
            "capability_scale_versions",
            "target_profiles",
            "semantic_competency_definitions",
            "criterion_identities",
            "legacy_criterion_assertions",
            "migration_backfill_runs",
        }
        & tables
        or {
            "identity_created_at",
            "creation_source",
            "legacy_unspecified_reason",
            "retired_at",
            "retirement_reason",
        }
        & competency_identity_columns
    )
    activity_markers_present = bool(
        any(name.startswith("_alembic_tmp_") for name in tables)
        or {
            "activity_category_versions",
            "activities",
            "session_contributions",
            "contribution_retractions",
            "session_corrections",
        }
        & tables
    )
    activity_id_columns = {
        row[1]: row for row in connection.execute("PRAGMA table_info(learning_sessions)")
    }
    activity_tables = {
        "activity_category_versions",
        "activities",
        "session_contributions",
        "contribution_retractions",
        "session_corrections",
    }
    evidence_tables = {
        "evidence",
        "evidence_links",
        "evidence_retractions",
        "evidence_invalidations",
        "evidence_link_retractions",
        "evidence_redactions",
    }
    activity_backfill_present = bool(
        any(
            connection.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone()
            for table in activity_tables & tables
        )
        or (
            "activities" in tables
            and (
                connection.execute("SELECT 1 FROM activities LIMIT 1").fetchone()
                or (
                    "activity_id" in activity_id_columns
                    and connection.execute(
                        "SELECT 1 FROM learning_sessions WHERE activity_id IS NOT NULL LIMIT 1"
                    ).fetchone()
                )
                or (
                    "migration_backfill_runs" in tables
                    and connection.execute(
                        "SELECT 1 FROM migration_backfill_runs "
                        "WHERE source_kind='v1_learning_sessions' LIMIT 1"
                    ).fetchone()
                )
            )
        )
    )
    missing_activity_history_table = not activity_tables <= tables
    if revision in {"0001_initial", "0002_roadmap_scope_events"} and security_markers_present:
        raise RuntimeError(
            "Database has an ambiguously partial authentication migration; restore its verified "
            "pre-migration backup before retrying."
        )
    if revision == "0001_initial" and "roadmap_scope_events" in tables:
        raise RuntimeError(
            "Database has an ambiguously partial roadmap-scope migration; restore a verified "
            "backup before retrying."
        )
    if (
        revision
        in {
            "0001_initial",
            "0002_roadmap_scope_events",
            "0003_auth_security_foundation",
            "0004_analysis_projection_foundation",
        }
        and profile_markers_present
    ):
        raise RuntimeError(
            "Database has an ambiguously partial profile/competency migration; restore its "
            "verified pre-migration backup before retrying."
        )
    if (
        (revision == "0005_profile_competency_core" and activity_markers_present)
        or (revision == "0006_activity_session_schema" and activity_backfill_present)
        or (
            revision == "0007_activity_session_backfill"
            and (
                any(name.startswith("_alembic_tmp_") for name in tables)
                or missing_activity_history_table
                or (
                    "activity_id" in activity_id_columns
                    and bool(activity_id_columns["activity_id"][3])
                )
            )
        )
    ):
        raise RuntimeError(
            "Database has an ambiguously partial Activity/Session migration; restore its "
            "verified pre-migration backup before retrying."
        )
    if revision == "0008_activity_session_constraint" and (
        any(name.startswith("_alembic_tmp_") for name in tables)
        or bool(evidence_tables & tables)
        or (
            "migration_backfill_runs" in tables
            and connection.execute(
                "SELECT 1 FROM migration_backfill_runs "
                "WHERE source_kind='v1_evidence_sources' LIMIT 1"
            ).fetchone()
        )
    ):
        raise RuntimeError(
            "Database has an ambiguously partial unified Evidence migration; restore its "
            "verified pre-migration backup before retrying."
        )


async def get_db() -> AsyncGenerator[Session, None]:
    if _engine is None:
        initialize_database()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
