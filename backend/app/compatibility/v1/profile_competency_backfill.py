from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.capability_scales import BUILTIN_CREATED_AT, stable_v2_id

POLICY_KEY = "legacy-backfill-policy/v1"
RUN_ID = stable_v2_id("backfill-run:profile-competency:legacy-backfill-policy/v1")


@dataclass(frozen=True)
class ProfileCompetencyBackfill:
    criterion_identities: list[dict[str, Any]]
    legacy_assertions: list[dict[str, Any]]
    source_row_count: int
    source_hash: str
    result_hash: str


def canonical_rows_hash(rows: list[dict[str, Any]]) -> str:
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def build_profile_competency_backfill(
    tables: dict[str, list[dict[str, Any]]],
) -> ProfileCompetencyBackfill:
    """Frozen V1 criterion conversion shared by migration and portable upgrade."""
    roadmap_by_id = {row["id"]: row for row in tables.get("roadmaps", [])}
    roadmap_id_by_version = {
        row["id"]: row["roadmap_id"] for row in tables.get("roadmap_versions", [])
    }
    competency_definition_by_id = {
        row["id"]: row for row in tables.get("competency_definitions", [])
    }
    source_definitions: dict[str, list[dict[str, Any]]] = {}
    for row in tables.get("exit_criterion_definitions", []):
        source_definitions.setdefault(row["exit_criterion_identity_id"], []).append(row)

    criterion_rows: list[dict[str, Any]] = []
    assertion_rows: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    identities = sorted(tables.get("exit_criterion_identities", []), key=lambda row: row["id"])
    for identity in identities:
        candidates = source_definitions.get(identity["id"], [])
        if not candidates:
            raise ValueError("Every V1 exit criterion must have a source definition.")

        def source_order(row: dict[str, Any]) -> tuple[int, int, str]:
            definition = competency_definition_by_id[row["competency_definition_id"]]
            roadmap = roadmap_by_id[roadmap_id_by_version[definition["roadmap_version_id"]]]
            current = bool(
                roadmap["is_current"]
                and roadmap["active_version_id"] == definition["roadmap_version_id"]
            )
            return (1 if current else 0, int(row["created_at"]), str(row["id"]))

        source = max(candidates, key=source_order)
        source_rows.append(
            {
                "identity_id": identity["id"],
                "competency_identity_id": identity["competency_identity_id"],
                "stable_key": identity["stable_key"],
                "current_state": identity["current_state"],
                "updated_at": int(identity["updated_at"]),
                "source_definition_id": source["id"],
                "source_definition_created_at": int(source["created_at"]),
                "required": bool(source["required"]),
            }
        )
        criterion_rows.append(
            {
                "id": identity["id"],
                "competency_identity_id": identity["competency_identity_id"],
                "stable_key": identity["stable_key"],
                "created_at": None,
                "creation_source": None,
                "legacy_unspecified_reason": (
                    "Creation metadata predates the V2 criterion contract."
                ),
                "retired_at": None,
                "retirement_reason": None,
            }
        )
        assertion_rows.append(
            {
                "id": stable_v2_id(f"legacy-assertion:{identity['id']}:{source['id']}"),
                "criterion_identity_id": identity["id"],
                "source_exit_criterion_identity_id": identity["id"],
                "source_exit_criterion_definition_id": source["id"],
                "legacy_state": identity["current_state"],
                "requirement_type": "required" if source["required"] else None,
                "demonstration_rule": None,
                "evidence_strength": None,
                "independence": None,
                "source_confidence": None,
                "legacy_unspecified_reason": (
                    "V1 checkbox state does not specify V2 demonstration, evidence quality, "
                    "independence, or source confidence."
                ),
                "asserted_at": int(identity["updated_at"]),
                "cutoff_at": max(BUILTIN_CREATED_AT, int(identity["updated_at"]) + 1),
                "provenance": POLICY_KEY,
            }
        )

    result_rows = sorted(criterion_rows + assertion_rows, key=lambda row: (row["id"], len(row)))
    return ProfileCompetencyBackfill(
        criterion_identities=criterion_rows,
        legacy_assertions=assertion_rows,
        source_row_count=len(source_rows),
        source_hash=canonical_rows_hash(source_rows),
        result_hash=canonical_rows_hash(result_rows),
    )
