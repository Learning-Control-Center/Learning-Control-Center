from __future__ import annotations

import json
from dataclasses import asdict, replace
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.activity_views import activity_actuality_as_of
from app.capability_views import capability_as_of, criterion_evaluation_as_of
from app.curriculum.contracts import (
    ActiveCurriculumVersionReferencePublicDTO,
    AssessmentRubricPublicDTO,
    CurriculumActionPublicDTO,
    CurriculumAvailabilityPublicDTO,
    CurriculumCatalogPublicDTO,
    CurriculumObjectivePublicDTO,
    CurriculumRequirementPublicDTO,
    CurriculumTargetPublicDTO,
    CurriculumUnitPublicDTO,
    CurriculumVersionInput,
    EvidenceOpportunityPublicDTO,
    LearningUnitInput,
    TargetSuitabilityPublicDTO,
)
from app.curriculum.models import (
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
from app.determinism import canonical_json, content_hash
from app.errors import AppError
from app.models import (
    CapabilityScaleDimension,
    CapabilityScaleLevel,
    CapabilityScaleVersion,
    CriterionDefinition,
    SemanticCompetencyDefinition,
    SemanticDefinitionDimension,
)
from app.profile_views import target_profile_relevance_as_of
from app.requirements.contracts import (
    RequirementDefinitionDTO,
    RequirementFactDTO,
    RequirementState,
    evaluate_requirements,
)
from app.time_utils import datetime_to_epoch_ms, utc_now_ms

CURRICULUM_SCHEMA_VERSION = "curriculum-schema/v1"
CURRICULUM_REQUIREMENT_POLICY = "curriculum-requirement-policy/v1"
CURRICULUM_OPPORTUNITY_POLICY = "curriculum-evidence-opportunity-policy/v1"
CURRICULUM_AVAILABILITY_POLICY = "curriculum-availability-policy/v1"


def _unique(values: list[Any], label: str) -> None:
    if len(values) != len(set(values)):
        raise AppError(422, "CURRICULUM_VERSION_INVALID", f"{label} values must be unique.")


def _validate_collection_shape(payload: CurriculumVersionInput) -> None:
    _unique([item.stable_key for item in payload.objectives], "Objective stable-key")
    _unique([item.order_index for item in payload.objectives], "Objective order")
    _unique([item.stable_key for item in payload.units], "Learning-unit stable-key")
    _unique([item.order_index for item in payload.units], "Learning-unit order")
    _unique([item.stable_key for item in payload.assessment_rubrics], "Rubric stable-key")
    objectives = {item.stable_key for item in payload.objectives}
    units = {item.stable_key for item in payload.units}
    for unit in payload.units:
        durations = (
            unit.minimum_useful_duration_ms,
            unit.preferred_duration_ms,
            unit.maximum_useful_duration_ms,
        )
        if any(value is None for value in durations) and not all(
            value is None for value in durations
        ):
            raise AppError(422, "DURATION_RANGE_INVALID", "Duration ranges are all-or-none.")
        if all(value is not None for value in durations):
            minimum, preferred, maximum = durations
            assert minimum is not None and preferred is not None and maximum is not None
            if (
                minimum <= 0
                or minimum > preferred
                or preferred > maximum
                or any(value % 300_000 for value in (minimum, preferred, maximum))
            ):
                raise AppError(
                    422,
                    "DURATION_RANGE_INVALID",
                    "Known ranges must be ordered positive five-minute quanta.",
                )
        if unit.objective_stable_key is not None and unit.objective_stable_key not in objectives:
            raise AppError(
                422,
                "CURRICULUM_OBJECTIVE_REFERENCE_INVALID",
                "A learning unit references an objective outside this version.",
            )
        _unique([item.order_index for item in unit.targets], "Target order")
        _unique([item.stable_key for item in unit.requirements], "Requirement stable-key")
        _unique([item.order_index for item in unit.requirements], "Requirement order")
        _unique(
            [item.stable_key for item in unit.evidence_opportunities],
            "Evidence-opportunity stable-key",
        )
        _unique(
            [item.order_index for item in unit.evidence_opportunities],
            "Evidence-opportunity order",
        )
        for requirement in unit.requirements:
            if (
                requirement.requirement_type == "learning_unit_completed"
                and requirement.subject.get("learningUnitStableKey") not in units
            ):
                raise AppError(
                    422,
                    "CURRICULUM_REQUIREMENT_INVALID",
                    "A learning-unit requirement must reference a unit in the same version.",
                )
    hard_dependencies = {
        unit.stable_key: {
            str(requirement.subject["learningUnitStableKey"])
            for requirement in unit.requirements
            if requirement.requirement_type == "learning_unit_completed"
            and requirement.effect == "hard"
        }
        for unit in payload.units
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(unit_key: str) -> None:
        if unit_key in visiting:
            raise AppError(
                422,
                "CURRICULUM_REQUIREMENT_CYCLE",
                "Hard learning-unit completion requirements must form a DAG.",
            )
        if unit_key in visited:
            return
        visiting.add(unit_key)
        for dependency in sorted(hard_dependencies[unit_key]):
            visit(dependency)
        visiting.remove(unit_key)
        visited.add(unit_key)

    for unit_key in sorted(hard_dependencies):
        visit(unit_key)


def _validate_exact_target(db: Session, target: Any) -> None:
    definition = db.get(SemanticCompetencyDefinition, target.semantic_definition_id)
    scale = db.get(CapabilityScaleVersion, target.scale_version_id)
    if definition is None or scale is None or definition.scale_version_id != scale.id:
        raise AppError(
            422,
            "CURRICULUM_TARGET_INCOMPATIBLE",
            "A target must pin an existing semantic definition and its exact scale version.",
        )
    if target.criterion_definition_id is not None:
        criterion = db.get(CriterionDefinition, target.criterion_definition_id)
        if (
            criterion is None
            or criterion.semantic_definition_id != definition.id
            or criterion.dimension_id != target.dimension_id
        ):
            raise AppError(
                422,
                "CURRICULUM_TARGET_INCOMPATIBLE",
                "A target criterion must belong to the pinned semantic definition.",
            )
    if target.dimension_id is not None:
        dimension = db.get(CapabilityScaleDimension, target.dimension_id)
        enabled = db.get(SemanticDefinitionDimension, (definition.id, target.dimension_id))
        if dimension is None or dimension.scale_version_id != scale.id or enabled is None:
            raise AppError(
                422,
                "CURRICULUM_TARGET_INCOMPATIBLE",
                "A target dimension must be enabled by the pinned semantic definition and scale.",
            )
    levels: list[CapabilityScaleLevel] = []
    for level_id in (target.minimum_level_id, target.maximum_level_id):
        if level_id is None:
            continue
        level = db.get(CapabilityScaleLevel, level_id)
        if level is None or level.scale_version_id != scale.id:
            raise AppError(
                422,
                "CURRICULUM_TARGET_INCOMPATIBLE",
                "Suitability levels must belong to the pinned scale version.",
            )
        levels.append(level)
    if len(levels) == 2 and levels[0].ordinal_rank > levels[1].ordinal_rank:
        raise AppError(422, "CURRICULUM_TARGET_INCOMPATIBLE", "The suitability range is reversed.")


def _validate_requirement(db: Session, unit: LearningUnitInput, requirement: Any) -> None:
    expected_keys = {
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
    if set(requirement.subject) != expected_keys[requirement.requirement_type]:
        raise AppError(
            422,
            "CURRICULUM_REQUIREMENT_INVALID",
            "A requirement subject does not match its declared type.",
            {"unitStableKey": unit.stable_key, "requirementStableKey": requirement.stable_key},
        )
    expected_scope = {
        "capability_at_least": "learner",
        "criterion_demonstrated": "learner",
        "learning_unit_completed": "curriculum",
        "resource_available": "environment",
        "user_constraint": "user",
    }[requirement.requirement_type]
    if requirement.scope != expected_scope:
        raise AppError(
            422,
            "CURRICULUM_REQUIREMENT_INVALID",
            "A requirement scope does not match its declared type.",
        )
    if requirement.requirement_type == "capability_at_least":
        definition = db.get(
            SemanticCompetencyDefinition, requirement.subject["semanticDefinitionId"]
        )
        scale = db.get(CapabilityScaleVersion, requirement.subject["scaleVersionId"])
        level = db.get(CapabilityScaleLevel, requirement.subject["levelId"])
        dimension_id = requirement.subject["dimensionId"]
        dimension = db.get(CapabilityScaleDimension, dimension_id) if dimension_id else None
        enabled = (
            db.get(SemanticDefinitionDimension, (definition.id, dimension_id))
            if definition is not None and dimension_id
            else None
        )
        if (
            definition is None
            or scale is None
            or level is None
            or definition.scale_version_id != scale.id
            or level.scale_version_id != scale.id
            or (
                dimension_id is not None
                and (dimension is None or dimension.scale_version_id != scale.id or enabled is None)
            )
        ):
            raise AppError(
                422,
                "CURRICULUM_REQUIREMENT_INVALID",
                "A capability requirement must pin compatible definition, scale, and level.",
            )
    elif requirement.requirement_type == "criterion_demonstrated":
        if db.get(CriterionDefinition, requirement.subject["criterionDefinitionId"]) is None:
            raise AppError(
                422, "CURRICULUM_REQUIREMENT_INVALID", "The requirement criterion is missing."
            )
    elif requirement.requirement_type == "learning_unit_completed":
        if (
            not isinstance(requirement.subject["learningUnitStableKey"], str)
            or not requirement.subject["learningUnitStableKey"]
        ):
            raise AppError(
                422, "CURRICULUM_REQUIREMENT_INVALID", "The required unit key is invalid."
            )
    elif requirement.requirement_type == "resource_available":
        if (
            not isinstance(requirement.subject["resourceKey"], str)
            or not requirement.subject["resourceKey"]
        ):
            raise AppError(422, "CURRICULUM_REQUIREMENT_INVALID", "The resource key is invalid.")
    elif requirement.requirement_type == "user_constraint":
        if (
            not isinstance(requirement.subject["constraintKey"], str)
            or not requirement.subject["constraintKey"]
        ):
            raise AppError(
                422, "CURRICULUM_REQUIREMENT_INVALID", "The user-constraint key is invalid."
            )


def validate_version(db: Session, payload: CurriculumVersionInput) -> dict[str, Any]:
    _validate_collection_shape(payload)
    for unit in payload.units:
        for target in unit.targets:
            _validate_exact_target(db, target)
        for requirement in unit.requirements:
            _validate_requirement(db, unit, requirement)
    for rubric in payload.assessment_rubrics:
        definition = db.get(SemanticCompetencyDefinition, rubric.semantic_definition_id)
        criterion = (
            db.get(CriterionDefinition, rubric.criterion_definition_id)
            if rubric.criterion_definition_id
            else None
        )
        if definition is None or (
            rubric.criterion_definition_id is not None
            and (criterion is None or criterion.semantic_definition_id != definition.id)
        ):
            raise AppError(
                422,
                "ASSESSMENT_RUBRIC_TARGET_INVALID",
                "The rubric must pin an existing compatible semantic target.",
            )
    normalized = payload.model_dump(mode="json")
    normalized["objectives"] = sorted(
        normalized["objectives"], key=lambda item: (item["order_index"], item["stable_key"])
    )
    normalized["units"] = sorted(
        normalized["units"], key=lambda item: (item["order_index"], item["stable_key"])
    )
    for unit in normalized["units"]:
        unit["targets"] = sorted(
            unit["targets"], key=lambda item: (item["order_index"], item["semantic_definition_id"])
        )
        unit["requirements"] = sorted(
            unit["requirements"], key=lambda item: (item["order_index"], item["stable_key"])
        )
        unit["evidence_opportunities"] = sorted(
            unit["evidence_opportunities"],
            key=lambda item: (item["order_index"], item["stable_key"]),
        )
        for opportunity in unit["evidence_opportunities"]:
            opportunity["intended_strengths"] = sorted(opportunity["intended_strengths"])
            opportunity["intended_independence_modes"] = sorted(
                opportunity["intended_independence_modes"]
            )
    normalized["assessment_rubrics"] = sorted(
        normalized["assessment_rubrics"], key=lambda item: item["stable_key"]
    )
    return {"valid": True, "contentHash": content_hash(normalized), "normalized": normalized}


def _input_target_signature(db: Session, targets: list[Any]) -> tuple[tuple[str, str | None], ...]:
    # Stable unit meaning is anchored by kind plus primary targets. Supporting targets are
    # versioned definition detail and may evolve without creating a new stable unit identity.
    values: list[tuple[str, str | None]] = []
    for target in targets:
        if target.role != "primary":
            continue
        definition = db.get(SemanticCompetencyDefinition, target.semantic_definition_id)
        assert definition is not None
        criterion = (
            db.get(CriterionDefinition, target.criterion_definition_id)
            if target.criterion_definition_id
            else None
        )
        values.append(
            (
                definition.competency_identity_id,
                criterion.criterion_identity_id if criterion is not None else None,
            )
        )
    return tuple(sorted(values, key=lambda item: (item[0], item[1] or "")))


def _stored_target_signature(
    db: Session, unit_definition_id: str
) -> tuple[tuple[str, str | None], ...]:
    targets = db.scalars(
        select(LearningUnitTarget).where(
            LearningUnitTarget.learning_unit_definition_id == unit_definition_id,
            LearningUnitTarget.role == "primary",
        )
    ).all()
    values: list[tuple[str, str | None]] = []
    for target in targets:
        definition = db.get(SemanticCompetencyDefinition, target.semantic_definition_id)
        assert definition is not None
        criterion = (
            db.get(CriterionDefinition, target.criterion_definition_id)
            if target.criterion_definition_id
            else None
        )
        values.append(
            (
                definition.competency_identity_id,
                criterion.criterion_identity_id if criterion is not None else None,
            )
        )
    return tuple(sorted(values, key=lambda item: (item[0], item[1] or "")))


def create_version(
    db: Session, curriculum: Curriculum, payload: CurriculumVersionInput
) -> CurriculumVersion:
    validation = validate_version(db, payload)
    previous = db.scalar(
        select(CurriculumVersion)
        .where(CurriculumVersion.curriculum_id == curriculum.id)
        .order_by(CurriculumVersion.version.desc())
        .limit(1)
    )
    now = utc_now_ms()
    version = CurriculumVersion(
        curriculum_id=curriculum.id,
        version=1 if previous is None else previous.version + 1,
        title=payload.title,
        description=payload.description,
        schema_version=CURRICULUM_SCHEMA_VERSION,
        definition_payload_json=canonical_json(validation["normalized"]),
        content_hash=validation["contentHash"],
        effective_at=datetime_to_epoch_ms(payload.effective_at),
        created_at=now,
        creation_source=payload.creation_source,
        supersedes_version_id=previous.id if previous else None,
    )
    db.add(version)
    db.flush()

    objective_identities: dict[str, CurriculumObjectiveIdentity] = {}
    for objective_input in payload.objectives:
        objective_identity = db.scalar(
            select(CurriculumObjectiveIdentity).where(
                CurriculumObjectiveIdentity.curriculum_id == curriculum.id,
                CurriculumObjectiveIdentity.stable_key == objective_input.stable_key,
            )
        )
        if objective_identity is None:
            objective_identity = CurriculumObjectiveIdentity(
                curriculum_id=curriculum.id,
                stable_key=objective_input.stable_key,
                created_at=now,
            )
            db.add(objective_identity)
            db.flush()
        objective_identities[objective_input.stable_key] = objective_identity
        db.add(
            CurriculumObjectiveDefinition(
                curriculum_version_id=version.id,
                objective_identity_id=objective_identity.id,
                title=objective_input.title,
                description=objective_input.description,
                order_index=objective_input.order_index,
            )
        )

    for unit_input in payload.units:
        unit_identity = db.scalar(
            select(LearningUnitIdentity).where(
                LearningUnitIdentity.curriculum_id == curriculum.id,
                LearningUnitIdentity.stable_key == unit_input.stable_key,
            )
        )
        if unit_identity is None:
            unit_identity = LearningUnitIdentity(
                curriculum_id=curriculum.id, stable_key=unit_input.stable_key, created_at=now
            )
            db.add(unit_identity)
            db.flush()
        previous_definition = db.scalar(
            select(LearningUnitDefinition)
            .join(CurriculumVersion)
            .where(
                LearningUnitDefinition.unit_identity_id == unit_identity.id,
                CurriculumVersion.curriculum_id == curriculum.id,
            )
            .order_by(CurriculumVersion.version.desc())
            .limit(1)
        )
        if previous_definition is not None:
            meaning_changed = _stored_target_signature(
                db, previous_definition.id
            ) != _input_target_signature(db, unit_input.targets)
            if previous_definition.kind != unit_input.kind or meaning_changed:
                raise AppError(
                    422,
                    "LEARNING_UNIT_IDENTITY_INCOMPATIBLE",
                    "Changing a unit kind or primary semantic target requires a new identity.",
                )
        unit = LearningUnitDefinition(
            curriculum_version_id=version.id,
            unit_identity_id=unit_identity.id,
            objective_identity_id=(
                objective_identities[unit_input.objective_stable_key].id
                if unit_input.objective_stable_key
                else None
            ),
            kind=unit_input.kind,
            title=unit_input.title,
            description=unit_input.description,
            action_payload_json=canonical_json(unit_input.action.model_dump(mode="json")),
            status=unit_input.status,
            provenance=unit_input.provenance,
            order_index=unit_input.order_index,
            minimum_useful_duration_ms=unit_input.minimum_useful_duration_ms,
            preferred_duration_ms=unit_input.preferred_duration_ms,
            maximum_useful_duration_ms=unit_input.maximum_useful_duration_ms,
        )
        db.add(unit)
        db.flush()
        for target in unit_input.targets:
            db.add(
                LearningUnitTarget(
                    learning_unit_definition_id=unit.id,
                    semantic_definition_id=target.semantic_definition_id,
                    criterion_definition_id=target.criterion_definition_id,
                    scale_version_id=target.scale_version_id,
                    dimension_id=target.dimension_id,
                    intended_learning_outcome=target.intended_learning_outcome,
                    minimum_level_id=target.minimum_level_id,
                    maximum_level_id=target.maximum_level_id,
                    supports_unassessed=target.supports_unassessed,
                    role=target.role,
                    order_index=target.order_index,
                )
            )
        for requirement in unit_input.requirements:
            db.add(
                LearningUnitRequirement(
                    learning_unit_definition_id=unit.id,
                    stable_key=requirement.stable_key,
                    requirement_type=requirement.requirement_type,
                    effect=requirement.effect,
                    scope=requirement.scope,
                    subject_json=canonical_json(requirement.subject),
                    order_index=requirement.order_index,
                    policy_version=CURRICULUM_REQUIREMENT_POLICY,
                )
            )
        for opportunity in unit_input.evidence_opportunities:
            db.add(
                EvidenceOpportunityDefinition(
                    learning_unit_definition_id=unit.id,
                    stable_key=opportunity.stable_key,
                    evidence_kind=opportunity.evidence_kind,
                    possible_characteristics_json=canonical_json(
                        {
                            "intendedStrengths": sorted(opportunity.intended_strengths),
                            "intendedIndependenceModes": sorted(
                                opportunity.intended_independence_modes
                            ),
                        }
                    ),
                    required_characteristics_json=canonical_json(
                        {
                            "actualActivity": opportunity.requires_actual_activity,
                            "artifact": opportunity.requires_artifact,
                        }
                    ),
                    order_index=opportunity.order_index,
                    policy_version=CURRICULUM_OPPORTUNITY_POLICY,
                )
            )

    for rubric_input in payload.assessment_rubrics:
        rubric_identity = db.scalar(
            select(AssessmentRubricIdentity).where(
                AssessmentRubricIdentity.curriculum_id == curriculum.id,
                AssessmentRubricIdentity.stable_key == rubric_input.stable_key,
            )
        )
        if rubric_identity is None:
            rubric_identity = AssessmentRubricIdentity(
                curriculum_id=curriculum.id,
                stable_key=rubric_input.stable_key,
                created_at=now,
            )
            db.add(rubric_identity)
            db.flush()
        previous_rubric = db.scalar(
            select(AssessmentRubricDefinition)
            .join(CurriculumVersion)
            .where(
                AssessmentRubricDefinition.rubric_identity_id == rubric_identity.id,
                CurriculumVersion.curriculum_id == curriculum.id,
            )
            .order_by(CurriculumVersion.version.desc())
            .limit(1)
        )
        if previous_rubric is not None:
            previous_semantic_definition = db.get(
                SemanticCompetencyDefinition, previous_rubric.semantic_definition_id
            )
            next_semantic_definition = db.get(
                SemanticCompetencyDefinition, rubric_input.semantic_definition_id
            )
            previous_criterion = (
                db.get(CriterionDefinition, previous_rubric.criterion_definition_id)
                if previous_rubric.criterion_definition_id
                else None
            )
            next_criterion = (
                db.get(CriterionDefinition, rubric_input.criterion_definition_id)
                if rubric_input.criterion_definition_id
                else None
            )
            assert previous_semantic_definition is not None and next_semantic_definition is not None
            if (
                previous_semantic_definition.competency_identity_id
                != next_semantic_definition.competency_identity_id
                or (previous_criterion.criterion_identity_id if previous_criterion else None)
                != (next_criterion.criterion_identity_id if next_criterion else None)
            ):
                raise AppError(
                    422,
                    "ASSESSMENT_RUBRIC_IDENTITY_INCOMPATIBLE",
                    "Changing a rubric semantic target requires a new stable identity.",
                )
        db.add(
            AssessmentRubricDefinition(
                curriculum_version_id=version.id,
                rubric_identity_id=rubric_identity.id,
                title=rubric_input.title,
                instructions=rubric_input.instructions,
                rubric_json=canonical_json(rubric_input.rubric),
                semantic_definition_id=rubric_input.semantic_definition_id,
                criterion_definition_id=rubric_input.criterion_definition_id,
            )
        )
    db.flush()
    return version


def _unit_dto(
    db: Session, curriculum: Curriculum, version: CurriculumVersion, unit: LearningUnitDefinition
) -> CurriculumUnitPublicDTO:
    identity = db.get(LearningUnitIdentity, unit.unit_identity_id)
    assert identity is not None
    objective = (
        db.get(CurriculumObjectiveIdentity, unit.objective_identity_id)
        if unit.objective_identity_id
        else None
    )
    targets = db.scalars(
        select(LearningUnitTarget)
        .where(LearningUnitTarget.learning_unit_definition_id == unit.id)
        .order_by(LearningUnitTarget.order_index, LearningUnitTarget.id)
    ).all()
    requirements = db.scalars(
        select(LearningUnitRequirement)
        .where(LearningUnitRequirement.learning_unit_definition_id == unit.id)
        .order_by(LearningUnitRequirement.order_index, LearningUnitRequirement.stable_key)
    ).all()
    opportunities = db.scalars(
        select(EvidenceOpportunityDefinition)
        .where(EvidenceOpportunityDefinition.learning_unit_definition_id == unit.id)
        .order_by(
            EvidenceOpportunityDefinition.order_index, EvidenceOpportunityDefinition.stable_key
        )
    ).all()
    duration = None
    if unit.minimum_useful_duration_ms is not None:
        assert (
            unit.preferred_duration_ms is not None and unit.maximum_useful_duration_ms is not None
        )
        duration = (
            unit.minimum_useful_duration_ms,
            unit.preferred_duration_ms,
            unit.maximum_useful_duration_ms,
        )
    action = json.loads(unit.action_payload_json)
    return CurriculumUnitPublicDTO(
        curriculum_id=curriculum.id,
        curriculum_stable_key=curriculum.stable_key,
        curriculum_version_id=version.id,
        curriculum_version=version.version,
        unit_identity_id=identity.id,
        unit_stable_key=identity.stable_key,
        unit_definition_id=unit.id,
        kind=unit.kind,
        candidate_type={
            "resource": "curriculum_unit",
            "exercise": "curriculum_unit",
            "practice_task": "practice_task",
            "verification_template": "verification",
        }[unit.kind],
        title=unit.title,
        description=unit.description,
        objective_identity_id=objective.id if objective is not None else None,
        objective_stable_key=objective.stable_key if objective is not None else None,
        status=unit.status,
        provenance=unit.provenance,
        action=CurriculumActionPublicDTO(
            kind=action["kind"],
            resource_reference=action.get("resource_reference"),
            instructions=action.get("instructions"),
            verification_method=action.get("verification_method"),
        ),
        order_index=unit.order_index,
        duration_range_ms=duration,
        targets=tuple(
            CurriculumTargetPublicDTO(
                target_id=item.id,
                semantic_definition_id=item.semantic_definition_id,
                criterion_definition_id=item.criterion_definition_id,
                scale_version_id=item.scale_version_id,
                dimension_id=item.dimension_id,
                intended_learning_outcome=item.intended_learning_outcome,
                minimum_level_id=item.minimum_level_id,
                maximum_level_id=item.maximum_level_id,
                supports_unassessed=item.supports_unassessed,
                role=item.role,
                order_index=item.order_index,
            )
            for item in targets
        ),
        requirements=tuple(
            CurriculumRequirementPublicDTO(
                stable_key=item.stable_key,
                requirement_type=item.requirement_type,
                effect=item.effect,
                scope=item.scope,
                subject_json=item.subject_json,
                order_index=item.order_index,
                policy_version=item.policy_version,
            )
            for item in requirements
        ),
        evidence_opportunities=tuple(
            EvidenceOpportunityPublicDTO(
                stable_key=item.stable_key,
                evidence_kind=item.evidence_kind,
                intended_strengths=tuple(
                    json.loads(item.possible_characteristics_json)["intendedStrengths"]
                ),
                intended_independence_modes=tuple(
                    json.loads(item.possible_characteristics_json)["intendedIndependenceModes"]
                ),
                requires_actual_activity=bool(
                    json.loads(item.required_characteristics_json)["actualActivity"]
                ),
                requires_artifact=bool(json.loads(item.required_characteristics_json)["artifact"]),
                order_index=item.order_index,
                policy_version=item.policy_version,
            )
            for item in opportunities
        ),
    )


def build_catalog(db: Session, cutoff_at: int) -> CurriculumCatalogPublicDTO:
    latest: dict[str, CurriculumActivationEvent] = {}
    events = db.scalars(
        select(CurriculumActivationEvent)
        .where(CurriculumActivationEvent.activated_at < cutoff_at)
        .order_by(
            CurriculumActivationEvent.curriculum_id,
            CurriculumActivationEvent.activated_at,
            CurriculumActivationEvent.event_sequence,
            CurriculumActivationEvent.id,
        )
    ).all()
    for event in events:
        version = db.get(CurriculumVersion, event.to_curriculum_version_id)
        if version is not None and version.effective_at < cutoff_at:
            latest[event.curriculum_id] = event
    references: list[ActiveCurriculumVersionReferencePublicDTO] = []
    objectives: list[CurriculumObjectivePublicDTO] = []
    units: list[CurriculumUnitPublicDTO] = []
    rubrics: list[AssessmentRubricPublicDTO] = []
    for curriculum_id, event in sorted(latest.items()):
        curriculum = db.get(Curriculum, curriculum_id)
        version = db.get(CurriculumVersion, event.to_curriculum_version_id)
        if curriculum is None or version is None:
            raise AppError(500, "CURRICULUM_HISTORY_INVALID", "Activation history is incomplete.")
        if curriculum.retired_at is not None and curriculum.retired_at < cutoff_at:
            continue
        references.append(
            ActiveCurriculumVersionReferencePublicDTO(
                curriculum_id=curriculum.id,
                stable_key=curriculum.stable_key,
                version_id=version.id,
                version=version.version,
                content_hash=version.content_hash,
                activation_event_id=event.id,
                activation_sequence=event.event_sequence,
            )
        )
        for definition, identity in db.execute(
            select(CurriculumObjectiveDefinition, CurriculumObjectiveIdentity)
            .join(
                CurriculumObjectiveIdentity,
                CurriculumObjectiveIdentity.id
                == CurriculumObjectiveDefinition.objective_identity_id,
            )
            .where(CurriculumObjectiveDefinition.curriculum_version_id == version.id)
            .order_by(
                CurriculumObjectiveDefinition.order_index, CurriculumObjectiveIdentity.stable_key
            )
        ).all():
            objectives.append(
                CurriculumObjectivePublicDTO(
                    curriculum_id=curriculum.id,
                    curriculum_version_id=version.id,
                    identity_id=identity.id,
                    stable_key=identity.stable_key,
                    title=definition.title,
                    description=definition.description,
                    order_index=definition.order_index,
                )
            )
        for unit in db.scalars(
            select(LearningUnitDefinition)
            .where(LearningUnitDefinition.curriculum_version_id == version.id)
            .order_by(LearningUnitDefinition.order_index, LearningUnitDefinition.id)
        ).all():
            if unit.status == "active":
                units.append(_unit_dto(db, curriculum, version, unit))
        for definition, identity in db.execute(
            select(AssessmentRubricDefinition, AssessmentRubricIdentity)
            .join(
                AssessmentRubricIdentity,
                AssessmentRubricIdentity.id == AssessmentRubricDefinition.rubric_identity_id,
            )
            .where(AssessmentRubricDefinition.curriculum_version_id == version.id)
            .order_by(AssessmentRubricIdentity.stable_key)
        ).all():
            rubrics.append(
                AssessmentRubricPublicDTO(
                    curriculum_id=curriculum.id,
                    curriculum_version_id=version.id,
                    definition_id=definition.id,
                    identity_id=identity.id,
                    stable_key=identity.stable_key,
                    title=definition.title,
                    instructions=definition.instructions,
                    rubric_json=definition.rubric_json,
                    semantic_definition_id=definition.semantic_definition_id,
                    criterion_definition_id=definition.criterion_definition_id,
                )
            )
    return CurriculumCatalogPublicDTO.build(
        cutoff_at=cutoff_at,
        active_version_references=tuple(references),
        objectives=tuple(objectives),
        units=tuple(units),
        assessment_rubrics=tuple(rubrics),
    )


def serialize_catalog(dto: CurriculumCatalogPublicDTO) -> dict[str, Any]:
    return {
        "cutoffAt": dto.cutoff_at,
        "cutoffSemantics": dto.cutoff_semantics,
        "activeVersionReferences": [
            {
                "curriculumId": item.curriculum_id,
                "stableKey": item.stable_key,
                "versionId": item.version_id,
                "version": item.version,
                "contentHash": item.content_hash,
                "activationEventId": item.activation_event_id,
                "activationSequence": item.activation_sequence,
            }
            for item in dto.active_version_references
        ],
        "objectives": [
            {
                "curriculumId": item.curriculum_id,
                "curriculumVersionId": item.curriculum_version_id,
                "identityId": item.identity_id,
                "stableKey": item.stable_key,
                "title": item.title,
                "description": item.description,
                "orderIndex": item.order_index,
            }
            for item in dto.objectives
        ],
        "units": [serialize_unit(item) for item in dto.units],
        "assessmentRubrics": [
            {
                "curriculumId": item.curriculum_id,
                "curriculumVersionId": item.curriculum_version_id,
                "definitionId": item.definition_id,
                "identityId": item.identity_id,
                "stableKey": item.stable_key,
                "title": item.title,
                "instructions": item.instructions,
                "rubric": json.loads(item.rubric_json),
                "semanticDefinitionId": item.semantic_definition_id,
                "criterionDefinitionId": item.criterion_definition_id,
            }
            for item in dto.assessment_rubrics
        ],
        "inputHash": dto.input_hash,
    }


def serialize_unit(item: CurriculumUnitPublicDTO) -> dict[str, Any]:
    return {
        "curriculumId": item.curriculum_id,
        "curriculumStableKey": item.curriculum_stable_key,
        "curriculumVersionId": item.curriculum_version_id,
        "curriculumVersion": item.curriculum_version,
        "unitIdentityId": item.unit_identity_id,
        "unitStableKey": item.unit_stable_key,
        "unitDefinitionId": item.unit_definition_id,
        "kind": item.kind,
        "candidateType": item.candidate_type,
        "title": item.title,
        "description": item.description,
        "objectiveIdentityId": item.objective_identity_id,
        "objectiveStableKey": item.objective_stable_key,
        "status": item.status,
        "provenance": item.provenance,
        "action": {
            key: value
            for key, value in {
                "kind": item.action.kind,
                "resourceReference": item.action.resource_reference,
                "instructions": item.action.instructions,
                "verificationMethod": item.action.verification_method,
            }.items()
            if value is not None
        },
        "orderIndex": item.order_index,
        "durationRangeMs": list(item.duration_range_ms) if item.duration_range_ms else None,
        "targets": [
            {
                "targetId": target.target_id,
                "semanticDefinitionId": target.semantic_definition_id,
                "criterionDefinitionId": target.criterion_definition_id,
                "scaleVersionId": target.scale_version_id,
                "dimensionId": target.dimension_id,
                "intendedLearningOutcome": target.intended_learning_outcome,
                "minimumLevelId": target.minimum_level_id,
                "maximumLevelId": target.maximum_level_id,
                "supportsUnassessed": target.supports_unassessed,
                "role": target.role,
                "orderIndex": target.order_index,
            }
            for target in item.targets
        ],
        "requirements": [
            {
                "stableKey": requirement.stable_key,
                "requirementType": requirement.requirement_type,
                "effect": requirement.effect,
                "scope": requirement.scope,
                "subject": json.loads(requirement.subject_json),
                "orderIndex": requirement.order_index,
                "policyVersion": requirement.policy_version,
            }
            for requirement in item.requirements
        ],
        "evidenceOpportunities": [
            {
                "stableKey": opportunity.stable_key,
                "evidenceKind": opportunity.evidence_kind,
                "intendedStrengths": list(opportunity.intended_strengths),
                "intendedIndependenceModes": list(opportunity.intended_independence_modes),
                "requiresActualActivity": opportunity.requires_actual_activity,
                "requiresArtifact": opportunity.requires_artifact,
                "orderIndex": opportunity.order_index,
                "policyVersion": opportunity.policy_version,
            }
            for opportunity in item.evidence_opportunities
        ],
    }


def next_activation_sequence(db: Session, curriculum_id: str) -> int:
    return (
        db.scalar(
            select(func.max(CurriculumActivationEvent.event_sequence)).where(
                CurriculumActivationEvent.curriculum_id == curriculum_id
            )
        )
        or 0
    ) + 1


def resolve_requirement_facts(
    db: Session, unit: LearningUnitDefinition, cutoff_at: int
) -> tuple[RequirementFactDTO, ...]:
    """Load cutoff-bounded public facts; missing external facts remain explicitly Unknown."""
    facts: list[RequirementFactDTO] = []
    requirements = db.scalars(
        select(LearningUnitRequirement)
        .where(LearningUnitRequirement.learning_unit_definition_id == unit.id)
        .order_by(LearningUnitRequirement.order_index, LearningUnitRequirement.stable_key)
    ).all()
    version = db.get(CurriculumVersion, unit.curriculum_version_id)
    for requirement in requirements:
        subject = json.loads(requirement.subject_json)
        state = RequirementState.UNKNOWN
        reason = "PUBLIC_FACT_NOT_SUPPLIED"
        if requirement.requirement_type == "capability_at_least":
            definition = db.get(SemanticCompetencyDefinition, subject["semanticDefinitionId"])
            required_level = db.get(CapabilityScaleLevel, subject["levelId"])
            current = capability_as_of(
                db,
                semantic_definition_id=subject["semanticDefinitionId"],
                scale_version_id=subject["scaleVersionId"],
                dimension_id=subject["dimensionId"],
                exclusive_cutoff_at=cutoff_at,
            )
            if definition is None or required_level is None:
                state, reason = RequirementState.UNKNOWN, "CAPABILITY_REFERENCE_MISSING"
            elif (
                current is None
                or current.selected_level_ordinal is None
                or current.assessment_status == "unknown"
            ):
                state, reason = RequirementState.UNKNOWN, "CAPABILITY_UNKNOWN"
            elif current.selected_level_ordinal >= required_level.ordinal_rank:
                state, reason = RequirementState.MET, "CAPABILITY_AT_LEAST_MET"
            else:
                state, reason = RequirementState.NOT_MET, "CAPABILITY_BELOW_REQUIRED"
        elif requirement.requirement_type == "criterion_demonstrated":
            result = criterion_evaluation_as_of(
                db,
                criterion_definition_id=subject["criterionDefinitionId"],
                exclusive_cutoff_at=cutoff_at,
            )
            if result is None or result.state == "unknown":
                state, reason = RequirementState.UNKNOWN, "CRITERION_UNKNOWN"
            elif result.state == "demonstrated":
                state, reason = RequirementState.MET, "CRITERION_DEMONSTRATED"
            else:
                state, reason = RequirementState.NOT_MET, "CRITERION_NOT_DEMONSTRATED"
        elif requirement.requirement_type == "learning_unit_completed" and version is not None:
            identity_id = db.scalar(
                select(LearningUnitIdentity.id).where(
                    LearningUnitIdentity.curriculum_id == version.curriculum_id,
                    LearningUnitIdentity.stable_key == subject["learningUnitStableKey"],
                )
            )
            completed = False
            if identity_id:
                visible_links = db.scalars(
                    select(ActivityCurriculumUnitLink)
                    .join(
                        LearningUnitDefinition,
                        LearningUnitDefinition.id
                        == ActivityCurriculumUnitLink.learning_unit_definition_id,
                    )
                    .outerjoin(
                        ActivityCurriculumLinkCorrection,
                        ActivityCurriculumLinkCorrection.activity_curriculum_unit_link_id
                        == ActivityCurriculumUnitLink.id,
                    )
                    .where(
                        LearningUnitDefinition.unit_identity_id == identity_id,
                        ActivityCurriculumUnitLink.created_at < cutoff_at,
                        (
                            ActivityCurriculumLinkCorrection.id.is_(None)
                            | (ActivityCurriculumLinkCorrection.corrected_at >= cutoff_at)
                        ),
                    )
                ).all()
                completed = any(
                    (
                        actuality := activity_actuality_as_of(
                            db,
                            activity_id=item.activity_id,
                            exclusive_cutoff_at=cutoff_at,
                        )
                    )
                    is not None
                    and actuality.outcome_classification == "completed"
                    for item in visible_links
                )
            state = RequirementState.MET if completed else RequirementState.NOT_MET
            reason = "LEARNING_UNIT_COMPLETED" if completed else "LEARNING_UNIT_NOT_COMPLETED"
        facts.append(
            RequirementFactDTO(
                stable_key=requirement.stable_key,
                state=state,
                reason_code=reason,
            )
        )
    return tuple(facts)


def evaluate_target_suitability(
    db: Session, unit: LearningUnitDefinition, cutoff_at: int
) -> tuple[TargetSuitabilityPublicDTO, ...]:
    """Evaluate each declared target independently against cutoff-visible capability history."""
    results: list[TargetSuitabilityPublicDTO] = []
    targets = db.scalars(
        select(LearningUnitTarget)
        .where(LearningUnitTarget.learning_unit_definition_id == unit.id)
        .order_by(LearningUnitTarget.order_index, LearningUnitTarget.id)
    ).all()
    for target in targets:
        semantic_definition = db.get(SemanticCompetencyDefinition, target.semantic_definition_id)
        assert semantic_definition is not None
        dimension = (
            db.get(CapabilityScaleDimension, target.dimension_id) if target.dimension_id else None
        )
        profile_relevance = target_profile_relevance_as_of(
            db,
            competency_identity_id=semantic_definition.competency_identity_id,
            dimension_key=dimension.stable_key if dimension is not None else None,
            exclusive_cutoff_at=cutoff_at,
        )
        capability = capability_as_of(
            db,
            semantic_definition_id=target.semantic_definition_id,
            scale_version_id=target.scale_version_id,
            dimension_id=target.dimension_id,
            exclusive_cutoff_at=cutoff_at,
        )
        minimum = (
            db.get(CapabilityScaleLevel, target.minimum_level_id)
            if target.minimum_level_id
            else None
        )
        maximum = (
            db.get(CapabilityScaleLevel, target.maximum_level_id)
            if target.maximum_level_id
            else None
        )
        if (
            capability is None
            or capability.assessment_status == "unknown"
            or capability.selected_level_ordinal is None
        ):
            state = "met" if target.supports_unassessed else "unknown"
            reason = "UNASSESSED_SUPPORTED" if target.supports_unassessed else "CAPABILITY_UNKNOWN"
        elif minimum is not None and capability.selected_level_ordinal < minimum.ordinal_rank:
            state, reason = "not_met", "CAPABILITY_BELOW_SUITABILITY_RANGE"
        elif maximum is not None and capability.selected_level_ordinal > maximum.ordinal_rank:
            state, reason = "not_met", "CAPABILITY_ABOVE_SUITABILITY_RANGE"
        else:
            state, reason = "met", "CAPABILITY_SUITABLE"
        results.append(
            TargetSuitabilityPublicDTO(
                target_id=target.id,
                semantic_definition_id=target.semantic_definition_id,
                criterion_definition_id=target.criterion_definition_id,
                scale_version_id=target.scale_version_id,
                dimension_id=target.dimension_id,
                role=target.role,
                state=state,
                reason_code=reason,
                selected_level_id=(
                    capability.selected_level_id if capability is not None else None
                ),
                supports_unassessed=target.supports_unassessed,
                profile_relevance_state=profile_relevance.state,
                target_profile_version_id=profile_relevance.target_profile_version_id,
                profile_target_identity_ids=profile_relevance.profile_target_identity_ids,
                candidate_usability_state="unknown",
            )
        )
    return tuple(results)


def _aggregate_states(states: list[str]) -> str:
    if any(state == "not_met" for state in states):
        return "not_met"
    if any(state == "unknown" for state in states):
        return "unknown"
    return "met"


def _aggregate_alternatives(states: list[str]) -> str:
    if any(state == "met" for state in states):
        return "met"
    if any(state == "unknown" for state in states):
        return "unknown"
    return "not_met"


def build_unit_availability(
    db: Session, unit: LearningUnitDefinition, cutoff_at: int
) -> CurriculumAvailabilityPublicDTO:
    definitions = tuple(
        RequirementDefinitionDTO(
            stable_key=item.stable_key,
            requirement_type=item.requirement_type,
            effect=item.effect,
            subject_json=item.subject_json,
            order_index=item.order_index,
        )
        for item in db.scalars(
            select(LearningUnitRequirement)
            .where(LearningUnitRequirement.learning_unit_definition_id == unit.id)
            .order_by(LearningUnitRequirement.order_index, LearningUnitRequirement.stable_key)
        ).all()
    )
    requirements = evaluate_requirements(
        definitions, resolve_requirement_facts(db, unit, cutoff_at)
    )
    hard_availability = [
        item.state.value
        for item in requirements
        if item.effect == "hard"
        and item.requirement_type in {"resource_available", "user_constraint"}
    ]
    hard_readiness = [
        item.state.value
        for item in requirements
        if item.effect == "hard"
        and item.requirement_type
        in {"capability_at_least", "criterion_demonstrated", "learning_unit_completed"}
    ]
    availability_state = _aggregate_states(hard_availability)
    readiness_state = _aggregate_states(hard_readiness)
    requirement_state = _aggregate_states(hard_availability + hard_readiness)
    raw_suitability = evaluate_target_suitability(db, unit, cutoff_at)
    suitability = tuple(
        replace(
            item,
            candidate_usability_state=_aggregate_states([requirement_state, item.state]),
        )
        for item in raw_suitability
    )
    candidate_state = (
        _aggregate_alternatives([item.candidate_usability_state for item in suitability])
        if suitability
        else requirement_state
    )
    payload = {
        "learningUnitDefinitionId": unit.id,
        "cutoffAt": cutoff_at,
        "cutoffSemantics": "exclusive",
        "availabilityState": availability_state,
        "readinessState": readiness_state,
        "candidateUsabilityState": candidate_state,
        "requirements": [asdict(item) for item in requirements],
        "targetSuitability": [asdict(item) for item in suitability],
        "policyVersion": CURRICULUM_AVAILABILITY_POLICY,
    }
    return CurriculumAvailabilityPublicDTO(
        learning_unit_definition_id=unit.id,
        cutoff_at=cutoff_at,
        cutoff_semantics="exclusive",
        availability_state=availability_state,
        readiness_state=readiness_state,
        candidate_usability_state=candidate_state,
        requirements=requirements,
        target_suitability=suitability,
        policy_version=CURRICULUM_AVAILABILITY_POLICY,
        input_hash=content_hash(payload),
    )


def unit_availability_as_of(
    db: Session, *, learning_unit_definition_id: str, exclusive_cutoff_at: int
) -> CurriculumAvailabilityPublicDTO:
    """Load the public availability contract without exposing Curriculum persistence."""
    unit = db.get(LearningUnitDefinition, learning_unit_definition_id)
    if unit is None:
        raise AppError(404, "CURRICULUM_UNIT_NOT_FOUND", "The learning unit does not exist.")
    return build_unit_availability(db, unit, exclusive_cutoff_at)


def serialize_availability(dto: CurriculumAvailabilityPublicDTO) -> dict[str, Any]:
    return {
        "learningUnitDefinitionId": dto.learning_unit_definition_id,
        "cutoffAt": dto.cutoff_at,
        "cutoffSemantics": dto.cutoff_semantics,
        "availabilityState": dto.availability_state,
        "readinessState": dto.readiness_state,
        "candidateUsabilityState": dto.candidate_usability_state,
        "requirements": [
            {
                "stableKey": item.stable_key,
                "requirementType": item.requirement_type,
                "effect": item.effect,
                "state": item.state.value,
                "reasonCode": item.reason_code,
                "orderIndex": item.order_index,
            }
            for item in dto.requirements
        ],
        "targetSuitability": [
            {
                "targetId": item.target_id,
                "semanticDefinitionId": item.semantic_definition_id,
                "criterionDefinitionId": item.criterion_definition_id,
                "scaleVersionId": item.scale_version_id,
                "dimensionId": item.dimension_id,
                "role": item.role,
                "state": item.state,
                "reasonCode": item.reason_code,
                "selectedLevelId": item.selected_level_id,
                "supportsUnassessed": item.supports_unassessed,
                "profileRelevanceState": item.profile_relevance_state,
                "targetProfileVersionId": item.target_profile_version_id,
                "profileTargetIdentityIds": list(item.profile_target_identity_ids),
                "candidateUsabilityState": item.candidate_usability_state,
            }
            for item in dto.target_suitability
        ],
        "policyVersion": dto.policy_version,
        "inputHash": dto.input_hash,
    }
