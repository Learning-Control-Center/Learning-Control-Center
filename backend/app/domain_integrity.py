from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Boolean, Integer, String, Table, Text, func, select, text

from app.capability_scales import builtin_scale_tables
from app.compatibility.v1.activity_backfill import (
    POLICY_KEY as ACTIVITY_POLICY_KEY,
)
from app.compatibility.v1.activity_backfill import (
    RUN_ID as ACTIVITY_RUN_ID,
)
from app.compatibility.v1.activity_backfill import (
    activity_category_rows,
)
from app.compatibility.v1.evidence_backfill import POLICY_KEY as EVIDENCE_POLICY_KEY
from app.compatibility.v1.evidence_backfill import RUN_ID as EVIDENCE_RUN_ID
from app.compatibility.v1.evidence_backfill import result_rows_hash as evidence_rows_hash
from app.compatibility.v1.profile_competency_backfill import (
    POLICY_KEY,
    RUN_ID,
    canonical_rows_hash,
)
from app.curriculum.contracts import CurriculumVersionInput
from app.curriculum.models import (
    ActiveCurriculumVersionState,
    ActivityCurriculumLinkCorrection,
    ActivityCurriculumUnitLink,
    AssessmentRubricDefinition,
    AssessmentRubricIdentity,
    Curriculum,
    CurriculumActivationEvent,
    CurriculumObjectiveDefinition,
    CurriculumObjectiveIdentity,
    CurriculumVersion,
    EvidenceOpportunityDefinition,
    LearningUnitDefinition,
    LearningUnitIdentity,
    LearningUnitRequirement,
    LearningUnitTarget,
)
from app.determinism import content_hash
from app.domain import has_required_dependency_cycle
from app.errors import AppError
from app.learning_graph.models import (
    ActiveLearningGraphState,
    CompetencyEdgeDefinition,
    CompetencyEdgeIdentity,
    LearningGraph,
    LearningGraphActivationEvent,
    LearningGraphVersion,
)
from app.learning_graph.service import (
    GRAPH_SATISFACTION_POLICY,
    GRAPH_SCHEMA_VERSION,
)
from app.models import (
    ActiveCompetencyDefinitionState,
    ActiveTargetProfileState,
    Activity,
    ActivityCategoryVersion,
    AnalysisRun,
    AnalysisSnapshot,
    ApplicationSetting,
    CapabilityEvaluationRun,
    CapabilityScaleDimension,
    CapabilityScaleLevel,
    CapabilityScaleVersion,
    CapabilityStateEvent,
    CompetencyCapabilityState,
    CompetencyDefinition,
    CompetencyDefinitionActivationEvent,
    CompetencyIdentity,
    CompetencyPrerequisite,
    CompetencyReviewState,
    CompetencyState,
    CompetencyStatusEvent,
    ContributionRetraction,
    CriterionDefinition,
    CriterionEvaluationResult,
    CriterionIdentity,
    DisciplineProfile,
    Evidence,
    EvidenceInvalidation,
    EvidenceLink,
    EvidenceLinkRetraction,
    EvidenceRedaction,
    EvidenceRetraction,
    ExitCriterionDefinition,
    ExitCriterionIdentity,
    ExportRecord,
    GeneratedReport,
    ImportRecord,
    LearningSession,
    LegacyCriterionAssertion,
    MigrationBackfillRun,
    MilestoneIdentity,
    Phase,
    ProfileDomain,
    ProfileMilestone,
    ProfileMilestoneTarget,
    ProfileTarget,
    ProfileTargetIdentity,
    ReadinessGate,
    ReadinessGateIdentity,
    ReadinessGatePredicate,
    ReadinessGateTarget,
    RecommendationSnapshot,
    ReviewEvent,
    Roadmap,
    RoadmapScopeEvent,
    RoadmapVersion,
    SemanticCompetencyDefinition,
    SemanticDefinitionDimension,
    SessionContribution,
    SessionCorrection,
    TargetProfile,
    TargetProfileActivationEvent,
    TargetProfileVersion,
    Track,
    VerificationEvidence,
    VerificationRecord,
)
from app.projects.contracts import ProjectVersionInput
from app.projects.evidence_policy import (
    PROJECT_EVIDENCE_POLICY,
    derive_project_evidence_characteristics,
)
from app.projects.models import (
    ActiveProjectVersionState,
    ActivityProjectTaskLink,
    ActivityProjectTaskLinkCorrection,
    Project,
    ProjectCriterionDefinition,
    ProjectCriterionEvaluation,
    ProjectCriterionEvaluationEvidence,
    ProjectCriterionIdentity,
    ProjectEvent,
    ProjectEvidenceOpportunity,
    ProjectGoalDefinition,
    ProjectGoalIdentity,
    ProjectMilestoneDefinition,
    ProjectMilestoneIdentity,
    ProjectRequirement,
    ProjectTarget,
    ProjectTaskDefinition,
    ProjectTaskDependency,
    ProjectTaskIdentity,
    ProjectVersion,
    ProjectVersionActivationEvent,
    SessionProjectContribution,
    SessionProjectContributionRetraction,
)
from app.projects.service import PROJECT_CRITERION_POLICY, evaluate_project_criterion_evidence
from app.roadmap_projection.models import (
    LegacyRoadmapActiveState,
    RoadmapNodePositionOverride,
    RoadmapProjectionPreference,
)
from app.schemas import validate_external_reference
from app.time_utils import datetime_to_epoch_ms


def validate_portable_row_types(
    tables: dict[str, list[dict[str, Any]]], models_by_table: dict[str, Any]
) -> None:
    for table_name, rows in tables.items():
        table = models_by_table[table_name].__table__
        for row_index, row in enumerate(rows):
            for column in table.columns:
                value = row[column.name]
                if value is None:
                    continue
                valid = True
                if isinstance(column.type, Boolean):
                    valid = type(value) is bool
                elif isinstance(column.type, Integer):
                    valid = type(value) is int
                elif isinstance(column.type, (String, Text)):
                    valid = isinstance(value, str)
                if not valid:
                    raise AppError(
                        422,
                        "PORTABLE_TYPE_INVALID",
                        "Portable backup values must use canonical data types.",
                        {"table": table_name, "row": row_index, "column": column.name},
                    )


def _validate_json_columns(connection: Any) -> None:
    json_columns = (
        (DisciplineProfile, "adaptation_phase_config_json"),
        (GeneratedReport, "structured_payload_json"),
        (RecommendationSnapshot, "structured_payload_json"),
        (ImportRecord, "dry_run_summary_json"),
        (ExportRecord, "scope_summary_json"),
        (ApplicationSetting, "value_json"),
        (AnalysisRun, "scope_json"),
        (AnalysisRun, "input_lineage_json"),
        (AnalysisRun, "completeness_metadata_json"),
        (AnalysisSnapshot, "semantic_definition_references_json"),
        (AnalysisSnapshot, "capability_scale_version_references_json"),
        (AnalysisSnapshot, "policy_versions_json"),
        (AnalysisSnapshot, "input_lineage_json"),
        (AnalysisSnapshot, "normalized_facts_json"),
        (AnalysisSnapshot, "signals_json"),
        (AnalysisSnapshot, "unknown_markers_json"),
        (ReadinessGatePredicate, "subject_json"),
        (CriterionDefinition, "demonstration_rule_json"),
        (Evidence, "provenance_json"),
        (EvidenceLink, "provenance_json"),
        (EvidenceRedaction, "redacted_fields_json"),
        (CapabilityEvaluationRun, "input_payload_json"),
        (LearningUnitDefinition, "action_payload_json"),
        (CurriculumVersion, "definition_payload_json"),
        (LearningUnitRequirement, "subject_json"),
        (EvidenceOpportunityDefinition, "possible_characteristics_json"),
        (EvidenceOpportunityDefinition, "required_characteristics_json"),
        (AssessmentRubricDefinition, "rubric_json"),
        (ProjectVersion, "definition_payload_json"),
        (ProjectRequirement, "subject_json"),
        (ProjectEvidenceOpportunity, "intended_characteristics_json"),
        (ProjectEvent, "payload_json"),
        (ProjectCriterionEvaluation, "facts_json"),
    )
    for model, column_name in json_columns:
        column = getattr(model, column_name)
        for value in connection.execute(select(column)).scalars():
            try:
                json.loads(value)
            except (TypeError, json.JSONDecodeError) as exc:
                raise AppError(
                    422,
                    "PORTABLE_JSON_INVALID",
                    "Portable backup contains invalid structured JSON.",
                    {"table": cast(Table, model.__table__).name, "column": column_name},
                ) from exc


def _validate_roadmap_scope(connection: Any) -> None:
    roadmaps = connection.execute(
        select(
            Roadmap.id,
            Roadmap.is_current,
            Roadmap.active_version_id,
            Roadmap.current_phase_id,
        )
    ).all()
    if roadmaps and sum(bool(row.is_current) for row in roadmaps) != 1:
        raise AppError(
            422,
            "PORTABLE_ROADMAP_STATE_INVALID",
            "Configured roadmap data requires exactly one current roadmap.",
        )
    versions = {
        row.id: row
        for row in connection.execute(select(RoadmapVersion.id, RoadmapVersion.roadmap_id)).all()
    }
    phases = {
        row.id: row
        for row in connection.execute(
            select(Phase.id, Phase.roadmap_version_id, Phase.archived)
        ).all()
    }
    for roadmap in roadmaps:
        if roadmap.is_current:
            version = versions.get(roadmap.active_version_id)
            phase = phases.get(roadmap.current_phase_id)
            if (
                version is None
                or phase is None
                or version.roadmap_id != roadmap.id
                or phase.roadmap_version_id != version.id
                or phase.archived
            ):
                raise AppError(
                    422,
                    "PORTABLE_ROADMAP_STATE_INVALID",
                    "The current roadmap pointers are inconsistent.",
                )
        elif roadmap.active_version_id is not None or roadmap.current_phase_id is not None:
            raise AppError(
                422,
                "PORTABLE_ROADMAP_STATE_INVALID",
                "A non-current roadmap cannot retain current pointers.",
            )


def _validate_roadmap_scope_history(connection: Any) -> None:
    events = connection.execute(
        select(
            RoadmapScopeEvent.roadmap_id,
            RoadmapScopeEvent.roadmap_version_id,
            RoadmapScopeEvent.phase_id,
            RoadmapScopeEvent.occurred_at,
            RoadmapScopeEvent.event_sequence,
        )
    ).all()
    versions = {
        row.id: row.roadmap_id
        for row in connection.execute(select(RoadmapVersion.id, RoadmapVersion.roadmap_id)).all()
    }
    phases = {
        row.id: row.roadmap_version_id
        for row in connection.execute(select(Phase.id, Phase.roadmap_version_id)).all()
    }
    for event in events:
        if (
            versions.get(event.roadmap_version_id) != event.roadmap_id
            or phases.get(event.phase_id) != event.roadmap_version_id
        ):
            raise AppError(
                422,
                "PORTABLE_SCOPE_HISTORY_INVALID",
                "A roadmap scope event contains inconsistent references.",
            )
    ordered_events = sorted(events, key=lambda event: event.event_sequence)
    if [event.event_sequence for event in ordered_events] != list(
        range(1, len(ordered_events) + 1)
    ) or any(
        previous.occurred_at > current.occurred_at
        for previous, current in zip(ordered_events, ordered_events[1:], strict=False)
    ):
        raise AppError(
            422,
            "PORTABLE_SCOPE_HISTORY_INVALID",
            "Roadmap scope event ordering is invalid.",
        )

    current = connection.execute(
        select(Roadmap.id, Roadmap.active_version_id, Roadmap.current_phase_id).where(
            Roadmap.is_current.is_(True)
        )
    ).first()
    if current is None:
        return
    if not events:
        raise AppError(
            422,
            "PORTABLE_SCOPE_HISTORY_INVALID",
            "The current roadmap has no historical scope baseline.",
        )
    latest = ordered_events[-1]
    if (
        latest.roadmap_id != current.id
        or latest.roadmap_version_id != current.active_version_id
        or latest.phase_id != current.current_phase_id
    ):
        raise AppError(
            422,
            "PORTABLE_SCOPE_HISTORY_INVALID",
            "The latest roadmap scope event does not match the current scope.",
        )


def _validate_versioned_roadmap(connection: Any) -> None:
    phases = {
        row.id: row.roadmap_version_id
        for row in connection.execute(select(Phase.id, Phase.roadmap_version_id)).all()
    }
    tracks = {
        row.id: row
        for row in connection.execute(
            select(Track.id, Track.roadmap_version_id, Track.phase_id)
        ).all()
    }
    definitions = connection.execute(
        select(
            CompetencyDefinition.id,
            CompetencyDefinition.competency_identity_id,
            CompetencyDefinition.roadmap_version_id,
            CompetencyDefinition.phase_id,
            CompetencyDefinition.track_id,
            CompetencyDefinition.parent_definition_id,
        )
    ).all()
    definitions_by_id = {row.id: row for row in definitions}
    identities_by_version: dict[str, set[str]] = defaultdict(set)
    parent_edges: dict[str, dict[str, set[str]]] = defaultdict(dict)
    dependency_edges: dict[str, dict[str, set[str]]] = defaultdict(dict)

    for definition in definitions:
        track = tracks.get(definition.track_id)
        if (
            phases.get(definition.phase_id) != definition.roadmap_version_id
            or track is None
            or track.roadmap_version_id != definition.roadmap_version_id
            or track.phase_id != definition.phase_id
        ):
            raise AppError(
                422,
                "PORTABLE_ROADMAP_PLACEMENT_INVALID",
                "A competency phase or track placement is inconsistent.",
            )
        identities_by_version[definition.roadmap_version_id].add(definition.competency_identity_id)
        if definition.parent_definition_id is not None:
            parent = definitions_by_id.get(definition.parent_definition_id)
            if parent is None or parent.roadmap_version_id != definition.roadmap_version_id:
                raise AppError(
                    422,
                    "PORTABLE_ROADMAP_PLACEMENT_INVALID",
                    "A competency parent belongs to a different roadmap version.",
                )
            parent_edges[definition.roadmap_version_id].setdefault(definition.id, set()).add(
                parent.id
            )

    for track in tracks.values():
        if phases.get(track.phase_id) != track.roadmap_version_id:
            raise AppError(
                422,
                "PORTABLE_ROADMAP_PLACEMENT_INVALID",
                "A track phase placement is inconsistent.",
            )

    for prerequisite in connection.execute(
        select(
            CompetencyPrerequisite.competency_definition_id,
            CompetencyPrerequisite.prerequisite_competency_identity_id,
            CompetencyPrerequisite.kind,
        )
    ).all():
        definition = definitions_by_id[prerequisite.competency_definition_id]
        version_id = definition.roadmap_version_id
        if (
            prerequisite.prerequisite_competency_identity_id
            not in identities_by_version[version_id]
        ):
            raise AppError(
                422,
                "PORTABLE_PREREQUISITE_INVALID",
                "A prerequisite is not defined in the same roadmap version.",
            )
        if prerequisite.kind == "required":
            dependency_edges[version_id].setdefault(definition.competency_identity_id, set()).add(
                prerequisite.prerequisite_competency_identity_id
            )

    if any(has_required_dependency_cycle(edges) for edges in parent_edges.values()):
        raise AppError(
            422,
            "COMPETENCY_HIERARCHY_CYCLE",
            "The competency hierarchy contains a cycle.",
        )
    if any(has_required_dependency_cycle(edges) for edges in dependency_edges.values()):
        raise AppError(
            422,
            "REQUIRED_DEPENDENCY_CYCLE",
            "Required prerequisites contain a cycle.",
        )

    criterion_identities = {
        row.id: row.competency_identity_id
        for row in connection.execute(
            select(ExitCriterionIdentity.id, ExitCriterionIdentity.competency_identity_id)
        ).all()
    }
    for criterion in connection.execute(
        select(
            ExitCriterionDefinition.exit_criterion_identity_id,
            ExitCriterionDefinition.competency_definition_id,
        )
    ).all():
        definition = definitions_by_id[criterion.competency_definition_id]
        if (
            criterion_identities[criterion.exit_criterion_identity_id]
            != definition.competency_identity_id
        ):
            raise AppError(
                422,
                "PORTABLE_EXIT_CRITERION_INVALID",
                "An exit criterion is attached to a different competency identity.",
            )


def _validate_competency_history(connection: Any) -> None:
    identity_ids = set(connection.execute(select(CompetencyIdentity.id)).scalars())
    states = {
        row.competency_identity_id: row.current_status
        for row in connection.execute(
            select(CompetencyState.competency_identity_id, CompetencyState.current_status)
        ).all()
    }
    if set(states) != identity_ids:
        raise AppError(
            422,
            "PORTABLE_COMPETENCY_STATE_INVALID",
            "Every competency identity requires exactly one current state.",
            {
                "missingStateCount": len(identity_ids - set(states)),
                "unexpectedStateCount": len(set(states) - identity_ids),
            },
        )

    verifications = {
        row.id: row
        for row in connection.execute(
            select(
                VerificationRecord.id,
                VerificationRecord.competency_identity_id,
                VerificationRecord.result,
            )
        ).all()
    }
    events_by_identity: dict[str, list[Any]] = defaultdict(list)
    for event in connection.execute(
        select(
            CompetencyStatusEvent.competency_identity_id,
            CompetencyStatusEvent.to_status,
            CompetencyStatusEvent.verification_record_id,
            CompetencyStatusEvent.created_at,
        )
    ).all():
        events_by_identity[event.competency_identity_id].append(event)
        if event.to_status == "verified":
            verification = verifications.get(event.verification_record_id)
            if (
                verification is None
                or verification.result != "passed"
                or verification.competency_identity_id != event.competency_identity_id
            ):
                raise AppError(
                    422,
                    "PORTABLE_VERIFICATION_INVALID",
                    "Every verified status event requires its matching passed verification.",
                )

    for identity_id, current_status in states.items():
        events = events_by_identity.get(identity_id, [])
        if not events:
            raise AppError(
                422,
                "PORTABLE_STATUS_HISTORY_INVALID",
                "Every competency state requires status history.",
            )
        latest_timestamp = max(event.created_at for event in events)
        if not any(
            event.created_at == latest_timestamp and event.to_status == current_status
            for event in events
        ):
            raise AppError(
                422,
                "PORTABLE_STATUS_HISTORY_INVALID",
                "Current competency state does not match its latest status history.",
            )
        if current_status == "verified" and not any(
            record.result == "passed" and record.competency_identity_id == identity_id
            for record in verifications.values()
        ):
            raise AppError(
                422,
                "PORTABLE_VERIFICATION_INVALID",
                "Verified competency state requires passed verification history.",
            )


def _validate_analysis_history(connection: Any) -> None:
    runs = {
        row.id: row
        for row in connection.execute(
            select(
                AnalysisRun.id,
                AnalysisRun.purpose,
                AnalysisRun.status,
                AnalysisRun.generated_at,
                AnalysisRun.cutoff_at,
                AnalysisRun.configuration_hash,
                AnalysisRun.input_lineage_json,
                AnalysisRun.input_hash,
                AnalysisRun.application_version,
            )
        ).all()
    }
    snapshot_ids: set[str] = set()
    snapshot_run_ids: set[str] = set()
    snapshots = connection.execute(
        select(
            AnalysisSnapshot.id,
            AnalysisSnapshot.run_id,
            AnalysisSnapshot.purpose,
            AnalysisSnapshot.generated_at,
            AnalysisSnapshot.cutoff_at,
            AnalysisSnapshot.cutoff_semantics,
            AnalysisSnapshot.configuration_hash,
            AnalysisSnapshot.application_version,
            AnalysisSnapshot.input_lineage_json,
            AnalysisSnapshot.input_hash,
            AnalysisSnapshot.normalized_facts_json,
            AnalysisSnapshot.signals_json,
            AnalysisSnapshot.completeness,
            AnalysisSnapshot.unknown_markers_json,
            AnalysisSnapshot.semantic_definition_references_json,
            AnalysisSnapshot.capability_scale_version_references_json,
            AnalysisSnapshot.policy_versions_json,
            AnalysisSnapshot.output_hash,
        )
    ).all()
    for snapshot in snapshots:
        run = runs.get(snapshot.run_id)
        if run is None:
            raise AppError(422, "PORTABLE_ANALYSIS_INVALID", "An analysis snapshot has no run.")
        lineage = json.loads(snapshot.input_lineage_json)
        normalized = json.loads(snapshot.normalized_facts_json)
        signals = json.loads(snapshot.signals_json)
        unknown = json.loads(snapshot.unknown_markers_json)
        semantic_references = json.loads(snapshot.semantic_definition_references_json)
        scale_references = json.loads(snapshot.capability_scale_version_references_json)
        policy_versions = json.loads(snapshot.policy_versions_json)
        matching = (
            run.purpose == snapshot.purpose
            and run.generated_at == snapshot.generated_at
            and run.cutoff_at == snapshot.cutoff_at
            and run.configuration_hash == snapshot.configuration_hash
            and run.input_lineage_json == snapshot.input_lineage_json
            and run.input_hash == snapshot.input_hash
            and run.application_version == snapshot.application_version
        )
        structures_valid = (
            snapshot.cutoff_semantics == "exclusive"
            and snapshot.cutoff_at > snapshot.generated_at
            and isinstance(lineage, list)
            and isinstance(normalized, dict)
            and isinstance(signals, list)
            and isinstance(unknown, list)
            and isinstance(semantic_references, list)
            and isinstance(scale_references, list)
            and isinstance(policy_versions, dict)
            and all(
                isinstance(marker, dict)
                and all(isinstance(marker.get(key), str) for key in ("code", "path", "reason"))
                for marker in unknown
            )
        )
        expected_input = content_hash({"lineage": lineage, "normalizedFacts": normalized})
        expected_output = content_hash(
            {
                "normalizedFacts": normalized,
                "signals": signals,
                "completeness": snapshot.completeness,
                "unknownMarkers": unknown,
            }
        )
        if (
            not matching
            or not structures_valid
            or snapshot.input_hash != expected_input
            or snapshot.output_hash != expected_output
        ):
            raise AppError(
                422,
                "PORTABLE_ANALYSIS_INVALID",
                "Analysis history lineage or hashes are inconsistent.",
            )
        snapshot_ids.add(snapshot.id)
        snapshot_run_ids.add(snapshot.run_id)
    invalid_run_lifecycle = any(
        (run.status == "failed" and run_id in snapshot_run_ids)
        or (run.status in {"completed", "partial"} and run_id not in snapshot_run_ids)
        for run_id, run in runs.items()
    )
    if invalid_run_lifecycle:
        raise AppError(
            422,
            "PORTABLE_ANALYSIS_INVALID",
            "Analysis run status and snapshot lifecycle are inconsistent.",
        )
    linked_ids = connection.execute(
        select(RecommendationSnapshot.analysis_snapshot_id).where(
            RecommendationSnapshot.analysis_snapshot_id.is_not(None)
        )
    ).scalars()
    if any(snapshot_id not in snapshot_ids for snapshot_id in linked_ids):
        raise AppError(
            422,
            "PORTABLE_ANALYSIS_INVALID",
            "Recommendation analysis lineage is invalid.",
        )


