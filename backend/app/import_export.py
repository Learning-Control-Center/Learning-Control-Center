from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from sqlalchemy import Engine, Table, func, insert, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.analytics import build_analytics
from app.api_serialization import serialize_api_instants
from app.auth import AuthContext, get_auth_context, require_csrf
from app.config import Settings, get_settings_dependency
from app.database import Base, create_database_engine, get_db
from app.domain import has_required_dependency_cycle, transition_status
from app.errors import AppError
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
    RoadmapVersion,
    Track,
    VerificationEvidence,
    VerificationRecord,
)
from app.schemas import (
    ExportRequest,
    ImportApplyRequest,
    ImportInspectRequest,
    RoadmapCreate,
    VerificationCreate,
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


def _analysis_payload(db: Session, request: ExportRequest) -> dict[str, Any]:
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
    selected_identity_ids: set[str] | None = (
        set(request.competency_identity_ids) if request.competency_identity_ids else None
    )
    if request.track_ids:
        track_identity_ids = set(
            db.scalars(
                select(CompetencyDefinition.competency_identity_id).where(
                    CompetencyDefinition.track_id.in_(request.track_ids)
                )
            ).all()
        )
        selected_identity_ids = (
            track_identity_ids
            if selected_identity_ids is None
            else selected_identity_ids & track_identity_ids
        )
    if request.current_phase_only:
        current = db.scalar(select(Roadmap).where(Roadmap.is_current.is_(True)))
        current_phase_identity_ids: set[str] = set()
        if current and current.current_phase_id:
            current_phase_identity_ids = set(
                db.scalars(
                    select(CompetencyDefinition.competency_identity_id).where(
                        CompetencyDefinition.phase_id == current.current_phase_id
                    )
                ).all()
            )
        selected_identity_ids = (
            current_phase_identity_ids
            if selected_identity_ids is None
            else selected_identity_ids & current_phase_identity_ids
        )
    analytics = build_analytics(
        db,
        range_name=request.range,
        start_date=start_date,
        end_date=end_date,
        competency_identity_ids=selected_identity_ids,
        track_ids=set(request.track_ids) if request.track_ids else None,
    )
    start_ms, _ = local_day_bounds_ms(start_date, profile.timezone)
    _, end_ms = local_day_bounds_ms(end_date, profile.timezone)
    sessions_query = select(LearningSession).order_by(LearningSession.started_at)
    sessions_query = sessions_query.where(
        LearningSession.started_at >= start_ms,
        LearningSession.started_at < end_ms,
    )
    if selected_identity_ids is not None:
        sessions_query = sessions_query.where(
            LearningSession.competency_identity_id.in_(selected_identity_ids)
        )
    if request.track_ids:
        sessions_query = sessions_query.where(LearningSession.track_id.in_(request.track_ids))
    verifications_query = select(VerificationRecord).order_by(VerificationRecord.created_at)
    if selected_identity_ids is not None:
        verifications_query = verifications_query.where(
            VerificationRecord.competency_identity_id.in_(selected_identity_ids)
        )
    verifications_query = verifications_query.where(
        VerificationRecord.created_at >= start_ms,
        VerificationRecord.created_at < end_ms,
    )
    categories = set(request.categories)
    include_all = not categories
    payload: dict[str, Any] = {"scope": request.model_dump(mode="json")}
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
                    GeneratedReport.period_end >= start_date.isoformat(),
                    GeneratedReport.period_start <= end_date.isoformat(),
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
    included_categories = (
        ", ".join(scope["categories"]) if scope["categories"] else "default analysis set"
    )
    return "\n".join(
        [
            "# Learning-Control-Center export",
            "",
            f"Exported: {created_at}",
            f"Range: {scope['range']}",
            f"Included categories: {included_categories}",
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


def _validate_portable_payload(payload: dict[str, Any]) -> dict[str, Any]:
    tables = payload.get("tables")
    if not isinstance(tables, dict):
        raise AppError(422, "PORTABLE_SCHEMA_INVALID", "Portable backup tables are missing.")
    unknown = set(tables) - set(PORTABLE_BY_TABLE)
    missing = set(PORTABLE_BY_TABLE) - set(tables)
    if unknown or missing:
        raise AppError(
            422,
            "PORTABLE_SCHEMA_INVALID",
            "Portable backup table coverage is invalid.",
            {"unknownTables": sorted(unknown), "missingTables": sorted(missing)},
        )
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
                violations = connection.execute(text("PRAGMA foreign_key_check")).all()
                if violations:
                    raise AppError(
                        422,
                        "PORTABLE_REFERENCES_INVALID",
                        "Portable backup references are invalid.",
                        {"violationCount": len(violations)},
                    )
                current_count = connection.scalar(
                    select(func.count(Roadmap.id)).where(Roadmap.is_current.is_(True))
                )
                roadmap_count = connection.scalar(select(func.count(Roadmap.id)))
                if roadmap_count and current_count != 1:
                    raise AppError(
                        422,
                        "PORTABLE_ROADMAP_STATE_INVALID",
                        "Configured roadmap data requires exactly one current roadmap.",
                    )
                roadmap_rows = connection.execute(
                    select(
                        Roadmap.id,
                        Roadmap.is_current,
                        Roadmap.active_version_id,
                        Roadmap.current_phase_id,
                    )
                ).all()
                for roadmap_row in roadmap_rows:
                    if roadmap_row.is_current:
                        if not roadmap_row.active_version_id or not roadmap_row.current_phase_id:
                            raise AppError(
                                422,
                                "PORTABLE_ROADMAP_STATE_INVALID",
                                "The current roadmap requires active version and phase pointers.",
                            )
                        version = connection.execute(
                            select(RoadmapVersion.id, RoadmapVersion.roadmap_id).where(
                                RoadmapVersion.id == roadmap_row.active_version_id
                            )
                        ).one()
                        phase = connection.execute(
                            select(Phase.roadmap_version_id, Phase.archived).where(
                                Phase.id == roadmap_row.current_phase_id
                            )
                        ).one()
                        if (
                            version.roadmap_id != roadmap_row.id
                            or phase.roadmap_version_id != version.id
                            or phase.archived
                        ):
                            raise AppError(
                                422,
                                "PORTABLE_ROADMAP_STATE_INVALID",
                                "The current roadmap pointers are inconsistent.",
                            )
                    elif roadmap_row.active_version_id or roadmap_row.current_phase_id:
                        raise AppError(
                            422,
                            "PORTABLE_ROADMAP_STATE_INVALID",
                            "A non-current roadmap cannot retain current pointers.",
                        )
                verified_ids = set(
                    connection.execute(
                        select(CompetencyState.competency_identity_id).where(
                            CompetencyState.current_status == "verified"
                        )
                    ).scalars()
                )
                passed_ids = set(
                    connection.execute(
                        select(VerificationRecord.competency_identity_id).where(
                            VerificationRecord.result == "passed"
                        )
                    ).scalars()
                )
                if verified_ids - passed_ids:
                    raise AppError(
                        422,
                        "PORTABLE_VERIFICATION_INVALID",
                        "Verified competency state requires passed verification history.",
                    )
                edges: dict[str, set[str]] = {}
                definitions = connection.execute(
                    select(CompetencyDefinition.id, CompetencyDefinition.competency_identity_id)
                ).all()
                definition_identity = {row.id: row.competency_identity_id for row in definitions}
                for row in connection.execute(
                    select(
                        CompetencyPrerequisite.competency_definition_id,
                        CompetencyPrerequisite.prerequisite_competency_identity_id,
                    ).where(CompetencyPrerequisite.kind == "required")
                ).all():
                    identity_id = definition_identity[row.competency_definition_id]
                    edges.setdefault(identity_id, set()).add(
                        row.prerequisite_competency_identity_id
                    )
                if has_required_dependency_cycle(edges):
                    raise AppError(
                        422,
                        "REQUIRED_DEPENDENCY_CYCLE",
                        "Required prerequisites contain a cycle.",
                    )
        finally:
            validation_engine.dispose()
    return {"tableCounts": {name: len(rows) for name, rows in sorted(tables.items())}}


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
        summary.update(_validate_portable_payload(payload.package.payload))
        summary["mode"] = "empty_state_or_full_replacement"
        summary["authenticationPreserved"] = True
    elif payload.package.packageType == "verification_update":
        records = payload.package.payload.get("verifications", [])
        if not isinstance(records, list):
            raise AppError(
                422, "VERIFICATION_UPDATE_INVALID", "Verification records must be a list."
            )
        try:
            validated_records = [VerificationCreate.model_validate(item) for item in records]
        except ValidationError as exc:
            raise AppError(
                422,
                "VERIFICATION_UPDATE_INVALID",
                "A verification update record is invalid.",
            ) from exc
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
        states = payload.package.payload.get("states", [])
        if not isinstance(states, list) or not all(isinstance(item, dict) for item in states):
            raise AppError(422, "STATE_UPDATE_INVALID", "Competency states must be a list.")
        allowed = {
            "not_started",
            "learning",
            "practicing",
            "ready_for_verification",
            "verified",
            "needs_review",
        }
        known_identities = set(db.scalars(select(CompetencyIdentity.id)).all())
        for item in states:
            identity_id = item.get("competency_identity_id")
            status = item.get("status")
            if identity_id not in known_identities or status not in allowed:
                raise AppError(422, "STATE_UPDATE_INVALID", "A competency state is invalid.")
            if status == "verified":
                verification = item.get("verification")
                try:
                    validated = VerificationCreate.model_validate(verification)
                except ValidationError as exc:
                    raise AppError(
                        422,
                        "VERIFICATION_RECORD_REQUIRED",
                        "Imported verified status requires its passed verification record.",
                    ) from exc
                if validated.result != "passed" or validated.competency_identity_id != identity_id:
                    raise AppError(
                        422,
                        "VERIFICATION_RECORD_REQUIRED",
                        "Imported verified status requires a matching passed verification record.",
                    )
        summary["statesUpdated"] = len(states)
        summary["stateChanges"] = [
            {
                "competencyIdentityId": item["competency_identity_id"],
                "toStatus": item["status"],
            }
            for item in states
        ]
    else:
        roadmap = payload.package.payload.get("roadmap")
        if not isinstance(roadmap, dict):
            raise AppError(422, "ROADMAP_PACKAGE_INVALID", "The roadmap package is invalid.")
        try:
            validated_roadmap = RoadmapCreate.model_validate(roadmap)
            from app.roadmap import validate_roadmap_payload

            validate_roadmap_payload(validated_roadmap)
        except ValidationError as exc:
            raise AppError(
                422,
                "ROADMAP_PACKAGE_INVALID",
                "The roadmap package is invalid.",
            ) from exc
        incoming = {
            item.stable_key: item
            for phase in validated_roadmap.phases
            for track in phase.tracks
            for item in track.competencies
        }
        current = db.scalar(select(Roadmap).where(Roadmap.is_current.is_(True)))
        existing: dict[str, CompetencyDefinition] = {}
        if current and current.active_version_id:
            for definition in db.scalars(
                select(CompetencyDefinition).where(
                    CompetencyDefinition.roadmap_version_id == current.active_version_id
                )
            ).all():
                identity = db.get(CompetencyIdentity, definition.competency_identity_id)
                if identity:
                    existing[identity.stable_key] = definition
        added = sorted(set(incoming) - set(existing))
        removed = sorted(set(existing) - set(incoming))
        modified = []
        for stable_key in sorted(set(incoming) & set(existing)):
            before = existing[stable_key]
            after = incoming[stable_key]
            changes: dict[str, Any] = {}
            for field, old, new in (
                ("title", before.title, after.title),
                ("priority", before.priority, after.priority),
                ("weight", before.weight, after.weight),
            ):
                if old != new:
                    changes[field] = {"from": old, "to": new}
            if changes:
                modified.append({"stableKey": stable_key, "changes": changes})
        summary["roadmap"] = {
            "stableKey": validated_roadmap.stable_key,
            "version": validated_roadmap.version,
            "operation": payload.package.packageType,
            "added": added,
            "modified": modified,
            "archivedFromActiveVersion": removed,
            "preservedStateCount": len(set(incoming) & set(existing)),
            "learningLogsTouched": False,
        }
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


def _apply_portable_restore(db: Session, payload: dict[str, Any], replace_existing: bool) -> None:
    existing_learning = sum(
        db.scalar(select(func.count(model.id))) or 0
        for model in (Roadmap, LearningSession, VerificationRecord)
    )
    if existing_learning and not replace_existing:
        raise AppError(
            409,
            "RESTORE_REPLACEMENT_CONFIRMATION_REQUIRED",
            "Existing learning state requires explicit full replacement.",
        )
    _delete_portable_state(db)
    _insert_portable_tables(db.connection(), payload["tables"])
    violations = db.execute(text("PRAGMA foreign_key_check")).all()
    if violations:
        raise AppError(422, "RESTORE_INTEGRITY_FAILED", "Post-restore integrity validation failed.")


def _apply_verification_update(db: Session, payload: dict[str, Any]) -> None:
    for raw in payload.get("verifications", []):
        item = VerificationCreate.model_validate(raw)
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
                _apply_portable_restore(db, payload.package.payload, payload.replace_existing)
            elif payload.package.packageType == "verification_update":
                _apply_verification_update(db, payload.package.payload)
            elif payload.package.packageType == "state_update":
                for item in payload.package.payload.get("states", []):
                    status = item["status"]
                    verification_id = None
                    if status == "verified":
                        verification_payload = VerificationCreate.model_validate(
                            item["verification"]
                        )
                        _apply_verification_update(
                            db, {"verifications": [verification_payload.model_dump()]}
                        )
                        continue
                    transition_status(
                        db,
                        item["competency_identity_id"],
                        status,
                        reason=item.get("reason", "Imported status update"),
                        source="import",
                        verification_record_id=verification_id,
                    )
            else:
                from app.roadmap import apply_roadmap_payload

                roadmap_payload = RoadmapCreate.model_validate(payload.package.payload["roadmap"])
                apply_roadmap_payload(db, roadmap_payload)
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
            violations = db.execute(text("PRAGMA foreign_key_check")).all()
            if violations:
                raise AppError(
                    422, "IMPORT_INTEGRITY_FAILED", "Post-import integrity validation failed."
                )
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
