from __future__ import annotations

from collections import defaultdict
from typing import Any, cast

from sqlalchemy import Table, select
from sqlalchemy.orm import Session

from app.compatibility.v1.roadmap_active_state import current_legacy_roadmap
from app.models import (
    CompetencyAbilityItem,
    CompetencyDefinition,
    CompetencyIdentity,
    CompetencyPrerequisite,
    CompetencyUnderstandingItem,
    ExitCriterionDefinition,
    ExitCriterionIdentity,
    Phase,
    Roadmap,
    RoadmapVersion,
    Track,
)
from app.schemas import RoadmapCreate

PORTABLE_DOMAIN_TABLES: dict[str, frozenset[str]] = {
    # Shared identity anchors belong to their modern canonical owner. Tables that
    # connect domains belong to the domain that owns the relationship. Each
    # portable table is intentionally assigned exactly once so category totals do
    # not double count rows.
    "masterImport": frozenset({"master_import_revisions", "master_import_owned_keys"}),
    "assessment": frozenset(
        {"assessment_executions", "assessment_artifacts", "assessment_reviews"}
    ),
    "targetProfiles": frozenset(
        {
            "target_profiles",
            "target_profile_versions",
            "profile_domains",
            "profile_target_identities",
            "profile_targets",
            "milestone_identities",
            "profile_milestones",
            "profile_milestone_targets",
            "readiness_gate_identities",
            "readiness_gates",
            "readiness_gate_predicates",
            "readiness_gate_targets",
            "active_target_profile_state",
            "target_profile_activation_events",
        }
    ),
    "semanticCompetencies": frozenset(
        {
            "competency_identities",
            "capability_scale_versions",
            "capability_scale_dimensions",
            "capability_scale_levels",
            "semantic_competency_definitions",
            "semantic_definition_dimensions",
            "criterion_identities",
            "criterion_definitions",
            "active_competency_definition_states",
            "competency_definition_activation_events",
        }
    ),
    "capabilityReview": frozenset(
        {
            "capability_evaluation_runs",
            "criterion_evaluation_results",
            "capability_state_events",
            "review_events",
        }
    ),
    "sessions": frozenset(
        {
            "activity_category_versions",
            "activities",
            "learning_sessions",
            "session_contributions",
            "contribution_retractions",
            "session_corrections",
        }
    ),
    "evidence": frozenset(
        {
            "evidence",
            "evidence_links",
            "evidence_retractions",
            "evidence_invalidations",
            "evidence_link_retractions",
            "evidence_redactions",
        }
    ),
    "curriculum": frozenset(
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
    ),
    "projects": frozenset(
        {
            "projects",
            "project_versions",
            "project_milestone_identities",
            "project_milestone_definitions",
            "project_goal_identities",
            "project_goal_definitions",
            "project_task_identities",
            "project_task_definitions",
            "project_criterion_identities",
            "project_criterion_definitions",
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
    ),
    "learningGraph": frozenset(
        {
            "learning_graphs",
            "learning_graph_versions",
            "competency_edge_identities",
            "competency_edge_definitions",
            "active_learning_graph_states",
            "learning_graph_activation_events",
        }
    ),
    "roadmapProjection": frozenset(
        {
            "legacy_roadmap_active_states",
            "roadmap_node_position_overrides",
            "roadmap_projection_preferences",
        }
    ),
    "discipline": frozenset(
        {
            "discipline_profiles",
            "discipline_configuration_events",
        }
    ),
    "analysisV3": frozenset(
        {
            "analysis_runs",
            "analysis_snapshots",
            "analysis_v3_run_lineages",
            "analysis_v3_snapshot_details",
            "analysis_v3_normalized_facts",
            "analysis_v3_competency_gaps",
            "analysis_v3_signals",
            "analysis_v3_unknown_markers",
        }
    ),
    "recommendationV2": frozenset(
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
    ),
    "todayV2": frozenset(
        {
            "today_generations",
            "today_suggestions",
            "today_interactions",
            "today_interaction_corrections",
            "suggestion_activity_relations",
            "suggestion_activity_relation_corrections",
        }
    ),
    "learningControlAuthority": frozenset(
        {
            "learning_control_authority_events",
            "learning_control_authority_state",
        }
    ),
    "roadmap": frozenset(
        {
            "roadmaps",
            "roadmap_versions",
            "phases",
            "tracks",
            "roadmap_scope_events",
            "competency_definitions",
            "competency_prerequisites",
            "competency_understanding_items",
            "competency_ability_items",
            "exit_criterion_identities",
            "exit_criterion_definitions",
        }
    ),
    "competencyProgress": frozenset({"competency_states", "competency_status_events"}),
    "verification": frozenset({"verification_records", "verification_evidence"}),
    "recommendations": frozenset({"recommendation_snapshots"}),
    "legacyCompatibility": frozenset({"legacy_criterion_assertions", "migration_backfill_runs"}),
    "reflections": frozenset({"daily_reflections"}),
    "reports": frozenset({"generated_reports"}),
    "settings": frozenset({"application_settings"}),
    "operationHistory": frozenset({"import_records", "export_records"}),
}


def _value_change(before: Any, after: Any) -> dict[str, Any]:
    return {"from": before, "to": after}


def _list_change(before: list[str], after: list[str]) -> dict[str, list[str]] | None:
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    return {"added": added, "removed": removed} if added or removed else None


def _entity_changes(
    before: dict[str, Any], after: dict[str, Any], fields: tuple[str, ...]
) -> dict[str, Any]:
    return {
        field: _value_change(before[field], after[field])
        for field in fields
        if before[field] != after[field]
    }


def _collection_diff(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
    fields: tuple[str, ...],
) -> dict[str, Any]:
    modified = []
    for stable_key in sorted(set(before) & set(after)):
        changes = _entity_changes(before[stable_key], after[stable_key], fields)
        if changes:
            modified.append({"stableKey": stable_key, "changes": changes})
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "modified": modified,
    }


