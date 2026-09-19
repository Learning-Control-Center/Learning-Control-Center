from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import AuthContext, get_auth_context, require_csrf
from app.curriculum.contracts import (
    ActivityUnitLinkCorrectionInput,
    ActivityUnitLinkInput,
    CurriculumActivationInput,
    CurriculumCreate,
    CurriculumVersionInput,
)
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
    LearningUnitDefinition,
)
from app.curriculum.service import (
    build_catalog,
    build_unit_availability,
    create_version,
    next_activation_sequence,
    serialize_availability,
    serialize_catalog,
    serialize_unit,
    validate_version,
)
from app.database import get_db
from app.errors import AppError
from app.models import Activity, ProjectionInvalidation
from app.roadmap_projection.policies import ACTIVE_PROJECTION_POLICY
from app.time_utils import epoch_ms_to_rfc3339, utc_now_ms

router = APIRouter(prefix="/curricula", tags=["v2 curriculum"])


def _queue_curriculum_invalidations(
    db: Session, *, subject_id: str, source_fact_id: str, requested_at: int
) -> None:
    for projection_kind, policy in (
        ("curriculum_availability", "curriculum-availability-policy/v1"),
        ("roadmap_projection_v2", ACTIVE_PROJECTION_POLICY),
        ("analysis", "analysis-policy/v3.0"),
    ):
        db.add(
            ProjectionInvalidation(
                projection_kind=projection_kind,
                subject_type="curriculum_version",
                subject_id=subject_id,
                source_fact_id=source_fact_id,
                target_policy_version=policy,
                status="pending",
                attempt_count=0,
                requested_at=requested_at,
            )
        )


def _curriculum(db: Session, curriculum_id: str) -> Curriculum:
    value = db.get(Curriculum, curriculum_id)
    if value is None:
        raise AppError(404, "CURRICULUM_NOT_FOUND", "The Curriculum does not exist.")
    return value


def _version(db: Session, curriculum_id: str, version_id: str) -> CurriculumVersion:
    value = db.get(CurriculumVersion, version_id)
    if value is None or value.curriculum_id != curriculum_id:
        raise AppError(
            404, "CURRICULUM_VERSION_NOT_FOUND", "The Curriculum version does not exist."
        )
    return value


def _serialize_version(db: Session, value: CurriculumVersion) -> dict[str, Any]:
    curriculum = db.get(Curriculum, value.curriculum_id)
    assert curriculum is not None
    objectives = db.execute(
        select(CurriculumObjectiveDefinition, CurriculumObjectiveIdentity)
        .join(
            CurriculumObjectiveIdentity,
            CurriculumObjectiveIdentity.id == CurriculumObjectiveDefinition.objective_identity_id,
        )
        .where(CurriculumObjectiveDefinition.curriculum_version_id == value.id)
        .order_by(CurriculumObjectiveDefinition.order_index)
    ).all()
    catalog = build_catalog_for_version(db, curriculum, value)
    rubrics = db.execute(
        select(AssessmentRubricDefinition, AssessmentRubricIdentity)
        .join(
            AssessmentRubricIdentity,
            AssessmentRubricIdentity.id == AssessmentRubricDefinition.rubric_identity_id,
        )
        .where(AssessmentRubricDefinition.curriculum_version_id == value.id)
        .order_by(AssessmentRubricIdentity.stable_key)
    ).all()
    return {
        "id": value.id,
        "curriculumId": value.curriculum_id,
        "curriculumStableKey": curriculum.stable_key,
        "version": value.version,
        "title": value.title,
        "description": value.description,
        "schemaVersion": value.schema_version,
        "contentHash": value.content_hash,
        "effectiveAt": epoch_ms_to_rfc3339(value.effective_at),
        "createdAt": epoch_ms_to_rfc3339(value.created_at),
        "creationSource": value.creation_source,
        "supersedesVersionId": value.supersedes_version_id,
        "objectives": [
            {
                "id": definition.id,
                "identityId": identity.id,
                "stableKey": identity.stable_key,
                "title": definition.title,
                "description": definition.description,
                "orderIndex": definition.order_index,
            }
            for definition, identity in objectives
        ],
        "units": [serialize_unit(item) for item in catalog],
        "assessmentRubrics": [
            {
                "id": definition.id,
                "identityId": identity.id,
                "stableKey": identity.stable_key,
                "title": definition.title,
                "instructions": definition.instructions,
                "rubric": json.loads(definition.rubric_json),
                "semanticDefinitionId": definition.semantic_definition_id,
                "criterionDefinitionId": definition.criterion_definition_id,
            }
            for definition, identity in rubrics
        ],
    }


