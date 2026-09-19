from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app import database

TODAY_V2_TABLES = (
    "today_generations",
    "today_suggestions",
    "today_interactions",
    "today_interaction_corrections",
    "suggestion_activity_relations",
    "suggestion_activity_relation_corrections",
    "today_suggestion_current_states",
)


def _migration_config(database_path: Path) -> Config:
    repository_root = Path(__file__).resolve().parents[2]
    config = Config(str(repository_root / "alembic.ini"))
    config.set_main_option("script_location", str(repository_root / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


def _all_rows(connection: sqlite3.Connection, table_name: str) -> list[tuple[object, ...]]:
    return sorted(connection.execute(f'SELECT * FROM "{table_name}"').fetchall(), key=repr)


@pytest.mark.parametrize("marker_table", ["today_generations", "_alembic_tmp_today_generations"])
def test_stamped_0016_with_today_marker_is_refused(tmp_path: Path, marker_table: str) -> None:
    database_path = tmp_path / f"partial-0017-{marker_table}.sqlite3"
    command.upgrade(_migration_config(database_path), "0016_recommendation_v2")
    with sqlite3.connect(database_path) as connection:
        connection.execute(f'CREATE TABLE "{marker_table}" (id TEXT PRIMARY KEY)')
    with pytest.raises(RuntimeError, match="ambiguously partial Today V2 migration"):
        database.run_migrations(f"sqlite:///{database_path}")


@pytest.mark.parametrize("missing_table", TODAY_V2_TABLES)
def test_stamped_0017_with_missing_today_table_is_refused(
    tmp_path: Path, missing_table: str
) -> None:
    database_path = tmp_path / f"incomplete-0017-{missing_table}.sqlite3"
    command.upgrade(_migration_config(database_path), "0017_today_v2")
    with sqlite3.connect(database_path) as connection:
        connection.execute(f'DROP TABLE "{missing_table}"')
    with pytest.raises(RuntimeError, match="ambiguously partial Today V2 migration"):
        database.run_migrations(f"sqlite:///{database_path}")


def test_0017_preserves_predecessor_and_does_not_invent_today_history(tmp_path: Path) -> None:
    database_path = tmp_path / "populated-0016.sqlite3"
    config = _migration_config(database_path)
    command.upgrade(config, "0016_recommendation_v2")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO recommendation_snapshots "
            "(id,generated_at,local_date,engine_version,primary_activity_type,"
            "structured_payload_json,accepted_primary,analysis_snapshot_id) "
            "VALUES ('legacy-unknown-decision',1,'2026-01-01',1,'practice','{}',0,NULL)"
        )
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            if row[0] != "alembic_version"
        }
        before = {name: _all_rows(connection, name) for name in table_names}
    command.upgrade(config, "0017_today_v2")
    with sqlite3.connect(database_path) as connection:
        assert {name: _all_rows(connection, name) for name in table_names} == before
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        for table_name in TODAY_V2_TABLES:
            assert connection.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone() == (0,)


def test_0017_empty_downgrade_reupgrades_but_populated_history_refuses(tmp_path: Path) -> None:
    database_path = tmp_path / "today-downgrade.sqlite3"
    config = _migration_config(database_path)
    command.upgrade(config, "0017_today_v2")
    command.downgrade(config, "0016_recommendation_v2")
    command.upgrade(config, "0017_today_v2")
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "INSERT INTO today_generations "
            "(id,idempotency_key,request_hash,local_date,timezone,recommendation_run_id,"
            "recommendation_policy_version,today_policy_version,explicit_generation_sequence,"
            "generation_key,generated_at,output_hash,is_regeneration) VALUES "
            "('generation','today-test-key','" + "1" * 64 + "','2026-01-01','UTC','missing-run',"
            "'recommendation-policy-registry/v1','today-policy/v2.0',1,"
            "'2026-01-01|recommendation-policy-registry/v1|1',1,'" + "2" * 64 + "',0)"
        )
    with pytest.raises(RuntimeError, match="Refusing to downgrade immutable Today V2 history"):
        command.downgrade(config, "0016_recommendation_v2")
