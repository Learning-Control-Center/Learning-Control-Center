from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.v3.models import AnalysisV3CurrentState
from app.assessment.contracts import AssessmentObservationInput, AssessmentReviewRequest
from app.assessment.models import AssessmentArtifact, AssessmentExecution, AssessmentReview
from app.capability import commit_source_and_drain
from app.curriculum.models import (
    ActiveCurriculumVersionState,
    AssessmentRubricDefinition,
    AssessmentRubricIdentity,
    EvidenceOpportunityDefinition,
    LearningUnitDefinition,
    LearningUnitTarget,
)
from app.curriculum.service import build_unit_availability
from app.determinism import canonical_json
from app.errors import AppError
from app.evidence import EVIDENCE_POLICY, _add_link, _queue_evidence_invalidations
from app.models import (
    CapabilityScaleLevel,
    CompetencyCapabilityState,
    CompetencyIdentity,
    CriterionDefinition,
    CriterionEvaluationResult,
    CriterionIdentity,
    Evidence,
    EvidenceLink,
    EvidenceRetraction,
    LearningSession,
    SessionCorrection,
    new_id,
)
from app.recommendation.v2.public import load_public_recommendation_item
from app.schemas import EvidenceLinkCreate
from app.time_utils import epoch_ms_to_rfc3339, utc_now_ms
from app.today.models import TodaySuggestion, TodaySuggestionCurrentState

ASSESSMENT_EXECUTION_POLICY = "assessment-execution-policy/v1"
ASSESSMENT_REVIEW_POLICY = "first-party-assessment-review/v1"
ASSISTANCE_RANK = {"none": 0, "docs_only": 1, "ai_hint": 2, "ai_assisted": 3, "agent_led": 4}
MAX_ASSESSMENT_ARTIFACT_BYTES = 262_144
ASSESSMENT_ARTIFACT_CAPTURE = "server_received_bytes/v1"


def assessment_artifact_valid(
    artifact: Mapping[str, Any], execution: Mapping[str, Any], criterion_id: str
) -> bool:
    """Validate server-captured bytes and their exact task/Session binding."""
    try:
        content = base64.b64decode(artifact["content_base64"], validate=True)
    except (KeyError, TypeError, ValueError, binascii.Error):
        return False
    return bool(
        artifact.get("capture_method") == ASSESSMENT_ARTIFACT_CAPTURE
        and artifact.get("execution_id") == execution.get("id")
        and artifact.get("session_id") == execution.get("session_id")
        and artifact.get("unit_definition_id") == execution.get("unit_definition_id")
        and artifact.get("opportunity_id") == execution.get("opportunity_id")
        and artifact.get("criterion_definition_id") == criterion_id
        and isinstance(artifact.get("size_bytes"), int)
        and 0 < artifact["size_bytes"] <= MAX_ASSESSMENT_ARTIFACT_BYTES
        and len(content) == artifact["size_bytes"]
        and base64.b64encode(content).decode("ascii") == artifact["content_base64"]
        and hashlib.sha256(content).hexdigest() == artifact.get("sha256")
        and isinstance(artifact.get("created_at"), int)
        and bool(artifact.get("filename"))
        and "/" not in artifact["filename"]
        and "\\" not in artifact["filename"]
    )


def assessment_session_reviewable(session: Mapping[str, Any] | None, *, activity_id: str) -> bool:
    """The single source Session gate for live review and Portable assessment history."""
    return bool(
        session is not None
        and session.get("activity_id") == activity_id
        and session.get("session_mode") == "timed"
        and session.get("timed_state") == "completed"
        and session.get("outcome") in {"completed", "partial"}
        and isinstance(session.get("duration_ms"), int)
        and session["duration_ms"] > 0
        and session.get("tombstoned_at") is None
        and session.get("assistance_mode") in ASSISTANCE_RANK
    )


def assessment_session_facts(session: LearningSession) -> dict[str, Any]:
    return {
        "activity_id": session.activity_id,
        "session_mode": session.session_mode,
        "timed_state": session.timed_state,
        "outcome": session.outcome,
        "duration_ms": session.duration_ms,
        "tombstoned_at": session.tombstoned_at,
        "assistance_mode": session.assistance_mode,
    }