def build_catalog_for_version(
    db: Session, curriculum: Curriculum, version: CurriculumVersion
) -> list[Any]:
    from app.curriculum.service import _unit_dto

    return [
        _unit_dto(db, curriculum, version, unit)
        for unit in db.scalars(
            select(LearningUnitDefinition)
            .where(LearningUnitDefinition.curriculum_version_id == version.id)
            .order_by(LearningUnitDefinition.order_index, LearningUnitDefinition.id)
        ).all()
    ]


@router.post("", status_code=201)
async def create_curriculum(
    payload: CurriculumCreate,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if db.scalar(select(Curriculum.id).where(Curriculum.stable_key == payload.stable_key)):
        raise AppError(409, "CURRICULUM_EXISTS", "The Curriculum stable key already exists.")
    value = Curriculum(stable_key=payload.stable_key, creation_source=payload.creation_source)
    db.add(value)
    db.commit()
    return {
        "id": value.id,
        "stableKey": value.stable_key,
        "createdAt": epoch_ms_to_rfc3339(value.created_at),
        "creationSource": value.creation_source,
        "activeVersionId": None,
    }


@router.get("")
async def list_curricula(
    _auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    states = {
        item.curriculum_id: item for item in db.scalars(select(ActiveCurriculumVersionState)).all()
    }
    return [
        {
            "id": item.id,
            "stableKey": item.stable_key,
            "createdAt": epoch_ms_to_rfc3339(item.created_at),
            "creationSource": item.creation_source,
            "retiredAt": epoch_ms_to_rfc3339(item.retired_at) if item.retired_at else None,
            "activeVersionId": (
                states[item.id].curriculum_version_id if item.id in states else None
            ),
        }
        for item in db.scalars(select(Curriculum).order_by(Curriculum.stable_key)).all()
    ]


@router.post("/{curriculum_id}/versions/validate")
async def validate_curriculum_version(
    curriculum_id: str,
    payload: CurriculumVersionInput,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _curriculum(db, curriculum_id)
    result = validate_version(db, payload)
    active = db.get(ActiveCurriculumVersionState, curriculum_id)
    active_version = db.get(CurriculumVersion, active.curriculum_version_id) if active else None
    result["diff"] = {
        "activeVersionId": active_version.id if active_version else None,
        "activeContentHash": active_version.content_hash if active_version else None,
        "contentChanged": active_version is None
        or active_version.content_hash != result["contentHash"],
    }
    return result


@router.post("/{curriculum_id}/versions", status_code=201)
async def create_curriculum_version(
    curriculum_id: str,
    payload: CurriculumVersionInput,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    curriculum = _curriculum(db, curriculum_id)
    value = create_version(db, curriculum, payload)
    db.commit()
    return _serialize_version(db, value)


@router.get("/{curriculum_id}/versions")
async def list_curriculum_versions(
    curriculum_id: str,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    _curriculum(db, curriculum_id)
    return [
        _serialize_version(db, item)
        for item in db.scalars(
            select(CurriculumVersion)
            .where(CurriculumVersion.curriculum_id == curriculum_id)
            .order_by(CurriculumVersion.version)
        ).all()
    ]


@router.get("/{curriculum_id}/versions/{version_id}")
async def get_curriculum_version(
    curriculum_id: str,
    version_id: str,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _serialize_version(db, _version(db, curriculum_id, version_id))


@router.post("/{curriculum_id}/versions/{version_id}/activate")
async def activate_curriculum_version(
    curriculum_id: str,
    version_id: str,
    payload: CurriculumActivationInput,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    version = _version(db, curriculum_id, version_id)
    prior = db.scalar(
        select(CurriculumActivationEvent).where(
            CurriculumActivationEvent.idempotency_key == payload.idempotency_key
        )
    )
    if prior:
        if (
            prior.curriculum_id == curriculum_id
            and prior.to_curriculum_version_id == version_id
            and prior.source == payload.source
            and prior.reason == payload.reason
        ):
            return {"curriculumId": curriculum_id, "activeVersionId": version_id}
        raise AppError(409, "IDEMPOTENCY_CONFLICT", "The idempotency key was already used.")
    state = db.get(ActiveCurriculumVersionState, curriculum_id)
    previous_id = state.curriculum_version_id if state else None
    if previous_id == version_id:
        raise AppError(409, "CURRICULUM_VERSION_ALREADY_ACTIVE", "The version is already active.")
    if state:
        previous = db.get(CurriculumVersion, previous_id)
        if previous and version.version <= previous.version:
            raise AppError(
                409,
                "CURRICULUM_ACTIVATION_INVALID",
                "Curriculum activation cannot move backward.",
            )
    now = utc_now_ms()
    if version.effective_at > now:
        raise AppError(
            409,
            "CURRICULUM_VERSION_NOT_EFFECTIVE",
            "A Curriculum version cannot be activated before its effective instant.",
        )
    event = CurriculumActivationEvent(
        curriculum_id=curriculum_id,
        from_curriculum_version_id=previous_id,
        to_curriculum_version_id=version_id,
        activated_at=now,
        source=payload.source,
        reason=payload.reason,
        event_sequence=next_activation_sequence(db, curriculum_id),
        idempotency_key=payload.idempotency_key,
    )
    db.add(event)
    if state is None:
        db.add(
            ActiveCurriculumVersionState(
                curriculum_id=curriculum_id,
                curriculum_version_id=version_id,
                activated_at=now,
            )
        )
    else:
        state.curriculum_version_id = version_id
        state.activated_at = now
    db.flush()
    _queue_curriculum_invalidations(
        db, subject_id=version_id, source_fact_id=event.id, requested_at=now
    )
    db.commit()
    return {"curriculumId": curriculum_id, "activeVersionId": version_id}


@router.get("/{curriculum_id}/activation-history")
async def curriculum_activation_history(
    curriculum_id: str,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    _curriculum(db, curriculum_id)
    return [
        {
            "id": item.id,
            "fromVersionId": item.from_curriculum_version_id,
            "toVersionId": item.to_curriculum_version_id,
            "activatedAt": epoch_ms_to_rfc3339(item.activated_at),
            "source": item.source,
            "reason": item.reason,
            "eventSequence": item.event_sequence,
            "idempotencyKey": item.idempotency_key,
        }
        for item in db.scalars(
            select(CurriculumActivationEvent)
            .where(CurriculumActivationEvent.curriculum_id == curriculum_id)
            .order_by(CurriculumActivationEvent.event_sequence)
        ).all()
    ]


@router.get("/catalog/active")
async def active_curriculum_catalog(
    cutoff_at: int = Query(default_factory=lambda: utc_now_ms() + 1),
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return serialize_catalog(build_catalog(db, cutoff_at))


@router.get("/units/{unit_definition_id}/availability")
async def learning_unit_availability(
    unit_definition_id: str,
    _cutoff_at: int = Query(default_factory=lambda: utc_now_ms() + 1, alias="cutoff_at"),
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    unit = db.get(LearningUnitDefinition, unit_definition_id)
    if unit is None:
        raise AppError(404, "LEARNING_UNIT_NOT_FOUND", "The learning unit does not exist.")
    return serialize_availability(build_unit_availability(db, unit, _cutoff_at))


@router.post("/activity-links", status_code=201)
async def link_activity_to_curriculum_unit(
    payload: ActivityUnitLinkInput,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if db.get(Activity, payload.activity_id) is None:
        raise AppError(404, "ACTIVITY_NOT_FOUND", "The Activity does not exist.")
    unit = db.get(LearningUnitDefinition, payload.learning_unit_definition_id)
    if unit is None:
        raise AppError(404, "LEARNING_UNIT_NOT_FOUND", "The learning unit does not exist.")
    prior = db.scalar(
        select(ActivityCurriculumUnitLink).where(
            ActivityCurriculumUnitLink.idempotency_key == payload.idempotency_key
        )
    )
    if prior:
        if (
            prior.activity_id == payload.activity_id
            and prior.learning_unit_definition_id == payload.learning_unit_definition_id
            and prior.provenance == payload.provenance
        ):
            return {"id": prior.id}
        raise AppError(409, "IDEMPOTENCY_CONFLICT", "The idempotency key was already used.")
    link = ActivityCurriculumUnitLink(**payload.model_dump())
    db.add(link)
    db.flush()
    _queue_curriculum_invalidations(
        db,
        subject_id=unit.curriculum_version_id,
        source_fact_id=link.id,
        requested_at=link.created_at,
    )
    db.commit()
    return {"id": link.id}


@router.post("/activity-links/{link_id}/correct", status_code=201)
async def correct_activity_curriculum_link(
    link_id: str,
    payload: ActivityUnitLinkCorrectionInput,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    link = db.get(ActivityCurriculumUnitLink, link_id)
    if link is None:
        raise AppError(404, "CURRICULUM_ACTIVITY_LINK_NOT_FOUND", "The link does not exist.")
    prior = db.scalar(
        select(ActivityCurriculumLinkCorrection).where(
            ActivityCurriculumLinkCorrection.idempotency_key == payload.idempotency_key
        )
    )
    if prior:
        if (
            prior.activity_curriculum_unit_link_id == link_id
            and prior.replacement_link_id == payload.replacement_link_id
            and prior.reason == payload.reason
        ):
            return {"id": prior.id}
        raise AppError(409, "IDEMPOTENCY_CONFLICT", "The idempotency key was already used.")
    existing_correction = db.scalar(
        select(ActivityCurriculumLinkCorrection).where(
            ActivityCurriculumLinkCorrection.activity_curriculum_unit_link_id == link_id
        )
    )
    if existing_correction is not None:
        raise AppError(
            409,
            "CURRICULUM_ACTIVITY_LINK_ALREADY_CORRECTED",
            "The Activity link already has an immutable correction.",
        )
    if payload.replacement_link_id:
        if payload.replacement_link_id == link_id:
            raise AppError(
                422,
                "CURRICULUM_LINK_REPLACEMENT_INVALID",
                "A Curriculum Activity link cannot replace itself.",
            )
        replacement = db.get(ActivityCurriculumUnitLink, payload.replacement_link_id)
        if replacement is None or replacement.activity_id != link.activity_id:
            raise AppError(
                422,
                "CURRICULUM_LINK_REPLACEMENT_INVALID",
                "A replacement must exist and refer to the same Activity.",
            )
    correction = ActivityCurriculumLinkCorrection(
        activity_curriculum_unit_link_id=link_id,
        replacement_link_id=payload.replacement_link_id,
        reason=payload.reason,
        corrected_at=utc_now_ms(),
        idempotency_key=payload.idempotency_key,
    )
    db.add(correction)
    db.flush()
    unit = db.get(LearningUnitDefinition, link.learning_unit_definition_id)
    assert unit is not None
    _queue_curriculum_invalidations(
        db,
        subject_id=unit.curriculum_version_id,
        source_fact_id=correction.id,
        requested_at=correction.corrected_at,
    )
    db.commit()
    return {"id": correction.id}
