from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from time import sleep

import pytest
from alembic import command as alembic_command
from alembic.config import Config
from app.analysis.v3.service import run_analysis
from app.assessment.contracts import AssessmentObservationInput
from app.assessment.models import AssessmentArtifact, AssessmentExecution, AssessmentReview
from app.assessment.service import assessment_task_options, derive_review_evidence_policy
from app.authority.models import LearningControlAuthorityState
from app.curriculum.models import (
    AssessmentRubricDefinition,
    AssessmentRubricIdentity,
    LearningUnitDefinition,
)
from app.errors import AppError
from app.import_export import _apply_portable_restore, _portable_payload, _validate_portable_payload
from app.models import (
    Activity,
    AnalysisSnapshot,
    CompetencyCapabilityState,
    Evidence,
    EvidenceLink,
    LearningSession,
    MasterImportRevision,
)
from app.portability.registry import (
    PORTABLE_V10_MANIFEST,
    PORTABLE_V11_ASSESSMENT_TABLES,
    PORTABLE_V11_MANIFEST,
)
from app.recommendation.v2.models import RecommendationV2Run
from app.recommendation.v2.public import load_public_recommendation_item
from app.recommendation.v2.service import generate_recommendations
from app.settings_api import get_or_create_profile
from app.time_utils import utc_now_ms
from app.today.models import TodayGeneration, TodaySuggestion
from app.today.service import (
    _append_interaction,
    _create_relation,
    _start_command_facts,
    current_today,
    generate_today,
)
from app.v2_activities import create_activity_in_uow, start_timed_session_in_uow
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from test_master_import import _submit_master, package

LINUX_KEYS = (
    "trace-pipeline-status",
    "select-compose-files",
    "repair-permission-boundary",
    "resolve-execution-environment",
    "control-process-lifecycle",
    "compose-fresh-shell-task",
)
FIXTURE_TRANSCRIPT = (
    "Explicit test fixture transcript: quoted selection, counts, stderr, "
    "status=0, pipefail status=1."
)


def linux_assessment_fixture(*, requires_artifact: bool = True) -> dict:
    value = deepcopy(package())
    value["packageId"] = "linux-assessment-explicit-fixture"
    payload = value["payload"]
    competency = payload["competencies"][0]
    competency["stableKey"] = "mi.linux.shell"
    competency["title"] = "Linux shell and processes"
    competency["criteria"] = [
        {
            "stableKey": key,
            "levelKey": "familiar" if index == 0 else "guided" if index == 1 else "independent",
            "requirementType": "required",
            "demonstrationRule": "guided_performance" if index < 2 else "independent_performance",
            "description": "Observe actual shell performance for " + key,
        }
        for index, key in enumerate(LINUX_KEYS)
    ]
    target = payload["targetProfile"]["targets"][0]
    target["competencyRef"] = "mi.linux.shell"
    target["levelKey"] = "independent"
    unit = payload["curriculum"]["units"][0]
    unit["stableKey"] = "mi.unit.trace-quoted-pipeline"
    unit["title"] = "Trace a shell pipeline and status"
    unit["action"] = {
        "kind": "exercise",
        "instructions": (
            "In a temporary directory with files containing spaces and mismatched lines, "
            "run a quoted grep/find-style selection, a pipe, and redirection. Predict and "
            "observe stdin/stdout/stderr and count. Inject a failed upstream command; compare "
            "default Bash pipeline status and pipefail status. Explain each stage."
        ),
    }
    unit["targets"][0]["competencyRef"] = "mi.linux.shell"
    unit["targets"][0]["criterionRef"] = "mi.linux.shell::trace-pipeline-status"
    unit["targets"][0]["maximumLevelKey"] = "familiar"
    unit["evidenceOpportunities"] = [
        {
            "stableKey": "observed-artifact",
            "evidenceKind": "assessment",
            "intendedStrengths": ["moderate"],
            "intendedIndependenceModes": ["guided", "independent"],
            "requiresActualActivity": True,
            "requiresArtifact": requires_artifact,
            "orderIndex": 0,
        }
    ]
    payload["curriculum"]["assessmentRubrics"] = [
        {
            "stableKey": "mi.rubric.linux.shell",
            "title": "Linux shell and processes evidence rubric",
            "competencyRef": "mi.linux.shell",
            "instructions": "Assess each required criterion from attributable actual outputs.",
            "rubric": {
                "policyVersion": "master-rubric-v1",
                "criteria": [
                    {
                        "criterionRef": f"mi.linux.shell::{key}",
                        "description": "Inspect actual result for " + key,
                        "weight": (10, 12, 13, 20, 22, 23)[index],
                    }
                    for index, key in enumerate(LINUX_KEYS)
                ],
            },
        }
    ]
    payload["initialSpine"] = ["mi.unit.trace-quoted-pipeline"]
    return value


async def _seed_today(
    authenticated_client: tuple[AsyncClient, str],
    db: Session,
    master_package: dict | None = None,
) -> tuple[AsyncClient, str, TodaySuggestion]:
    client, csrf = authenticated_client
    get_or_create_profile(db)
    _, applied = await _submit_master(client, csrf, master_package or linux_assessment_fixture())
    assert applied.status_code == 200, applied.text
    snapshot = run_analysis(
        db,
        idempotency_key="assessment-fixture-analysis",
        purpose="learning_control",
        update_current=True,
    )
    db.commit()
    generate_recommendations(
        db,
        idempotency_key="assessment-fixture-recommendation",
        analysis_snapshot_id=snapshot.id,
        available_time_ms=None,
    )
    db.commit()
    generation = generate_today(
        db,
        idempotency_key="assessment-fixture-today",
        analysis_snapshot_id=snapshot.id,
        available_time_ms=None,
        context_costs=(),
        regenerate=False,
    )
    db.commit()
    suggestion = next(
        (
            item
            for item in db.scalars(
                select(TodaySuggestion).where(TodaySuggestion.generation_id == generation.id)
            ).all()
            if load_public_recommendation_item(
                db, run_id=item.recommendation_run_id, candidate_id=item.candidate_id
            ).candidate_type
            == "assessment"
        ),
        None,
    )
    assert suggestion is not None
    return client, csrf, suggestion


