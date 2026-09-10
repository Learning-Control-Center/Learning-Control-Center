from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Boolean, Integer, String, Table, Text, func, select, text

from app.analysis.contracts import content_hash
from app.capability_scales import builtin_scale_tables
from app.compatibility.v1.profile_competency_backfill import (
    POLICY_KEY,
    RUN_ID,
    canonical_rows_hash,
)
from app.domain import has_required_dependency_cycle
from app.errors import AppError
from app.models import (
    ActiveCompetencyDefinitionState,
    ActiveTargetProfileState,
    AnalysisRun,
    AnalysisSnapshot,
    ApplicationSetting,
    CapabilityScaleDimension,
    CapabilityScaleLevel,
    CapabilityScaleVersion,
    CompetencyDefinition,
    CompetencyDefinitionActivationEvent,
    CompetencyIdentity,
    CompetencyPrerequisite,
    CompetencyState,
    CompetencyStatusEvent,
    CriterionDefinition,
    CriterionIdentity,
    DisciplineProfile,
    ExitCriterionDefinition,
    ExitCriterionIdentity,
    ExportRecord,
    GeneratedReport,
    ImportRecord,
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
    Roadmap,
    RoadmapScopeEvent,
    RoadmapVersion,
    SemanticCompetencyDefinition,
    SemanticDefinitionDimension,
    TargetProfile,
    TargetProfileActivationEvent,
    TargetProfileVersion,
    Track,
    VerificationRecord,
)


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
    expected_subject_keys = {
        "capability_at_least": {
            "competencyIdentityId",
            "dimensionKey",
            "scaleStableKey",
            "scaleVersion",
            "levelStableKey",
        },
        "criterion_demonstrated": {"criterionIdentityId"},
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
            or predicate.predicate_type == "project_criterion_demonstrated"
            or predicate.predicate_type not in expected_subject_keys
            or not isinstance(subject, dict)
            or set(subject) != expected_subject_keys.get(predicate.predicate_type, set())
        )
        if not invalid and predicate.predicate_type == "criterion_demonstrated":
            invalid = subject["criterionIdentityId"] not in criterion_identity_ids
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
        )
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
    for timezone_name in connection.execute(select(DisciplineProfile.timezone)).scalars():
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise AppError(
                422,
                "PORTABLE_TIMEZONE_INVALID",
                "The discipline timezone is not a valid IANA timezone.",
            ) from exc


def portable_state_presence(connection: Any, portable_models: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    builtin_ids = {
        table_name: {str(row["id"]) for row in rows}
        for table_name, rows in builtin_scale_tables().items()
    }
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
            count = len(ids - {RUN_ID})
        else:
            count = connection.scalar(select(func.count()).select_from(model)) or 0
        if count:
            counts[model.__table__.name] = count
    return counts
