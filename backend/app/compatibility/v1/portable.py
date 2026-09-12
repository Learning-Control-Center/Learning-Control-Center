from __future__ import annotations

from typing import Any

from app.capability_scales import BUILTIN_CREATED_AT, builtin_scale_tables
from app.compatibility.v1.activity_backfill import (
    POLICY_KEY as ACTIVITY_POLICY_KEY,
)
from app.compatibility.v1.activity_backfill import (
    RUN_ID as ACTIVITY_RUN_ID,
)
from app.compatibility.v1.activity_backfill import (
    activity_category_rows,
    build_activity_session_backfill,
)
from app.compatibility.v1.evidence_backfill import (
    POLICY_KEY as EVIDENCE_POLICY_KEY,
)
from app.compatibility.v1.evidence_backfill import (
    RECORDED_AT as EVIDENCE_RECORDED_AT,
)
from app.compatibility.v1.evidence_backfill import (
    RUN_ID as EVIDENCE_RUN_ID,
)
from app.compatibility.v1.evidence_backfill import build_evidence_backfill
from app.compatibility.v1.profile_competency_backfill import (
    POLICY_KEY,
    RUN_ID,
    build_profile_competency_backfill,
)


class UnsupportedV1PortableSchema(ValueError):
    pass


def upgrade_v1_evidence_tables(
    tables: dict[str, list[dict[str, Any]]], *, import_package_id: str
) -> dict[str, Any]:
    try:
        backfill = build_evidence_backfill(tables, import_package_id=import_package_id)
    except (KeyError, TypeError, ValueError) as exc:
        raise UnsupportedV1PortableSchema(str(exc)) from exc
    tables["evidence"] = backfill.evidence
    tables["evidence_links"] = backfill.links
    tables["evidence_retractions"] = []
    tables["evidence_invalidations"] = []
    tables["evidence_link_retractions"] = []
    tables["evidence_redactions"] = []
    runs = tables.setdefault("migration_backfill_runs", [])
    runs[:] = [row for row in runs if row.get("id") != EVIDENCE_RUN_ID]
    runs.append(
        {
            "id": EVIDENCE_RUN_ID,
            "policy_key": EVIDENCE_POLICY_KEY,
            "source_kind": "v1_evidence_sources",
            "source_row_count": backfill.source_row_count,
            "result_row_count": len(backfill.evidence) + len(backfill.links),
            "source_hash": backfill.source_hash,
            "result_hash": backfill.result_hash,
            "recorded_at": EVIDENCE_RECORDED_AT,
        }
    )
    return {
        "legacyEvidenceCreated": len(backfill.evidence),
        "legacyEvidenceLinksCreated": len(backfill.links),
        "legacyEvidenceSourceRowCount": backfill.source_row_count,
        "legacyEvidenceSourceHash": backfill.source_hash,
        "legacyEvidenceResultHash": backfill.result_hash,
    }


def upgrade_v1_activity_session_tables(tables: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    try:
        backfill = build_activity_session_backfill(tables.get("learning_sessions", []))
    except (KeyError, TypeError, ValueError) as exc:
        raise UnsupportedV1PortableSchema(str(exc)) from exc
    tables["activity_category_versions"] = activity_category_rows()
    tables["activities"] = backfill.activities
    tables["session_contributions"] = backfill.contributions
    tables["contribution_retractions"] = []
    tables["session_corrections"] = []
    for session in tables.get("learning_sessions", []):
        session["activity_id"] = backfill.session_activity_ids[str(session["id"])]
        session["tombstoned_at"] = None
        session["tombstone_reason"] = None
    runs = tables.setdefault("migration_backfill_runs", [])
    runs[:] = [row for row in runs if row.get("id") != ACTIVITY_RUN_ID]
    runs.append(
        {
            "id": ACTIVITY_RUN_ID,
            "policy_key": ACTIVITY_POLICY_KEY,
            "source_kind": "v1_learning_sessions",
            "source_row_count": len(tables.get("learning_sessions", [])),
            "result_row_count": len(backfill.activities) + len(backfill.contributions),
            "source_hash": backfill.source_hash,
            "result_hash": backfill.result_hash,
            "recorded_at": BUILTIN_CREATED_AT,
        }
    )
    return {
        "legacyActivitiesCreated": len(backfill.activities),
        "legacySessionContributionsCreated": len(backfill.contributions),
        "legacySessionSourceHash": backfill.source_hash,
        "legacySessionResultHash": backfill.result_hash,
    }


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
