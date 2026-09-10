from __future__ import annotations

from typing import Any

from app.capability_scales import BUILTIN_CREATED_AT, builtin_scale_tables
from app.compatibility.v1.profile_competency_backfill import (
    POLICY_KEY,
    RUN_ID,
    build_profile_competency_backfill,
)


class UnsupportedV1PortableSchema(ValueError):
    pass


def read_v1_portable_package(package: dict[str, Any]) -> dict[str, Any]:
    """Validate only the frozen V1 envelope before handing it to the upgrader."""
    if package.get("schemaVersion") != 1:
        raise UnsupportedV1PortableSchema("The V1 reader only accepts schemaVersion 1.")
    if package.get("packageType") not in {"portable_logical_backup", "restore"}:
        raise UnsupportedV1PortableSchema("The package is not a V1 portable backup.")
    payload = package.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("tables"), dict):
        raise UnsupportedV1PortableSchema("The V1 portable payload is malformed.")
    return package


def upgrade_v1_profile_competency_tables(
    tables: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Apply the frozen, lossless V1-to-V2 profile/criterion conversion."""
    for row in tables.get("competency_identities", []):
        row.update(
            {
                "identity_created_at": None,
                "creation_source": None,
                "legacy_unspecified_reason": (
                    "Creation metadata predates the V2 identity contract."
                ),
                "retired_at": None,
                "retirement_reason": None,
            }
        )
    tables.update(builtin_scale_tables())
    for table_name in (
        "target_profiles",
        "target_profile_versions",
        "profile_domains",
        "profile_target_identities",
        "profile_targets",
        "milestone_identities",
        "profile_milestones",
        "profile_milestone_targets",
        "readiness_gates",
        "readiness_gate_identities",
        "readiness_gate_predicates",
        "readiness_gate_targets",
        "active_target_profile_state",
        "target_profile_activation_events",
        "semantic_competency_definitions",
        "semantic_definition_dimensions",
        "criterion_definitions",
        "active_competency_definition_states",
        "competency_definition_activation_events",
    ):
        tables[table_name] = []

    try:
        backfill = build_profile_competency_backfill(tables)
    except (KeyError, TypeError, ValueError) as exc:
        raise UnsupportedV1PortableSchema(str(exc)) from exc
    tables["criterion_identities"] = backfill.criterion_identities
    tables["legacy_criterion_assertions"] = backfill.legacy_assertions
    tables["migration_backfill_runs"] = [
        {
            "id": RUN_ID,
            "policy_key": POLICY_KEY,
            "source_kind": "v1_exit_criteria",
            "source_row_count": backfill.source_row_count,
            "result_row_count": len(backfill.criterion_identities)
            + len(backfill.legacy_assertions),
            "source_hash": backfill.source_hash,
            "result_hash": backfill.result_hash,
            "recorded_at": BUILTIN_CREATED_AT,
        }
    ]
    return {
        "competencyIdentitiesWithLegacyCreationUnknown": len(
            tables.get("competency_identities", [])
        ),
        "legacyCriterionAssertionsCreated": len(backfill.legacy_assertions),
        "legacyCriterionSourceRowCount": backfill.source_row_count,
        "legacyCriterionSourceHash": backfill.source_hash,
        "legacyCriterionResultHash": backfill.result_hash,
        "nativeSemanticDefinitionsInferred": 0,
        "targetProfilesInferred": 0,
    }
