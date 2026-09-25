from __future__ import annotations

import hashlib
import json

PORTABLE_SCHEMA_CURRENT = 11
PORTABLE_SCHEMA_READABLE = frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11})
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

PORTABLE_V6_ANALYSIS_TABLES = frozenset(
    {
        "discipline_configuration_events",
        "analysis_v3_run_lineages",
        "analysis_v3_snapshot_details",
        "analysis_v3_normalized_facts",
        "analysis_v3_competency_gaps",
        "analysis_v3_signals",
        "analysis_v3_unknown_markers",
    }
)
PORTABLE_V1_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V1_FORBIDDEN_TABLES) | set(PORTABLE_V6_ANALYSIS_TABLES)
)
PORTABLE_V2_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V2_FORBIDDEN_TABLES) | set(PORTABLE_V6_ANALYSIS_TABLES)
)
PORTABLE_V3_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V3_FORBIDDEN_TABLES) | set(PORTABLE_V6_ANALYSIS_TABLES)
)
PORTABLE_V4_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V4_FORBIDDEN_TABLES) | set(PORTABLE_V6_ANALYSIS_TABLES)
)
PORTABLE_V5_FORBIDDEN_TABLES = PORTABLE_V6_ANALYSIS_TABLES
PORTABLE_V6_MANIFEST = {
    "includedCanonicalDomains": [
        *PORTABLE_V5_MANIFEST["includedCanonicalDomains"],
        "discipline_configuration_history",
        "analysis_v3_diagnostics",
    ],
    "includedImmutableHistory": [
        *PORTABLE_V5_MANIFEST["includedImmutableHistory"],
        "discipline_configuration_events",
        "analysis_v3_runs_snapshots_facts_gaps_signals_unknowns",
    ],
    "omittedRebuildableState": [
        *PORTABLE_V5_MANIFEST["omittedRebuildableState"],
        "analysis_v3_current_state",
    ],
    "restoreActions": [
        *PORTABLE_V5_MANIFEST["restoreActions"],
        "rebuild_analysis_v3_current_pointer",
        "verify_analysis_v3_history_hash_parity",
    ],
}

PORTABLE_V7_RECOMMENDATION_TABLES = frozenset(
    {
        "recommendation_v2_runs",
        "recommendation_v2_candidates",
        "recommendation_v2_eligibility_decisions",
        "recommendation_v2_eligibility_rule_results",
        "recommendation_v2_expected_values",
        "recommendation_v2_score_components",
        "recommendation_v2_selection_decisions",
        "recommendation_v2_recommendations",
        "recommendation_v2_reasons",
    }
)
PORTABLE_V1_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V1_FORBIDDEN_TABLES) | set(PORTABLE_V7_RECOMMENDATION_TABLES)
)
PORTABLE_V2_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V2_FORBIDDEN_TABLES) | set(PORTABLE_V7_RECOMMENDATION_TABLES)
)
PORTABLE_V3_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V3_FORBIDDEN_TABLES) | set(PORTABLE_V7_RECOMMENDATION_TABLES)
)
PORTABLE_V4_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V4_FORBIDDEN_TABLES) | set(PORTABLE_V7_RECOMMENDATION_TABLES)
)
PORTABLE_V5_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V5_FORBIDDEN_TABLES) | set(PORTABLE_V7_RECOMMENDATION_TABLES)
)
PORTABLE_V6_FORBIDDEN_TABLES = PORTABLE_V7_RECOMMENDATION_TABLES
PORTABLE_V7_MANIFEST = {
    "includedCanonicalDomains": [
        *PORTABLE_V6_MANIFEST["includedCanonicalDomains"],
        "recommendation_v2_decision_history",
    ],
    "includedImmutableHistory": [
        *PORTABLE_V6_MANIFEST["includedImmutableHistory"],
        "recommendation_v2_runs_candidates_eligibility_scores_decisions_reasons",
    ],
    "omittedRebuildableState": [*PORTABLE_V6_MANIFEST["omittedRebuildableState"]],
    "restoreActions": [
        *PORTABLE_V6_MANIFEST["restoreActions"],
        "verify_recommendation_v2_history_hash_parity",
    ],
}

