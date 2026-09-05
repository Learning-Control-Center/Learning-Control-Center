from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from sqlalchemy import Engine, Table, insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.analytics import build_analytics
from app.api_serialization import serialize_api_instants
from app.auth import AuthContext, get_auth_context, require_csrf
from app.config import Settings, get_settings_dependency
from app.database import Base, create_database_engine, get_db
from app.domain import transition_status
from app.domain_integrity import (
    portable_state_presence,
    validate_domain_integrity,
    validate_portable_row_types,
)
from app.errors import AppError
from app.import_diff import build_portable_replacement_diff, build_roadmap_diff
from app.models import (
    ApplicationSetting,
    CompetencyAbilityItem,
    CompetencyDefinition,
    CompetencyIdentity,
    CompetencyPrerequisite,
    CompetencyState,
    CompetencyStatusEvent,
    CompetencyUnderstandingItem,
    DailyReflection,
    DisciplineProfile,
    ExitCriterionDefinition,
    ExitCriterionIdentity,
    ExportRecord,
    GeneratedReport,
    ImportRecord,
    LearningSession,
    OperationalBackup,
    Phase,
    RecommendationSnapshot,
    Roadmap,
    RoadmapScopeEvent,
    RoadmapVersion,
    Track,
    VerificationEvidence,
    VerificationRecord,
)
from app.schemas import (
    ExportRequest,
    ImportApplyRequest,
    ImportInspectRequest,
    PortablePackagePayload,
    RoadmapCreate,
    RoadmapPackagePayload,
    StateUpdatePayload,
    VerificationUpdatePayload,
)
from app.security import RateLimitRule, new_secret, rate_limiter
from app.settings_api import get_or_create_profile
from app.time_utils import (
    epoch_ms_to_rfc3339,
    local_date_for_ms,
    local_day_bounds_ms,
    utc_now_ms,
)

router = APIRouter(prefix="/import-export", tags=["import/export"])
logger = logging.getLogger(__name__)

PORTABLE_MODELS = [
    Roadmap,
    RoadmapVersion,
    Phase,
    Track,
    RoadmapScopeEvent,
    CompetencyIdentity,
    CompetencyDefinition,
    CompetencyPrerequisite,
    CompetencyUnderstandingItem,
    CompetencyAbilityItem,
    ExitCriterionIdentity,
    ExitCriterionDefinition,
    CompetencyState,
    VerificationRecord,
    VerificationEvidence,
    CompetencyStatusEvent,
    LearningSession,
    DailyReflection,
    GeneratedReport,
    RecommendationSnapshot,
    DisciplineProfile,
    ImportRecord,
    ExportRecord,
    ApplicationSetting,
]


def _table(model: Any) -> Table:
    return cast(Table, model.__table__)


PORTABLE_BY_TABLE = {_table(model).name: model for model in PORTABLE_MODELS}


@dataclass
class Preview:
    digest: str
    token: str
    expires_at: int
    summary: dict[str, Any]


@dataclass(frozen=True)
class ResolvedExportScope:
    start_date: date
    end_date: date
    timezone: str
    selected_identity_ids: set[str] | None
    scope_payload: dict[str, Any]


_previews: dict[str, Preview] = {}


