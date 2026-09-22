from __future__ import annotations

from types import SimpleNamespace

import app.main as main_module
import pytest
from app.models import AuthSession, User
from app.time_utils import epoch_ms_to_rfc3339
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.orm import Session


def test_sqlite_foreign_keys_and_time_round_trip(db: Session) -> None:
    assert db.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    assert epoch_ms_to_rfc3339(1_788_284_472_481) == "2026-09-01T17:41:12.481Z"


async def test_bootstrap_login_session_csrf_and_logout(
    client: AsyncClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    bootstrap = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": "learner",
            "password": "correct horse battery staple",
            "bootstrap_token": "test-bootstrap-token-with-enough-entropy",
        },
    )
    assert bootstrap.status_code == 201
    csrf = bootstrap.json()["csrf_token"]
    assert db.query(User).one().password_hash.startswith("$argon2id$")
    stored_session = db.query(AuthSession).one()
    cookie = client.cookies.get("lcc_session")
    assert cookie and cookie not in stored_session.token_lookup_hash
    assert cookie.split(".", 1)[0] not in stored_session.token_lookup_hash

    # Startup after the first account must succeed even before root removes the
    # now-consumed token. The API must still reject its reuse.
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: SimpleNamespace(
            environment="production", bootstrap_token="test-bootstrap-token-with-enough-entropy"
        ),
    )
    main_module._validate_database_state(db)

    second_bootstrap = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": "other",
            "password": "another long secure password",
            "bootstrap_token": "test-bootstrap-token-with-enough-entropy",
        },
    )
    assert second_bootstrap.status_code == 409

    missing_csrf = await client.put(
        "/api/v1/settings/discipline",
        json={
            "weekly_target_active_days": 4,
            "target_duration_ms_per_active_day": 2_700_000,
            "timezone": "UTC",
        },
    )
    assert missing_csrf.status_code == 403
    accepted = await client.put(
        "/api/v1/settings/discipline",
        json={
            "weekly_target_active_days": 4,
            "target_duration_ms_per_active_day": 2_700_000,
            "timezone": "UTC",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert accepted.status_code == 200
    assert (
        await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    ).status_code == 204
    assert (await client.get("/api/v1/auth/session")).status_code == 401
