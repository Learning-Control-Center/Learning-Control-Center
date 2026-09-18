from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app import database
from app.analysis.v3.service import run_analysis
from app.database import create_database_engine
from sqlalchemy import text
from sqlalchemy.orm import Session

RECOMMENDATION_V2_TABLES = (
    "recommendation_v2_runs",
    "recommendation_v2_candidates",
    "recommendation_v2_eligibility_decisions",
    "recommendation_v2_eligibility_rule_results",
    "recommendation_v2_expected_values",
    "recommendation_v2_score_components",
    "recommendation_v2_selection_decisions",
    "recommendation_v2_recommendations",
    "recommendation_v2_reasons",
)


def _migration_config(database_path: Path) -> Config:
    repository_root = Path(__file__).resolve().parents[2]
    config = Config(str(repository_root / "alembic.ini"))
    config.set_main_option("script_location", str(repository_root / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


@pytest.mark.parametrize(
    "marker_table",
    ["recommendation_v2_runs", "_alembic_tmp_recommendation_v2_runs"],
)
def test_stamped_0015_with_recommendation_v2_marker_is_refused(
    tmp_path: Path, marker_table: str
) -> None:
    database_path = tmp_path / f"partial-0016-{marker_table}.sqlite3"
    command.upgrade(_migration_config(database_path), "0015_analysis_v3")
    with sqlite3.connect(database_path) as connection:
        connection.execute(f'CREATE TABLE "{marker_table}" (id TEXT PRIMARY KEY)')

    with pytest.raises(RuntimeError, match="ambiguously partial Recommendation V2 migration"):
        database.run_migrations(f"sqlite:///{database_path}")


@pytest.mark.parametrize("missing_table", RECOMMENDATION_V2_TABLES)
def test_stamped_0016_with_missing_recommendation_v2_table_is_refused(
    tmp_path: Path, missing_table: str
) -> None:
    database_path = tmp_path / f"incomplete-0016-{missing_table}.sqlite3"
    command.upgrade(_migration_config(database_path), "0016_recommendation_v2")
    with sqlite3.connect(database_path) as connection:
        connection.execute(f'DROP TABLE "{missing_table}"')

    with pytest.raises(RuntimeError, match="ambiguously partial Recommendation V2 migration"):
        database.run_migrations(f"sqlite:///{database_path}")


def _all_rows(connection: sqlite3.Connection, table_name: str) -> list[tuple[object, ...]]:
    columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table_name}")')]
    rows = list(connection.execute(f'SELECT * FROM "{table_name}"'))
    return sorted(rows, key=repr) if columns else []


def test_0016_preserves_populated_0015_history_byte_for_byte(tmp_path: Path) -> None:
    database_path = tmp_path / "populated-0015.sqlite3"
    config = _migration_config(database_path)
    command.upgrade(config, "0015_analysis_v3")
    engine = create_database_engine(f"sqlite:///{database_path}")
    with Session(engine) as session:
        run_analysis(
            session,
            idempotency_key="migration-predecessor-analysis",
            purpose="learning_control",
            update_current=True,
        )
        session.commit()
        session.execute(
            text(
                "INSERT INTO analysis_runs "
                "(id,idempotency_key,purpose,scope_json,generated_at,cutoff_at,status,"
                "algorithm_version,configuration_reference,configuration_hash,"
                "input_lineage_json,input_hash,application_version,failure_metadata_json,"
                "completeness_metadata_json) VALUES "
                "('legacy-analysis-run','legacy-analysis-idempotency',"
                "'v1_recommendation_compat','{}',1,2,'completed','recommendation-v1',"
                "'legacy-config',:hash,'{}',:hash,'1.0',NULL,'{}')"
            ),
            {"hash": "1" * 64},
        )
        session.execute(
            text(
                "INSERT INTO analysis_snapshots "
                "(id,run_id,purpose,schema_version,generated_at,cutoff_at,cutoff_semantics,"
                "timezone,completed_through_date,target_profile_id,target_profile_version_id,"
                "capability_scale_version_references_json,learning_graph_reference,"
                "curriculum_reference,semantic_definition_references_json,policy_versions_json,"
                "discipline_configuration_reference,configuration_hash,application_version,"
                "input_lineage_json,input_hash,normalized_facts_json,signals_json,completeness,"
                "unknown_markers_json,output_hash) VALUES "
                "('legacy-analysis-snapshot','legacy-analysis-run','v1_recommendation_compat',"
                "1,1,2,'exclusive','UTC','1970-01-01',NULL,NULL,'[]',NULL,NULL,'[]','{}',"
                "'legacy-config',:hash,'1.0','{}',:hash,'[]','[]','complete','[]',:hash)"
            ),
            {"hash": "1" * 64},
        )
        session.execute(
            text(
                "INSERT INTO recommendation_snapshots "
                "(id,generated_at,local_date,engine_version,primary_activity_type,"
                "structured_payload_json,accepted_primary,analysis_snapshot_id) "
                "VALUES ('legacy-recommendation',1,'2026-01-01',1,'practice',"
                "'{}',0,:snapshot_id)"
            ),
            {"snapshot_id": "legacy-analysis-snapshot"},
        )
        session.commit()
    engine.dispose()
    with sqlite3.connect(database_path) as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            if row[0] != "alembic_version"
        }
        before = {name: _all_rows(connection, name) for name in table_names}
    command.upgrade(config, "0016_recommendation_v2")
    with sqlite3.connect(database_path) as connection:
        after = {name: _all_rows(connection, name) for name in table_names}
        assert after == before
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        for table_name in RECOMMENDATION_V2_TABLES:
            assert connection.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone() == (0,)