PORTABLE_V8_TODAY_TABLES = frozenset(
    {
        "today_generations",
        "today_suggestions",
        "today_interactions",
        "today_interaction_corrections",
        "suggestion_activity_relations",
        "suggestion_activity_relation_corrections",
    }
)
PORTABLE_V1_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V1_FORBIDDEN_TABLES) | set(PORTABLE_V8_TODAY_TABLES)
)
PORTABLE_V2_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V2_FORBIDDEN_TABLES) | set(PORTABLE_V8_TODAY_TABLES)
)
PORTABLE_V3_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V3_FORBIDDEN_TABLES) | set(PORTABLE_V8_TODAY_TABLES)
)
PORTABLE_V4_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V4_FORBIDDEN_TABLES) | set(PORTABLE_V8_TODAY_TABLES)
)
PORTABLE_V5_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V5_FORBIDDEN_TABLES) | set(PORTABLE_V8_TODAY_TABLES)
)
PORTABLE_V6_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V6_FORBIDDEN_TABLES) | set(PORTABLE_V8_TODAY_TABLES)
)
PORTABLE_V7_FORBIDDEN_TABLES = PORTABLE_V8_TODAY_TABLES
PORTABLE_V8_MANIFEST = {
    "includedCanonicalDomains": [
        *PORTABLE_V7_MANIFEST["includedCanonicalDomains"],
        "today_v2_advisory_history",
        "suggestion_actual_activity_relations",
    ],
    "includedImmutableHistory": [
        *PORTABLE_V7_MANIFEST["includedImmutableHistory"],
        "today_v2_generations_suggestions_interactions_relations_corrections",
    ],
    "omittedRebuildableState": [
        *PORTABLE_V7_MANIFEST["omittedRebuildableState"],
        "today_suggestion_current_states",
    ],
    "restoreActions": [
        *PORTABLE_V7_MANIFEST["restoreActions"],
        "rebuild_today_suggestion_current_states",
        "verify_today_v2_history_and_current_state_parity",
    ],
}

PORTABLE_V9_AUTHORITY_TABLES = frozenset(
    {"learning_control_authority_state", "learning_control_authority_events"}
)
PORTABLE_V1_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V1_FORBIDDEN_TABLES) | set(PORTABLE_V9_AUTHORITY_TABLES)
)
PORTABLE_V2_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V2_FORBIDDEN_TABLES) | set(PORTABLE_V9_AUTHORITY_TABLES)
)
PORTABLE_V3_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V3_FORBIDDEN_TABLES) | set(PORTABLE_V9_AUTHORITY_TABLES)
)
PORTABLE_V4_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V4_FORBIDDEN_TABLES) | set(PORTABLE_V9_AUTHORITY_TABLES)
)
PORTABLE_V5_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V5_FORBIDDEN_TABLES) | set(PORTABLE_V9_AUTHORITY_TABLES)
)
PORTABLE_V6_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V6_FORBIDDEN_TABLES) | set(PORTABLE_V9_AUTHORITY_TABLES)
)
PORTABLE_V7_FORBIDDEN_TABLES = frozenset(
    set(PORTABLE_V7_FORBIDDEN_TABLES) | set(PORTABLE_V9_AUTHORITY_TABLES)
)
PORTABLE_V8_FORBIDDEN_TABLES = PORTABLE_V9_AUTHORITY_TABLES
PORTABLE_V9_MANIFEST = {
    "includedCanonicalDomains": [
        *PORTABLE_V8_MANIFEST["includedCanonicalDomains"],
        "learning_control_authority_state",
    ],
    "includedImmutableHistory": [
        *PORTABLE_V8_MANIFEST["includedImmutableHistory"],
        "learning_control_authority_events",
    ],
    "omittedRebuildableState": [*PORTABLE_V8_MANIFEST["omittedRebuildableState"]],
    "restoreActions": [
        *PORTABLE_V8_MANIFEST["restoreActions"],
        "verify_monotonic_learning_control_authority_history",
    ],
}