def _validate_v2_profile_competency(connection: Any) -> None:
    identity_ids = set(connection.execute(select(CompetencyIdentity.id)).scalars())
    for identity in connection.execute(
        select(
            CompetencyIdentity.id,
            CompetencyIdentity.identity_created_at,
            CompetencyIdentity.creation_source,
            CompetencyIdentity.legacy_unspecified_reason,
            CompetencyIdentity.retired_at,
            CompetencyIdentity.retirement_reason,
        )
    ).all():
        native = identity.identity_created_at is not None and bool(identity.creation_source)
        legacy = (
            identity.identity_created_at is None
            and identity.creation_source is None
            and bool(identity.legacy_unspecified_reason)
        )
        retirement_complete = (identity.retired_at is None) == (identity.retirement_reason is None)
        if not (native ^ legacy) or not retirement_complete:
            raise AppError(
                422,
                "PORTABLE_COMPETENCY_IDENTITY_INVALID",
                "Competency creation and retirement metadata is incomplete.",
            )

    scales = {
        row.id: row
        for row in connection.execute(
            select(
                CapabilityScaleVersion.id,
                CapabilityScaleVersion.scale_stable_key,
                CapabilityScaleVersion.scale_version,
            )
        ).all()
    }
    expected_scale_tables = builtin_scale_tables()
    for scale_model in (
        CapabilityScaleVersion,
        CapabilityScaleDimension,
        CapabilityScaleLevel,
    ):
        table = cast(Table, scale_model.__table__)
        actual_rows = [
            dict(row._mapping) for row in connection.execute(select(*table.columns)).all()
        ]
        expected_rows = expected_scale_tables[table.name]
        if sorted(actual_rows, key=lambda row: str(row["id"])) != sorted(
            expected_rows, key=lambda row: str(row["id"])
        ):
            raise AppError(
                422,
                "PORTABLE_CAPABILITY_SCALE_INVALID",
                "Built-in capability scale facts do not match the supported policy version.",
            )
    levels = {
        row.id: row.scale_version_id
        for row in connection.execute(
            select(CapabilityScaleLevel.id, CapabilityScaleLevel.scale_version_id)
        ).all()
    }
    dimensions = {
        row.id: row.scale_version_id
        for row in connection.execute(
            select(CapabilityScaleDimension.id, CapabilityScaleDimension.scale_version_id)
        ).all()
    }

    profiles = set(connection.execute(select(TargetProfile.id)).scalars())
    versions = {
        row.id: row
        for row in connection.execute(
            select(
                TargetProfileVersion.id,
                TargetProfileVersion.target_profile_id,
                TargetProfileVersion.version,
                TargetProfileVersion.supersedes_version_id,
            )
        ).all()
    }
    for version in versions.values():
        previous = versions.get(version.supersedes_version_id)
        if (
            version.target_profile_id not in profiles
            or (version.version == 1 and version.supersedes_version_id is not None)
            or (
                version.version > 1
                and (
                    previous is None
                    or previous.target_profile_id != version.target_profile_id
                    or previous.version != version.version - 1
                )
            )
        ):
            raise AppError(
                422,
                "PORTABLE_TARGET_PROFILE_INVALID",
                "Target profile version history is inconsistent.",
            )

    domains = {
        row.id: row
        for row in connection.execute(
            select(
                ProfileDomain.id,
                ProfileDomain.profile_version_id,
                ProfileDomain.minimum_percent,
                ProfileDomain.maximum_percent,
            )
        ).all()
    }
    domains_by_version: dict[str, list[Any]] = defaultdict(list)
    for domain in domains.values():
        domains_by_version[domain.profile_version_id].append(domain)
    for version_id, version_domains in domains_by_version.items():
        if version_id not in versions:
            raise AppError(422, "PORTABLE_TARGET_PROFILE_INVALID", "A profile domain is orphaned.")
        if any(
            item.minimum_percent is not None or item.maximum_percent is not None
            for item in version_domains
        ):
            minimum = sum(item.minimum_percent or 0 for item in version_domains)
            maximum = sum(
                item.maximum_percent if item.maximum_percent is not None else 100
                for item in version_domains
            )
            if minimum > 100 or maximum < 100:
                raise AppError(
                    422,
                    "PORTABLE_PROFILE_ALLOCATION_INVALID",
                    "Profile domain allocations are infeasible.",
                )

    target_identities = {
        row.id: row
        for row in connection.execute(
            select(
                ProfileTargetIdentity.id,
                ProfileTargetIdentity.target_profile_id,
                ProfileTargetIdentity.competency_identity_id,
                ProfileTargetIdentity.dimension_key,
            )
        ).all()
    }
    targets = {
        row.id: row
        for row in connection.execute(
            select(
                ProfileTarget.id,
                ProfileTarget.profile_version_id,
                ProfileTarget.target_identity_id,
                ProfileTarget.profile_domain_id,
                ProfileTarget.scale_version_id,
                ProfileTarget.target_level_id,
                ProfileTarget.target_date,
                ProfileTarget.target_month,
                ProfileTarget.date_interpretation,
            )
        ).all()
    }
    scale_families_by_target_identity: dict[str, set[str]] = defaultdict(set)
    for target in targets.values():
        version = versions.get(target.profile_version_id)
        identity = target_identities.get(target.target_identity_id)
        domain = domains.get(target.profile_domain_id)
        scale = scales.get(target.scale_version_id)
        if scale is not None:
            scale_families_by_target_identity[target.target_identity_id].add(scale.scale_stable_key)
        dimension_keys = {
            row.stable_key
            for row in connection.execute(
                select(CapabilityScaleDimension.stable_key).where(
                    CapabilityScaleDimension.scale_version_id == target.scale_version_id
                )
            ).all()
        }
        dated = target.target_date is not None or target.target_month is not None
        if (
            version is None
            or identity is None
            or identity.target_profile_id != version.target_profile_id
            or identity.competency_identity_id not in identity_ids
            or domain is None
            or domain.profile_version_id != target.profile_version_id
            or scale is None
            or levels.get(target.target_level_id) != target.scale_version_id
            or (identity.dimension_key is not None and identity.dimension_key not in dimension_keys)
            or (scale.scale_stable_key == "technical" and identity.dimension_key is not None)
            or dated != (target.date_interpretation is not None)
        ):
            raise AppError(
                422,
                "PORTABLE_PROFILE_TARGET_INVALID",
                "A target contains inconsistent identity, scale, domain, or date references.",
            )
    if any(len(families) > 1 for families in scale_families_by_target_identity.values()):
        raise AppError(
            422,
            "PORTABLE_PROFILE_TARGET_INVALID",
            "A stable target identity crosses incomparable scale families.",
        )

    milestone_identities = {
        row.id: row
        for row in connection.execute(
            select(
                MilestoneIdentity.id,
                MilestoneIdentity.target_profile_id,
            )
        ).all()
    }
    milestones = {
        row.id: row
        for row in connection.execute(
            select(
                ProfileMilestone.id,
                ProfileMilestone.profile_version_id,
                ProfileMilestone.milestone_identity_id,
            )
        ).all()
    }
    for milestone in milestones.values():
        version = versions.get(milestone.profile_version_id)
        identity = milestone_identities.get(milestone.milestone_identity_id)
        if (
            version is None
            or identity is None
            or identity.target_profile_id != version.target_profile_id
        ):
            raise AppError(
                422,
                "PORTABLE_PROFILE_MILESTONE_INVALID",
                "A milestone identity belongs to a different profile.",
            )
    for link in connection.execute(
        select(
            ProfileMilestoneTarget.milestone_id,
            ProfileMilestoneTarget.profile_target_id,
        )
    ).all():
        milestone = milestones.get(link.milestone_id)
        target = targets.get(link.profile_target_id)
        if (
            milestone is None
            or target is None
            or milestone.profile_version_id != target.profile_version_id
        ):
            raise AppError(
                422,
                "PORTABLE_PROFILE_MILESTONE_INVALID",
                "A milestone link crosses profile versions.",
            )

    gate_identities = {
        row.id: row
        for row in connection.execute(
            select(ReadinessGateIdentity.id, ReadinessGateIdentity.target_profile_id)
        ).all()
    }
    gates = {
        row.id: row
        for row in connection.execute(
            select(
                ReadinessGate.id,
                ReadinessGate.profile_version_id,
                ReadinessGate.gate_identity_id,
                ReadinessGate.milestone_id,
            )
        ).all()
    }
    for gate in gates.values():
        version = versions.get(gate.profile_version_id)
        identity = gate_identities.get(gate.gate_identity_id)
        milestone = milestones.get(gate.milestone_id) if gate.milestone_id else None
        if (
            version is None
            or identity is None
            or identity.target_profile_id != version.target_profile_id
            or (milestone is not None and milestone.profile_version_id != gate.profile_version_id)
        ):
            raise AppError(
                422,
                "PORTABLE_READINESS_GATE_INVALID",
                "A readiness gate contains cross-profile references.",
            )
    for link in connection.execute(
        select(ReadinessGateTarget.gate_id, ReadinessGateTarget.profile_target_id)
    ).all():
        gate = gates.get(link.gate_id)
        target = targets.get(link.profile_target_id)
        if gate is None or target is None or gate.profile_version_id != target.profile_version_id:
            raise AppError(
                422,
                "PORTABLE_READINESS_GATE_INVALID",
                "A readiness gate target crosses profile versions.",
            )
    criterion_identity_ids = set(connection.execute(select(CriterionIdentity.id)).scalars())
    project_criterion_identity_ids = set(
        connection.execute(select(ProjectCriterionIdentity.id)).scalars()
    )
    expected_subject_keys = {
        "capability_at_least": {
            "competencyIdentityId",
            "dimensionKey",
            "scaleStableKey",
            "scaleVersion",
            "levelStableKey",
        },
        "criterion_demonstrated": {"criterionIdentityId"},
        "project_criterion_demonstrated": {"projectCriterionIdentityId"},
        "evidence_present": {"evidencePolicyStableKey"},
    }
    for predicate in connection.execute(
        select(
            ReadinessGatePredicate.gate_id,
            ReadinessGatePredicate.predicate_type,
            ReadinessGatePredicate.subject_json,
        )
    ).all():
        subject = json.loads(predicate.subject_json)
        invalid = (
            predicate.gate_id not in gates
            or predicate.predicate_type not in expected_subject_keys
            or not isinstance(subject, dict)
            or set(subject) != expected_subject_keys.get(predicate.predicate_type, set())
        )
        if not invalid and predicate.predicate_type == "criterion_demonstrated":
            invalid = subject["criterionIdentityId"] not in criterion_identity_ids
        if not invalid and predicate.predicate_type == "project_criterion_demonstrated":
            invalid = subject["projectCriterionIdentityId"] not in project_criterion_identity_ids
        if not invalid and predicate.predicate_type == "capability_at_least":
            scale = next(
                (
                    item
                    for item in scales.values()
                    if item.scale_stable_key == subject["scaleStableKey"]
                    and item.scale_version == subject["scaleVersion"]
                ),
                None,
            )
            level_exists = bool(
                scale
                and connection.scalar(
                    select(func.count())
                    .select_from(CapabilityScaleLevel)
                    .where(
                        CapabilityScaleLevel.scale_version_id == scale.id,
                        CapabilityScaleLevel.stable_key == subject["levelStableKey"],
                    )
                )
            )
            dimension = subject["dimensionKey"]
            dimension_exists = dimension is None or bool(
                scale
                and connection.scalar(
                    select(func.count())
                    .select_from(CapabilityScaleDimension)
                    .where(
                        CapabilityScaleDimension.scale_version_id == scale.id,
                        CapabilityScaleDimension.stable_key == dimension,
                    )
                )
            )
            invalid = (
                subject["competencyIdentityId"] not in identity_ids
                or not level_exists
                or not dimension_exists
                or bool(scale and scale.scale_stable_key == "technical" and dimension is not None)
            )
        if invalid:
            raise AppError(
                422,
                "PORTABLE_READINESS_PREDICATE_INVALID",
                "A readiness predicate is unsupported or structurally invalid.",
            )

    active_profiles = connection.execute(
        select(
            ActiveTargetProfileState.target_profile_id,
            ActiveTargetProfileState.target_profile_version_id,
        )
    ).all()
    for active in active_profiles:
        version = versions.get(active.target_profile_version_id)
        if version is None or version.target_profile_id != active.target_profile_id:
            raise AppError(422, "PORTABLE_PROFILE_ACTIVATION_INVALID", "Active profile is invalid.")

    profile_events = connection.execute(
        select(
            TargetProfileActivationEvent.from_profile_version_id,
            TargetProfileActivationEvent.to_profile_version_id,
            TargetProfileActivationEvent.source,
            TargetProfileActivationEvent.event_sequence,
        ).order_by(TargetProfileActivationEvent.event_sequence)
    ).all()
    profile_chain_valid = all(
        event.from_profile_version_id
        == (None if index == 0 else profile_events[index - 1].to_profile_version_id)
        for index, event in enumerate(profile_events)
    )
    active_profile_valid = (not profile_events and not active_profiles) or (
        bool(profile_events)
        and len(active_profiles) == 1
        and active_profiles[0].target_profile_version_id == profile_events[-1].to_profile_version_id
    )
    if (
        [item.event_sequence for item in profile_events] != list(range(1, len(profile_events) + 1))
        or not profile_chain_valid
        or not active_profile_valid
        or any(
            not item.source
            or item.to_profile_version_id not in versions
            or (
                item.from_profile_version_id is not None
                and item.from_profile_version_id not in versions
            )
            for item in profile_events
        )
    ):
        raise AppError(
            422,
            "PORTABLE_PROFILE_ACTIVATION_INVALID",
            "Profile activation history is inconsistent.",
        )

    definitions = {
        row.id: row
        for row in connection.execute(
            select(
                SemanticCompetencyDefinition.id,
                SemanticCompetencyDefinition.competency_identity_id,
                SemanticCompetencyDefinition.definition_version,
                SemanticCompetencyDefinition.scale_version_id,
                SemanticCompetencyDefinition.supersedes_definition_id,
            )
        ).all()
    }
    definition_dimensions: dict[str, set[str]] = defaultdict(set)
    for row in connection.execute(
        select(
            SemanticDefinitionDimension.semantic_definition_id,
            SemanticDefinitionDimension.scale_dimension_id,
        )
    ).all():
        definition = definitions.get(row.semantic_definition_id)
        if (
            definition is None
            or dimensions.get(row.scale_dimension_id) != definition.scale_version_id
        ):
            raise AppError(
                422,
                "PORTABLE_SEMANTIC_DEFINITION_INVALID",
                "Definition dimensions are inconsistent.",
            )
        definition_dimensions[row.semantic_definition_id].add(row.scale_dimension_id)
    for definition in definitions.values():
        previous = definitions.get(definition.supersedes_definition_id)
        if (
            definition.competency_identity_id not in identity_ids
            or definition.scale_version_id not in scales
            or (definition.definition_version == 1 and previous is not None)
            or (
                definition.definition_version > 1
                and (
                    previous is None
                    or previous.competency_identity_id != definition.competency_identity_id
                    or previous.definition_version != definition.definition_version - 1
                )
            )
        ):
            raise AppError(
                422, "PORTABLE_SEMANTIC_DEFINITION_INVALID", "Definition history is invalid."
            )

    criterion_identities = {
        row.id: row
        for row in connection.execute(
            select(
                CriterionIdentity.id,
                CriterionIdentity.competency_identity_id,
                CriterionIdentity.stable_key,
                CriterionIdentity.created_at,
                CriterionIdentity.creation_source,
                CriterionIdentity.legacy_unspecified_reason,
                CriterionIdentity.retired_at,
                CriterionIdentity.retirement_reason,
            )
        ).all()
    }
    allowed_rules = {
        "exposure",
        "guided_performance",
        "independent_performance",
        "repeated_independent_performance",
        "authoritative_assessment",
    }
    criteria = connection.execute(
        select(
            CriterionDefinition.criterion_identity_id,
            CriterionDefinition.semantic_definition_id,
            CriterionDefinition.level_id,
            CriterionDefinition.dimension_id,
            CriterionDefinition.demonstration_rule_json,
        )
    ).all()
    for criterion in criteria:
        identity = criterion_identities.get(criterion.criterion_identity_id)
        definition = definitions.get(criterion.semantic_definition_id)
        try:
            rule_payload = json.loads(criterion.demonstration_rule_json)
        except (TypeError, json.JSONDecodeError):
            rule_payload = None
        if (
            identity is None
            or definition is None
            or identity.competency_identity_id != definition.competency_identity_id
            or levels.get(criterion.level_id) != definition.scale_version_id
            or (
                criterion.dimension_id is not None
                and criterion.dimension_id not in definition_dimensions[definition.id]
            )
            or not isinstance(rule_payload, dict)
            or set(rule_payload) != {"rule"}
            or rule_payload["rule"] not in allowed_rules
        ):
            raise AppError(422, "PORTABLE_CRITERION_INVALID", "A native criterion is invalid.")
    for identity in criterion_identities.values():
        native = identity.created_at is not None and bool(identity.creation_source)
        legacy = (
            identity.created_at is None
            and identity.creation_source is None
            and bool(identity.legacy_unspecified_reason)
        )
        if not (native ^ legacy):
            raise AppError(
                422, "PORTABLE_CRITERION_IDENTITY_INVALID", "Criterion metadata is invalid."
            )
    assertions = connection.execute(
        select(
            LegacyCriterionAssertion.id,
            LegacyCriterionAssertion.criterion_identity_id,
            LegacyCriterionAssertion.source_exit_criterion_identity_id,
            LegacyCriterionAssertion.source_exit_criterion_definition_id,
            LegacyCriterionAssertion.legacy_state,
            LegacyCriterionAssertion.requirement_type,
            LegacyCriterionAssertion.demonstration_rule,
            LegacyCriterionAssertion.evidence_strength,
            LegacyCriterionAssertion.independence,
            LegacyCriterionAssertion.source_confidence,
            LegacyCriterionAssertion.legacy_unspecified_reason,
            LegacyCriterionAssertion.asserted_at,
            LegacyCriterionAssertion.cutoff_at,
            LegacyCriterionAssertion.provenance,
        )
    ).all()
    legacy_identity_state = {
        row.id: (row.competency_identity_id, row.stable_key, row.current_state)
        for row in connection.execute(
            select(
                ExitCriterionIdentity.id,
                ExitCriterionIdentity.competency_identity_id,
                ExitCriterionIdentity.stable_key,
                ExitCriterionIdentity.current_state,
            )
        ).all()
    }
    legacy_definitions = {
        row.id: (row.exit_criterion_identity_id, row.created_at, row.required)
        for row in connection.execute(
            select(
                ExitCriterionDefinition.id,
                ExitCriterionDefinition.exit_criterion_identity_id,
                ExitCriterionDefinition.created_at,
                ExitCriterionDefinition.required,
            )
        ).all()
    }
    latest_assertion_by_identity: dict[str, Any] = {}
    for assertion in assertions:
        criterion_identity = criterion_identities.get(assertion.criterion_identity_id)
        source_identity = legacy_identity_state.get(assertion.source_exit_criterion_identity_id)
        source_definition = legacy_definitions.get(assertion.source_exit_criterion_definition_id)
        expected_requirement = "required" if source_definition and source_definition[2] else None
        if (
            criterion_identity is None
            or source_identity is None
            or source_identity[0] != criterion_identity.competency_identity_id
            or source_identity[1] != criterion_identity.stable_key
            or (
                assertion.provenance == POLICY_KEY
                and assertion.criterion_identity_id != assertion.source_exit_criterion_identity_id
            )
            or source_definition is None
            or source_definition[0] != assertion.source_exit_criterion_identity_id
            or assertion.requirement_type != expected_requirement
            or assertion.provenance not in {"legacy-backfill-policy/v1", "v1_compatibility"}
            or assertion.cutoff_at <= assertion.asserted_at
            or assertion.demonstration_rule is not None
            or assertion.evidence_strength is not None
            or assertion.independence is not None
            or assertion.source_confidence is not None
            or not assertion.legacy_unspecified_reason
        ):
            raise AppError(
                422, "PORTABLE_LEGACY_ASSERTION_INVALID", "A legacy assertion invents V2 semantics."
            )
        previous = latest_assertion_by_identity.get(assertion.criterion_identity_id)
        if previous is None or (assertion.asserted_at, assertion.cutoff_at) > (
            previous.asserted_at,
            previous.cutoff_at,
        ):
            latest_assertion_by_identity[assertion.criterion_identity_id] = assertion
    if any(
        assertion.legacy_state
        != legacy_identity_state[assertion.source_exit_criterion_identity_id][2]
        for assertion in latest_assertion_by_identity.values()
    ):
        raise AppError(
            422,
            "PORTABLE_LEGACY_ASSERTION_INVALID",
            "Latest legacy assertions do not match current V1 criterion state.",
        )

    backfill_assertions = [item for item in assertions if item.provenance == POLICY_KEY]
    runs = connection.execute(
        select(
            MigrationBackfillRun.id,
            MigrationBackfillRun.policy_key,
            MigrationBackfillRun.source_kind,
            MigrationBackfillRun.source_row_count,
            MigrationBackfillRun.result_row_count,
            MigrationBackfillRun.source_hash,
            MigrationBackfillRun.result_hash,
        ).where(MigrationBackfillRun.id == RUN_ID)
    ).all()
    if len(runs) != 1:
        raise AppError(422, "PORTABLE_BACKFILL_RUN_INVALID", "Backfill provenance is incomplete.")
    run = runs[0]
    source_rows: list[dict[str, Any]] = []
    result_rows: list[dict[str, Any]] = []
    for assertion in sorted(backfill_assertions, key=lambda item: item.criterion_identity_id):
        identity = criterion_identities[assertion.criterion_identity_id]
        source_identity = legacy_identity_state[assertion.source_exit_criterion_identity_id]
        source_definition = legacy_definitions[assertion.source_exit_criterion_definition_id]
        source_rows.append(
            {
                "identity_id": assertion.source_exit_criterion_identity_id,
                "competency_identity_id": source_identity[0],
                "stable_key": source_identity[1],
                "current_state": assertion.legacy_state,
                "updated_at": assertion.asserted_at,
                "source_definition_id": assertion.source_exit_criterion_definition_id,
                "source_definition_created_at": source_definition[1],
                "required": bool(source_definition[2]),
            }
        )
        result_rows.extend(
            [
                {
                    "id": identity.id,
                    "competency_identity_id": identity.competency_identity_id,
                    "stable_key": identity.stable_key,
                    "created_at": identity.created_at,
                    "creation_source": identity.creation_source,
                    "legacy_unspecified_reason": identity.legacy_unspecified_reason,
                    "retired_at": identity.retired_at,
                    "retirement_reason": identity.retirement_reason,
                },
                {
                    "id": assertion.id,
                    "criterion_identity_id": assertion.criterion_identity_id,
                    "source_exit_criterion_identity_id": (
                        assertion.source_exit_criterion_identity_id
                    ),
                    "source_exit_criterion_definition_id": (
                        assertion.source_exit_criterion_definition_id
                    ),
                    "legacy_state": assertion.legacy_state,
                    "requirement_type": assertion.requirement_type,
                    "demonstration_rule": assertion.demonstration_rule,
                    "evidence_strength": assertion.evidence_strength,
                    "independence": assertion.independence,
                    "source_confidence": assertion.source_confidence,
                    "legacy_unspecified_reason": assertion.legacy_unspecified_reason,
                    "asserted_at": assertion.asserted_at,
                    "cutoff_at": assertion.cutoff_at,
                    "provenance": assertion.provenance,
                },
            ]
        )
    result_rows.sort(key=lambda row: (row["id"], len(row)))
    if (
        run.id != RUN_ID
        or run.policy_key != POLICY_KEY
        or run.source_kind != "v1_exit_criteria"
        or run.source_row_count != len(source_rows)
        or run.result_row_count != len(result_rows)
        or run.source_hash != canonical_rows_hash(source_rows)
        or run.result_hash != canonical_rows_hash(result_rows)
    ):
        raise AppError(
            422,
            "PORTABLE_BACKFILL_RUN_INVALID",
            "Backfill counts or hashes do not match immutable legacy history.",
        )

    active_definitions = connection.execute(
        select(
            ActiveCompetencyDefinitionState.competency_identity_id,
            ActiveCompetencyDefinitionState.semantic_definition_id,
        )
    ).all()
    active_definition_by_competency = {
        item.competency_identity_id: item.semantic_definition_id for item in active_definitions
    }
    for active in active_definitions:
        definition = definitions.get(active.semantic_definition_id)
        if definition is None or definition.competency_identity_id != active.competency_identity_id:
            raise AppError(
                422, "PORTABLE_DEFINITION_ACTIVATION_INVALID", "Active definition is invalid."
            )

    definition_events = connection.execute(
        select(
            CompetencyDefinitionActivationEvent.competency_identity_id,
            CompetencyDefinitionActivationEvent.from_definition_id,
            CompetencyDefinitionActivationEvent.to_definition_id,
            CompetencyDefinitionActivationEvent.source,
            CompetencyDefinitionActivationEvent.event_sequence,
        ).order_by(CompetencyDefinitionActivationEvent.event_sequence)
    ).all()
    if [item.event_sequence for item in definition_events] != list(
        range(1, len(definition_events) + 1)
    ):
        raise AppError(
            422,
            "PORTABLE_DEFINITION_ACTIVATION_INVALID",
            "Definition activation event ordering is invalid.",
        )
    previous_target_by_competency: dict[str, str] = {}
    for item in definition_events:
        target = definitions.get(item.to_definition_id)
        previous = definitions.get(item.from_definition_id) if item.from_definition_id else None
        if (
            not item.source
            or target is None
            or target.competency_identity_id != item.competency_identity_id
            or item.from_definition_id
            != previous_target_by_competency.get(item.competency_identity_id)
            or (
                previous is not None
                and previous.competency_identity_id != item.competency_identity_id
            )
        ):
            raise AppError(
                422,
                "PORTABLE_DEFINITION_ACTIVATION_INVALID",
                "Definition activation history contains inconsistent references.",
            )
        previous_target_by_competency[item.competency_identity_id] = item.to_definition_id
    if previous_target_by_competency != active_definition_by_competency:
        raise AppError(
            422,
            "PORTABLE_DEFINITION_ACTIVATION_INVALID",
            "Definition activation history does not match active definition state.",
        )


