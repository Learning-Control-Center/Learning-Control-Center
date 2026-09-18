from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import AuthContext, get_auth_context, require_csrf
from app.capability import commit_source_and_drain
from app.database import get_db
from app.errors import AppError
from app.evidence import create_project_outcome_evidence, invalidate_project_source_evidence
from app.models import (
    Activity,
    Evidence,
    EvidenceInvalidation,
    EvidenceRetraction,
    LearningSession,
    ProjectionInvalidation,
)
from app.projects.contracts import (
    ActivityProjectLinkCorrectionInput,
    ActivityProjectLinkInput,
    ProjectActivationInput,
    ProjectBlockerCorrectionInput,
    ProjectCreate,
    ProjectCriterionEvaluationInput,
    ProjectEventInput,
    ProjectEvidenceInput,
    ProjectVersionInput,
    SessionProjectContributionInput,
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
from app.projects.service import (
    PROJECT_AVAILABILITY_POLICY,
    active_project_versions_as_of,
    blockers_as_of,
    build_catalog,
    build_task_candidate,
    canonical_json,
    command_hash,
    create_version,
    evaluate_project_criterion_evidence,
    project_lifecycle_as_of,
    task_lifecycle_as_of,
    validate_version,
)
from app.time_utils import datetime_to_epoch_ms, epoch_ms_to_rfc3339, utc_now_ms

router = APIRouter(prefix="/projects", tags=["v2 projects"])


def _project(db: Session, project_id: str) -> Project:
    value = db.get(Project, project_id)
    if value is None:
        raise AppError(404, "PROJECT_NOT_FOUND", "The Project does not exist.")
    return value


def _version(db: Session, project_id: str, version_id: str) -> ProjectVersion:
    value = db.get(ProjectVersion, version_id)
    if value is None or value.project_id != project_id:
        raise AppError(404, "PROJECT_VERSION_NOT_FOUND", "The Project version does not exist.")
    return value


def _queue(db: Session, *, project_id: str, source_fact_id: str, requested_at: int) -> None:
    for kind, policy in (
        ("project_availability", PROJECT_AVAILABILITY_POLICY),
        ("roadmap_projection_v2", "roadmap-projection/v2.0"),
        ("analysis", "analysis-policy/v3"),
    ):
        db.add(
            ProjectionInvalidation(
                projection_kind=kind,
                subject_type="project",
                subject_id=project_id,
                source_fact_id=source_fact_id,
                target_policy_version=policy,
                status="pending",
                attempt_count=0,
                requested_at=requested_at,
            )
        )


def _serialize_version(db: Session, value: ProjectVersion) -> dict[str, Any]:
    project = db.get(Project, value.project_id)
    assert project is not None
    goals = db.execute(
        select(ProjectGoalDefinition, ProjectGoalIdentity)
        .join(ProjectGoalIdentity, ProjectGoalIdentity.id == ProjectGoalDefinition.goal_identity_id)
        .where(ProjectGoalDefinition.project_version_id == value.id)
        .order_by(ProjectGoalDefinition.order_index)
    ).all()
    milestones = db.execute(
        select(ProjectMilestoneDefinition, ProjectMilestoneIdentity)
        .join(
            ProjectMilestoneIdentity,
            ProjectMilestoneIdentity.id == ProjectMilestoneDefinition.milestone_identity_id,
        )
        .where(ProjectMilestoneDefinition.project_version_id == value.id)
        .order_by(ProjectMilestoneDefinition.order_index)
    ).all()
    tasks = db.execute(
        select(ProjectTaskDefinition, ProjectTaskIdentity)
        .join(ProjectTaskIdentity, ProjectTaskIdentity.id == ProjectTaskDefinition.task_identity_id)
        .where(ProjectTaskDefinition.project_version_id == value.id)
        .order_by(ProjectTaskDefinition.order_index)
    ).all()
    criteria = db.execute(
        select(ProjectCriterionDefinition, ProjectCriterionIdentity)
        .join(
            ProjectCriterionIdentity,
            ProjectCriterionIdentity.id == ProjectCriterionDefinition.criterion_identity_id,
        )
        .where(ProjectCriterionDefinition.project_version_id == value.id)
        .order_by(ProjectCriterionDefinition.order_index)
    ).all()
    return {
        "id": value.id,
        "projectId": value.project_id,
        "projectStableKey": project.stable_key,
        "version": value.version,
        "title": value.title,
        "description": value.description,
        "schemaVersion": value.schema_version,
        "contentHash": value.content_hash,
        "effectiveAt": epoch_ms_to_rfc3339(value.effective_at),
        "createdAt": epoch_ms_to_rfc3339(value.created_at),
        "creationSource": value.creation_source,
        "supersedesVersionId": value.supersedes_version_id,
        "goals": [
            {
                "id": definition.id,
                "identityId": identity.id,
                "stableKey": identity.stable_key,
                "title": definition.title,
                "description": definition.description,
                "orderIndex": definition.order_index,
            }
            for definition, identity in goals
        ],
        "milestones": [
            {
                "id": definition.id,
                "identityId": identity.id,
                "stableKey": identity.stable_key,
                "title": definition.title,
                "description": definition.description,
                "orderIndex": definition.order_index,
            }
            for definition, identity in milestones
        ],
        "tasks": [
            {
                "id": definition.id,
                "identityId": identity.id,
                "stableKey": identity.stable_key,
                "milestoneIdentityId": definition.milestone_identity_id,
                "title": definition.title,
                "description": definition.description,
                "instructions": definition.instructions,
                "status": definition.status,
                "orderIndex": definition.order_index,
                "minimumUsefulDurationMs": definition.minimum_useful_duration_ms,
                "preferredDurationMs": definition.preferred_duration_ms,
                "maximumUsefulDurationMs": definition.maximum_useful_duration_ms,
            }
            for definition, identity in tasks
        ],
        "criteria": [
            {
                "id": definition.id,
                "identityId": identity.id,
                "stableKey": identity.stable_key,
                "milestoneIdentityId": definition.milestone_identity_id,
                "title": definition.title,
                "description": definition.description,
                "evaluationPolicyVersion": definition.evaluation_policy_version,
                "orderIndex": definition.order_index,
            }
            for definition, identity in criteria
        ],
        "targets": [
            {
                "id": item.id,
                "taskDefinitionId": item.task_definition_id,
                "projectCriterionDefinitionId": item.project_criterion_definition_id,
                "semanticDefinitionId": item.semantic_definition_id,
                "criterionDefinitionId": item.criterion_definition_id,
                "scaleVersionId": item.scale_version_id,
                "dimensionId": item.dimension_id,
                "levelId": item.level_id,
                "intendedOutcome": item.intended_outcome,
                "role": item.role,
                "orderIndex": item.order_index,
            }
            for item in db.scalars(
                select(ProjectTarget)
                .where(ProjectTarget.project_version_id == value.id)
                .order_by(ProjectTarget.order_index)
            ).all()
        ],
        "requirements": [
            {
                "id": item.id,
                "taskDefinitionId": item.task_definition_id,
                "stableKey": item.stable_key,
                "requirementType": item.requirement_type,
                "effect": item.effect,
                "scope": item.scope,
                "subject": json.loads(item.subject_json),
                "orderIndex": item.order_index,
                "policyVersion": item.policy_version,
            }
            for item in db.scalars(
                select(ProjectRequirement)
                .where(ProjectRequirement.project_version_id == value.id)
                .order_by(ProjectRequirement.order_index)
            ).all()
        ],
        "dependencies": [
            {
                "id": item.id,
                "dependentTaskIdentityId": item.dependent_task_identity_id,
                "prerequisiteTaskIdentityId": item.prerequisite_task_identity_id,
                "dependencyType": item.dependency_type,
            }
            for item in db.scalars(
                select(ProjectTaskDependency)
                .where(ProjectTaskDependency.project_version_id == value.id)
                .order_by(ProjectTaskDependency.id)
            ).all()
        ],
        "evidenceOpportunities": [
            {
                "id": item.id,
                "taskDefinitionId": item.task_definition_id,
                "projectCriterionDefinitionId": item.project_criterion_definition_id,
                "stableKey": item.stable_key,
                "evidenceKind": item.evidence_kind,
                "intendedCharacteristics": json.loads(item.intended_characteristics_json),
                "orderIndex": item.order_index,
                "policyVersion": item.policy_version,
            }
            for item in db.scalars(
                select(ProjectEvidenceOpportunity)
                .where(ProjectEvidenceOpportunity.project_version_id == value.id)
                .order_by(ProjectEvidenceOpportunity.order_index)
            ).all()
        ],
    }


@router.post("", status_code=201)
async def create_project(
    payload: ProjectCreate,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if db.scalar(select(Project.id).where(Project.stable_key == payload.stable_key)):
        raise AppError(409, "PROJECT_EXISTS", "The Project stable key already exists.")
    value = Project(stable_key=payload.stable_key, creation_source=payload.creation_source)
    db.add(value)
    db.commit()
    return {
        "id": value.id,
        "stableKey": value.stable_key,
        "createdAt": epoch_ms_to_rfc3339(value.created_at),
        "creationSource": value.creation_source,
        "activeVersionId": None,
        "lifecycleState": "planned",
    }


@router.get("")
async def list_projects(
    cutoff_at: int | None = Query(default=None),
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    cutoff = cutoff_at or (utc_now_ms() + 1)
    states = {
        project.id: version.id
        for project, version, _activation in active_project_versions_as_of(db, cutoff)
    }
    return [
        {
            "id": item.id,
            "stableKey": item.stable_key,
            "createdAt": epoch_ms_to_rfc3339(item.created_at),
            "creationSource": item.creation_source,
            "activeVersionId": states.get(item.id),
            "lifecycleState": project_lifecycle_as_of(db, item.id, cutoff),
        }
        for item in db.scalars(
            select(Project)
            .where(Project.created_at < cutoff)
            .order_by(Project.stable_key)
        ).all()
    ]


@router.post("/{project_id}/versions/validate")
async def validate_project_version(
    project_id: str,
    payload: ProjectVersionInput,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _project(db, project_id)
    result = validate_version(db, payload)
    active = db.get(ActiveProjectVersionState, project_id)
    active_version = db.get(ProjectVersion, active.project_version_id) if active else None
    result["diff"] = {
        "activeVersionId": active_version.id if active_version else None,
        "activeContentHash": active_version.content_hash if active_version else None,
        "contentChanged": active_version is None
        or active_version.content_hash != result["contentHash"],
    }
    return result


@router.post("/{project_id}/versions", status_code=201)
async def create_project_version(
    project_id: str,
    payload: ProjectVersionInput,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    value = create_version(db, _project(db, project_id), payload)
    db.commit()
    return _serialize_version(db, value)


@router.get("/{project_id}/versions")
async def list_project_versions(
    project_id: str, _auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    _project(db, project_id)
    return [
        _serialize_version(db, item)
        for item in db.scalars(
            select(ProjectVersion)
            .where(ProjectVersion.project_id == project_id)
            .order_by(ProjectVersion.version)
        ).all()
    ]


@router.get("/{project_id}/versions/{version_id}")
async def get_project_version(
    project_id: str,
    version_id: str,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _serialize_version(db, _version(db, project_id, version_id))


@router.post("/{project_id}/versions/{version_id}/activate")
async def activate_project_version(
    project_id: str,
    version_id: str,
    payload: ProjectActivationInput,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = _project(db, project_id)
    version = _version(db, project_id, version_id)
    existing = db.scalar(
        select(ProjectVersionActivationEvent).where(
            ProjectVersionActivationEvent.idempotency_key == payload.idempotency_key
        )
    )
    if existing:
        if (
            existing.project_id != project.id
            or existing.to_project_version_id != version.id
            or existing.reason != payload.reason
            or existing.source != payload.source
        ):
            raise AppError(
                409, "IDEMPOTENCY_KEY_REUSED", "The activation key was used with another command."
            )
        return {
            "eventId": existing.id,
            "activeVersionId": existing.to_project_version_id,
            "eventSequence": existing.event_sequence,
        }
    now = utc_now_ms()
    if version.effective_at > now:
        raise AppError(
            422, "PROJECT_VERSION_NOT_EFFECTIVE", "A future Project version cannot be activated."
        )
    state = db.get(ActiveProjectVersionState, project.id)
    sequence = (
        db.scalar(
            select(func.max(ProjectVersionActivationEvent.event_sequence)).where(
                ProjectVersionActivationEvent.project_id == project.id
            )
        )
        or 0
    ) + 1
    event = ProjectVersionActivationEvent(
        project_id=project.id,
        from_project_version_id=state.project_version_id if state else None,
        to_project_version_id=version.id,
        activated_at=now,
        source=payload.source,
        reason=payload.reason,
        event_sequence=sequence,
        idempotency_key=payload.idempotency_key,
    )
    db.add(event)
    if state is None:
        db.add(
            ActiveProjectVersionState(
                project_id=project.id, project_version_id=version.id, activated_at=now
            )
        )
    else:
        state.project_version_id = version.id
        state.activated_at = now
    db.flush()
    _queue(db, project_id=project.id, source_fact_id=event.id, requested_at=now)
    db.commit()
    return {"eventId": event.id, "activeVersionId": version.id, "eventSequence": sequence}


def _active_version(db: Session, project_id: str) -> ProjectVersion:
    state = db.get(ActiveProjectVersionState, project_id)
    if state is None:
        raise AppError(409, "PROJECT_VERSION_NOT_ACTIVE", "The Project has no active version.")
    value = db.get(ProjectVersion, state.project_version_id)
    assert value is not None
    return value


def _validate_event_command(
    db: Session, project: Project, version: ProjectVersion, payload: ProjectEventInput, now: int
) -> None:
    task = (
        db.get(ProjectTaskIdentity, payload.task_identity_id) if payload.task_identity_id else None
    )
    if task is not None and task.project_id != project.id:
        raise AppError(422, "PROJECT_EVENT_INVALID", "The event task belongs to another Project.")
    if (
        task is not None
        and db.scalar(
            select(ProjectTaskDefinition.id).where(
                ProjectTaskDefinition.project_version_id == version.id,
                ProjectTaskDefinition.task_identity_id == task.id,
            )
        )
        is None
    ):
        raise AppError(
            422,
            "PROJECT_EVENT_INVALID",
            "The event task is not present in the active Project version.",
        )
    if payload.event_type == "project_lifecycle":
        if (
            payload.project_lifecycle_state is None
            or any((payload.task_identity_id, payload.task_lifecycle_state, payload.blocker_key))
            or payload.blocker_actionable is not None
        ):
            raise AppError(422, "PROJECT_EVENT_INVALID", "A Project lifecycle event is malformed.")
        current = project_lifecycle_as_of(db, project.id, now + 1)
        allowed = {
            "planned": {"active", "archived"},
            "active": {"completed", "archived"},
            "completed": {"archived"},
            "archived": set(),
        }
        if (
            payload.project_lifecycle_state != current
            and payload.project_lifecycle_state not in allowed[current]
        ):
            raise AppError(
                409, "PROJECT_TRANSITION_INVALID", "The Project lifecycle transition is invalid."
            )
    elif payload.event_type == "task_lifecycle":
        if (
            task is None
            or payload.task_lifecycle_state is None
            or payload.project_lifecycle_state is not None
            or payload.blocker_key is not None
            or payload.blocker_actionable is not None
        ):
            raise AppError(422, "PROJECT_EVENT_INVALID", "A task lifecycle event is malformed.")
        current = task_lifecycle_as_of(db, project.id, task.id, now + 1)
        allowed = {
            "not_started": {"started", "cancelled"},
            "started": {"completed", "cancelled"},
            "completed": set(),
            "cancelled": set(),
        }
        if (
            payload.task_lifecycle_state != current
            and payload.task_lifecycle_state not in allowed[current]
        ):
            raise AppError(
                409, "PROJECT_TRANSITION_INVALID", "The Project task transition is invalid."
            )
    else:
        if (
            task is None
            or payload.blocker_key is None
            or payload.project_lifecycle_state is not None
            or payload.task_lifecycle_state is not None
            or (payload.event_type == "blocker_opened" and payload.blocker_actionable is None)
            or (payload.event_type == "blocker_resolved" and payload.blocker_actionable is not None)
        ):
            raise AppError(422, "PROJECT_EVENT_INVALID", "A Project blocker event is malformed.")
        open_blockers = blockers_as_of(db, project.id, task.id, now + 1)
        if payload.event_type == "blocker_opened" and payload.blocker_key in open_blockers:
            raise AppError(409, "PROJECT_BLOCKER_EXISTS", "The blocker is already open.")
        if payload.event_type == "blocker_resolved" and payload.blocker_key not in open_blockers:
            raise AppError(409, "PROJECT_BLOCKER_NOT_OPEN", "The blocker is not open.")


@router.post("/{project_id}/events", status_code=201)
async def append_project_event(
    project_id: str,
    payload: ProjectEventInput,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = _project(db, project_id)
    version = _active_version(db, project_id)
    body = payload.model_dump(mode="json", exclude={"idempotency_key"})
    digest = command_hash(body)
    existing = db.scalar(
        select(ProjectEvent).where(ProjectEvent.idempotency_key == payload.idempotency_key)
    )
    if existing:
        if existing.project_id != project.id or existing.command_hash != digest:
            raise AppError(
                409, "IDEMPOTENCY_KEY_REUSED", "The event key was used with another command."
            )
        return _serialize_event(existing)
    now = utc_now_ms()
    _validate_event_command(db, project, version, payload, now)
    sequence = (
        db.scalar(
            select(func.max(ProjectEvent.event_sequence)).where(
                ProjectEvent.project_id == project.id
            )
        )
        or 0
    ) + 1
    event = ProjectEvent(
        project_id=project.id,
        project_version_id=version.id,
        event_type=payload.event_type,
        task_identity_id=payload.task_identity_id,
        project_lifecycle_state=payload.project_lifecycle_state,
        task_lifecycle_state=payload.task_lifecycle_state,
        blocker_key=payload.blocker_key,
        blocker_actionable=payload.blocker_actionable,
        payload_json=canonical_json(payload.details),
        command_hash=digest,
        occurred_at=now,
        event_sequence=sequence,
        source=payload.source,
        idempotency_key=payload.idempotency_key,
    )
    db.add(event)
    db.flush()
    _queue(db, project_id=project.id, source_fact_id=event.id, requested_at=now)
    db.commit()
    return _serialize_event(event)


def _serialize_event(item: ProjectEvent) -> dict[str, Any]:
    return {
        "id": item.id,
        "projectId": item.project_id,
        "projectVersionId": item.project_version_id,
        "eventType": item.event_type,
        "taskIdentityId": item.task_identity_id,
        "projectLifecycleState": item.project_lifecycle_state,
        "taskLifecycleState": item.task_lifecycle_state,
        "blockerKey": item.blocker_key,
        "blockerActionable": item.blocker_actionable,
        "details": json.loads(item.payload_json),
        "occurredAt": epoch_ms_to_rfc3339(item.occurred_at),
        "eventSequence": item.event_sequence,
        "source": item.source,
        "correctsEventId": item.corrects_event_id,
    }


@router.post("/{project_id}/blockers/correct", status_code=201)
async def correct_blocker(
    project_id: str,
    payload: ProjectBlockerCorrectionInput,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = _project(db, project_id)
    version = _active_version(db, project_id)
    digest = command_hash(payload.model_dump(mode="json", exclude={"idempotency_key"}))
    existing = db.scalar(
        select(ProjectEvent).where(ProjectEvent.idempotency_key == payload.idempotency_key)
    )
    if existing:
        if existing.project_id != project.id or existing.command_hash != digest:
            raise AppError(409, "IDEMPOTENCY_KEY_REUSED", "The correction key was reused.")
        return _serialize_event(existing)
    original = db.get(ProjectEvent, payload.corrects_event_id)
    if (
        original is None
        or original.project_id != project.id
        or original.event_type not in {"blocker_opened", "blocker_corrected"}
    ):
        raise AppError(
            422, "PROJECT_BLOCKER_CORRECTION_INVALID", "The corrected blocker event is invalid."
        )
    if payload.blocker_key != original.blocker_key:
        raise AppError(
            422,
            "PROJECT_BLOCKER_CORRECTION_INVALID",
            "A blocker correction cannot change the blocker identity.",
        )
    if db.scalar(select(ProjectEvent.id).where(ProjectEvent.corrects_event_id == original.id)):
        raise AppError(
            409,
            "PROJECT_BLOCKER_ALREADY_CORRECTED",
            "The blocker event already has a correction.",
        )
    assert original.task_identity_id is not None
    if original.blocker_key not in blockers_as_of(
        db, project.id, original.task_identity_id, utc_now_ms() + 1
    ):
        raise AppError(
            409,
            "PROJECT_BLOCKER_NOT_OPEN",
            "A resolved blocker cannot be corrected.",
        )
    now = utc_now_ms()
    sequence = (
        db.scalar(
            select(func.max(ProjectEvent.event_sequence)).where(
                ProjectEvent.project_id == project.id
            )
        )
        or 0
    ) + 1
    item = ProjectEvent(
        project_id=project.id,
        project_version_id=version.id,
        event_type="blocker_corrected",
        task_identity_id=original.task_identity_id,
        blocker_key=payload.blocker_key,
        blocker_actionable=payload.actionable,
        payload_json=canonical_json(payload.details),
        command_hash=digest,
        occurred_at=now,
        event_sequence=sequence,
        source=payload.source,
        idempotency_key=payload.idempotency_key,
        corrects_event_id=original.id,
    )
    db.add(item)
    db.flush()
    _queue(db, project_id=project.id, source_fact_id=item.id, requested_at=now)
    db.commit()
    return _serialize_event(item)


@router.get("/{project_id}/events")
async def project_event_history(
    project_id: str, _auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    _project(db, project_id)
    return [
        _serialize_event(item)
        for item in db.scalars(
            select(ProjectEvent)
            .where(ProjectEvent.project_id == project_id)
            .order_by(ProjectEvent.event_sequence)
        ).all()
    ]


@router.get("/catalog/current")
async def current_project_catalog(
    cutoff_at: int | None = Query(default=None),
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    value = build_catalog(db, cutoff_at or (utc_now_ms() + 1))
    return {
        "cutoffAt": value.cutoff_at,
        "cutoffSemantics": value.cutoff_semantics,
        "activeVersionReferences": [asdict(item) for item in value.active_version_references],
        "candidates": [asdict(item) for item in value.candidates],
        "inputHash": value.input_hash,
    }


@router.get("/tasks/{task_definition_id}/availability")
async def task_availability(
    task_definition_id: str,
    cutoff_at: int | None = Query(default=None),
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    task = db.get(ProjectTaskDefinition, task_definition_id)
    version = db.get(ProjectVersion, task.project_version_id) if task else None
    project = db.get(Project, version.project_id) if version else None
    if task is None or version is None or project is None:
        raise AppError(404, "PROJECT_TASK_NOT_FOUND", "The Project task does not exist.")
    cutoff = cutoff_at or (utc_now_ms() + 1)
    active_ids = {
        active_version.id
        for active_project, active_version, _event in active_project_versions_as_of(db, cutoff)
        if active_project.id == project.id
    }
    if version.id not in active_ids:
        raise AppError(
            409,
            "PROJECT_TASK_VERSION_NOT_ACTIVE",
            "Task availability is candidate-ready only for the version active at the cutoff.",
        )
    return asdict(build_task_candidate(db, project, version, task, cutoff))


@router.post("/activity-links", status_code=201)
async def create_activity_link(
    payload: ActivityProjectLinkInput,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    existing = db.scalar(
        select(ActivityProjectTaskLink).where(
            ActivityProjectTaskLink.idempotency_key == payload.idempotency_key
        )
    )
    if existing:
        if (
            existing.activity_id != payload.activity_id
            or existing.task_definition_id != payload.task_definition_id
            or existing.provenance != payload.provenance
        ):
            raise AppError(409, "IDEMPOTENCY_KEY_REUSED", "The activity-link key was reused.")
        return _serialize_activity_link(existing)
    activity = db.get(Activity, payload.activity_id)
    task = db.get(ProjectTaskDefinition, payload.task_definition_id)
    if activity is None or task is None:
        raise AppError(422, "PROJECT_ACTIVITY_LINK_INVALID", "The Activity or task does not exist.")
    duplicate = db.scalar(
        select(ActivityProjectTaskLink.id)
        .outerjoin(
            ActivityProjectTaskLinkCorrection,
            ActivityProjectTaskLinkCorrection.activity_project_task_link_id
            == ActivityProjectTaskLink.id,
        )
        .where(
            ActivityProjectTaskLink.activity_id == activity.id,
            ActivityProjectTaskLink.task_definition_id == task.id,
            ActivityProjectTaskLinkCorrection.id.is_(None),
        )
    )
    if duplicate is not None:
        raise AppError(
            409,
            "PROJECT_ACTIVITY_LINK_EXISTS",
            "The active Activity-to-Project-task link already exists.",
        )
    item = ActivityProjectTaskLink(
        activity_id=activity.id,
        task_definition_id=task.id,
        provenance=payload.provenance,
        idempotency_key=payload.idempotency_key,
    )
    db.add(item)
    db.flush()
    version = db.get(ProjectVersion, task.project_version_id)
    assert version is not None
    _queue(db, project_id=version.project_id, source_fact_id=item.id, requested_at=item.created_at)
    db.commit()
    return _serialize_activity_link(item)


def _serialize_activity_link(item: ActivityProjectTaskLink) -> dict[str, Any]:
    return {
        "id": item.id,
        "activityId": item.activity_id,
        "taskDefinitionId": item.task_definition_id,
        "provenance": item.provenance,
        "createdAt": epoch_ms_to_rfc3339(item.created_at),
    }


@router.post("/activity-links/{link_id}/correct", status_code=201)
async def correct_activity_link(
    link_id: str,
    payload: ActivityProjectLinkCorrectionInput,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    link = db.get(ActivityProjectTaskLink, link_id)
    if link is None:
        raise AppError(
            404, "PROJECT_ACTIVITY_LINK_NOT_FOUND", "The Project activity link does not exist."
        )
    existing = db.scalar(
        select(ActivityProjectTaskLinkCorrection).where(
            ActivityProjectTaskLinkCorrection.idempotency_key == payload.idempotency_key
        )
    )
    if existing:
        if (
            existing.activity_project_task_link_id != link.id
            or existing.replacement_link_id != payload.replacement_link_id
            or existing.reason != payload.reason
        ):
            raise AppError(409, "IDEMPOTENCY_KEY_REUSED", "The correction key was reused.")
        return {"id": existing.id, "correctedAt": epoch_ms_to_rfc3339(existing.corrected_at)}
    if db.scalar(
        select(ActivityProjectTaskLinkCorrection.id).where(
            ActivityProjectTaskLinkCorrection.activity_project_task_link_id == link.id
        )
    ):
        raise AppError(409, "PROJECT_ACTIVITY_LINK_CORRECTED", "The link is already corrected.")
    replacement = (
        db.get(ActivityProjectTaskLink, payload.replacement_link_id)
        if payload.replacement_link_id
        else None
    )
    if payload.replacement_link_id is not None and replacement is None:
        raise AppError(
            422,
            "PROJECT_ACTIVITY_LINK_INVALID",
            "The replacement Project activity link does not exist.",
        )
    if replacement is not None and replacement.id == link.id:
        raise AppError(
            422,
            "PROJECT_ACTIVITY_LINK_INVALID",
            "A Project activity link cannot replace itself.",
        )
    if replacement and replacement.activity_id != link.activity_id:
        raise AppError(
            422,
            "PROJECT_ACTIVITY_LINK_INVALID",
            "Replacement links must describe the same Activity.",
        )
    if replacement and db.scalar(
        select(ActivityProjectTaskLinkCorrection.id).where(
            ActivityProjectTaskLinkCorrection.activity_project_task_link_id == replacement.id
        )
    ):
        raise AppError(
            422,
            "PROJECT_ACTIVITY_LINK_INVALID",
            "A replacement Project activity link must still be active.",
        )
    now = utc_now_ms()
    item = ActivityProjectTaskLinkCorrection(
        activity_project_task_link_id=link.id,
        replacement_link_id=replacement.id if replacement else None,
        reason=payload.reason,
        corrected_at=now,
        idempotency_key=payload.idempotency_key,
    )
    db.add(item)
    db.flush()
    task = db.get(ProjectTaskDefinition, link.task_definition_id)
    version = db.get(ProjectVersion, task.project_version_id) if task else None
    assert version is not None
    _queue(db, project_id=version.project_id, source_fact_id=item.id, requested_at=now)
    invalidate_project_source_evidence(
        db,
        activity_project_task_link_id=link.id,
        reason="The source Project activity attribution was corrected.",
    )
    commit_source_and_drain(db)
    return {"id": item.id, "correctedAt": epoch_ms_to_rfc3339(item.corrected_at)}


@router.post("/{project_id}/session-contributions", status_code=201)
async def create_session_project_contribution(
    project_id: str,
    payload: SessionProjectContributionInput,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = _project(db, project_id)
    digest = command_hash(payload.model_dump(mode="json", exclude={"idempotency_key"}))
    existing_command = db.scalar(
        select(SessionProjectContribution).where(
            SessionProjectContribution.idempotency_key == payload.idempotency_key
        )
    )
    if existing_command is not None:
        if existing_command.project_id != project.id or existing_command.command_hash != digest:
            raise AppError(409, "IDEMPOTENCY_KEY_REUSED", "The contribution key was reused.")
        return _serialize_project_contribution(existing_command)
    session = db.get(LearningSession, payload.session_id)
    version = _version(db, project_id, payload.project_version_id)
    task = (
        db.get(ProjectTaskDefinition, payload.task_definition_id)
        if payload.task_definition_id
        else None
    )
    if (
        session is None
        or session.tombstoned_at is not None
        or (task is not None and task.project_version_id != version.id)
        or (payload.task_definition_id and task is None)
    ):
        raise AppError(
            422,
            "PROJECT_SESSION_CONTRIBUTION_INVALID",
            "The Project SessionContribution is inconsistent.",
        )
    duplicate = db.scalar(
        select(SessionProjectContribution.id)
        .outerjoin(
            SessionProjectContributionRetraction,
            SessionProjectContributionRetraction.contribution_id == SessionProjectContribution.id,
        )
        .where(
            SessionProjectContribution.session_id == session.id,
            SessionProjectContribution.project_id == project.id,
            SessionProjectContribution.project_version_id == version.id,
            SessionProjectContribution.task_definition_id == (task.id if task else None),
            SessionProjectContributionRetraction.id.is_(None),
        )
    )
    if duplicate is not None:
        raise AppError(
            409,
            "PROJECT_SESSION_CONTRIBUTION_EXISTS",
            "The active Project SessionContribution already exists.",
        )
    if payload.relevance == "primary":
        existing_primary = db.scalar(
            select(SessionProjectContribution.id)
            .outerjoin(
                SessionProjectContributionRetraction,
                SessionProjectContributionRetraction.contribution_id
                == SessionProjectContribution.id,
            )
            .where(
                SessionProjectContribution.session_id == session.id,
                SessionProjectContribution.relevance == "primary",
                SessionProjectContributionRetraction.id.is_(None),
            )
        )
        if existing_primary:
            raise AppError(
                409,
                "PRIMARY_PROJECT_CONTRIBUTION_EXISTS",
                "A Session may have at most one active Primary Project contribution.",
            )
    item = SessionProjectContribution(
        session_id=session.id,
        project_id=project.id,
        project_version_id=version.id,
        task_definition_id=task.id if task else None,
        relevance=payload.relevance,
        provenance=payload.provenance,
        idempotency_key=payload.idempotency_key,
        command_hash=digest,
    )
    db.add(item)
    db.flush()
    _queue(db, project_id=project.id, source_fact_id=item.id, requested_at=item.created_at)
    db.commit()
    return _serialize_project_contribution(item)


def _serialize_project_contribution(item: SessionProjectContribution) -> dict[str, Any]:
    return {
        "id": item.id,
        "sessionId": item.session_id,
        "projectId": item.project_id,
        "projectVersionId": item.project_version_id,
        "taskDefinitionId": item.task_definition_id,
        "relevance": item.relevance,
        "provenance": item.provenance,
        "createdAt": epoch_ms_to_rfc3339(item.created_at),
    }


@router.post("/{project_id}/evidence", status_code=201)
async def submit_project_evidence(
    project_id: str,
    payload: ProjectEvidenceInput,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _project(db, project_id)
    item = create_project_outcome_evidence(
        db,
        activity_project_task_link_id=payload.activity_project_task_link_id,
        opportunity_id=payload.opportunity_id,
        title=payload.title,
        description=payload.description,
        occurred_at=datetime_to_epoch_ms(payload.occurred_at),
        artifact_hash=payload.artifact_hash,
        external_reference=payload.external_reference,
        rubric_result=payload.rubric_result,
        idempotency_key=payload.idempotency_key,
    )
    provenance = json.loads(item.provenance_json)
    if provenance["project_id"] != project_id:
        raise AppError(
            422, "PROJECT_EVIDENCE_LINEAGE_INVALID", "The Evidence belongs to another Project."
        )
    _queue(db, project_id=project_id, source_fact_id=item.id, requested_at=item.created_at)
    commit_source_and_drain(db)
    return {
        "id": item.id,
        "evidenceType": item.evidence_type,
        "sourceType": item.source_type,
        "sourceId": item.source_id,
        "strength": item.strength,
        "independence": item.independence,
        "sourceConfidence": item.source_confidence,
        "occurredAt": epoch_ms_to_rfc3339(item.occurred_at) if item.occurred_at else None,
        "policyVersion": item.policy_version,
        "provenance": provenance,
    }


@router.post("/criteria/{criterion_definition_id}/evaluations", status_code=201)
async def evaluate_project_criterion(
    criterion_definition_id: str,
    payload: ProjectCriterionEvaluationInput,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    criterion = db.get(ProjectCriterionDefinition, criterion_definition_id)
    if criterion is None:
        raise AppError(404, "PROJECT_CRITERION_NOT_FOUND", "The ProjectCriterion does not exist.")
    existing = db.scalar(
        select(ProjectCriterionEvaluation).where(
            ProjectCriterionEvaluation.idempotency_key == payload.idempotency_key
        )
    )
    if existing:
        existing_ids = sorted(
            db.scalars(
                select(ProjectCriterionEvaluationEvidence.evidence_id).where(
                    ProjectCriterionEvaluationEvidence.project_criterion_evaluation_id
                    == existing.id
                )
            ).all()
        )
        if existing.project_criterion_definition_id != criterion.id or existing_ids != sorted(
            set(payload.evidence_ids)
        ):
            raise AppError(
                409,
                "IDEMPOTENCY_KEY_REUSED",
                "The evaluation key was used with another command.",
            )
        return _serialize_evaluation(db, existing)
    evidence_ids = sorted(set(payload.evidence_ids))
    if len(evidence_ids) != len(payload.evidence_ids):
        raise AppError(
            422, "PROJECT_CRITERION_EVIDENCE_INVALID", "Evaluation Evidence IDs must be unique."
        )
    evidence_rows = [db.get(Evidence, item) for item in evidence_ids]
    if any(item is None for item in evidence_rows):
        raise AppError(422, "PROJECT_CRITERION_EVIDENCE_INVALID", "Evaluation Evidence is missing.")
    for evidence in evidence_rows:
        assert evidence is not None
        provenance = json.loads(evidence.provenance_json)
        if (
            evidence.evidence_type not in {"project", "code", "assessment", "manual", "review"}
            or provenance.get("project_criterion_definition_id") != criterion.id
        ):
            raise AppError(
                422,
                "PROJECT_CRITERION_EVIDENCE_INVALID",
                "Evaluation requires qualifying Project Evidence for this exact criterion.",
            )
        retracted = db.scalar(
            select(EvidenceRetraction.id).where(EvidenceRetraction.evidence_id == evidence.id)
        )
        invalidated = db.scalar(
            select(EvidenceInvalidation.id).where(EvidenceInvalidation.evidence_id == evidence.id)
        )
        if retracted or invalidated:
            raise AppError(
                422,
                "PROJECT_CRITERION_EVIDENCE_INVALID",
                "Retracted or invalidated Evidence cannot qualify.",
            )
    now = utc_now_ms()
    evidence_facts = tuple(
        {
            "evidenceId": evidence.id,
            "strength": evidence.strength,
            "independence": evidence.independence,
            "sourceConfidence": evidence.source_confidence,
        }
        for evidence in evidence_rows
        if evidence is not None
    )
    state, policy_facts = evaluate_project_criterion_evidence(evidence_facts)
    facts = {
        **policy_facts,
        "evidenceIds": evidence_ids,
        "criterionDefinitionId": criterion.id,
    }
    item = ProjectCriterionEvaluation(
        project_criterion_definition_id=criterion.id,
        state=state,
        evaluated_at=now,
        policy_version=criterion.evaluation_policy_version,
        facts_json=canonical_json(facts),
        evidence_set_hash=command_hash(evidence_ids),
        idempotency_key=payload.idempotency_key,
    )
    db.add(item)
    db.flush()
    for evidence_id in evidence_ids:
        db.add(
            ProjectCriterionEvaluationEvidence(
                project_criterion_evaluation_id=item.id, evidence_id=evidence_id
            )
        )
    version = db.get(ProjectVersion, criterion.project_version_id)
    assert version is not None
    _queue(db, project_id=version.project_id, source_fact_id=item.id, requested_at=now)
    db.commit()
    return _serialize_evaluation(db, item)


def _serialize_evaluation(db: Session, item: ProjectCriterionEvaluation) -> dict[str, Any]:
    return {
        "id": item.id,
        "projectCriterionDefinitionId": item.project_criterion_definition_id,
        "state": item.state,
        "evaluatedAt": epoch_ms_to_rfc3339(item.evaluated_at),
        "policyVersion": item.policy_version,
        "facts": json.loads(item.facts_json),
        "evidenceSetHash": item.evidence_set_hash,
        "evidenceIds": sorted(
            db.scalars(
                select(ProjectCriterionEvaluationEvidence.evidence_id).where(
                    ProjectCriterionEvaluationEvidence.project_criterion_evaluation_id == item.id
                )
            ).all()
        ),
    }


@router.get("/criteria/{criterion_definition_id}/evaluations")
async def project_criterion_history(
    criterion_definition_id: str,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    if db.get(ProjectCriterionDefinition, criterion_definition_id) is None:
        raise AppError(404, "PROJECT_CRITERION_NOT_FOUND", "The ProjectCriterion does not exist.")
    return [
        _serialize_evaluation(db, item)
        for item in db.scalars(
            select(ProjectCriterionEvaluation)
            .where(
                ProjectCriterionEvaluation.project_criterion_definition_id
                == criterion_definition_id
            )
            .order_by(ProjectCriterionEvaluation.evaluated_at, ProjectCriterionEvaluation.id)
        ).all()
    ]