PORTABLE_V10_MASTER_IMPORT_TABLES = frozenset(
    {"master_import_revisions", "master_import_owned_keys"}
)
PORTABLE_V10_MANIFEST = {
    "includedCanonicalDomains": [
        *PORTABLE_V9_MANIFEST["includedCanonicalDomains"],
        "master_import_owned_keys",
    ],
    "includedImmutableHistory": [
        *PORTABLE_V9_MANIFEST["includedImmutableHistory"],
        "master_import_revisions",
    ],
    "omittedRebuildableState": [*PORTABLE_V9_MANIFEST["omittedRebuildableState"]],
    "restoreActions": [
        *PORTABLE_V9_MANIFEST["restoreActions"],
        "validate_master_import_ledger",
    ],
}

PORTABLE_V11_ASSESSMENT_TABLES = frozenset(
    {"assessment_executions", "assessment_artifacts", "assessment_reviews"}
)
PORTABLE_V11_MANIFEST = {
    "includedCanonicalDomains": [
        *PORTABLE_V10_MANIFEST["includedCanonicalDomains"],
        "assessment_executions",
    ],
    "includedImmutableHistory": [
        *PORTABLE_V10_MANIFEST["includedImmutableHistory"],
        "assessment_artifacts",
        "assessment_reviews",
    ],
    "omittedRebuildableState": [*PORTABLE_V10_MANIFEST["omittedRebuildableState"]],
    "restoreActions": [
        *PORTABLE_V10_MANIFEST["restoreActions"],
        "validate_assessment_execution_lineage",
    ],
}


def upgrade_v10_to_v11_tables(tables: dict[str, list[dict[str, object]]]) -> dict[str, int]:
    """Older packages predate canonical assessment execution history."""
    if set(tables) & PORTABLE_V11_ASSESSMENT_TABLES:
        raise ValueError("Portable V10 cannot contain assessment execution history.")
    for table_name in PORTABLE_V11_ASSESSMENT_TABLES:
        tables[table_name] = []
    return {"initializedAssessmentTables": len(PORTABLE_V11_ASSESSMENT_TABLES)}


def upgrade_v9_to_v10_tables(tables: dict[str, list[dict[str, object]]]) -> dict[str, int]:
    """V9 predates Master Import; preserve that fact as an empty ledger."""
    if set(tables) & PORTABLE_V10_MASTER_IMPORT_TABLES:
        raise ValueError("Portable V9 cannot contain Master Import ledger rows.")
    for table_name in PORTABLE_V10_MASTER_IMPORT_TABLES:
        tables[table_name] = []
    return {"initializedMasterImportTables": len(PORTABLE_V10_MASTER_IMPORT_TABLES)}


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


def upgrade_v5_to_v6_tables(tables: dict[str, list[dict[str, object]]]) -> dict[str, int]:
    """Add empty V3 history plus an exact compatibility configuration baseline."""
    created = 0
    for table_name in sorted(PORTABLE_V6_ANALYSIS_TABLES):
        if table_name not in tables:
            if table_name == "discipline_configuration_events":
                profiles = tables.get("discipline_profiles", [])
                rows: list[dict[str, object]] = []
                if profiles:
                    profile = profiles[0]
                    adaptation = json.loads(str(profile.get("adaptation_phase_config_json", "{}")))
                    payload = {
                        "adaptationPhaseConfig": adaptation,
                        "targetDurationMsPerActiveDay": profile.get(
                            "target_duration_ms_per_active_day"
                        ),
                        "timezone": profile.get("timezone"),
                        "weeklyTargetActiveDays": profile.get("weekly_target_active_days"),
                    }
                    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
                    rows.append(
                        {
                            "id": "portable-v5-discipline-baseline",
                            "event_sequence": 1,
                            "idempotency_key": "portable-v5-discipline-baseline",
                            "configuration_json": encoded,
                            "configuration_hash": hashlib.sha256(
                                encoded.encode("utf-8")
                            ).hexdigest(),
                            "recorded_at": profile.get("updated_at"),
                            "source": "portable_v5_compatibility_baseline",
                        }
                    )
                tables[table_name] = rows
            else:
                tables[table_name] = []
            created += 1
    return {"initializedAnalysisV3Tables": created}