def _validate_activity_sessions(connection: Any) -> None:
    category_table = cast(Table, ActivityCategoryVersion.__table__)
    actual_categories = [
        dict(row._mapping) for row in connection.execute(select(*category_table.columns)).all()
    ]
    if sorted(actual_categories, key=lambda row: str(row["id"])) != sorted(
        activity_category_rows(), key=lambda row: str(row["id"])
    ):
        raise AppError(
            422, "PORTABLE_ACTIVITY_CATEGORY_INVALID", "Activity vocabulary is inconsistent."
        )
    activities = {
        row.id: row
        for row in connection.execute(
            select(
                Activity.id,
                Activity.category_stable_key,
                Activity.category_version,
                Activity.context_started_at,
                Activity.context_ended_at,
                Activity.supersedes_activity_id,
                Activity.provenance,
            )
        ).all()
    }
    categories = {(row["stable_key"], row["vocabulary_version"]) for row in actual_categories}
    for activity in activities.values():
        if (
            (activity.category_stable_key, activity.category_version) not in categories
            or (
                activity.context_started_at is not None
                and activity.context_ended_at is not None
                and activity.context_ended_at < activity.context_started_at
            )
            or (
                activity.supersedes_activity_id is not None
                and activity.supersedes_activity_id not in activities
            )
        ):
            raise AppError(422, "PORTABLE_ACTIVITY_INVALID", "An Activity is inconsistent.")
    for activity in activities.values():
        seen: set[str] = set()
        current = activity
        while current.supersedes_activity_id is not None:
            if current.id in seen:
                raise AppError(
                    422, "PORTABLE_ACTIVITY_INVALID", "Activity supersession contains a cycle."
                )
            seen.add(current.id)
            current = activities[current.supersedes_activity_id]
    sessions = {
        row.id: row
        for row in connection.execute(
            select(
                LearningSession.id,
                LearningSession.activity_id,
                LearningSession.competency_identity_id,
                LearningSession.tombstoned_at,
                LearningSession.tombstone_reason,
            )
        ).all()
    }
    if any(
        item.activity_id not in activities
        or ((item.tombstoned_at is None) != (item.tombstone_reason is None))
        for item in sessions.values()
    ):
        raise AppError(422, "PORTABLE_SESSION_ACTIVITY_INVALID", "A Session is inconsistent.")
    contributions = {
        row.id: row
        for row in connection.execute(
            select(
                SessionContribution.id,
                SessionContribution.session_id,
                SessionContribution.competency_identity_id,
                SessionContribution.criterion_identity_id,
                SessionContribution.relevance,
                SessionContribution.created_at,
                SessionContribution.provenance,
            )
        ).all()
    }
    criterion_competencies = {
        row.id: row.competency_identity_id
        for row in connection.execute(
            select(CriterionIdentity.id, CriterionIdentity.competency_identity_id)
        ).all()
    }
    retractions = {
        row.contribution_id: row
        for row in connection.execute(
            select(
                ContributionRetraction.contribution_id,
                ContributionRetraction.replacement_contribution_id,
            )
        ).all()
    }
    primary_by_session: dict[str, int] = defaultdict(int)
    for contribution in contributions.values():
        criterion_competency = (
            criterion_competencies.get(contribution.criterion_identity_id)
            if contribution.criterion_identity_id
            else contribution.competency_identity_id
        )
        if (
            contribution.session_id not in sessions
            or criterion_competency != contribution.competency_identity_id
        ):
            raise AppError(
                422, "PORTABLE_SESSION_CONTRIBUTION_INVALID", "A contribution is inconsistent."
            )
        if contribution.id not in retractions and contribution.relevance == "primary":
            primary_by_session[contribution.session_id] += 1
    if any(count > 1 for count in primary_by_session.values()):
        raise AppError(
            422,
            "PORTABLE_SESSION_CONTRIBUTION_INVALID",
            "A Session has multiple Primary competencies.",
        )
    active_primary = {
        contribution.session_id: contribution.competency_identity_id
        for contribution in contributions.values()
        if contribution.id not in retractions and contribution.relevance == "primary"
    }
    if any(
        session.competency_identity_id != active_primary.get(session.id)
        for session in sessions.values()
    ):
        raise AppError(
            422,
            "PORTABLE_SESSION_CONTRIBUTION_INVALID",
            "The legacy competency projection does not match the active Primary contribution.",
        )
    for contribution_id, retraction in retractions.items():
        replacement = contributions.get(retraction.replacement_contribution_id)
        if contribution_id not in contributions or (
            retraction.replacement_contribution_id == contribution_id
            or (
                replacement is not None
                and replacement.session_id != contributions[contribution_id].session_id
            )
        ):
            raise AppError(
                422, "PORTABLE_CONTRIBUTION_RETRACTION_INVALID", "A retraction is inconsistent."
            )
    for correction in connection.execute(
        select(
            SessionCorrection.session_id,
            SessionCorrection.changed_fields_json,
            SessionCorrection.before_json,
            SessionCorrection.after_json,
        )
    ).all():
        try:
            values = [
                json.loads(correction.changed_fields_json),
                json.loads(correction.before_json),
                json.loads(correction.after_json),
            ]
        except (TypeError, json.JSONDecodeError) as exc:
            raise AppError(
                422, "PORTABLE_SESSION_CORRECTION_INVALID", "A correction is malformed."
            ) from exc
        if (
            correction.session_id not in sessions
            or not isinstance(values[0], list)
            or not isinstance(values[1], dict)
            or not isinstance(values[2], dict)
        ):
            raise AppError(
                422, "PORTABLE_SESSION_CORRECTION_INVALID", "A correction is inconsistent."
            )
    backfill_activities = [
        dict(row._mapping)
        for row in connection.execute(select(*cast(Table, Activity.__table__).columns)).all()
        if row._mapping["provenance"] == ACTIVITY_POLICY_KEY
    ]
    backfill_contributions = [
        dict(row._mapping)
        for row in connection.execute(
            select(*cast(Table, SessionContribution.__table__).columns)
        ).all()
        if row._mapping["provenance"] == "deterministic_legacy_backfill"
    ]
    result_rows = sorted(
        backfill_activities + backfill_contributions,
        key=lambda row: (str(row["id"]), len(row)),
    )
    run = connection.execute(
        select(
            MigrationBackfillRun.policy_key,
            MigrationBackfillRun.source_kind,
            MigrationBackfillRun.source_row_count,
            MigrationBackfillRun.result_row_count,
            MigrationBackfillRun.source_hash,
            MigrationBackfillRun.result_hash,
        ).where(MigrationBackfillRun.id == ACTIVITY_RUN_ID)
    ).one_or_none()
    if (
        run is None
        or run.policy_key != ACTIVITY_POLICY_KEY
        or run.source_kind != "v1_learning_sessions"
        or run.source_row_count != len(backfill_activities)
        or run.result_row_count != len(result_rows)
        or len(run.source_hash) != 64
        or run.result_hash != canonical_rows_hash(result_rows)
    ):
        raise AppError(
            422, "PORTABLE_ACTIVITY_BACKFILL_INVALID", "Activity backfill lineage is inconsistent."
        )


