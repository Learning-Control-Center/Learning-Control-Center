from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import AuthContext, get_auth_context, require_csrf
from app.database import get_db
from app.domain import active_timed_session, apply_session_promotion
from app.errors import AppError
from app.evidence import (
    create_session_evidence,
    link_contribution_to_session_evidence,
    retract_contribution_evidence_links,
    retract_session_evidence,
)
from app.models import (
    Activity,
    CompetencyIdentity,
    ContributionRetraction,
    CriterionIdentity,
    LearningSession,
    ProjectionInvalidation,
    SessionContribution,
)
from app.projects.models import (
    ActiveProjectVersionState,
    Project,
    ProjectTaskDefinition,
    ProjectVersion,
    SessionProjectContribution,
    SessionProjectContributionRetraction,
)
from app.schemas import (
    ActivityCreate,
    SessionContributionCreate,
    TimedSessionComplete,
    V2ManualSessionCreate,
    V2TimedSessionStart,
)
from app.sessions import _finalize_timed, _get_timed, serialize_session
from app.time_utils import datetime_to_epoch_ms, epoch_ms_to_rfc3339, utc_now_ms

router = APIRouter(tags=["v2 activities and sessions"])


def _validated_contribution_targets(
    db: Session, payloads: list[SessionContributionCreate]
) -> list[SessionContributionCreate]:
    if (
        sum(item.relevance == "primary" and item.target_type == "competency" for item in payloads)
        > 1
    ):
        raise AppError(
            422, "PRIMARY_CONTRIBUTION_EXISTS", "Only one Primary competency is allowed."
        )
    if sum(item.relevance == "primary" and item.target_type == "project" for item in payloads) > 1:
        raise AppError(
            422, "PRIMARY_PROJECT_CONTRIBUTION_EXISTS", "Only one Primary Project is allowed."
        )
    seen: set[tuple[str, str | None, str | None]] = set()
    for payload in payloads:
        if payload.target_type == "project":
            assert payload.project_id is not None
            project = db.get(Project, payload.project_id)
            active = db.get(ActiveProjectVersionState, payload.project_id)
            version_id = payload.project_version_id or (
                active.project_version_id if active is not None else None
            )
            version = db.get(ProjectVersion, version_id) if version_id else None
            task = (
                db.get(ProjectTaskDefinition, payload.project_task_definition_id)
                if payload.project_task_definition_id
                else None
            )
            if (
                project is None
                or version is None
                or version.project_id != project.id
                or (task is not None and task.project_version_id != version.id)
                or (payload.project_task_definition_id is not None and task is None)
            ):
                raise AppError(
                    422,
                    "SESSION_CONTRIBUTION_INVALID",
                    "Project contribution target is invalid.",
                )
            payload.project_version_id = version.id
            project_target = (project.id, version.id, task.id if task else None)
            if project_target in seen:
                raise AppError(
                    422, "SESSION_CONTRIBUTION_INVALID", "Contribution target is duplicated."
                )
            seen.add(project_target)
            continue
        assert payload.competency_identity_id is not None
        competency = db.get(CompetencyIdentity, payload.competency_identity_id)
        criterion = (
            db.get(CriterionIdentity, payload.criterion_identity_id)
            if payload.criterion_identity_id
            else None
        )
        competency_target = (
            payload.competency_identity_id,
            payload.criterion_identity_id,
            None,
        )
        if (
            competency is None
            or competency_target in seen
            or (
                payload.criterion_identity_id
                and (criterion is None or criterion.competency_identity_id != competency.id)
            )
        ):
            raise AppError(422, "SESSION_CONTRIBUTION_INVALID", "Contribution target is invalid.")
        seen.add(competency_target)
    return payloads


