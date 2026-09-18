from __future__ import annotations

from collections.abc import Generator
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from app.config import get_settings
from app.database import create_database_engine
from app.models import AuthSession, RoadmapScopeEvent
from app.portability.registry import (
    PORTABLE_V2_FOUNDATION_TABLES,
    PORTABLE_V3_CURRICULUM_TABLES,
    PORTABLE_V4_PROJECT_TABLES,
)
from httpx import AsyncClient
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session


async def _portable_export(client: AsyncClient, csrf: str) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/import-export/export",
        json={"purpose": "portable_logical_backup", "format": "json"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    return response.json()["content"]


async def _restore(
    client: AsyncClient, csrf: str, package: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    preview = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "scope-backup.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )
    assert preview.status_code == 200, preview.text
    applied = await client.post(
        "/api/v1/import-export/import/apply",
        json={
            "filename": "scope-backup.json",
            "package": package,
            "confirmation_token": preview.json()["confirmationToken"],
            "replace_existing": True,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert applied.status_code == 200, applied.text
    return preview.json(), applied.json()


async def test_scope_events_record_activation_phase_change_and_import_order(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
    roadmap_payload: dict[str, object],
    db: Session,
) -> None:
    client, csrf, roadmap = configured_client
    events = db.scalars(select(RoadmapScopeEvent).order_by(RoadmapScopeEvent.event_sequence)).all()
    assert len(events) == 1
    assert events[0].source == "roadmap_apply"
    assert events[0].event_sequence == 1
    assert events[0].roadmap_version_id == roadmap["activeVersion"]["id"]
    assert events[0].phase_id == roadmap["currentPhaseId"]

    phase_two_id = roadmap["phases"][1]["id"]
    changed = await client.put(
        f"/api/v1/roadmap/current-phase/{phase_two_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert changed.status_code == 200, changed.text

    updated = deepcopy(roadmap_payload)
    updated["version"] = "2.0.0"
    updated["current_phase_stable_key"] = "phase-1"
    package = {
        "schemaVersion": 1,
        "packageType": "roadmap_update",
        "packageId": "scope-roadmap-update",
        "appVersion": "1.0.0",
        "createdAt": "2026-09-05T12:00:00.000Z",
        "payload": {"roadmap": updated},
    }
    preview = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "roadmap.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )
    assert preview.status_code == 200, preview.text
    applied = await client.post(
        "/api/v1/import-export/import/apply",
        json={
            "filename": "roadmap.json",
            "package": package,
            "confirmation_token": preview.json()["confirmationToken"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert applied.status_code == 200, applied.text

    db.expire_all()
    events = db.scalars(select(RoadmapScopeEvent).order_by(RoadmapScopeEvent.event_sequence)).all()
    assert [event.event_sequence for event in events] == [1, 2, 3]
    assert [event.source for event in events] == [
        "roadmap_apply",
        "current_phase_change",
        "roadmap_import",
    ]
    assert [event.occurred_at for event in events] == sorted(event.occurred_at for event in events)
    assert events[1].phase_id == phase_two_id
    assert events[2].roadmap_version_id != events[0].roadmap_version_id


async def test_new_portable_backup_round_trips_scope_history_and_preserves_auth(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, roadmap = configured_client
    phase_two_id = roadmap["phases"][1]["id"]
    response = await client.put(
        f"/api/v1/roadmap/current-phase/{phase_two_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    package = await _portable_export(client, csrf)
    expected_events = deepcopy(package["payload"]["tables"]["roadmap_scope_events"])
    auth_ids = set(db.scalars(select(AuthSession.id)).all())
    phase_one_id = roadmap["phases"][0]["id"]
    response = await client.put(
        f"/api/v1/roadmap/current-phase/{phase_one_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200

    preview, applied = await _restore(client, csrf, package)

    assert preview["summary"]["portableCompatibility"] == "current"
    assert preview["summary"]["scopeHistoryBaselineAdded"] is False
    assert applied["authenticationPreserved"] is True
    assert set(db.scalars(select(AuthSession.id)).all()) == auth_ids
    restored_events = [
        {column.name: getattr(event, column.name) for column in RoadmapScopeEvent.__table__.columns}
        for event in db.scalars(
            select(RoadmapScopeEvent).order_by(RoadmapScopeEvent.event_sequence)
        ).all()
    ]
    assert restored_events[: len(expected_events)] == expected_events
    assert restored_events[-1]["source"] == "portable_restore"
    assert restored_events[-1]["event_sequence"] == len(expected_events) + 1
    assert restored_events[-1]["phase_id"] == phase_two_id
    round_trip = await _portable_export(client, csrf)
    assert round_trip["schemaVersion"] == 4
    assert round_trip["payload"]["tables"]["roadmap_scope_events"] == restored_events


async def test_legacy_portable_backup_gets_only_deterministic_current_scope_baseline(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, _roadmap = configured_client
    package = await _portable_export(client, csrf)
    current_roadmap = next(
        row for row in package["payload"]["tables"]["roadmaps"] if row["is_current"]
    )
    package["payload"]["tables"].pop("roadmap_scope_events")
    for table_name in PORTABLE_V2_FOUNDATION_TABLES:
        package["payload"]["tables"].pop(table_name)
    for table_name in PORTABLE_V3_CURRICULUM_TABLES:
        package["payload"]["tables"].pop(table_name)
    for table_name in PORTABLE_V4_PROJECT_TABLES:
        package["payload"]["tables"].pop(table_name)
    package["payload"].pop("curriculumCatalogCheckpoint")
    package["payload"].pop("projectCatalogCheckpoint")
    package["payload"].pop("portableScope")
    package["payload"].pop("manifest")
    package["schemaVersion"] = 1
    auth_ids = set(db.scalars(select(AuthSession.id)).all())

    preview, _applied = await _restore(client, csrf, package)

    assert package["schemaVersion"] == 1
    assert preview["summary"]["portableCompatibility"] == "legacy_scope_baseline"
    assert preview["summary"]["scopeHistoryBaselineAdded"] is True
    events = db.scalars(select(RoadmapScopeEvent)).all()
    assert len(events) == 1
    assert events[0].source == "restore_baseline"
    assert events[0].event_sequence == 1
    assert events[0].occurred_at == current_roadmap["updated_at"]
    assert events[0].roadmap_id == current_roadmap["id"]
    assert events[0].roadmap_version_id == current_roadmap["active_version_id"]
    assert events[0].phase_id == current_roadmap["current_phase_id"]
    assert set(db.scalars(select(AuthSession.id)).all()) == auth_ids


async def test_portable_scope_history_rejects_invalid_current_scope_reference(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, _roadmap = configured_client
    package = await _portable_export(client, csrf)
    event = package["payload"]["tables"]["roadmap_scope_events"][-1]
    event["phase_id"] = next(
        phase["id"]
        for phase in package["payload"]["tables"]["phases"]
        if phase["id"] != event["phase_id"]
    )

    response = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "invalid-scope.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "PORTABLE_SCOPE_HISTORY_INVALID"


def _migration_config(database_path: Path) -> Config:
    repository_root = Path(__file__).resolve().parents[2]
    config = Config(str(repository_root / "alembic.ini"))
    config.set_main_option("script_location", str(repository_root / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


@pytest.fixture
def migration_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[Path, Config], None, None]:
    database_path = tmp_path / "scope-migration.sqlite3"
    monkeypatch.setenv("LCC_DATABASE_URL", f"sqlite:///{database_path}")
    get_settings.cache_clear()
    yield database_path, _migration_config(database_path)
    get_settings.cache_clear()


def test_scope_migration_fresh_check_downgrade_reupgrade_and_baseline(
    migration_database: tuple[Path, Config],
) -> None:
    database_path, config = migration_database
    command.upgrade(config, "head")
    command.check(config)
    engine = create_database_engine(f"sqlite:///{database_path}")
    assert inspect(engine).has_table("roadmap_scope_events")
    engine.dispose()

    command.downgrade(config, "0001_initial")
    engine = create_database_engine(f"sqlite:///{database_path}")
    assert not inspect(engine).has_table("roadmap_scope_events")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO roadmaps "
                "(id, stable_key, title, description, is_current, active_version_id, "
                "current_phase_id, created_at, updated_at) "
                "VALUES ('roadmap-1', 'roadmap', 'Roadmap', '', 1, NULL, NULL, 1000, 1234)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO roadmap_versions "
                "(id, roadmap_id, version, schema_version, changelog, source, created_at) "
                "VALUES ('version-1', 'roadmap-1', '1.0.0', 1, '', 'manual', 1000)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO phases "
                "(id, roadmap_version_id, stable_key, title, description, order_index, archived) "
                "VALUES ('phase-1', 'version-1', 'phase-1', 'Phase', '', 0, 0)"
            )
        )
        connection.execute(
            text(
                "UPDATE roadmaps SET active_version_id = 'version-1', "
                "current_phase_id = 'phase-1' WHERE id = 'roadmap-1'"
            )
        )
    engine.dispose()

    command.upgrade(config, "head")
    command.check(config)
    engine = create_database_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        event = (
            connection.execute(
                text(
                    "SELECT roadmap_id, roadmap_version_id, phase_id, source, occurred_at, "
                    "event_sequence FROM roadmap_scope_events"
                )
            )
            .mappings()
            .one()
        )
    engine.dispose()
    assert dict(event) == {
        "roadmap_id": "roadmap-1",
        "roadmap_version_id": "version-1",
        "phase_id": "phase-1",
        "source": "migration_baseline",
        "occurred_at": 1234,
        "event_sequence": 1,
    }
