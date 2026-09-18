from __future__ import annotations

import hashlib
import json

PORTABLE_SCHEMA_CURRENT = 5
PORTABLE_SCHEMA_READABLE = frozenset({1, 2, 3, 4, 5})
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

PORTABLE_V3_CURRICULUM_TABLES = frozenset(
    {
        "curricula",
        "curriculum_versions",
        "curriculum_objective_identities",
        "curriculum_objective_definitions",
        "learning_unit_identities",
        "learning_unit_definitions",
        "learning_unit_targets",
        "learning_unit_requirements",
        "curriculum_evidence_opportunities",
        "assessment_rubric_identities",
        "assessment_rubric_definitions",
        "active_curriculum_version_states",
        "curriculum_activation_events",
        "activity_curriculum_unit_links",
        "activity_curriculum_link_corrections",
    }
)
PORTABLE_V1_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V1_FORBIDDEN_TABLES) | set(PORTABLE_V3_CURRICULUM_TABLES)
)
PORTABLE_V2_FORBIDDEN_TABLES = PORTABLE_V3_CURRICULUM_TABLES
PORTABLE_V3_MANIFEST = {
    "includedCanonicalDomains": [
        *PORTABLE_V2_MANIFEST["includedCanonicalDomains"],
        "curriculum",
        "curriculum_activity_links",
    ],
    "includedImmutableHistory": [
        *PORTABLE_V2_MANIFEST["includedImmutableHistory"],
        "curriculum_versions",
        "curriculum_activation_events",
        "activity_curriculum_link_corrections",
    ],
    "omittedRebuildableState": [
        *PORTABLE_V2_MANIFEST["omittedRebuildableState"],
        "curriculum_availability",
    ],
    "restoreActions": [
        *PORTABLE_V2_MANIFEST["restoreActions"],
        "rebuild_curriculum_availability",
        "verify_curriculum_catalog_hash_parity",
    ],
}

PORTABLE_V4_PROJECT_TABLES = frozenset(
    {
        "projects",
        "project_versions",
        "project_goal_identities",
        "project_goal_definitions",
        "project_task_identities",
        "project_task_definitions",
        "project_criterion_identities",
        "project_criterion_definitions",
        "project_milestone_identities",
        "project_milestone_definitions",
        "project_targets",
        "project_requirements",
        "project_task_dependencies",
        "project_evidence_opportunities",
        "active_project_version_states",
        "project_version_activation_events",
        "project_events",
        "activity_project_task_links",
        "activity_project_task_link_corrections",
        "session_project_contributions",
        "session_project_contribution_retractions",
        "project_criterion_evaluations",
        "project_criterion_evaluation_evidence",
    }
)
PORTABLE_V1_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V1_FORBIDDEN_TABLES) | set(PORTABLE_V4_PROJECT_TABLES)
)
PORTABLE_V2_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V2_FORBIDDEN_TABLES) | set(PORTABLE_V4_PROJECT_TABLES)
)
PORTABLE_V3_FORBIDDEN_TABLES = PORTABLE_V4_PROJECT_TABLES
PORTABLE_V4_MANIFEST = {
    "includedCanonicalDomains": [
        *PORTABLE_V3_MANIFEST["includedCanonicalDomains"],
        "projects",
        "project_activity_and_session_attribution",
        "project_evidence_evaluations",
    ],
    "includedImmutableHistory": [
        *PORTABLE_V3_MANIFEST["includedImmutableHistory"],
        "project_versions",
        "project_version_activation_events",
        "project_events",
        "activity_project_task_link_corrections",
        "session_project_contribution_retractions",
        "project_criterion_evaluations",
    ],
    "omittedRebuildableState": [
        *PORTABLE_V3_MANIFEST["omittedRebuildableState"],
        "project_current_lifecycle",
        "project_task_availability",
    ],
    "restoreActions": [
        *PORTABLE_V3_MANIFEST["restoreActions"],
        "rebuild_project_current_state",
        "rebuild_project_task_availability",
        "verify_project_catalog_hash_parity",
    ],
}