def _payload_snapshot(payload: RoadmapCreate) -> dict[str, Any]:
    phases: dict[str, dict[str, Any]] = {}
    tracks: dict[str, dict[str, Any]] = {}
    competencies: dict[str, dict[str, Any]] = {}
    for phase in payload.phases:
        phases[phase.stable_key] = {
            "title": phase.title,
            "description": phase.description,
            "orderIndex": phase.order_index,
            "archived": False,
        }
        for track in phase.tracks:
            tracks[track.stable_key] = {
                "title": track.title,
                "description": track.description,
                "orderIndex": track.order_index,
                "phaseStableKey": phase.stable_key,
            }
            for competency in track.competencies:
                competencies[competency.stable_key] = {
                    "title": competency.title,
                    "description": competency.description,
                    "goal": competency.goal,
                    "priority": competency.priority,
                    "weight": competency.weight,
                    "orderIndex": competency.order_index,
                    "phaseStableKey": phase.stable_key,
                    "trackStableKey": track.stable_key,
                    "parentStableKey": competency.parent_stable_key,
                    "requiredPrerequisites": sorted(competency.prerequisite_stable_keys),
                    "recommendedPrerequisites": sorted(
                        competency.recommended_prerequisite_stable_keys
                    ),
                    "mustUnderstand": competency.must_understand,
                    "mustBeAbleTo": competency.must_be_able_to,
                    "position": {"x": competency.position_x, "y": competency.position_y},
                    "exitCriteria": {
                        criterion.stable_key: {
                            "text": criterion.text,
                            "required": criterion.required,
                            "weight": criterion.weight,
                        }
                        for criterion in competency.exit_criteria
                    },
                }
    return {
        "metadata": {
            "stableKey": payload.stable_key,
            "title": payload.title,
            "description": payload.description,
        },
        "version": payload.version,
        "changelog": payload.changelog,
        "source": payload.source,
        "currentPhaseStableKey": payload.current_phase_stable_key,
        "phases": phases,
        "tracks": tracks,
        "competencies": competencies,
    }