def _add_initial_contributions(
    db: Session,
    session: LearningSession,
    validated: list[SessionContributionCreate],
) -> None:
    for payload in validated:
        if payload.target_type == "project":
            assert payload.project_id is not None and payload.project_version_id is not None
            project_contribution = SessionProjectContribution(
                session_id=session.id,
                project_id=payload.project_id,
                project_version_id=payload.project_version_id,
                task_definition_id=payload.project_task_definition_id,
                relevance=payload.relevance,
                provenance=payload.provenance,
            )
            db.add(project_contribution)
            db.flush()
            db.add(
                ProjectionInvalidation(
                    projection_kind="analysis",
                    subject_type="learning_session",
                    subject_id=session.id,
                    source_fact_id=project_contribution.id,
                    target_policy_version="analysis-policy/v3",
                    status="pending",
                    attempt_count=0,
                    requested_at=utc_now_ms(),
                )
            )
            continue
        assert payload.competency_identity_id is not None
        competency = db.get(CompetencyIdentity, payload.competency_identity_id)
        assert competency is not None
        competency_contribution = SessionContribution(
            session_id=session.id,
            competency_identity_id=competency.id,
            criterion_identity_id=payload.criterion_identity_id,
            relevance=payload.relevance,
            provenance=payload.provenance,
        )
        db.add(competency_contribution)
        db.flush()
        if payload.relevance == "primary":
            session.competency_identity_id = competency.id
        db.add(
            ProjectionInvalidation(
                projection_kind="analysis",
                subject_type="learning_session",
                subject_id=session.id,
                source_fact_id=competency_contribution.id,
                target_policy_version="analysis-policy/v1",
                status="pending",
                attempt_count=0,
                requested_at=utc_now_ms(),
            )
        )


def _serialize_activity(item: Activity) -> dict[str, Any]:
    return {
        "id": item.id,
        "title": item.title,
        "description": item.description,
        "categoryStableKey": item.category_stable_key,
        "categoryVersion": item.category_version,
        "occurredAt": (
            epoch_ms_to_rfc3339(item.occurred_at) if item.occurred_at is not None else None
        ),
        "creatorSource": item.creator_source,
        "provenance": item.provenance,
        "createdAt": epoch_ms_to_rfc3339(item.created_at),
        "supersedesActivityId": item.supersedes_activity_id,
        "outcomeClassification": item.outcome_classification,
    }


def _serialize_session(
    db: Session, item: LearningSession, now_ms: int | None = None
) -> dict[str, Any]:
    payload = serialize_session(item, now_ms)
    retracted_ids = set(
        db.scalars(
            select(ContributionRetraction.contribution_id)
            .join(
                SessionContribution,
                SessionContribution.id == ContributionRetraction.contribution_id,
            )
            .where(SessionContribution.session_id == item.id)
        ).all()
    )
    contributions = db.scalars(
        select(SessionContribution)
        .where(SessionContribution.session_id == item.id)
        .order_by(SessionContribution.created_at, SessionContribution.id)
    ).all()
    retracted_project_ids = set(
        db.scalars(
            select(SessionProjectContributionRetraction.contribution_id)
            .join(
                SessionProjectContribution,
                SessionProjectContribution.id
                == SessionProjectContributionRetraction.contribution_id,
            )
            .where(SessionProjectContribution.session_id == item.id)
        ).all()
    )
    project_contributions = db.scalars(
        select(SessionProjectContribution)
        .where(SessionProjectContribution.session_id == item.id)
        .order_by(SessionProjectContribution.created_at, SessionProjectContribution.id)
    ).all()
    payload.update(
        {
            "activityId": item.activity_id,
            "contributions": [
                {
                    "id": contribution.id,
                    "targetType": "competency",
                    "competencyIdentityId": contribution.competency_identity_id,
                    "criterionIdentityId": contribution.criterion_identity_id,
                    "relevance": contribution.relevance,
                    "provenance": contribution.provenance,
                    "createdAt": epoch_ms_to_rfc3339(contribution.created_at),
                    "retracted": contribution.id in retracted_ids,
                }
                for contribution in contributions
            ]
            + [
                {
                    "id": contribution.id,
                    "targetType": "project",
                    "projectId": contribution.project_id,
                    "projectVersionId": contribution.project_version_id,
                    "projectTaskDefinitionId": contribution.task_definition_id,
                    "relevance": contribution.relevance,
                    "provenance": contribution.provenance,
                    "createdAt": epoch_ms_to_rfc3339(contribution.created_at),
                    "retracted": contribution.id in retracted_project_ids,
                }
                for contribution in project_contributions
            ],
        }
    )
    return payload