def _package_digest(package: dict[str, Any]) -> str:
    raw = json.dumps(package, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _row_dict(item: Any) -> dict[str, Any]:
    return {column.name: getattr(item, column.name) for column in _table(type(item)).columns}


def _portable_payload(db: Session) -> dict[str, Any]:
    return {
        "tables": {
            _table(model).name: [_row_dict(item) for item in db.scalars(select(model)).all()]
            for model in PORTABLE_MODELS
        }
    }


def _resolve_export_scope(db: Session, request: ExportRequest) -> ResolvedExportScope:
    profile = get_or_create_profile(db)
    today = local_date_for_ms(utc_now_ms(), profile.timezone)
    if request.range == "custom":
        start_date = date.fromisoformat(request.start_date or "")
        end_date = date.fromisoformat(request.end_date or "")
    elif request.range == "all":
        start_date = date(1, 1, 1)
        end_date = today
    else:
        days = {"7d": 7, "30d": 30, "90d": 90}[request.range]
        start_date = today - timedelta(days=days - 1)
        end_date = today
    if start_date > end_date:
        raise AppError(422, "EXPORT_RANGE_INVALID", "The export date range is invalid.")

    current = db.scalar(select(Roadmap).where(Roadmap.is_current.is_(True)))
    active_version_id = current.active_version_id if current else None
    current_tracks = (
        db.scalars(select(Track).where(Track.roadmap_version_id == active_version_id)).all()
        if active_version_id
        else []
    )
    current_definitions = (
        db.scalars(
            select(CompetencyDefinition).where(
                CompetencyDefinition.roadmap_version_id == active_version_id
            )
        ).all()
        if active_version_id
        else []
    )
    known_track_ids = {item.id for item in current_tracks}
    known_identity_ids = {item.competency_identity_id for item in current_definitions}
    if set(request.track_ids) - known_track_ids:
        raise AppError(
            422,
            "EXPORT_SCOPE_INVALID",
            "A selected track does not belong to the active roadmap version.",
        )
    if set(request.competency_identity_ids) - known_identity_ids:
        raise AppError(
            422,
            "EXPORT_SCOPE_INVALID",
            "A selected competency does not belong to the active roadmap version.",
        )

    selected_identity_ids: set[str] | None = (
        set(request.competency_identity_ids) if request.competency_identity_ids else None
    )
    if request.track_ids:
        track_identity_ids = {
            item.competency_identity_id
            for item in current_definitions
            if item.track_id in request.track_ids
        }
        selected_identity_ids = (
            track_identity_ids
            if selected_identity_ids is None
            else selected_identity_ids & track_identity_ids
        )
    if request.current_phase_only:
        current_phase_identity_ids: set[str] = set()
        if current and current.current_phase_id:
            current_phase_identity_ids = {
                item.competency_identity_id
                for item in current_definitions
                if item.phase_id == current.current_phase_id
            }
        selected_identity_ids = (
            current_phase_identity_ids
            if selected_identity_ids is None
            else selected_identity_ids & current_phase_identity_ids
        )
    identities = {
        item.id: item
        for item in db.scalars(
            select(CompetencyIdentity).where(
                CompetencyIdentity.id.in_(request.competency_identity_ids)
            )
        ).all()
    }
    definitions_by_identity = {item.competency_identity_id: item for item in current_definitions}
    resolved_categories = list(request.categories) or [
        "roadmap",
        "analytics",
        "sessions",
        "verification",
        "reports",
        "settings",
    ]
    scope_payload = request.model_dump(mode="json")
    scope_payload.update(
        {
            "resolved_start_date": start_date.isoformat(),
            "resolved_end_date": end_date.isoformat(),
            "timezone": profile.timezone,
            "resolved_categories": resolved_categories,
            "selected_tracks": [
                {"id": item.id, "stable_key": item.stable_key, "title": item.title}
                for item in current_tracks
                if item.id in request.track_ids
            ],
            "selected_competencies": [
                {
                    "identity_id": identity_id,
                    "stable_key": identities[identity_id].stable_key,
                    "title": definitions_by_identity[identity_id].title,
                }
                for identity_id in request.competency_identity_ids
            ],
        }
    )
    return ResolvedExportScope(
        start_date=start_date,
        end_date=end_date,
        timezone=profile.timezone,
        selected_identity_ids=selected_identity_ids,
        scope_payload=scope_payload,
    )


def _roadmap_analysis_payload(
    db: Session, request: ExportRequest, selected_identity_ids: set[str] | None
) -> dict[str, Any] | None:
    from app.roadmap import serialize_current_roadmap

    current = db.scalar(select(Roadmap).where(Roadmap.is_current.is_(True)))
    if current is None:
        return None
    serialized = serialize_current_roadmap(db, current)
    filtered_phases: list[dict[str, Any]] = []
    for phase in serialized["phases"]:
        if request.current_phase_only and phase["id"] != serialized["currentPhaseId"]:
            continue
        filtered_tracks: list[dict[str, Any]] = []
        for track in phase["tracks"]:
            if request.track_ids and track["id"] not in request.track_ids:
                continue
            competencies = [
                item
                for item in track["competencies"]
                if selected_identity_ids is None or item["identityId"] in selected_identity_ids
            ]
            if competencies or not request.competency_identity_ids:
                filtered_tracks.append({**track, "competencies": competencies})
        if filtered_tracks:
            filtered_phases.append({**phase, "tracks": filtered_tracks})
    return {**serialized, "phases": filtered_phases}


def _analysis_payload(db: Session, request: ExportRequest) -> dict[str, Any]:
    resolved = _resolve_export_scope(db, request)
    analytics = build_analytics(
        db,
        range_name=request.range,
        start_date=resolved.start_date,
        end_date=resolved.end_date,
        competency_identity_ids=resolved.selected_identity_ids,
        track_ids=set(request.track_ids) if request.track_ids else None,
    )
    start_ms, _ = local_day_bounds_ms(resolved.start_date, resolved.timezone)
    _, end_ms = local_day_bounds_ms(resolved.end_date, resolved.timezone)
    sessions_query = select(LearningSession).order_by(LearningSession.started_at)
    sessions_query = sessions_query.where(
        LearningSession.started_at >= start_ms,
        LearningSession.started_at < end_ms,
    )
    if resolved.selected_identity_ids is not None:
        sessions_query = sessions_query.where(
            LearningSession.competency_identity_id.in_(resolved.selected_identity_ids)
        )
    if request.track_ids:
        sessions_query = sessions_query.where(LearningSession.track_id.in_(request.track_ids))
    verifications_query = select(VerificationRecord).order_by(VerificationRecord.created_at)
    if resolved.selected_identity_ids is not None:
        verifications_query = verifications_query.where(
            VerificationRecord.competency_identity_id.in_(resolved.selected_identity_ids)
        )
    verifications_query = verifications_query.where(
        VerificationRecord.created_at >= start_ms,
        VerificationRecord.created_at < end_ms,
    )
    categories = set(request.categories)
    include_all = not categories
    payload: dict[str, Any] = {"scope": resolved.scope_payload}
    if include_all or "roadmap" in categories:
        payload["roadmap"] = _roadmap_analysis_payload(db, request, resolved.selected_identity_ids)
    if include_all or "analytics" in categories:
        payload["analytics"] = analytics
    if include_all or "sessions" in categories:
        payload["sessions"] = [_row_dict(item) for item in db.scalars(sessions_query).all()]
    if include_all or "verification" in categories:
        payload["verification"] = [
            _row_dict(item) for item in db.scalars(verifications_query).all()
        ]
    if include_all or "reports" in categories:
        payload["reports"] = [
            _row_dict(item)
            for item in db.scalars(
                select(GeneratedReport).where(
                    GeneratedReport.period_end >= resolved.start_date.isoformat(),
                    GeneratedReport.period_start <= resolved.end_date.isoformat(),
                )
            ).all()
        ]
    if include_all or "settings" in categories:
        safe_profile = db.get(DisciplineProfile, 1)
        payload["safeSettings"] = _row_dict(safe_profile) if safe_profile else None
    return payload


def _human_report(payload: dict[str, Any], created_at: str) -> str:
    scope = payload["scope"]
    analytics = payload.get("analytics", {})
    included_categories = ", ".join(scope["resolved_categories"])
    selected_tracks = (
        ", ".join(f"{item['title']} ({item['stable_key']})" for item in scope["selected_tracks"])
        or "All tracks"
    )
    selected_competencies = (
        ", ".join(
            f"{item['title']} ({item['stable_key']})" for item in scope["selected_competencies"]
        )
        or "All competencies"
    )
    return "\n".join(
        [
            "# Learning-Control-Center export",
            "",
            f"Exported: {created_at}",
            f"Range preset: {scope['range']}",
            (
                f"Resolved period: {scope['resolved_start_date']} to "
                f"{scope['resolved_end_date']} ({scope['timezone']})"
            ),
            f"Included categories: {included_categories}",
            f"Current phase only: {'yes' if scope['current_phase_only'] else 'no'}",
            f"Tracks: {selected_tracks}",
            f"Competencies: {selected_competencies}",
            "",
            "## Summary",
            "",
            f"- Total learning duration: {analytics.get('totalDurationMs', 'N/A')} ms",
            f"- Active days: {analytics.get('activeDays', 'N/A')}",
            f"- Reviews due: {analytics.get('reviewDebt', {}).get('count', 'N/A')}",
            "",
        ]
    )


def _envelope(package_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    now = utc_now_ms()
    return {
        "schemaVersion": 1,
        "packageType": package_type,
        "packageId": str(uuid.uuid4()),
        "appVersion": "1.0.0",
        "createdAt": epoch_ms_to_rfc3339(now),
        "payload": payload,
    }


def create_operational_backup(
    db: Session, settings: Settings, purpose: str, source_engine: Engine | None = None
) -> OperationalBackup:
    settings.backup_directory.mkdir(parents=True, exist_ok=True)
    settings.backup_directory.chmod(0o700)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = settings.backup_directory / f"lcc-{purpose}-{timestamp}.sqlite3"
    bound = source_engine or db.get_bind()
    backup_engine = bound if isinstance(bound, Engine) else bound.engine
    raw = backup_engine.raw_connection()
    try:
        source_connection = raw.driver_connection
        if source_connection is None:
            raise RuntimeError("The SQLite driver connection is unavailable.")
        target = sqlite3.connect(destination)
        try:
            source_connection.backup(target)
        finally:
            target.close()
    finally:
        raw.close()
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    os.chmod(destination, 0o600)
    record = OperationalBackup(
        purpose=purpose,
        path=str(destination),
        checksum_sha256=digest,
        size_bytes=destination.stat().st_size,
    )
    db.add(record)
    db.flush()
    return record


def _insert_portable_tables(connection: Any, tables: dict[str, list[dict[str, Any]]]) -> None:
    roadmap_pointers: list[tuple[str, str | None, str | None, bool]] = []
    parent_pointers: list[tuple[str, str | None]] = []
    for model in PORTABLE_MODELS:
        table_name = _table(model).name
        rows = [dict(row) for row in tables.get(table_name, [])]
        if model is Roadmap:
            for row in rows:
                roadmap_pointers.append(
                    (
                        row["id"],
                        row.get("active_version_id"),
                        row.get("current_phase_id"),
                        row.get("is_current", False),
                    )
                )
                row["active_version_id"] = None
                row["current_phase_id"] = None
                row["is_current"] = False
        if model is CompetencyDefinition:
            for row in rows:
                parent_pointers.append((row["id"], row.get("parent_definition_id")))
                row["parent_definition_id"] = None
        if rows:
            connection.execute(insert(_table(model)), rows)
    for definition_id, parent_id in parent_pointers:
        if parent_id:
            connection.execute(
                _table(CompetencyDefinition)
                .update()
                .where(CompetencyDefinition.id == definition_id)
                .values(parent_definition_id=parent_id)
            )
    for roadmap_id, version_id, phase_id, is_current in roadmap_pointers:
        connection.execute(
            _table(Roadmap)
            .update()
            .where(Roadmap.id == roadmap_id)
            .values(active_version_id=version_id, current_phase_id=phase_id, is_current=is_current)
        )


def _legacy_scope_baseline(
    tables: dict[str, list[dict[str, Any]]], package_id: str
) -> list[dict[str, Any]]:
    current_roadmaps = [row for row in tables["roadmaps"] if row.get("is_current") is True]
    if len(current_roadmaps) != 1:
        return []
    roadmap = current_roadmaps[0]
    roadmap_id = roadmap.get("id")
    version_id = roadmap.get("active_version_id")
    phase_id = roadmap.get("current_phase_id")
    occurred_at = roadmap.get("updated_at")
    if not all(isinstance(value, str) for value in (roadmap_id, version_id, phase_id)):
        return []
    if type(occurred_at) is not int:
        return []
    event_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"lcc:scope-baseline:{package_id}:{roadmap_id}:{version_id}:{phase_id}",
        )
    )
    return [
        {
            "id": event_id,
            "roadmap_id": roadmap_id,
            "roadmap_version_id": version_id,
            "phase_id": phase_id,
            "source": "restore_baseline",
            "reason": "Current scope baseline from legacy portable backup",
            "occurred_at": occurred_at,
            "event_sequence": 1,
        }
    ]


