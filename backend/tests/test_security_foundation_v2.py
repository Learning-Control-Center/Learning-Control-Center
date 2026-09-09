from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import app.main as main_module
import pytest
from app import ops
from app.authn.client_ip import resolve_client_ip
from app.authn.service import (
    arm_generation_guard,
    change_password_atomically,
    consume_failure_budget,
    revoke_all_sessions,
)
from app.config import Settings, get_settings, is_strong_operator_secret
from app.database import create_database_engine, run_migrations
from app.errors import AppError
from app.models import AuthRateLimitBucket, AuthSession, SecurityAuditEvent, User
from app.operation_lock import database_path, exclusive_operation_lock
from app.security import RateLimitRule, hash_password
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request


def _request(peer: str, forwarded: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": forwarded or [],
            "client": (peer, 1234),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


def test_production_configuration_fails_closed(tmp_path: Path) -> None:
    valid = {
        "environment": "production",
        "database_url": f"sqlite:///{tmp_path / 'data' / 'lcc.sqlite3'}",
        "backup_directory": tmp_path / "backups",
        "public_origin": "https://learn.example.test",
        "allowed_origins": ["https://learn.example.test"],
        "allowed_hosts": ["learn.example.test"],
        "trusted_proxy_cidrs": ["127.0.0.0/8", "::1/128"],
        "security_secret": "Q7vN2xK9mR4pT8wY3cF6hJ1sD5gL0bZa",
    }
    assert Settings(**valid).secure_cookies is True
    with pytest.raises(ValidationError):
        Settings(**{**valid, "public_origin": "http://learn.example.test"})
    with pytest.raises(ValidationError):
        Settings(**{**valid, "database_url": "sqlite:///relative.sqlite3"})
    with pytest.raises(ValidationError):
        Settings(**{**valid, "security_secret": "change-me"})
    with pytest.raises(ValidationError):
        Settings(**{**valid, "environment": "prodution"})
    with pytest.raises(ValidationError):
        Settings(**{**valid, "public_origin": "https://user@learn.example.test"})
    with pytest.raises(ValidationError):
        Settings(**{**valid, "public_origin": "https://learn.example.test?unsafe=1"})
    with pytest.raises(ValidationError):
        Settings(**{**valid, "trusted_proxy_cidrs": ["0.0.0.0/0"]})


def test_invalid_production_settings_do_not_create_storage(tmp_path: Path) -> None:
    database_file = tmp_path / "missing" / "lcc.sqlite3"
    backup_directory = tmp_path / "backups"
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            database_url=f"sqlite:///{database_file}",
            backup_directory=backup_directory,
            public_origin="http://learn.example.test",
            allowed_origins=["http://learn.example.test"],
            allowed_hosts=["learn.example.test"],
            trusted_proxy_cidrs=["127.0.0.0/8"],
            security_secret="Q7vN2xK9mR4pT8wY3cF6hJ1sD5gL0bZa",
        )
    assert not database_file.parent.exists()
    assert not backup_directory.exists()


@pytest.mark.parametrize(
    "value",
    [None, "a" * 64, "change-me-to-a-long-bootstrap-token", "password-password-password-password"],
)
def test_operator_secret_policy_rejects_weak_values(value: str | None) -> None:
    assert not is_strong_operator_secret(value)


