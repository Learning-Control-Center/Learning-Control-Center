from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import AuthContext, get_auth_context, require_csrf
from app.database import get_db
from app.errors import AppError
from app.models import (
    ActiveCompetencyDefinitionState,
    ActiveTargetProfileState,
    CapabilityScaleDimension,
    CapabilityScaleLevel,
    CapabilityScaleVersion,
    CompetencyDefinitionActivationEvent,
    CompetencyIdentity,
    CompetencyState,
    CompetencyStatusEvent,
    CriterionDefinition,
    CriterionIdentity,
    MilestoneIdentity,
    ProfileDomain,
    ProfileMilestone,
    ProfileMilestoneTarget,
    ProfileTarget,
    ProfileTargetIdentity,
    ReadinessGate,
    ReadinessGateIdentity,
    ReadinessGatePredicate,
    ReadinessGateTarget,
    SemanticCompetencyDefinition,
    SemanticDefinitionDimension,
    TargetProfile,
    TargetProfileActivationEvent,
    TargetProfileVersion,
    new_id,
)
from app.schemas import (
    ActivationRequest,
    CompetencyIdentityCreate,
    ReadinessPredicateInput,
    SemanticCompetencyDefinitionCreate,
    TargetProfileCreate,
    TargetProfileVersionCreate,
)
from app.time_utils import datetime_to_epoch_ms, epoch_ms_to_rfc3339, utc_now_ms

router = APIRouter(tags=["v2 profile and competency"])


def _unique(values: Iterable[object], label: str) -> None:
    materialized = list(values)
    if len(set(materialized)) != len(materialized):
        raise AppError(422, "PROFILE_VERSION_INVALID", f"{label} values must be unique.")


def _scale(db: Session, stable_key: str, version: str) -> CapabilityScaleVersion:
    value = db.scalar(
        select(CapabilityScaleVersion).where(
            CapabilityScaleVersion.scale_stable_key == stable_key,
            CapabilityScaleVersion.scale_version == version,
        )
    )
    if value is None:
        raise AppError(422, "CAPABILITY_SCALE_INVALID", "The capability scale does not exist.")
    return value


def _scale_members(
    db: Session, scale: CapabilityScaleVersion
) -> tuple[dict[str, CapabilityScaleLevel], dict[str, CapabilityScaleDimension]]:
    levels = {
        item.stable_key: item
        for item in db.scalars(
            select(CapabilityScaleLevel).where(CapabilityScaleLevel.scale_version_id == scale.id)
        ).all()
    }
    dimensions = {
        item.stable_key: item
        for item in db.scalars(
            select(CapabilityScaleDimension).where(
                CapabilityScaleDimension.scale_version_id == scale.id
            )
        ).all()
    }
    return levels, dimensions