def upgrade_v6_to_v7_tables(tables: dict[str, list[dict[str, object]]]) -> dict[str, int]:
    """Add empty Recommendation V2 history without inventing V1/V2 decisions."""
    created = 0
    for table_name in sorted(PORTABLE_V7_RECOMMENDATION_TABLES):
        if table_name not in tables:
            tables[table_name] = []
            created += 1
    return {"initializedRecommendationV2Tables": created}


def upgrade_v7_to_v8_tables(tables: dict[str, list[dict[str, object]]]) -> dict[str, int]:
    """Add empty Today V2 history without converting ambiguous V1 decisions."""
    created = 0
    for table_name in sorted(PORTABLE_V8_TODAY_TABLES):
        if table_name not in tables:
            tables[table_name] = []
            created += 1
    return {"initializedTodayV2Tables": created}


def upgrade_v8_to_v9_tables(tables: dict[str, list[dict[str, object]]]) -> dict[str, int]:
    """Add the legacy authority baseline and contract Roadmap pointer columns."""
    roadmaps = tables.get("roadmaps", [])
    pointer_columns = {"is_current", "active_version_id", "current_phase_id"}
    if roadmaps:
        if any(not pointer_columns <= set(roadmap) for roadmap in roadmaps):
            raise ValueError("Portable V8 Roadmap pointers are partially represented.")
        states = {
            str(row.get("roadmap_id")): row
            for row in tables.get("legacy_roadmap_active_states", [])
        }
        if len(states) != len(roadmaps):
            raise ValueError("Portable V8 Roadmap pointer parity is invalid.")
        for roadmap in roadmaps:
            roadmap_id = str(roadmap["id"])
            state_row = states.get(roadmap_id)
            pointer_state = {
                "activeVersionId": roadmap.get("active_version_id"),
                "currentPhaseId": roadmap.get("current_phase_id"),
                "isCurrent": bool(roadmap.get("is_current")),
                "roadmapId": roadmap_id,
            }
            expected_hash = hashlib.sha256(
                json.dumps(pointer_state, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if state_row is None or (
                state_row.get("active_version_id") != roadmap.get("active_version_id")
                or state_row.get("current_phase_id") != roadmap.get("current_phase_id")
                or bool(state_row.get("is_current")) != bool(roadmap.get("is_current"))
                or state_row.get("state_hash") != expected_hash
            ):
                raise ValueError("Portable V8 Roadmap pointer parity is invalid.")
    state = {
        "canonicalLearningAuthority": "legacy_v1",
        "recommendationPresentation": "legacy_v1",
        "roadmapPresentation": "legacy_v1",
        "todayPresentation": "legacy_v1",
    }
    reason = "Initial legacy authority baseline"
    event_payload = {
        "commandType": "bootstrap",
        "reason": reason,
        "resultingState": state,
    }
    created = 0
    if "learning_control_authority_events" not in tables:
        tables["learning_control_authority_events"] = [
            {
                "id": "authority-bootstrap-legacy-v1",
                "event_sequence": 1,
                "idempotency_key": "authority-bootstrap-legacy-v1",
                "command_type": "bootstrap",
                "prior_state_json": None,
                "resulting_state_json": json.dumps(state, sort_keys=True, separators=(",", ":")),
                "reason": reason,
                "actor": "system",
                "source": "migration",
                "payload_hash": hashlib.sha256(
                    json.dumps(event_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
                "occurred_at": 0,
            }
        ]
        created += 1
    if "learning_control_authority_state" not in tables:
        tables["learning_control_authority_state"] = [
            {
                "id": 1,
                "canonical_learning_authority": "legacy_v1",
                "roadmap_presentation": "legacy_v1",
                "recommendation_presentation": "legacy_v1",
                "today_presentation": "legacy_v1",
                "event_sequence": 1,
                "last_event_id": "authority-bootstrap-legacy-v1",
                "state_hash": hashlib.sha256(
                    json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
                "updated_at": 0,
            }
        ]
        created += 1
    stripped = 0
    for roadmap in roadmaps:
        for column in ("is_current", "active_version_id", "current_phase_id"):
            if column in roadmap:
                roadmap.pop(column)
                stripped += 1
    return {"initializedAuthorityTables": created, "removedLegacyRoadmapPointers": stripped}


def supports_portable_schema(version: int) -> bool:
    return version in PORTABLE_SCHEMA_READABLE
