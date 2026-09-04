from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

MILLISECONDS_PER_DAY = 86_400_000


def utc_now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


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
