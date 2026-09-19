from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from itertools import count
from typing import Literal
from zoneinfo import ZoneInfo

MILLISECONDS_PER_DAY = 86_400_000


def _system_utc_now() -> datetime:
    return datetime.now(UTC)


_process_utc_now: Callable[[], datetime] = _system_utc_now


def configure_process_clock(
    *,
    environment: Literal["development", "test", "production"],
    fixed_utc_now: datetime | None,
    step_ms: int = 0,
) -> None:
    """Compose the process clock without exposing a runtime mutation boundary."""
    if step_ms < 0:
        raise ValueError("The fixture clock step must not be negative.")
    if environment == "production" and (fixed_utc_now is not None or step_ms):
        raise RuntimeError("Production cannot use the fixture clock override.")
    if fixed_utc_now is None and step_ms:
        raise ValueError("The fixture clock step requires a fixed fixture instant.")
    if fixed_utc_now is None:
        clock = _system_utc_now
    else:
        if fixed_utc_now.tzinfo is None or fixed_utc_now.utcoffset() is None:
            raise ValueError("The fixture clock instant must be timezone-aware.")
        normalized = fixed_utc_now.astimezone(UTC)
        ticks = count()

        def clock() -> datetime:
            return normalized + timedelta(milliseconds=next(ticks) * step_ms)

    global _process_utc_now
    _process_utc_now = clock


def utc_now_ms() -> int:
    return int(_process_utc_now().timestamp() * 1000)


def datetime_to_epoch_ms(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An exact instant must be timezone-aware.")
    return int(value.astimezone(UTC).timestamp() * 1000)


def epoch_ms_to_datetime(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def epoch_ms_to_rfc3339(value: int) -> str:
    return epoch_ms_to_datetime(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def local_date_for_ms(value: int, timezone_name: str) -> date:
    return epoch_ms_to_datetime(value).astimezone(ZoneInfo(timezone_name)).date()


def local_day_bounds_ms(local_day: date, timezone_name: str) -> tuple[int, int]:
    timezone = ZoneInfo(timezone_name)
    start = datetime.combine(local_day, datetime.min.time(), tzinfo=timezone)
    end = start + timedelta(days=1)
    return datetime_to_epoch_ms(start), datetime_to_epoch_ms(end)


def completed_local_days(now_ms: int, timezone_name: str, count: int) -> list[date]:
    today = local_date_for_ms(now_ms, timezone_name)
    return [today - timedelta(days=offset) for offset in range(count, 0, -1)]
