from __future__ import annotations

from collections.abc import Mapping
from typing import Any

LEGACY_BASELINE: dict[str, str] = {
    "canonicalLearningAuthority": "legacy_v1",
    "recommendationPresentation": "legacy_v1",
    "roadmapPresentation": "legacy_v1",
    "todayPresentation": "legacy_v1",
}

V2_DEFAULT: dict[str, str] = {
    "canonicalLearningAuthority": "v2",
    "recommendationPresentation": "v2",
    "roadmapPresentation": "v2",
    "todayPresentation": "v2",
}


def is_valid_state_payload(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) != set(LEGACY_BASELINE):
        return False
    if value.get("canonicalLearningAuthority") == "legacy_v1":
        return dict(value) == LEGACY_BASELINE
    return (
        value.get("canonicalLearningAuthority") == "v2"
        and value.get("recommendationPresentation") in {"v2", "v1_read_only"}
        and value.get("roadmapPresentation") in {"v2", "v1_read_only"}
        and value.get("todayPresentation") in {"v2", "v1_read_only"}
    )


def is_valid_transition(
    *,
    sequence: int,
    command_type: str,
    prior: object,
    resulting: object,
    actor: str,
    source: str,
) -> bool:
    if not is_valid_state_payload(resulting):
        return False
    resulting_state = dict(resulting) if isinstance(resulting, Mapping) else {}
    if sequence == 1:
        return (
            command_type == "bootstrap"
            and prior is None
            and resulting_state == LEGACY_BASELINE
            and actor == "system"
            and source == "migration"
        )
    if not is_valid_state_payload(prior) or not isinstance(prior, Mapping):
        return False
    prior_state: dict[str, Any] = dict(prior)
    if command_type == "activate_v2":
        return prior_state == LEGACY_BASELINE and resulting_state == V2_DEFAULT
    if command_type == "surface_change":
        return (
            prior_state.get("canonicalLearningAuthority") == "v2"
            and resulting_state.get("canonicalLearningAuthority") == "v2"
        )
    return False