def _validate_evidence(connection: Any) -> None:
    redaction_fields = {"description", "external_reference"}
    redactions_by_evidence: dict[str, set[str]] = {}
    for redaction in connection.execute(
        select(EvidenceRedaction.evidence_id, EvidenceRedaction.redacted_fields_json)
    ).all():
        try:
            fields = json.loads(redaction.redacted_fields_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AppError(
                422, "PORTABLE_EVIDENCE_REDACTION_INVALID", "A redaction is malformed."
            ) from exc
        if (
            not isinstance(fields, list)
            or not fields
            or any(field not in redaction_fields for field in fields)
        ):
            raise AppError(
                422, "PORTABLE_EVIDENCE_REDACTION_INVALID", "A redaction is inconsistent."
            )
        redactions_by_evidence[redaction.evidence_id] = set(fields)
    evidence_rows = {
        row.id: row
        for row in connection.execute(
            select(
                Evidence.id,
                Evidence.source_type,
                Evidence.source_id,
                Evidence.supersedes_evidence_id,
                Evidence.strength_unknown_reason,
                Evidence.independence_unknown_reason,
                Evidence.source_confidence_unknown_reason,
                Evidence.occurred_at_unknown_reason,
                Evidence.provenance_json,
                Evidence.policy_version,
                Evidence.schema_version,
                Evidence.description,
                Evidence.external_reference,
            )
        ).all()
    }
    for item in evidence_rows.values():
        try:
            provenance = json.loads(item.provenance_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AppError(
                422, "PORTABLE_EVIDENCE_INVALID", "Evidence provenance is malformed."
            ) from exc
        if not isinstance(provenance, dict):
            raise AppError(422, "PORTABLE_EVIDENCE_INVALID", "Evidence provenance is inconsistent.")
        allowed_unknown_reasons = {
            "legacy_unspecified",
            "source_policy_unspecified",
            "source_unspecified",
            "user_unspecified",
            "import_unspecified",
            "activity_outcome_unknown",
            "session_assistance_unknown",
        }
        unknown_reasons = (
            item.strength_unknown_reason,
            item.independence_unknown_reason,
            item.source_confidence_unknown_reason,
            item.occurred_at_unknown_reason,
        )
        capture_method = provenance.get("capture_method")
        expected_provenance_policy = (
            EVIDENCE_POLICY_KEY
            if capture_method == "deterministic_legacy_backfill"
            else item.policy_version
        )
        origin_kind = provenance.get("origin_kind")
        import_package_id = provenance.get("import_package_id")
        current_description_hash = evidence_rows_hash([{"value": item.description}])
        current_reference_hash = evidence_rows_hash([{"value": item.external_reference}])
        redacted_fields_for_item = redactions_by_evidence.get(item.id, set())
        try:
            validate_external_reference(item.external_reference)
        except ValueError as exc:
            raise AppError(
                422,
                "PORTABLE_EVIDENCE_REFERENCE_UNSAFE",
                "Evidence contains a credential-bearing external reference.",
            ) from exc
        if (
            origin_kind not in {"local", "external", "import"}
            or not isinstance(provenance.get("creator_kind"), str)
            or not provenance.get("creator_kind")
            or not isinstance(capture_method, str)
            or provenance.get("policy_version") != expected_provenance_policy
            or provenance.get("source_record_type") != item.source_type
            or provenance.get("source_record_id") != item.source_id
            or (origin_kind == "import" and not isinstance(import_package_id, str))
            or (origin_kind == "import" and not import_package_id)
            or (origin_kind != "import" and import_package_id not in {None, ""})
            or (
                item.source_type == "native_evidence_command"
                and not re.fullmatch(r"[0-9a-f]{64}", str(provenance.get("command_hash", "")))
            )
            or (
                capture_method == "deterministic_legacy_backfill"
                and "description" not in redacted_fields_for_item
                and provenance.get("description_hash") != current_description_hash
            )
            or (
                capture_method == "deterministic_legacy_backfill"
                and "external_reference" not in redacted_fields_for_item
                and provenance.get("external_reference_hash") != current_reference_hash
            )
            or item.policy_version != "evidence-policy/v1"
            or item.schema_version != 1
            or any(
                reason is not None and reason not in allowed_unknown_reasons
                for reason in unknown_reasons
            )
            or (
                item.supersedes_evidence_id is not None
                and item.supersedes_evidence_id not in evidence_rows
            )
        ):
            raise AppError(422, "PORTABLE_EVIDENCE_INVALID", "Evidence provenance is inconsistent.")
    for item in evidence_rows.values():
        seen: set[str] = set()
        current = item
        while current.supersedes_evidence_id is not None:
            if current.id in seen:
                raise AppError(
                    422,
                    "PORTABLE_EVIDENCE_INVALID",
                    "Evidence supersession contains a cycle.",
                )
            seen.add(current.id)
            current = evidence_rows[current.supersedes_evidence_id]

    criterion_competencies = dict(
        connection.execute(
            select(CriterionIdentity.id, CriterionIdentity.competency_identity_id)
        ).all()
    )
    for reference in connection.execute(select(VerificationEvidence.reference)).scalars():
        try:
            validate_external_reference(reference)
        except ValueError as exc:
            raise AppError(
                422,
                "PORTABLE_VERIFICATION_REFERENCE_UNSAFE",
                "Verification Evidence contains a credential-bearing reference.",
            ) from exc
    criterion_definitions = {
        row.id: row
        for row in connection.execute(
            select(
                CriterionDefinition.id,
                CriterionDefinition.criterion_identity_id,
                CriterionDefinition.level_id,
                CriterionDefinition.dimension_id,
            )
        ).all()
    }
    dimensions = dict(
        connection.execute(
            select(CapabilityScaleDimension.id, CapabilityScaleDimension.scale_version_id)
        ).all()
    )
    levels = dict(
        connection.execute(
            select(CapabilityScaleLevel.id, CapabilityScaleLevel.scale_version_id)
        ).all()
    )
    scales = set(connection.execute(select(CapabilityScaleVersion.id)).scalars())
    contributions = {
        row.id: row
        for row in connection.execute(
            select(
                SessionContribution.id,
                SessionContribution.session_id,
                SessionContribution.competency_identity_id,
                SessionContribution.criterion_identity_id,
            )
        ).all()
    }
    links = {
        row.id: row
        for row in connection.execute(
            select(
                EvidenceLink.id,
                EvidenceLink.evidence_id,
                EvidenceLink.source_contribution_id,
                EvidenceLink.competency_identity_id,
                EvidenceLink.criterion_identity_id,
                EvidenceLink.criterion_definition_id,
                EvidenceLink.scale_version_id,
                EvidenceLink.dimension_id,
                EvidenceLink.level_id,
                EvidenceLink.provenance_json,
            )
        ).all()
    }
    for link in links.values():
        contribution = contributions.get(link.source_contribution_id)
        linked_evidence = evidence_rows.get(link.evidence_id)
        definition = criterion_definitions.get(link.criterion_definition_id)
        try:
            link_provenance = json.loads(link.provenance_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AppError(
                422, "PORTABLE_EVIDENCE_LINK_INVALID", "EvidenceLink provenance is malformed."
            ) from exc
        expected_link_policy = (
            EVIDENCE_POLICY_KEY
            if isinstance(link_provenance, dict)
            and link_provenance.get("capture_method") == "deterministic_legacy_backfill"
            else "evidence-policy/v1"
        )
        if (
            link.evidence_id not in evidence_rows
            or not isinstance(link_provenance, dict)
            or not isinstance(link_provenance.get("capture_method"), str)
            or link_provenance.get("policy_version") != expected_link_policy
            or link_provenance.get("evidence_id") != link.evidence_id
            or (
                link.source_contribution_id is not None
                and link_provenance.get("session_contribution_id") != link.source_contribution_id
            )
            or (
                link.source_contribution_id is None
                and link_provenance.get("session_contribution_id") is not None
            )
            or (
                link.criterion_identity_id is not None
                and criterion_competencies.get(link.criterion_identity_id)
                != link.competency_identity_id
            )
            or (
                link.criterion_definition_id is not None
                and (
                    link.criterion_identity_id is None
                    or definition is None
                    or definition.criterion_identity_id != link.criterion_identity_id
                    or definition.level_id != link.level_id
                    or definition.dimension_id != link.dimension_id
                    or levels.get(definition.level_id) != link.scale_version_id
                )
            )
            or (link.scale_version_id is not None and link.scale_version_id not in scales)
            or (
                link.dimension_id is not None
                and dimensions.get(link.dimension_id) != link.scale_version_id
            )
            or (link.level_id is not None and levels.get(link.level_id) != link.scale_version_id)
            or (
                link.source_contribution_id is not None
                and (
                    contribution is None
                    or linked_evidence is None
                    or linked_evidence.source_type != "learning_session"
                    or linked_evidence.source_id != contribution.session_id
                    or contribution.competency_identity_id != link.competency_identity_id
                    or contribution.criterion_identity_id != link.criterion_identity_id
                )
            )
        ):
            raise AppError(
                422, "PORTABLE_EVIDENCE_LINK_INVALID", "An EvidenceLink is inconsistent."
            )

    superseding_children: dict[str, list[str]] = defaultdict(list)
    for item in evidence_rows.values():
        if item.supersedes_evidence_id is not None:
            superseding_children[item.supersedes_evidence_id].append(item.id)
    if any(len(children) > 1 for children in superseding_children.values()):
        raise AppError(
            422,
            "PORTABLE_EVIDENCE_LIFECYCLE_INVALID",
            "Evidence supersession history contains a fork.",
        )
    retraction_replacements: dict[str, str | None] = {}
    for item in connection.execute(
        select(
            EvidenceRetraction.evidence_id,
            EvidenceRetraction.replacement_evidence_id,
        )
    ).all():
        retraction_replacements[item.evidence_id] = item.replacement_evidence_id
        replacement = evidence_rows.get(item.replacement_evidence_id)
        if item.replacement_evidence_id == item.evidence_id or (
            replacement is not None and replacement.supersedes_evidence_id != item.evidence_id
        ):
            raise AppError(
                422,
                "PORTABLE_EVIDENCE_LIFECYCLE_INVALID",
                "Evidence retraction replacement is inconsistent.",
            )
    if any(
        retraction_replacements.get(parent_id) != children[0]
        for parent_id, children in superseding_children.items()
    ):
        raise AppError(
            422,
            "PORTABLE_EVIDENCE_LIFECYCLE_INVALID",
            "Evidence supersession is missing its matching retraction.",
        )
    for item in connection.execute(
        select(
            EvidenceLinkRetraction.evidence_link_id,
            EvidenceLinkRetraction.replacement_link_id,
        )
    ).all():
        original = links.get(item.evidence_link_id)
        replacement = links.get(item.replacement_link_id)
        if item.replacement_link_id == item.evidence_link_id or (
            replacement is not None
            and original is not None
            and replacement.evidence_id != original.evidence_id
        ):
            raise AppError(
                422,
                "PORTABLE_EVIDENCE_LIFECYCLE_INVALID",
                "EvidenceLink retraction replacement is inconsistent.",
            )
    evidence_table = cast(Table, Evidence.__table__)
    link_table = cast(Table, EvidenceLink.__table__)
    backfill_evidence = []
    for row in connection.execute(select(*evidence_table.columns)).all():
        values = dict(row._mapping)
        provenance = json.loads(values["provenance_json"])
        if provenance.get("capture_method") == "deterministic_legacy_backfill":
            backfill_evidence.append(values)
    backfill_links = []
    for row in connection.execute(select(*link_table.columns)).all():
        values = dict(row._mapping)
        provenance = json.loads(values["provenance_json"])
        if provenance.get("capture_method") == "deterministic_legacy_backfill":
            backfill_links.append(values)
    result_rows = sorted(
        backfill_evidence + backfill_links,
        key=lambda row: (str(row["id"]), len(row)),
    )
    run = connection.execute(
        select(
            MigrationBackfillRun.policy_key,
            MigrationBackfillRun.source_kind,
            MigrationBackfillRun.source_row_count,
            MigrationBackfillRun.result_row_count,
            MigrationBackfillRun.source_hash,
            MigrationBackfillRun.result_hash,
        ).where(MigrationBackfillRun.id == EVIDENCE_RUN_ID)
    ).one_or_none()
    if (
        run is None
        or run.policy_key != EVIDENCE_POLICY_KEY
        or run.source_kind != "v1_evidence_sources"
        or run.source_row_count < 0
        or len(run.source_hash) != 64
        or run.result_row_count != len(result_rows)
        or run.result_hash != evidence_rows_hash(result_rows)
    ):
        raise AppError(
            422,
            "PORTABLE_EVIDENCE_BACKFILL_INVALID",
            "Evidence backfill lineage is inconsistent.",
        )


def validate_domain_integrity(connection: Any) -> None:
    violations = connection.execute(text("PRAGMA foreign_key_check")).all()
    if violations:
        raise AppError(
            422,
            "PORTABLE_REFERENCES_INVALID",
            "Portable backup references are invalid.",
            {"violationCount": len(violations)},
        )
    _validate_json_columns(connection)
    _validate_analysis_history(connection)
    _validate_roadmap_scope(connection)
    _validate_roadmap_scope_history(connection)
    _validate_versioned_roadmap(connection)
    _validate_competency_history(connection)
    _validate_v2_profile_competency(connection)
    _validate_activity_sessions(connection)
    _validate_evidence(connection)
    _validate_capability_history(connection)
    _validate_curriculum(connection)
    _validate_projects(connection)
    _validate_learning_graph_and_projection(connection)
    for timezone_name in connection.execute(select(DisciplineProfile.timezone)).scalars():
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise AppError(
                422,
                "PORTABLE_TIMEZONE_INVALID",
                "The discipline timezone is not a valid IANA timezone.",
            ) from exc


def _validate_curriculum(connection: Any) -> None:
    curricula = {
        item.id: item for item in connection.execute(select(*Curriculum.__table__.c)).all()
    }
    versions = {
        item.id: item for item in connection.execute(select(*CurriculumVersion.__table__.c)).all()
    }
    objective_identities = {
        item.id: item
        for item in connection.execute(select(*CurriculumObjectiveIdentity.__table__.c)).all()
    }
    unit_identities = {
        item.id: item
        for item in connection.execute(select(*LearningUnitIdentity.__table__.c)).all()
    }
    rubric_identities = {
        item.id: item
        for item in connection.execute(select(*AssessmentRubricIdentity.__table__.c)).all()
    }
    for curriculum in curricula.values():
        if (curriculum.retired_at is None) != (curriculum.retirement_reason is None):
            raise AppError(
                422,
                "PORTABLE_CURRICULUM_INVALID",
                "Curriculum retirement metadata must be complete.",
            )
    versions_by_curriculum: dict[str, list[Any]] = defaultdict(list)
    for version in versions.values():
        if (
            version.curriculum_id not in curricula
            or version.content_hash != content_hash(json.loads(version.definition_payload_json))
            or version.schema_version != "curriculum-schema/v1"
        ):
            raise AppError(
                422,
                "PORTABLE_CURRICULUM_INVALID",
                "A Curriculum version has invalid lineage or policy metadata.",
            )
        if version.supersedes_version_id is not None:
            previous = versions.get(version.supersedes_version_id)
            if (
                previous is None
                or previous.curriculum_id != version.curriculum_id
                or previous.version >= version.version
            ):
                raise AppError(
                    422,
                    "PORTABLE_CURRICULUM_INVALID",
                    "A Curriculum supersession reference is inconsistent.",
                )
        versions_by_curriculum[version.curriculum_id].append(version)
    for curriculum_id, items in versions_by_curriculum.items():
        ordered = sorted(items, key=lambda item: item.version)
        if [item.version for item in ordered] != list(range(1, len(ordered) + 1)):
            raise AppError(
                422,
                "PORTABLE_CURRICULUM_INVALID",
                "Curriculum versions must be contiguous per aggregate.",
            )
        for index, version in enumerate(ordered):
            expected = None if index == 0 else ordered[index - 1].id
            if version.supersedes_version_id != expected:
                raise AppError(
                    422,
                    "PORTABLE_CURRICULUM_INVALID",
                    "Curriculum supersession must reference the immediately prior version.",
                    {"curriculumId": curriculum_id, "versionId": version.id},
                )
    objective_definitions = connection.execute(
        select(*CurriculumObjectiveDefinition.__table__.c)
    ).all()
    objective_definitions_by_pair: set[tuple[str, str]] = set()
    for definition in objective_definitions:
        identity = objective_identities.get(definition.objective_identity_id)
        version = versions.get(definition.curriculum_version_id)
        if identity is None or version is None or identity.curriculum_id != version.curriculum_id:
            raise AppError(
                422,
                "PORTABLE_CURRICULUM_INVALID",
                "An objective identity belongs to another Curriculum.",
            )
        objective_definitions_by_pair.add(
            (definition.curriculum_version_id, definition.objective_identity_id)
        )
    unit_definitions = {
        item.id: item
        for item in connection.execute(select(*LearningUnitDefinition.__table__.c)).all()
    }
    for definition in unit_definitions.values():
        identity = unit_identities.get(definition.unit_identity_id)
        version = versions.get(definition.curriculum_version_id)
        objective = objective_identities.get(definition.objective_identity_id)
        duration = (
            definition.minimum_useful_duration_ms,
            definition.preferred_duration_ms,
            definition.maximum_useful_duration_ms,
        )
        duration_valid = all(value is None for value in duration) or (
            all(type(value) is int and value > 0 and value % 300_000 == 0 for value in duration)
            and duration[0] <= duration[1] <= duration[2]
        )
        if (
            identity is None
            or version is None
            or identity.curriculum_id != version.curriculum_id
            or (objective is not None and objective.curriculum_id != version.curriculum_id)
            or (
                objective is not None
                and (version.id, objective.id) not in objective_definitions_by_pair
            )
            or definition.status not in {"active", "archived"}
            or not definition.provenance
            or not duration_valid
        ):
            raise AppError(
                422,
                "PORTABLE_CURRICULUM_INVALID",
                "A learning-unit definition has inconsistent identity or duration lineage.",
            )
    semantic_definitions = {
        item.id: item
        for item in connection.execute(select(*SemanticCompetencyDefinition.__table__.c)).all()
    }
    criteria = {
        item.id: item for item in connection.execute(select(*CriterionDefinition.__table__.c)).all()
    }
    levels = {
        item.id: item
        for item in connection.execute(select(*CapabilityScaleLevel.__table__.c)).all()
    }
    scales = {
        item.id: item
        for item in connection.execute(select(*CapabilityScaleVersion.__table__.c)).all()
    }
    dimensions = {
        item.id: item
        for item in connection.execute(select(*CapabilityScaleDimension.__table__.c)).all()
    }
    enabled_dimensions = {
        (item.semantic_definition_id, item.scale_dimension_id)
        for item in connection.execute(select(*SemanticDefinitionDimension.__table__.c)).all()
    }
    targets = connection.execute(select(*LearningUnitTarget.__table__.c)).all()
    requirements = connection.execute(select(*LearningUnitRequirement.__table__.c)).all()
    opportunities = connection.execute(select(*EvidenceOpportunityDefinition.__table__.c)).all()
    rubrics = connection.execute(select(*AssessmentRubricDefinition.__table__.c)).all()
    for target in targets:
        definition = semantic_definitions.get(target.semantic_definition_id)
        criterion = criteria.get(target.criterion_definition_id)
        minimum = levels.get(target.minimum_level_id)
        maximum = levels.get(target.maximum_level_id)
        dimension = dimensions.get(target.dimension_id)
        if (
            target.learning_unit_definition_id not in unit_definitions
            or definition is None
            or definition.scale_version_id != target.scale_version_id
            or (criterion is not None and criterion.semantic_definition_id != definition.id)
            or (target.criterion_definition_id is not None and criterion is None)
            or (minimum is not None and minimum.scale_version_id != target.scale_version_id)
            or (maximum is not None and maximum.scale_version_id != target.scale_version_id)
            or (
                target.dimension_id is not None
                and (
                    dimension is None
                    or dimension.scale_version_id != target.scale_version_id
                    or (definition.id, target.dimension_id) not in enabled_dimensions
                )
            )
            or (criterion is not None and criterion.dimension_id != target.dimension_id)
            or (target.minimum_level_id is not None and minimum is None)
            or (target.maximum_level_id is not None and maximum is None)
            or not target.intended_learning_outcome
            or (
                minimum is not None
                and maximum is not None
                and minimum.ordinal_rank > maximum.ordinal_rank
            )
        ):
            raise AppError(
                422,
                "PORTABLE_CURRICULUM_TARGET_INVALID",
                "A Curriculum target is not exact-scale compatible.",
            )
    requirement_subject_keys = {
        "capability_at_least": {
            "semanticDefinitionId",
            "scaleVersionId",
            "levelId",
            "dimensionId",
        },
        "criterion_demonstrated": {"criterionDefinitionId"},
        "learning_unit_completed": {"learningUnitStableKey"},
        "resource_available": {"resourceKey"},
        "user_constraint": {"constraintKey", "expectedValue"},
    }
    requirement_scopes = {
        "capability_at_least": "learner",
        "criterion_demonstrated": "learner",
        "learning_unit_completed": "curriculum",
        "resource_available": "environment",
        "user_constraint": "user",
    }
    hard_dependencies: dict[str, set[str]] = defaultdict(set)
    for requirement in requirements:
        subject = json.loads(requirement.subject_json)
        keys = requirement_subject_keys.get(requirement.requirement_type)
        if (
            requirement.learning_unit_definition_id not in unit_definitions
            or requirement.policy_version != "curriculum-requirement-policy/v1"
            or not isinstance(subject, dict)
            or keys is None
            or set(subject) != keys
            or requirement.scope != requirement_scopes.get(requirement.requirement_type)
        ):
            raise AppError(
                422,
                "PORTABLE_CURRICULUM_REQUIREMENT_INVALID",
                "A Curriculum requirement is malformed or uses an unknown policy.",
            )
        if requirement.requirement_type == "capability_at_least":
            semantic_definition = semantic_definitions.get(subject["semanticDefinitionId"])
            scale = scales.get(subject["scaleVersionId"])
            level = levels.get(subject["levelId"])
            dimension = dimensions.get(subject["dimensionId"])
            if (
                semantic_definition is None
                or scale is None
                or semantic_definition.scale_version_id != scale.id
                or level is None
                or level.scale_version_id != scale.id
                or (
                    subject["dimensionId"] is not None
                    and (
                        dimension is None
                        or dimension.scale_version_id != scale.id
                        or (semantic_definition.id, dimension.id) not in enabled_dimensions
                    )
                )
            ):
                raise AppError(
                    422,
                    "PORTABLE_CURRICULUM_REQUIREMENT_INVALID",
                    "A capability requirement has incompatible pinned references.",
                )
        elif requirement.requirement_type == "criterion_demonstrated":
            if criteria.get(subject["criterionDefinitionId"]) is None:
                raise AppError(
                    422,
                    "PORTABLE_CURRICULUM_REQUIREMENT_INVALID",
                    "A criterion requirement references a missing definition.",
                )
        elif requirement.requirement_type == "learning_unit_completed":
            source_unit = unit_definitions[requirement.learning_unit_definition_id]
            source_version = versions[source_unit.curriculum_version_id]
            referenced_unit = next(
                (
                    item
                    for item in unit_definitions.values()
                    if item.curriculum_version_id == source_version.id
                    and unit_identities[item.unit_identity_id].stable_key
                    == subject["learningUnitStableKey"]
                ),
                None,
            )
            if referenced_unit is None:
                raise AppError(
                    422,
                    "PORTABLE_CURRICULUM_REQUIREMENT_INVALID",
                    "A learning-unit requirement references another Curriculum or a missing unit.",
                )
            if requirement.effect == "hard":
                hard_dependencies[source_unit.id].add(referenced_unit.id)
        elif requirement.requirement_type == "resource_available":
            if not isinstance(subject["resourceKey"], str) or not subject["resourceKey"]:
                raise AppError(
                    422,
                    "PORTABLE_CURRICULUM_REQUIREMENT_INVALID",
                    "A resource requirement has an invalid stable key.",
                )
        elif requirement.requirement_type == "user_constraint" and (
            not isinstance(subject["constraintKey"], str) or not subject["constraintKey"]
        ):
            raise AppError(
                422,
                "PORTABLE_CURRICULUM_REQUIREMENT_INVALID",
                "A user-constraint requirement has an invalid stable key.",
            )
    visiting_units: set[str] = set()
    visited_units: set[str] = set()

    def visit_unit(unit_id: str) -> None:
        if unit_id in visiting_units:
            raise AppError(
                422,
                "PORTABLE_CURRICULUM_REQUIREMENT_INVALID",
                "Hard learning-unit requirements must form a DAG.",
            )
        if unit_id in visited_units:
            return
        visiting_units.add(unit_id)
        for dependency_id in sorted(hard_dependencies.get(unit_id, set())):
            visit_unit(dependency_id)
        visiting_units.remove(unit_id)
        visited_units.add(unit_id)

    for unit_id in sorted(unit_definitions):
        visit_unit(unit_id)
    for opportunity in opportunities:
        possible = json.loads(opportunity.possible_characteristics_json)
        required = json.loads(opportunity.required_characteristics_json)
        if (
            opportunity.learning_unit_definition_id not in unit_definitions
            or opportunity.policy_version != "curriculum-evidence-opportunity-policy/v1"
            or not isinstance(possible, dict)
            or set(possible) != {"intendedIndependenceModes", "intendedStrengths"}
            or not isinstance(required, dict)
            or set(required) != {"actualActivity", "artifact"}
            or opportunity.evidence_kind
            not in {
                "session",
                "verification",
                "project",
                "code",
                "assessment",
                "manual",
                "review",
            }
            or not isinstance(possible["intendedStrengths"], list)
            or any(not isinstance(item, str) for item in possible["intendedStrengths"])
            or len(possible["intendedStrengths"]) != len(set(possible["intendedStrengths"]))
            or any(
                item not in {"weak", "moderate", "strong"} for item in possible["intendedStrengths"]
            )
            or not isinstance(possible["intendedIndependenceModes"], list)
            or any(not isinstance(item, str) for item in possible["intendedIndependenceModes"])
            or len(possible["intendedIndependenceModes"])
            != len(set(possible["intendedIndependenceModes"]))
            or any(
                item not in {"guided", "assisted", "independent", "not_applicable"}
                for item in possible["intendedIndependenceModes"]
            )
            or type(required["actualActivity"]) is not bool
            or type(required["artifact"]) is not bool
        ):
            raise AppError(
                422,
                "PORTABLE_CURRICULUM_OPPORTUNITY_INVALID",
                "An Evidence opportunity is a malformed definition.",
            )
    for rubric in rubrics:
        version = versions.get(rubric.curriculum_version_id)
        identity = rubric_identities.get(rubric.rubric_identity_id)
        definition = semantic_definitions.get(rubric.semantic_definition_id)
        criterion = criteria.get(rubric.criterion_definition_id)
        if (
            version is None
            or identity is None
            or identity.curriculum_id != version.curriculum_id
            or definition is None
            or (rubric.criterion_definition_id is not None and criterion is None)
            or (criterion is not None and criterion.semantic_definition_id != definition.id)
        ):
            raise AppError(
                422,
                "PORTABLE_ASSESSMENT_RUBRIC_INVALID",
                "An assessment rubric has inconsistent exact target lineage.",
            )
    for version in versions.values():
        try:
            payload = json.loads(version.definition_payload_json)
            validated_payload = CurriculumVersionInput.model_validate(payload).model_dump(
                mode="json"
            )
            effective_at = datetime.fromisoformat(payload["effective_at"].replace("Z", "+00:00"))
            actual_objectives = sorted(
                (
                    {
                        "stable_key": objective_identities[item.objective_identity_id].stable_key,
                        "title": item.title,
                        "description": item.description,
                        "order_index": item.order_index,
                    }
                    for item in objective_definitions
                    if item.curriculum_version_id == version.id
                ),
                key=lambda item: (item["order_index"], item["stable_key"]),
            )
            actual_units: list[dict[str, Any]] = []
            for unit in sorted(
                (
                    item
                    for item in unit_definitions.values()
                    if item.curriculum_version_id == version.id
                ),
                key=lambda item: (
                    item.order_index,
                    unit_identities[item.unit_identity_id].stable_key,
                ),
            ):
                objective_key = (
                    objective_identities[unit.objective_identity_id].stable_key
                    if unit.objective_identity_id is not None
                    else None
                )
                actual_units.append(
                    {
                        "stable_key": unit_identities[unit.unit_identity_id].stable_key,
                        "objective_stable_key": objective_key,
                        "kind": unit.kind,
                        "title": unit.title,
                        "description": unit.description,
                        "action": json.loads(unit.action_payload_json),
                        "status": unit.status,
                        "provenance": unit.provenance,
                        "order_index": unit.order_index,
                        "minimum_useful_duration_ms": unit.minimum_useful_duration_ms,
                        "preferred_duration_ms": unit.preferred_duration_ms,
                        "maximum_useful_duration_ms": unit.maximum_useful_duration_ms,
                        "targets": [
                            {
                                "semantic_definition_id": item.semantic_definition_id,
                                "criterion_definition_id": item.criterion_definition_id,
                                "scale_version_id": item.scale_version_id,
                                "dimension_id": item.dimension_id,
                                "intended_learning_outcome": item.intended_learning_outcome,
                                "minimum_level_id": item.minimum_level_id,
                                "maximum_level_id": item.maximum_level_id,
                                "supports_unassessed": bool(item.supports_unassessed),
                                "role": item.role,
                                "order_index": item.order_index,
                            }
                            for item in sorted(
                                (
                                    item
                                    for item in targets
                                    if item.learning_unit_definition_id == unit.id
                                ),
                                key=lambda item: (item.order_index, item.semantic_definition_id),
                            )
                        ],
                        "requirements": [
                            {
                                "stable_key": item.stable_key,
                                "requirement_type": item.requirement_type,
                                "effect": item.effect,
                                "scope": item.scope,
                                "subject": json.loads(item.subject_json),
                                "order_index": item.order_index,
                            }
                            for item in sorted(
                                (
                                    item
                                    for item in requirements
                                    if item.learning_unit_definition_id == unit.id
                                ),
                                key=lambda item: (item.order_index, item.stable_key),
                            )
                        ],
                        "evidence_opportunities": [
                            {
                                "stable_key": item.stable_key,
                                "evidence_kind": item.evidence_kind,
                                "intended_strengths": json.loads(
                                    item.possible_characteristics_json
                                )["intendedStrengths"],
                                "intended_independence_modes": json.loads(
                                    item.possible_characteristics_json
                                )["intendedIndependenceModes"],
                                "requires_actual_activity": json.loads(
                                    item.required_characteristics_json
                                )["actualActivity"],
                                "requires_artifact": json.loads(item.required_characteristics_json)[
                                    "artifact"
                                ],
                                "order_index": item.order_index,
                            }
                            for item in sorted(
                                (
                                    item
                                    for item in opportunities
                                    if item.learning_unit_definition_id == unit.id
                                ),
                                key=lambda item: (item.order_index, item.stable_key),
                            )
                        ],
                    }
                )
            actual_rubrics = sorted(
                (
                    {
                        "stable_key": rubric_identities[item.rubric_identity_id].stable_key,
                        "title": item.title,
                        "instructions": item.instructions,
                        "rubric": json.loads(item.rubric_json),
                        "semantic_definition_id": item.semantic_definition_id,
                        "criterion_definition_id": item.criterion_definition_id,
                    }
                    for item in rubrics
                    if item.curriculum_version_id == version.id
                ),
                key=lambda item: item["stable_key"],
            )
            parity_matches = (
                payload == validated_payload
                and payload["title"] == version.title
                and payload["description"] == version.description
                and payload["creation_source"] == version.creation_source
                and datetime_to_epoch_ms(effective_at) == version.effective_at
                and payload["objectives"] == actual_objectives
                and payload["units"] == actual_units
                and payload["assessment_rubrics"] == actual_rubrics
            )
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise AppError(
                422,
                "PORTABLE_CURRICULUM_INVALID",
                "A Curriculum version definition envelope is malformed.",
            ) from exc
        if not parity_matches:
            raise AppError(
                422,
                "PORTABLE_CURRICULUM_INVALID",
                "A Curriculum version definition envelope does not match its immutable rows.",
            )
    definitions_by_unit_identity: dict[str, list[Any]] = defaultdict(list)
    for unit in unit_definitions.values():
        definitions_by_unit_identity[unit.unit_identity_id].append(unit)
    for identity_id, items in definitions_by_unit_identity.items():
        signatures: set[tuple[str, tuple[tuple[str, str | None], ...]]] = set()
        for unit in items:
            primary_targets = []
            for target in targets:
                if target.learning_unit_definition_id != unit.id or target.role != "primary":
                    continue
                definition = semantic_definitions[target.semantic_definition_id]
                criterion = criteria.get(target.criterion_definition_id)
                primary_targets.append(
                    (
                        definition.competency_identity_id,
                        criterion.criterion_identity_id if criterion is not None else None,
                    )
                )
            signatures.add(
                (
                    unit.kind,
                    tuple(sorted(primary_targets, key=lambda item: (item[0], item[1] or ""))),
                )
            )
        if len(signatures) != 1:
            raise AppError(
                422,
                "PORTABLE_CURRICULUM_INVALID",
                "A stable learning-unit identity changes kind or primary semantic meaning.",
                {"unitIdentityId": identity_id},
            )
    rubrics_by_identity: dict[str, set[tuple[str, str | None]]] = defaultdict(set)
    for rubric in rubrics:
        definition = semantic_definitions[rubric.semantic_definition_id]
        criterion = criteria.get(rubric.criterion_definition_id)
        rubrics_by_identity[rubric.rubric_identity_id].add(
            (
                definition.competency_identity_id,
                criterion.criterion_identity_id if criterion is not None else None,
            )
        )
    if any(len(signatures) != 1 for signatures in rubrics_by_identity.values()):
        raise AppError(
            422,
            "PORTABLE_ASSESSMENT_RUBRIC_INVALID",
            "A stable rubric identity changes semantic meaning.",
        )
    events_by_curriculum: dict[str, list[Any]] = defaultdict(list)
    for event in connection.execute(select(*CurriculumActivationEvent.__table__.c)).all():
        version = versions.get(event.to_curriculum_version_id)
        previous = versions.get(event.from_curriculum_version_id)
        if (
            version is None
            or version.curriculum_id != event.curriculum_id
            or curricula[event.curriculum_id].created_at > event.activated_at
            or version.created_at > event.activated_at
            or (previous is not None and previous.curriculum_id != event.curriculum_id)
            or (event.from_curriculum_version_id is not None and previous is None)
            or version.effective_at > event.activated_at
            or (previous is not None and previous.version >= version.version)
        ):
            raise AppError(
                422,
                "PORTABLE_CURRICULUM_ACTIVATION_INVALID",
                "Curriculum activation history is inconsistent.",
            )
        events_by_curriculum[event.curriculum_id].append(event)
    states = {
        item.curriculum_id: item
        for item in connection.execute(select(*ActiveCurriculumVersionState.__table__.c)).all()
    }
    for state in states.values():
        version = versions.get(state.curriculum_version_id)
        events = events_by_curriculum.get(state.curriculum_id, [])
        latest = max(
            events, key=lambda item: (item.activated_at, item.event_sequence, item.id), default=None
        )
        if (
            version is None
            or version.curriculum_id != state.curriculum_id
            or latest is None
            or latest.to_curriculum_version_id != state.curriculum_version_id
            or latest.activated_at != state.activated_at
        ):
            raise AppError(
                422,
                "PORTABLE_CURRICULUM_ACTIVATION_INVALID",
                "Current Curriculum state does not match immutable activation history.",
            )
    for _curriculum_id, events in events_by_curriculum.items():
        ordered = sorted(events, key=lambda item: item.event_sequence)
        if [item.event_sequence for item in ordered] != list(range(1, len(ordered) + 1)):
            raise AppError(
                422,
                "PORTABLE_CURRICULUM_ACTIVATION_INVALID",
                "Curriculum activation sequences must be contiguous per aggregate.",
            )
        if _curriculum_id not in states:
            raise AppError(
                422,
                "PORTABLE_CURRICULUM_ACTIVATION_INVALID",
                "Every activation chain must have a current-state row.",
            )
        previous_id = None
        previous_activated_at: int | None = None
        for event in ordered:
            if event.from_curriculum_version_id != previous_id or (
                previous_activated_at is not None and event.activated_at < previous_activated_at
            ):
                raise AppError(
                    422,
                    "PORTABLE_CURRICULUM_ACTIVATION_INVALID",
                    "Curriculum activation lineage must form one ordered chain.",
                )
            previous_id = event.to_curriculum_version_id
            previous_activated_at = event.activated_at
    links = {
        item.id: item
        for item in connection.execute(select(*ActivityCurriculumUnitLink.__table__.c)).all()
    }
    activities = {item.id: item for item in connection.execute(select(*Activity.__table__.c)).all()}
    for item in links.values():
        activity = activities.get(item.activity_id)
        unit = unit_definitions.get(item.learning_unit_definition_id)
        version = versions.get(unit.curriculum_version_id) if unit is not None else None
        if (
            item.provenance not in {"user_selected", "user_confirmed", "imported_asserted"}
            or activity is None
            or version is None
            or item.created_at < activity.created_at
            or item.created_at < version.created_at
        ):
            raise AppError(
                422,
                "PORTABLE_CURRICULUM_LINK_INVALID",
                "A Curriculum Activity link has invalid provenance or chronology.",
            )
    replacement_edges: dict[str, str] = {}
    for correction in connection.execute(
        select(*ActivityCurriculumLinkCorrection.__table__.c)
    ).all():
        original = links.get(correction.activity_curriculum_unit_link_id)
        replacement = links.get(correction.replacement_link_id)
        if (
            original is None
            or correction.replacement_link_id == correction.activity_curriculum_unit_link_id
            or (
                correction.replacement_link_id is not None
                and (
                    replacement is None
                    or replacement.activity_id != original.activity_id
                    or replacement.created_at > correction.corrected_at
                )
            )
            or (original is not None and correction.corrected_at < original.created_at)
        ):
            raise AppError(
                422,
                "PORTABLE_CURRICULUM_LINK_INVALID",
                "A Curriculum Activity-link correction is inconsistent.",
            )
        if correction.replacement_link_id is not None:
            replacement_edges[correction.activity_curriculum_unit_link_id] = (
                correction.replacement_link_id
            )
    for link_id in replacement_edges:
        seen: set[str] = set()
        current: str | None = link_id
        while current is not None:
            if current in seen:
                raise AppError(
                    422,
                    "PORTABLE_CURRICULUM_LINK_INVALID",
                    "Curriculum Activity-link corrections must not form cycles.",
                )
            seen.add(current)
            current = replacement_edges.get(current)


def _validate_projects(connection: Any) -> None:
    projects = {item.id: item for item in connection.execute(select(*Project.__table__.c)).all()}
    versions = {
        item.id: item for item in connection.execute(select(*ProjectVersion.__table__.c)).all()
    }
    normalized_by_version: dict[str, dict[str, Any]] = {}
    identities_by_kind = {
        "goal": {
            item.id: item
            for item in connection.execute(select(*ProjectGoalIdentity.__table__.c)).all()
        },
        "task": {
            item.id: item
            for item in connection.execute(select(*ProjectTaskIdentity.__table__.c)).all()
        },
        "criterion": {
            item.id: item
            for item in connection.execute(select(*ProjectCriterionIdentity.__table__.c)).all()
        },
        "milestone": {
            item.id: item
            for item in connection.execute(select(*ProjectMilestoneIdentity.__table__.c)).all()
        },
    }
    grouped_versions: dict[str, list[Any]] = defaultdict(list)
    for version in versions.values():
        grouped_versions[version.project_id].append(version)
        try:
            parsed = ProjectVersionInput.model_validate(json.loads(version.definition_payload_json))
        except Exception as exc:
            raise AppError(
                422,
                "PORTABLE_PROJECT_INVALID",
                "A Project definition envelope is malformed.",
            ) from exc
        normalized = parsed.model_dump(mode="json")
        for key in (
            "goals",
            "milestones",
            "tasks",
            "criteria",
            "targets",
            "requirements",
            "evidence_opportunities",
        ):
            normalized[key] = sorted(
                normalized[key], key=lambda item: (item["order_index"], item.get("stable_key", ""))
            )
        for opportunity in normalized["evidence_opportunities"]:
            opportunity["intended_strengths"] = sorted(opportunity["intended_strengths"])
            opportunity["intended_independence_modes"] = sorted(
                opportunity["intended_independence_modes"]
            )
        normalized["dependencies"] = sorted(
            normalized["dependencies"],
            key=lambda item: (
                item["dependent_task_stable_key"],
                item["prerequisite_task_stable_key"],
                item["dependency_type"],
            ),
        )
        normalized_by_version[version.id] = normalized
        if (
            version.project_id not in projects
            or version.schema_version != "project-definition/v1"
            or version.content_hash != content_hash(normalized)
            or json.loads(version.definition_payload_json) != normalized
            or version.title != normalized["title"]
            or version.description != normalized["description"]
            or version.effective_at != datetime_to_epoch_ms(parsed.effective_at)
            or version.creation_source != normalized["creation_source"]
            or version.created_at < projects[version.project_id].created_at
        ):
            raise AppError(
                422,
                "PORTABLE_PROJECT_INVALID",
                "Project version lineage or content hashing is inconsistent.",
            )
    for _project_id, items in grouped_versions.items():
        ordered = sorted(items, key=lambda item: item.version)
        if [item.version for item in ordered] != list(range(1, len(ordered) + 1)):
            raise AppError(
                422,
                "PORTABLE_PROJECT_INVALID",
                "Project versions must be contiguous per aggregate.",
            )
        for index, item in enumerate(ordered):
            expected = ordered[index - 1].id if index else None
            if item.supersedes_version_id != expected:
                raise AppError(
                    422,
                    "PORTABLE_PROJECT_INVALID",
                    "Project version supersession lineage is inconsistent.",
                )
    for values in identities_by_kind.values():
        for identity in values.values():
            if (
                identity.project_id not in projects
                or identity.created_at < projects[identity.project_id].created_at
            ):
                raise AppError(
                    422,
                    "PORTABLE_PROJECT_INVALID",
                    "A Project stable identity has invalid ownership or chronology.",
                )
    task_defs = {
        item.id: item
        for item in connection.execute(select(*ProjectTaskDefinition.__table__.c)).all()
    }
    criterion_defs = {
        item.id: item
        for item in connection.execute(select(*ProjectCriterionDefinition.__table__.c)).all()
    }
    goal_defs = list(connection.execute(select(*ProjectGoalDefinition.__table__.c)).all())
    milestone_defs = list(connection.execute(select(*ProjectMilestoneDefinition.__table__.c)).all())
    for model, identity_kind, identity_column in (
        (ProjectGoalDefinition, "goal", "goal_identity_id"),
        (ProjectMilestoneDefinition, "milestone", "milestone_identity_id"),
        (ProjectTaskDefinition, "task", "task_identity_id"),
        (ProjectCriterionDefinition, "criterion", "criterion_identity_id"),
    ):
        for definition in connection.execute(select(*model.__table__.c)):
            version = versions.get(definition.project_version_id)
            identity = identities_by_kind[identity_kind].get(getattr(definition, identity_column))
            if version is None or identity is None or identity.project_id != version.project_id:
                raise AppError(
                    422,
                    "PORTABLE_PROJECT_INVALID",
                    "A Project definition crosses aggregate boundaries.",
                )
            milestone_id = getattr(definition, "milestone_identity_id", None)
            if milestone_id is not None:
                milestone_identity = identities_by_kind["milestone"].get(milestone_id)
                if (
                    milestone_identity is None
                    or milestone_identity.project_id != version.project_id
                ):
                    raise AppError(
                        422,
                        "PORTABLE_PROJECT_INVALID",
                        "A Project definition references another aggregate's milestone.",
                    )
            if (
                model is ProjectCriterionDefinition
                and definition.evaluation_policy_version != PROJECT_CRITERION_POLICY
            ):
                raise AppError(
                    422,
                    "PORTABLE_PROJECT_INVALID",
                    "A ProjectCriterion declares an unsupported evaluation policy.",
                )
    targets = list(connection.execute(select(*ProjectTarget.__table__.c)).all())
    requirements = list(connection.execute(select(*ProjectRequirement.__table__.c)).all())
    dependencies = list(connection.execute(select(*ProjectTaskDependency.__table__.c)).all())
    opportunities = list(connection.execute(select(*ProjectEvidenceOpportunity.__table__.c)).all())
    opportunity_characteristics: dict[str, dict[str, Any]] = {}
    for item in opportunities:
        try:
            parsed_characteristics = json.loads(item.intended_characteristics_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AppError(
                422,
                "PORTABLE_PROJECT_INVALID",
                "A Project Evidence opportunity has malformed characteristics.",
            ) from exc
        if (
            not isinstance(parsed_characteristics, dict)
            or set(parsed_characteristics)
            != {
                "intended_strengths",
                "intended_independence_modes",
                "requires_artifact",
            }
            or not isinstance(parsed_characteristics["intended_strengths"], list)
            or not isinstance(parsed_characteristics["intended_independence_modes"], list)
            or not isinstance(parsed_characteristics["requires_artifact"], bool)
        ):
            raise AppError(
                422,
                "PORTABLE_PROJECT_INVALID",
                "A Project Evidence opportunity has malformed characteristics.",
            )
        opportunity_characteristics[item.id] = parsed_characteristics

    def stable_key(kind: str, identity_id: str | None) -> str | None:
        if identity_id is None:
            return None
        identity = identities_by_kind[kind].get(identity_id)
        return identity.stable_key if identity is not None else None

    for version_id, expected in normalized_by_version.items():
        actual_goals = sorted(
            [
                {
                    "stable_key": stable_key("goal", item.goal_identity_id),
                    "title": item.title,
                    "description": item.description,
                    "order_index": item.order_index,
                }
                for item in goal_defs
                if item.project_version_id == version_id
            ],
            key=lambda item: (item["order_index"], item["stable_key"] or ""),
        )
        actual_milestones = sorted(
            [
                {
                    "stable_key": stable_key("milestone", item.milestone_identity_id),
                    "title": item.title,
                    "description": item.description,
                    "order_index": item.order_index,
                }
                for item in milestone_defs
                if item.project_version_id == version_id
            ],
            key=lambda item: (item["order_index"], item["stable_key"] or ""),
        )
        actual_tasks = sorted(
            [
                {
                    "stable_key": stable_key("task", item.task_identity_id),
                    "title": item.title,
                    "description": item.description,
                    "order_index": item.order_index,
                    "milestone_stable_key": stable_key("milestone", item.milestone_identity_id),
                    "instructions": item.instructions,
                    "status": item.status,
                    "minimum_useful_duration_ms": item.minimum_useful_duration_ms,
                    "preferred_duration_ms": item.preferred_duration_ms,
                    "maximum_useful_duration_ms": item.maximum_useful_duration_ms,
                }
                for item in task_defs.values()
                if item.project_version_id == version_id
            ],
            key=lambda item: (item["order_index"], item["stable_key"] or ""),
        )
        actual_criteria = sorted(
            [
                {
                    "stable_key": stable_key("criterion", item.criterion_identity_id),
                    "title": item.title,
                    "description": item.description,
                    "order_index": item.order_index,
                    "milestone_stable_key": stable_key("milestone", item.milestone_identity_id),
                    "evaluation_policy_version": item.evaluation_policy_version,
                }
                for item in criterion_defs.values()
                if item.project_version_id == version_id
            ],
            key=lambda item: (item["order_index"], item["stable_key"] or ""),
        )
        actual_targets = sorted(
            [
                {
                    "task_stable_key": stable_key(
                        "task",
                        task_defs[item.task_definition_id].task_identity_id
                        if item.task_definition_id in task_defs
                        else None,
                    ),
                    "project_criterion_stable_key": stable_key(
                        "criterion",
                        criterion_defs[item.project_criterion_definition_id].criterion_identity_id
                        if item.project_criterion_definition_id in criterion_defs
                        else None,
                    ),
                    "semantic_definition_id": item.semantic_definition_id,
                    "criterion_definition_id": item.criterion_definition_id,
                    "scale_version_id": item.scale_version_id,
                    "dimension_id": item.dimension_id,
                    "level_id": item.level_id,
                    "intended_outcome": item.intended_outcome,
                    "role": item.role,
                    "order_index": item.order_index,
                }
                for item in targets
                if item.project_version_id == version_id
            ],
            key=lambda item: (item["order_index"], item["task_stable_key"] or ""),
        )
        actual_requirements = sorted(
            [
                {
                    "task_stable_key": stable_key(
                        "task",
                        task_defs[item.task_definition_id].task_identity_id
                        if item.task_definition_id in task_defs
                        else None,
                    ),
                    "stable_key": item.stable_key,
                    "requirement_type": item.requirement_type,
                    "effect": item.effect,
                    "scope": item.scope,
                    "subject": json.loads(item.subject_json),
                    "order_index": item.order_index,
                }
                for item in requirements
                if item.project_version_id == version_id
            ],
            key=lambda item: (item["order_index"], item["stable_key"]),
        )
        actual_dependencies = sorted(
            [
                {
                    "dependent_task_stable_key": stable_key(
                        "task", item.dependent_task_identity_id
                    ),
                    "prerequisite_task_stable_key": stable_key(
                        "task", item.prerequisite_task_identity_id
                    ),
                    "dependency_type": item.dependency_type,
                }
                for item in dependencies
                if item.project_version_id == version_id
            ],
            key=lambda item: (
                item["dependent_task_stable_key"] or "",
                item["prerequisite_task_stable_key"] or "",
                item["dependency_type"],
            ),
        )
        actual_opportunities = sorted(
            [
                {
                    "stable_key": item.stable_key,
                    "task_stable_key": stable_key(
                        "task",
                        task_defs[item.task_definition_id].task_identity_id
                        if item.task_definition_id in task_defs
                        else None,
                    ),
                    "project_criterion_stable_key": stable_key(
                        "criterion",
                        criterion_defs[item.project_criterion_definition_id].criterion_identity_id
                        if item.project_criterion_definition_id in criterion_defs
                        else None,
                    ),
                    "evidence_kind": item.evidence_kind,
                    "intended_strengths": opportunity_characteristics[item.id][
                        "intended_strengths"
                    ],
                    "intended_independence_modes": opportunity_characteristics[item.id][
                        "intended_independence_modes"
                    ],
                    "requires_artifact": opportunity_characteristics[item.id]["requires_artifact"],
                    "order_index": item.order_index,
                }
                for item in opportunities
                if item.project_version_id == version_id
            ],
            key=lambda item: (item["order_index"], item["stable_key"]),
        )
        if any(
            actual != expected[name]
            for name, actual in (
                ("goals", actual_goals),
                ("milestones", actual_milestones),
                ("tasks", actual_tasks),
                ("criteria", actual_criteria),
                ("targets", actual_targets),
                ("requirements", actual_requirements),
                ("dependencies", actual_dependencies),
                ("evidence_opportunities", actual_opportunities),
            )
        ):
            raise AppError(
                422,
                "PORTABLE_PROJECT_INVALID",
                "Project child definitions do not match their immutable definition envelope.",
            )
    semantic_defs = {
        item.id: item
        for item in connection.execute(select(*SemanticCompetencyDefinition.__table__.c)).all()
    }
    native_criterion_defs = {
        item.id: item for item in connection.execute(select(*CriterionDefinition.__table__.c)).all()
    }
    scale_dimensions = {
        item.id: item
        for item in connection.execute(select(*CapabilityScaleDimension.__table__.c)).all()
    }
    scale_levels = {
        item.id: item
        for item in connection.execute(select(*CapabilityScaleLevel.__table__.c)).all()
    }
    enabled_dimensions = {
        (item.semantic_definition_id, item.scale_dimension_id)
        for item in connection.execute(select(*SemanticDefinitionDimension.__table__.c)).all()
    }
    for target in targets:
        version = versions.get(target.project_version_id)
        task = task_defs.get(target.task_definition_id) if target.task_definition_id else None
        criterion = (
            criterion_defs.get(target.project_criterion_definition_id)
            if target.project_criterion_definition_id
            else None
        )
        semantic = semantic_defs.get(target.semantic_definition_id)
        native_criterion = (
            native_criterion_defs.get(target.criterion_definition_id)
            if target.criterion_definition_id
            else None
        )
        dimension = scale_dimensions.get(target.dimension_id) if target.dimension_id else None
        level = scale_levels.get(target.level_id) if target.level_id else None
        if (
            version is None
            or (task is not None and task.project_version_id != version.id)
            or (criterion is not None and criterion.project_version_id != version.id)
            or (target.task_definition_id is not None and task is None)
            or (target.project_criterion_definition_id is not None and criterion is None)
            or semantic is None
            or semantic.scale_version_id != target.scale_version_id
            or (
                native_criterion is not None
                and native_criterion.semantic_definition_id != semantic.id
            )
            or (
                native_criterion is not None
                and native_criterion.dimension_id != target.dimension_id
            )
            or (native_criterion is not None and native_criterion.level_id != target.level_id)
            or (target.criterion_definition_id is not None and native_criterion is None)
            or (
                dimension is not None
                and (
                    dimension.scale_version_id != target.scale_version_id
                    or (semantic.id, dimension.id) not in enabled_dimensions
                )
            )
            or (target.dimension_id is not None and dimension is None)
            or (level is not None and level.scale_version_id != target.scale_version_id)
            or (target.level_id is not None and level is None)
        ):
            raise AppError(
                422,
                "PORTABLE_PROJECT_INVALID",
                "A Project target crosses definition versions.",
            )
    primary_global_targets: dict[str, list[Any]] = defaultdict(list)
    primary_task_targets: dict[str, list[Any]] = defaultdict(list)
    for target in targets:
        if target.role != "primary":
            continue
        if target.task_definition_id is not None:
            primary_task_targets[target.task_definition_id].append(target)
        elif target.project_criterion_definition_id is None:
            primary_global_targets[target.project_version_id].append(target)

    def stored_target_signature(items: list[Any]) -> set[tuple[Any, ...]]:
        return {
            (
                item.semantic_definition_id,
                item.criterion_definition_id,
                item.scale_version_id,
                item.dimension_id,
                item.level_id,
            )
            for item in items
        }

    definitions_by_task_identity: dict[str, list[Any]] = defaultdict(list)
    for task in task_defs.values():
        definitions_by_task_identity[task.task_identity_id].append(task)
    for items in definitions_by_task_identity.values():
        ordered = sorted(items, key=lambda item: versions[item.project_version_id].version)
        prior_signature: set[tuple[Any, ...]] | None = None
        for item in ordered:
            signature = stored_target_signature(
                primary_task_targets[item.id] + primary_global_targets[item.project_version_id]
            )
            if prior_signature is not None and signature != prior_signature:
                raise AppError(
                    422,
                    "PORTABLE_PROJECT_INVALID",
                    "A stable Project task identity changes its Primary semantic target signature.",
                )
            prior_signature = signature
    hard_edges_by_version: dict[str, dict[str, set[str]]] = defaultdict(dict)
    task_ids_by_version: dict[str, set[str]] = defaultdict(set)
    for task in task_defs.values():
        task_ids_by_version[task.project_version_id].add(task.task_identity_id)
        hard_edges_by_version[task.project_version_id].setdefault(task.task_identity_id, set())
    for dependency in dependencies:
        if (
            dependency.dependent_task_identity_id
            not in task_ids_by_version[dependency.project_version_id]
            or dependency.prerequisite_task_identity_id
            not in task_ids_by_version[dependency.project_version_id]
        ):
            raise AppError(
                422,
                "PORTABLE_PROJECT_INVALID",
                "A Project dependency crosses definition versions.",
            )
        if dependency.dependency_type == "hard":
            hard_edges_by_version[dependency.project_version_id].setdefault(
                dependency.dependent_task_identity_id, set()
            ).add(dependency.prerequisite_task_identity_id)
    for version_id in task_ids_by_version:
        if has_required_dependency_cycle(hard_edges_by_version[version_id]):
            raise AppError(
                422,
                "PORTABLE_PROJECT_INVALID",
                "Hard Project task dependencies contain a cycle.",
            )
    requirement_subject_keys = {
        "capability_at_least": {
            "semanticDefinitionId",
            "scaleVersionId",
            "dimensionId",
            "levelId",
        },
        "criterion_demonstrated": {"criterionDefinitionId"},
        "project_criterion_demonstrated": {"projectCriterionStableKey"},
        "project_task_completed": {"taskStableKey"},
        "resource_available": {"resourceKey"},
        "user_constraint": {"constraintKey", "expectedValue"},
    }
    requirement_scopes = {
        "capability_at_least": "learner",
        "criterion_demonstrated": "learner",
        "project_criterion_demonstrated": "project",
        "project_task_completed": "project",
        "resource_available": "environment",
        "user_constraint": "user",
    }
    for requirement in requirements:
        subject = json.loads(requirement.subject_json)
        version = versions.get(requirement.project_version_id)
        project_id = version.project_id if version is not None else None
        version_task_keys = {
            stable_key("task", item.task_identity_id)
            for item in task_defs.values()
            if item.project_version_id == requirement.project_version_id
        }
        version_criterion_keys = {
            stable_key("criterion", item.criterion_identity_id)
            for item in criterion_defs.values()
            if item.project_version_id == requirement.project_version_id
        }
        invalid = (
            requirement.policy_version != "project-requirement-policy/v1"
            or requirement.requirement_type not in requirement_subject_keys
            or not isinstance(subject, dict)
            or set(subject) != requirement_subject_keys.get(requirement.requirement_type, set())
            or requirement.scope != requirement_scopes.get(requirement.requirement_type)
        )
        if requirement.task_definition_id is not None and (
            requirement.task_definition_id not in task_defs
            or task_defs[requirement.task_definition_id].project_version_id
            != requirement.project_version_id
        ):
            invalid = True
        if not invalid and requirement.requirement_type == "project_task_completed":
            invalid = subject["taskStableKey"] not in version_task_keys
        if not invalid and requirement.requirement_type == "project_criterion_demonstrated":
            invalid = subject["projectCriterionStableKey"] not in version_criterion_keys
        if not invalid and requirement.requirement_type == "criterion_demonstrated":
            invalid = subject["criterionDefinitionId"] not in native_criterion_defs
        if not invalid and requirement.requirement_type == "resource_available":
            invalid = not isinstance(subject["resourceKey"], str) or not subject["resourceKey"]
        if not invalid and requirement.requirement_type == "user_constraint":
            invalid = (
                not isinstance(subject["constraintKey"], str)
                or not subject["constraintKey"]
                or subject["expectedValue"] is None
            )
        if not invalid and requirement.requirement_type == "capability_at_least":
            semantic = semantic_defs.get(subject["semanticDefinitionId"])
            dimension = (
                scale_dimensions.get(subject["dimensionId"])
                if subject["dimensionId"] is not None
                else None
            )
            level = scale_levels.get(subject["levelId"])
            invalid = (
                semantic is None
                or semantic.scale_version_id != subject["scaleVersionId"]
                or level is None
                or level.scale_version_id != subject["scaleVersionId"]
                or (
                    subject["dimensionId"] is not None
                    and (
                        dimension is None
                        or dimension.scale_version_id != subject["scaleVersionId"]
                        or (semantic.id, dimension.id) not in enabled_dimensions
                    )
                )
            )
        if invalid or project_id is None:
            raise AppError(
                422,
                "PORTABLE_PROJECT_INVALID",
                "A Project requirement is malformed or crosses definition versions.",
            )
    for opportunity in opportunities:
        task = (
            task_defs.get(opportunity.task_definition_id)
            if opportunity.task_definition_id
            else None
        )
        criterion = (
            criterion_defs.get(opportunity.project_criterion_definition_id)
            if opportunity.project_criterion_definition_id
            else None
        )
        if (
            (task is None and criterion is None)
            or (task is not None and task.project_version_id != opportunity.project_version_id)
            or (
                criterion is not None
                and criterion.project_version_id != opportunity.project_version_id
            )
            or opportunity.policy_version != "project-evidence-opportunity-policy/v1"
            or set(json.loads(opportunity.intended_characteristics_json))
            != {
                "intended_strengths",
                "intended_independence_modes",
                "requires_artifact",
            }
        ):
            raise AppError(
                422,
                "PORTABLE_PROJECT_INVALID",
                "A Project Evidence opportunity has invalid ownership.",
            )
    activation_events: dict[str, list[Any]] = defaultdict(list)
    for item in connection.execute(select(*ProjectVersionActivationEvent.__table__.c)):
        version = versions.get(item.to_project_version_id)
        previous = (
            versions.get(item.from_project_version_id) if item.from_project_version_id else None
        )
        if (
            version is None
            or version.project_id != item.project_id
            or (previous is not None and previous.project_id != item.project_id)
            or item.activated_at < version.created_at
            or item.activated_at < version.effective_at
        ):
            raise AppError(
                422,
                "PORTABLE_PROJECT_INVALID",
                "Project activation history is inconsistent.",
            )
        activation_events[item.project_id].append(item)
    latest_activation: dict[str, Any] = {}
    for project_id, items in activation_events.items():
        ordered = sorted(items, key=lambda item: item.event_sequence)
        if [item.event_sequence for item in ordered] != list(range(1, len(ordered) + 1)):
            raise AppError(
                422,
                "PORTABLE_PROJECT_INVALID",
                "Project activation sequences must be contiguous.",
            )
        previous_id = None
        activation_previous_time: int | None = None
        for item in ordered:
            if item.from_project_version_id != previous_id or (
                activation_previous_time is not None
                and item.activated_at < activation_previous_time
            ):
                raise AppError(
                    422,
                    "PORTABLE_PROJECT_INVALID",
                    "Project activation lineage is disconnected.",
                )
            previous_id = item.to_project_version_id
            activation_previous_time = item.activated_at
        latest_activation[project_id] = ordered[-1]
    states = {
        item.project_id: item
        for item in connection.execute(select(*ActiveProjectVersionState.__table__.c)).all()
    }
    if set(states) != set(latest_activation) or any(
        state.project_version_id != latest_activation[project_id].to_project_version_id
        or state.activated_at != latest_activation[project_id].activated_at
        for project_id, state in states.items()
    ):
        raise AppError(
            422,
            "PORTABLE_PROJECT_INVALID",
            "Current Project version state disagrees with activation history.",
        )
    events_by_project: dict[str, list[Any]] = defaultdict(list)
    event_ids: dict[str, Any] = {}
    for item in connection.execute(select(*ProjectEvent.__table__.c)):
        event_ids[item.id] = item
        version = versions.get(item.project_version_id)
        task = (
            identities_by_kind["task"].get(item.task_identity_id) if item.task_identity_id else None
        )
        if (
            version is None
            or version.project_id != item.project_id
            or item.occurred_at < version.created_at
            or (task is not None and task.project_id != item.project_id)
            or (item.task_identity_id is not None and task is None)
            or (
                item.task_identity_id is not None
                and item.task_identity_id not in task_ids_by_version[version.id]
            )
        ):
            raise AppError(
                422,
                "PORTABLE_PROJECT_INVALID",
                "A Project event has invalid ownership or chronology.",
            )
        events_by_project[item.project_id].append(item)
    for project_id, items in events_by_project.items():
        ordered = sorted(items, key=lambda item: item.event_sequence)
        if [item.event_sequence for item in ordered] != list(range(1, len(ordered) + 1)):
            raise AppError(
                422,
                "PORTABLE_PROJECT_INVALID",
                "Project event sequences must be contiguous.",
            )
        corrected_by: set[str] = set()
        lifecycle_state = "planned"
        task_states: dict[str, str] = defaultdict(lambda: "not_started")
        open_blockers: set[tuple[str, str]] = set()
        event_previous_time: int | None = None
        for item in ordered:
            try:
                details = json.loads(item.payload_json)
            except (TypeError, json.JSONDecodeError) as exc:
                raise AppError(
                    422, "PORTABLE_PROJECT_INVALID", "A Project event payload is malformed."
                ) from exc
            if not isinstance(details, dict) or (
                event_previous_time is not None and item.occurred_at < event_previous_time
            ):
                raise AppError(
                    422,
                    "PORTABLE_PROJECT_INVALID",
                    "Project event knowledge time is not monotonic.",
                )
            event_previous_time = item.occurred_at
            if item.corrects_event_id is not None:
                corrected = event_ids.get(item.corrects_event_id)
                if (
                    item.event_type != "blocker_corrected"
                    or corrected is None
                    or corrected.project_id != project_id
                    or corrected.event_sequence >= item.event_sequence
                    or corrected.id in corrected_by
                    or corrected.event_type not in {"blocker_opened", "blocker_corrected"}
                    or corrected.task_identity_id != item.task_identity_id
                    or corrected.blocker_key != item.blocker_key
                    or item.task_identity_id is None
                    or item.blocker_key is None
                    or item.blocker_actionable is None
                    or item.project_lifecycle_state is not None
                    or item.task_lifecycle_state is not None
                ):
                    raise AppError(
                        422,
                        "PORTABLE_PROJECT_INVALID",
                        "A Project event correction is inconsistent.",
                    )
                correction_body = {
                    "corrects_event_id": item.corrects_event_id,
                    "blocker_key": item.blocker_key,
                    "actionable": item.blocker_actionable,
                    "details": details,
                    "source": item.source,
                }
                if item.command_hash != content_hash(correction_body):
                    raise AppError(
                        422,
                        "PORTABLE_PROJECT_INVALID",
                        "A Project event command hash is inconsistent.",
                    )
                blocker = (item.task_identity_id, item.blocker_key)
                if blocker not in open_blockers:
                    raise AppError(
                        422,
                        "PORTABLE_PROJECT_INVALID",
                        "A Project blocker correction does not target an open blocker.",
                    )
                corrected_by.add(corrected.id)
                continue
            event_body = {
                "event_type": item.event_type,
                "project_lifecycle_state": item.project_lifecycle_state,
                "task_identity_id": item.task_identity_id,
                "task_lifecycle_state": item.task_lifecycle_state,
                "blocker_key": item.blocker_key,
                "blocker_actionable": item.blocker_actionable,
                "details": details,
                "source": item.source,
            }
            if item.command_hash != content_hash(event_body):
                raise AppError(
                    422,
                    "PORTABLE_PROJECT_INVALID",
                    "A Project event command hash is inconsistent.",
                )
            if item.event_type == "project_lifecycle":
                allowed = {
                    "planned": {"active", "archived"},
                    "active": {"completed", "archived"},
                    "completed": {"archived"},
                    "archived": set(),
                }
                next_state = item.project_lifecycle_state
                if (
                    next_state is None
                    or item.task_identity_id is not None
                    or item.task_lifecycle_state is not None
                    or item.blocker_key is not None
                    or item.blocker_actionable is not None
                    or (
                        next_state != lifecycle_state and next_state not in allowed[lifecycle_state]
                    )
                ):
                    raise AppError(
                        422,
                        "PORTABLE_PROJECT_INVALID",
                        "A Project lifecycle event is invalid.",
                    )
                lifecycle_state = next_state
            elif item.event_type == "task_lifecycle":
                if item.task_identity_id is None or item.task_lifecycle_state is None:
                    raise AppError(
                        422, "PORTABLE_PROJECT_INVALID", "A task lifecycle event is invalid."
                    )
                current = task_states[item.task_identity_id]
                allowed = {
                    "not_started": {"started", "cancelled"},
                    "started": {"completed", "cancelled"},
                    "completed": set(),
                    "cancelled": set(),
                }
                if (
                    item.project_lifecycle_state is not None
                    or item.blocker_key is not None
                    or item.blocker_actionable is not None
                    or (
                        item.task_lifecycle_state != current
                        and item.task_lifecycle_state not in allowed[current]
                    )
                ):
                    raise AppError(
                        422, "PORTABLE_PROJECT_INVALID", "A task lifecycle event is invalid."
                    )
                task_states[item.task_identity_id] = item.task_lifecycle_state
            else:
                if (
                    item.event_type not in {"blocker_opened", "blocker_resolved"}
                    or item.task_identity_id is None
                    or item.blocker_key is None
                    or item.project_lifecycle_state is not None
                    or item.task_lifecycle_state is not None
                    or (item.event_type == "blocker_opened" and item.blocker_actionable is None)
                    or (
                        item.event_type == "blocker_resolved"
                        and item.blocker_actionable is not None
                    )
                ):
                    raise AppError(
                        422, "PORTABLE_PROJECT_INVALID", "A Project blocker event is invalid."
                    )
                blocker = (item.task_identity_id, item.blocker_key)
                if (item.event_type == "blocker_opened") == (blocker in open_blockers):
                    raise AppError(
                        422,
                        "PORTABLE_PROJECT_INVALID",
                        "A Project blocker event cannot be replayed deterministically.",
                    )
                if item.event_type == "blocker_opened":
                    open_blockers.add(blocker)
                else:
                    open_blockers.remove(blocker)
    activity_rows = {
        item.id: item for item in connection.execute(select(*Activity.__table__.c)).all()
    }
    links = {
        item.id: item
        for item in connection.execute(select(*ActivityProjectTaskLink.__table__.c)).all()
    }
    for link in links.values():
        activity = activity_rows.get(link.activity_id)
        task = task_defs.get(link.task_definition_id)
        version = versions.get(task.project_version_id) if task else None
        if (
            activity is None
            or task is None
            or version is None
            or link.created_at < activity.created_at
            or link.created_at < version.created_at
        ):
            raise AppError(
                422,
                "PORTABLE_PROJECT_INVALID",
                "A Project Activity link has invalid provenance or chronology.",
            )
    link_corrections = list(
        connection.execute(select(*ActivityProjectTaskLinkCorrection.__table__.c)).all()
    )
    correction_by_link = {item.activity_project_task_link_id: item for item in link_corrections}
    replacement_edges: dict[str, str] = {}
    for correction in link_corrections:
        link = links.get(correction.activity_project_task_link_id)
        replacement = (
            links.get(correction.replacement_link_id) if correction.replacement_link_id else None
        )
        if (
            link is None
            or correction.corrected_at < link.created_at
            or correction.replacement_link_id == correction.activity_project_task_link_id
            or (correction.replacement_link_id is not None and replacement is None)
            or (replacement is not None and replacement.activity_id != link.activity_id)
            or (replacement is not None and replacement.created_at > correction.corrected_at)
        ):
            raise AppError(
                422,
                "PORTABLE_PROJECT_INVALID",
                "A Project Activity-link correction is inconsistent.",
            )
        if replacement is not None:
            replacement_edges[link.id] = replacement.id
    for link_id in replacement_edges:
        seen: set[str] = set()
        current_link_id: str | None = link_id
        while current_link_id is not None:
            if current_link_id in seen:
                raise AppError(
                    422,
                    "PORTABLE_PROJECT_INVALID",
                    "Project Activity-link corrections must not form cycles.",
                )
            seen.add(current_link_id)
            current_link_id = replacement_edges.get(current_link_id)
    active_link_targets: set[tuple[str, str]] = set()
    for link in links.values():
        if link.id in correction_by_link:
            continue
        link_key = (link.activity_id, link.task_definition_id)
        if link_key in active_link_targets:
            raise AppError(
                422,
                "PORTABLE_PROJECT_INVALID",
                "An Activity has duplicate active links to the same Project task.",
            )
        active_link_targets.add(link_key)
    sessions = {
        item.id: item for item in connection.execute(select(*LearningSession.__table__.c)).all()
    }
    contributions = {
        item.id: item
        for item in connection.execute(select(*SessionProjectContribution.__table__.c)).all()
    }
    contribution_retractions = list(
        connection.execute(select(*SessionProjectContributionRetraction.__table__.c))
    )
    retracted_contribution_ids = {item.contribution_id for item in contribution_retractions}
    primary_by_session: set[str] = set()
    active_contribution_targets: set[tuple[str, str, str, str | None]] = set()
    for contribution in contributions.values():
        session = sessions.get(contribution.session_id)
        version = versions.get(contribution.project_version_id)
        task = (
            task_defs.get(contribution.task_definition_id)
            if contribution.task_definition_id
            else None
        )
        if (
            session is None
            or version is None
            or version.project_id != contribution.project_id
            or (task is not None and task.project_version_id != version.id)
            or contribution.created_at < session.created_at
            or (
                session.tombstoned_at is not None
                and contribution.created_at >= session.tombstoned_at
            )
            or ((contribution.idempotency_key is None) != (contribution.command_hash is None))
        ):
            raise AppError(
                422,
                "PORTABLE_PROJECT_INVALID",
                "A Project SessionContribution is inconsistent.",
            )
        if contribution.idempotency_key is not None and contribution.command_hash != content_hash(
            {
                "session_id": contribution.session_id,
                "project_version_id": contribution.project_version_id,
                "task_definition_id": contribution.task_definition_id,
                "relevance": contribution.relevance,
                "provenance": contribution.provenance,
            }
        ):
            raise AppError(
                422,
                "PORTABLE_PROJECT_INVALID",
                "A Project SessionContribution command hash is inconsistent.",
            )
        if contribution.id not in retracted_contribution_ids:
            target = (
                contribution.session_id,
                contribution.project_id,
                contribution.project_version_id,
                contribution.task_definition_id,
            )
            if target in active_contribution_targets:
                raise AppError(
                    422,
                    "PORTABLE_PROJECT_INVALID",
                    "A Session has duplicate active Project contributions.",
                )
            active_contribution_targets.add(target)
        if (
            contribution.relevance == "primary"
            and contribution.id not in retracted_contribution_ids
        ):
            if contribution.session_id in primary_by_session:
                raise AppError(
                    422,
                    "PORTABLE_PROJECT_INVALID",
                    "A Session has multiple Primary Project contributions.",
                )
            primary_by_session.add(contribution.session_id)
    for retraction in contribution_retractions:
        original = contributions.get(retraction.contribution_id)
        replacement = (
            contributions.get(retraction.replacement_contribution_id)
            if retraction.replacement_contribution_id
            else None
        )
        if (
            original is None
            or retraction.retracted_at < original.created_at
            or retraction.replacement_contribution_id == retraction.contribution_id
            or (retraction.replacement_contribution_id is not None and replacement is None)
            or (replacement is not None and replacement.session_id != original.session_id)
            or (replacement is not None and replacement.created_at > retraction.retracted_at)
        ):
            raise AppError(
                422,
                "PORTABLE_PROJECT_INVALID",
                "A Project SessionContribution retraction is inconsistent.",
            )
    evidence_rows = {
        item.id: item for item in connection.execute(select(*Evidence.__table__.c)).all()
    }
    evidence_links_by_evidence: dict[str, list[Any]] = defaultdict(list)
    for item in connection.execute(select(*EvidenceLink.__table__.c)):
        evidence_links_by_evidence[item.evidence_id].append(item)
    invalidations = {
        item.evidence_id: item
        for item in connection.execute(select(*EvidenceInvalidation.__table__.c)).all()
    }
    evidence_retractions = {
        item.evidence_id: item
        for item in connection.execute(select(*EvidenceRetraction.__table__.c)).all()
    }
    session_corrections_by_session: dict[str, list[Any]] = defaultdict(list)
    for item in connection.execute(select(*SessionCorrection.__table__.c)):
        session_corrections_by_session[item.session_id].append(item)

    def project_session_assistance_modes(
        activity_id: str, cutoff_at: int, occurred_at: int
    ) -> tuple[str, ...]:
        modes: list[str] = []
        for session in sessions.values():
            if session.created_at >= cutoff_at:
                continue
            values: dict[str, Any] = {
                "activity_id": session.activity_id,
                "assistance_mode": session.assistance_mode,
                "ended_at": session.ended_at,
                "outcome": session.outcome,
            }
            for correction in sorted(
                (
                    item
                    for item in session_corrections_by_session[session.id]
                    if item.corrected_at >= cutoff_at
                ),
                key=lambda item: (item.corrected_at, item.id),
                reverse=True,
            ):
                before = json.loads(correction.before_json)
                for key in values:
                    if key in before:
                        values[key] = before[key]
            if (
                values["activity_id"] == activity_id
                and isinstance(values["ended_at"], int)
                and values["ended_at"] < cutoff_at
                and values["ended_at"] <= occurred_at
                and values["outcome"] in {"completed", "partial"}
                and not (session.tombstoned_at is not None and session.tombstoned_at < cutoff_at)
            ):
                modes.append(str(values["assistance_mode"]))
        return tuple(sorted(modes))

    criterion_identities = {
        item.id: item for item in connection.execute(select(*CriterionIdentity.__table__.c)).all()
    }
    project_evidence_idempotency_keys: set[str] = set()
    for evidence in evidence_rows.values():
        if evidence.source_type != "activity_project_task_link":
            continue
        source_link = links.get(evidence.source_id)
        source_task = task_defs.get(source_link.task_definition_id) if source_link else None
        source_version = versions.get(source_task.project_version_id) if source_task else None
        try:
            provenance = json.loads(evidence.provenance_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AppError(
                422, "PORTABLE_PROJECT_INVALID", "Project Evidence provenance is malformed."
            ) from exc
        opportunity = (
            next(
                (item for item in opportunities if item.id == provenance.get("opportunity_id")),
                None,
            )
            if isinstance(provenance, dict)
            else None
        )
        characteristics = opportunity_characteristics.get(opportunity.id, {}) if opportunity else {}
        raw_derived = (
            provenance.get("derived_characteristics", {}) if isinstance(provenance, dict) else {}
        )
        derived_shape_invalid = not isinstance(raw_derived, dict)
        derived = raw_derived if isinstance(raw_derived, dict) else {}
        activity = activity_rows.get(source_link.activity_id) if source_link is not None else None
        has_artifact = evidence.artifact_hash is not None or evidence.external_reference is not None
        actual_at = (
            activity.context_ended_at
            if activity is not None and activity.context_ended_at is not None
            else activity.occurred_at
            if activity is not None and activity.occurred_at is not None
            else activity.created_at
            if activity is not None
            else None
        )
        cutoff_at = evidence.created_at + 1
        actual_modes = (
            project_session_assistance_modes(
                source_link.activity_id, cutoff_at, evidence.occurred_at
            )
            if source_link is not None and evidence.occurred_at is not None
            else ()
        )
        rubric_result = derived.get("rubricResult")
        policy_result = (
            derive_project_evidence_characteristics(
                activity_outcome=activity.outcome_classification if activity else None,
                has_artifact=has_artifact,
                assistance_modes=actual_modes,
                attribution_provenance=source_link.provenance,
                rubric_result=rubric_result,
                has_project_criterion=(
                    opportunity is not None
                    and opportunity.project_criterion_definition_id is not None
                ),
            )
            if source_link is not None
            else None
        )
        command_body = {
            "activityProjectTaskLinkId": evidence.source_id,
            "opportunityId": opportunity.id if opportunity else None,
            "title": evidence.title,
            "description": evidence.description,
            "occurredAt": evidence.occurred_at,
            "artifactHash": evidence.artifact_hash,
            "externalReference": evidence.external_reference,
            "rubricResult": rubric_result,
        }
        invalid = (
            derived_shape_invalid
            or source_link is None
            or source_task is None
            or source_version is None
            or opportunity is None
            or opportunity.project_version_id != source_version.id
            or opportunity.task_definition_id not in {None, source_task.id}
            or evidence.source_role != opportunity.stable_key
            or evidence.evidence_type != opportunity.evidence_kind
            or evidence.policy_version != "evidence-policy/v1"
            or provenance.get("source_record_type") != evidence.source_type
            or provenance.get("source_record_id") != evidence.source_id
            or provenance.get("project_evidence_policy_version") != PROJECT_EVIDENCE_POLICY
            or provenance.get("project_id") != source_version.project_id
            or provenance.get("project_version_id") != source_version.id
            or provenance.get("task_definition_id") != source_task.id
            or provenance.get("activity_project_task_link_id") != source_link.id
            or provenance.get("project_criterion_definition_id")
            != opportunity.project_criterion_definition_id
            or provenance.get("command_hash") != content_hash(command_body)
            or not isinstance(provenance.get("idempotency_key"), str)
            or not provenance.get("idempotency_key")
            or derived.get("hasArtifact") is not has_artifact
            or derived.get("activityOutcome")
            != (activity.outcome_classification if activity else None)
            or derived.get("assistanceModes") != list(actual_modes)
            or rubric_result not in {None, "passed", "partially_met", "not_met"}
            or (
                rubric_result is not None
                and opportunity is not None
                and opportunity.project_criterion_definition_id is None
            )
            or policy_result is None
            or evidence.strength != policy_result.strength
            or evidence.strength_unknown_reason != policy_result.strength_unknown_reason
            or evidence.independence != policy_result.independence
            or evidence.independence_unknown_reason != policy_result.independence_unknown_reason
            or evidence.source_confidence != policy_result.source_confidence
            or evidence.source_confidence_unknown_reason
            != policy_result.source_confidence_unknown_reason
            or source_link.created_at > evidence.created_at
            or actual_at is None
            or actual_at > evidence.occurred_at
            or evidence.occurred_at > evidence.created_at
            or (bool(characteristics.get("requires_artifact")) and not has_artifact)
            or (
                characteristics.get("intended_strengths")
                and evidence.strength not in characteristics["intended_strengths"]
            )
            or (
                characteristics.get("intended_independence_modes")
                and evidence.independence not in characteristics["intended_independence_modes"]
            )
        )
        source_correction = correction_by_link.get(evidence.source_id)
        if source_correction is not None:
            invalidation = invalidations.get(evidence.id)
            invalid = invalid or source_correction.corrected_at <= evidence.created_at
            invalid = invalid or invalidation is None
        idempotency_key = provenance.get("idempotency_key")
        if idempotency_key in project_evidence_idempotency_keys:
            invalid = True
        elif isinstance(idempotency_key, str):
            project_evidence_idempotency_keys.add(idempotency_key)
        opportunity_criterion_id = (
            opportunity.project_criterion_definition_id if opportunity is not None else None
        )
        selected_targets = [
            item
            for item in targets
            if source_version is not None
            and source_task is not None
            and item.project_version_id == source_version.id
            and (
                item.task_definition_id == source_task.id
                or (
                    item.task_definition_id is None and item.project_criterion_definition_id is None
                )
                or item.project_criterion_definition_id == opportunity_criterion_id
            )
        ]
        expected_links: set[tuple[Any, ...]] = set()
        for target in selected_targets:
            semantic = semantic_defs.get(target.semantic_definition_id)
            native_criterion = (
                native_criterion_defs.get(target.criterion_definition_id)
                if target.criterion_definition_id
                else None
            )
            criterion_identity = (
                criterion_identities.get(native_criterion.criterion_identity_id)
                if native_criterion
                else None
            )
            expected_links.add(
                (
                    semantic.competency_identity_id if semantic else None,
                    criterion_identity.id if criterion_identity else None,
                    native_criterion.id if native_criterion else None,
                    target.scale_version_id,
                    target.dimension_id,
                    target.level_id,
                    "contradicts" if rubric_result == "not_met" else "supports",
                    target.role,
                )
            )
        actual_links = {
            (
                item.competency_identity_id,
                item.criterion_identity_id,
                item.criterion_definition_id,
                item.scale_version_id,
                item.dimension_id,
                item.level_id,
                item.effect,
                item.relevance,
            )
            for item in evidence_links_by_evidence[evidence.id]
        }
        if invalid or not expected_links or actual_links != expected_links:
            raise AppError(
                422,
                "PORTABLE_PROJECT_INVALID",
                "Project Evidence lineage or derived characteristics are inconsistent.",
            )
    evaluation_evidence: dict[str, list[str]] = defaultdict(list)
    for item in connection.execute(select(*ProjectCriterionEvaluationEvidence.__table__.c)):
        evaluation_evidence[item.project_criterion_evaluation_id].append(item.evidence_id)
    for evaluation in connection.execute(select(*ProjectCriterionEvaluation.__table__.c)):
        criterion = criterion_defs.get(evaluation.project_criterion_definition_id)
        ids = sorted(evaluation_evidence[evaluation.id])
        evidence = [evidence_rows.get(item) for item in ids]
        evidence_facts = tuple(
            {
                "evidenceId": item.id,
                "strength": item.strength,
                "independence": item.independence,
                "sourceConfidence": item.source_confidence,
            }
            for item in evidence
            if item is not None
        )
        expected_state, policy_facts = evaluate_project_criterion_evidence(evidence_facts)
        expected_facts = {
            **policy_facts,
            "evidenceIds": ids,
            "criterionDefinitionId": criterion.id if criterion else None,
        }
        if (
            criterion is None
            or not ids
            or any(item is None for item in evidence)
            or evaluation.evidence_set_hash != content_hash(ids)
            or evaluation.policy_version != criterion.evaluation_policy_version
            or evaluation.state != expected_state
            or json.loads(evaluation.facts_json) != expected_facts
            or any(
                item.created_at > evaluation.evaluated_at
                or json.loads(item.provenance_json).get("project_criterion_definition_id")
                != criterion.id
                or (
                    item.id in invalidations
                    and invalidations[item.id].created_at <= evaluation.evaluated_at
                )
                or (
                    item.id in evidence_retractions
                    and evidence_retractions[item.id].created_at <= evaluation.evaluated_at
                )
                for item in evidence
                if item is not None
            )
        ):
            raise AppError(
                422,
                "PORTABLE_PROJECT_INVALID",
                "A ProjectCriterion evaluation is disconnected from qualifying Evidence.",
            )


def _validate_capability_history(connection: Any) -> None:
    definitions = {
        item.id: item
        for item in connection.execute(select(*SemanticCompetencyDefinition.__table__.c)).all()
    }
    scales = {
        item.id: item
        for item in connection.execute(select(*CapabilityScaleVersion.__table__.c)).all()
    }
    levels = {
        item.id: item
        for item in connection.execute(select(*CapabilityScaleLevel.__table__.c)).all()
    }
    dimensions = {
        item.id: item
        for item in connection.execute(select(*CapabilityScaleDimension.__table__.c)).all()
    }
    enabled_dimensions = {
        (item.semantic_definition_id, item.scale_dimension_id)
        for item in connection.execute(select(*SemanticDefinitionDimension.__table__.c)).all()
    }
    criteria = {
        item.id: item for item in connection.execute(select(*CriterionDefinition.__table__.c)).all()
    }
    runs = {
        item.id: item
        for item in connection.execute(select(*CapabilityEvaluationRun.__table__.c)).all()
    }
    input_payloads: dict[str, dict[str, Any]] = {}
    for run in runs.values():
        try:
            input_payload = json.loads(run.input_payload_json)
            confidence_facts = json.loads(run.confidence_facts_json)
            decisive_ids = json.loads(run.decisive_evidence_ids_json)
            passed_ids = json.loads(run.passed_level_ids_json)
            reasons = json.loads(run.reasons_json)
        except (TypeError, ValueError) as exc:
            raise AppError(
                422,
                "PORTABLE_CAPABILITY_HISTORY_INVALID",
                "Capability evaluation history contains invalid JSON.",
            ) from exc
        if not isinstance(input_payload, dict):
            raise AppError(
                422,
                "PORTABLE_CAPABILITY_HISTORY_INVALID",
                "Capability evaluation input lineage is malformed.",
            )
        input_payloads[run.id] = input_payload
        input_evidence = input_payload.get("evidence")
        input_criteria = input_payload.get("criteria")
        output = {
            "selectedLevelId": run.selected_level_id,
            "assessmentStatus": run.assessment_status,
            "aggregateConfidence": run.aggregate_confidence,
            "confidenceFacts": confidence_facts,
            "downgradeCause": run.downgrade_cause,
            "decisiveEvidenceIds": decisive_ids,
            "passedLevelIds": passed_ids,
            "reasons": reasons,
        }
        definition = definitions.get(run.semantic_definition_id)
        selected_level = levels.get(run.selected_level_id) if run.selected_level_id else None
        dimension = dimensions.get(run.dimension_id) if run.dimension_id else None
        if (
            not all(len(value) == 64 for value in (run.evidence_set_hash, run.input_hash))
            or content_hash(input_payload) != run.input_hash
            or not isinstance(input_evidence, list)
            or content_hash(input_evidence) != run.evidence_set_hash
            or not isinstance(input_criteria, list)
            or input_payload.get("competencyIdentityId") != run.competency_identity_id
            or input_payload.get("semanticDefinitionId") != run.semantic_definition_id
            or input_payload.get("scaleVersionId") != run.scale_version_id
            or input_payload.get("scopeKey") != run.scope_key
            or input_payload.get("cutoffAt") != run.cutoff_at
            or input_payload.get("policies")
            != [
                "criterion-evaluation-policy/v1",
                "capability-policy/v1",
                "evidence-policy/v1",
                "capability-downgrade-policy/v1",
            ]
            or content_hash(output) != run.output_hash
            or not isinstance(confidence_facts, dict)
            or not all(isinstance(value, list) for value in (decisive_ids, passed_ids, reasons))
            or run.criterion_policy_version != "criterion-evaluation-policy/v1"
            or run.capability_policy_version != "capability-policy/v1"
            or run.evidence_policy_version != "evidence-policy/v1"
            or run.downgrade_policy_version != "capability-downgrade-policy/v1"
            or definition is None
            or definition.competency_identity_id != run.competency_identity_id
            or definition.scale_version_id != run.scale_version_id
            or run.scale_version_id not in scales
            or (
                run.dimension_id is not None
                and (
                    dimension is None
                    or dimension.scale_version_id != run.scale_version_id
                    or (run.semantic_definition_id, run.dimension_id) not in enabled_dimensions
                )
            )
            or run.scope_key != (f"dimension:{run.dimension_id}" if run.dimension_id else "overall")
            or (
                selected_level is not None
                and selected_level.scale_version_id != run.scale_version_id
            )
            or any(
                level_id not in levels or levels[level_id].scale_version_id != run.scale_version_id
                for level_id in passed_ids
            )
        ):
            raise AppError(
                422,
                "PORTABLE_CAPABILITY_HISTORY_INVALID",
                "Capability evaluation hashes or facts are inconsistent.",
            )
    for result in connection.execute(select(*CriterionEvaluationResult.__table__.c)):
        run = runs.get(result.run_id)
        criterion = criteria.get(result.criterion_definition_id)
        try:
            decisive = json.loads(result.decisive_evidence_ids_json)
            facts = json.loads(result.facts_json)
        except (TypeError, ValueError) as exc:
            raise AppError(
                422,
                "PORTABLE_CAPABILITY_HISTORY_INVALID",
                "Criterion evaluation history contains invalid JSON.",
            ) from exc
        input_criteria = input_payloads.get(result.run_id, {}).get("criteria", [])
        matching_input = [
            item
            for item in input_criteria
            if isinstance(item, dict) and item.get("id") == result.criterion_definition_id
        ]
        if (
            run is None
            or result.evidence_set_hash != run.evidence_set_hash
            or not isinstance(decisive, list)
            or not isinstance(facts, dict)
            or criterion is None
            or criterion.semantic_definition_id != run.semantic_definition_id
            or criterion.dimension_id != run.dimension_id
            or criterion.level_id not in levels
            or levels[criterion.level_id].scale_version_id != run.scale_version_id
            or len(matching_input) != 1
            or matching_input[0].get("state") != result.state
            or matching_input[0].get("decisiveEvidenceIds") != decisive
            or matching_input[0].get("facts") != facts
        ):
            raise AppError(
                422,
                "PORTABLE_CAPABILITY_HISTORY_INVALID",
                "Criterion evaluation history is inconsistent with its run.",
            )
    result_counts = dict(
        connection.execute(
            select(CriterionEvaluationResult.run_id, func.count()).group_by(
                CriterionEvaluationResult.run_id
            )
        ).all()
    )
    if any(
        result_counts.get(run_id, 0) != len(payload.get("criteria", []))
        for run_id, payload in input_payloads.items()
    ):
        raise AppError(
            422,
            "PORTABLE_CAPABILITY_HISTORY_INVALID",
            "Criterion evaluation history is incomplete for its run.",
        )
    for model in (CapabilityStateEvent, ReviewEvent):
        sequences: dict[tuple[str, str], int] = defaultdict(int)
        events = connection.execute(
            select(*model.__table__.c).order_by(
                model.competency_identity_id, model.scope_key, model.event_sequence
            )
        )
        for event in events:
            key = (event.competency_identity_id, event.scope_key)
            sequences[key] += 1
            run = runs.get(event.evaluation_run_id)
            if run is None:
                raise AppError(
                    422,
                    "PORTABLE_CAPABILITY_HISTORY_INVALID",
                    "Capability or review event history is disconnected.",
                )
            event_invalid = (
                event.event_sequence != sequences[key]
                or run.competency_identity_id != event.competency_identity_id
                or run.scope_key != event.scope_key
                or run.semantic_definition_id != event.semantic_definition_id
                or run.dimension_id != event.dimension_id
            )
            if model is CapabilityStateEvent:
                try:
                    event_decisive = json.loads(event.decisive_evidence_ids_json)
                except (TypeError, ValueError):
                    event_invalid = True
                else:
                    event_invalid = event_invalid or (
                        event.new_level_id != run.selected_level_id
                        or event.new_assessment_status != run.assessment_status
                        or event.new_confidence != run.aggregate_confidence
                        or event_decisive != json.loads(run.decisive_evidence_ids_json)
                    )
            else:
                try:
                    review_reasons = json.loads(event.reason_codes_json)
                except (TypeError, ValueError):
                    event_invalid = True
                else:
                    event_invalid = event_invalid or (
                        not isinstance(review_reasons, list)
                        or event.freshness_policy_version != "freshness-policy/v1"
                        or (event.current_through_days is None) != (event.stale_after_days is None)
                        or (
                            event.current_through_days is not None
                            and event.stale_after_days is not None
                            and event.stale_after_days < event.current_through_days
                        )
                    )
            if event_invalid:
                raise AppError(
                    422,
                    "PORTABLE_CAPABILITY_HISTORY_INVALID",
                    "Capability or review event history is disconnected.",
                )
    for state in connection.execute(select(*CompetencyCapabilityState.__table__.c)):
        run = runs.get(state.evaluation_run_id)
        if (
            run is None
            or run.competency_identity_id != state.competency_identity_id
            or run.scope_key != state.scope_key
            or run.semantic_definition_id != state.semantic_definition_id
            or run.scale_version_id != state.scale_version_id
            or run.dimension_id != state.dimension_id
            or run.selected_level_id != state.capability_level_id
            or run.assessment_status != state.assessment_status
            or run.aggregate_confidence != state.aggregate_confidence
            or run.capability_policy_version != state.capability_policy_version
            or run.criterion_policy_version != state.criterion_policy_version
            or run.evidence_policy_version != state.evidence_policy_version
            or run.evidence_set_hash != state.evidence_set_hash
            or run.confidence_facts_json != state.confidence_facts_json
        ):
            raise AppError(
                422,
                "PORTABLE_CAPABILITY_PROJECTION_INVALID",
                "The current capability projection is disconnected from history.",
            )
    for state in connection.execute(select(*CompetencyReviewState.__table__.c)):
        run = runs.get(state.evaluation_run_id)
        if (
            run is None
            or run.competency_identity_id != state.competency_identity_id
            or run.scope_key != state.scope_key
            or run.semantic_definition_id != state.semantic_definition_id
            or run.scale_version_id != state.scale_version_id
            or run.dimension_id != state.dimension_id
            or state.freshness_policy_version != "freshness-policy/v1"
            or (state.current_through_days is None) != (state.stale_after_days is None)
            or (
                state.current_through_days is not None
                and state.stale_after_days is not None
                and state.stale_after_days < state.current_through_days
            )
        ):
            raise AppError(
                422,
                "PORTABLE_CAPABILITY_PROJECTION_INVALID",
                "The current review projection is disconnected from history.",
            )


def _validate_learning_graph_and_projection(connection: Any) -> None:
    graphs = {row.id: row for row in connection.execute(select(*LearningGraph.__table__.c)).all()}
    versions = {
        row.id: row for row in connection.execute(select(*LearningGraphVersion.__table__.c)).all()
    }
    identities = {
        row.id: row for row in connection.execute(select(*CompetencyEdgeIdentity.__table__.c)).all()
    }
    definitions = list(connection.execute(select(*CompetencyEdgeDefinition.__table__.c)).all())
    definitions_by_version: dict[str, list[Any]] = defaultdict(list)
    semantics = {
        row.id: row
        for row in connection.execute(select(*SemanticCompetencyDefinition.__table__.c)).all()
    }
    levels = {
        row.id: row for row in connection.execute(select(*CapabilityScaleLevel.__table__.c)).all()
    }
    dimensions = {
        row.id: row
        for row in connection.execute(select(*CapabilityScaleDimension.__table__.c)).all()
    }
    semantic_dimensions = {
        (row.semantic_definition_id, row.scale_dimension_id)
        for row in connection.execute(select(*SemanticDefinitionDimension.__table__.c)).all()
    }
    criteria = {
        row.id: row for row in connection.execute(select(*CriterionDefinition.__table__.c)).all()
    }
    for edge in definitions:
        definitions_by_version[edge.learning_graph_version_id].append(edge)
        identity = identities.get(edge.edge_identity_id)
        version = versions.get(edge.learning_graph_version_id)
        if (
            identity is None
            or version is None
            or identity.learning_graph_id != version.learning_graph_id
            or identity.edge_type != edge.edge_type
            or identity.source_competency_identity_id != edge.source_competency_identity_id
            or identity.target_competency_identity_id != edge.target_competency_identity_id
            or (
                edge.edge_type == "related"
                and edge.source_competency_identity_id > edge.target_competency_identity_id
            )
            or (edge.edge_type == "prerequisite" and edge.requirement_kind is None)
            or (edge.edge_type != "prerequisite" and edge.requirement_kind is not None)
        ):
            raise AppError(
                422,
                "PORTABLE_LEARNING_GRAPH_INVALID",
                "Learning Graph edge identity or semantics are inconsistent.",
            )
        try:
            requirement = json.loads(edge.requirement_json)
        except (TypeError, ValueError) as exc:
            raise AppError(
                422,
                "PORTABLE_LEARNING_GRAPH_INVALID",
                "A Learning Graph requirement is invalid.",
            ) from exc
        if not isinstance(requirement, dict) or (
            edge.requirement_kind is not None and requirement.get("kind") != edge.requirement_kind
        ):
            raise AppError(
                422,
                "PORTABLE_LEARNING_GRAPH_INVALID",
                "A Learning Graph requirement does not match its typed definition.",
            )
        source = semantics.get(edge.source_semantic_definition_id)
        target = semantics.get(edge.target_semantic_definition_id)
        if source is None or target is None:
            raise AppError(
                422, "PORTABLE_LEARNING_GRAPH_INVALID", "Graph endpoints must be semantic facts."
            )
        if (
            source.competency_identity_id != edge.source_competency_identity_id
            or target.competency_identity_id != edge.target_competency_identity_id
            or edge.source_competency_identity_id == edge.target_competency_identity_id
        ):
            raise AppError(
                422,
                "PORTABLE_LEARNING_GRAPH_INVALID",
                "Graph definitions must pin versions of their stable Competency endpoints.",
            )
        if edge.requirement_kind == "capability_at_least":
            level = levels.get(requirement.get("minimum_level_id"))
            dimension_id = requirement.get("dimension_id")
            dimension = dimensions.get(dimension_id) if dimension_id is not None else None
            if (
                requirement.get("scale_version_id") != source.scale_version_id
                or level is None
                or level.scale_version_id != source.scale_version_id
                or (
                    dimension_id is not None
                    and (dimension is None or dimension.scale_version_id != source.scale_version_id)
                )
                or (dimension_id is None and edge.satisfaction_scope_key != "overall")
                or (
                    dimension_id is not None
                    and (
                        (source.id, dimension_id) not in semantic_dimensions
                        or edge.satisfaction_scope_key != f"dimension:{dimension_id}"
                    )
                )
                or requirement.get("review_requirement") not in {"none", "review_due_false"}
            ):
                raise AppError(
                    422,
                    "PORTABLE_LEARNING_GRAPH_INVALID",
                    "Native capability requirements require an exact scale version.",
                )
        if edge.requirement_kind == "criterion_set_demonstrated":
            criterion_ids = requirement.get("criterion_definition_ids")
            if (
                not isinstance(criterion_ids, list)
                or not criterion_ids
                or len(criterion_ids) != len(set(criterion_ids))
                or any(
                    criterion_id not in criteria
                    or criteria[criterion_id].semantic_definition_id != source.id
                    for criterion_id in criterion_ids
                )
            ):
                raise AppError(
                    422,
                    "PORTABLE_LEARNING_GRAPH_INVALID",
                    "Native criterion requirements must belong to the prerequisite competency.",
                )
    for version in versions.values():
        if version.learning_graph_id not in graphs:
            raise AppError(
                422, "PORTABLE_LEARNING_GRAPH_INVALID", "Graph version ownership is invalid."
            )
        try:
            payload = json.loads(version.definition_payload_json)
        except (TypeError, ValueError) as exc:
            raise AppError(
                422, "PORTABLE_LEARNING_GRAPH_INVALID", "Graph definition JSON is invalid."
            ) from exc
        if (
            not isinstance(payload, dict)
            or version.schema_version != GRAPH_SCHEMA_VERSION
            or version.satisfaction_policy_version != GRAPH_SATISFACTION_POLICY
            or payload.get("schema_version") != GRAPH_SCHEMA_VERSION
            or payload.get("satisfaction_policy_version") != GRAPH_SATISFACTION_POLICY
            or content_hash(payload) != version.content_hash
        ):
            raise AppError(
                422,
                "PORTABLE_LEARNING_GRAPH_INVALID",
                "Graph policy lineage or content hash is invalid.",
            )
        payload_edges = payload.get("edges")
        stored_edges = sorted(
            definitions_by_version.get(version.id, []), key=lambda row: (row.order_index, row.id)
        )
        if not isinstance(payload_edges, list) or len(payload_edges) != len(stored_edges):
            raise AppError(
                422, "PORTABLE_LEARNING_GRAPH_INVALID", "Graph payload children are incomplete."
            )
        prerequisite_graph: dict[str, set[str]] = defaultdict(set)
        specialization_graph: dict[str, set[str]] = defaultdict(set)
        pinned_definitions: dict[str, str] = {}
        for payload_edge, stored in zip(
            sorted(
                payload_edges,
                key=lambda row: (row.get("order_index", -1), row.get("stable_key", "")),
            ),
            stored_edges,
            strict=True,
        ):
            identity = identities[stored.edge_identity_id]
            requirement = json.loads(stored.requirement_json)
            if (
                not isinstance(payload_edge, dict)
                or payload_edge.get("stable_key") != identity.stable_key
                or payload_edge.get("edge_type") != stored.edge_type
                or payload_edge.get("source_semantic_definition_id")
                != stored.source_semantic_definition_id
                or payload_edge.get("target_semantic_definition_id")
                != stored.target_semantic_definition_id
                or payload_edge.get("source_competency_identity_id")
                != stored.source_competency_identity_id
                or payload_edge.get("target_competency_identity_id")
                != stored.target_competency_identity_id
                or payload_edge.get("satisfaction_scope_key") != stored.satisfaction_scope_key
                or (payload_edge.get("requirement") or {}) != requirement
                or payload_edge.get("provenance") != stored.provenance
                or payload_edge.get("meaning_key") != identity.meaning_key
                or payload_edge.get("order_index") != stored.order_index
            ):
                raise AppError(
                    422,
                    "PORTABLE_LEARNING_GRAPH_INVALID",
                    "Graph payload and typed children do not match.",
                )
            if stored.edge_type == "prerequisite":
                prerequisite_graph[stored.source_competency_identity_id].add(
                    stored.target_competency_identity_id
                )
            if stored.edge_type == "specialization":
                specialization_graph[stored.source_competency_identity_id].add(
                    stored.target_competency_identity_id
                )
            for competency_id, definition_id in (
                (stored.source_competency_identity_id, stored.source_semantic_definition_id),
                (stored.target_competency_identity_id, stored.target_semantic_definition_id),
            ):
                prior = pinned_definitions.setdefault(competency_id, definition_id)
                if (
                    prior != definition_id
                    or semantics[definition_id].effective_at > version.effective_at
                ):
                    raise AppError(
                        422,
                        "PORTABLE_LEARNING_GRAPH_INVALID",
                        "A graph version must pin one already-effective definition per competency.",
                    )
        if has_required_dependency_cycle(prerequisite_graph) or has_required_dependency_cycle(
            specialization_graph
        ):
            raise AppError(
                422,
                "PORTABLE_LEARNING_GRAPH_INVALID",
                "Hard Learning Graph relations violate acyclicity.",
            )
    versions_by_graph: dict[str, list[Any]] = defaultdict(list)
    for version in versions.values():
        versions_by_graph[version.learning_graph_id].append(version)
    for graph_id, graph_versions in versions_by_graph.items():
        ordered_versions = sorted(graph_versions, key=lambda item: item.version)
        if [item.version for item in ordered_versions] != list(
            range(1, len(ordered_versions) + 1)
        ) or any(
            item.supersedes_version_id != (ordered_versions[index - 1].id if index else None)
            for index, item in enumerate(ordered_versions)
        ):
            raise AppError(
                422,
                "PORTABLE_LEARNING_GRAPH_INVALID",
                f"Graph version lineage is not contiguous for {graph_id}.",
            )
    activation_rows = sorted(
        connection.execute(select(*LearningGraphActivationEvent.__table__.c)).all(),
        key=lambda row: (row.event_sequence, row.id),
    )
    previous_version_id: str | None = None
    previous_activated_at: int | None = None
    last_version_number_by_graph: dict[str, int] = {}
    for expected_sequence, event in enumerate(activation_rows, start=1):
        to_version = versions.get(event.to_learning_graph_version_id)
        from_version = versions.get(event.from_learning_graph_version_id)
        if (
            event.event_sequence != expected_sequence
            or event.learning_graph_id not in graphs
            or to_version is None
            or to_version.learning_graph_id != event.learning_graph_id
            or event.from_learning_graph_version_id != previous_version_id
            or (event.from_learning_graph_version_id is not None and from_version is None)
            or (previous_activated_at is not None and event.activated_at < previous_activated_at)
            or to_version.effective_at > event.activated_at
            or to_version.version < last_version_number_by_graph.get(event.learning_graph_id, 0)
        ):
            raise AppError(
                422, "PORTABLE_LEARNING_GRAPH_INVALID", "Graph activation history is invalid."
            )
        previous_version_id = event.to_learning_graph_version_id
        previous_activated_at = event.activated_at
        last_version_number_by_graph[event.learning_graph_id] = to_version.version
    states = list(connection.execute(select(*ActiveLearningGraphState.__table__.c)).all())
    if len(states) != (1 if activation_rows else 0):
        raise AppError(
            422, "PORTABLE_LEARNING_GRAPH_INVALID", "Current Graph state is not replayable."
        )
    if states:
        state = states[0]
        latest = activation_rows[-1]
        if (
            state.id != 1
            or state.learning_graph_id != latest.learning_graph_id
            or state.learning_graph_version_id != latest.to_learning_graph_version_id
            or state.activated_at != latest.activated_at
        ):
            raise AppError(
                422, "PORTABLE_LEARNING_GRAPH_INVALID", "Current Graph state is not replayable."
            )
    profile_versions = {row.id for row in connection.execute(select(TargetProfileVersion.id)).all()}
    profile_targets_by_version: dict[str, set[str]] = defaultdict(set)
    profile_target_identities = {
        row.id: row.competency_identity_id
        for row in connection.execute(
            select(ProfileTargetIdentity.id, ProfileTargetIdentity.competency_identity_id)
        ).all()
    }
    for row in connection.execute(
        select(ProfileTarget.profile_version_id, ProfileTarget.target_identity_id)
    ).all():
        profile_targets_by_version[row.profile_version_id].add(
            profile_target_identities[row.target_identity_id]
        )
    graph_nodes_by_version: dict[str, set[str]] = defaultdict(set)
    for edge in definitions:
        graph_nodes_by_version[edge.learning_graph_version_id].update(
            {edge.source_competency_identity_id, edge.target_competency_identity_id}
        )
    projection_input_rows = [
        *connection.execute(select(*RoadmapNodePositionOverride.__table__.c)).all(),
        *connection.execute(select(*RoadmapProjectionPreference.__table__.c)).all(),
    ]
    for row in projection_input_rows:
        parts = row.scope_key.split(":")
        if (
            len(parts) != 4
            or parts[0] != "profile"
            or parts[2] != "graph"
            or parts[1] not in profile_versions
            or parts[3] not in versions
            or (
                hasattr(row, "node_key")
                and row.node_key
                not in (profile_targets_by_version[parts[1]] | graph_nodes_by_version[parts[3]])
            )
        ):
            raise AppError(
                422,
                "PORTABLE_ROADMAP_PROJECTION_INPUT_INVALID",
                "A Roadmap Projection override or preference has invalid scope.",
            )
    roadmaps = {row.id: row for row in connection.execute(select(*Roadmap.__table__.c)).all()}
    compatibility_states = {
        row.roadmap_id: row
        for row in connection.execute(select(*LegacyRoadmapActiveState.__table__.c)).all()
    }
    if set(roadmaps) != set(compatibility_states):
        raise AppError(
            422,
            "PORTABLE_LEGACY_ROADMAP_STATE_INVALID",
            "Every legacy Roadmap requires exact active-state compatibility data.",
        )
    for roadmap_id, roadmap in roadmaps.items():
        state = compatibility_states[roadmap_id]
        expected_hash = content_hash(
            {
                "roadmapId": roadmap.id,
                "activeVersionId": roadmap.active_version_id,
                "currentPhaseId": roadmap.current_phase_id,
                "isCurrent": bool(roadmap.is_current),
            }
        )
        if (
            state.active_version_id != roadmap.active_version_id
            or state.current_phase_id != roadmap.current_phase_id
            or bool(state.is_current) != bool(roadmap.is_current)
            or state.state_hash != expected_hash
        ):
            raise AppError(
                422,
                "PORTABLE_LEGACY_ROADMAP_STATE_INVALID",
                "Legacy Roadmap active-state hashes do not match.",
            )


def portable_state_presence(connection: Any, portable_models: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    builtin_ids = {
        table_name: {str(row["id"]) for row in rows}
        for table_name, rows in builtin_scale_tables().items()
    }
    builtin_ids["activity_category_versions"] = {str(row["id"]) for row in activity_category_rows()}
    for model in portable_models:
        if model is DisciplineProfile:
            profiles = connection.execute(select(DisciplineProfile)).scalars().all()
            count = sum(
                profile.weekly_target_active_days != 5
                or profile.target_duration_ms_per_active_day != 3_600_000
                or profile.timezone != "UTC"
                or profile.adaptation_phase_config_json != "{}"
                for profile in profiles
            )
        elif model.__table__.name in builtin_ids:
            ids = set(connection.execute(select(model.id)).scalars())
            count = len(ids - builtin_ids[model.__table__.name])
        elif model is MigrationBackfillRun:
            ids = set(connection.execute(select(MigrationBackfillRun.id)).scalars())
            count = len(ids - {RUN_ID, ACTIVITY_RUN_ID, EVIDENCE_RUN_ID})
        else:
            count = connection.scalar(select(func.count()).select_from(model)) or 0
        if count:
            counts[model.__table__.name] = count
    return counts
