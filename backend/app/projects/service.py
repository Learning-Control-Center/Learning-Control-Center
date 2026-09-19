from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.activity_views import activity_actuality_as_of
from app.capability_views import capability_as_of, criterion_evaluation_as_of
from app.determinism import content_hash
from app.domain import has_required_dependency_cycle
from app.errors import AppError
from app.models import (
    Activity,
    CapabilityScaleDimension,
    CapabilityScaleLevel,
    CapabilityScaleVersion,
    CriterionDefinition,
    Evidence,
    EvidenceInvalidation,
    EvidenceRetraction,
    ProjectionInvalidation,
    SemanticCompetencyDefinition,
    SemanticDefinitionDimension,
)
from app.projects.contracts import (
    ActiveProjectVersionReferencePublicDTO,
    ProjectBlockerFactPublicDTO,
    ProjectCandidatePublicDTO,
    ProjectCatalogPublicDTO,
    ProjectEvidenceOpportunityPublicDTO,
    ProjectTargetFactPublicDTO,
    ProjectVersionInput,
)
from app.projects.models import (
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
from app.requirements.contracts import (
    RequirementDefinitionDTO,
    RequirementFactDTO,
    RequirementState,
    evaluate_requirements,
)
from app.roadmap_projection.policies import ACTIVE_PROJECTION_POLICY
from app.session_views import session_actuality_as_of
from app.time_utils import datetime_to_epoch_ms

PROJECT_SCHEMA_VERSION = "project-definition/v1"
PROJECT_REQUIREMENT_POLICY = "project-requirement-policy/v1"
PROJECT_AVAILABILITY_POLICY = "project-availability-policy/v1"
PROJECT_CRITERION_POLICY = "project-criterion-policy/v1"


def link_actual_activity_to_project_task(
    db: Session,
    *,
    activity_id: str,
    task_definition_id: str,
    provenance: str,
    idempotency_key: str,
    created_at: int,
) -> ActivityProjectTaskLink:
    """Create a transaction-aware canonical Activity-to-Project-task attribution."""
    existing = db.scalar(
        select(ActivityProjectTaskLink).where(
            ActivityProjectTaskLink.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        if (
            existing.activity_id == activity_id
            and existing.task_definition_id == task_definition_id
            and existing.provenance == provenance
        ):
            return existing
        raise AppError(409, "IDEMPOTENCY_KEY_REUSED", "The activity-link key was reused.")
    activity = db.get(Activity, activity_id)
    task = db.get(ProjectTaskDefinition, task_definition_id)
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
            ActivityProjectTaskLink.activity_id == activity_id,
            ActivityProjectTaskLink.task_definition_id == task_definition_id,
            ActivityProjectTaskLinkCorrection.id.is_(None),
        )
    )
    if duplicate is not None:
        raise AppError(
            409,
            "PROJECT_ACTIVITY_LINK_EXISTS",
            "The active Activity-to-Project-task link already exists.",
        )
    link = ActivityProjectTaskLink(
        activity_id=activity_id,
        task_definition_id=task_definition_id,
        provenance=provenance,
        idempotency_key=idempotency_key,
        created_at=created_at,
    )
    db.add(link)
    db.flush()
    version = db.get(ProjectVersion, task.project_version_id)
    assert version is not None
    for projection_kind, policy in (
        ("project_availability", PROJECT_AVAILABILITY_POLICY),
        ("roadmap_projection_v2", ACTIVE_PROJECTION_POLICY),
        ("analysis", "analysis-policy/v3.0"),
    ):
        db.add(
            ProjectionInvalidation(
                projection_kind=projection_kind,
                subject_type="project",
                subject_id=version.project_id,
                source_fact_id=link.id,
                target_policy_version=policy,
                status="pending",
                attempt_count=0,
                requested_at=created_at,
            )
        )
    return link


def evaluate_project_criterion_evidence(
    evidence_facts: tuple[dict[str, str], ...],
) -> tuple[str, dict[str, Any]]:
    """Apply the closed deterministic v1 ProjectCriterion evidence policy."""
    ordered = tuple(sorted(evidence_facts, key=lambda item: item["evidenceId"]))
    demonstrated = [
        item
        for item in ordered
        if item["strength"] == "strong"
        and item["independence"] == "independent"
        and item["sourceConfidence"] in {"medium", "high"}
    ]
    partial = [
        item
        for item in ordered
        if item["strength"] in {"moderate", "strong"}
        and item["independence"] in {"assisted", "independent"}
        and item["sourceConfidence"] in {"medium", "high"}
    ]
    unknown = any(
        "unknown" in {item["strength"], item["independence"], item["sourceConfidence"]}
        for item in ordered
    )
    if demonstrated:
        state = "demonstrated"
        decisive = demonstrated
    elif partial:
        state = "partially_demonstrated"
        decisive = partial
    elif unknown:
        state = "unknown"
        decisive = list(ordered)
    else:
        state = "not_demonstrated"
        decisive = list(ordered)
    return state, {
        "policyVersion": PROJECT_CRITERION_POLICY,
        "evidenceFacts": list(ordered),
        "decisiveEvidenceIds": [item["evidenceId"] for item in decisive],
    }


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def command_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _unique(values: Iterable[Any], label: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise AppError(422, "PROJECT_DEFINITION_INVALID", f"{label} must be unique.")


def _normalized_payload(payload: ProjectVersionInput) -> dict[str, Any]:
    value = payload.model_dump(mode="json")
    for key in (
        "goals",
        "milestones",
        "tasks",
        "criteria",
        "targets",
        "requirements",
        "evidence_opportunities",
    ):
        value[key] = sorted(
            value[key], key=lambda item: (item["order_index"], item.get("stable_key", ""))
        )
    for opportunity in value["evidence_opportunities"]:
        opportunity["intended_strengths"] = sorted(opportunity["intended_strengths"])
        opportunity["intended_independence_modes"] = sorted(
            opportunity["intended_independence_modes"]
        )
    value["dependencies"] = sorted(
        value["dependencies"],
        key=lambda item: (
            item["dependent_task_stable_key"],
            item["prerequisite_task_stable_key"],
            item["dependency_type"],
        ),
    )
    return value


def validate_version(db: Session, payload: ProjectVersionInput) -> dict[str, Any]:
    _unique((item.stable_key for item in payload.goals), "Goal stable keys")
    _unique((item.order_index for item in payload.goals), "Goal order indices")
    _unique((item.stable_key for item in payload.milestones), "Milestone stable keys")
    _unique((item.order_index for item in payload.milestones), "Milestone order indices")
    _unique((item.stable_key for item in payload.tasks), "Task stable keys")
    _unique((item.order_index for item in payload.tasks), "Task order indices")
    _unique((item.stable_key for item in payload.criteria), "Criterion stable keys")
    _unique((item.order_index for item in payload.criteria), "Criterion order indices")
    _unique((item.order_index for item in payload.targets), "Target order indices")
    _unique((item.stable_key for item in payload.requirements), "Requirement stable keys")
    _unique((item.order_index for item in payload.requirements), "Requirement order indices")
    _unique((item.stable_key for item in payload.evidence_opportunities), "Opportunity stable keys")
    _unique(
        (item.order_index for item in payload.evidence_opportunities), "Opportunity order indices"
    )
    milestones = {item.stable_key for item in payload.milestones}
    tasks = {item.stable_key for item in payload.tasks}
    criteria = {item.stable_key for item in payload.criteria}
    for task_input in payload.tasks:
        if task_input.milestone_stable_key and task_input.milestone_stable_key not in milestones:
            raise AppError(
                422, "PROJECT_DEFINITION_INVALID", "A task references an unknown milestone."
            )
    for criterion_input in payload.criteria:
        if (
            criterion_input.milestone_stable_key
            and criterion_input.milestone_stable_key not in milestones
        ):
            raise AppError(
                422, "PROJECT_DEFINITION_INVALID", "A criterion references an unknown milestone."
            )
        if criterion_input.evaluation_policy_version != PROJECT_CRITERION_POLICY:
            raise AppError(
                422,
                "PROJECT_CRITERION_POLICY_UNSUPPORTED",
                "The ProjectCriterion evaluation policy is not installed.",
            )
    for target_input in payload.targets:
        if target_input.task_stable_key and target_input.task_stable_key not in tasks:
            raise AppError(
                422, "PROJECT_DEFINITION_INVALID", "A target references an unknown task."
            )
        if (
            target_input.project_criterion_stable_key
            and target_input.project_criterion_stable_key not in criteria
        ):
            raise AppError(
                422,
                "PROJECT_DEFINITION_INVALID",
                "A target references an unknown ProjectCriterion.",
            )
        definition = db.get(SemanticCompetencyDefinition, target_input.semantic_definition_id)
        criterion = (
            db.get(CriterionDefinition, target_input.criterion_definition_id)
            if target_input.criterion_definition_id
            else None
        )
        scale = db.get(CapabilityScaleVersion, target_input.scale_version_id)
        dimension = (
            db.get(CapabilityScaleDimension, target_input.dimension_id)
            if target_input.dimension_id
            else None
        )
        level = (
            db.get(CapabilityScaleLevel, target_input.level_id) if target_input.level_id else None
        )
        if (
            definition is None
            or definition.scale_version_id != target_input.scale_version_id
            or scale is None
            or (criterion is not None and criterion.semantic_definition_id != definition.id)
            or (criterion is not None and criterion.dimension_id != target_input.dimension_id)
            or (criterion is not None and criterion.level_id != target_input.level_id)
            or (target_input.criterion_definition_id and criterion is None)
            or (dimension is not None and dimension.scale_version_id != scale.id)
            or (target_input.dimension_id and dimension is None)
            or (level is not None and level.scale_version_id != scale.id)
            or (target_input.level_id and level is None)
            or (
                target_input.dimension_id is not None
                and definition is not None
                and db.get(
                    SemanticDefinitionDimension,
                    (definition.id, target_input.dimension_id),
                )
                is None
            )
        ):
            raise AppError(
                422,
                "PROJECT_TARGET_INVALID",
                "A Project target has inconsistent semantic references.",
            )
    dependencies: list[tuple[str, str, bool]] = []
    for dependency_input in payload.dependencies:
        if (
            dependency_input.dependent_task_stable_key not in tasks
            or dependency_input.prerequisite_task_stable_key not in tasks
        ):
            raise AppError(
                422, "PROJECT_DEFINITION_INVALID", "A dependency references an unknown task."
            )
        dependencies.append(
            (
                dependency_input.dependent_task_stable_key,
                dependency_input.prerequisite_task_stable_key,
                dependency_input.dependency_type == "hard",
            )
        )
    _unique(dependencies, "Task dependencies")
    hard_edges: dict[str, set[str]] = {task: set() for task in tasks}
    for dependent, prerequisite, hard in dependencies:
        if hard:
            hard_edges[dependent].add(prerequisite)
    if has_required_dependency_cycle(hard_edges):
        raise AppError(
            422, "PROJECT_TASK_DAG_INVALID", "Hard Project task dependencies must form a DAG."
        )
    for requirement_input in payload.requirements:
        if requirement_input.task_stable_key and requirement_input.task_stable_key not in tasks:
            raise AppError(
                422, "PROJECT_DEFINITION_INVALID", "A requirement references an unknown task."
            )
        expected_scope = _validate_requirement_subject(
            db,
            requirement_input.requirement_type,
            requirement_input.subject,
            tasks,
            criteria,
        )
        if requirement_input.scope != expected_scope:
            raise AppError(
                422,
                "PROJECT_REQUIREMENT_INVALID",
                "The Project requirement scope does not match its predicate type.",
            )
    for opportunity_input in payload.evidence_opportunities:
        if opportunity_input.task_stable_key and opportunity_input.task_stable_key not in tasks:
            raise AppError(
                422,
                "PROJECT_DEFINITION_INVALID",
                "An Evidence opportunity references an unknown task.",
            )
        if (
            opportunity_input.project_criterion_stable_key
            and opportunity_input.project_criterion_stable_key not in criteria
        ):
            raise AppError(
                422,
                "PROJECT_DEFINITION_INVALID",
                "An Evidence opportunity references an unknown criterion.",
            )
    normalized = _normalized_payload(payload)
    return {"valid": True, "normalized": normalized, "contentHash": content_hash(normalized)}


def _validate_requirement_subject(
    db: Session,
    requirement_type: str,
    subject: dict[str, Any],
    task_keys: set[str],
    criterion_keys: set[str],
) -> str:
    expected: dict[str, set[str]] = {
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
    if set(subject) != expected[requirement_type]:
        raise AppError(
            422, "PROJECT_REQUIREMENT_INVALID", "A Project requirement subject is malformed."
        )
    if requirement_type == "project_task_completed" and subject["taskStableKey"] not in task_keys:
        raise AppError(
            422,
            "PROJECT_REQUIREMENT_INVALID",
            "A task-completion requirement references an unknown task.",
        )
    if (
        requirement_type == "project_criterion_demonstrated"
        and subject["projectCriterionStableKey"] not in criterion_keys
    ):
        raise AppError(
            422,
            "PROJECT_REQUIREMENT_INVALID",
            "A ProjectCriterion requirement references an unknown criterion.",
        )
    if (
        requirement_type == "criterion_demonstrated"
        and db.get(CriterionDefinition, subject["criterionDefinitionId"]) is None
    ):
        raise AppError(
            422,
            "PROJECT_REQUIREMENT_INVALID",
            "A criterion requirement references an unknown definition.",
        )
    if requirement_type == "capability_at_least":
        definition = db.get(SemanticCompetencyDefinition, subject["semanticDefinitionId"])
        scale = db.get(CapabilityScaleVersion, subject["scaleVersionId"])
        level = db.get(CapabilityScaleLevel, subject["levelId"])
        dimension = (
            db.get(CapabilityScaleDimension, subject["dimensionId"])
            if subject["dimensionId"] is not None
            else None
        )
        enabled_dimension = (
            bool(
                dimension is None
                or db.scalar(
                    select(SemanticDefinitionDimension.semantic_definition_id).where(
                        SemanticDefinitionDimension.semantic_definition_id == definition.id,
                        SemanticDefinitionDimension.scale_dimension_id == dimension.id,
                    )
                )
            )
            if definition is not None
            else False
        )
        if (
            definition is None
            or scale is None
            or definition.scale_version_id != scale.id
            or level is None
            or level.scale_version_id != scale.id
            or (subject["dimensionId"] is not None and dimension is None)
            or (dimension is not None and dimension.scale_version_id != scale.id)
            or not enabled_dimension
        ):
            raise AppError(
                422,
                "PROJECT_REQUIREMENT_INVALID",
                "A capability requirement has inconsistent semantic references.",
            )
    expected_scopes = {
        "capability_at_least": "learner",
        "criterion_demonstrated": "learner",
        "project_criterion_demonstrated": "project",
        "project_task_completed": "project",
        "resource_available": "environment",
        "user_constraint": "user",
    }
    if requirement_type == "resource_available" and (
        not isinstance(subject["resourceKey"], str) or not subject["resourceKey"]
    ):
        raise AppError(
            422,
            "PROJECT_REQUIREMENT_INVALID",
            "A resource requirement must reference a stable resource key.",
        )
    if requirement_type == "user_constraint" and (
        not isinstance(subject["constraintKey"], str)
        or not subject["constraintKey"]
        or subject["expectedValue"] is None
    ):
        raise AppError(
            422,
            "PROJECT_REQUIREMENT_INVALID",
            "A user constraint must reference a stable key and expected value.",
        )
    return expected_scopes[requirement_type]


def _identity(db: Session, model: Any, project: Project, stable_key: str) -> Any:
    value = db.scalar(
        select(model).where(model.project_id == project.id, model.stable_key == stable_key)
    )
    if value is None:
        value = model(project_id=project.id, stable_key=stable_key)
        db.add(value)
        db.flush()
    return value


def _target_signature(
    *,
    semantic_definition_id: str,
    criterion_definition_id: str | None,
    scale_version_id: str,
    dimension_id: str | None,
    level_id: str | None,
) -> tuple[str, str | None, str, str | None, str | None]:
    return (
        semantic_definition_id,
        criterion_definition_id,
        scale_version_id,
        dimension_id,
        level_id,
    )


def _validate_task_identity_compatibility(
    db: Session,
    project: Project,
    payload: ProjectVersionInput,
) -> None:
    new_global = [
        item
        for item in payload.targets
        if item.task_stable_key is None
        and item.project_criterion_stable_key is None
        and item.role == "primary"
    ]
    for task in payload.tasks:
        identity = db.scalar(
            select(ProjectTaskIdentity).where(
                ProjectTaskIdentity.project_id == project.id,
                ProjectTaskIdentity.stable_key == task.stable_key,
            )
        )
        if identity is None:
            continue
        prior = db.execute(
            select(ProjectTaskDefinition, ProjectVersion)
            .join(ProjectVersion, ProjectVersion.id == ProjectTaskDefinition.project_version_id)
            .where(
                ProjectVersion.project_id == project.id,
                ProjectTaskDefinition.task_identity_id == identity.id,
            )
            .order_by(ProjectVersion.version.desc())
            .limit(1)
        ).first()
        if prior is None:
            continue
        previous_task, previous_version = prior
        previous_global = db.scalars(
            select(ProjectTarget).where(
                ProjectTarget.project_version_id == previous_version.id,
                ProjectTarget.task_definition_id.is_(None),
                ProjectTarget.project_criterion_definition_id.is_(None),
                ProjectTarget.role == "primary",
            )
        ).all()
        old_targets = db.scalars(
            select(ProjectTarget).where(
                ProjectTarget.project_version_id == previous_version.id,
                ProjectTarget.task_definition_id == previous_task.id,
                ProjectTarget.role == "primary",
            )
        ).all()
        old_signature = {
            _target_signature(
                semantic_definition_id=item.semantic_definition_id,
                criterion_definition_id=item.criterion_definition_id,
                scale_version_id=item.scale_version_id,
                dimension_id=item.dimension_id,
                level_id=item.level_id,
            )
            for item in (*old_targets, *previous_global)
        }
        new_signature = {
            _target_signature(
                semantic_definition_id=item.semantic_definition_id,
                criterion_definition_id=item.criterion_definition_id,
                scale_version_id=item.scale_version_id,
                dimension_id=item.dimension_id,
                level_id=item.level_id,
            )
            for item in (
                *[
                    target
                    for target in payload.targets
                    if target.task_stable_key == task.stable_key and target.role == "primary"
                ],
                *new_global,
            )
        }
        if old_signature != new_signature:
            raise AppError(
                422,
                "PROJECT_TASK_IDENTITY_INCOMPATIBLE",
                "A stable Project task key cannot change its Primary semantic target signature.",
                {"taskStableKey": task.stable_key},
            )


def create_version(db: Session, project: Project, payload: ProjectVersionInput) -> ProjectVersion:
    validated = validate_version(db, payload)
    previous = db.scalar(
        select(ProjectVersion)
        .where(ProjectVersion.project_id == project.id)
        .order_by(ProjectVersion.version.desc())
        .limit(1)
    )
    _validate_task_identity_compatibility(db, project, payload)
    version = ProjectVersion(
        project_id=project.id,
        version=(previous.version + 1 if previous else 1),
        title=payload.title,
        description=payload.description,
        schema_version=PROJECT_SCHEMA_VERSION,
        definition_payload_json=canonical_json(validated["normalized"]),
        content_hash=validated["contentHash"],
        effective_at=datetime_to_epoch_ms(payload.effective_at),
        creation_source=payload.creation_source,
        supersedes_version_id=previous.id if previous else None,
    )
    db.add(version)
    db.flush()
    goal_ids = {
        item.stable_key: _identity(db, ProjectGoalIdentity, project, item.stable_key)
        for item in payload.goals
    }
    milestone_ids = {
        item.stable_key: _identity(db, ProjectMilestoneIdentity, project, item.stable_key)
        for item in payload.milestones
    }
    task_ids = {
        item.stable_key: _identity(db, ProjectTaskIdentity, project, item.stable_key)
        for item in payload.tasks
    }
    criterion_ids = {
        item.stable_key: _identity(db, ProjectCriterionIdentity, project, item.stable_key)
        for item in payload.criteria
    }
    for goal_input in payload.goals:
        db.add(
            ProjectGoalDefinition(
                project_version_id=version.id,
                goal_identity_id=goal_ids[goal_input.stable_key].id,
                title=goal_input.title,
                description=goal_input.description,
                order_index=goal_input.order_index,
            )
        )
    for milestone_input in payload.milestones:
        db.add(
            ProjectMilestoneDefinition(
                project_version_id=version.id,
                milestone_identity_id=milestone_ids[milestone_input.stable_key].id,
                title=milestone_input.title,
                description=milestone_input.description,
                order_index=milestone_input.order_index,
            )
        )
    db.flush()
    task_defs: dict[str, ProjectTaskDefinition] = {}
    for task_input in payload.tasks:
        task_definition = ProjectTaskDefinition(
            project_version_id=version.id,
            task_identity_id=task_ids[task_input.stable_key].id,
            milestone_identity_id=milestone_ids[task_input.milestone_stable_key].id
            if task_input.milestone_stable_key
            else None,
            title=task_input.title,
            description=task_input.description,
            instructions=task_input.instructions,
            status=task_input.status,
            order_index=task_input.order_index,
            minimum_useful_duration_ms=task_input.minimum_useful_duration_ms,
            preferred_duration_ms=task_input.preferred_duration_ms,
            maximum_useful_duration_ms=task_input.maximum_useful_duration_ms,
        )
        db.add(task_definition)
        db.flush()
        task_defs[task_input.stable_key] = task_definition
    criterion_defs: dict[str, ProjectCriterionDefinition] = {}
    for criterion_input in payload.criteria:
        criterion_definition = ProjectCriterionDefinition(
            project_version_id=version.id,
            criterion_identity_id=criterion_ids[criterion_input.stable_key].id,
            milestone_identity_id=milestone_ids[criterion_input.milestone_stable_key].id
            if criterion_input.milestone_stable_key
            else None,
            title=criterion_input.title,
            description=criterion_input.description,
            evaluation_policy_version=criterion_input.evaluation_policy_version,
            order_index=criterion_input.order_index,
        )
        db.add(criterion_definition)
        db.flush()
        criterion_defs[criterion_input.stable_key] = criterion_definition
    for target_input in payload.targets:
        db.add(
            ProjectTarget(
                project_version_id=version.id,
                task_definition_id=task_defs[target_input.task_stable_key].id
                if target_input.task_stable_key
                else None,
                project_criterion_definition_id=criterion_defs[
                    target_input.project_criterion_stable_key
                ].id
                if target_input.project_criterion_stable_key
                else None,
                semantic_definition_id=target_input.semantic_definition_id,
                criterion_definition_id=target_input.criterion_definition_id,
                scale_version_id=target_input.scale_version_id,
                dimension_id=target_input.dimension_id,
                level_id=target_input.level_id,
                intended_outcome=target_input.intended_outcome,
                role=target_input.role,
                order_index=target_input.order_index,
            )
        )
    for requirement_input in payload.requirements:
        db.add(
            ProjectRequirement(
                project_version_id=version.id,
                task_definition_id=task_defs[requirement_input.task_stable_key].id
                if requirement_input.task_stable_key
                else None,
                stable_key=requirement_input.stable_key,
                requirement_type=requirement_input.requirement_type,
                effect=requirement_input.effect,
                scope=requirement_input.scope,
                subject_json=canonical_json(requirement_input.subject),
                order_index=requirement_input.order_index,
                policy_version=PROJECT_REQUIREMENT_POLICY,
            )
        )
    for dependency_input in payload.dependencies:
        db.add(
            ProjectTaskDependency(
                project_version_id=version.id,
                dependent_task_identity_id=task_ids[dependency_input.dependent_task_stable_key].id,
                prerequisite_task_identity_id=task_ids[
                    dependency_input.prerequisite_task_stable_key
                ].id,
                dependency_type=dependency_input.dependency_type,
            )
        )
    for opportunity_input in payload.evidence_opportunities:
        db.add(
            ProjectEvidenceOpportunity(
                project_version_id=version.id,
                task_definition_id=task_defs[opportunity_input.task_stable_key].id
                if opportunity_input.task_stable_key
                else None,
                project_criterion_definition_id=criterion_defs[
                    opportunity_input.project_criterion_stable_key
                ].id
                if opportunity_input.project_criterion_stable_key
                else None,
                stable_key=opportunity_input.stable_key,
                evidence_kind=opportunity_input.evidence_kind,
                intended_characteristics_json=canonical_json(
                    {
                        "intended_strengths": sorted(opportunity_input.intended_strengths),
                        "intended_independence_modes": sorted(
                            opportunity_input.intended_independence_modes
                        ),
                        "requires_artifact": opportunity_input.requires_artifact,
                    }
                ),
                order_index=opportunity_input.order_index,
                policy_version="project-evidence-opportunity-policy/v1",
            )
        )
    db.flush()
    return version


def active_project_versions_as_of(
    db: Session, cutoff_at: int
) -> list[tuple[Project, ProjectVersion, ProjectVersionActivationEvent]]:
    result: list[tuple[Project, ProjectVersion, ProjectVersionActivationEvent]] = []
    for project in db.scalars(select(Project).order_by(Project.stable_key)).all():
        event = db.scalar(
            select(ProjectVersionActivationEvent)
            .join(
                ProjectVersion,
                ProjectVersion.id == ProjectVersionActivationEvent.to_project_version_id,
            )
            .where(
                ProjectVersionActivationEvent.project_id == project.id,
                ProjectVersionActivationEvent.activated_at < cutoff_at,
                ProjectVersion.effective_at < cutoff_at,
            )
            .order_by(
                ProjectVersionActivationEvent.activated_at.desc(),
                ProjectVersionActivationEvent.event_sequence.desc(),
                ProjectVersionActivationEvent.id.desc(),
            )
            .limit(1)
        )
        if event is not None:
            version = db.get(ProjectVersion, event.to_project_version_id)
            assert version is not None
            result.append((project, version, event))
    return result


def _effective_events(db: Session, project_id: str, cutoff_at: int) -> list[ProjectEvent]:
    events = db.scalars(
        select(ProjectEvent)
        .where(ProjectEvent.project_id == project_id, ProjectEvent.occurred_at < cutoff_at)
        .order_by(ProjectEvent.event_sequence, ProjectEvent.id)
    ).all()
    corrected = {
        item.corrects_event_id
        for item in events
        if item.event_type == "blocker_corrected" and item.corrects_event_id
    }
    return [item for item in events if item.id not in corrected]


def project_lifecycle_as_of(db: Session, project_id: str, cutoff_at: int) -> str:
    state = "planned"
    for event in _effective_events(db, project_id, cutoff_at):
        if event.event_type == "project_lifecycle" and event.project_lifecycle_state:
            state = event.project_lifecycle_state
    return state


def task_lifecycle_as_of(
    db: Session, project_id: str, task_identity_id: str, cutoff_at: int
) -> str:
    state = "not_started"
    for event in _effective_events(db, project_id, cutoff_at):
        if (
            event.event_type == "task_lifecycle"
            and event.task_identity_id == task_identity_id
            and event.task_lifecycle_state
        ):
            state = event.task_lifecycle_state
    return state


def blockers_as_of(
    db: Session, project_id: str, task_identity_id: str, cutoff_at: int
) -> dict[str, ProjectEvent]:
    open_items: dict[str, ProjectEvent] = {}
    for event in _effective_events(db, project_id, cutoff_at):
        if event.task_identity_id != task_identity_id or not event.blocker_key:
            continue
        if event.event_type in {"blocker_opened", "blocker_corrected"}:
            open_items[event.blocker_key] = event
        elif event.event_type == "blocker_resolved":
            open_items.pop(event.blocker_key, None)
    return open_items


def _blocker_opened_at(db: Session, event: ProjectEvent) -> int:
    current = event
    visited: set[str] = set()
    while current.event_type == "blocker_corrected" and current.corrects_event_id:
        if current.id in visited:
            break
        visited.add(current.id)
        original = db.get(ProjectEvent, current.corrects_event_id)
        if original is None:
            break
        current = original
    return current.occurred_at


def project_criterion_state_as_of(db: Session, criterion_definition_id: str, cutoff_at: int) -> str:
    evaluation = db.scalar(
        select(ProjectCriterionEvaluation)
        .where(
            ProjectCriterionEvaluation.project_criterion_definition_id == criterion_definition_id,
            ProjectCriterionEvaluation.evaluated_at < cutoff_at,
        )
        .order_by(
            ProjectCriterionEvaluation.evaluated_at.desc(), ProjectCriterionEvaluation.id.desc()
        )
        .limit(1)
    )
    if evaluation is None:
        return "unknown"
    evidence_ids = set(
        db.scalars(
            select(ProjectCriterionEvaluationEvidence.evidence_id).where(
                ProjectCriterionEvaluationEvidence.project_criterion_evaluation_id == evaluation.id
            )
        ).all()
    )
    if not evidence_ids:
        return "unknown"
    invalid = (
        db.scalar(
            select(func.count())
            .select_from(EvidenceInvalidation)
            .where(
                EvidenceInvalidation.evidence_id.in_(evidence_ids),
                EvidenceInvalidation.created_at < cutoff_at,
            )
        )
        or 0
    )
    retracted = (
        db.scalar(
            select(func.count())
            .select_from(EvidenceRetraction)
            .where(
                EvidenceRetraction.evidence_id.in_(evidence_ids),
                EvidenceRetraction.created_at < cutoff_at,
            )
        )
        or 0
    )
    if invalid or retracted:
        return "unknown"
    evidence_count = (
        db.scalar(
            select(func.count())
            .select_from(Evidence)
            .where(
                Evidence.id.in_(evidence_ids),
                Evidence.created_at < cutoff_at,
                Evidence.occurred_at < cutoff_at,
            )
        )
        or 0
    )
    return evaluation.state if evidence_count == len(evidence_ids) else "unknown"


def _task_completed_as_of(db: Session, project_id: str, stable_key: str, cutoff_at: int) -> str:
    identity = db.scalar(
        select(ProjectTaskIdentity).where(
            ProjectTaskIdentity.project_id == project_id,
            ProjectTaskIdentity.stable_key == stable_key,
        )
    )
    if identity is None:
        return "unknown"
    return (
        "met"
        if task_lifecycle_as_of(db, project_id, identity.id, cutoff_at) == "completed"
        else "not_met"
    )


def _requirement_fact(
    db: Session, project: Project, version: ProjectVersion, item: ProjectRequirement, cutoff_at: int
) -> RequirementFactDTO:
    subject = json.loads(item.subject_json)
    state = RequirementState.UNKNOWN
    reason = "REQUIREMENT_FACT_UNKNOWN"
    if item.requirement_type == "capability_at_least":
        capability_fact = capability_as_of(
            db,
            semantic_definition_id=subject["semanticDefinitionId"],
            scale_version_id=subject["scaleVersionId"],
            dimension_id=subject["dimensionId"],
            exclusive_cutoff_at=cutoff_at,
        )
        required = db.get(CapabilityScaleLevel, subject["levelId"])
        selected = (
            db.get(CapabilityScaleLevel, capability_fact.selected_level_id)
            if capability_fact is not None and capability_fact.selected_level_id
            else None
        )
        if (
            capability_fact is not None
            and capability_fact.assessment_status == "evaluated"
            and required
            and selected
            and required.scale_version_id == selected.scale_version_id
        ):
            state = (
                RequirementState.MET
                if selected.ordinal_rank >= required.ordinal_rank
                else RequirementState.NOT_MET
            )
            reason = (
                "CAPABILITY_LEVEL_MET"
                if state == RequirementState.MET
                else "CAPABILITY_LEVEL_NOT_MET"
            )
    elif item.requirement_type == "criterion_demonstrated":
        criterion_fact = criterion_evaluation_as_of(
            db,
            criterion_definition_id=subject["criterionDefinitionId"],
            exclusive_cutoff_at=cutoff_at,
        )
        value = criterion_fact.state if criterion_fact is not None else "unknown"
        state = (
            RequirementState.MET
            if value == "demonstrated"
            else (RequirementState.UNKNOWN if value == "unknown" else RequirementState.NOT_MET)
        )
        reason = f"CRITERION_{value.upper()}"
    elif item.requirement_type == "project_criterion_demonstrated":
        identity = db.scalar(
            select(ProjectCriterionIdentity).where(
                ProjectCriterionIdentity.project_id == project.id,
                ProjectCriterionIdentity.stable_key == subject["projectCriterionStableKey"],
            )
        )
        definition = db.scalar(
            select(ProjectCriterionDefinition).where(
                ProjectCriterionDefinition.project_version_id == version.id,
                ProjectCriterionDefinition.criterion_identity_id
                == (identity.id if identity else ""),
            )
        )
        value = (
            project_criterion_state_as_of(db, definition.id, cutoff_at) if definition else "unknown"
        )
        state = (
            RequirementState.MET
            if value == "demonstrated"
            else (RequirementState.UNKNOWN if value == "unknown" else RequirementState.NOT_MET)
        )
        reason = f"PROJECT_CRITERION_{value.upper()}"
    elif item.requirement_type == "project_task_completed":
        state = RequirementState(
            _task_completed_as_of(db, project.id, subject["taskStableKey"], cutoff_at)
        )
        reason = (
            "PROJECT_TASK_COMPLETED"
            if state == RequirementState.MET
            else "PROJECT_TASK_NOT_COMPLETED"
        )
    elif item.requirement_type == "resource_available":
        state = RequirementState.UNKNOWN
        reason = "RESOURCE_FACT_NOT_SUPPLIED"
    elif item.requirement_type == "user_constraint":
        state = RequirementState.UNKNOWN
        reason = "USER_CONSTRAINT_FACT_NOT_SUPPLIED"
    return RequirementFactDTO(item.stable_key, state, reason)


def _aggregate(states: list[str]) -> str:
    if any(item == "not_met" for item in states):
        return "not_met"
    if any(item == "unknown" for item in states):
        return "unknown"
    return "met"


def build_task_candidate(
    db: Session,
    project: Project,
    version: ProjectVersion,
    task: ProjectTaskDefinition,
    cutoff_at: int,
) -> ProjectCandidatePublicDTO:
    identity = db.get(ProjectTaskIdentity, task.task_identity_id)
    assert identity is not None
    lifecycle = task_lifecycle_as_of(db, project.id, identity.id, cutoff_at)
    project_lifecycle = project_lifecycle_as_of(db, project.id, cutoff_at)
    blockers = blockers_as_of(db, project.id, identity.id, cutoff_at)
    requirements = db.scalars(
        select(ProjectRequirement)
        .where(
            ProjectRequirement.project_version_id == version.id,
            (ProjectRequirement.task_definition_id == task.id)
            | (ProjectRequirement.task_definition_id.is_(None)),
        )
        .order_by(ProjectRequirement.order_index)
    ).all()
    evaluations = evaluate_requirements(
        tuple(
            RequirementDefinitionDTO(
                item.stable_key,
                item.requirement_type,
                item.effect,
                item.subject_json,
                item.order_index,
            )
            for item in requirements
        ),
        tuple(_requirement_fact(db, project, version, item, cutoff_at) for item in requirements),
    )
    hard_availability = [
        item.state.value
        for item in evaluations
        if item.effect == "hard"
        and item.requirement_type in {"resource_available", "user_constraint"}
    ]
    hard_readiness = [
        item.state.value
        for item in evaluations
        if item.effect == "hard"
        and item.requirement_type not in {"resource_available", "user_constraint"}
    ]
    hard_dependencies = db.scalars(
        select(ProjectTaskDependency).where(
            ProjectTaskDependency.project_version_id == version.id,
            ProjectTaskDependency.dependent_task_identity_id == identity.id,
            ProjectTaskDependency.dependency_type == "hard",
        )
    ).all()
    for dependency in hard_dependencies:
        if (
            task_lifecycle_as_of(
                db, project.id, dependency.prerequisite_task_identity_id, cutoff_at
            )
            != "completed"
        ):
            hard_readiness.append("not_met")
    availability = _aggregate(hard_availability) if hard_availability else "met"
    readiness = _aggregate(hard_readiness) if hard_readiness else "met"
    if (
        project_lifecycle != "active"
        or task.status == "archived"
        or lifecycle in {"completed", "cancelled"}
    ):
        usability = "not_met"
    elif blockers:
        usability = "not_met"
        availability = "not_met"
    else:
        usability = _aggregate([availability, readiness])
    target_rows = db.scalars(
        select(ProjectTarget)
        .where(
            ProjectTarget.project_version_id == version.id,
            (ProjectTarget.task_definition_id == task.id)
            | (
                ProjectTarget.task_definition_id.is_(None)
                & ProjectTarget.project_criterion_definition_id.is_(None)
            ),
        )
        .order_by(ProjectTarget.order_index, ProjectTarget.id)
    ).all()
    targets = tuple(item.id for item in target_rows)
    target_facts = tuple(
        ProjectTargetFactPublicDTO(
            target_id=item.id,
            semantic_definition_id=item.semantic_definition_id,
            criterion_definition_id=item.criterion_definition_id,
            scale_version_id=item.scale_version_id,
            dimension_id=item.dimension_id,
            level_id=item.level_id,
            intended_outcome=item.intended_outcome,
            role=item.role,
        )
        for item in target_rows
    )
    opportunity_rows = db.scalars(
        select(ProjectEvidenceOpportunity)
        .where(
            ProjectEvidenceOpportunity.project_version_id == version.id,
            ProjectEvidenceOpportunity.task_definition_id == task.id,
        )
        .order_by(ProjectEvidenceOpportunity.order_index, ProjectEvidenceOpportunity.id)
    ).all()
    opportunity_modes = tuple(
        sorted(
            {
                str(mode)
                for opportunity in opportunity_rows
                for mode in json.loads(opportunity.intended_characteristics_json).get(
                    "intended_independence_modes", ()
                )
            }
        )
    )
    opportunity_facts = tuple(
        ProjectEvidenceOpportunityPublicDTO(
            item.id,
            item.task_definition_id,
            item.project_criterion_definition_id,
            tuple(
                json.loads(item.intended_characteristics_json).get(
                    "intended_independence_modes", ()
                )
            ),
        )
        for item in opportunity_rows
    )
    blocker_facts = tuple(
        ProjectBlockerFactPublicDTO(
            event_id=event.id,
            blocker_key=key,
            actionable=bool(event.blocker_actionable),
            details=json.loads(event.payload_json),
            opened_at=_blocker_opened_at(db, event),
            corrected_at=event.occurred_at if event.event_type == "blocker_corrected" else None,
        )
        for key, event in sorted(blockers.items())
    )
    duration = None
    if task.minimum_useful_duration_ms is not None:
        assert (
            task.preferred_duration_ms is not None and task.maximum_useful_duration_ms is not None
        )
        duration = (
            task.minimum_useful_duration_ms,
            task.preferred_duration_ms,
            task.maximum_useful_duration_ms,
        )
    base = {
        "projectId": project.id,
        "projectVersionId": version.id,
        "taskDefinitionId": task.id,
        "cutoffAt": cutoff_at,
        "lifecycle": lifecycle,
        "projectLifecycle": project_lifecycle,
        "availability": availability,
        "readiness": readiness,
        "blockers": sorted(blockers),
        "blockerFacts": [asdict(item) for item in blocker_facts],
        "requirements": [asdict(item) for item in evaluations],
        "targets": [asdict(item) for item in target_facts],
        "evidenceOpportunities": [
            {
                "id": item.id,
                "intendedCharacteristics": json.loads(item.intended_characteristics_json),
            }
            for item in opportunity_rows
        ],
    }
    return ProjectCandidatePublicDTO(
        "project_task",
        project.id,
        project.stable_key,
        version.id,
        identity.id,
        task.id,
        identity.stable_key,
        task.title,
        task.description,
        task.instructions,
        duration,
        lifecycle,
        availability,
        readiness,
        usability,
        tuple(sorted(key for key, event in blockers.items() if event.blocker_actionable)),
        blocker_facts,
        evaluations,
        targets,
        target_facts,
        content_hash(base),
        tuple(item.id for item in opportunity_rows),
        opportunity_modes,
        opportunity_facts,
    )


def build_catalog(db: Session, cutoff_at: int) -> ProjectCatalogPublicDTO:
    refs: list[ActiveProjectVersionReferencePublicDTO] = []
    candidates: list[ProjectCandidatePublicDTO] = []
    for project, version, activation in active_project_versions_as_of(db, cutoff_at):
        latest_project_event = db.scalar(
            select(ProjectEvent)
            .where(ProjectEvent.project_id == project.id, ProjectEvent.occurred_at < cutoff_at)
            .order_by(ProjectEvent.event_sequence.desc(), ProjectEvent.id.desc())
            .limit(1)
        )
        refs.append(
            ActiveProjectVersionReferencePublicDTO(
                project.id,
                project.stable_key,
                version.id,
                version.version,
                version.content_hash,
                activation.id,
                activation.event_sequence,
                latest_project_event.id if latest_project_event else None,
                latest_project_event.event_sequence if latest_project_event else 0,
            )
        )
        for task in db.scalars(
            select(ProjectTaskDefinition)
            .where(ProjectTaskDefinition.project_version_id == version.id)
            .order_by(ProjectTaskDefinition.order_index, ProjectTaskDefinition.id)
        ).all():
            candidates.append(build_task_candidate(db, project, version, task, cutoff_at))
    return ProjectCatalogPublicDTO.build(
        cutoff_at=cutoff_at, active_version_references=tuple(refs), candidates=tuple(candidates)
    )


def activity_project_link_is_actual(
    db: Session, link: ActivityProjectTaskLink, cutoff_at: int
) -> bool:
    activity = db.get(Activity, link.activity_id)
    if (
        activity is None
        or link.created_at >= cutoff_at
        or activity_actuality_as_of(db, activity_id=activity.id, exclusive_cutoff_at=cutoff_at)
        is None
    ):
        return False
    correction = db.scalar(
        select(ActivityProjectTaskLinkCorrection).where(
            ActivityProjectTaskLinkCorrection.activity_project_task_link_id == link.id,
            ActivityProjectTaskLinkCorrection.corrected_at < cutoff_at,
        )
    )
    return correction is None


def project_duration_context_ms(db: Session, project_id: str, cutoff_at: int) -> int:
    """Return cutoff-visible actual Session duration once from Project attribution facts."""
    session_ids = set(
        db.scalars(
            select(SessionProjectContribution.session_id)
            .outerjoin(
                SessionProjectContributionRetraction,
                (
                    SessionProjectContributionRetraction.contribution_id
                    == SessionProjectContribution.id
                )
                & (SessionProjectContributionRetraction.retracted_at < cutoff_at),
            )
            .where(
                SessionProjectContribution.project_id == project_id,
                SessionProjectContribution.created_at < cutoff_at,
                SessionProjectContributionRetraction.id.is_(None),
            )
        ).all()
    )
    return sum(
        actuality.duration_ms
        for session_id in session_ids
        if (
            actuality := session_actuality_as_of(
                db, session_id=session_id, exclusive_cutoff_at=cutoff_at
            )
        )
        is not None
    )
