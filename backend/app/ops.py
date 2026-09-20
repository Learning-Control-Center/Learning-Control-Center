from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import shutil
import sqlite3
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config import get_settings
from app.operation_lock import database_path, exclusive_operation_lock
from app.security import hash_password
from app.time_utils import utc_now_ms


def expected_revision() -> str:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "backend" / "alembic"))
    revision = ScriptDirectory.from_config(config).get_current_head()
    if revision is None:
        raise RuntimeError("The migration graph has no current head.")
    return revision


def _supported_restore_revisions() -> set[str]:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "backend" / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    head = scripts.get_current_head()
    if head is None:
        raise RuntimeError("The migration graph has no current head.")
    return {revision.revision for revision in scripts.iterate_revisions(head, "base")}


def _validate_database(connection: sqlite3.Connection, *, require_current: bool = True) -> str:
    if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
        raise RuntimeError("Database integrity validation failed.")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("Database foreign-key validation failed.")
    revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    supported = _supported_restore_revisions()
    if revision is None or revision[0] not in supported:
        raise RuntimeError("Database schema revision is not supported by this application.")
    if require_current and revision != (expected_revision(),):
        raise RuntimeError("Database schema is not at the current application revision.")
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    user_columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
    session_columns = {row[1] for row in connection.execute("PRAGMA table_info(auth_sessions)")}
    if revision[0] in {"0001_initial", "0002_roadmap_scope_events"} and (
        {"security_audit_events", "auth_rate_limit_buckets"} & tables
        or {"credential_generation", "password_changed_at"} & user_columns
        or "credential_generation" in session_columns
    ):
        raise RuntimeError("Database contains a partially applied authentication migration.")
    users = connection.execute("SELECT id FROM users").fetchall()
    if len(users) != 1:
        raise RuntimeError("The operation requires exactly one configured user.")
    return str(users[0][0])


def _sqlite_backup(
    source_path: Path,
    destination: Path,
    *,
    validation: str = "current",
) -> str:
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise RuntimeError("Database integrity validation failed.")
        if target.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Database foreign-key validation failed.")
        if validation != "physical":
            _validate_database(target, require_current=validation == "current")
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        target.close()
        source.close()
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def restore_database(source_path: Path) -> int:
    settings = get_settings()
    source_path = source_path.resolve(strict=True)
    database_file = database_path(settings.database_url)
    if source_path == database_file:
        raise RuntimeError("Restore source must differ from the configured database.")
    settings.backup_directory.mkdir(parents=True, exist_ok=True)
    settings.backup_directory.chmod(0o700)
    with exclusive_operation_lock(settings.database_url):
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        before_path = settings.backup_directory / f"lcc-pre-restore-{timestamp}.sqlite3"
        before_digest = _sqlite_backup(database_file, before_path, validation="physical")
        staging_path = database_file.with_suffix(database_file.suffix + f".{uuid.uuid4()}.restore")
        _sqlite_backup(source_path, staging_path, validation="supported")
        staging = sqlite3.connect(staging_path)
        try:
            source_revision = staging.execute("SELECT version_num FROM alembic_version").fetchone()
        finally:
            staging.close()
        if source_revision != (expected_revision(),):
            root = Path(__file__).resolve().parents[2]
            config = Config(str(root / "alembic.ini"))
            config.set_main_option("script_location", str(root / "backend" / "alembic"))
            staging_url = f"sqlite:///{staging_path}"
            config.set_main_option("sqlalchemy.url", staging_url)
            config.attributes["database_url"] = staging_url
            command.upgrade(config, "head")
        restored = sqlite3.connect(staging_path, isolation_level=None)
        try:
            restored.execute("PRAGMA foreign_keys=ON")
            user_id = _validate_database(restored)
            now = utc_now_ms()
            restored.execute("BEGIN IMMEDIATE")
            restored.execute(
                "UPDATE users SET credential_generation=credential_generation+1, updated_at=? "
                "WHERE id=?",
                (now, user_id),
            )
            restored.execute(
                "UPDATE auth_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (now, user_id),
            )
            restored.execute(
                "INSERT INTO operational_backups "
                "(id,purpose,path,checksum_sha256,size_bytes,created_at) VALUES (?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    "pre-restore",
                    str(before_path),
                    before_digest,
                    before_path.stat().st_size,
                    now,
                ),
            )
            restored.execute(
                "INSERT INTO security_audit_events "
                "(id,event_type,user_id,actor_kind,details_json,occurred_at) VALUES (?,?,?,?,?,?)",
                (str(uuid.uuid4()), "database_restored", user_id, "operator", "{}", now),
            )
            restored.execute("COMMIT")
            _validate_database(restored)
        except Exception:
            if restored.in_transaction:
                restored.execute("ROLLBACK")
            staging_path.unlink(missing_ok=True)
            raise
        finally:
            restored.close()
        os.replace(staging_path, database_file)
        database_file.with_name(database_file.name + "-wal").unlink(missing_ok=True)
        database_file.with_name(database_file.name + "-shm").unlink(missing_ok=True)
    print("Restore completed; all restored sessions were revoked.", file=sys.stderr)
    return 0


