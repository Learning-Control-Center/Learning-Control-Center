from __future__ import annotations

from copy import deepcopy

from app.models import AuthSession, CompetencyIdentity, ImportRecord, LearningSession, Track, User
from app.time_utils import utc_now_ms
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session


async def _portable_export(client: AsyncClient, csrf: str) -> dict[str, object]:
    response = await client.post(
        "/api/v1/import-export/export",
        json={"purpose": "portable_logical_backup", "format": "json"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    return response.json()["content"]


async def test_export_taxonomy_and_secret_exclusion(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, _roadmap = configured_client
    invalid = await client.post(
        "/api/v1/import-export/export",
        json={"purpose": "portable_logical_backup", "format": "markdown"},
        headers={"X-CSRF-Token": csrf},
    )
    assert invalid.status_code == 422

    package = await _portable_export(client, csrf)
    tables = package["payload"]["tables"]
    assert "users" not in tables
    assert "auth_sessions" not in tables
    serialized = str(package)
    assert "password_hash" not in serialized
    assert "token_lookup_hash" not in serialized
    assert "csrf_secret_hash" not in serialized

    analysis = await client.post(
        "/api/v1/import-export/export",
        json={
            "purpose": "analysis_snapshot",
            "format": "json",
            "range": "30d",
            "categories": ["analytics"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert analysis.status_code == 200
    assert set(analysis.json()["content"]["payload"]) == {"scope", "analytics"}

    basics = db.scalar(
        select(CompetencyIdentity).where(CompetencyIdentity.stable_key == "python.basics")
    )
    functions = db.scalar(
        select(CompetencyIdentity).where(CompetencyIdentity.stable_key == "python.functions")
    )
    python_track = db.scalar(select(Track).where(Track.stable_key == "python"))
    started_at = utc_now_ms() - 60_000
    db.add(
        LearningSession(
            competency_identity_id=basics.id,
            track_id=python_track.id,
            session_mode="manual",
            activity_type="learning",
            assistance_mode="none",
            started_at=started_at,
            ended_at=started_at + 60_000,
            accumulated_duration_ms=60_000,
            duration_ms=60_000,
            outcome="completed",
        )
    )
    db.commit()
    selective = await client.post(
        "/api/v1/import-export/export",
        json={
            "purpose": "analysis_snapshot",
            "format": "json",
            "range": "7d",
            "categories": ["analytics", "sessions"],
            "competency_identity_ids": [basics.id],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert selective.status_code == 200, selective.text
    payload = selective.json()["content"]["payload"]
    assert set(payload["analytics"]["competencies"]) == {basics.id}
    assert functions.id not in payload["analytics"]["competencies"]
    assert payload["analytics"]["totalDurationMs"] == 60_000
    assert len(payload["sessions"]) == 1
    assert payload["sessions"][0]["competency_identity_id"] == basics.id

    human = await client.post(
        "/api/v1/import-export/export",
        json={"purpose": "human_report", "format": "markdown", "range": "7d"},
        headers={"X-CSRF-Token": csrf},
    )
    assert human.status_code == 200
    assert human.json()["content"].startswith("# Learning-Control-Center export")


async def test_dry_run_does_not_mutate_and_full_restore_preserves_authentication(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, _roadmap = configured_client
    package = await _portable_export(client, csrf)
    identity_count = db.scalar(select(func.count(CompetencyIdentity.id)))
    user_id = db.scalar(select(User.id))
    session_count = db.scalar(select(func.count(AuthSession.id)))
    inspect = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "backup.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )
    assert inspect.status_code == 200, inspect.text
    assert inspect.json()["dryRun"] is True
    assert db.scalar(select(func.count(CompetencyIdentity.id))) == identity_count
    assert db.scalar(select(func.count(ImportRecord.id))) == 0

    no_replace = await client.post(
        "/api/v1/import-export/import/apply",
        json={
            "filename": "backup.json",
            "package": package,
            "confirmation_token": inspect.json()["confirmationToken"],
            "replace_existing": False,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert no_replace.status_code == 409

    inspect_again = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "backup.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )
    applied = await client.post(
        "/api/v1/import-export/import/apply",
        json={
            "filename": "backup.json",
            "package": package,
            "confirmation_token": inspect_again.json()["confirmationToken"],
            "replace_existing": True,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert applied.status_code == 200, applied.text
    assert db.scalar(select(User.id)) == user_id
    assert db.scalar(select(func.count(AuthSession.id))) == session_count
    assert db.scalar(select(func.count(CompetencyIdentity.id))) == identity_count
    assert db.scalar(select(func.count(ImportRecord.id))) == 1


async def test_invalid_portable_package_is_rejected_without_mutation(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, _roadmap = configured_client
    package = await _portable_export(client, csrf)
    corrupt = deepcopy(package)
    corrupt["packageId"] = "corrupt-package"
    del corrupt["payload"]["tables"]["learning_sessions"]
    before = db.scalar(select(func.count(CompetencyIdentity.id)))
    response = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "corrupt.json", "package": corrupt},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422
    assert db.scalar(select(func.count(CompetencyIdentity.id))) == before
