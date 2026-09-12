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
        "activity_category_versions",
        "activities",
        "session_contributions",
        "contribution_retractions",
        "session_corrections",
        "evidence",
        "evidence_links",
        "evidence_retractions",
        "evidence_invalidations",
        "evidence_link_retractions",
        "evidence_redactions",
        "capability_evaluation_runs",
        "criterion_evaluation_results",
        "capability_state_events",
        "review_events",
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
        "activities",
        "session_contributions",
        "evidence",
        "evidence_links",
    ],
    "includedImmutableHistory": [
        "analysis_runs",
        "analysis_snapshots",
        "target_profile_activation_events",
        "competency_definition_activation_events",
        "migration_backfill_runs",
        "contribution_retractions",
        "session_corrections",
        "evidence_retractions",
        "evidence_invalidations",
        "evidence_link_retractions",
        "evidence_redactions",
        "capability_evaluation_runs",
        "criterion_evaluation_results",
        "capability_state_events",
        "review_events",
    ],
    "omittedRebuildableState": [
        "competency_capability_states",
        "competency_review_states",
        "projection_invalidations",
    ],
    "restoreActions": [
        "clear_projection_invalidations",
        "rebuild_capability_states",
        "rebuild_review_states",
        "verify_projection_hash_parity",
    ],
}


def supports_portable_schema(version: int) -> bool:
    return version in PORTABLE_SCHEMA_READABLE
