from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import warnings
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from app import database, ops
from app import models as _models  # noqa: F401
from app.config import get_settings
from app.database import Base, create_database_engine
from app.import_export import _validate_portable_payload
from app.models import LearningSession
from app.sessions import serialize_session
from sqlalchemy.exc import SAWarning
from sqlalchemy.orm import Session

FIXTURES = Path(__file__).parent / "fixtures" / "v1"


def _config(path: Path) -> Config:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    return config


def _table_rows(
    connection: sqlite3.Connection, table: str, columns: list[str]
) -> list[tuple[object, ...]]:
    selected = ",".join(f'"{column}"' for column in columns)
    return sorted(connection.execute(f'SELECT {selected} FROM "{table}"').fetchall(), key=repr)


def _table_names(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        connection.close()


def test_frozen_revision_boundaries_are_explicit(tmp_path: Path) -> None:
    database_path = tmp_path / "boundaries.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0001_initial")
    at_0001 = _table_names(database_path)
    assert len(at_0001 - {"alembic_version"}) == 26
    assert "roadmap_scope_events" not in at_0001
    assert "security_audit_events" not in at_0001
    assert "auth_rate_limit_buckets" not in at_0001

    command.upgrade(config, "0002_roadmap_scope_events")
    at_0002 = _table_names(database_path)
    assert at_0002 == at_0001 | {"roadmap_scope_events"}


def test_frozen_v1_fixture_checksums() -> None:
    assert {path.name for path in FIXTURES.iterdir()} == {
        "SHA256SUMS",
        "empty-0002.sqlite3",
        "expected-api-history.json",
        "paused-timer-0002.sqlite3",
        "populated-0002.sqlite3",
        "portable-empty-schema-v1.json",
        "portable-minimal-schema-v1.json",
        "running-timer-0002.sqlite3",
        "schema-signature.json",
    }
    expected = {
        line.split()[1]: line.split()[0]
        for line in (FIXTURES / "SHA256SUMS").read_text().splitlines()
    }
    for name, digest in expected.items():
        assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == digest

    connection = sqlite3.connect(FIXTURES / "empty-0002.sqlite3")
    try:
        rows = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL "
            "AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version' ORDER BY type,name"
        ).fetchall()
    finally:
        connection.close()
    signature = "\n".join("|".join(map(str, row)) for row in rows) + "\n"
    expected_schema = json.loads((FIXTURES / "schema-signature.json").read_text())
    assert len(rows) == expected_schema["schemaObjectCount"]
    assert hashlib.sha256(signature.encode()).hexdigest() == expected_schema["schemaSha256"]


@pytest.mark.parametrize(
    "fixture_name", ["portable-empty-schema-v1.json", "portable-minimal-schema-v1.json"]
)
def test_frozen_v1_portable_packages_are_valid(fixture_name: str) -> None:
    package = json.loads((FIXTURES / fixture_name).read_text())
    tables, summary = _validate_portable_payload(package["payload"], package["packageId"])
    assert set(tables) == set(package["payload"]["tables"])
    assert summary["portableCompatibility"] == "current"


def test_frozen_v1_api_and_history_golden(tmp_path: Path) -> None:
    golden = json.loads((FIXTURES / "expected-api-history.json").read_text())
    database_path = tmp_path / "golden.sqlite3"
    shutil.copyfile(FIXTURES / "populated-0002.sqlite3", database_path)
    engine = create_database_engine(f"sqlite:///{database_path}")
    try:
        with Session(engine) as session:
            item = session.get(LearningSession, "fixture-learning-session")
            assert item is not None
            assert golden["api"]["sessionHistory"] == {
                "items": [serialize_session(item)],
                "limit": 50,
                "offset": 0,
            }
    finally:
        engine.dispose()
    connection = sqlite3.connect(FIXTURES / "populated-0002.sqlite3")
    try:
        assert golden["history"] == {
            "verificationRecordIds": [
                row[0] for row in connection.execute("SELECT id FROM verification_records")
            ],
            "verificationEvidenceIds": [
                row[0] for row in connection.execute("SELECT id FROM verification_evidence")
            ],
            "competencyStatusEventIds": [
                row[0] for row in connection.execute("SELECT id FROM competency_status_events")
            ],
            "roadmapScopeEventIds": [
                row[0] for row in connection.execute("SELECT id FROM roadmap_scope_events")
            ],
        }
    finally:
        connection.close()