def test_sqlite_storage_is_created_with_restrictive_modes(tmp_path: Path) -> None:
    database_path = tmp_path / "private" / "lcc.sqlite3"
    engine = create_database_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE mode_probe (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO mode_probe DEFAULT VALUES")
    assert database_path.stat().st_mode & 0o777 == 0o600
    assert database_path.parent.stat().st_mode & 0o777 == 0o700
    with exclusive_operation_lock(f"sqlite:///{database_path}"):
        lock_path = database_path.with_suffix(".sqlite3.operations.lock")
        assert lock_path.stat().st_mode & 0o777 == 0o600
    engine.dispose()


def test_operation_lock_accepts_development_database_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert database_path("sqlite:///./data/lcc.db") == (tmp_path / "data" / "lcc.db").resolve()


def test_production_bootstrap_state_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_file = tmp_path / "data" / "lcc.sqlite3"
    database_url = f"sqlite:///{database_file}"
    run_migrations(database_url)
    engine = create_database_engine(database_url)

    def production_settings(bootstrap_token: str) -> Settings:
        return Settings(
            environment="production",
            database_url=database_url,
            backup_directory=tmp_path / "backups",
            bootstrap_token=bootstrap_token,
            public_origin="https://learn.example.test",
            allowed_origins=["https://learn.example.test"],
            allowed_hosts=["learn.example.test"],
            trusted_proxy_cidrs=["127.0.0.0/8"],
            security_secret="Q7vN2xK9mR4pT8wY3cF6hJ1sD5gL0bZa",
        )

    with Session(engine) as session:
        monkeypatch.setattr(main_module, "get_settings", lambda: production_settings("a" * 64))
        with pytest.raises(RuntimeError, match="strong bootstrap token"):
            main_module._validate_database_state(session)
        session.add(User(username="learner", password_hash=hash_password("secure password value")))
        session.commit()
        monkeypatch.setattr(
            main_module,
            "get_settings",
            lambda: production_settings("Q7vN2xK9mR4pT8wY3cF6hJ1sD5gL0bZa"),
        )
        with pytest.raises(RuntimeError, match="Remove the bootstrap token"):
            main_module._validate_database_state(session)
    engine.dispose()


def test_forwarded_client_resolution_has_an_explicit_trust_boundary() -> None:
    settings = Settings(trusted_proxy_cidrs=["127.0.0.0/8", "10.0.0.0/8"])
    header = [(b"x-forwarded-for", b"198.51.100.8, 10.1.2.3")]
    assert resolve_client_ip(_request("127.0.0.1", header), settings) == "198.51.100.8"
    assert resolve_client_ip(_request("192.0.2.10", header), settings) == "192.0.2.10"
    assert resolve_client_ip(_request("127.0.0.1"), settings) == "127.0.0.1"
    ipv6_settings = Settings(trusted_proxy_cidrs=["::1/128", "2001:db8:1::/48"])
    assert (
        resolve_client_ip(
            _request("::1", [(b"x-forwarded-for", b"2001:db8::7, 2001:db8:1::9")]),
            ipv6_settings,
        )
        == "2001:db8::7"
    )
    with pytest.raises(AppError):
        resolve_client_ip(_request("127.0.0.1", [(b"x-forwarded-for", b"not-an-ip")]), settings)
    with pytest.raises(AppError):
        resolve_client_ip(
            _request(
                "127.0.0.1",
                [(b"x-forwarded-for", b"198.51.100.8"), (b"x-forwarded-for", b"192.0.2.2")],
            ),
            settings,
        )


async def test_password_change_revokes_sessions_and_requires_new_login(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    wrong = await client.post(
        "/api/v1/auth/password/change",
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": "wrong password value",
            "new_password": "a replacement secure password",
            "confirm_new_password": "a replacement secure password",
        },
    )
    assert wrong.status_code == 401
    user = db.scalar(select(User))
    assert user is not None and user.credential_generation == 1

    changed = await client.post(
        "/api/v1/auth/password/change",
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": "correct horse battery staple",
            "new_password": "a replacement secure password",
            "confirm_new_password": "a replacement secure password",
        },
    )
    assert changed.status_code == 204
    db.refresh(user)
    assert user.credential_generation == 2
    assert all(item.revoked_at is not None for item in db.scalars(select(AuthSession)))
    assert "password_changed" in set(db.scalars(select(SecurityAuditEvent.event_type)))
    assert (await client.get("/api/v1/auth/session")).status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"username": "learner", "password": "a replacement secure password"},
        )
    ).status_code == 200


async def test_origin_rejection_and_security_headers(
    authenticated_client: tuple[AsyncClient, str],
) -> None:
    client, csrf = authenticated_client
    rejected = await client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": csrf, "Origin": "https://attacker.example"},
    )
    assert rejected.status_code == 403
    response = await client.get("/api/v1/auth/session")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


async def test_malformed_login_is_counted_by_durable_limiter(
    client: AsyncClient, db: Session
) -> None:
    for _ in range(5):
        response = await client.post(
            "/api/v1/auth/login",
            content=b'{"username":',
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422
    limited = await client.post(
        "/api/v1/auth/login",
        json={"username": "learner", "password": "correct horse battery staple"},
    )
    assert limited.status_code == 429
    assert db.scalar(select(func.count(AuthRateLimitBucket.id))) == 2


def test_rate_limit_is_atomic_persistent_and_rolls_windows(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'rate-limit.sqlite3'}"
    run_migrations(database_url)
    engine = create_database_engine(database_url)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(security_secret="test-rate-limit-secret")
    rule = RateLimitRule(attempts=3, window_ms=1_000)

    def attempt() -> bool:
        with maker() as session:
            return consume_failure_budget(
                session, settings, "login-client", "198.51.100.8", rule, now_ms=1_234
            )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: attempt(), range(8)))
    assert results.count(True) == 3
    with maker() as restarted_session:
        assert not consume_failure_budget(
            restarted_session,
            settings,
            "login-client",
            "198.51.100.8",
            rule,
            now_ms=1_999,
        )
        assert consume_failure_budget(
            restarted_session,
            settings,
            "login-client",
            "198.51.100.8",
            rule,
            now_ms=2_000,
        )
    engine.dispose()