async def _start_and_complete(
    client: AsyncClient,
    csrf: str,
    db: Session,
    suggestion: TodaySuggestion,
    *,
    assistance: str = "none",
) -> tuple[AssessmentExecution, dict]:
    sleep(0.01)  # Keep disposable fixture events after the frozen Analysis cutoff.
    option = assessment_task_options(db, suggestion.id)[0]
    started = await client.post(
        f"/api/v2/today/suggestions/{suggestion.id}/start",
        json={
            "idempotency_key": "linux-start-for-review",
            "assistance_mode": assistance,
            "assessment_unit_definition_id": option["unitDefinitionId"],
            "assessment_opportunity_id": option["opportunityId"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert started.status_code == 201, started.text
    execution = db.scalar(
        select(AssessmentExecution).where(AssessmentExecution.today_suggestion_id == suggestion.id)
    )
    assert execution is not None
    complete = await client.post(
        f"/api/v2/sessions/{execution.session_id}/complete",
        json={"outcome": "completed"},
        headers={"X-CSRF-Token": csrf},
    )
    assert complete.status_code == 200, complete.text
    return execution, option


def _observation(
    criterion_id: str,
    *,
    state: str = "demonstrated",
    artifact: str | None = FIXTURE_TRANSCRIPT,
    additional: str | None = None,
    corroboration_id: str | None = None,
) -> dict:
    return {
        "criterion_definition_id": criterion_id,
        "state": state,
        "task_setup": "Disposable files with spaces and one failed upstream command.",
        "expected_result": "The normal pipeline shows downstream status; pipefail exposes failure.",
        "observed_output": "Fixture transcript records stage outputs, counts, stderr and statuses.",
        "comparison": "The observed statuses match the prior prediction and explain each stage.",
        "artifact_content": artifact,
        "corroboration": (
            {"kind": "server_stored_artifact", "artifact_id": corroboration_id}
            if corroboration_id
            else None
        ),
        "additional_assistance_mode": additional,
    }


def _review_request(
    criterion_id: str,
    *,
    state: str = "demonstrated",
    artifact: str | None = FIXTURE_TRANSCRIPT,
    additional: str | None = None,
    key: str = "review-first",
    corroboration_id: str | None = None,
) -> dict:
    return {
        "idempotency_key": key,
        "observations": [
            _observation(
                criterion_id,
                state=state,
                artifact=artifact,
                additional=additional,
                corroboration_id=corroboration_id,
            )
        ],
        "attestation": {
            "actual_session": True,
            "assistance_complete": True,
            "outputs_authentic": True,
            "review_truthful": True,
        },
    }


async def _upload_artifact(
    client: AsyncClient,
    csrf: str,
    execution: AssessmentExecution,
    criterion_id: str,
    *,
    key: str = "fixture-assessment-artifact",
    content: bytes = FIXTURE_TRANSCRIPT.encode(),
) -> str:
    uploaded = await client.post(
        f"/api/v2/assessment-executions/{execution.id}/artifacts",
        params={"criterion_definition_id": criterion_id, "filename": "task-output.txt"},
        content=content,
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": key,
            "Content-Type": "application/octet-stream",
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["sha256"] == hashlib.sha256(content).hexdigest()
    return uploaded.json()["id"]


async def test_linux_partial_assessment_start_and_review(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf, suggestion = await _seed_today(authenticated_client, db)
    historical_counts = tuple(
        db.scalar(select(func.count()).select_from(model))
        for model in (AnalysisSnapshot, RecommendationV2Run, TodayGeneration)
    )
    options = await client.get(f"/api/v2/today/suggestions/{suggestion.id}/assessment-options")
    assert options.status_code == 200, options.text
    items = options.json()["items"]
    assert len(items) == 1
    option = items[0]
    assert option["unitTitle"] == "Trace a shell pipeline and status"
    assert [row["criterionStableKey"] for row in option["criteria"]] == ["trace-pipeline-status"]
    assert option["intendedStrengths"] == ["moderate"]
    assert option["requiresArtifact"] is True
    start = await client.post(
        f"/api/v2/today/suggestions/{suggestion.id}/start",
        json={
            "idempotency_key": "linux-assessment-start",
            "assistance_mode": "docs_only",
            "contributions": [],
            "assessment_unit_definition_id": option["unitDefinitionId"],
            "assessment_opportunity_id": option["opportunityId"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert start.status_code == 201, start.text
    execution = db.scalar(select(AssessmentExecution))
    assert execution is not None
    assert execution.unit_definition_id == option["unitDefinitionId"]
    assert execution.opportunity_id == option["opportunityId"]
    assert execution.rubric_definition_id == option["rubricDefinitionId"]
    assert db.get(LearningSession, execution.session_id).assistance_mode == "docs_only"
    assert db.scalar(select(func.count()).select_from(Evidence)) == 0
    detail = await client.get(f"/api/v2/assessment-executions/{execution.id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["task"]["action"] == option["action"]

    criterion_id = option["criteria"][0]["criterionDefinitionId"]
    premature = await client.post(
        f"/api/v2/assessment-executions/{execution.id}/artifacts",
        params={"criterion_definition_id": criterion_id, "filename": "task-output.txt"},
        content=FIXTURE_TRANSCRIPT.encode(),
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": "premature-artifact",
            "Content-Type": "application/octet-stream",
        },
    )
    assert premature.status_code == 409
    assert db.scalar(select(func.count()).select_from(AssessmentArtifact)) == 0

    complete = await client.post(
        f"/api/v2/sessions/{execution.session_id}/complete",
        json={"outcome": "completed"},
        headers={"X-CSRF-Token": csrf},
    )
    assert complete.status_code == 200, complete.text
    assert db.scalar(select(func.count()).select_from(AssessmentReview)) == 0
    assert (
        db.scalar(select(func.count()).select_from(EvidenceLink)) == 1
    )  # Generic Session Evidence only.
    artifact_id = await _upload_artifact(client, csrf, execution, criterion_id)
    assert await _upload_artifact(client, csrf, execution, criterion_id) == artifact_id
    assert db.scalar(select(func.count()).select_from(AssessmentArtifact)) == 1
    review = await client.post(
        f"/api/v2/assessment-executions/{execution.id}/reviews",
        json={
            "idempotency_key": "linux-assessment-review",
            "observations": [
                {
                    "criterion_definition_id": criterion_id,
                    "state": "demonstrated",
                    "task_setup": "Disposable files with spaces and one failed upstream command.",
                    "expected_result": (
                        "The normal pipeline shows downstream status; pipefail exposes failure."
                    ),
                    "observed_output": (
                        "Fixture transcript records stage outputs, counts, stderr and statuses."
                    ),
                    "comparison": (
                        "The observed statuses match the prior prediction and explain each stage."
                    ),
                    "artifact_content": FIXTURE_TRANSCRIPT,
                    "corroboration": {"kind": "server_stored_artifact", "artifact_id": artifact_id},
                }
            ],
            "attestation": {
                "actual_session": True,
                "assistance_complete": True,
                "outputs_authentic": True,
                "review_truthful": True,
            },
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert review.status_code == 201, review.text
    result = review.json()
    assert result["snapshot"]["rows"][0]["derivedSourceConfidence"] == "medium"
    assert result["snapshot"]["rows"][0]["derivedStrength"] == "moderate"
    assert result["capability"]["levelKey"] != "independent"
    assert {item["criterionStableKey"] for item in result["criterionStates"]} == set(LINUX_KEYS)
    assert sum(item["state"] == "unknown" for item in result["criterionStates"]) == 5
    evidence_id = result["snapshot"]["rows"][0]["evidenceId"]
    links = db.scalars(select(EvidenceLink).where(EvidenceLink.evidence_id == evidence_id)).all()
    assert len(links) == 1 and links[0].criterion_definition_id == criterion_id
    capability = db.get(CompetencyCapabilityState, (execution.competency_identity_id, "overall"))
    assert capability is not None and capability.assessment_status != "independent"
    assert (
        tuple(
            db.scalar(select(func.count()).select_from(model))
            for model in (AnalysisSnapshot, RecommendationV2Run, TodayGeneration)
        )
        == historical_counts
    )
    assert result["analysisStatus"] == "stale"


async def test_assessment_start_requires_eligible_task_and_explicit_assistance(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf, suggestion = await _seed_today(authenticated_client, db)
    option = assessment_task_options(db, suggestion.id)[0]
    base = {
        "assessment_unit_definition_id": option["unitDefinitionId"],
        "assessment_opportunity_id": option["opportunityId"],
    }
    for index, mutation in enumerate(
        (
            {},
            {"assistance_mode": "none", "assessment_unit_definition_id": "missing-unit"},
            {"assistance_mode": "none", "assessment_opportunity_id": "missing-opportunity"},
        )
    ):
        response = await client.post(
            f"/api/v2/today/suggestions/{suggestion.id}/start",
            json={"idempotency_key": f"invalid-assessment-{index}", **base, **mutation},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 422, response.text
    assert db.scalar(select(func.count()).select_from(Activity)) == 0
    assert db.scalar(select(func.count()).select_from(LearningSession)) == 0
    assert db.scalar(select(func.count()).select_from(AssessmentExecution)) == 0


async def test_no_eligible_authored_task_prevents_start_without_mutation(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf, suggestion = await _seed_today(authenticated_client, db)
    option = assessment_task_options(db, suggestion.id)[0]
    db.execute(
        update(LearningUnitDefinition)
        .where(LearningUnitDefinition.id == option["unitDefinitionId"])
        .values(status="archived")
    )
    db.commit()
    found = await client.get(f"/api/v2/today/suggestions/{suggestion.id}/assessment-options")
    assert found.status_code == 200 and found.json()["items"] == []
    started = await client.post(
        f"/api/v2/today/suggestions/{suggestion.id}/start",
        json={
            "idempotency_key": "no-eligible-authored-task",
            "assistance_mode": "none",
            "assessment_unit_definition_id": option["unitDefinitionId"],
            "assessment_opportunity_id": option["opportunityId"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert started.status_code == 422
    assert db.scalar(select(func.count()).select_from(Activity)) == 0
    assert db.scalar(select(func.count()).select_from(LearningSession)) == 0
    assert db.scalar(select(func.count()).select_from(AssessmentExecution)) == 0


@pytest.mark.parametrize(
    "state,artifact,expected_strength,expected_confidence,expected_effect",
    [
        ("demonstrated", None, "weak", "low", "context_only"),
        (
            "partial",
            "Explicit fixture transcript containing setup and actual shell observations.",
            "weak",
            "medium",
            "context_only",
        ),
        (
            "contradicted",
            "Explicit fixture transcript containing setup and actual shell observations.",
            "moderate",
            "medium",
            "contradicts",
        ),
        ("unobserved", None, None, None, None),
    ],
)
async def test_review_states_and_artifact_policy(
    authenticated_client: tuple[AsyncClient, str],
    db: Session,
    state: str,
    artifact: str | None,
    expected_strength: str | None,
    expected_confidence: str | None,
    expected_effect: str | None,
) -> None:
    client, csrf, suggestion = await _seed_today(authenticated_client, db)
    execution, option = await _start_and_complete(client, csrf, db, suggestion)
    criterion_id = option["criteria"][0]["criterionDefinitionId"]
    artifact_id = (
        await _upload_artifact(client, csrf, execution, criterion_id)
        if artifact is not None and state != "unobserved"
        else None
    )
    result = await client.post(
        f"/api/v2/assessment-executions/{execution.id}/reviews",
        json=_review_request(
            criterion_id, state=state, artifact=artifact, corroboration_id=artifact_id
        ),
        headers={"X-CSRF-Token": csrf},
    )
    assert result.status_code == 201, result.text
    row = result.json()["snapshot"]["rows"][0]
    assert row["state"] == state
    assert row.get("derivedStrength") == expected_strength
    assert row.get("derivedSourceConfidence") == expected_confidence
    if expected_effect is None:
        assert row["evidenceId"] is None
        assert (
            db.scalar(
                select(func.count())
                .select_from(Evidence)
                .where(Evidence.source_type == "assessment_review")
            )
            == 0
        )
    else:
        evidence = db.get(Evidence, row["evidenceId"])
        link = db.scalar(select(EvidenceLink).where(EvidenceLink.evidence_id == evidence.id))
        assert (
            evidence.strength == expected_strength
            and evidence.source_confidence == expected_confidence
        )
        assert link is not None and link.effect == expected_effect


async def test_review_authority_assistance_and_correction(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf, suggestion = await _seed_today(authenticated_client, db)
    execution, option = await _start_and_complete(client, csrf, db, suggestion, assistance="none")
    criterion_id = option["criteria"][0]["criterionDefinitionId"]
    payload = _review_request(criterion_id, additional="ai_assisted")
    no_override = await client.post(
        f"/api/v2/assessment-executions/{execution.id}/reviews",
        json={**payload, "source_confidence": "high"},
        headers={"X-CSRF-Token": csrf},
    )
    assert no_override.status_code == 422
    unattested = await client.post(
        f"/api/v2/assessment-executions/{execution.id}/reviews",
        json={**payload, "attestation": {**payload["attestation"], "outputs_authentic": False}},
        headers={"X-CSRF-Token": csrf},
    )
    assert unattested.status_code == 422
    first = await client.post(
        f"/api/v2/assessment-executions/{execution.id}/reviews",
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert first.status_code == 201, first.text
    row = first.json()["snapshot"]["rows"][0]
    assert row["derivedIndependence"] == "assisted"
    assert first.json()["analysisStatus"] == "stale"
    repeated = await client.post(
        f"/api/v2/assessment-executions/{execution.id}/reviews",
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert repeated.status_code == 201 and repeated.json()["id"] == first.json()["id"]
    conflict = await client.post(
        f"/api/v2/assessment-executions/{execution.id}/reviews",
        json={**payload, "observations": [_observation(criterion_id, state="unobserved")]},
        headers={"X-CSRF-Token": csrf},
    )
    assert conflict.status_code == 409
    corrected = await client.post(
        f"/api/v2/assessment-executions/{execution.id}/reviews",
        json={
            **_review_request(criterion_id, state="unobserved", key="review-corrected"),
            "supersedes_review_id": first.json()["id"],
            "correction_reason": "The observed criterion was mistakenly marked demonstrated.",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert corrected.status_code == 201, corrected.text
    assert db.scalar(select(func.count()).select_from(AssessmentReview)) == 2
    assert (
        db.scalar(
            select(func.count())
            .select_from(Evidence)
            .where(Evidence.source_type == "assessment_review")
        )
        == 1
    )
    from app.models import EvidenceRetraction

    assert (
        db.scalar(
            select(EvidenceRetraction).where(EvidenceRetraction.evidence_id == row["evidenceId"])
        )
        is not None
    )
    _validate_portable_payload(_portable_payload(db), "corrected-assessment-v11", 11)


async def test_agent_led_assessment_is_context_only_despite_complete_artifact(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf, suggestion = await _seed_today(authenticated_client, db)
    execution, option = await _start_and_complete(
        client, csrf, db, suggestion, assistance="agent_led"
    )
    criterion_id = option["criteria"][0]["criterionDefinitionId"]
    artifact_id = await _upload_artifact(client, csrf, execution, criterion_id)
    result = await client.post(
        f"/api/v2/assessment-executions/{execution.id}/reviews",
        json=_review_request(criterion_id, corroboration_id=artifact_id),
        headers={"X-CSRF-Token": csrf},
    )
    assert result.status_code == 201, result.text
    row = result.json()["snapshot"]["rows"][0]
    assert row["derivedSourceConfidence"] == "medium"
    assert row["derivedStrength"] == "weak"
    link = db.scalar(select(EvidenceLink).where(EvidenceLink.evidence_id == row["evidenceId"]))
    assert link is not None and link.effect == "context_only"
    assert all(item["state"] == "unknown" for item in result.json()["criterionStates"])


async def test_session_assistance_correction_retracts_assessment_evidence(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf, suggestion = await _seed_today(authenticated_client, db)
    execution, option = await _start_and_complete(client, csrf, db, suggestion)
    criterion_id = option["criteria"][0]["criterionDefinitionId"]
    artifact_id = await _upload_artifact(client, csrf, execution, criterion_id)
    review = await client.post(
        f"/api/v2/assessment-executions/{execution.id}/reviews",
        json=_review_request(criterion_id, corroboration_id=artifact_id),
        headers={"X-CSRF-Token": csrf},
    )
    assert review.status_code == 201, review.text
    assert review.json()["snapshot"]["rows"][0]["derivedSourceConfidence"] == "medium"
    evidence_id = review.json()["snapshot"]["rows"][0]["evidenceId"]
    corrected = await client.patch(
        f"/api/v1/sessions/{execution.session_id}",
        json={"assistance_mode": "ai_assisted"},
        headers={"X-CSRF-Token": csrf},
    )
    assert corrected.status_code == 200, corrected.text
    from app.models import EvidenceRetraction, SessionCorrection

    assert db.scalar(
        select(SessionCorrection).where(SessionCorrection.session_id == execution.session_id)
    )
    assert db.scalar(
        select(EvidenceRetraction).where(EvidenceRetraction.evidence_id == evidence_id)
    )
    assert db.get(AssessmentExecution, execution.id).assistance_mode_at_start == "none"
    detail = await client.get(f"/api/v2/assessment-executions/{execution.id}")
    assert detail.status_code == 200 and detail.json()["assistanceMode"] == "ai_assisted"
    assert detail.json()["latestResult"]["snapshot"]["rows"][0]["evidenceActive"] is False
    _validate_portable_payload(_portable_payload(db), "corrected-session-assessment-v11", 11)


async def test_assessment_portable_v11_round_trip(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf, suggestion = await _seed_today(authenticated_client, db)
    execution, option = await _start_and_complete(client, csrf, db, suggestion)
    criterion_id = option["criteria"][0]["criterionDefinitionId"]
    artifact_id = await _upload_artifact(client, csrf, execution, criterion_id)
    review = await client.post(
        f"/api/v2/assessment-executions/{execution.id}/reviews",
        json=_review_request(criterion_id, corroboration_id=artifact_id),
        headers={"X-CSRF-Token": csrf},
    )
    assert review.status_code == 201, review.text
    portable = _portable_payload(db)
    assert portable["manifest"] == PORTABLE_V11_MANIFEST
    assert len(portable["tables"]["assessment_executions"]) == 1
    assert len(portable["tables"]["assessment_artifacts"]) == 1
    assert len(portable["tables"]["assessment_reviews"]) == 1
    tables, _ = _validate_portable_payload(portable, "assessment-v11-round-trip", 11)
    assert tables["assessment_reviews"][0]["id"] == review.json()["id"]
    tampered = deepcopy(portable)
    assessment_evidence = next(
        row for row in tampered["tables"]["evidence"] if row["source_type"] == "assessment_review"
    )
    assessment_evidence["source_confidence"] = "high"
    with pytest.raises(AppError, match="Assessment execution or review lineage"):
        _validate_portable_payload(tampered, "assessment-confidence-tampered", 11)
    _apply_portable_restore(
        db, portable, True, package_id="assessment-v11-round-trip", schema_version=11
    )
    db.commit()
    assert db.get(AssessmentExecution, execution.id) is not None
    assert db.get(AssessmentArtifact, artifact_id) is not None
    assert db.get(AssessmentReview, review.json()["id"]) is not None


def test_portable_v10_adapter_initializes_empty_assessment_history(db: Session) -> None:
    older = _portable_payload(db)
    older["manifest"] = PORTABLE_V10_MANIFEST
    for table_name in PORTABLE_V11_ASSESSMENT_TABLES:
        older["tables"].pop(table_name)
    upgraded, summary = _validate_portable_payload(older, "assessment-v10-upgrade", 10)
    assert all(upgraded[name] == [] for name in PORTABLE_V11_ASSESSMENT_TABLES)
    assert summary["compatibilityConversions"]["initializedAssessmentTables"] == 3


@pytest.mark.parametrize(
    "change",
    [
        "blocked",
        "cancelled",
        "zero_duration",
        "wrong_activity",
        "wrong_session",
        "broken_review",
        "assistance",
    ],
)
async def test_portable_v11_rejects_invalid_assessment_source_lineage(
    authenticated_client: tuple[AsyncClient, str], db: Session, change: str
) -> None:
    client, csrf, suggestion = await _seed_today(authenticated_client, db)
    execution, option = await _start_and_complete(client, csrf, db, suggestion)
    review = await client.post(
        f"/api/v2/assessment-executions/{execution.id}/reviews",
        json=_review_request(option["criteria"][0]["criterionDefinitionId"]),
        headers={"X-CSRF-Token": csrf},
    )
    assert review.status_code == 201, review.text
    other_activity_id = None
    other_session_id = None
    if change in {"wrong_activity", "wrong_session"}:
        now = utc_now_ms()
        other_activity = create_activity_in_uow(
            db,
            title="Unrelated actual work",
            description=None,
            category_stable_key="practice",
            occurred_at=now,
            creator_source="user",
            provenance="test_fixture",
        )
        other_activity_id = other_activity.id
        if change == "wrong_session":
            other_session = start_timed_session_in_uow(
                db,
                activity=other_activity,
                assistance_mode="none",
                notes=None,
                contributions=[],
                started_at=now,
            )
            other_session_id = other_session.id
        db.commit()
        if other_session_id is not None:
            sleep(0.01)
            finished = await client.post(
                f"/api/v2/sessions/{other_session_id}/complete",
                json={"outcome": "completed"},
                headers={"X-CSRF-Token": csrf},
            )
            assert finished.status_code == 200, finished.text
    valid = _portable_payload(db)
    _validate_portable_payload(valid, "valid-assessment-lineage", 11)
    tampered = deepcopy(valid)
    session = next(
        row for row in tampered["tables"]["learning_sessions"] if row["id"] == execution.session_id
    )
    if change == "blocked":
        session["outcome"] = "blocked"
    elif change == "cancelled":
        session["timed_state"] = "cancelled"
        session["outcome"] = None
    elif change == "zero_duration":
        session["duration_ms"] = 0
        session["accumulated_duration_ms"] = 0
    elif change == "wrong_activity":
        tampered["tables"]["assessment_executions"][0]["activity_id"] = other_activity_id
    elif change == "wrong_session":
        tampered["tables"]["assessment_executions"][0]["session_id"] = other_session_id
    elif change == "broken_review":
        row = tampered["tables"]["assessment_reviews"][0]
        snapshot = json.loads(row["snapshot_json"])
        snapshot["rows"][0]["evidenceId"] = "wrong-evidence"
        row["snapshot_json"] = json.dumps(snapshot)
    else:
        session["assistance_mode"] = "ai_assisted"
    with pytest.raises(AppError):
        _validate_portable_payload(tampered, f"tampered-assessment-{change}", 11)
    if change == "blocked":
        db.rollback()
        with pytest.raises(AppError):
            with db.begin():
                _apply_portable_restore(
                    db, tampered, True, package_id="tampered-assessment-restore", schema_version=11
                )
        assert db.get(AssessmentExecution, execution.id) is not None


async def test_optional_artifact_narrative_remains_low_until_output_is_captured(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf, suggestion = await _seed_today(
        authenticated_client, db, linux_assessment_fixture(requires_artifact=False)
    )
    option = assessment_task_options(db, suggestion.id)[0]
    execution, _ = await _start_and_complete(client, csrf, db, suggestion)
    criterion_id = option["criteria"][0]["criterionDefinitionId"]
    narrative = await client.post(
        f"/api/v2/assessment-executions/{execution.id}/reviews",
        json=_review_request(criterion_id, artifact=None),
        headers={"X-CSRF-Token": csrf},
    )
    assert narrative.status_code == 201, narrative.text
    row = narrative.json()["snapshot"]["rows"][0]
    assert row["derivedSourceConfidence"] == "low"
    assert row["derivedStrength"] == "weak"
    assert all(item["state"] == "unknown" for item in narrative.json()["criterionStates"])
    artifact_id = await _upload_artifact(client, csrf, execution, criterion_id)
    corrected = await client.post(
        f"/api/v2/assessment-executions/{execution.id}/reviews",
        json={
            **_review_request(
                criterion_id, key="captured-output-review", corroboration_id=artifact_id
            ),
            "supersedes_review_id": narrative.json()["id"],
            "correction_reason": "The task output capture was omitted from the first review.",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert corrected.status_code == 201, corrected.text
    assert corrected.json()["snapshot"]["rows"][0]["derivedSourceConfidence"] == "medium"


def test_optional_artifact_attestation_cannot_replace_output_provenance() -> None:
    observation = AssessmentObservationInput.model_validate(
        _observation("criterion-1", artifact=None)
    )
    derived = derive_review_evidence_policy(
        observation,
        session_mode="none",
        requires_artifact=False,
        intended_modes={"independent"},
        intended_strengths={"moderate"},
    )
    assert derived["confidence"] == "low"
    assert derived["effect"] == "context_only"
    reference_only = observation.model_copy(
        update={"artifact_reference": "https://example.invalid/claimed-artifact"}
    )
    referenced = derive_review_evidence_policy(
        reference_only,
        session_mode="none",
        requires_artifact=False,
        intended_modes={"independent"},
        intended_strengths={"moderate"},
    )
    assert referenced["confidence"] == "low"


@pytest.mark.parametrize("requires_artifact", [False, True])
@pytest.mark.parametrize(
    "narrative_kind", ["plain", "attested", "external_reference", "external_path", "fake_output"]
)
async def test_free_text_artifact_claims_never_corroborate_medium(
    authenticated_client: tuple[AsyncClient, str],
    db: Session,
    requires_artifact: bool,
    narrative_kind: str,
) -> None:
    client, csrf, suggestion = await _seed_today(
        authenticated_client, db, linux_assessment_fixture(requires_artifact=requires_artifact)
    )
    execution, option = await _start_and_complete(client, csrf, db, suggestion)
    criterion_id = option["criteria"][0]["criterionDefinitionId"]
    payload = _review_request(criterion_id, artifact="I completed the task successfully. " * 20)
    row = payload["observations"][0]
    if narrative_kind == "external_reference":
        row["artifact_reference"] = "https://example.invalid/claimed-output"
    elif narrative_kind == "external_path":
        row["artifact_reference"] = "/tmp/claimed-output.txt"
    elif narrative_kind == "fake_output":
        row["artifact_content"] = "SHA-256: " + "a" * 64 + "\n$ pipeline | status\nSUCCESS\n" * 10
    reviewed = await client.post(
        f"/api/v2/assessment-executions/{execution.id}/reviews",
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert reviewed.status_code == 201, reviewed.text
    result_row = reviewed.json()["snapshot"]["rows"][0]
    assert result_row["derivedSourceConfidence"] == "low"
    assert result_row["derivedStrength"] == "weak"
    linked = db.scalar(
        select(EvidenceLink).where(EvidenceLink.evidence_id == result_row["evidenceId"])
    )
    assert linked is not None and linked.effect == "context_only"
    assert all(item["state"] == "unknown" for item in reviewed.json()["criterionStates"])


async def test_caller_cannot_claim_hash_confidence_or_other_artifact(
    authenticated_client: tuple[AsyncClient, str],
    db: Session,
) -> None:
    client, csrf, suggestion = await _seed_today(authenticated_client, db)
    execution, option = await _start_and_complete(client, csrf, db, suggestion)
    criterion_id = option["criteria"][0]["criterionDefinitionId"]
    artifact_id = await _upload_artifact(client, csrf, execution, criterion_id)
    base = _review_request(criterion_id)
    variants = [
        {**base, "source_confidence": "medium"},
        {**base, "observations": [{**base["observations"][0], "artifact_hash": "a" * 64}]},
        {
            **base,
            "observations": [
                {
                    **base["observations"][0],
                    "corroboration": {"kind": "external_reference", "artifact_id": artifact_id},
                }
            ],
        },
        {
            **base,
            "observations": [
                {
                    **base["observations"][0],
                    "corroboration": {"kind": "server_stored_artifact", "artifact_id": "missing"},
                }
            ],
        },
    ]
    for payload in variants:
        rejected = await client.post(
            f"/api/v2/assessment-executions/{execution.id}/reviews",
            json=payload,
            headers={"X-CSRF-Token": csrf},
        )
        assert rejected.status_code == 422, rejected.text
    assert db.scalar(select(func.count()).select_from(AssessmentReview)) == 0


@pytest.mark.parametrize(
    "change",
    ["bytes", "hash", "execution", "session", "criterion", "snapshot_id", "evidence_provenance"],
)
async def test_portable_v11_rejects_artifact_provenance_tamper(
    authenticated_client: tuple[AsyncClient, str],
    db: Session,
    change: str,
) -> None:
    client, csrf, suggestion = await _seed_today(authenticated_client, db)
    execution, option = await _start_and_complete(client, csrf, db, suggestion)
    criterion_id = option["criteria"][0]["criterionDefinitionId"]
    artifact_id = await _upload_artifact(client, csrf, execution, criterion_id)
    reviewed = await client.post(
        f"/api/v2/assessment-executions/{execution.id}/reviews",
        json=_review_request(criterion_id, corroboration_id=artifact_id),
        headers={"X-CSRF-Token": csrf},
    )
    assert reviewed.status_code == 201, reviewed.text
    valid = _portable_payload(db)
    _validate_portable_payload(valid, "valid-stored-artifact", 11)
    tampered = deepcopy(valid)
    artifact = tampered["tables"]["assessment_artifacts"][0]
    if change == "bytes":
        artifact["content_base64"] = "ZmFrZQ=="
    elif change == "hash":
        artifact["sha256"] = "a" * 64
    elif change == "execution":
        artifact["execution_id"] = "missing"
    elif change == "session":
        artifact["session_id"] = "missing"
    elif change == "criterion":
        artifact["criterion_definition_id"] = "missing"
    elif change == "snapshot_id":
        row = tampered["tables"]["assessment_reviews"][0]
        snapshot = json.loads(row["snapshot_json"])
        snapshot["rows"][0]["corroborationId"] = "missing"
        row["snapshot_json"] = json.dumps(snapshot)
    else:
        evidence = next(
            item
            for item in tampered["tables"]["evidence"]
            if item["source_type"] == "assessment_review"
        )
        provenance = json.loads(evidence["provenance_json"])
        provenance["corroboration_id"] = "missing"
        evidence["provenance_json"] = json.dumps(provenance)
    with pytest.raises(AppError):
        _validate_portable_payload(tampered, f"tampered-stored-artifact-{change}", 11)
    if change == "hash":
        db.rollback()
        with pytest.raises(AppError):
            with db.begin():
                _apply_portable_restore(
                    db,
                    tampered,
                    True,
                    package_id="tampered-stored-artifact-restore",
                    schema_version=11,
                )
        assert db.get(AssessmentArtifact, artifact_id) is not None


@pytest.mark.parametrize("matching_suitable", [False, True])
async def test_assessment_option_requires_its_own_target_to_be_suitable(
    authenticated_client: tuple[AsyncClient, str], db: Session, matching_suitable: bool
) -> None:
    master = linux_assessment_fixture()
    unit = master["payload"]["curriculum"]["units"][0]
    original = unit["targets"][0]
    original["supportsUnassessed"] = matching_suitable
    unit["targets"].append(
        {
            **original,
            "criterionRef": "mi.linux.shell::select-compose-files",
            "supportsUnassessed": True,
            "orderIndex": 1,
        }
    )
    rubric = master["payload"]["curriculum"]["assessmentRubrics"][0]
    rubric["rubric"]["criteria"] = rubric["rubric"]["criteria"][:1]
    rubric["rubric"]["criteria"][0]["weight"] = 100
    _client, _csrf, suggestion = await _seed_today(authenticated_client, db, master)
    options = assessment_task_options(db, suggestion.id)
    assert len(options) == (1 if matching_suitable else 0)
    if matching_suitable:
        pinned = assessment_task_options(db, suggestion.id, require_eligible=False)[0]
        criterion_id = pinned["criteria"][0]["criterionDefinitionId"]
        assert [row["criterionDefinitionId"] for row in options[0]["criteria"]] == [criterion_id]


async def test_finalized_assessment_stays_open_until_review_across_today_days(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf, suggestion = await _seed_today(authenticated_client, db)
    execution, option = await _start_and_complete(client, csrf, db, suggestion)
    blocked = await client.post(
        f"/api/v2/today/suggestions/{suggestion.id}/completed",
        json={"idempotency_key": "premature-assessment-close", "session_id": execution.session_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "ASSESSMENT_REVIEW_REQUIRED"
    refreshed_analysis = run_analysis(
        db,
        idempotency_key="assessment-awaiting-review-analysis",
        purpose="learning_control",
        update_current=True,
    )
    db.commit()
    generate_recommendations(
        db,
        idempotency_key="assessment-awaiting-review-recommendation",
        analysis_snapshot_id=refreshed_analysis.id,
        available_time_ms=None,
    )
    db.commit()
    generate_today(
        db,
        idempotency_key="assessment-awaiting-review-regeneration",
        analysis_snapshot_id=refreshed_analysis.id,
        available_time_ms=None,
        context_costs=(),
        regenerate=True,
    )
    db.commit()
    same_day = current_today(db)
    assert any(item["id"] == suggestion.id for item in same_day["continuingStartedSuggestions"])
    later = current_today(db, now_ms=suggestion.expires_at + 86_400_000)
    assert any(item["id"] == suggestion.id for item in later["continuingStartedSuggestions"])
    listed = await client.get("/api/v2/assessment-executions")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [execution.id]
    detail = await client.get(f"/api/v2/assessment-executions/{execution.id}")
    assert detail.status_code == 200 and detail.json()["reviewRequired"] is True
    review = await client.post(
        f"/api/v2/assessment-executions/{execution.id}/reviews",
        json=_review_request(option["criteria"][0]["criterionDefinitionId"]),
        headers={"X-CSRF-Token": csrf},
    )
    assert review.status_code == 201, review.text
    closed = await client.post(
        f"/api/v2/today/suggestions/{suggestion.id}/completed",
        json={"idempotency_key": "reviewed-assessment-close", "session_id": execution.session_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert closed.status_code == 201, closed.text
    assert closed.json()["status"] == "completed"


async def test_legacy_started_assessment_preserves_old_work_and_starts_new_bound_attempt(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf, suggestion = await _seed_today(authenticated_client, db)
    option = assessment_task_options(db, suggestion.id)[0]
    activated = await client.post(
        "/api/v2/authority/activate-v2",
        json={
            "idempotency_key": "legacy-upgrade-v2-authority",
            "reason": "Disposable production-like upgrade compatibility fixture.",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert activated.status_code == 200, activated.text
    now = utc_now_ms()
    activity = create_activity_in_uow(
        db,
        title="Earlier assessment work",
        description="Started before task-bound assessments were supported.",
        category_stable_key="verification",
        occurred_at=now,
        creator_source="user",
        provenance="today_v2_direct_start",
    )
    session = start_timed_session_in_uow(
        db,
        activity=activity,
        assistance_mode="none",
        notes=None,
        contributions=[],
        started_at=now,
    )
    _create_relation(
        db,
        suggestion_id=suggestion.id,
        activity_id=activity.id,
        relation_type="matched",
        actor="system",
        source="today_direct_start",
        automatic=True,
        idempotency_key="legacy-assessment-relation",
        created_at=now,
    )
    _append_interaction(
        db,
        suggestion,
        interaction_type="started",
        idempotency_key="legacy-assessment-start",
        actor="user",
        source="today_start",
        occurred_at=now,
        activity_id=activity.id,
        session_id=session.id,
        command_facts=_start_command_facts(assistance_mode="none", notes=None, contributions=[]),
    )
    db.commit()
    before_authority = db.get(LearningControlAuthorityState, 1)
    before_authority_value = (
        before_authority.canonical_learning_authority if before_authority else None
    )
    assert before_authority_value == "v2"
    assert db.scalar(select(func.count()).select_from(MasterImportRevision)) == 1
    history_counts = tuple(
        db.scalar(select(func.count()).select_from(model))
        for model in (RecommendationV2Run, TodayGeneration)
    )
    database_url = str(db.get_bind().url)
    db.close()
    root = Path(__file__).resolve().parents[2]
    migration_config = Config(str(root / "alembic.ini"))
    migration_config.set_main_option("script_location", str(root / "backend" / "alembic"))
    migration_config.set_main_option("sqlalchemy.url", database_url)
    alembic_command.downgrade(migration_config, "0020_master_import_ledger")
    alembic_command.upgrade(migration_config, "0021_assessment_execution")
    assert (
        tuple(
            db.scalar(select(func.count()).select_from(model))
            for model in (RecommendationV2Run, TodayGeneration)
        )
        == history_counts
    )
    after_authority = db.get(LearningControlAuthorityState, 1)
    assert (
        after_authority.canonical_learning_authority if after_authority else None
    ) == before_authority_value
    assert db.scalar(select(func.count()).select_from(AssessmentExecution)) == 0
    legacy = await client.get(f"/api/v2/assessment-executions/by-suggestion/{suggestion.id}")
    assert legacy.status_code == 200 and legacy.json()["kind"] == "legacy_started"
    unfinished = await client.post(
        f"/api/v2/today/suggestions/{suggestion.id}/restart-legacy-assessment",
        json={
            "idempotency_key": "legacy-new-attempt",
            "assistance_mode": "docs_only",
            "assessment_unit_definition_id": option["unitDefinitionId"],
            "assessment_opportunity_id": option["opportunityId"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert unfinished.status_code == 409
    finished = await client.post(
        f"/api/v2/sessions/{session.id}/complete",
        json={"outcome": "completed"},
        headers={"X-CSRF-Token": csrf},
    )
    assert finished.status_code == 200, finished.text
    invalid_new_task = await client.post(
        f"/api/v2/today/suggestions/{suggestion.id}/restart-legacy-assessment",
        json={
            "idempotency_key": "legacy-invalid-new-task",
            "assistance_mode": "docs_only",
            "assessment_unit_definition_id": "missing-task",
            "assessment_opportunity_id": option["opportunityId"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert invalid_new_task.status_code == 422
    still_legacy = await client.get(f"/api/v2/assessment-executions/by-suggestion/{suggestion.id}")
    assert still_legacy.status_code == 200
    assert still_legacy.json()["kind"] == "legacy_started"
    assert db.scalar(select(func.count()).select_from(AssessmentExecution)) == 0
    restarted = await client.post(
        f"/api/v2/today/suggestions/{suggestion.id}/restart-legacy-assessment",
        json={
            "idempotency_key": "legacy-new-attempt",
            "assistance_mode": "docs_only",
            "assessment_unit_definition_id": option["unitDefinitionId"],
            "assessment_opportunity_id": option["opportunityId"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert restarted.status_code == 201, restarted.text
    replay = await client.post(
        f"/api/v2/today/suggestions/{suggestion.id}/restart-legacy-assessment",
        json={
            "idempotency_key": "legacy-new-attempt",
            "assistance_mode": "docs_only",
            "assessment_unit_definition_id": option["unitDefinitionId"],
            "assessment_opportunity_id": option["opportunityId"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert replay.status_code == 201
    assert db.scalar(select(func.count()).select_from(AssessmentExecution)) == 1
    execution = db.scalar(select(AssessmentExecution))
    assert execution is not None
    assert execution.session_id != session.id and execution.activity_id != activity.id
    assert db.get(LearningSession, session.id) is not None
    assert db.scalar(select(func.count()).select_from(AssessmentReview)) == 0
    assert (
        db.scalar(
            select(func.count())
            .select_from(Evidence)
            .where(Evidence.source_type == "assessment_review")
        )
        == 0
    )
    _validate_portable_payload(_portable_payload(db), "legacy-new-bound-attempt", 11)


async def test_unstarted_pre_0021_assessment_starts_without_today_regeneration(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf, suggestion = await _seed_today(authenticated_client, db)
    original_generation_count = db.scalar(select(func.count()).select_from(TodayGeneration))
    database_url = str(db.get_bind().url)
    db.close()
    root = Path(__file__).resolve().parents[2]
    migration_config = Config(str(root / "alembic.ini"))
    migration_config.set_main_option("script_location", str(root / "backend" / "alembic"))
    migration_config.set_main_option("sqlalchemy.url", database_url)
    alembic_command.downgrade(migration_config, "0020_master_import_ledger")
    alembic_command.upgrade(migration_config, "0021_assessment_execution")
    assert db.scalar(select(func.count()).select_from(TodayGeneration)) == original_generation_count
    assert db.scalar(select(func.count()).select_from(AssessmentExecution)) == 0
    options = await client.get(f"/api/v2/today/suggestions/{suggestion.id}/assessment-options")
    assert options.status_code == 200 and options.json()["items"]
    task = options.json()["items"][0]
    started = await client.post(
        f"/api/v2/today/suggestions/{suggestion.id}/start",
        json={
            "idempotency_key": "upgraded-unstarted-assessment-start",
            "assistance_mode": "none",
            "assessment_unit_definition_id": task["unitDefinitionId"],
            "assessment_opportunity_id": task["opportunityId"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert started.status_code == 201, started.text
    assert db.scalar(select(func.count()).select_from(AssessmentExecution)) == 1
    assert db.scalar(select(func.count()).select_from(TodayGeneration)) == original_generation_count


async def test_exact_frozen_linux_master_import_cold_start_when_available(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    artifact_path = os.environ.get("LCC_FROZEN_MASTER_IMPORT")
    if not artifact_path:
        pytest.skip("Exact frozen Master Import path was not provided.")
    content = Path(artifact_path).read_bytes()
    assert hashlib.sha256(content).hexdigest() == (
        "a73c7d913e8ca1cb52107ecb9f2d32a8006c6d3be29d94a79eb5e246358b1700"
    )
    client, csrf, _suggestion = await _seed_today(authenticated_client, db, json.loads(content))
    identity = db.scalar(
        select(AssessmentRubricIdentity).where(
            AssessmentRubricIdentity.stable_key == "mi.rubric.linux.shell"
        )
    )
    assert identity is not None
    rubric = db.scalar(
        select(AssessmentRubricDefinition).where(
            AssessmentRubricDefinition.rubric_identity_id == identity.id
        )
    )
    assert rubric is not None
    available_suggestions = db.scalars(select(TodaySuggestion)).all()
    suggestion = next(
        (
            item
            for item in available_suggestions
            if load_public_recommendation_item(
                db, run_id=item.recommendation_run_id, candidate_id=item.candidate_id
            ).source_entity_id
            == rubric.id
        ),
        None,
    )
    assert suggestion is not None, [
        load_public_recommendation_item(
            db, run_id=item.recommendation_run_id, candidate_id=item.candidate_id
        ).title
        for item in available_suggestions
    ]
    options = assessment_task_options(db, suggestion.id)
    assert {item["unitTitle"] for item in options} == {
        "Select and transform a disposable file tree",
        "Trace a shell pipeline and status",
    }
    trace = next(item for item in options if "pipeline" in item["unitTitle"].lower())
    assert [item["criterionStableKey"] for item in trace["criteria"]] == ["trace-pipeline-status"]
    assert trace["intendedStrengths"] == ["moderate"]
    started = await client.post(
        f"/api/v2/today/suggestions/{suggestion.id}/start",
        json={
            "idempotency_key": "frozen-linux-trace-start",
            "assistance_mode": "none",
            "assessment_unit_definition_id": trace["unitDefinitionId"],
            "assessment_opportunity_id": trace["opportunityId"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert started.status_code == 201, started.text
    execution = db.scalar(select(AssessmentExecution))
    assert execution is not None
    sleep(0.01)
    completed = await client.post(
        f"/api/v2/sessions/{execution.session_id}/complete",
        json={"outcome": "completed"},
        headers={"X-CSRF-Token": csrf},
    )
    assert completed.status_code == 200, completed.text
    assert (
        db.scalar(
            select(func.count())
            .select_from(Evidence)
            .where(Evidence.source_type == "assessment_review")
        )
        == 0
    )
    criterion_id = trace["criteria"][0]["criterionDefinitionId"]
    narrative = await client.post(
        f"/api/v2/assessment-executions/{execution.id}/reviews",
        json=_review_request(
            criterion_id, artifact="I completed this exact task successfully. " * 10
        ),
        headers={"X-CSRF-Token": csrf},
    )
    assert narrative.status_code == 201, narrative.text
    assert narrative.json()["snapshot"]["rows"][0]["derivedSourceConfidence"] == "low"
    assert all(item["state"] == "unknown" for item in narrative.json()["criterionStates"])
    artifact_id = await _upload_artifact(client, csrf, execution, criterion_id)
    reviewed = await client.post(
        f"/api/v2/assessment-executions/{execution.id}/reviews",
        json={
            **_review_request(
                criterion_id, key="frozen-artifact-correction", corroboration_id=artifact_id
            ),
            "supersedes_review_id": narrative.json()["id"],
            "correction_reason": "The original review lacked stored task output.",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert reviewed.status_code == 201, reviewed.text
    result = reviewed.json()
    assert result["snapshot"]["rows"][0]["derivedStrength"] == "moderate"
    assert result["snapshot"]["rows"][0]["derivedSourceConfidence"] == "medium"
    assert result["capability"]["levelKey"] != "independent"
    assert sum(item["state"] == "unknown" for item in result["criterionStates"]) == 5
    links = db.scalars(
        select(EvidenceLink).where(
            EvidenceLink.evidence_id == result["snapshot"]["rows"][0]["evidenceId"]
        )
    ).all()
    assert len(links) == 1
    assert links[0].criterion_definition_id == trace["criteria"][0]["criterionDefinitionId"]
