from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta, timezone

import pytest
from app.config import Settings
from app.time_utils import configure_process_clock, datetime_to_epoch_ms, utc_now_ms
from pydantic import ValidationError


@pytest.fixture(autouse=True)
def restore_system_clock() -> Generator[None, None, None]:
    configure_process_clock(environment="test", fixed_utc_now=None)
    yield
    configure_process_clock(environment="test", fixed_utc_now=None)


def test_process_clock_defaults_to_real_utc() -> None:
    before = datetime_to_epoch_ms(datetime.now(UTC))
    observed = utc_now_ms()
    after = datetime_to_epoch_ms(datetime.now(UTC))

    assert before <= observed <= after


def test_nonproduction_process_clock_uses_explicit_normalized_instant() -> None:
    declared = datetime(2026, 9, 19, 15, 30, 45, 123000, tzinfo=timezone(timedelta(hours=3)))

    configure_process_clock(environment="test", fixed_utc_now=declared)

    assert utc_now_ms() == datetime_to_epoch_ms(declared)


def test_nonproduction_fixture_clock_can_advance_deterministically() -> None:
    declared = datetime(2026, 9, 19, 12, 30, tzinfo=UTC)

    configure_process_clock(environment="test", fixed_utc_now=declared, step_ms=10)

    assert [utc_now_ms(), utc_now_ms(), utc_now_ms()] == [
        datetime_to_epoch_ms(declared),
        datetime_to_epoch_ms(declared) + 10,
        datetime_to_epoch_ms(declared) + 20,
    ]


def test_process_clock_rejects_naive_fixture_instant() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        configure_process_clock(
            environment="test",
            fixed_utc_now=datetime(2026, 9, 19, 12, 30),
        )


def test_process_clock_defensively_rejects_production_override() -> None:
    with pytest.raises(RuntimeError, match="Production cannot use"):
        configure_process_clock(
            environment="production",
            fixed_utc_now=datetime(2026, 9, 19, 12, 30, tzinfo=UTC),
        )


def test_settings_parse_and_normalize_explicit_fixture_clock() -> None:
    configured = Settings(
        environment="test",
        fixture_clock_at="2026-09-19T15:30:45.123+03:00",
    )

    assert configured.fixture_clock_at == datetime(2026, 9, 19, 12, 30, 45, 123000, tzinfo=UTC)


def test_settings_reject_naive_and_production_fixture_clocks() -> None:
    with pytest.raises(ValidationError, match="timezone offset"):
        Settings(environment="test", fixture_clock_at=datetime(2026, 9, 19, 12, 30))

    with pytest.raises(ValidationError, match="Production cannot enable fixture_clock_at"):
        Settings(
            environment="production",
            fixture_clock_at=datetime(2026, 9, 19, 12, 30, tzinfo=UTC),
            database_url="sqlite:////tmp/lcc-clock-production/database.sqlite3",
            backup_directory="/tmp/lcc-clock-backups",
            public_origin="https://lcc.example.test",
            allowed_origins=["https://lcc.example.test"],
            allowed_hosts=["lcc.example.test"],
            security_secret="A1b2C3d4E5f6G7h8J9k0LmnopQrstUVW",
            trusted_proxy_cidrs=["127.0.0.1/32"],
        )

    with pytest.raises(ValidationError, match="requires fixture_clock_at"):
        Settings(environment="test", fixture_clock_step_ms=10)