def test_concurrent_password_changes_have_one_winner(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'credential-race.sqlite3'}"
    run_migrations(database_url)
    engine = create_database_engine(database_url)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    old_hash = hash_password("correct horse battery staple")
    with maker() as session:
        user = User(username="learner", password_hash=old_hash)
        session.add(user)
        session.commit()
        user_id = user.id

    def change(index: int) -> str:
        with maker() as session:
            user = session.get(User, user_id)
            assert user is not None
            try:
                change_password_atomically(
                    session,
                    user,
                    1,
                    old_hash,
                    hash_password(f"replacement password value {index}"),
                )
                session.commit()
                return "changed"
            except AppError as exc:
                session.rollback()
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(change, range(2)))
    assert results.count("changed") == 1
    assert results.count("CREDENTIAL_STATE_CHANGED") == 1
    with maker() as session:
        user = session.get(User, user_id)
        assert user is not None and user.credential_generation == 2
    engine.dispose()


def test_generation_guard_rejects_mutation_after_revoke_all(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'generation-guard.sqlite3'}"
    run_migrations(database_url)
    engine = create_database_engine(database_url)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker() as setup:
        user = User(username="learner", password_hash=hash_password("secure password value"))
        setup.add(user)
        setup.flush()
        auth_session = AuthSession(
            user_id=user.id,
            token_lookup_hash="a" * 64,
            csrf_secret_hash="b" * 64,
            created_at=1,
            last_seen_at=1,
            absolute_expires_at=9_999_999_999_999,
            credential_generation=1,
        )
        setup.add(auth_session)
        setup.commit()
        user_id = user.id
        session_id = auth_session.id
    stale = maker()
    revoker = maker()
    try:
        stale_session = stale.get(AuthSession, session_id)
        current_user = revoker.get(User, user_id)
        assert stale_session is not None and current_user is not None
        arm_generation_guard(stale, stale_session)
        revoke_all_sessions(revoker, current_user, 1)
        revoker.commit()
        stale_user = stale.get(User, user_id)
        assert stale_user is not None
        stale_user.username = "should-not-commit"
        with pytest.raises(AppError, match="session became invalid"):
            stale.commit()
        stale.rollback()
    finally:
        stale.close()
        revoker.close()
        engine.dispose()


def test_offline_recovery_creates_backup_and_revokes_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "recovery.sqlite3"
    backup_path = tmp_path / "backups"
    database_url = f"sqlite:///{database_path}"
    run_migrations(database_url)
    engine = create_database_engine(database_url)
    with Session(engine) as db:
        user = User(username="learner", password_hash="$argon2id$fixture")
        db.add(user)
        db.flush()
        db.add(
            AuthSession(
                user_id=user.id,
                token_lookup_hash="a" * 64,
                csrf_secret_hash="b" * 64,
                created_at=1,
                last_seen_at=1,
                absolute_expires_at=9_999_999_999_999,
                credential_generation=1,
            )
        )
        db.commit()
    engine.dispose()
    monkeypatch.setenv("LCC_DATABASE_URL", database_url)
    monkeypatch.setenv("LCC_BACKUP_DIRECTORY", str(backup_path))
    get_settings.cache_clear()
    monkeypatch.setattr(ops.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(ops.sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr(ops.getpass, "getpass", lambda _prompt: "replacement password value")
    assert ops.recover_password() == 0
    get_settings.cache_clear()
    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("SELECT credential_generation FROM users").fetchone() == (2,)
        assert connection.execute(
            "SELECT revoked_at IS NOT NULL FROM auth_sessions"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT event_type FROM security_audit_events WHERE event_type='password_recovered'"
        ).fetchone() == ("password_recovered",)
    finally:
        connection.close()
    backups = list(backup_path.glob("lcc-pre-recovery-*.sqlite3"))
    assert len(backups) == 1
    backup = sqlite3.connect(backups[0])
    try:
        assert backup.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert backup.execute("SELECT credential_generation FROM users").fetchone() == (1,)
    finally:
        backup.close()
    assert ops.restore_database(backups[0]) == 0
    restored = sqlite3.connect(database_path)
    try:
        assert restored.execute("SELECT credential_generation FROM users").fetchone() == (2,)
        assert restored.execute("SELECT revoked_at IS NOT NULL FROM auth_sessions").fetchone() == (
            1,
        )
        assert restored.execute(
            "SELECT event_type FROM security_audit_events WHERE event_type='database_restored'"
        ).fetchone() == ("database_restored",)
    finally:
        restored.close()
    with exclusive_operation_lock(database_url), pytest.raises(RuntimeError):
        ops.recover_password()
