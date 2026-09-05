from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Boolean, Integer, String, Table, Text, func, select, text

from app.domain import has_required_dependency_cycle
from app.errors import AppError
from app.models import (
    ApplicationSetting,
    CompetencyDefinition,
    CompetencyIdentity,
    CompetencyPrerequisite,
    CompetencyState,
    CompetencyStatusEvent,
    DisciplineProfile,
    ExitCriterionDefinition,
    ExitCriterionIdentity,
    ExportRecord,
    GeneratedReport,
    ImportRecord,
    Phase,
    RecommendationSnapshot,
    Roadmap,
    RoadmapVersion,
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
    _validate_roadmap_scope(connection)
    _validate_versioned_roadmap(connection)
    _validate_competency_history(connection)
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
        else:
            count = connection.scalar(select(func.count()).select_from(model)) or 0
        if count:
            counts[model.__table__.name] = count
    return counts