def _database_snapshot(db: Session, roadmap: Roadmap | None) -> dict[str, Any]:
    if roadmap is None or roadmap.active_version_id is None:
        return {
            "metadata": {"stableKey": None, "title": None, "description": None},
            "version": None,
            "changelog": None,
            "source": None,
            "currentPhaseStableKey": None,
            "phases": {},
            "tracks": {},
            "competencies": {},
        }
    version = db.get(RoadmapVersion, roadmap.active_version_id)
    phases_list = db.scalars(
        select(Phase).where(Phase.roadmap_version_id == roadmap.active_version_id)
    ).all()
    tracks_list = db.scalars(
        select(Track).where(Track.roadmap_version_id == roadmap.active_version_id)
    ).all()
    definitions = db.scalars(
        select(CompetencyDefinition).where(
            CompetencyDefinition.roadmap_version_id == roadmap.active_version_id
        )
    ).all()
    phase_by_id = {item.id: item for item in phases_list}
    track_by_id = {item.id: item for item in tracks_list}
    identity_by_id = {
        item.id: item
        for item in db.scalars(
            select(CompetencyIdentity).where(
                CompetencyIdentity.id.in_([item.competency_identity_id for item in definitions])
            )
        ).all()
    }
    definition_by_id = {item.id: item for item in definitions}
    prerequisite_groups: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: {"required": [], "recommended": []}
    )
    for prerequisite in db.scalars(
        select(CompetencyPrerequisite).where(
            CompetencyPrerequisite.competency_definition_id.in_(definition_by_id)
        )
    ).all():
        prerequisite_groups[prerequisite.competency_definition_id][prerequisite.kind].append(
            identity_by_id[prerequisite.prerequisite_competency_identity_id].stable_key
        )
    understanding: dict[str, list[str]] = defaultdict(list)
    for understanding_item in db.scalars(
        select(CompetencyUnderstandingItem)
        .where(CompetencyUnderstandingItem.competency_definition_id.in_(definition_by_id))
        .order_by(CompetencyUnderstandingItem.order_index)
    ).all():
        understanding[understanding_item.competency_definition_id].append(understanding_item.text)
    abilities: dict[str, list[str]] = defaultdict(list)
    for ability_item in db.scalars(
        select(CompetencyAbilityItem)
        .where(CompetencyAbilityItem.competency_definition_id.in_(definition_by_id))
        .order_by(CompetencyAbilityItem.order_index)
    ).all():
        abilities[ability_item.competency_definition_id].append(ability_item.text)
    criterion_definitions = db.scalars(
        select(ExitCriterionDefinition).where(
            ExitCriterionDefinition.competency_definition_id.in_(definition_by_id)
        )
    ).all()
    criterion_identities = {
        item.id: item
        for item in db.scalars(
            select(ExitCriterionIdentity).where(
                ExitCriterionIdentity.id.in_(
                    [item.exit_criterion_identity_id for item in criterion_definitions]
                )
            )
        ).all()
    }
    criteria: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for criterion_definition in criterion_definitions:
        identity = criterion_identities[criterion_definition.exit_criterion_identity_id]
        criteria[criterion_definition.competency_definition_id][identity.stable_key] = {
            "text": criterion_definition.text,
            "required": criterion_definition.required,
            "weight": criterion_definition.weight,
        }
    phases = {
        item.stable_key: {
            "title": item.title,
            "description": item.description,
            "orderIndex": item.order_index,
            "archived": item.archived,
        }
        for item in phases_list
    }
    tracks = {
        item.stable_key: {
            "title": item.title,
            "description": item.description,
            "orderIndex": item.order_index,
            "phaseStableKey": phase_by_id[item.phase_id].stable_key,
        }
        for item in tracks_list
    }
    competencies: dict[str, dict[str, Any]] = {}
    for definition in definitions:
        stable_key = identity_by_id[definition.competency_identity_id].stable_key
        parent = (
            definition_by_id.get(definition.parent_definition_id)
            if definition.parent_definition_id
            else None
        )
        competencies[stable_key] = {
            "title": definition.title,
            "description": definition.description,
            "goal": definition.goal,
            "priority": definition.priority,
            "weight": definition.weight,
            "orderIndex": definition.order_index,
            "phaseStableKey": phase_by_id[definition.phase_id].stable_key,
            "trackStableKey": track_by_id[definition.track_id].stable_key,
            "parentStableKey": (
                identity_by_id[parent.competency_identity_id].stable_key if parent else None
            ),
            "requiredPrerequisites": sorted(prerequisite_groups[definition.id]["required"]),
            "recommendedPrerequisites": sorted(prerequisite_groups[definition.id]["recommended"]),
            "mustUnderstand": understanding[definition.id],
            "mustBeAbleTo": abilities[definition.id],
            "position": {"x": definition.position_x, "y": definition.position_y},
            "exitCriteria": criteria[definition.id],
        }
    current_phase = phase_by_id.get(roadmap.current_phase_id) if roadmap.current_phase_id else None
    return {
        "metadata": {
            "stableKey": roadmap.stable_key,
            "title": roadmap.title,
            "description": roadmap.description,
        },
        "version": version.version if version else None,
        "changelog": version.changelog if version else None,
        "source": version.source if version else None,
        "currentPhaseStableKey": current_phase.stable_key if current_phase else None,
        "phases": phases,
        "tracks": tracks,
        "competencies": competencies,
    }


