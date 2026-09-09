from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


def immutable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): immutable(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(immutable(item) for item in value)
    return value


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AnalysisEnvelope:
    purpose: str
    schema_version: int
    generated_at: int
    cutoff_at: int
    cutoff_semantics: str
    timezone: str
    completed_through_date: str
    target_profile_id: str | None
    target_profile_version_id: str | None
    capability_scale_version_references: tuple[str, ...]
    learning_graph_reference: str | None
    curriculum_reference: str | None
    semantic_definition_references: tuple[str, ...]
    policy_versions: Mapping[str, str | None]
    discipline_configuration_reference: str
    configuration_hash: str
    application_version: str
    input_lineage: tuple[Mapping[str, Any], ...]
    input_hash: str
    normalized_facts: Mapping[str, Any]
    signals: tuple[Mapping[str, Any], ...]
    completeness: str
    unknown_markers: tuple[Mapping[str, str], ...]
    output_hash: str