@pytest.mark.parametrize(
    "fixture_name",
    ["populated-0002.sqlite3", "running-timer-0002.sqlite3", "paused-timer-0002.sqlite3"],
)
def test_populated_0002_upgrades_without_changing_v1_values(
    tmp_path: Path, fixture_name: str
) -> None:
    target = tmp_path / "populated.sqlite3"
    shutil.copyfile(FIXTURES / fixture_name, target)
    before = sqlite3.connect(target)
    try:
        tables = [
            row[0]
            for row in before.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "AND name!='alembic_version' ORDER BY name"
            )
        ]
        columns = {
            table: [row[1] for row in before.execute(f'PRAGMA table_info("{table}")')]
            for table in tables
        }
        rows_before = {table: _table_rows(before, table, columns[table]) for table in tables}
    finally:
        before.close()
    command.upgrade(_config(target), "head")
    command.check(_config(target))
    after = sqlite3.connect(target)
    try:
        for table in tables:
            assert _table_rows(after, table, columns[table]) == rows_before[table]
        assert after.execute("SELECT credential_generation FROM users").fetchone() == (1,)
        assert after.execute("SELECT credential_generation FROM auth_sessions").fetchone() == (1,)
        assert after.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        after.close()


def test_migration_creates_verified_backup_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "upgrade.sqlite3"
    backup_directory = tmp_path / "backups"
    shutil.copyfile(FIXTURES / "populated-0002.sqlite3", database_path)
    monkeypatch.setenv("LCC_BACKUP_DIRECTORY", str(backup_directory))
    get_settings.cache_clear()
    try:
        backup = database.run_migrations(f"sqlite:///{database_path}")
    finally:
        get_settings.cache_clear()
    assert backup is not None
    backup_digest = hashlib.sha256(backup.read_bytes()).hexdigest()
    assert os.stat(backup).st_mode & 0o777 == 0o600
    assert os.stat(backup_directory).st_mode & 0o777 == 0o700
    manifest_path = backup.with_suffix(backup.suffix + ".json")
    manifest = json.loads(manifest_path.read_text())
    assert manifest["checksumSha256"] == backup_digest
    assert manifest["sourceRevision"] == "0002_roadmap_scope_events"
    assert manifest["targetRevision"] == "0003_auth_security_foundation"
    assert os.stat(manifest_path).st_mode & 0o777 == 0o600
    original = sqlite3.connect(FIXTURES / "populated-0002.sqlite3")
    copied = sqlite3.connect(backup)
    try:
        for table in sorted(_table_names(backup)):
            columns = [row[1] for row in original.execute(f'PRAGMA table_info("{table}")')]
            assert _table_rows(copied, table, columns) == _table_rows(original, table, columns)
        assert copied.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert copied.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        copied.close()
        original.close()


def test_backup_failure_prevents_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_path = tmp_path / "upgrade.sqlite3"
    shutil.copyfile(FIXTURES / "populated-0002.sqlite3", database_path)

    def fail_backup(_database_url: str, _config: Config) -> Path | None:
        raise RuntimeError("injected backup failure")

    monkeypatch.setattr(database, "_backup_before_migration", fail_backup)
    with pytest.raises(RuntimeError, match="injected backup failure"):
        database.run_migrations(f"sqlite:///{database_path}")
    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0002_roadmap_scope_events",
        )
        assert "credential_generation" not in {
            row[1] for row in connection.execute("PRAGMA table_info(users)")
        }
    finally:
        connection.close()