def _normalize_portable_tables(
    payload: dict[str, Any], package_id: str
) -> tuple[dict[str, list[dict[str, Any]]], bool]:
    try:
        parsed_tables = PortablePackagePayload.model_validate(payload).tables
    except ValidationError as exc:
        raise AppError(
            422, "PORTABLE_SCHEMA_INVALID", "The portable backup payload is invalid."
        ) from exc
    tables = {table_name: [dict(row) for row in rows] for table_name, rows in parsed_tables.items()}
    unknown = set(tables) - set(PORTABLE_BY_TABLE)
    missing = set(PORTABLE_BY_TABLE) - set(tables)
    legacy_without_scope_history = missing == {"roadmap_scope_events"}
    if unknown or (missing and not legacy_without_scope_history):
        raise AppError(
            422,
            "PORTABLE_SCHEMA_INVALID",
            "Portable backup table coverage is invalid.",
            {"unknownTables": sorted(unknown), "missingTables": sorted(missing)},
        )
    if legacy_without_scope_history:
        tables["roadmap_scope_events"] = _legacy_scope_baseline(tables, package_id)
    return tables, legacy_without_scope_history


def _validate_portable_payload(
    payload: dict[str, Any], package_id: str
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    tables, legacy_without_scope_history = _normalize_portable_tables(payload, package_id)
    for table_name, rows in tables.items():
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise AppError(422, "PORTABLE_SCHEMA_INVALID", f"Table {table_name} has invalid rows.")
        expected_columns = {column.name for column in _table(PORTABLE_BY_TABLE[table_name]).columns}
        if any(set(row) != expected_columns for row in rows):
            raise AppError(
                422,
                "PORTABLE_SCHEMA_INVALID",
                f"Table {table_name} has missing or unknown fields.",
            )
    validate_portable_row_types(tables, PORTABLE_BY_TABLE)
    with tempfile.NamedTemporaryFile(prefix="lcc-validate-", suffix=".sqlite3") as temporary:
        validation_engine = create_database_engine(f"sqlite:///{temporary.name}")
        try:
            Base.metadata.create_all(validation_engine)
            with validation_engine.begin() as connection:
                try:
                    _insert_portable_tables(connection, tables)
                except SQLAlchemyError as exc:
                    raise AppError(
                        422,
                        "PORTABLE_DATA_INVALID",
                        "Portable backup values violate the canonical data model.",
                    ) from exc
                validate_domain_integrity(connection)
        finally:
            validation_engine.dispose()
    return tables, {
        "tableCounts": {name: len(rows) for name, rows in sorted(tables.items())},
        "portableCompatibility": (
            "legacy_scope_baseline" if legacy_without_scope_history else "current"
        ),
        "scopeHistoryBaselineAdded": bool(
            legacy_without_scope_history and tables["roadmap_scope_events"]
        ),
    }


def _apply_roadmap_update(db: Session, payload: RoadmapCreate) -> None:
    from app.roadmap import apply_roadmap_payload

    apply_roadmap_payload(db, payload, scope_event_source="roadmap_import")


def _preflight_application(
    db: Session,
    operation: Callable[[Session], None],
    error_code: str,
    error_message: str,
) -> None:
    current_payload = _portable_payload(db)
    with tempfile.NamedTemporaryFile(prefix="lcc-preflight-", suffix=".sqlite3") as temporary:
        validation_engine = create_database_engine(f"sqlite:///{temporary.name}")
        try:
            Base.metadata.create_all(validation_engine)
            with Session(validation_engine) as validation_db:
                _insert_portable_tables(validation_db.connection(), current_payload["tables"])
                validation_db.flush()
                operation(validation_db)
                validation_db.flush()
                validate_domain_integrity(validation_db)
        except AppError:
            raise
        except SQLAlchemyError as exc:
            raise AppError(422, error_code, error_message) from exc
        finally:
            validation_engine.dispose()


def _inspect_package(
    payload: ImportInspectRequest, settings: Settings, db: Session
) -> dict[str, Any]:
    package = payload.package.model_dump(mode="json")
    size = len(json.dumps(package, separators=(",", ":")).encode("utf-8"))
    if size > settings.max_import_bytes:
        raise AppError(413, "IMPORT_TOO_LARGE", "The import package is too large.")
    if payload.package.schemaVersion != 1:
        raise AppError(
            422, "IMPORT_SCHEMA_UNSUPPORTED", "The import schema version is unsupported."
        )
    if db.scalar(
        select(ImportRecord.id).where(ImportRecord.package_id == payload.package.packageId)
    ):
        raise AppError(409, "IMPORT_PACKAGE_DUPLICATE", "This package has already been applied.")
    summary: dict[str, Any] = {
        "packageType": payload.package.packageType,
        "packageId": payload.package.packageId,
        "sizeBytes": size,
    }
    if payload.package.packageType in {"portable_logical_backup", "restore"}:
        incoming_tables, validation_summary = _validate_portable_payload(
            payload.package.payload, payload.package.packageId
        )
        summary.update(validation_summary)
        existing_state = portable_state_presence(db, PORTABLE_MODELS)
        existing_tables = _portable_payload(db)["tables"]
        summary["mode"] = "empty_state_or_full_replacement"
        summary["authenticationPreserved"] = True
        summary["existingStateEmpty"] = not bool(existing_state)
        summary["replacementRequired"] = bool(existing_state)
        summary["existingPortableStateCounts"] = existing_state
        summary["replacementDiff"] = build_portable_replacement_diff(
            existing_tables, incoming_tables, PORTABLE_BY_TABLE
        )
    elif payload.package.packageType == "verification_update":
        try:
            verification_payload = VerificationUpdatePayload.model_validate(payload.package.payload)
        except ValidationError as exc:
            raise AppError(
                422,
                "VERIFICATION_UPDATE_INVALID",
                "The verification update payload is invalid.",
            ) from exc
        validated_records = verification_payload.verifications
        known_identities = set(
            db.scalars(
                select(CompetencyIdentity.id).where(
                    CompetencyIdentity.id.in_(
                        [item.competency_identity_id for item in validated_records]
                    )
                )
            ).all()
        )
        if any(item.competency_identity_id not in known_identities for item in validated_records):
            raise AppError(
                422, "VERIFICATION_REFERENCE_INVALID", "A referenced competency does not exist."
            )
        _preflight_application(
            db,
            lambda validation_db: _apply_verification_update(validation_db, verification_payload),
            "VERIFICATION_UPDATE_INVALID",
            "The verification update cannot be applied to the current state.",
        )
        summary["verificationRecordsAdded"] = len(validated_records)
        summary["statusImplications"] = [
            {
                "competencyIdentityId": item.competency_identity_id,
                "result": item.result,
                "status": {"passed": "verified", "partial": "practicing", "failed": "needs_review"}[
                    item.result
                ],
            }
            for item in validated_records
        ]
    elif payload.package.packageType == "state_update":
        try:
            state_payload = StateUpdatePayload.model_validate(payload.package.payload)
        except ValidationError as exc:
            raise AppError(
                422, "STATE_UPDATE_INVALID", "The competency state update payload is invalid."
            ) from exc
        known_identities = set(db.scalars(select(CompetencyIdentity.id)).all())
        for item in state_payload.states:
            if item.competency_identity_id not in known_identities:
                raise AppError(422, "STATE_UPDATE_INVALID", "A competency state is invalid.")
        _preflight_application(
            db,
            lambda validation_db: _apply_state_update(validation_db, state_payload),
            "STATE_UPDATE_INVALID",
            "The competency state update cannot be applied to the current state.",
        )
        summary["statesUpdated"] = len(state_payload.states)
        summary["stateChanges"] = [
            {
                "competencyIdentityId": item.competency_identity_id,
                "toStatus": item.status,
            }
            for item in state_payload.states
        ]
    else:
        try:
            roadmap_payload = RoadmapPackagePayload.model_validate(payload.package.payload)
            validated_roadmap = roadmap_payload.roadmap
            from app.roadmap import validate_roadmap_payload

            validate_roadmap_payload(validated_roadmap)
        except ValidationError as exc:
            raise AppError(
                422,
                "ROADMAP_PACKAGE_INVALID",
                "The roadmap package is invalid.",
            ) from exc
        _preflight_application(
            db,
            lambda validation_db: _apply_roadmap_update(validation_db, validated_roadmap),
            "ROADMAP_PACKAGE_INVALID",
            "The roadmap package conflicts with the current roadmap state.",
        )
        summary["roadmap"] = build_roadmap_diff(db, validated_roadmap, payload.package.packageType)
    digest = _package_digest(package)
    token = new_secret()
    _previews[payload.package.packageId] = Preview(
        digest=digest,
        token=token,
        expires_at=utc_now_ms() + 15 * 60 * 1000,
        summary=summary,
    )
    return {
        "valid": True,
        "dryRun": True,
        "summary": summary,
        "diff": summary,
        "confirmationToken": token,
        "expiresInMs": 15 * 60 * 1000,
    }


def _delete_portable_state(db: Session) -> None:
    for roadmap in db.scalars(select(Roadmap)).all():
        roadmap.active_version_id = None
        roadmap.current_phase_id = None
        roadmap.is_current = False
    db.flush()
    for model in reversed(PORTABLE_MODELS):
        db.execute(_table(model).delete())
    db.flush()


def _current_scope(db: Session) -> tuple[str, str, str] | None:
    roadmap = db.execute(
        select(Roadmap.id, Roadmap.active_version_id, Roadmap.current_phase_id).where(
            Roadmap.is_current.is_(True)
        )
    ).first()
    if roadmap is None or roadmap.active_version_id is None or roadmap.current_phase_id is None:
        return None
    return roadmap.id, roadmap.active_version_id, roadmap.current_phase_id


def _apply_portable_restore(
    db: Session,
    payload: dict[str, Any],
    replace_existing: bool,
    *,
    package_id: str = "direct-restore",
) -> None:
    tables, legacy_without_scope_history = _normalize_portable_tables(payload, package_id)
    previous_scope = _current_scope(db)
    existing_state = portable_state_presence(db, PORTABLE_MODELS)
    if existing_state and not replace_existing:
        raise AppError(
            409,
            "RESTORE_REPLACEMENT_CONFIRMATION_REQUIRED",
            "Existing learning state requires explicit full replacement.",
            {"existingPortableStateCounts": existing_state},
        )
    _delete_portable_state(db)
    _insert_portable_tables(db.connection(), tables)
    restored_scope = _current_scope(db)
    if (
        not legacy_without_scope_history
        and restored_scope is not None
        and restored_scope != previous_scope
    ):
        from app.roadmap import record_roadmap_scope_event

        record_roadmap_scope_event(
            db,
            roadmap_id=restored_scope[0],
            roadmap_version_id=restored_scope[1],
            phase_id=restored_scope[2],
            source="portable_restore",
            reason="Current scope activated by portable restore",
        )
    validate_domain_integrity(db)


def _apply_verification_update(db: Session, payload: VerificationUpdatePayload) -> None:
    for item in payload.verifications:
        record = VerificationRecord(
            competency_identity_id=item.competency_identity_id,
            verification_source=item.verification_source,
            method=item.method,
            result=item.result,
            confidence=item.confidence,
            reviewer_label=item.reviewer_label,
            evidence_summary=item.evidence_summary,
            notes=item.notes,
        )
        db.add(record)
        db.flush()
        for evidence in item.evidence:
            db.add(
                VerificationEvidence(
                    verification_record_id=record.id,
                    kind=evidence.kind,
                    reference=evidence.reference,
                    description=evidence.description,
                )
            )
        status = {"passed": "verified", "partial": "practicing", "failed": "needs_review"}[
            item.result
        ]
        transition_status(
            db,
            item.competency_identity_id,
            status,
            reason=f"Imported verification result: {item.result}",
            source="import",
            verification_record_id=record.id if item.result == "passed" else None,
        )


def _apply_state_update(db: Session, payload: StateUpdatePayload) -> None:
    for item in payload.states:
        if item.status == "verified":
            if item.verification is None:
                raise AppError(
                    422,
                    "VERIFICATION_RECORD_REQUIRED",
                    "Imported verified status requires a matching passed verification record.",
                )
            _apply_verification_update(
                db, VerificationUpdatePayload(verifications=[item.verification])
            )
            continue
        transition_status(
            db,
            item.competency_identity_id,
            item.status,
            reason=item.reason,
            source="import",
        )


@router.post("/export")
async def export_data(
    payload: ExportRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    created_at = epoch_ms_to_rfc3339(utc_now_ms())
    if payload.purpose == "portable_logical_backup":
        result: Any = _envelope(payload.purpose, _portable_payload(db))
    else:
        analysis = _analysis_payload(db, payload)
        result = (
            _envelope(payload.purpose, analysis)
            if payload.format == "json"
            else _human_report(analysis, created_at)
        )
    record = ExportRecord(
        export_type=payload.purpose,
        format=payload.format,
        scope_summary_json=payload.model_dump_json(),
    )
    db.add(record)
    db.commit()
    return {"purpose": payload.purpose, "format": payload.format, "content": result}


@router.post("/import/inspect")
async def inspect_import(
    payload: ImportInspectRequest,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    settings: Settings = Depends(get_settings_dependency),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    import_rule = RateLimitRule(
        attempts=settings.import_rate_limit_attempts,
        window_ms=settings.import_rate_limit_window_ms,
    )
    if not rate_limiter.check("import", auth.session.id, import_rule):
        raise AppError(429, "IMPORT_RATE_LIMITED", "Too many import requests. Try again later.")
    rate_limiter.add("import", auth.session.id)
    try:
        result = _inspect_package(payload, settings, db)
    except AppError:
        logger.warning("Import inspection rejected")
        raise
    logger.info("Import inspection accepted")
    return result


@router.post("/import/apply")
async def apply_import(
    payload: ImportApplyRequest,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dependency),
) -> dict[str, Any]:
    import_rule = RateLimitRule(
        attempts=settings.import_rate_limit_attempts,
        window_ms=settings.import_rate_limit_window_ms,
    )
    if not rate_limiter.check("import", auth.session.id, import_rule):
        raise AppError(429, "IMPORT_RATE_LIMITED", "Too many import requests. Try again later.")
    rate_limiter.add("import", auth.session.id)
    package = payload.package.model_dump(mode="json")
    preview = _previews.get(payload.package.packageId)
    if (
        preview is None
        or preview.expires_at <= utc_now_ms()
        or preview.token != payload.confirmation_token
        or preview.digest != _package_digest(package)
    ):
        raise AppError(
            409,
            "IMPORT_CONFIRMATION_INVALID",
            "The validated import preview is missing, expired, or does not match.",
        )
    if db.scalar(select(ImportRecord).where(ImportRecord.package_id == payload.package.packageId)):
        raise AppError(409, "IMPORT_PACKAGE_DUPLICATE", "This package has already been applied.")
    _inspect_package(
        ImportInspectRequest(filename=payload.filename, package=payload.package), settings, db
    )
    db.rollback()
    try:
        with db.begin():
            backup = create_operational_backup(db, settings, "pre-import")
            if payload.package.packageType in {"portable_logical_backup", "restore"}:
                _apply_portable_restore(
                    db,
                    payload.package.payload,
                    payload.replace_existing,
                    package_id=payload.package.packageId,
                )
            elif payload.package.packageType == "verification_update":
                _apply_verification_update(
                    db, VerificationUpdatePayload.model_validate(payload.package.payload)
                )
            elif payload.package.packageType == "state_update":
                _apply_state_update(db, StateUpdatePayload.model_validate(payload.package.payload))
            else:
                roadmap_payload = RoadmapPackagePayload.model_validate(
                    payload.package.payload
                ).roadmap
                _apply_roadmap_update(db, roadmap_payload)
            db.add(
                ImportRecord(
                    package_id=payload.package.packageId,
                    import_type=payload.package.packageType,
                    schema_version=payload.package.schemaVersion,
                    source_filename=payload.filename,
                    dry_run_summary_json=json.dumps(preview.summary, separators=(",", ":")),
                    applied=True,
                    applied_at=utc_now_ms(),
                    pre_import_backup_reference=backup.path,
                )
            )
            validate_domain_integrity(db)
    except AppError:
        db.rollback()
        logger.warning("Import apply rejected: type=%s", payload.package.packageType)
        raise
    _previews.pop(payload.package.packageId, None)
    logger.info("Import applied successfully: type=%s", payload.package.packageType)
    return {
        "applied": True,
        "packageId": payload.package.packageId,
        "packageType": payload.package.packageType,
        "authenticationPreserved": True,
    }


@router.post("/backups/operational", status_code=201)
async def create_backup_endpoint(
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dependency),
) -> dict[str, Any]:
    record = create_operational_backup(db, settings, "manual")
    db.commit()
    logger.info("Operational backup created")
    return {
        "id": record.id,
        "createdAt": epoch_ms_to_rfc3339(record.created_at),
        "sizeBytes": record.size_bytes,
        "checksumSha256": record.checksum_sha256,
    }


@router.get("/history")
async def operation_history(
    limit: int = 50,
    offset: int = 0,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    bounded_limit = min(max(limit, 1), 200)
    bounded_offset = max(offset, 0)
    items: list[dict[str, Any]] = []
    for import_item in db.scalars(select(ImportRecord)).all():
        items.append(
            {
                "id": import_item.id,
                "operation": "import",
                "kind": import_item.import_type,
                "createdAt": import_item.created_at,
                "appliedAt": import_item.applied_at,
                "applied": import_item.applied,
                "sourceFilename": import_item.source_filename,
            }
        )
    for export_item in db.scalars(select(ExportRecord)).all():
        items.append(
            {
                "id": export_item.id,
                "operation": "export",
                "kind": export_item.export_type,
                "format": export_item.format,
                "createdAt": export_item.created_at,
            }
        )
    for backup_item in db.scalars(select(OperationalBackup)).all():
        items.append(
            {
                "id": backup_item.id,
                "operation": "backup",
                "kind": backup_item.purpose,
                "createdAt": backup_item.created_at,
                "sizeBytes": backup_item.size_bytes,
                "checksumSha256": backup_item.checksum_sha256,
            }
        )
    items.sort(key=lambda item: item["createdAt"], reverse=True)
    return serialize_api_instants(
        {
            "items": items[bounded_offset : bounded_offset + bounded_limit],
            "limit": bounded_limit,
            "offset": bounded_offset,
            "total": len(items),
        }
    )