def assessment_lineage_valid(
    *,
    execution: Mapping[str, Any],
    suggestion: Mapping[str, Any],
    candidate: Mapping[str, Any],
    rubric: Mapping[str, Any],
    unit: Mapping[str, Any],
    opportunity: Mapping[str, Any],
    session: Mapping[str, Any],
    finalized: bool,
) -> bool:
    """Validate the same pinned source lineage for live review and Portable V11."""
    return bool(
        execution.get("policy_version") == ASSESSMENT_EXECUTION_POLICY
        and suggestion.get("id") == execution.get("today_suggestion_id")
        and suggestion.get("recommendation_run_id") == execution.get("recommendation_run_id")
        and suggestion.get("candidate_id") == execution.get("recommendation_candidate_id")
        and candidate.get("id") == execution.get("recommendation_candidate_id")
        and candidate.get("run_id") == execution.get("recommendation_run_id")
        and candidate.get("candidate_type") == "assessment"
        and candidate.get("source_type") == "assessment_rubric"
        and candidate.get("source_entity_id") == rubric.get("id")
        and candidate.get("source_version_id") == execution.get("curriculum_version_id")
        and candidate.get("competency_identity_id") == execution.get("competency_identity_id")
        and candidate.get("target_identity_id") == execution.get("target_identity_id")
        and rubric.get("id") == execution.get("rubric_definition_id")
        and rubric.get("rubric_identity_id") == execution.get("rubric_identity_id")
        and rubric.get("curriculum_version_id") == execution.get("curriculum_version_id")
        and unit.get("id") == execution.get("unit_definition_id")
        and unit.get("curriculum_version_id") == execution.get("curriculum_version_id")
        and opportunity.get("id") == execution.get("opportunity_id")
        and opportunity.get("learning_unit_definition_id") == unit.get("id")
        and opportunity.get("evidence_kind") == "assessment"
        and session.get("id") == execution.get("session_id")
        and session.get("activity_id") == execution.get("activity_id")
        and (
            not finalized
            or assessment_session_reviewable(session, activity_id=execution["activity_id"])
        )
    )


def _rubric_for_suggestion(
    db: Session, suggestion: TodaySuggestion
) -> tuple[Any, AssessmentRubricDefinition]:
    candidate = load_public_recommendation_item(
        db, run_id=suggestion.recommendation_run_id, candidate_id=suggestion.candidate_id
    )
    if candidate.candidate_type != "assessment" or candidate.source_type != "assessment_rubric":
        raise AppError(
            422, "ASSESSMENT_SOURCE_INVALID", "The suggestion is not an authored assessment."
        )
    rubric = db.get(AssessmentRubricDefinition, candidate.source_entity_id)
    if rubric is None or rubric.curriculum_version_id != candidate.source_version_id:
        raise AppError(
            409, "ASSESSMENT_RUBRIC_MISSING", "The pinned assessment rubric is unavailable."
        )
    return candidate, rubric