def test_migrated_schema_matches_orm_and_only_known_cycle_is_accepted(tmp_path: Path) -> None:
    database_path = tmp_path / "parity.sqlite3"
    database.run_migrations(f"sqlite:///{database_path}")
    engine = create_database_engine(f"sqlite:///{database_path}")
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", SAWarning)
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"compare_server_default": True, "render_as_batch": True},
            )
            assert compare_metadata(context, Base.metadata) == []
        list(Base.metadata.sorted_tables)
    engine.dispose()
    cycle_warnings = [
        str(item.message)
        for item in captured
        if "Cannot correctly sort tables" in str(item.message)
    ]
    assert len(cycle_warnings) >= 1
    assert set(cycle_warnings) == {
        'Cannot correctly sort tables; there are unresolvable cycles between tables "phases, '
        'roadmap_versions, roadmaps", which is usually caused by mutually dependent foreign key '
        "constraints.  Foreign key constraints involving these tables will not be considered; "
        "this warning may raise an error in a future release."
    }
    unexpected = [
        str(item.message)
        for item in captured
        if issubclass(item.category, SAWarning)
        and "Cannot correctly sort tables" not in str(item.message)
    ]
    assert unexpected == []


@pytest.mark.parametrize(
    "partial_sql",
    [
        "CREATE TABLE _alembic_tmp_users (id TEXT PRIMARY KEY)",
        "CREATE TABLE _alembic_tmp_auth_sessions (id TEXT PRIMARY KEY)",
        "ALTER TABLE users ADD COLUMN credential_generation INTEGER",
        "CREATE TABLE security_audit_events (id TEXT PRIMARY KEY)",
    ],
)
def test_partial_0003_is_refused_then_verified_v1_restore_can_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, partial_sql: str
) -> None:
    database_path = tmp_path / "data" / "partial.sqlite3"
    backup_directory = tmp_path / "backups"
    database_path.parent.mkdir()
    shutil.copyfile(FIXTURES / "populated-0002.sqlite3", database_path)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(partial_sql)
        connection.commit()
    finally:
        connection.close()
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("LCC_DATABASE_URL", database_url)
    monkeypatch.setenv("LCC_BACKUP_DIRECTORY", str(backup_directory))
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="ambiguously partial authentication migration"):
            database.run_migrations(database_url)
        assert ops.restore_database(FIXTURES / "populated-0002.sqlite3") == 0
    finally:
        get_settings.cache_clear()
    restored = sqlite3.connect(database_path)
    try:
        assert restored.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0003_auth_security_foundation",
        )
        assert restored.execute("SELECT credential_generation FROM users").fetchone() == (2,)
        assert restored.execute("SELECT revoked_at IS NOT NULL FROM auth_sessions").fetchone() == (
            1,
        )
        assert restored.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert restored.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        restored.close()
    assert len(list(backup_directory.glob("lcc-pre-restore-*.sqlite3"))) == 1


def test_0003_downgrade_and_reupgrade_preserve_v1_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "round-trip.sqlite3"
    shutil.copyfile(FIXTURES / "populated-0002.sqlite3", database_path)
    config = _config(database_path)
    command.upgrade(config, "0003_auth_security_foundation")
    command.downgrade(config, "0002_roadmap_scope_events")
    downgraded = sqlite3.connect(database_path)
    original = sqlite3.connect(FIXTURES / "populated-0002.sqlite3")
    try:
        for table in sorted(_table_names(FIXTURES / "populated-0002.sqlite3")):
            columns = [row[1] for row in original.execute(f'PRAGMA table_info("{table}")')]
            assert _table_rows(downgraded, table, columns) == _table_rows(original, table, columns)
        assert "credential_generation" not in {
            row[1] for row in downgraded.execute("PRAGMA table_info(users)")
        }
        assert downgraded.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        original.close()
        downgraded.close()
    command.upgrade(config, "head")
    command.check(config)
