from __future__ import annotations

PORTABLE_SCHEMA_CURRENT = 2
PORTABLE_SCHEMA_READABLE = frozenset({1, 2})
PORTABLE_V2_FOUNDATION_TABLES = frozenset(
    {
        "analysis_runs",
        "analysis_snapshots",
        "capability_scale_versions",
        "capability_scale_dimensions",
        "capability_scale_levels",
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
        "criterion_identities",
        "criterion_definitions",
        "active_competency_definition_states",
        "competency_definition_activation_events",
        "legacy_criterion_assertions",
        "migration_backfill_runs",
    }
)
PORTABLE_V1_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V2_FOUNDATION_TABLES) | {"projection_invalidations"}
)
PORTABLE_V2_MANIFEST = {
    "includedCanonicalDomains": [
        "learning_state",
        "target_profiles",
        "semantic_competency_definitions",
        "capability_scales",
        "legacy_criterion_assertions",
    ],
    "includedImmutableHistory": [
        "analysis_runs",
        "analysis_snapshots",
        "target_profile_activation_events",
        "competency_definition_activation_events",
        "migration_backfill_runs",
    ],
    "omittedRebuildableState": ["projection_invalidations"],
    "restoreActions": ["clear_projection_invalidations"],
}


def supports_portable_schema(version: int) -> bool:
    return version in PORTABLE_SCHEMA_READABLE