def assessment_task_options(
    db: Session, suggestion_id: str, *, now_ms: int | None = None, require_eligible: bool = True
) -> list[dict[str, Any]]:
    suggestion = db.get(TodaySuggestion, suggestion_id)
    if suggestion is None:
        raise AppError(404, "TODAY_SUGGESTION_NOT_FOUND", "The suggestion does not exist.")
    candidate, rubric = _rubric_for_suggestion(db, suggestion)
    if require_eligible:
        identity = db.get(AssessmentRubricIdentity, rubric.rubric_identity_id)
        active = db.get(ActiveCurriculumVersionState, identity.curriculum_id) if identity else None
        if active is None or active.curriculum_version_id != rubric.curriculum_version_id:
            return []
    competency = db.get(CompetencyIdentity, candidate.competency_identity_id)
    if competency is None:
        return []
    rubric_rows = json.loads(rubric.rubric_json).get("criteria", [])
    rubric_checks = {
        row["criterionDefinitionId"]: row["description"]
        for row in rubric_rows
        if isinstance(row, dict)
        and isinstance(row.get("criterionDefinitionId"), str)
        and isinstance(row.get("description"), str)
    }
    options: list[dict[str, Any]] = []
    units = db.scalars(
        select(LearningUnitDefinition)
        .where(
            LearningUnitDefinition.curriculum_version_id == rubric.curriculum_version_id,
            LearningUnitDefinition.status == "active",
            LearningUnitDefinition.kind.in_(("exercise", "practice_task")),
        )
        .order_by(LearningUnitDefinition.order_index, LearningUnitDefinition.id)
    ).all()
    cutoff = (now_ms if now_ms is not None else utc_now_ms()) + 1
    for unit in units:
        opportunity_rows = db.scalars(
            select(EvidenceOpportunityDefinition)
            .where(
                EvidenceOpportunityDefinition.learning_unit_definition_id == unit.id,
                EvidenceOpportunityDefinition.evidence_kind == "assessment",
            )
            .order_by(EvidenceOpportunityDefinition.order_index)
        ).all()
        if not opportunity_rows:
            continue
        eligible_target_ids: set[str] | None = None
        if require_eligible:
            availability = build_unit_availability(db, unit, cutoff)
            if availability.availability_state != "met" or availability.readiness_state != "met":
                continue
            eligible_target_ids = {
                item.target_id
                for item in availability.target_suitability
                if item.candidate_usability_state == "met"
            }
        covered: list[dict[str, Any]] = []
        ineligible_covered_target = False
        for target in db.scalars(
            select(LearningUnitTarget)
            .where(
                LearningUnitTarget.learning_unit_definition_id == unit.id,
                LearningUnitTarget.semantic_definition_id == rubric.semantic_definition_id,
            )
            .order_by(LearningUnitTarget.order_index)
        ).all():
            definition = (
                db.get(CriterionDefinition, target.criterion_definition_id)
                if target.criterion_definition_id
                else None
            )
            criterion_identity = (
                db.get(CriterionIdentity, definition.criterion_identity_id) if definition else None
            )
            if (
                definition is None
                or criterion_identity is None
                or definition.id not in rubric_checks
            ):
                continue
            if eligible_target_ids is not None and target.id not in eligible_target_ids:
                ineligible_covered_target = True
                continue
            covered.append(
                {
                    "criterionDefinitionId": definition.id,
                    "criterionIdentityId": criterion_identity.id,
                    "criterionStableKey": criterion_identity.stable_key,
                    "description": definition.description,
                    "rubricCheck": rubric_checks[definition.id],
                    "scaleVersionId": target.scale_version_id,
                    "dimensionId": target.dimension_id,
                    "levelId": definition.level_id,
                }
            )
        if not covered or ineligible_covered_target:
            continue
        for opportunity in opportunity_rows:
            characteristics = json.loads(opportunity.possible_characteristics_json)
            required = json.loads(opportunity.required_characteristics_json)
            options.append(
                {
                    "rubricDefinitionId": rubric.id,
                    "rubricIdentityId": rubric.rubric_identity_id,
                    "curriculumVersionId": rubric.curriculum_version_id,
                    "unitDefinitionId": unit.id,
                    "opportunityId": opportunity.id,
                    "unitTitle": unit.title,
                    "unitKind": unit.kind,
                    "action": json.loads(unit.action_payload_json),
                    "criteria": covered,
                    "requiresArtifact": bool(required.get("artifact")),
                    "intendedStrengths": characteristics.get("intendedStrengths", []),
                    "intendedIndependenceModes": characteristics.get(
                        "intendedIndependenceModes", []
                    ),
                }
            )
    return options


def selected_task_option(
    db: Session,
    suggestion_id: str,
    unit_id: str,
    opportunity_id: str,
    *,
    now_ms: int | None = None,
    require_eligible: bool = True,
) -> dict[str, Any]:
    option = next(
        (
            item
            for item in assessment_task_options(
                db, suggestion_id, now_ms=now_ms, require_eligible=require_eligible
            )
            if item["unitDefinitionId"] == unit_id and item["opportunityId"] == opportunity_id
        ),
        None,
    )
    if option is None:
        raise AppError(
            422,
            "ASSESSMENT_TASK_INELIGIBLE",
            "The selected authored assessment task is not eligible.",
        )
    return option


def _effective_assistance(session_mode: str, additional: str | None) -> str:
    if additional is None or ASSISTANCE_RANK[session_mode] >= ASSISTANCE_RANK[additional]:
        return session_mode
    return additional


def _independence(mode: str) -> str:
    return {
        "none": "independent",
        "docs_only": "independent",
        "ai_hint": "assisted",
        "ai_assisted": "assisted",
        "agent_led": "guided",
    }[mode]