def recover_password() -> int:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise RuntimeError("Password recovery requires an interactive TTY.")
    settings = get_settings()
    database_file = database_path(settings.database_url)
    settings.backup_directory.mkdir(parents=True, exist_ok=True)
    settings.backup_directory.chmod(0o700)
    with exclusive_operation_lock(settings.database_url):
        connection = sqlite3.connect(database_file, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            if revision != (expected_revision(),):
                raise RuntimeError("Database schema is not at the current application revision.")
            users = connection.execute("SELECT id FROM users").fetchall()
            if len(users) != 1:
                raise RuntimeError("Password recovery requires exactly one configured user.")
            first = getpass.getpass("New password: ")
            second = getpass.getpass("Confirm new password: ")
            if first != second or not 12 <= len(first) <= 256:
                raise RuntimeError("Passwords must match and contain 12 to 256 characters.")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("BEGIN EXCLUSIVE")
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            backup_path = settings.backup_directory / f"lcc-pre-recovery-{timestamp}.sqlite3"
            temporary_path = settings.backup_directory / f".{backup_path.name}.{uuid.uuid4()}.tmp"
            descriptor = os.open(temporary_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                with database_file.open("rb") as source, os.fdopen(descriptor, "wb") as target:
                    shutil.copyfileobj(source, target)
                    target.flush()
                    os.fsync(target.fileno())
                check = sqlite3.connect(f"file:{temporary_path}?mode=ro", uri=True)
                try:
                    if check.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                        raise RuntimeError("The pre-recovery backup failed integrity validation.")
                    if check.execute("PRAGMA foreign_key_check").fetchall():
                        raise RuntimeError("The pre-recovery backup has foreign-key violations.")
                finally:
                    check.close()
                os.replace(temporary_path, backup_path)
            except Exception:
                temporary_path.unlink(missing_ok=True)
                raise
            backup_digest = hashlib.sha256(backup_path.read_bytes()).hexdigest()
            now = utc_now_ms()
            user_id = users[0][0]
            connection.execute(
                "UPDATE users SET password_hash=?, credential_generation=credential_generation+1, "
                "password_changed_at=?, updated_at=? WHERE id=?",
                (hash_password(first), now, now, user_id),
            )
            connection.execute(
                "UPDATE auth_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (now, user_id),
            )
            connection.execute(
                "INSERT INTO operational_backups "
                "(id,purpose,path,checksum_sha256,size_bytes,created_at) VALUES (?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    "pre-recovery",
                    str(backup_path),
                    backup_digest,
                    backup_path.stat().st_size,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO security_audit_events "
                "(id,event_type,user_id,actor_kind,details_json,occurred_at) VALUES (?,?,?,?,?,?)",
                (str(uuid.uuid4()), "password_recovered", user_id, "operator", "{}", now),
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
    print("Password reset completed; all sessions were revoked.", file=sys.stderr)
    return 0


def verify_installation() -> int:
    """Verify the configured production database without changing it."""
    settings = get_settings()
    database_file = database_path(settings.database_url)
    with exclusive_operation_lock(settings.database_url):
        connection = sqlite3.connect(f"file:{database_file}?mode=ro", uri=True)
        try:
            user_id = _validate_database(connection)
        finally:
            connection.close()
    print(
        f"Installation database is current and has one configured user ({user_id}).",
        file=sys.stderr,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="lcc-ops")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("recover-password", help="Reset the single user's password offline")
    commands.add_parser("verify", help="Verify the offline production database and user invariant")
    restore = commands.add_parser("restore", help="Restore a validated operational SQLite backup")
    restore.add_argument("--from", dest="source", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "recover-password":
        return recover_password()
    if args.command == "verify":
        return verify_installation()
    if args.command == "restore":
        return restore_database(args.source)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
