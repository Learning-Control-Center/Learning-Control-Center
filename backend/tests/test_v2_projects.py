from __future__ import annotations

import ast
import json
import shutil
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app import database
from app.analysis.contracts import content_hash
from app.errors import AppError
from app.import_export import _apply_portable_restore, _portable_payload, _validate_portable_payload
from app.models import (
    Activity,
    CompetencyCapabilityState,
    Evidence,
    EvidenceInvalidation,
    EvidenceLink,
    LearningSession,
    SessionContribution,
    SessionCorrection,
)
from app.portability.registry import PORTABLE_V3_MANIFEST, PORTABLE_V4_PROJECT_TABLES
from app.projects.contracts import ProjectVersionInput
from app.projects.evidence_policy import derive_project_evidence_characteristics
from app.projects.models import (
    ActivityProjectTaskLink,
    ActivityProjectTaskLinkCorrection,
    Project,
    ProjectCriterionEvaluation,
    ProjectEvent,
    ProjectTaskIdentity,
    ProjectVersion,
    ProjectVersionActivationEvent,
    SessionProjectContribution,
    SessionProjectContributionRetraction,
)
from app.projects.service import build_catalog, project_duration_context_ms
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session


async def _semantic_target(
    client: AsyncClient, csrf: str
) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
    scales = await client.get("/api/v2/capability-scales")
    technical = next(item for item in scales.json() if item["stableKey"] == "technical")
    levels = {item["stableKey"]: item for item in technical["levels"]}
    competency = await client.post(
        "/api/v2/competencies",
        json={"stable_key": "python.project", "creation_source": "test"},
        headers={"X-CSRF-Token": csrf},
    )
    assert competency.status_code == 201, competency.text
    definition = await client.post(
        f"/api/v2/competencies/{competency.json()['id']}/definitions",
        json={
            "title": "Python Project Delivery",
            "description": "Exact project target",
            "scope": "Deliver a tested Python project",
            "scale_stable_key": "technical",
            "scale_version": technical["version"],
            "dimension_keys": [],
            "effective_at": "2026-01-01T00:00:00Z",
            "creation_source": "test",
            "criteria": [
                {
                    "stable_key": "tested-delivery",
                    "level_stable_key": "independent",
                    "dimension_key": None,
                    "requirement_type": "required",
                    "demonstration_rule": "independent_performance",
                    "description": "Deliver a tested result",
                }
            ],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert definition.status_code == 201, definition.text
    return technical, levels, competency.json(), definition.json()


def _project_payload(
    technical: dict[str, object],
    levels: dict[str, object],
    definition: dict[str, object],
) -> dict[str, object]:
    criterion = definition["criteria"][0]  # type: ignore[index]
    target = {
        "semantic_definition_id": definition["id"],
        "criterion_definition_id": criterion["id"],  # type: ignore[index]
        "scale_version_id": technical["id"],
        "dimension_id": None,
        "level_id": levels["independent"]["id"],  # type: ignore[index]
        "intended_outcome": "Deliver a tested result.",
        "role": "primary",
    }
    return {
        "title": "Ship a Python Service",
        "description": "Outcome-driven Project",
        "effective_at": "2026-01-02T00:00:00Z",
        "creation_source": "test",
        "goals": [
            {
                "stable_key": "ship-service",
                "title": "Ship service",
                "description": "A working tested service",
                "order_index": 0,
            }
        ],
        "milestones": [
            {
                "stable_key": "delivery",
                "title": "Delivery",
                "description": "Ready to ship",
                "order_index": 0,
            }
        ],
        "tasks": [
            {
                "stable_key": "build",
                "title": "Build the service",
                "description": "Implement the behavior",
                "instructions": "Implement and exercise the service.",
                "milestone_stable_key": "delivery",
                "status": "active",
                "order_index": 0,
                "minimum_useful_duration_ms": 300_000,
                "preferred_duration_ms": 600_000,
                "maximum_useful_duration_ms": 900_000,
            },
            {
                "stable_key": "release",
                "title": "Release the service",
                "description": "Publish the verified result",
                "instructions": "Release only after evidence-backed acceptance.",
                "milestone_stable_key": "delivery",
                "status": "active",
                "order_index": 1,
                "minimum_useful_duration_ms": 300_000,
                "preferred_duration_ms": 300_000,
                "maximum_useful_duration_ms": 600_000,
            },
        ],
        "criteria": [
            {
                "stable_key": "tests-pass",
                "title": "Tests pass",
                "description": "Automated acceptance evidence passes",
                "milestone_stable_key": "delivery",
                "evaluation_policy_version": "project-criterion-policy/v1",
                "order_index": 0,
            }
        ],
        "targets": [
            {**target, "task_stable_key": "build", "order_index": 0},
            {
                **target,
                "project_criterion_stable_key": "tests-pass",
                "order_index": 1,
            },
        ],
        "requirements": [
            {
                "task_stable_key": "build",
                "stable_key": "workspace-ready",
                "requirement_type": "resource_available",
                "effect": "hard",
                "scope": "environment",
                "subject": {"resourceKey": "workspace"},
                "order_index": 0,
            },
            {
                "task_stable_key": "release",
                "stable_key": "tests-demonstrated",
                "requirement_type": "project_criterion_demonstrated",
                "effect": "hard",
                "scope": "project",
                "subject": {"projectCriterionStableKey": "tests-pass"},
                "order_index": 1,
            },
        ],
        "dependencies": [
            {
                "dependent_task_stable_key": "release",
                "prerequisite_task_stable_key": "build",
                "dependency_type": "hard",
            }
        ],
        "evidence_opportunities": [
            {
                "stable_key": "test-run",
                "task_stable_key": "build",
                "project_criterion_stable_key": "tests-pass",
                "evidence_kind": "project",
                "intended_strengths": ["weak", "moderate", "strong"],
                "intended_independence_modes": ["guided", "assisted", "independent"],
                "requires_artifact": True,
                "order_index": 0,
            }
        ],
    }


async def _create_active_project(
    client: AsyncClient, csrf: str
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    technical, levels, competency, definition = await _semantic_target(client, csrf)
    project = await client.post(
        "/api/v2/projects",
        json={"stable_key": "ship-python", "creation_source": "test"},
        headers={"X-CSRF-Token": csrf},
    )
    assert project.status_code == 201, project.text
    version = await client.post(
        f"/api/v2/projects/{project.json()['id']}/versions",
        json=_project_payload(technical, levels, definition),
        headers={"X-CSRF-Token": csrf},
    )
    assert version.status_code == 201, version.text
    activated = await client.post(
        f"/api/v2/projects/{project.json()['id']}/versions/{version.json()['id']}/activate",
        json={
            "reason": "Test active definition",
            "source": "test",
            "idempotency_key": "activate-project",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert activated.status_code == 200, activated.text
    lifecycle = await client.post(
        f"/api/v2/projects/{project.json()['id']}/events",
        json={
            "event_type": "project_lifecycle",
            "project_lifecycle_state": "active",
            "source": "test",
            "idempotency_key": "project-active",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert lifecycle.status_code == 201, lifecycle.text
    return project.json(), version.json(), competency, technical, levels, definition


def test_project_contract_rejects_mixed_duration_and_hard_cycle() -> None:
    with pytest.raises(ValidationError, match="all known or all unknown"):
        ProjectVersionInput.model_validate(
            {
                "title": "Invalid",
                "effective_at": "2026-01-01T00:00:00Z",
                "tasks": [
                    {
                        "stable_key": "one",
                        "title": "One",
                        "instructions": "Do it",
                        "order_index": 0,
                        "minimum_useful_duration_ms": 300_000,
                    }
                ],
            }
        )
    ordinary_artifact = derive_project_evidence_characteristics(
        activity_outcome="completed",
        has_artifact=True,
        assistance_modes=("none",),
        attribution_provenance="user_confirmed",
        rubric_result=None,
        has_project_criterion=True,
    )
    assert ordinary_artifact.strength == "moderate"
    assert ordinary_artifact.source_confidence == "medium"
    assisted = derive_project_evidence_characteristics(
        activity_outcome="completed",
        has_artifact=True,
        assistance_modes=("ai_assisted",),
        attribution_provenance="user_selected",
        rubric_result="passed",
        has_project_criterion=True,
    )
    assert assisted.strength == "strong"
    assert assisted.independence == "assisted"
    assert assisted.source_confidence == "low"
    unknown = derive_project_evidence_characteristics(
        activity_outcome=None,
        has_artifact=False,
        assistance_modes=(),
        attribution_provenance="imported_asserted",
        rubric_result=None,
        has_project_criterion=True,
    )
    assert unknown.strength == "unknown"
    assert unknown.strength_unknown_reason == "activity_outcome_unknown"
    assert unknown.independence == "unknown"
    assert unknown.independence_unknown_reason == "session_assistance_unknown"
    failed = derive_project_evidence_characteristics(
        activity_outcome="completed",
        has_artifact=True,
        assistance_modes=("none",),
        attribution_provenance="user_confirmed",
        rubric_result="not_met",
        has_project_criterion=True,
    )
    assert failed.strength == "weak"


async def test_project_dependency_cycles_distinguish_hard_from_recommended(
    authenticated_client: tuple[AsyncClient, str],
) -> None:
    client, csrf = authenticated_client
    technical, levels, _competency, definition = await _semantic_target(client, csrf)
    project = await client.post(
        "/api/v2/projects",
        json={"stable_key": "dependency-cycle-project", "creation_source": "test"},
        headers={"X-CSRF-Token": csrf},
    )
    assert project.status_code == 201, project.text
    payload = _project_payload(technical, levels, definition)
    hard_cycle = deepcopy(payload)
    hard_cycle["dependencies"].append(  # type: ignore[union-attr]
        {
            "dependent_task_stable_key": "build",
            "prerequisite_task_stable_key": "release",
            "dependency_type": "hard",
        }
    )
    rejected = await client.post(
        f"/api/v2/projects/{project.json()['id']}/versions/validate",
        json=hard_cycle,
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "PROJECT_TASK_DAG_INVALID"

    soft_cycle = deepcopy(payload)
    soft_cycle["dependencies"].append(  # type: ignore[union-attr]
        {
            "dependent_task_stable_key": "build",
            "prerequisite_task_stable_key": "release",
            "dependency_type": "recommended_before",
        }
    )
    accepted = await client.post(
        f"/api/v2/projects/{project.json()['id']}/versions/validate",
        json=soft_cycle,
        headers={"X-CSRF-Token": csrf},
    )
    assert accepted.status_code == 200, accepted.text

    mismatched_target = deepcopy(payload)
    alternate_level = next(item for key, item in levels.items() if key != "independent")
    mismatched_target["targets"][0]["level_id"] = alternate_level["id"]  # type: ignore[index]
    rejected_target = await client.post(
        f"/api/v2/projects/{project.json()['id']}/versions/validate",
        json=mismatched_target,
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected_target.status_code == 422, rejected_target.text
    assert rejected_target.json()["error"]["code"] == "PROJECT_TARGET_INVALID"


async def test_project_lifecycle_blockers_evidence_and_capability_separation(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    project, version, competency, _technical, _levels, _definition = await _create_active_project(
        client, csrf
    )
    tasks = {item["stableKey"]: item for item in version["tasks"]}
    criterion = version["criteria"][0]
    opportunity = version["evidenceOpportunities"][0]

    activation = db.scalar(
        select(ProjectVersionActivationEvent).where(
            ProjectVersionActivationEvent.project_id == project["id"]
        )
    )
    assert activation is not None
    project_record = db.get(Project, project["id"])
    assert project_record is not None
    before_creation = await client.get(
        f"/api/v2/projects?cutoff_at={project_record.created_at - 1}"
    )
    exact_creation = await client.get(f"/api/v2/projects?cutoff_at={project_record.created_at}")
    after_creation = await client.get(f"/api/v2/projects?cutoff_at={project_record.created_at + 1}")
    assert before_creation.json() == []
    assert exact_creation.json() == []
    assert [item["id"] for item in after_creation.json()] == [project["id"]]
    exact_activation = await client.get(f"/api/v2/projects?cutoff_at={activation.activated_at}")
    after_activation = await client.get(f"/api/v2/projects?cutoff_at={activation.activated_at + 1}")
    assert exact_activation.json()[0]["activeVersionId"] is None
    assert after_activation.json()[0]["activeVersionId"] == version["id"]

    incompatible_version = _project_payload(_technical, _levels, _definition)
    incompatible_version["targets"] = []
    incompatible = await client.post(
        f"/api/v2/projects/{project['id']}/versions",
        json=incompatible_version,
        headers={"X-CSRF-Token": csrf},
    )
    assert incompatible.status_code == 422, incompatible.text
    assert incompatible.json()["error"]["code"] == "PROJECT_TASK_IDENTITY_INCOMPATIBLE"

    catalog = await client.get("/api/v2/projects/catalog/current")
    assert catalog.status_code == 200, catalog.text
    candidates = {item["task_stable_key"]: item for item in catalog.json()["candidates"]}
    assert candidates["build"]["candidate_usability_state"] == "unknown"
    assert candidates["build"]["target_facts"][0]["semantic_definition_id"] == _definition["id"]
    assert candidates["release"]["readiness_state"] in {"unknown", "not_met"}

    malformed_event = await client.post(
        f"/api/v2/projects/{project['id']}/events",
        json={
            "event_type": "project_lifecycle",
            "project_lifecycle_state": "active",
            "blocker_actionable": False,
            "source": "test",
            "idempotency_key": "malformed-lifecycle-event",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert malformed_event.status_code == 422, malformed_event.text

    blocker = await client.post(
        f"/api/v2/projects/{project['id']}/events",
        json={
            "event_type": "blocker_opened",
            "task_identity_id": tasks["build"]["identityId"],
            "blocker_key": "missing-sdk",
            "blocker_actionable": True,
            "details": {"action": "install SDK"},
            "source": "test",
            "idempotency_key": "open-blocker",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert blocker.status_code == 201, blocker.text
    blocked = await client.get(f"/api/v2/projects/tasks/{tasks['build']['id']}/availability")
    assert blocked.json()["candidate_usability_state"] == "not_met"
    assert blocked.json()["actionable_blocker_keys"] == ["missing-sdk"]
    assert blocked.json()["blocker_facts"][0]["details"] == {"action": "install SDK"}
    resolved = await client.post(
        f"/api/v2/projects/{project['id']}/events",
        json={
            "event_type": "blocker_resolved",
            "task_identity_id": tasks["build"]["identityId"],
            "blocker_key": "missing-sdk",
            "source": "test",
            "idempotency_key": "resolve-blocker",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resolved.status_code == 201, resolved.text

    for state, key in (("started", "build-started"), ("completed", "build-completed")):
        response = await client.post(
            f"/api/v2/projects/{project['id']}/events",
            json={
                "event_type": "task_lifecycle",
                "task_identity_id": tasks["build"]["identityId"],
                "task_lifecycle_state": state,
                "source": "test",
                "idempotency_key": key,
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 201, response.text
    assert db.scalar(select(func.count()).select_from(CompetencyCapabilityState)) == 0
    assert db.scalar(select(func.count()).select_from(ProjectCriterionEvaluation)) == 0
    assert db.scalar(select(func.count()).select_from(Activity)) == 0
    assert db.scalar(select(func.count()).select_from(LearningSession)) == 0
    assert db.scalar(select(func.count()).select_from(Evidence)) == 0

    activity = await client.post(
        "/api/v2/activities",
        json={
            "title": "Implemented and tested service",
            "category_stable_key": "project",
            "occurred_at": "2026-01-03T10:00:00Z",
            "outcome_classification": "completed",
        },
        headers={"X-CSRF-Token": csrf},
    )
    link = await client.post(
        "/api/v2/projects/activity-links",
        json={
            "activity_id": activity.json()["id"],
            "task_definition_id": tasks["build"]["id"],
            "provenance": "user_confirmed",
            "idempotency_key": "link-build-activity",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert link.status_code == 201, link.text
    duplicate_link = await client.post(
        "/api/v2/projects/activity-links",
        json={
            "activity_id": activity.json()["id"],
            "task_definition_id": tasks["build"]["id"],
            "provenance": "user_selected",
            "idempotency_key": "duplicate-build-activity-link",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert duplicate_link.status_code == 409, duplicate_link.text
    session = await client.post(
        "/api/v2/sessions/manual",
        json={
            "activity_id": activity.json()["id"],
            "assistance_mode": "none",
            "started_at": "2026-01-03T10:00:00Z",
            "duration_ms": 600_000,
            "outcome": "completed",
            "contributions": [
                {
                    "target_type": "competency",
                    "competency_identity_id": competency["id"],
                    "relevance": "primary",
                },
                {
                    "target_type": "project",
                    "project_id": project["id"],
                    "project_version_id": version["id"],
                    "project_task_definition_id": tasks["build"]["id"],
                    "relevance": "primary",
                },
            ],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert session.status_code == 201, session.text
    stored_session = db.get(LearningSession, session.json()["id"])
    assert stored_session is not None
    assert db.scalar(select(func.count()).select_from(SessionContribution)) == 1
    assert db.scalar(select(func.count()).select_from(SessionProjectContribution)) == 1
    project_contribution = db.scalar(select(SessionProjectContribution))
    assert project_contribution is not None
    assert project_duration_context_ms(db, str(project["id"]), project_contribution.created_at) == 0
    assert (
        project_duration_context_ms(db, str(project["id"]), project_contribution.created_at + 1)
        == 600_000
    )
    assert project_duration_context_ms(db, str(project["id"]), 2_000_000_000_000) == 600_000

    later_session = await client.post(
        "/api/v2/sessions/manual",
        json={
            "activity_id": activity.json()["id"],
            "assistance_mode": "agent_led",
            "started_at": "2026-01-03T10:10:00Z",
            "duration_ms": 600_000,
            "outcome": "completed",
            "contributions": [],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert later_session.status_code == 201, later_session.text

    duplicate_contribution = await client.post(
        f"/api/v2/projects/{project['id']}/session-contributions",
        json={
            "session_id": session.json()["id"],
            "project_version_id": version["id"],
            "task_definition_id": tasks["build"]["id"],
            "relevance": "secondary",
            "provenance": "user_confirmed",
            "idempotency_key": "duplicate-project-contribution",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert duplicate_contribution.status_code == 409, duplicate_contribution.text

    client_assigned_characteristics = await client.post(
        f"/api/v2/projects/{project['id']}/evidence",
        json={
            "activity_project_task_link_id": link.json()["id"],
            "opportunity_id": opportunity["id"],
            "title": "Client-assigned evidence",
            "occurred_at": "2026-01-03T10:10:00Z",
            "artifact_hash": "b" * 64,
            "strength": "strong",
            "idempotency_key": "client-assigned-evidence",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert client_assigned_characteristics.status_code == 422

    missing_artifact = await client.post(
        f"/api/v2/projects/{project['id']}/evidence",
        json={
            "activity_project_task_link_id": link.json()["id"],
            "opportunity_id": opportunity["id"],
            "title": "Missing required artifact",
            "occurred_at": "2026-01-03T10:10:00Z",
            "rubric_result": "passed",
            "idempotency_key": "missing-project-artifact",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert missing_artifact.status_code == 422, missing_artifact.text
    assert missing_artifact.json()["error"]["code"] == "PROJECT_EVIDENCE_ARTIFACT_REQUIRED"

    evidence = await client.post(
        f"/api/v2/projects/{project['id']}/evidence",
        json={
            "activity_project_task_link_id": link.json()["id"],
            "opportunity_id": opportunity["id"],
            "title": "Passing test run",
            "description": "All acceptance tests passed",
            "occurred_at": "2026-01-03T10:10:00Z",
            "artifact_hash": "a" * 64,
            "rubric_result": "passed",
            "idempotency_key": "project-test-evidence",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert evidence.status_code == 201, evidence.text
    evidence_row = db.get(Evidence, evidence.json()["id"])
    assert evidence_row is not None and evidence_row.evidence_type == "project"
    assert evidence_row.strength == "strong"
    assert evidence_row.independence == "independent"
    assert evidence_row.source_confidence == "medium"
    second_activity = await client.post(
        "/api/v2/activities",
        json={
            "title": "Different actual Project work",
            "category_stable_key": "project",
            "occurred_at": "2026-01-03T11:00:00Z",
            "outcome_classification": "completed",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert second_activity.status_code == 201, second_activity.text
    second_link = await client.post(
        "/api/v2/projects/activity-links",
        json={
            "activity_id": second_activity.json()["id"],
            "task_definition_id": tasks["build"]["id"],
            "provenance": "user_confirmed",
            "idempotency_key": "second-evidence-source-link",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert second_link.status_code == 201, second_link.text
    cross_source_reuse = await client.post(
        f"/api/v2/projects/{project['id']}/evidence",
        json={
            "activity_project_task_link_id": second_link.json()["id"],
            "opportunity_id": opportunity["id"],
            "title": "Passing test run",
            "description": "All acceptance tests passed",
            "occurred_at": "2026-01-03T11:00:00Z",
            "artifact_hash": "a" * 64,
            "rubric_result": "passed",
            "idempotency_key": "project-test-evidence",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert cross_source_reuse.status_code == 409, cross_source_reuse.text
    failed_session = await client.post(
        "/api/v2/sessions/manual",
        json={
            "activity_id": second_activity.json()["id"],
            "assistance_mode": "none",
            "started_at": "2026-01-03T11:00:00Z",
            "duration_ms": 300_000,
            "outcome": "completed",
            "contributions": [],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert failed_session.status_code == 201, failed_session.text
    failed_evidence = await client.post(
        f"/api/v2/projects/{project['id']}/evidence",
        json={
            "activity_project_task_link_id": second_link.json()["id"],
            "opportunity_id": opportunity["id"],
            "title": "Failing rubric result",
            "occurred_at": "2026-01-03T11:05:00Z",
            "artifact_hash": "c" * 64,
            "rubric_result": "not_met",
            "idempotency_key": "failed-project-evidence",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert failed_evidence.status_code == 201, failed_evidence.text
    failed_row = db.get(Evidence, failed_evidence.json()["id"])
    assert failed_row is not None and failed_row.strength == "weak"
    failed_links = db.scalars(
        select(EvidenceLink).where(EvidenceLink.evidence_id == failed_row.id)
    ).all()
    assert failed_links and {item.effect for item in failed_links} == {"contradicts"}
    failed_evaluation = await client.post(
        f"/api/v2/projects/criteria/{criterion['id']}/evaluations",
        json={
            "evidence_ids": [failed_evidence.json()["id"]],
            "idempotency_key": "evaluate-failed-project-criterion",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert failed_evaluation.status_code == 201, failed_evaluation.text
    assert failed_evaluation.json()["state"] == "not_demonstrated"
    assert db.scalar(select(func.count()).select_from(CompetencyCapabilityState)) == 0
    evaluated = await client.post(
        f"/api/v2/projects/criteria/{criterion['id']}/evaluations",
        json={
            "evidence_ids": [evidence.json()["id"]],
            "idempotency_key": "evaluate-project-criterion",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert evaluated.status_code == 201, evaluated.text
    assert evaluated.json()["state"] == "demonstrated"
    assert evaluated.json()["facts"]["policyVersion"] == "project-criterion-policy/v1"
    release = await client.get(f"/api/v2/projects/tasks/{tasks['release']['id']}/availability")
    assert release.json()["readiness_state"] == "met"
    assert release.json()["candidate_usability_state"] == "met"
    assert db.scalar(select(func.count()).select_from(CompetencyCapabilityState)) == 0

    corrected_source = await client.post(
        f"/api/v2/projects/activity-links/{link.json()['id']}/correct",
        json={
            "replacement_link_id": None,
            "reason": "Actual attribution was incorrect",
            "idempotency_key": "remove-evidence-source-link",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert corrected_source.status_code == 201, corrected_source.text
    assert db.scalar(select(func.count()).select_from(EvidenceInvalidation)) == 1
    no_longer_ready = await client.get(
        f"/api/v2/projects/tasks/{tasks['release']['id']}/availability"
    )
    assert no_longer_ready.json()["readiness_state"] == "unknown"

    duration_update = await client.patch(
        f"/api/v1/sessions/{session.json()['id']}",
        json={"duration_ms": 900_000},
        headers={"X-CSRF-Token": csrf},
    )
    assert duration_update.status_code == 200, duration_update.text
    correction = db.scalars(
        select(SessionCorrection).order_by(SessionCorrection.corrected_at.desc())
    ).first()
    assert correction is not None
    assert project_duration_context_ms(db, str(project["id"]), correction.corrected_at) == 600_000
    assert (
        project_duration_context_ms(db, str(project["id"]), correction.corrected_at + 1) == 900_000
    )
    deleted = await client.delete(
        f"/api/v1/sessions/{session.json()['id']}?confirm=true",
        headers={"X-CSRF-Token": csrf},
    )
    assert deleted.status_code == 200, deleted.text
    tombstone = db.get(LearningSession, session.json()["id"])
    assert tombstone is not None and tombstone.tombstoned_at is not None
    assert project_duration_context_ms(db, str(project["id"]), tombstone.tombstoned_at) == 900_000
    assert project_duration_context_ms(db, str(project["id"]), tombstone.tombstoned_at + 1) == 0
    second_project = await client.post(
        "/api/v2/projects",
        json={"stable_key": "unreferenced-project", "creation_source": "test"},
        headers={"X-CSRF-Token": csrf},
    )
    assert second_project.status_code == 201, second_project.text
    selective = await client.post(
        "/api/v1/import-export/export",
        json={
            "purpose": "portable_logical_backup",
            "format": "json",
            "project_ids": [second_project.json()["id"]],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert selective.status_code == 200, selective.text
    scope = selective.json()["content"]["payload"]["portableScope"]
    assert scope["requestedProjectIds"] == [second_project.json()["id"]]
    assert project["id"] in scope["closureAddedProjectIds"]
    assert set(scope["includedProjectIds"]) == {project["id"], second_project.json()["id"]}
    _validate_portable_payload(
        selective.json()["content"]["payload"],
        "selective-project-evidence",
        schema_version=4,
    )
    tampered_actuality = deepcopy(selective.json()["content"]["payload"])
    project_evidence = next(
        item
        for item in tampered_actuality["tables"]["evidence"]
        if item["source_type"] == "activity_project_task_link"
    )
    provenance = json.loads(project_evidence["provenance_json"])
    provenance["derived_characteristics"]["activityOutcome"] = "partial"
    project_evidence["provenance_json"] = json.dumps(
        provenance, sort_keys=True, separators=(",", ":")
    )
    project_evidence["strength"] = "moderate"
    with pytest.raises(AppError, match="derived characteristics"):
        _validate_portable_payload(
            tampered_actuality, "tampered-project-actuality", schema_version=4
        )
    malformed_characteristics = deepcopy(selective.json()["content"]["payload"])
    malformed_evidence = next(
        item
        for item in malformed_characteristics["tables"]["evidence"]
        if item["source_type"] == "activity_project_task_link"
    )
    malformed_provenance = json.loads(malformed_evidence["provenance_json"])
    malformed_provenance["derived_characteristics"] = []
    malformed_evidence["provenance_json"] = json.dumps(
        malformed_provenance, sort_keys=True, separators=(",", ":")
    )
    with pytest.raises(AppError, match="derived characteristics"):
        _validate_portable_payload(
            malformed_characteristics, "malformed-project-characteristics", schema_version=4
        )
    project_row = db.get(Project, project["id"])
    assert project_row is not None
    project_row.stable_key = "mutated-project-key"
    with pytest.raises(ValueError, match="immutable"):
        db.commit()
    db.rollback()
    task_identity = db.get(ProjectTaskIdentity, tasks["build"]["identityId"])
    assert task_identity is not None
    task_identity.stable_key = "mutated-task-key"
    with pytest.raises(ValueError, match="immutable"):
        db.commit()
    db.rollback()
    project_row = db.get(Project, project["id"])
    assert project_row is not None
    db.delete(project_row)
    with pytest.raises(ValueError, match="immutable"):
        db.commit()
    db.rollback()


async def test_project_cutoff_corrections_idempotency_and_portable_v4(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    project, version, _competency, technical, levels, definition = await _create_active_project(
        client, csrf
    )
    tasks = {item["stableKey"]: item for item in version["tasks"]}
    event = await client.post(
        f"/api/v2/projects/{project['id']}/events",
        json={
            "event_type": "task_lifecycle",
            "task_identity_id": tasks["build"]["identityId"],
            "task_lifecycle_state": "started",
            "source": "test",
            "idempotency_key": "cutoff-task-started",
        },
        headers={"X-CSRF-Token": csrf},
    )
    repeated = await client.post(
        f"/api/v2/projects/{project['id']}/events",
        json={
            "event_type": "task_lifecycle",
            "task_identity_id": tasks["build"]["identityId"],
            "task_lifecycle_state": "started",
            "source": "test",
            "idempotency_key": "cutoff-task-started",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert repeated.status_code == 201 and repeated.json()["id"] == event.json()["id"]
    event_row = db.get(ProjectEvent, event.json()["id"])
    assert event_row is not None
    exact = build_catalog(db, event_row.occurred_at)
    after = build_catalog(db, event_row.occurred_at + 1)
    assert (
        next(item for item in exact.candidates if item.task_stable_key == "build").lifecycle_state
        == "not_started"
    )
    assert (
        next(item for item in after.candidates if item.task_stable_key == "build").lifecycle_state
        == "started"
    )
    blocker = await client.post(
        f"/api/v2/projects/{project['id']}/events",
        json={
            "event_type": "blocker_opened",
            "task_identity_id": tasks["build"]["identityId"],
            "blocker_key": "unstable-tooling",
            "blocker_actionable": False,
            "details": {"observed": "tooling failure"},
            "source": "test",
            "idempotency_key": "open-correctable-blocker",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert blocker.status_code == 201, blocker.text
    blocker_correction_command = {
        "corrects_event_id": blocker.json()["id"],
        "blocker_key": "unstable-tooling",
        "actionable": True,
        "details": {"action": "pin tool version"},
        "source": "test",
        "idempotency_key": "correct-blocker",
    }
    blocker_correction = await client.post(
        f"/api/v2/projects/{project['id']}/blockers/correct",
        json=blocker_correction_command,
        headers={"X-CSRF-Token": csrf},
    )
    assert blocker_correction.status_code == 201, blocker_correction.text
    blocker_correction_retry = await client.post(
        f"/api/v2/projects/{project['id']}/blockers/correct",
        json=blocker_correction_command,
        headers={"X-CSRF-Token": csrf},
    )
    assert blocker_correction_retry.status_code == 201, blocker_correction_retry.text
    assert blocker_correction_retry.json()["id"] == blocker_correction.json()["id"]
    opened_event = db.get(ProjectEvent, blocker.json()["id"])
    corrected_event = db.get(ProjectEvent, blocker_correction.json()["id"])
    assert opened_event is not None and corrected_event is not None
    corrected_catalog = build_catalog(db, corrected_event.occurred_at + 1)
    corrected_fact = next(
        item for item in corrected_catalog.candidates if item.task_stable_key == "build"
    ).blocker_facts[0]
    assert corrected_fact.opened_at == opened_event.occurred_at
    assert corrected_fact.corrected_at == corrected_event.occurred_at

    activity = await client.post(
        "/api/v2/activities",
        json={
            "title": "Actual project work",
            "category_stable_key": "project",
            "occurred_at": "2026-01-04T10:00:00Z",
        },
        headers={"X-CSRF-Token": csrf},
    )
    first = await client.post(
        "/api/v2/projects/activity-links",
        json={
            "activity_id": activity.json()["id"],
            "task_definition_id": tasks["build"]["id"],
            "provenance": "user_selected",
            "idempotency_key": "project-link-one",
        },
        headers={"X-CSRF-Token": csrf},
    )
    second = await client.post(
        "/api/v2/projects/activity-links",
        json={
            "activity_id": activity.json()["id"],
            "task_definition_id": tasks["release"]["id"],
            "provenance": "user_confirmed",
            "idempotency_key": "project-link-two",
        },
        headers={"X-CSRF-Token": csrf},
    )
    missing_replacement = await client.post(
        f"/api/v2/projects/activity-links/{first.json()['id']}/correct",
        json={
            "replacement_link_id": "00000000-0000-0000-0000-000000000000",
            "reason": "Missing replacement must not become a tombstone",
            "idempotency_key": "missing-project-link-replacement",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert missing_replacement.status_code == 422, missing_replacement.text
    corrected = await client.post(
        f"/api/v2/projects/activity-links/{first.json()['id']}/correct",
        json={
            "replacement_link_id": second.json()["id"],
            "reason": "Correct task",
            "idempotency_key": "correct-project-link",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert corrected.status_code == 201, corrected.text
    assert db.scalar(select(func.count()).select_from(ActivityProjectTaskLink)) == 2
    assert db.scalar(select(func.count()).select_from(ActivityProjectTaskLinkCorrection)) == 1

    replacement_project = await client.post(
        "/api/v2/projects",
        json={"stable_key": "replacement-project", "creation_source": "test"},
        headers={"X-CSRF-Token": csrf},
    )
    assert replacement_project.status_code == 201, replacement_project.text
    replacement_version = await client.post(
        f"/api/v2/projects/{replacement_project.json()['id']}/versions",
        json=_project_payload(technical, levels, definition),
        headers={"X-CSRF-Token": csrf},
    )
    assert replacement_version.status_code == 201, replacement_version.text
    replacement_activation = await client.post(
        f"/api/v2/projects/{replacement_project.json()['id']}/versions/"
        f"{replacement_version.json()['id']}/activate",
        json={
            "reason": "Cross-Project replacement fixture",
            "source": "test",
            "idempotency_key": "activate-replacement-project",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert replacement_activation.status_code == 200, replacement_activation.text
    replacement_task = next(
        item for item in replacement_version.json()["tasks"] if item["stableKey"] == "build"
    )
    cross_project_link = await client.post(
        "/api/v2/projects/activity-links",
        json={
            "activity_id": activity.json()["id"],
            "task_definition_id": replacement_task["id"],
            "provenance": "user_confirmed",
            "idempotency_key": "cross-project-link",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert cross_project_link.status_code == 201, cross_project_link.text
    chained_correction = await client.post(
        f"/api/v2/projects/activity-links/{second.json()['id']}/correct",
        json={
            "replacement_link_id": cross_project_link.json()["id"],
            "reason": "Correct Project as well as task",
            "idempotency_key": "cross-project-correction",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert chained_correction.status_code == 201, chained_correction.text

    selective = await client.post(
        "/api/v1/import-export/export",
        json={
            "purpose": "portable_logical_backup",
            "format": "json",
            "project_ids": [project["id"]],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert selective.status_code == 200, selective.text
    selective_package = selective.json()["content"]
    selective_scope = selective_package["payload"]["portableScope"]
    assert replacement_project.json()["id"] in selective_scope["closureAddedProjectIds"]
    _validate_portable_payload(
        selective_package["payload"], "cross-project-closure", schema_version=4
    )

    exported = await client.post(
        "/api/v1/import-export/export",
        json={"purpose": "portable_logical_backup", "format": "json"},
        headers={"X-CSRF-Token": csrf},
    )
    assert exported.status_code == 200, exported.text
    package = exported.json()["content"]
    assert package["schemaVersion"] == 4
    checkpoint = package["payload"]["projectCatalogCheckpoint"]
    assert checkpoint["cutoffSemantics"] == "exclusive"
    assert checkpoint["policyVersion"] == "project-availability-policy/v1"
    assert len(checkpoint["candidateHashes"]) == 4

    unsupported_policy = deepcopy(package["payload"])
    criterion_row = unsupported_policy["tables"]["project_criterion_definitions"][0]
    version_row = next(
        item
        for item in unsupported_policy["tables"]["project_versions"]
        if item["id"] == criterion_row["project_version_id"]
    )
    definition_envelope = json.loads(version_row["definition_payload_json"])
    definition_envelope["criteria"][0]["evaluation_policy_version"] = (
        "project-criterion-policy/unsupported"
    )
    version_row["definition_payload_json"] = json.dumps(
        definition_envelope, sort_keys=True, separators=(",", ":")
    )
    version_row["content_hash"] = content_hash(definition_envelope)
    criterion_row["evaluation_policy_version"] = "project-criterion-policy/unsupported"
    with pytest.raises(AppError, match="unsupported evaluation policy"):
        _validate_portable_payload(
            unsupported_policy, "unsupported-project-criterion-policy", schema_version=4
        )

    reversed_self_references = deepcopy(package["payload"])
    reversed_self_references["tables"]["project_events"].reverse()
    _validate_portable_payload(
        reversed_self_references, "reversed-project-self-references", schema_version=4
    )

    tampered_definition = deepcopy(package["payload"])
    tampered_definition["tables"]["project_task_definitions"][0]["title"] = "Tampered"
    with pytest.raises(AppError, match="definition envelope"):
        _validate_portable_payload(
            tampered_definition, "tampered-project-definition", schema_version=4
        )

    tampered = deepcopy(package["payload"])
    tampered["projectCatalogCheckpoint"]["catalogHash"] = "0" * 64
    body = tampered["projectCatalogCheckpoint"]
    body["inputHash"] = content_hash(
        {key: value for key, value in body.items() if key != "inputHash"}
    )
    before = deepcopy(_portable_payload(db)["tables"])
    with pytest.raises(AppError, match="rebuilt Project catalog"):
        _apply_portable_restore(db, tampered, True, package_id="tampered-project", schema_version=4)
    db.rollback()
    db.expire_all()
    assert _portable_payload(db)["tables"] == before

    preview = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "project-v4.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )
    assert preview.status_code == 200, preview.text
    restored = await client.post(
        "/api/v1/import-export/import/apply",
        json={
            "filename": "project-v4.json",
            "package": package,
            "confirmation_token": preview.json()["confirmationToken"],
            "replace_existing": True,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert restored.status_code == 200, restored.text
    assert db.scalar(select(func.count()).select_from(Project)) == 2
    assert db.scalar(select(func.count()).select_from(ProjectVersion)) == 2


async def test_project_contribution_command_and_retraction_cutoffs(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    project, version, _competency, _technical, _levels, _definition = await _create_active_project(
        client, csrf
    )
    task = next(item for item in version["tasks"] if item["stableKey"] == "build")
    activity = await client.post(
        "/api/v2/activities",
        json={
            "title": "Contribution command fixture",
            "category_stable_key": "project",
            "occurred_at": "2026-01-05T10:00:00Z",
            "outcome_classification": "completed",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert activity.status_code == 201, activity.text
    session = await client.post(
        "/api/v2/sessions/manual",
        json={
            "activity_id": activity.json()["id"],
            "assistance_mode": "none",
            "started_at": "2026-01-05T10:00:00Z",
            "duration_ms": 600_000,
            "outcome": "completed",
            "contributions": [],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert session.status_code == 201, session.text
    command = {
        "session_id": session.json()["id"],
        "project_version_id": version["id"],
        "task_definition_id": task["id"],
        "relevance": "secondary",
        "provenance": "user_confirmed",
        "idempotency_key": "project-contribution-command",
    }
    created = await client.post(
        f"/api/v2/projects/{project['id']}/session-contributions",
        json=command,
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    repeated = await client.post(
        f"/api/v2/projects/{project['id']}/session-contributions",
        json=command,
        headers={"X-CSRF-Token": csrf},
    )
    assert repeated.status_code == 201
    assert repeated.json()["id"] == created.json()["id"]
    conflict = await client.post(
        f"/api/v2/projects/{project['id']}/session-contributions",
        json={**command, "relevance": "supporting"},
        headers={"X-CSRF-Token": csrf},
    )
    assert conflict.status_code == 409, conflict.text
    contribution = db.get(SessionProjectContribution, created.json()["id"])
    assert contribution is not None
    assert project_duration_context_ms(db, str(project["id"]), contribution.created_at) == 0
    assert (
        project_duration_context_ms(db, str(project["id"]), contribution.created_at + 1) == 600_000
    )
    retracted = await client.delete(
        f"/api/v2/sessions/{session.json()['id']}/contributions/{contribution.id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert retracted.status_code == 200, retracted.text
    retraction = db.scalar(
        select(SessionProjectContributionRetraction).where(
            SessionProjectContributionRetraction.contribution_id == contribution.id
        )
    )
    assert retraction is not None
    assert project_duration_context_ms(db, str(project["id"]), retraction.retracted_at) == 600_000
    assert project_duration_context_ms(db, str(project["id"]), retraction.retracted_at + 1) == 0


def test_v3_to_v4_adapter_is_empty_and_project_dependencies_are_public(db: Session) -> None:
    payload = deepcopy(_portable_payload(db))
    payload["manifest"] = PORTABLE_V3_MANIFEST
    payload.pop("projectCatalogCheckpoint")
    payload.pop("portableScope")
    for table_name in PORTABLE_V4_PROJECT_TABLES:
        payload["tables"].pop(table_name)
    first, summary = _validate_portable_payload(payload, "v3-project-adapter", schema_version=3)
    second, _ = _validate_portable_payload(payload, "v3-project-adapter", schema_version=3)
    assert first == second
    assert summary["compatibilityConversions"]["nativeProjectsInferred"] == 0
    assert all(first[name] == [] for name in PORTABLE_V4_PROJECT_TABLES)

    service = (Path(__file__).parents[1] / "app" / "projects" / "service.py").read_text()
    tree = ast.parse(service)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "app.models"
        for alias in node.names
    }
    assert "CompetencyCapabilityState" not in imports
    assert "CapabilityEvaluationRun" not in imports
    assert "from app.capability_views import" in service
    assert "from app.activity_views import" in service


def test_no_v1_project_backfill(db: Session) -> None:
    assert db.scalar(select(func.count()).select_from(Project)) == 0


def _migration_config(database_path: Path) -> Config:
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    url = f"sqlite:///{database_path}"
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["database_url"] = url
    return config


def test_populated_0011_upgrade_invents_no_projects_and_preserves_existing_facts(
    tmp_path: Path,
) -> None:
    source = Path(__file__).parent / "fixtures" / "v2" / "populated-0010.sqlite3"
    database_path = tmp_path / "populated-0011.sqlite3"
    shutil.copyfile(source, database_path)
    config = _migration_config(database_path)
    command.upgrade(config, "0011_curriculum_core")
    connection = sqlite3.connect(database_path)
    connection.execute(
        "INSERT INTO curricula "
        "(id, stable_key, created_at, creation_source, retired_at, retirement_reason) "
        "VALUES ('curriculum-0011-fixture', 'data-bearing-0011', 1767225600000, "
        "'migration-fixture', NULL, NULL)"
    )
    connection.execute(
        "INSERT INTO curriculum_objective_identities "
        "(id, curriculum_id, stable_key, created_at) VALUES "
        "('objective-0011-fixture', 'curriculum-0011-fixture', 'preserved-objective', "
        "1767225600000)"
    )
    connection.commit()
    preserved_tables = (
        "roadmaps",
        "target_profiles",
        "semantic_competency_definitions",
        "activities",
        "learning_sessions",
        "evidence",
        "capability_evaluation_runs",
        "curricula",
        "curriculum_objective_identities",
    )
    before = {
        table: connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
        for table in preserved_tables
    }
    connection.close()

    command.upgrade(config, "0012_project_core")
    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0012_project_core",
        )
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM project_events").fetchone() == (0,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert before == {
            table: connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            for table in preserved_tables
        }
    finally:
        connection.close()
    command.downgrade(config, "0011_curriculum_core")
    command.upgrade(config, "0012_project_core")


def test_populated_project_downgrade_fails_closed(tmp_path: Path) -> None:
    source = Path(__file__).parent / "fixtures" / "v2" / "populated-0010.sqlite3"
    database_path = tmp_path / "populated-project.sqlite3"
    shutil.copyfile(source, database_path)
    config = _migration_config(database_path)
    command.upgrade(config, "0012_project_core")
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "INSERT INTO projects (id, stable_key, created_at, creation_source) "
            "VALUES ('project-fixture', 'fixture', 1, 'test')"
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(RuntimeError, match="Refusing to downgrade populated canonical Project"):
        command.downgrade(config, "0011_curriculum_core")
    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0012_project_core",
        )
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone() == (1,)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "partial_sql",
    [
        "CREATE TABLE projects (id TEXT PRIMARY KEY)",
        "CREATE TABLE _alembic_tmp_project_events (id TEXT PRIMARY KEY)",
    ],
)
def test_partial_project_migration_is_refused(tmp_path: Path, partial_sql: str) -> None:
    source = Path(__file__).parent / "fixtures" / "v2" / "populated-0010.sqlite3"
    database_path = tmp_path / "partial-project.sqlite3"
    shutil.copyfile(source, database_path)
    config = _migration_config(database_path)
    command.upgrade(config, "0011_curriculum_core")
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(partial_sql)
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(RuntimeError, match="ambiguously partial Project migration"):
        database.run_migrations(f"sqlite:///{database_path}")