def _validate_gate_subject(
    predicate: ReadinessPredicateInput,
    db: Session,
    target_identities: dict[str, ProfileTargetIdentity],
) -> None:
    subject = predicate.subject
    if predicate.predicate_type == "project_criterion_demonstrated":
        raise AppError(
            422,
            "FEATURE_NOT_AVAILABLE",
            "Project readiness predicates are unavailable until the Projects domain exists.",
        )
    expected: dict[str, set[str]] = {
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
    if set(subject) != expected[predicate.predicate_type] or any(
        not isinstance(value, str) or not value for value in subject.values() if value is not None
    ):
        raise AppError(
            422,
            "READINESS_PREDICATE_INVALID",
            "The readiness predicate subject does not match its declared type.",
        )
    if predicate.predicate_type == "capability_at_least":
        competency = db.get(CompetencyIdentity, subject["competencyIdentityId"])
        scale = _scale(db, subject["scaleStableKey"], subject["scaleVersion"])
        levels, dimensions = _scale_members(db, scale)
        dimension = subject["dimensionKey"]
        if (
            competency is None
            or subject["levelStableKey"] not in levels
            or (dimension is not None and dimension not in dimensions)
            or (scale.scale_stable_key == "technical" and dimension is not None)
        ):
            raise AppError(
                422, "READINESS_PREDICATE_INVALID", "The capability predicate is inconsistent."
            )
    elif predicate.predicate_type == "criterion_demonstrated":
        if db.get(CriterionIdentity, subject["criterionIdentityId"]) is None:
            raise AppError(422, "READINESS_PREDICATE_INVALID", "The criterion does not exist.")


def _serialize_scale(db: Session, scale: CapabilityScaleVersion) -> dict[str, Any]:
    levels, dimensions = _scale_members(db, scale)
    return {
        "id": scale.id,
        "stableKey": scale.scale_stable_key,
        "version": scale.scale_version,
        "displayName": scale.display_name,
        "description": scale.description,
        "dimensions": [
            {
                "id": item.id,
                "stableKey": item.stable_key,
                "displayLabel": item.display_label,
                "description": item.description,
                "orderIndex": item.order_index,
            }
            for item in sorted(dimensions.values(), key=lambda value: value.order_index)
        ],
        "levels": [
            {
                "id": item.id,
                "stableKey": item.stable_key,
                "ordinalRank": item.ordinal_rank,
                "displayLabel": item.display_label,
                "description": item.description,
                "criterionPolicyReference": item.criterion_policy_reference,
                "evidencePolicyReference": item.evidence_policy_reference,
            }
            for item in sorted(levels.values(), key=lambda value: value.ordinal_rank)
        ],
    }


@router.get("/capability-scales")
async def list_capability_scales(
    _auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return [
        _serialize_scale(db, item)
        for item in db.scalars(
            select(CapabilityScaleVersion).order_by(
                CapabilityScaleVersion.scale_stable_key,
                CapabilityScaleVersion.scale_version,
            )
        ).all()
    ]


@router.post("/competencies", status_code=201)
async def create_competency_identity(
    payload: CompetencyIdentityCreate,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if db.scalar(
        select(CompetencyIdentity.id).where(CompetencyIdentity.stable_key == payload.stable_key)
    ):
        raise AppError(409, "COMPETENCY_IDENTITY_EXISTS", "The stable key already exists.")
    created_at = utc_now_ms()
    identity = CompetencyIdentity(
        stable_key=payload.stable_key,
        identity_created_at=created_at,
        creation_source=payload.creation_source,
    )
    db.add(identity)
    db.flush()
    db.add(
        CompetencyState(
            competency_identity_id=identity.id,
            current_status="not_started",
            updated_at=created_at,
        )
    )
    db.add(
        CompetencyStatusEvent(
            competency_identity_id=identity.id,
            from_status=None,
            to_status="not_started",
            reason="Competency introduced through the V2 semantic API",
            source="v2_semantic_definition",
            created_at=created_at,
        )
    )
    db.commit()
    return {
        "id": identity.id,
        "stableKey": identity.stable_key,
        "createdAt": epoch_ms_to_rfc3339(created_at),
        "creationSource": identity.creation_source,
    }


def _serialize_semantic_definition(
    db: Session, definition: SemanticCompetencyDefinition
) -> dict[str, Any]:
    scale = db.get(CapabilityScaleVersion, definition.scale_version_id)
    dimensions = db.scalars(
        select(CapabilityScaleDimension)
        .join(
            SemanticDefinitionDimension,
            SemanticDefinitionDimension.scale_dimension_id == CapabilityScaleDimension.id,
        )
        .where(SemanticDefinitionDimension.semantic_definition_id == definition.id)
        .order_by(CapabilityScaleDimension.order_index)
    ).all()
    criteria = db.scalars(
        select(CriterionDefinition)
        .where(CriterionDefinition.semantic_definition_id == definition.id)
        .order_by(CriterionDefinition.created_at, CriterionDefinition.id)
    ).all()
    identities = {
        item.id: item
        for item in db.scalars(
            select(CriterionIdentity).where(
                CriterionIdentity.id.in_([item.criterion_identity_id for item in criteria])
            )
        ).all()
    }
    levels = {
        item.id: item
        for item in db.scalars(
            select(CapabilityScaleLevel).where(
                CapabilityScaleLevel.id.in_([item.level_id for item in criteria])
            )
        ).all()
    }
    dimension_by_id = {item.id: item for item in dimensions}
    return {
        "id": definition.id,
        "competencyIdentityId": definition.competency_identity_id,
        "definitionVersion": definition.definition_version,
        "title": definition.title,
        "description": definition.description,
        "scope": definition.scope,
        "scaleStableKey": scale.scale_stable_key if scale else None,
        "scaleVersion": scale.scale_version if scale else None,
        "dimensionKeys": [item.stable_key for item in dimensions],
        "createdAt": epoch_ms_to_rfc3339(definition.created_at),
        "effectiveAt": epoch_ms_to_rfc3339(definition.effective_at),
        "creationSource": definition.creation_source,
        "supersedesDefinitionId": definition.supersedes_definition_id,
        "criteria": [
            {
                "id": item.id,
                "identityId": item.criterion_identity_id,
                "stableKey": identities[item.criterion_identity_id].stable_key,
                "definitionVersion": item.definition_version,
                "levelStableKey": levels[item.level_id].stable_key,
                "dimensionKey": (
                    dimension_by_id[item.dimension_id].stable_key if item.dimension_id else None
                ),
                "requirementType": item.requirement_type,
                "demonstrationRule": json.loads(item.demonstration_rule_json)["rule"],
                "description": item.description,
                "verificationRubric": item.verification_rubric,
                "importanceWeight": item.importance_weight,
                "supersedesDefinitionId": item.supersedes_definition_id,
            }
            for item in criteria
        ],
    }


@router.post("/competencies/{competency_identity_id}/definitions", status_code=201)
async def create_semantic_definition(
    competency_identity_id: str,
    payload: SemanticCompetencyDefinitionCreate,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    identity = db.get(CompetencyIdentity, competency_identity_id)
    if identity is None:
        raise AppError(404, "COMPETENCY_NOT_FOUND", "The competency identity does not exist.")
    scale = _scale(db, payload.scale_stable_key, payload.scale_version)
    levels, dimensions = _scale_members(db, scale)
    _unique(payload.dimension_keys, "Definition dimension keys")
    _unique((item.stable_key for item in payload.criteria), "Criterion stable keys")
    if payload.scale_stable_key == "technical" and payload.dimension_keys:
        raise AppError(
            422, "CAPABILITY_DIMENSION_INVALID", "Technical definitions are dimensionless."
        )
    if payload.scale_stable_key == "cefr" and not payload.dimension_keys:
        raise AppError(422, "CAPABILITY_DIMENSION_INVALID", "CEFR definitions require dimensions.")
    if any(key not in dimensions for key in payload.dimension_keys):
        raise AppError(422, "CAPABILITY_DIMENSION_INVALID", "A definition dimension is invalid.")
    for criterion in payload.criteria:
        if criterion.level_stable_key not in levels:
            raise AppError(422, "CRITERION_SCALE_INVALID", "A criterion level is invalid.")
        if (
            criterion.dimension_key is not None
            and criterion.dimension_key not in payload.dimension_keys
        ):
            raise AppError(422, "CRITERION_SCALE_INVALID", "A criterion dimension is not enabled.")
        if payload.scale_stable_key == "technical" and criterion.dimension_key is not None:
            raise AppError(422, "CRITERION_SCALE_INVALID", "Technical criteria are dimensionless.")

    previous = db.scalar(
        select(SemanticCompetencyDefinition)
        .where(SemanticCompetencyDefinition.competency_identity_id == competency_identity_id)
        .order_by(SemanticCompetencyDefinition.definition_version.desc())
        .limit(1)
    )
    now = utc_now_ms()
    definition = SemanticCompetencyDefinition(
        competency_identity_id=competency_identity_id,
        definition_version=1 if previous is None else previous.definition_version + 1,
        title=payload.title,
        description=payload.description,
        scope=payload.scope,
        scale_version_id=scale.id,
        created_at=now,
        effective_at=datetime_to_epoch_ms(payload.effective_at),
        creation_source=payload.creation_source,
        supersedes_definition_id=previous.id if previous else None,
    )
    db.add(definition)
    db.flush()
    for key in payload.dimension_keys:
        db.add(
            SemanticDefinitionDimension(
                semantic_definition_id=definition.id,
                scale_dimension_id=dimensions[key].id,
            )
        )
    for criterion_input in payload.criteria:
        criterion_identity = db.scalar(
            select(CriterionIdentity).where(
                CriterionIdentity.competency_identity_id == competency_identity_id,
                CriterionIdentity.stable_key == criterion_input.stable_key,
            )
        )
        if criterion_identity is None:
            criterion_identity = CriterionIdentity(
                competency_identity_id=competency_identity_id,
                stable_key=criterion_input.stable_key,
                created_at=now,
                creation_source=payload.creation_source,
            )
            db.add(criterion_identity)
            db.flush()
        previous_criterion = db.scalar(
            select(CriterionDefinition)
            .where(CriterionDefinition.criterion_identity_id == criterion_identity.id)
            .order_by(CriterionDefinition.definition_version.desc())
            .limit(1)
        )
        db.add(
            CriterionDefinition(
                criterion_identity_id=criterion_identity.id,
                semantic_definition_id=definition.id,
                definition_version=(
                    1 if previous_criterion is None else previous_criterion.definition_version + 1
                ),
                level_id=levels[criterion_input.level_stable_key].id,
                dimension_id=(
                    dimensions[criterion_input.dimension_key].id
                    if criterion_input.dimension_key
                    else None
                ),
                requirement_type=criterion_input.requirement_type,
                demonstration_rule_json=json.dumps(
                    {"rule": criterion_input.demonstration_rule},
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                description=criterion_input.description,
                verification_rubric=criterion_input.verification_rubric,
                importance_weight=criterion_input.importance_weight,
                created_at=now,
                supersedes_definition_id=previous_criterion.id if previous_criterion else None,
            )
        )
    db.commit()
    return _serialize_semantic_definition(db, definition)


@router.get("/competencies/{competency_identity_id}/definitions")
async def list_semantic_definitions(
    competency_identity_id: str,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return [
        _serialize_semantic_definition(db, item)
        for item in db.scalars(
            select(SemanticCompetencyDefinition)
            .where(SemanticCompetencyDefinition.competency_identity_id == competency_identity_id)
            .order_by(SemanticCompetencyDefinition.definition_version)
        ).all()
    ]


@router.get("/competencies/{competency_identity_id}/definitions/{definition_id}")
async def get_semantic_definition(
    competency_identity_id: str,
    definition_id: str,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    definition = db.get(SemanticCompetencyDefinition, definition_id)
    if definition is None or definition.competency_identity_id != competency_identity_id:
        raise AppError(404, "COMPETENCY_DEFINITION_NOT_FOUND", "The definition does not exist.")
    return _serialize_semantic_definition(db, definition)


@router.post("/competencies/{competency_identity_id}/definitions/{definition_id}/activate")
async def activate_semantic_definition(
    competency_identity_id: str,
    definition_id: str,
    payload: ActivationRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    definition = db.get(SemanticCompetencyDefinition, definition_id)
    if definition is None or definition.competency_identity_id != competency_identity_id:
        raise AppError(404, "COMPETENCY_DEFINITION_NOT_FOUND", "The definition does not exist.")
    if payload.idempotency_key:
        prior_event = db.scalar(
            select(CompetencyDefinitionActivationEvent).where(
                CompetencyDefinitionActivationEvent.idempotency_key == payload.idempotency_key
            )
        )
        if prior_event is not None:
            if (
                prior_event.competency_identity_id == competency_identity_id
                and prior_event.to_definition_id == definition_id
            ):
                return {
                    "competencyIdentityId": competency_identity_id,
                    "activeDefinitionId": definition.id,
                }
            raise AppError(409, "IDEMPOTENCY_CONFLICT", "The idempotency key was already used.")
    state = db.get(ActiveCompetencyDefinitionState, competency_identity_id)
    previous_id = state.semantic_definition_id if state else None
    if previous_id == definition.id:
        raise AppError(409, "DEFINITION_ALREADY_ACTIVE", "The definition is already active.")
    if state is not None:
        previous = db.get(SemanticCompetencyDefinition, previous_id)
        if previous and definition.definition_version <= previous.definition_version:
            raise AppError(409, "DEFINITION_ACTIVATION_INVALID", "Activation cannot move backward.")
    now = utc_now_ms()
    if state is None:
        state = ActiveCompetencyDefinitionState(
            competency_identity_id=competency_identity_id,
            semantic_definition_id=definition.id,
            activated_at=now,
        )
        db.add(state)
    else:
        state.semantic_definition_id = definition.id
        state.activated_at = now
    db.add(
        CompetencyDefinitionActivationEvent(
            competency_identity_id=competency_identity_id,
            from_definition_id=previous_id,
            to_definition_id=definition.id,
            activated_at=now,
            source=payload.source,
            reason=payload.reason,
            event_sequence=(
                db.scalar(select(func.max(CompetencyDefinitionActivationEvent.event_sequence))) or 0
            )
            + 1,
            idempotency_key=payload.idempotency_key or f"generated:{new_id()}",
        )
    )
    db.commit()
    return {"competencyIdentityId": competency_identity_id, "activeDefinitionId": definition.id}


def _validate_profile_payload(payload: TargetProfileVersionCreate) -> None:
    _unique((item.stable_key for item in payload.domains), "Domain stable keys")
    _unique((item.order_index for item in payload.domains), "Domain order indices")
    _unique((item.stable_key for item in payload.targets), "Target stable keys")
    _unique((item.stable_key for item in payload.milestones), "Milestone stable keys")
    _unique((item.order_index for item in payload.milestones), "Milestone order indices")
    _unique((item.stable_key for item in payload.readiness_gates), "Gate stable keys")
    _unique((item.order_index for item in payload.readiness_gates), "Gate order indices")
    ranged = [
        item
        for item in payload.domains
        if item.minimum_percent is not None or item.maximum_percent is not None
    ]
    if ranged:
        minimum = sum(item.minimum_percent or 0 for item in payload.domains)
        maximum = sum(
            item.maximum_percent if item.maximum_percent is not None else 100
            for item in payload.domains
        )
        if minimum > 100 or maximum < 100:
            raise AppError(
                422,
                "PROFILE_ALLOCATION_INFEASIBLE",
                "Domain allocation ranges do not admit a total allocation of 100 percent.",
            )


def _create_profile_version(
    db: Session,
    profile: TargetProfile,
    payload: TargetProfileVersionCreate,
) -> TargetProfileVersion:
    _validate_profile_payload(payload)
    previous = db.scalar(
        select(TargetProfileVersion)
        .where(TargetProfileVersion.target_profile_id == profile.id)
        .order_by(TargetProfileVersion.version.desc())
        .limit(1)
    )
    now = utc_now_ms()
    version = TargetProfileVersion(
        target_profile_id=profile.id,
        version=1 if previous is None else previous.version + 1,
        title=payload.title,
        description=payload.description,
        creation_source=payload.creation_source,
        created_at=now,
        effective_at=datetime_to_epoch_ms(payload.effective_at),
        supersedes_version_id=previous.id if previous else None,
    )
    db.add(version)
    db.flush()
    domains: dict[str, ProfileDomain] = {}
    for domain_input in payload.domains:
        domain = ProfileDomain(
            profile_version_id=version.id,
            stable_key=domain_input.stable_key,
            title=domain_input.title,
            description=domain_input.description,
            minimum_percent=domain_input.minimum_percent,
            maximum_percent=domain_input.maximum_percent,
            order_index=domain_input.order_index,
        )
        db.add(domain)
        db.flush()
        domains[domain_input.stable_key] = domain
    targets: dict[str, ProfileTarget] = {}
    identities: dict[str, ProfileTargetIdentity] = {}
    for target_input in payload.targets:
        if target_input.domain_stable_key not in domains:
            raise AppError(422, "PROFILE_TARGET_INVALID", "A target domain does not exist.")
        if db.get(CompetencyIdentity, target_input.competency_identity_id) is None:
            raise AppError(422, "PROFILE_TARGET_INVALID", "A target competency does not exist.")
        scale = _scale(db, target_input.scale_stable_key, target_input.scale_version)
        levels, dimensions = _scale_members(db, scale)
        if (
            target_input.target_level_stable_key not in levels
            or (
                target_input.dimension_key is not None
                and target_input.dimension_key not in dimensions
            )
            or (
                target_input.scale_stable_key == "technical"
                and target_input.dimension_key is not None
            )
        ):
            raise AppError(422, "PROFILE_TARGET_INVALID", "A target scale reference is invalid.")
        identity = db.scalar(
            select(ProfileTargetIdentity).where(
                ProfileTargetIdentity.target_profile_id == profile.id,
                ProfileTargetIdentity.stable_key == target_input.stable_key,
            )
        )
        if identity is None:
            identity = ProfileTargetIdentity(
                target_profile_id=profile.id,
                stable_key=target_input.stable_key,
                competency_identity_id=target_input.competency_identity_id,
                dimension_key=target_input.dimension_key,
                created_at=now,
            )
            db.add(identity)
            db.flush()
        elif (
            identity.competency_identity_id != target_input.competency_identity_id
            or identity.dimension_key != target_input.dimension_key
        ):
            raise AppError(
                409,
                "PROFILE_TARGET_IDENTITY_CHANGED",
                "A stable target key cannot change competency or dimension meaning.",
            )
        previous_target_scale = db.scalar(
            select(CapabilityScaleVersion.scale_stable_key)
            .join(ProfileTarget, ProfileTarget.scale_version_id == CapabilityScaleVersion.id)
            .where(ProfileTarget.target_identity_id == identity.id)
            .order_by(ProfileTarget.created_at.desc(), ProfileTarget.id.desc())
            .limit(1)
        )
        if (
            previous_target_scale is not None
            and previous_target_scale != target_input.scale_stable_key
        ):
            raise AppError(
                409,
                "PROFILE_TARGET_SCALE_INCOMPARABLE",
                "A stable target identity cannot switch between incomparable scale families.",
            )
        target = ProfileTarget(
            profile_version_id=version.id,
            target_identity_id=identity.id,
            profile_domain_id=domains[target_input.domain_stable_key].id,
            scale_version_id=scale.id,
            target_level_id=levels[target_input.target_level_stable_key].id,
            priority=target_input.priority,
            target_date=target_input.target_date,
            target_month=target_input.target_month,
            date_interpretation=target_input.date_interpretation,
            freshness_override_days=target_input.freshness_override_days,
            created_at=now,
            creation_source=payload.creation_source,
        )
        db.add(target)
        db.flush()
        identities[target_input.stable_key] = identity
        targets[target_input.stable_key] = target
    milestones: dict[str, ProfileMilestone] = {}
    for milestone_input in payload.milestones:
        if any(key not in targets for key in milestone_input.target_stable_keys):
            raise AppError(422, "PROFILE_MILESTONE_INVALID", "A milestone target does not exist.")
        _unique(milestone_input.target_stable_keys, "Milestone target keys")
        milestone_identity = db.scalar(
            select(MilestoneIdentity).where(
                MilestoneIdentity.target_profile_id == profile.id,
                MilestoneIdentity.stable_key == milestone_input.stable_key,
            )
        )
        if milestone_identity is None:
            milestone_identity = MilestoneIdentity(
                target_profile_id=profile.id,
                stable_key=milestone_input.stable_key,
                created_at=now,
            )
            db.add(milestone_identity)
            db.flush()
        milestone = ProfileMilestone(
            profile_version_id=version.id,
            milestone_identity_id=milestone_identity.id,
            title=milestone_input.title,
            description=milestone_input.description,
            target_date=milestone_input.target_date,
            order_index=milestone_input.order_index,
        )
        db.add(milestone)
        db.flush()
        milestones[milestone_input.stable_key] = milestone
        for key in milestone_input.target_stable_keys:
            db.add(
                ProfileMilestoneTarget(milestone_id=milestone.id, profile_target_id=targets[key].id)
            )
    for gate_input in payload.readiness_gates:
        if (
            gate_input.milestone_stable_key is not None
            and gate_input.milestone_stable_key not in milestones
        ):
            raise AppError(422, "READINESS_GATE_INVALID", "A gate milestone does not exist.")
        if any(key not in targets for key in gate_input.target_stable_keys):
            raise AppError(422, "READINESS_GATE_INVALID", "A gate target does not exist.")
        _unique(gate_input.target_stable_keys, "Gate target keys")
        _unique(
            (predicate.order_index for predicate in gate_input.predicates),
            "Gate predicate order indices",
        )
        gate_identity = db.scalar(
            select(ReadinessGateIdentity).where(
                ReadinessGateIdentity.target_profile_id == profile.id,
                ReadinessGateIdentity.stable_key == gate_input.stable_key,
            )
        )
        if gate_identity is None:
            gate_identity = ReadinessGateIdentity(
                target_profile_id=profile.id,
                stable_key=gate_input.stable_key,
                created_at=now,
            )
            db.add(gate_identity)
            db.flush()
        gate = ReadinessGate(
            profile_version_id=version.id,
            milestone_id=(
                milestones[gate_input.milestone_stable_key].id
                if gate_input.milestone_stable_key
                else None
            ),
            gate_identity_id=gate_identity.id,
            title=gate_input.title,
            effect=gate_input.effect,
            order_index=gate_input.order_index,
        )
        db.add(gate)
        db.flush()
        for key in gate_input.target_stable_keys:
            db.add(ReadinessGateTarget(gate_id=gate.id, profile_target_id=targets[key].id))
        for predicate in gate_input.predicates:
            _validate_gate_subject(predicate, db, identities)
            db.add(
                ReadinessGatePredicate(
                    gate_id=gate.id,
                    order_index=predicate.order_index,
                    predicate_type=predicate.predicate_type,
                    requirement_type=predicate.requirement_type,
                    subject_json=json.dumps(
                        predicate.subject, separators=(",", ":"), sort_keys=True
                    ),
                )
            )
    return version


def _serialize_profile_version(db: Session, version: TargetProfileVersion) -> dict[str, Any]:
    profile = db.get(TargetProfile, version.target_profile_id)
    domains = db.scalars(
        select(ProfileDomain)
        .where(ProfileDomain.profile_version_id == version.id)
        .order_by(ProfileDomain.order_index)
    ).all()
    targets = db.scalars(
        select(ProfileTarget).where(ProfileTarget.profile_version_id == version.id)
    ).all()
    targets_by_id = {item.id: item for item in targets}
    target_identities = {
        item.id: item
        for item in db.scalars(
            select(ProfileTargetIdentity).where(
                ProfileTargetIdentity.id.in_([target.target_identity_id for target in targets])
            )
        ).all()
    }
    scales = {
        item.id: item
        for item in db.scalars(
            select(CapabilityScaleVersion).where(
                CapabilityScaleVersion.id.in_([target.scale_version_id for target in targets])
            )
        ).all()
    }
    levels = {
        item.id: item
        for item in db.scalars(
            select(CapabilityScaleLevel).where(
                CapabilityScaleLevel.id.in_([target.target_level_id for target in targets])
            )
        ).all()
    }
    domain_by_id = {item.id: item for item in domains}
    milestones = db.scalars(
        select(ProfileMilestone)
        .where(ProfileMilestone.profile_version_id == version.id)
        .order_by(ProfileMilestone.order_index)
    ).all()
    milestone_links: dict[str, list[str]] = {item.id: [] for item in milestones}
    for milestone_id, target_id in db.execute(
        select(
            ProfileMilestoneTarget.milestone_id,
            ProfileMilestoneTarget.profile_target_id,
        ).where(ProfileMilestoneTarget.milestone_id.in_([item.id for item in milestones]))
    ).all():
        milestone_links[milestone_id].append(
            target_identities[targets_by_id[target_id].target_identity_id].stable_key
        )
    gates = db.scalars(
        select(ReadinessGate)
        .where(ReadinessGate.profile_version_id == version.id)
        .order_by(ReadinessGate.order_index)
    ).all()
    gate_targets: dict[str, list[str]] = {item.id: [] for item in gates}
    for gate_id, target_id in db.execute(
        select(ReadinessGateTarget.gate_id, ReadinessGateTarget.profile_target_id).where(
            ReadinessGateTarget.gate_id.in_([item.id for item in gates])
        )
    ).all():
        gate_targets[gate_id].append(
            target_identities[targets_by_id[target_id].target_identity_id].stable_key
        )
    predicates: dict[str, list[ReadinessGatePredicate]] = {item.id: [] for item in gates}
    for predicate in db.scalars(
        select(ReadinessGatePredicate)
        .where(ReadinessGatePredicate.gate_id.in_([item.id for item in gates]))
        .order_by(ReadinessGatePredicate.order_index)
    ).all():
        predicates[predicate.gate_id].append(predicate)
    milestone_by_id = {item.id: item for item in milestones}
    milestone_identities = {
        item.id: item
        for item in db.scalars(
            select(MilestoneIdentity).where(
                MilestoneIdentity.id.in_([item.milestone_identity_id for item in milestones])
            )
        ).all()
    }
    gate_identities = {
        item.id: item
        for item in db.scalars(
            select(ReadinessGateIdentity).where(
                ReadinessGateIdentity.id.in_([item.gate_identity_id for item in gates])
            )
        ).all()
    }
    return {
        "profileId": profile.id if profile else version.target_profile_id,
        "stableKey": profile.stable_key if profile else None,
        "versionId": version.id,
        "version": version.version,
        "title": version.title,
        "description": version.description,
        "createdAt": epoch_ms_to_rfc3339(version.created_at),
        "effectiveAt": epoch_ms_to_rfc3339(version.effective_at),
        "creationSource": version.creation_source,
        "supersedesVersionId": version.supersedes_version_id,
        "domains": [
            {
                "id": item.id,
                "stableKey": item.stable_key,
                "title": item.title,
                "description": item.description,
                "minimumPercent": item.minimum_percent,
                "maximumPercent": item.maximum_percent,
                "orderIndex": item.order_index,
            }
            for item in domains
        ],
        "targets": [
            {
                "id": item.id,
                "identityId": item.target_identity_id,
                "stableKey": target_identities[item.target_identity_id].stable_key,
                "competencyIdentityId": target_identities[
                    item.target_identity_id
                ].competency_identity_id,
                "dimensionKey": target_identities[item.target_identity_id].dimension_key,
                "domainStableKey": domain_by_id[item.profile_domain_id].stable_key,
                "scaleStableKey": scales[item.scale_version_id].scale_stable_key,
                "scaleVersion": scales[item.scale_version_id].scale_version,
                "targetLevelStableKey": levels[item.target_level_id].stable_key,
                "priority": item.priority,
                "targetDate": item.target_date,
                "targetMonth": item.target_month,
                "dateInterpretation": item.date_interpretation,
                "freshnessOverrideDays": item.freshness_override_days,
            }
            for item in targets
        ],
        "milestones": [
            {
                "id": item.id,
                "identityId": item.milestone_identity_id,
                "stableKey": milestone_identities[item.milestone_identity_id].stable_key,
                "title": item.title,
                "description": item.description,
                "targetDate": item.target_date,
                "orderIndex": item.order_index,
                "targetStableKeys": sorted(milestone_links[item.id]),
            }
            for item in milestones
        ],
        "readinessGates": [
            {
                "id": item.id,
                "identityId": item.gate_identity_id,
                "stableKey": gate_identities[item.gate_identity_id].stable_key,
                "title": item.title,
                "effect": item.effect,
                "orderIndex": item.order_index,
                "milestoneStableKey": (
                    milestone_identities[
                        milestone_by_id[item.milestone_id].milestone_identity_id
                    ].stable_key
                    if item.milestone_id
                    else None
                ),
                "targetStableKeys": sorted(gate_targets[item.id]),
                "predicates": [
                    {
                        "id": predicate.id,
                        "predicateType": predicate.predicate_type,
                        "requirementType": predicate.requirement_type,
                        "orderIndex": predicate.order_index,
                        "subject": json.loads(predicate.subject_json),
                    }
                    for predicate in predicates[item.id]
                ],
            }
            for item in gates
        ],
    }


@router.post("/target-profiles", status_code=201)
async def create_target_profile(
    payload: TargetProfileCreate,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if db.scalar(select(TargetProfile.id).where(TargetProfile.stable_key == payload.stable_key)):
        raise AppError(409, "TARGET_PROFILE_EXISTS", "The target profile stable key exists.")
    profile = TargetProfile(stable_key=payload.stable_key, creation_source=payload.creation_source)
    db.add(profile)
    db.flush()
    version = _create_profile_version(db, profile, payload.version)
    db.commit()
    return _serialize_profile_version(db, version)


@router.post("/target-profiles/{profile_id}/versions", status_code=201)
async def create_target_profile_version(
    profile_id: str,
    payload: TargetProfileVersionCreate,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = db.get(TargetProfile, profile_id)
    if profile is None:
        raise AppError(404, "TARGET_PROFILE_NOT_FOUND", "The target profile does not exist.")
    version = _create_profile_version(db, profile, payload)
    db.commit()
    return _serialize_profile_version(db, version)


@router.get("/target-profiles")
async def list_target_profiles(
    _auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    active = db.get(ActiveTargetProfileState, 1)
    result = []
    for profile in db.scalars(select(TargetProfile).order_by(TargetProfile.created_at)).all():
        versions = db.scalars(
            select(TargetProfileVersion)
            .where(TargetProfileVersion.target_profile_id == profile.id)
            .order_by(TargetProfileVersion.version)
        ).all()
        result.append(
            {
                "id": profile.id,
                "stableKey": profile.stable_key,
                "creationSource": profile.creation_source,
                "activeVersionId": active.target_profile_version_id
                if active and active.target_profile_id == profile.id
                else None,
                "versions": [_serialize_profile_version(db, item) for item in versions],
            }
        )
    return result


@router.get("/target-profiles/{profile_id}/versions/{version_id}")
async def get_target_profile_version(
    profile_id: str,
    version_id: str,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    version = db.get(TargetProfileVersion, version_id)
    if version is None or version.target_profile_id != profile_id:
        raise AppError(
            404, "TARGET_PROFILE_VERSION_NOT_FOUND", "The profile version does not exist."
        )
    return _serialize_profile_version(db, version)


@router.post("/target-profiles/{profile_id}/versions/{version_id}/activate")
async def activate_target_profile_version(
    profile_id: str,
    version_id: str,
    payload: ActivationRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    version = db.get(TargetProfileVersion, version_id)
    if version is None or version.target_profile_id != profile_id:
        raise AppError(
            404, "TARGET_PROFILE_VERSION_NOT_FOUND", "The profile version does not exist."
        )
    if payload.idempotency_key:
        prior_event = db.scalar(
            select(TargetProfileActivationEvent).where(
                TargetProfileActivationEvent.idempotency_key == payload.idempotency_key
            )
        )
        if prior_event is not None:
            if prior_event.to_profile_version_id == version_id:
                return {"profileId": profile_id, "activeVersionId": version.id}
            raise AppError(409, "IDEMPOTENCY_CONFLICT", "The idempotency key was already used.")
    state = db.get(ActiveTargetProfileState, 1)
    previous_id = state.target_profile_version_id if state else None
    if previous_id == version.id:
        raise AppError(
            409, "TARGET_PROFILE_ALREADY_ACTIVE", "The profile version is already active."
        )
    if state is not None and state.target_profile_id == profile_id:
        previous = db.get(TargetProfileVersion, previous_id)
        if previous and version.version <= previous.version:
            raise AppError(
                409, "TARGET_PROFILE_ACTIVATION_INVALID", "Activation cannot move backward."
            )
    now = utc_now_ms()
    if state is None:
        state = ActiveTargetProfileState(
            id=1,
            target_profile_id=profile_id,
            target_profile_version_id=version.id,
            activated_at=now,
        )
        db.add(state)
    else:
        state.target_profile_id = profile_id
        state.target_profile_version_id = version.id
        state.activated_at = now
    db.add(
        TargetProfileActivationEvent(
            from_profile_version_id=previous_id,
            to_profile_version_id=version.id,
            activated_at=now,
            source=payload.source,
            reason=payload.reason,
            event_sequence=(
                db.scalar(select(func.max(TargetProfileActivationEvent.event_sequence))) or 0
            )
            + 1,
            idempotency_key=payload.idempotency_key or f"generated:{new_id()}",
        )
    )
    db.commit()
    return {"profileId": profile_id, "activeVersionId": version.id}