def _complete_observation(
    row: Any, *, requires_artifact: bool, corroboration_hash: str | None
) -> bool:
    fields = (row.task_setup, row.expected_result, row.observed_output, row.comparison)
    if any(value is None or len(value.strip()) < 24 for value in fields):
        return False
    if requires_artifact and not corroboration_hash:
        return False
    # An authored optional artifact does not lower the first-party confidence gate.
    # Only a separately stored, server-hashed artifact can corroborate the narrative.
    return bool(corroboration_hash)


def derive_review_evidence_policy(
    row: AssessmentObservationInput,
    *,
    session_mode: str,
    requires_artifact: bool,
    intended_modes: set[str],
    intended_strengths: set[str],
    corroboration_hash: str | None = None,
) -> dict[str, str | None]:
    if row.state == "unobserved":
        raise ValueError("An unobserved criterion cannot produce Evidence.")
    complete = _complete_observation(
        row, requires_artifact=requires_artifact, corroboration_hash=corroboration_hash
    )
    mode = _effective_assistance(session_mode, row.additional_assistance_mode)
    independence = _independence(mode)
    if intended_modes and independence not in intended_modes:
        complete = False
    strength = (
        "moderate"
        if complete
        and row.state != "partial"
        and mode != "agent_led"
        and "moderate" in intended_strengths
        else "weak"
    )
    can_affect_criterion = (
        complete
        and row.state != "partial"
        and mode != "agent_led"
        and strength in intended_strengths
    )
    return {
        "confidence": "medium" if complete else "low",
        "strength": strength,
        "effect": (
            "contradicts"
            if can_affect_criterion and row.state == "contradicted"
            else "context_only"
            if not can_affect_criterion
            else "supports"
        ),
        "independence": independence,
        "effective_mode": mode,
        "artifact_hash": corroboration_hash,
    }