def _exit_criteria_change(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    modified = []
    for stable_key in sorted(set(before) & set(after)):
        changes = _entity_changes(
            before[stable_key], after[stable_key], ("text", "required", "weight")
        )
        if changes:
            modified.append({"stableKey": stable_key, "changes": changes})
    result = {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "modified": modified,
    }
    return result if any(result.values()) else None


def build_roadmap_diff(
    db: Session, incoming_payload: RoadmapCreate, operation: str
) -> dict[str, Any]:
    current = current_legacy_roadmap(db)
    before = _database_snapshot(db, current)
    after = _payload_snapshot(incoming_payload)
    competency_modified = []
    simple_fields = (
        "title",
        "description",
        "goal",
        "priority",
        "weight",
        "orderIndex",
        "phaseStableKey",
        "trackStableKey",
        "parentStableKey",
        "mustUnderstand",
        "mustBeAbleTo",
        "position",
    )
    for stable_key in sorted(set(before["competencies"]) & set(after["competencies"])):
        old = before["competencies"][stable_key]
        new = after["competencies"][stable_key]
        changes = _entity_changes(old, new, simple_fields)
        required = _list_change(old["requiredPrerequisites"], new["requiredPrerequisites"])
        recommended = _list_change(old["recommendedPrerequisites"], new["recommendedPrerequisites"])
        exit_criteria = _exit_criteria_change(old["exitCriteria"], new["exitCriteria"])
        if required:
            changes["requiredPrerequisites"] = required
        if recommended:
            changes["recommendedPrerequisites"] = recommended
        if exit_criteria:
            changes["exitCriteria"] = exit_criteria
        if changes:
            competency_modified.append({"stableKey": stable_key, "changes": changes})
    current_phase_change = (
        _value_change(before["currentPhaseStableKey"], after["currentPhaseStableKey"])
        if before["currentPhaseStableKey"] != after["currentPhaseStableKey"]
        else None
    )
    return {
        "stableKey": incoming_payload.stable_key,
        "version": incoming_payload.version,
        "operation": operation,
        "roadmapMetadataChanges": _entity_changes(
            before["metadata"], after["metadata"], ("stableKey", "title", "description")
        ),
        "versionTransition": _value_change(before["version"], after["version"]),
        "incomingVersionMetadata": {
            "changelog": after["changelog"],
            "source": after["source"],
        },
        "currentPhase": current_phase_change,
        "phases": _collection_diff(
            before["phases"], after["phases"], ("title", "description", "orderIndex", "archived")
        ),
        "tracks": _collection_diff(
            before["tracks"],
            after["tracks"],
            ("title", "description", "orderIndex", "phaseStableKey"),
        ),
        "added": sorted(set(after["competencies"]) - set(before["competencies"])),
        "modified": competency_modified,
        "archivedFromActiveVersion": sorted(
            set(before["competencies"]) - set(after["competencies"])
        ),
        "preservedStateCount": len(set(before["competencies"]) & set(after["competencies"])),
        "learningLogsTouched": False,
    }


def _primary_key(table: Table, row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row[column.name] for column in table.primary_key.columns)


def build_portable_replacement_diff(
    existing_tables: dict[str, list[dict[str, Any]]],
    incoming_tables: dict[str, list[dict[str, Any]]],
    models_by_table: dict[str, Any],
) -> dict[str, Any]:
    assignments = [
        table_name for table_names in PORTABLE_DOMAIN_TABLES.values() for table_name in table_names
    ]
    classified_tables = set(assignments)
    portable_tables = set(models_by_table)
    duplicate_tables = sorted(
        table_name for table_name in classified_tables if assignments.count(table_name) > 1
    )
    if classified_tables != portable_tables or duplicate_tables:
        raise ValueError(
            "Portable diff taxonomy must classify every portable table exactly once: "
            f"missing={sorted(portable_tables - classified_tables)}, "
            f"unknown={sorted(classified_tables - portable_tables)}, "
            f"duplicates={duplicate_tables}"
        )
    table_changes: dict[str, dict[str, int | bool]] = {}
    for table_name, model in sorted(models_by_table.items()):
        table = cast(Table, model.__table__)
        existing_rows = {_primary_key(table, row): row for row in existing_tables[table_name]}
        incoming_rows = {_primary_key(table, row): row for row in incoming_tables[table_name]}
        added_keys = set(incoming_rows) - set(existing_rows)
        removed_keys = set(existing_rows) - set(incoming_rows)
        modified_keys = {
            key
            for key in set(existing_rows) & set(incoming_rows)
            if existing_rows[key] != incoming_rows[key]
        }
        table_changes[table_name] = {
            "existing": len(existing_rows),
            "incoming": len(incoming_rows),
            "added": len(added_keys),
            "modified": len(modified_keys),
            "removed": len(removed_keys),
            "changed": bool(added_keys or modified_keys or removed_keys),
        }
    categories: dict[str, dict[str, int | bool]] = {}
    for category, table_names in PORTABLE_DOMAIN_TABLES.items():
        existing_count = sum(int(table_changes[name]["existing"]) for name in table_names)
        incoming_count = sum(int(table_changes[name]["incoming"]) for name in table_names)
        added_count = sum(int(table_changes[name]["added"]) for name in table_names)
        modified_count = sum(int(table_changes[name]["modified"]) for name in table_names)
        removed_count = sum(int(table_changes[name]["removed"]) for name in table_names)
        categories[category] = {
            "existing": existing_count,
            "incoming": incoming_count,
            "added": added_count,
            "modified": modified_count,
            "removed": removed_count,
            "changed": bool(added_count or modified_count or removed_count),
        }
    return {
        "operation": "fullReplacement",
        "mergeSupported": False,
        "authenticationPreserved": True,
        "categoriesTouched": [name for name, changes in categories.items() if changes["changed"]],
        "willAddRows": any(changes["added"] for changes in table_changes.values()),
        "willModifyRows": any(changes["modified"] for changes in table_changes.values()),
        "willDeleteRows": any(changes["removed"] for changes in table_changes.values()),
        "categories": categories,
        "tables": table_changes,
    }
