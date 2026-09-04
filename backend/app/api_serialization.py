from __future__ import annotations

from typing import Any, cast

from app.time_utils import epoch_ms_to_rfc3339

INSTANT_KEYS = {
    "absoluteExpiresAt",
    "activeSince",
    "appliedAt",
    "createdAt",
    "endedAt",
    "generatedAt",
    "lastPassedVerificationAt",
    "lastSuccessfulEvidenceAt",
    "latestBlockedSessionAt",
    "latestLaterSuccessfulPracticalAt",
    "startedAt",
    "updatedAt",
}


def _serialize_api_instants(value: Any) -> Any:
    if isinstance(value, list):
        return [_serialize_api_instants(item) for item in value]
    if isinstance(value, dict):
        return {
            key: (
                epoch_ms_to_rfc3339(item)
                if key in INSTANT_KEYS and isinstance(item, int)
                else _serialize_api_instants(item)
            )
            for key, item in value.items()
        }
    return value


def serialize_api_instants[T](value: T) -> T:
    """Return an API-safe copy with known instant fields rendered as RFC 3339 UTC."""
    return cast(T, _serialize_api_instants(value))