def capture_assessment_artifact(
    db: Session,
    execution_id: str,
    criterion_id: str,
    idempotency_key: str,
    filename: str,
    content: bytes,
) -> AssessmentArtifact:
    if not 0 < len(content) <= MAX_ASSESSMENT_ARTIFACT_BYTES:
        raise AppError(422, "ASSESSMENT_ARTIFACT_SIZE", "Artifact must contain 1 to 262144 bytes.")
    safe_filename = filename.replace("\\", "/").split("/")[-1].strip()
    if not safe_filename or len(safe_filename) > 255:
        raise AppError(422, "ASSESSMENT_ARTIFACT_NAME", "A valid artifact filename is required.")
    digest = hashlib.sha256(content).hexdigest()
    existing = db.scalar(
        select(AssessmentArtifact).where(AssessmentArtifact.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if (
            existing.execution_id != execution_id
            or existing.criterion_definition_id != criterion_id
            or existing.filename != safe_filename
            or existing.sha256 != digest
        ):
            raise AppError(
                409,
                "ASSESSMENT_ARTIFACT_IDEMPOTENCY_CONFLICT",
                "The artifact key belongs to a different upload.",
            )
        return existing
    execution = db.get(AssessmentExecution, execution_id)
    if execution is None:
        raise AppError(
            404, "ASSESSMENT_EXECUTION_NOT_FOUND", "The assessment execution does not exist."
        )
    session = db.get(LearningSession, execution.session_id)
    if session is None or not assessment_session_reviewable(
        assessment_session_facts(session), activity_id=execution.activity_id
    ):
        raise AppError(
            409,
            "ASSESSMENT_SESSION_NOT_FINAL",
            "Artifact capture requires a completed, non-cancelled actual Session.",
        )
    option = selected_task_option(
        db,
        execution.today_suggestion_id,
        execution.unit_definition_id,
        execution.opportunity_id,
        require_eligible=False,
    )
    if criterion_id not in {item["criterionDefinitionId"] for item in option["criteria"]}:
        raise AppError(
            422, "ASSESSMENT_ARTIFACT_SCOPE", "Artifact criterion is outside the selected task."
        )
    artifact = AssessmentArtifact(
        id=new_id(),
        idempotency_key=idempotency_key,
        execution_id=execution.id,
        session_id=execution.session_id,
        unit_definition_id=execution.unit_definition_id,
        opportunity_id=execution.opportunity_id,
        criterion_definition_id=criterion_id,
        filename=safe_filename,
        content_base64=base64.b64encode(content).decode("ascii"),
        sha256=digest,
        size_bytes=len(content),
        capture_method=ASSESSMENT_ARTIFACT_CAPTURE,
        created_at=max(utc_now_ms(), session.ended_at or 0),
    )
    db.add(artifact)
    db.commit()
    return artifact


def _active_review(db: Session, execution_id: str) -> AssessmentReview | None:
    return db.scalar(
        select(AssessmentReview)
        .where(AssessmentReview.execution_id == execution_id)
        .order_by(AssessmentReview.review_sequence.desc())
        .limit(1)
    )


def assessment_execution_detail(db: Session, execution_id: str) -> dict[str, Any]:
    execution = db.get(AssessmentExecution, execution_id)
    if execution is None:
        raise AppError(
            404, "ASSESSMENT_EXECUTION_NOT_FOUND", "The assessment execution does not exist."
        )
    session = db.get(LearningSession, execution.session_id)
    unit = db.get(LearningUnitDefinition, execution.unit_definition_id)
    rubric = db.get(AssessmentRubricDefinition, execution.rubric_definition_id)
    assert session is not None and unit is not None and rubric is not None
    reviews = db.scalars(
        select(AssessmentReview)
        .where(AssessmentReview.execution_id == execution.id)
        .order_by(AssessmentReview.review_sequence)
    ).all()
    artifacts = db.scalars(
        select(AssessmentArtifact)
        .where(AssessmentArtifact.execution_id == execution.id)
        .order_by(AssessmentArtifact.created_at, AssessmentArtifact.id)
    ).all()
    state = db.get(TodaySuggestionCurrentState, execution.today_suggestion_id)
    latest_correction_at = db.scalar(
        select(func.max(SessionCorrection.corrected_at)).where(
            SessionCorrection.session_id == execution.session_id
        )
    )
    review_required = not reviews or (
        latest_correction_at is not None and latest_correction_at >= reviews[-1].reviewed_at
    )
    selected = selected_task_option(
        db, execution.today_suggestion_id, unit.id, execution.opportunity_id, require_eligible=False
    )
    return {
        "id": execution.id,
        "suggestionId": execution.today_suggestion_id,
        "sessionId": execution.session_id,
        "activityId": execution.activity_id,
        "assistanceMode": session.assistance_mode,
        "sessionState": session.timed_state,
        "sessionOutcome": session.outcome,
        "todayStatus": state.status if state is not None else None,
        "reviewRequired": review_required,
        "rubricDefinitionId": rubric.id,
        "rubricTitle": rubric.title,
        "task": selected,
        "artifacts": [
            {
                "id": item.id,
                "criterionDefinitionId": item.criterion_definition_id,
                "filename": item.filename,
                "sizeBytes": item.size_bytes,
                "sha256": item.sha256,
                "captureMethod": item.capture_method,
            }
            for item in artifacts
        ],
        "reviews": [
            {
                "id": item.id,
                "reviewedAt": epoch_ms_to_rfc3339(item.reviewed_at),
                "snapshot": json.loads(item.snapshot_json),
            }
            for item in reviews
        ],
        "latestResult": assessment_review_result(db, reviews[-1]) if reviews else None,
    }


def retract_execution_evidence(db: Session, session_id: str, reason: str) -> None:
    execution = db.scalar(
        select(AssessmentExecution).where(AssessmentExecution.session_id == session_id)
    )
    if execution is None:
        return
    review_ids = list(
        db.scalars(
            select(AssessmentReview.id).where(AssessmentReview.execution_id == execution.id)
        ).all()
    )
    for evidence in (
        db.scalars(
            select(Evidence).where(
                Evidence.source_type == "assessment_review", Evidence.source_id.in_(review_ids)
            )
        ).all()
        if review_ids
        else []
    ):
        if (
            db.scalar(
                select(EvidenceRetraction.id).where(EvidenceRetraction.evidence_id == evidence.id)
            )
            is not None
        ):
            continue
        fact = EvidenceRetraction(evidence_id=evidence.id, reason=reason, actor_kind="system")
        db.add(fact)
        db.flush()
        links = db.scalars(
            select(EvidenceLink).where(EvidenceLink.evidence_id == evidence.id)
        ).all()
        _queue_evidence_invalidations(db, source_fact_id=fact.id, links=links)


def submit_assessment_review(
    db: Session, execution_id: str, payload: AssessmentReviewRequest, reviewer_user_id: str
) -> AssessmentReview:
    command_hash = hashlib.sha256(canonical_json(payload.model_dump()).encode("utf-8")).hexdigest()
    existing = db.scalar(
        select(AssessmentReview).where(AssessmentReview.idempotency_key == payload.idempotency_key)
    )
    if existing is not None:
        snapshot = json.loads(existing.snapshot_json)
        if (
            existing.execution_id != execution_id
            or existing.reviewer_user_id != reviewer_user_id
            or snapshot.get("commandHash") != command_hash
        ):
            raise AppError(
                409,
                "ASSESSMENT_REVIEW_IDEMPOTENCY_CONFLICT",
                "The review key belongs to a different command.",
            )
        return existing
    execution = db.get(AssessmentExecution, execution_id)
    if execution is None:
        raise AppError(
            404, "ASSESSMENT_EXECUTION_NOT_FOUND", "The assessment execution does not exist."
        )
    session = db.get(LearningSession, execution.session_id)
    if session is not None and session.activity_id != execution.activity_id:
        raise AppError(
            409,
            "ASSESSMENT_ACTIVITY_SUPERSEDED",
            "The assessment Activity changed during Session correction; start a new assessment.",
        )
    if session is None or not assessment_session_reviewable(
        assessment_session_facts(session), activity_id=execution.activity_id
    ):
        raise AppError(
            409,
            "ASSESSMENT_SESSION_NOT_FINAL",
            "Review requires a completed, non-cancelled actual Session.",
        )
    suggestion = db.get(TodaySuggestion, execution.today_suggestion_id)
    rubric = db.get(AssessmentRubricDefinition, execution.rubric_definition_id)
    unit = db.get(LearningUnitDefinition, execution.unit_definition_id)
    opportunity = db.get(EvidenceOpportunityDefinition, execution.opportunity_id)
    candidate = (
        load_public_recommendation_item(
            db,
            run_id=execution.recommendation_run_id,
            candidate_id=execution.recommendation_candidate_id,
        )
        if suggestion is not None
        else None
    )
    if (
        suggestion is None
        or candidate is None
        or rubric is None
        or unit is None
        or opportunity is None
        or not assessment_lineage_valid(
            execution=vars(execution),
            suggestion=vars(suggestion),
            candidate={
                **vars(candidate),
                "id": candidate.candidate_id,
                "run_id": execution.recommendation_run_id,
            },
            rubric=vars(rubric),
            unit=vars(unit),
            opportunity=vars(opportunity),
            session=vars(session),
            finalized=True,
        )
    ):
        raise AppError(
            409,
            "ASSESSMENT_LINEAGE_INVALID",
            "The assessment's pinned task and Session lineage is inconsistent.",
        )
    attestation = payload.attestation
    if not all(
        (
            attestation.actual_session,
            attestation.assistance_complete,
            attestation.outputs_authentic,
            attestation.review_truthful,
        )
    ):
        raise AppError(
            422,
            "ASSESSMENT_ATTESTATION_REQUIRED",
            "All first-party review attestations are required.",
        )
    prior = _active_review(db, execution.id)
    if prior is not None and (
        payload.supersedes_review_id != prior.id or not payload.correction_reason
    ):
        raise AppError(
            409,
            "ASSESSMENT_REVIEW_CORRECTION_REQUIRED",
            "A new review must explicitly correct the latest review.",
        )
    if prior is None and payload.supersedes_review_id is not None:
        raise AppError(
            422, "ASSESSMENT_REVIEW_CORRECTION_INVALID", "There is no review to correct."
        )
    option = selected_task_option(
        db,
        execution.today_suggestion_id,
        execution.unit_definition_id,
        execution.opportunity_id,
        now_ms=execution.started_at,
        require_eligible=False,
    )
    covered = {item["criterionDefinitionId"]: item for item in option["criteria"]}
    rows = {item.criterion_definition_id: item for item in payload.observations}
    if len(rows) != len(payload.observations) or set(rows) != set(covered):
        raise AppError(
            422,
            "ASSESSMENT_REVIEW_SCOPE_INVALID",
            "Review rows must match exactly the selected task's criterion scope.",
        )
    review_id = new_id()
    snapshot_rows: list[dict[str, Any]] = []
    for criterion_id, row in rows.items():
        criterion = covered[criterion_id]
        if row.state == "unobserved":
            if row.corroboration is not None:
                raise AppError(
                    422, "ASSESSMENT_ARTIFACT_SCOPE", "Unobserved criteria cannot use an artifact."
                )
            snapshot_rows.append(
                {"criterionDefinitionId": criterion_id, "state": "unobserved", "evidenceId": None}
            )
            continue
        artifact = (
            db.get(AssessmentArtifact, row.corroboration.artifact_id)
            if row.corroboration is not None
            else None
        )
        if row.corroboration is not None and (
            artifact is None
            or not assessment_artifact_valid(vars(artifact), vars(execution), criterion_id)
            or artifact.created_at < (session.ended_at or 0)
        ):
            raise AppError(
                422,
                "ASSESSMENT_ARTIFACT_INVALID",
                "The selected artifact does not belong to this criterion and Session.",
            )
        derived = derive_review_evidence_policy(
            row,
            session_mode=session.assistance_mode,
            requires_artifact=option["requiresArtifact"],
            intended_modes=set(option["intendedIndependenceModes"]),
            intended_strengths=set(option["intendedStrengths"]),
            corroboration_hash=artifact.sha256 if artifact is not None else None,
        )
        confidence = str(derived["confidence"])
        strength = str(derived["strength"])
        effect = str(derived["effect"])
        independence = str(derived["independence"])
        mode = str(derived["effective_mode"])
        artifact_hash = derived["artifact_hash"]
        evidence = Evidence(
            id=new_id(),
            evidence_type="assessment",
            source_type="assessment_review",
            source_id=review_id,
            source_role=f"criterion:{criterion_id}",
            title=f"Assessment: {criterion['criterionStableKey']}",
            description=row.observed_output,
            strength=strength,
            independence=independence,
            source_confidence=confidence,
            occurred_at=session.ended_at,
            provenance_json=canonical_json(
                {
                    "origin_kind": "local",
                    "creator_kind": "user",
                    "reviewer_kind": "self",
                    "reviewer_user_id": reviewer_user_id,
                    "source_record_type": "assessment_review",
                    "source_record_id": review_id,
                    "capture_method": "structured_first_party_review",
                    "policy_version": EVIDENCE_POLICY,
                    "assessment_policy_version": ASSESSMENT_REVIEW_POLICY,
                    "assessment_occurrence_session_id": session.id,
                    "activity_id": execution.activity_id,
                    "execution_id": execution.id,
                    "rubric_definition_id": execution.rubric_definition_id,
                    "unit_definition_id": execution.unit_definition_id,
                    "opportunity_id": execution.opportunity_id,
                    "session_assistance_mode": session.assistance_mode,
                    "additional_assistance_mode": row.additional_assistance_mode,
                    "effective_assistance_mode": mode,
                    "review_state": row.state,
                    "corroboration_id": artifact.id if artifact is not None else None,
                    "corroboration_kind": ASSESSMENT_ARTIFACT_CAPTURE
                    if artifact is not None
                    else None,
                    "source_confidence_assignment": "structured_first_party_medium"
                    if confidence == "medium"
                    else "incomplete_first_party_low",
                }
            ),
            policy_version=EVIDENCE_POLICY,
            schema_version=1,
            artifact_hash=artifact_hash,
            external_reference=row.artifact_reference,
            authoritative_for_downgrade=False,
        )
        db.add(evidence)
        db.flush()
        link = _add_link(
            db,
            evidence,
            EvidenceLinkCreate(
                competency_identity_id=execution.competency_identity_id,
                criterion_identity_id=criterion["criterionIdentityId"],
                criterion_definition_id=criterion_id,
                scale_version_id=criterion["scaleVersionId"],
                dimension_id=criterion["dimensionId"],
                level_id=criterion["levelId"],
                effect=effect,
                relevance="primary",
            ),
            provenance={
                "capture_method": "assessment_review",
                "policy_version": EVIDENCE_POLICY,
                "review_id": review_id,
                "execution_id": execution.id,
            },
        )
        _queue_evidence_invalidations(db, source_fact_id=evidence.id, links=[link])
        snapshot_rows.append(
            {
                "criterionDefinitionId": criterion_id,
                "state": row.state,
                "evidenceId": evidence.id,
                "taskSetup": row.task_setup,
                "expectedResult": row.expected_result,
                "observedOutput": row.observed_output,
                "comparison": row.comparison,
                "artifactContent": row.artifact_content,
                "artifactHash": artifact_hash,
                "corroborationId": artifact.id if artifact is not None else None,
                "artifactReference": row.artifact_reference,
                "additionalAssistanceMode": row.additional_assistance_mode,
                "derivedStrength": strength,
                "derivedIndependence": independence,
                "derivedSourceConfidence": confidence,
            }
        )
    review = AssessmentReview(
        id=review_id,
        execution_id=execution.id,
        idempotency_key=payload.idempotency_key,
        review_sequence=prior.review_sequence + 1 if prior else 1,
        supersedes_review_id=prior.id if prior else None,
        reviewer_user_id=reviewer_user_id,
        reviewer_kind="self",
        policy_version=ASSESSMENT_REVIEW_POLICY,
        snapshot_json=canonical_json(
            {
                "rows": snapshot_rows,
                "attestation": attestation.model_dump(),
                "correctionReason": payload.correction_reason,
                "commandHash": command_hash,
            }
        ),
        reviewed_at=max(
            utc_now_ms(),
            (
                db.scalar(
                    select(func.max(SessionCorrection.corrected_at)).where(
                        SessionCorrection.session_id == session.id
                    )
                )
                or 0
            )
            + 1,
        ),
    )
    db.add(review)
    db.flush()
    if prior is not None:
        for evidence in db.scalars(
            select(Evidence).where(
                Evidence.source_type == "assessment_review", Evidence.source_id == prior.id
            )
        ).all():
            if (
                db.scalar(
                    select(EvidenceRetraction.id).where(
                        EvidenceRetraction.evidence_id == evidence.id
                    )
                )
                is None
            ):
                fact = EvidenceRetraction(
                    evidence_id=evidence.id,
                    reason=payload.correction_reason or "Assessment review corrected.",
                    actor_kind="user",
                )
                db.add(fact)
                db.flush()
                links = db.scalars(
                    select(EvidenceLink).where(EvidenceLink.evidence_id == evidence.id)
                ).all()
                _queue_evidence_invalidations(db, source_fact_id=fact.id, links=links)
    commit_source_and_drain(db)
    return review


def assessment_review_result(db: Session, review: AssessmentReview) -> dict[str, Any]:
    execution = db.get(AssessmentExecution, review.execution_id)
    assert execution is not None
    capability = db.get(CompetencyCapabilityState, (execution.competency_identity_id, "overall"))
    level = (
        db.get(CapabilityScaleLevel, capability.capability_level_id)
        if capability is not None and capability.capability_level_id is not None
        else None
    )
    criterion_results = (
        db.scalars(
            select(CriterionEvaluationResult).where(
                CriterionEvaluationResult.run_id == capability.evaluation_run_id
            )
        ).all()
        if capability is not None
        else []
    )
    criterion_states = []
    for item in criterion_results:
        definition = db.get(CriterionDefinition, item.criterion_definition_id)
        identity = (
            db.get(CriterionIdentity, definition.criterion_identity_id) if definition else None
        )
        if identity is not None:
            criterion_states.append(
                {
                    "criterionDefinitionId": item.criterion_definition_id,
                    "criterionStableKey": identity.stable_key,
                    "state": item.state,
                    "facts": json.loads(item.facts_json),
                }
            )
    analysis = db.scalar(
        select(AnalysisV3CurrentState).where(AnalysisV3CurrentState.purpose == "learning_control")
    )
    snapshot = json.loads(review.snapshot_json)
    for row in snapshot["rows"]:
        if row.get("evidenceId") is not None:
            row["evidenceActive"] = (
                db.scalar(
                    select(EvidenceRetraction.id).where(
                        EvidenceRetraction.evidence_id == row["evidenceId"]
                    )
                )
                is None
            )
    return {
        "id": review.id,
        "executionId": execution.id,
        "policyVersion": review.policy_version,
        "snapshot": snapshot,
        "capability": (
            {
                "assessmentStatus": capability.assessment_status,
                "levelId": capability.capability_level_id,
                "levelKey": level.stable_key if level else None,
                "confidence": capability.aggregate_confidence,
            }
            if capability
            else None
        ),
        "criterionStates": sorted(criterion_states, key=lambda item: item["criterionStableKey"]),
        "analysisStatus": analysis.status if analysis is not None else "not_generated",
    }