def _activity_or_404(db: Session, activity_id: str) -> Activity:
    activity = db.get(Activity, activity_id)
    if activity is None:
        raise AppError(404, "ACTIVITY_NOT_FOUND", "The Activity does not exist.")
    return activity


def _queue_session_invalidation(db: Session, session: LearningSession, source_fact_id: str) -> None:
    db.add(
        ProjectionInvalidation(
            projection_kind="analysis",
            subject_type="learning_session",
            subject_id=session.id,
            source_fact_id=source_fact_id,
            target_policy_version="analysis-policy/v1",
            status="pending",
            attempt_count=0,
            requested_at=utc_now_ms(),
        )
    )


@router.post("/activities", status_code=201)
async def create_activity(
    payload: ActivityCreate,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    activity = Activity(
        title=payload.title,
        description=payload.description,
        category_stable_key=payload.category_stable_key,
        category_version="v1",
        occurred_at=datetime_to_epoch_ms(payload.occurred_at) if payload.occurred_at else None,
        creator_source="user",
        provenance="user_recorded",
        outcome_classification=payload.outcome_classification,
    )
    db.add(activity)
    db.commit()
    return _serialize_activity(activity)


@router.get("/activities")
async def list_activities(
    _auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return [
        _serialize_activity(item)
        for item in db.scalars(select(Activity).order_by(Activity.created_at.desc())).all()
    ]


@router.post("/sessions/manual", status_code=201)
async def create_manual_session(
    payload: V2ManualSessionCreate,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    activity = _activity_or_404(db, payload.activity_id)
    validated = _validated_contribution_targets(db, payload.contributions)
    started_at = datetime_to_epoch_ms(payload.started_at)
    session = LearningSession(
        activity_id=activity.id,
        track_id=None,
        session_mode="manual",
        timed_state=None,
        activity_type=activity.category_stable_key,
        assistance_mode=payload.assistance_mode,
        started_at=started_at,
        ended_at=started_at + payload.duration_ms,
        accumulated_duration_ms=payload.duration_ms,
        duration_ms=payload.duration_ms,
        difficulty=payload.difficulty,
        outcome=payload.outcome,
        notes=payload.notes,
    )
    db.add(session)
    db.flush()
    _add_initial_contributions(db, session, validated)
    create_session_evidence(db, session)
    _queue_session_invalidation(db, session, session.id)
    apply_session_promotion(db, session)
    db.commit()
    return _serialize_session(db, session)


@router.post("/sessions/timed", status_code=201)
async def start_timed_session(
    payload: V2TimedSessionStart,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if active_timed_session(db) is not None:
        raise AppError(409, "ACTIVE_SESSION_EXISTS", "Complete or cancel the active Session first.")
    activity = _activity_or_404(db, payload.activity_id)
    validated = _validated_contribution_targets(db, payload.contributions)
    now = utc_now_ms()
    session = LearningSession(
        activity_id=activity.id,
        track_id=None,
        session_mode="timed",
        timed_state="running",
        activity_type=activity.category_stable_key,
        assistance_mode=payload.assistance_mode,
        started_at=now,
        active_since=now,
        accumulated_duration_ms=0,
        notes=payload.notes,
    )
    db.add(session)
    try:
        db.flush()
        _add_initial_contributions(db, session, validated)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            409, "ACTIVE_SESSION_EXISTS", "Complete or cancel the active Session first."
        ) from exc
    return _serialize_session(db, session, now)


@router.post("/sessions/{session_id}/pause")
async def pause_timed_session(
    session_id: str,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    session = _get_timed(db, session_id)
    if session.timed_state != "running" or session.active_since is None:
        raise AppError(409, "SESSION_NOT_RUNNING", "Only a running Session can be paused.")
    now = utc_now_ms()
    session.accumulated_duration_ms += now - session.active_since
    session.active_since = None
    session.timed_state = "paused"
    db.commit()
    return _serialize_session(db, session, now)


@router.post("/sessions/{session_id}/resume")
async def resume_timed_session(
    session_id: str,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    session = _get_timed(db, session_id)
    if session.timed_state != "paused" or session.active_since is not None:
        raise AppError(409, "SESSION_NOT_PAUSED", "Only a paused Session can be resumed.")
    now = utc_now_ms()
    session.active_since = now
    session.timed_state = "running"
    db.commit()
    return _serialize_session(db, session, now)


@router.post("/sessions/{session_id}/complete")
async def complete_timed_session(
    session_id: str,
    payload: TimedSessionComplete,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    session = _get_timed(db, session_id)
    now = _finalize_timed(session, cancel=False, payload=payload)
    create_session_evidence(db, session)
    _queue_session_invalidation(db, session, session.id)
    apply_session_promotion(db, session)
    db.commit()
    return _serialize_session(db, session, now)


@router.post("/sessions/{session_id}/cancel")
async def cancel_timed_session(
    session_id: str,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    session = _get_timed(db, session_id)
    now = _finalize_timed(session, cancel=True, payload=None)
    retract_session_evidence(db, session.id, "Timed Session was cancelled.")
    _queue_session_invalidation(db, session, session.id)
    db.commit()
    return _serialize_session(db, session, now)


@router.post("/sessions/{session_id}/contributions", status_code=201)
async def add_session_contribution(
    session_id: str,
    payload: SessionContributionCreate,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    session = db.get(LearningSession, session_id)
    if session is None or session.tombstoned_at is not None:
        raise AppError(404, "SESSION_NOT_FOUND", "The session does not exist.")
    if payload.target_type == "project":
        _validated_contribution_targets(db, [payload])
        assert payload.project_id is not None and payload.project_version_id is not None
        active_project_contributions = db.scalars(
            select(SessionProjectContribution)
            .outerjoin(
                SessionProjectContributionRetraction,
                SessionProjectContributionRetraction.contribution_id
                == SessionProjectContribution.id,
            )
            .where(
                SessionProjectContribution.session_id == session_id,
                SessionProjectContributionRetraction.id.is_(None),
            )
        ).all()
        if payload.relevance == "primary" and any(
            item.relevance == "primary" for item in active_project_contributions
        ):
            raise AppError(
                409,
                "PRIMARY_PROJECT_CONTRIBUTION_EXISTS",
                "The Session already has a Primary Project.",
            )
        if any(
            item.project_id == payload.project_id
            and item.project_version_id == payload.project_version_id
            and item.task_definition_id == payload.project_task_definition_id
            for item in active_project_contributions
        ):
            raise AppError(
                409, "SESSION_CONTRIBUTION_EXISTS", "The Project contribution already exists."
            )
        project_contribution = SessionProjectContribution(
            session_id=session.id,
            project_id=payload.project_id,
            project_version_id=payload.project_version_id,
            task_definition_id=payload.project_task_definition_id,
            relevance=payload.relevance,
            provenance=payload.provenance,
        )
        db.add(project_contribution)
        db.flush()
        db.add(
            ProjectionInvalidation(
                projection_kind="analysis",
                subject_type="learning_session",
                subject_id=session.id,
                source_fact_id=project_contribution.id,
                target_policy_version="analysis-policy/v3",
                status="pending",
                attempt_count=0,
                requested_at=utc_now_ms(),
            )
        )
        db.commit()
        return next(
            value
            for value in _serialize_session(db, session)["contributions"]
            if value["id"] == project_contribution.id
        )
    assert payload.competency_identity_id is not None
    competency = db.get(CompetencyIdentity, payload.competency_identity_id)
    criterion = (
        db.get(CriterionIdentity, payload.criterion_identity_id)
        if payload.criterion_identity_id
        else None
    )
    if competency is None or (
        payload.criterion_identity_id
        and (criterion is None or criterion.competency_identity_id != competency.id)
    ):
        raise AppError(422, "SESSION_CONTRIBUTION_INVALID", "Contribution target is invalid.")
    active_competency_contributions = db.scalars(
        select(SessionContribution)
        .outerjoin(
            ContributionRetraction,
            ContributionRetraction.contribution_id == SessionContribution.id,
        )
        .where(
            SessionContribution.session_id == session_id,
            ContributionRetraction.id.is_(None),
        )
    ).all()
    if payload.relevance == "primary" and any(
        item.relevance == "primary" for item in active_competency_contributions
    ):
        raise AppError(
            409, "PRIMARY_CONTRIBUTION_EXISTS", "The Session already has a Primary competency."
        )
    if any(
        item.competency_identity_id == payload.competency_identity_id
        and item.criterion_identity_id == payload.criterion_identity_id
        for item in active_competency_contributions
    ):
        raise AppError(409, "SESSION_CONTRIBUTION_EXISTS", "The contribution already exists.")
    contribution = SessionContribution(
        session_id=session_id,
        competency_identity_id=payload.competency_identity_id,
        criterion_identity_id=payload.criterion_identity_id,
        relevance=payload.relevance,
        provenance=payload.provenance,
    )
    db.add(contribution)
    db.flush()
    link_contribution_to_session_evidence(db, session_id, contribution)
    if payload.relevance == "primary":
        session.competency_identity_id = payload.competency_identity_id
        apply_session_promotion(db, session)
    db.add(
        ProjectionInvalidation(
            projection_kind="analysis",
            subject_type="learning_session",
            subject_id=session_id,
            source_fact_id=contribution.id,
            target_policy_version="analysis-policy/v1",
            status="pending",
            attempt_count=0,
            requested_at=utc_now_ms(),
        )
    )
    db.commit()
    return {"id": contribution.id, "sessionId": session_id, "relevance": contribution.relevance}


@router.delete("/sessions/{session_id}/contributions/{contribution_id}")
async def retract_session_contribution(
    session_id: str,
    contribution_id: str,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    contribution = db.get(SessionContribution, contribution_id)
    if contribution is None:
        project_contribution = db.get(SessionProjectContribution, contribution_id)
        if project_contribution is None or project_contribution.session_id != session_id:
            raise AppError(
                404, "SESSION_CONTRIBUTION_NOT_FOUND", "The contribution does not exist."
            )
        if db.scalar(
            select(SessionProjectContributionRetraction.id).where(
                SessionProjectContributionRetraction.contribution_id == contribution_id
            )
        ):
            return {"retracted": True}
        now = utc_now_ms()
        project_retraction = SessionProjectContributionRetraction(
            contribution_id=contribution_id,
            reason="Project contribution retracted through the V2 API.",
            retracted_at=now,
        )
        db.add(project_retraction)
        db.flush()
        db.add(
            ProjectionInvalidation(
                projection_kind="analysis",
                subject_type="learning_session",
                subject_id=session_id,
                source_fact_id=project_retraction.id,
                target_policy_version="analysis-policy/v3",
                status="pending",
                attempt_count=0,
                requested_at=now,
            )
        )
        db.commit()
        return {"retracted": True}
    if contribution.session_id != session_id:
        raise AppError(404, "SESSION_CONTRIBUTION_NOT_FOUND", "The contribution does not exist.")
    if db.scalar(
        select(ContributionRetraction.id).where(
            ContributionRetraction.contribution_id == contribution_id
        )
    ):
        return {"retracted": True}
    now = utc_now_ms()
    retraction = ContributionRetraction(
        contribution_id=contribution_id,
        retracted_at=now,
        source="user",
        reason="Contribution retracted through the V2 API.",
    )
    db.add(retraction)
    db.flush()
    retract_contribution_evidence_links(db, contribution)
    if contribution.relevance == "primary":
        session = db.get(LearningSession, session_id)
        assert session is not None
        session.competency_identity_id = None
    db.add(
        ProjectionInvalidation(
            projection_kind="analysis",
            subject_type="learning_session",
            subject_id=session_id,
            source_fact_id=retraction.id,
            target_policy_version="analysis-policy/v1",
            status="pending",
            attempt_count=0,
            requested_at=now,
        )
    )
    db.commit()
    return {"retracted": True}