PORTABLE_V5_GRAPH_PROJECTION_TABLES = frozenset(
    {
        "learning_graphs",
        "learning_graph_versions",
        "competency_edge_identities",
        "competency_edge_definitions",
        "active_learning_graph_states",
        "learning_graph_activation_events",
        "legacy_roadmap_active_states",
        "roadmap_node_position_overrides",
        "roadmap_projection_preferences",
    }
)
PORTABLE_V1_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V1_FORBIDDEN_TABLES) | set(PORTABLE_V5_GRAPH_PROJECTION_TABLES)
)
PORTABLE_V2_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V2_FORBIDDEN_TABLES) | set(PORTABLE_V5_GRAPH_PROJECTION_TABLES)
)
PORTABLE_V3_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V3_FORBIDDEN_TABLES) | set(PORTABLE_V5_GRAPH_PROJECTION_TABLES)
)
PORTABLE_V4_FORBIDDEN_TABLES = PORTABLE_V5_GRAPH_PROJECTION_TABLES
PORTABLE_V5_MANIFEST = {
    "includedCanonicalDomains": [
        *PORTABLE_V4_MANIFEST["includedCanonicalDomains"],
        "learning_graph",
        "roadmap_projection_manual_input",
        "legacy_roadmap_active_state_compatibility",
    ],
    "includedImmutableHistory": [
        *PORTABLE_V4_MANIFEST["includedImmutableHistory"],
        "learning_graph_versions",
        "learning_graph_activation_events",
    ],
    "omittedRebuildableState": [
        *PORTABLE_V4_MANIFEST["omittedRebuildableState"],
        "learning_graph_edge_satisfaction",
        "roadmap_projection_cache",
        "roadmap_projection_checkpoint",
    ],
    "restoreActions": [
        *PORTABLE_V4_MANIFEST["restoreActions"],
        "rebuild_learning_graph_satisfaction",
        "rebuild_roadmap_projection",
        "verify_roadmap_projection_hash_parity",
        "verify_legacy_roadmap_active_state_parity",
    ],
}


def upgrade_v2_to_v3_tables(tables: dict[str, list[dict[str, object]]]) -> dict[str, int]:
    """Apply the lossless v2-to-v3 empty Curriculum-domain adapter in place."""
    created = 0
    for table_name in sorted(PORTABLE_V3_CURRICULUM_TABLES):
        if table_name not in tables:
            tables[table_name] = []
            created += 1
    return {"initializedCurriculumTables": created}


def upgrade_v3_to_v4_tables(tables: dict[str, list[dict[str, object]]]) -> dict[str, int]:
    """Apply the lossless v3-to-v4 empty Project-domain adapter in place."""
    created = 0
    for table_name in sorted(PORTABLE_V4_PROJECT_TABLES):
        if table_name not in tables:
            tables[table_name] = []
            created += 1
    return {"initializedProjectTables": created}


def upgrade_v4_to_v5_tables(tables: dict[str, list[dict[str, object]]]) -> dict[str, int]:
    """Apply the lossless v4-to-v5 empty native Graph/Projection adapter in place."""
    created = 0
    for table_name in sorted(PORTABLE_V5_GRAPH_PROJECTION_TABLES):
        if table_name not in tables:
            if table_name == "legacy_roadmap_active_states":
                states: list[dict[str, object]] = []
                for roadmap in sorted(tables.get("roadmaps", []), key=lambda row: str(row["id"])):
                    hash_payload = {
                        "activeVersionId": roadmap.get("active_version_id"),
                        "currentPhaseId": roadmap.get("current_phase_id"),
                        "isCurrent": bool(roadmap.get("is_current")),
                        "roadmapId": roadmap["id"],
                    }
                    states.append(
                        {
                            "roadmap_id": roadmap["id"],
                            "active_version_id": roadmap.get("active_version_id"),
                            "current_phase_id": roadmap.get("current_phase_id"),
                            "is_current": bool(roadmap.get("is_current")),
                            "state_hash": hashlib.sha256(
                                json.dumps(
                                    hash_payload, sort_keys=True, separators=(",", ":")
                                ).encode("utf-8")
                            ).hexdigest(),
                            "updated_at": roadmap["updated_at"],
                        }
                    )
                tables[table_name] = states
            else:
                tables[table_name] = []
            created += 1
    return {"initializedLearningGraphProjectionTables": created}


def supports_portable_schema(version: int) -> bool:
    return version in PORTABLE_SCHEMA_READABLE
