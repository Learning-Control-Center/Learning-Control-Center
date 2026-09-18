from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import AuthContext, get_auth_context, require_csrf
from app.capability import commit_source_and_drain
from app.database import get_db
from app.domain import transition_status
from app.errors import AppError
from app.models import (
    Activity,
    CapabilityScaleDimension,
    CapabilityScaleLevel,
    CapabilityScaleVersion,
    CompetencyIdentity,
    ContributionRetraction,
    CriterionDefinition,
    CriterionIdentity,
    Evidence,
    EvidenceInvalidation,
    EvidenceLink,
    EvidenceLinkRetraction,
    EvidenceRedaction,
    EvidenceRetraction,
    LearningSession,
    ProjectionInvalidation,
    SemanticCompetencyDefinition,
    SessionContribution,
    VerificationEvidence,
    VerificationRecord,
    new_id,
)
from app.schemas import (
    EvidenceCreate,
    EvidenceLifecycleRequest,
    EvidenceLinkCommand,
    EvidenceLinkCreate,
    EvidenceRedactionRequest,
    VerificationCreate,
)
from app.session_views import session_actuality_as_of
from app.time_utils import datetime_to_epoch_ms, epoch_ms_to_rfc3339, utc_now_ms

router = APIRouter(tags=["v2 evidence"])
EVIDENCE_POLICY = "evidence-policy/v1"


def create_project_outcome_evidence(
    db: Session,
    *,
    activity_project_task_link_id: str,
    opportunity_id: str,
    title: str,
    description: str | None,
    occurred_at: int,
    artifact_hash: str | None,
    external_reference: str | None,
    rubric_result: str | None,
    idempotency_key: str,
) -> Evidence:
    """Create unified Evidence for an actual Project task outcome.

    Authored opportunities constrain lineage and intended targets only. Actual strength,
    independence, and confidence are derived by the versioned Evidence policy.
    """
    from app.projects.evidence_policy import (
        PROJECT_EVIDENCE_POLICY,
        derive_project_evidence_characteristics,
    )
    from app.projects.models import (
        ActivityProjectTaskLink,
        ProjectEvidenceOpportunity,
        ProjectTarget,
        ProjectTaskDefinition,
        ProjectVersion,
    )
    from app.projects.service import activity_project_link_is_actual

    command = {
        "activityProjectTaskLinkId": activity_project_task_link_id,
        "opportunityId": opportunity_id,
        "title": title,
        "description": description,
        "occurredAt": occurred_at,
        "artifactHash": artifact_hash,
        "externalReference": external_reference,
        "rubricResult": rubric_result,
    }
    command_digest = hashlib.sha256(_canonical_json(command).encode("utf-8")).hexdigest()

    for candidate in db.scalars(
        select(Evidence).where(Evidence.source_type == "activity_project_task_link")
    ).all():
        candidate_provenance = json.loads(candidate.provenance_json)
        if candidate_provenance.get("idempotency_key") != idempotency_key:
            continue
        if candidate_provenance.get("command_hash") != command_digest:
            raise AppError(409, "IDEMPOTENCY_KEY_REUSED", "The Project Evidence key was reused.")
        return candidate

    link = db.get(ActivityProjectTaskLink, activity_project_task_link_id)
    opportunity = db.get(ProjectEvidenceOpportunity, opportunity_id)
    task = db.get(ProjectTaskDefinition, link.task_definition_id) if link else None
    version = db.get(ProjectVersion, task.project_version_id) if task else None
    if (
        link is None
        or opportunity is None
        or task is None
        or version is None
        or opportunity.project_version_id != version.id
        or (opportunity.task_definition_id not in {None, task.id})
    ):
        raise AppError(
            422,
            "PROJECT_EVIDENCE_LINEAGE_INVALID",
            "Project Evidence must reference an actual task link and compatible opportunity.",
        )
    activity = db.get(Activity, link.activity_id)
    actual_at = (
        activity.context_ended_at
        if activity is not None and activity.context_ended_at is not None
        else activity.occurred_at
        if activity is not None and activity.occurred_at is not None
        else activity.created_at
        if activity is not None
        else None
    )
    if (
        not activity_project_link_is_actual(db, link, utc_now_ms() + 1)
        or actual_at is None
        or occurred_at < actual_at
        or occurred_at > utc_now_ms()
    ):
        raise AppError(
            422,
            "PROJECT_EVIDENCE_ACTUALITY_INVALID",
            "Project Evidence must follow an active actual Activity link.",
        )
    existing = db.scalar(
        select(Evidence).where(
            Evidence.source_type == "activity_project_task_link",
            Evidence.source_id == link.id,
            Evidence.source_role == opportunity.stable_key,
            Evidence.policy_version == EVIDENCE_POLICY,
        )
    )
    if existing is not None:
        provenance = json.loads(existing.provenance_json)
        if (
            provenance.get("idempotency_key") != idempotency_key
            or provenance.get("command_hash") != command_digest
        ):
            raise AppError(
                409,
                "PROJECT_EVIDENCE_EXISTS",
                "Project Evidence already exists with another command.",
            )
        return existing
    characteristics = json.loads(opportunity.intended_characteristics_json)
    requires_artifact = bool(characteristics.get("requires_artifact"))
    if requires_artifact and artifact_hash is None and external_reference is None:
        raise AppError(
            422,
            "PROJECT_EVIDENCE_ARTIFACT_REQUIRED",
            "This Project Evidence opportunity requires an artifact reference.",
        )
    if rubric_result is not None and opportunity.project_criterion_definition_id is None:
        raise AppError(
            422,
            "PROJECT_EVIDENCE_RUBRIC_SCOPE_INVALID",
            "A rubric result requires a ProjectCriterion-scoped Evidence opportunity.",
        )
    outcome = activity.outcome_classification if activity is not None else None
    has_artifact = artifact_hash is not None or external_reference is not None
    recorded_at = utc_now_ms()
    sessions = db.scalars(
        select(LearningSession).where(LearningSession.activity_id == link.activity_id)
    ).all()
    actual_sessions = [
        item
        for session in sessions
        if (
            item := session_actuality_as_of(
                db, session_id=session.id, exclusive_cutoff_at=recorded_at + 1
            )
        )
        is not None
        and item.ended_at <= occurred_at
    ]
    derived = derive_project_evidence_characteristics(
        activity_outcome=outcome,
        has_artifact=has_artifact,
        assistance_modes=tuple(sorted(item.assistance_mode for item in actual_sessions)),
        attribution_provenance=link.provenance,
        rubric_result=rubric_result,
        has_project_criterion=opportunity.project_criterion_definition_id is not None,
    )
    intended_strengths = set(characteristics.get("intended_strengths", []))
    intended_modes = set(characteristics.get("intended_independence_modes", []))
    if (intended_strengths and derived.strength not in intended_strengths) or (
        intended_modes and derived.independence not in intended_modes
    ):
        raise AppError(
            422,
            "PROJECT_EVIDENCE_CHARACTERISTICS_NOT_QUALIFYING",
            "Actual execution characteristics do not satisfy this Evidence opportunity.",
        )
    targets = db.scalars(
        select(ProjectTarget)
        .where(
            ProjectTarget.project_version_id == version.id,
            (
                (ProjectTarget.task_definition_id == task.id)
                | (
                    (ProjectTarget.task_definition_id.is_(None))
                    & (ProjectTarget.project_criterion_definition_id.is_(None))
                )
                | (
                    ProjectTarget.project_criterion_definition_id
                    == opportunity.project_criterion_definition_id
                )
            ),
        )
        .order_by(ProjectTarget.order_index, ProjectTarget.id)
    ).all()
    if not targets:
        raise AppError(
            422,
            "PROJECT_EVIDENCE_TARGET_MISSING",
            "Project Evidence requires an exact authored competency target.",
        )
    provenance = {
        "origin_kind": "local",
        "creator_kind": "user",
        "capture_method": "project_application_service",
        "policy_version": EVIDENCE_POLICY,
        "source_record_type": "activity_project_task_link",
        "source_record_id": link.id,
        "project_evidence_policy_version": PROJECT_EVIDENCE_POLICY,
        "project_id": version.project_id,
        "project_version_id": version.id,
        "task_definition_id": task.id,
        "activity_project_task_link_id": link.id,
        "opportunity_id": opportunity.id,
        "project_criterion_definition_id": opportunity.project_criterion_definition_id,
        "idempotency_key": idempotency_key,
        "command_hash": command_digest,
        "derived_characteristics": {
            "activityOutcome": outcome,
            "assistanceModes": sorted(item.assistance_mode for item in actual_sessions),
            "hasArtifact": has_artifact,
            "rubricResult": rubric_result,
        },
    }
    evidence = Evidence(
        evidence_type=opportunity.evidence_kind,
        source_type="activity_project_task_link",
        source_id=link.id,
        source_role=opportunity.stable_key,
        title=title,
        description=description,
        strength=derived.strength,
        strength_unknown_reason=derived.strength_unknown_reason,
        independence=derived.independence,
        independence_unknown_reason=derived.independence_unknown_reason,
        source_confidence=derived.source_confidence,
        source_confidence_unknown_reason=derived.source_confidence_unknown_reason,
        occurred_at=occurred_at,
        occurred_at_unknown_reason=None,
        provenance_json=_canonical_json(provenance),
        policy_version=EVIDENCE_POLICY,
        schema_version=1,
        artifact_hash=artifact_hash,
        external_reference=external_reference,
        authoritative_for_downgrade=False,
        created_at=recorded_at,
    )
    db.add(evidence)
    db.flush()
    links: list[EvidenceLink] = []
    linked_targets: set[tuple[str, str | None, str | None, str, str | None, str | None, str]] = (
        set()
    )
    for target in targets:
        definition = (
            db.get(CriterionDefinition, target.criterion_definition_id)
            if target.criterion_definition_id
            else None
        )
        criterion = (
            db.get(CriterionIdentity, definition.criterion_identity_id) if definition else None
        )
        semantic = db.get(SemanticCompetencyDefinition, target.semantic_definition_id)
        assert semantic is not None
        link_key = (
            semantic.competency_identity_id,
            criterion.id if criterion else None,
            definition.id if definition else None,
            target.scale_version_id,
            target.dimension_id,
            target.level_id,
            target.role,
        )
        if link_key in linked_targets:
            continue
        linked_targets.add(link_key)
        links.append(
            _add_link(
                db,
                evidence,
                EvidenceLinkCreate(
                    competency_identity_id=semantic.competency_identity_id,
                    criterion_identity_id=criterion.id if criterion else None,
                    criterion_definition_id=definition.id if definition else None,
                    scale_version_id=target.scale_version_id,
                    dimension_id=target.dimension_id,
                    level_id=target.level_id,
                    effect="contradicts" if rubric_result == "not_met" else "supports",
                    relevance=target.role,
                ),
                provenance=provenance,
                idempotency_key=f"{idempotency_key}:{target.id}",
            )
        )
    _queue_evidence_invalidations(db, source_fact_id=evidence.id, links=links)
    return evidence


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _command_hash(payload: EvidenceCreate) -> str:
    return hashlib.sha256(
        _canonical_json(payload.model_dump(mode="json")).encode("utf-8")
    ).hexdigest()


def _queue_evidence_invalidations(
    db: Session, *, source_fact_id: str, links: Sequence[EvidenceLink]
) -> None:
    now = utc_now_ms()
    subjects = {(link.competency_identity_id, link.criterion_identity_id) for link in links}
    for competency_id, criterion_id in subjects:
        for projection_kind, subject_type, subject_id, policy in (
            ("criterion_evaluation", "criterion", criterion_id, "criterion-evaluation-policy/v1"),
            ("capability", "competency", competency_id, "capability-policy/v1"),
            ("review", "competency", competency_id, "freshness-policy/v1"),
            ("analysis", "competency", competency_id, "analysis-policy/v1"),
        ):
            if subject_id is None:
                continue
            db.add(
                ProjectionInvalidation(
                    projection_kind=projection_kind,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    source_fact_id=source_fact_id,
                    target_policy_version=policy,
                    status="pending",
                    attempt_count=0,
                    requested_at=now,
                )
            )


def invalidate_project_source_evidence(
    db: Session, *, activity_project_task_link_id: str, reason: str
) -> list[str]:
    """Invalidate Evidence whose actual Project source attribution was corrected."""
    invalidated: list[str] = []
    items = db.scalars(
        select(Evidence).where(
            Evidence.source_type == "activity_project_task_link",
            Evidence.source_id == activity_project_task_link_id,
        )
    ).all()
    for item in items:
        existing = db.scalar(
            select(EvidenceInvalidation.id).where(EvidenceInvalidation.evidence_id == item.id)
        )
        if existing is not None:
            continue
        fact = EvidenceInvalidation(
            evidence_id=item.id,
            reason=reason,
            actor_kind="system",
        )
        db.add(fact)
        db.flush()
        links = db.scalars(select(EvidenceLink).where(EvidenceLink.evidence_id == item.id)).all()
        _queue_evidence_invalidations(db, source_fact_id=fact.id, links=links)
        invalidated.append(item.id)
    return invalidated


def _validate_link(db: Session, payload: EvidenceLinkCreate) -> None:
    competency = db.get(CompetencyIdentity, payload.competency_identity_id)
    criterion = (
        db.get(CriterionIdentity, payload.criterion_identity_id)
        if payload.criterion_identity_id
        else None
    )
    definition = (
        db.get(CriterionDefinition, payload.criterion_definition_id)
        if payload.criterion_definition_id
        else None
    )
    scale = (
        db.get(CapabilityScaleVersion, payload.scale_version_id)
        if payload.scale_version_id
        else None
    )
    dimension = (
        db.get(CapabilityScaleDimension, payload.dimension_id) if payload.dimension_id else None
    )
    level = db.get(CapabilityScaleLevel, payload.level_id) if payload.level_id else None
    invalid = competency is None
    invalid = invalid or bool(
        payload.criterion_identity_id
        and (
            criterion is None or criterion.competency_identity_id != payload.competency_identity_id
        )
    )
    invalid = invalid or bool(
        payload.criterion_definition_id
        and (
            payload.criterion_identity_id is None
            or definition is None
            or definition.criterion_identity_id != payload.criterion_identity_id
            or payload.level_id != definition.level_id
            or payload.dimension_id != definition.dimension_id
            or level is None
            or payload.scale_version_id != level.scale_version_id
        )
    )
    invalid = invalid or bool(payload.scale_version_id and scale is None)
    invalid = invalid or bool(
        payload.dimension_id
        and (
            payload.scale_version_id is None
            or dimension is None
            or dimension.scale_version_id != payload.scale_version_id
        )
    )
    invalid = invalid or bool(
        payload.level_id
        and (
            payload.scale_version_id is None
            or level is None
            or level.scale_version_id != payload.scale_version_id
        )
    )
    if invalid:
        raise AppError(422, "EVIDENCE_LINK_INVALID", "Evidence link scope is inconsistent.")


def _add_link(
    db: Session,
    evidence: Evidence,
    payload: EvidenceLinkCreate,
    *,
    provenance: dict[str, Any],
    source_contribution_id: str | None = None,
    idempotency_key: str | None = None,
    link_id: str | None = None,
) -> EvidenceLink:
    _validate_link(db, payload)
    existing = db.scalar(
        select(EvidenceLink)
        .outerjoin(
            EvidenceLinkRetraction,
            EvidenceLinkRetraction.evidence_link_id == EvidenceLink.id,
        )
        .where(
            EvidenceLink.evidence_id == evidence.id,
            EvidenceLink.source_contribution_id == source_contribution_id,
            EvidenceLink.competency_identity_id == payload.competency_identity_id,
            EvidenceLink.criterion_identity_id == payload.criterion_identity_id,
            EvidenceLink.criterion_definition_id == payload.criterion_definition_id,
            EvidenceLink.scale_version_id == payload.scale_version_id,
            EvidenceLink.dimension_id == payload.dimension_id,
            EvidenceLink.level_id == payload.level_id,
            EvidenceLink.effect == payload.effect,
            EvidenceLink.relevance == payload.relevance,
            EvidenceLinkRetraction.id.is_(None),
        )
    )
    if existing is not None:
        raise AppError(409, "EVIDENCE_LINK_EXISTS", "The active EvidenceLink already exists.")
    link = EvidenceLink(
        id=link_id or new_id(),
        evidence_id=evidence.id,
        source_contribution_id=source_contribution_id,
        idempotency_key=idempotency_key,
        competency_identity_id=payload.competency_identity_id,
        criterion_identity_id=payload.criterion_identity_id,
        criterion_definition_id=payload.criterion_definition_id,
        scale_version_id=payload.scale_version_id,
        dimension_id=payload.dimension_id,
        level_id=payload.level_id,
        effect=payload.effect,
        relevance=payload.relevance,
        provenance_json=_canonical_json({**provenance, "evidence_id": evidence.id}),
    )
    db.add(link)
    db.flush()
    return link


def create_verification_with_evidence(
    db: Session,
    payload: VerificationCreate,
    *,
    origin_kind: str,
    lifecycle_source: str,
    lifecycle_reason_prefix: str = "Verification result",
    import_package_id: str | None = None,
) -> tuple[VerificationRecord, str]:
    record = VerificationRecord(
        competency_identity_id=payload.competency_identity_id,
        verification_source=payload.verification_source,
        method=payload.method,
        result=payload.result,
        confidence=payload.confidence,
        reviewer_label=payload.reviewer_label,
        evidence_summary=payload.evidence_summary,
        notes=payload.notes,
    )
    db.add(record)
    db.flush()
    attachments: list[VerificationEvidence] = []
    for item in payload.evidence:
        attachment = VerificationEvidence(
            verification_record_id=record.id,
            kind=item.kind,
            reference=item.reference,
            description=item.description,
        )
        db.add(attachment)
        db.flush()
        attachments.append(attachment)
    provenance_base = {
        "origin_kind": origin_kind,
        "creator_kind": "user" if origin_kind == "local" else "import",
        "capture_method": "verification_application_service",
        "policy_version": EVIDENCE_POLICY,
        "import_package_id": import_package_id,
    }
    result_evidence = Evidence(
        evidence_type="verification",
        source_type="verification_record",
        source_id=record.id,
        source_role="result",
        title=f"Verification: {record.method}",
        description=record.evidence_summary,
        strength="unknown",
        strength_unknown_reason="source_policy_unspecified",
        independence="unknown",
        independence_unknown_reason="source_policy_unspecified",
        source_confidence="unknown",
        source_confidence_unknown_reason="source_policy_unspecified",
        occurred_at=None,
        occurred_at_unknown_reason="source_unspecified",
        provenance_json=_canonical_json(
            {
                **provenance_base,
                "source_record_type": "verification_record",
                "source_record_id": record.id,
                "original_confidence": record.confidence,
                "result": record.result,
            }
        ),
        policy_version=EVIDENCE_POLICY,
        schema_version=1,
        authoritative_for_downgrade=False,
    )
    db.add(result_evidence)
    db.flush()
    result_link = _add_link(
        db,
        result_evidence,
        EvidenceLinkCreate(
            competency_identity_id=record.competency_identity_id,
            effect="contradicts" if record.result == "failed" else "supports",
            relevance="primary",
        ),
        provenance={**provenance_base, "verification_result": record.result},
    )
    _queue_evidence_invalidations(db, source_fact_id=result_evidence.id, links=[result_link])
    for attachment in attachments:
        context = Evidence(
            evidence_type="verification",
            source_type="verification_evidence",
            source_id=attachment.id,
            source_role="context",
            title=f"Verification attachment: {attachment.kind}",
            description=attachment.description,
            strength="unknown",
            strength_unknown_reason="source_policy_unspecified",
            independence="unknown",
            independence_unknown_reason="source_policy_unspecified",
            source_confidence="unknown",
            source_confidence_unknown_reason="source_policy_unspecified",
            occurred_at=None,
            occurred_at_unknown_reason="source_unspecified",
            provenance_json=_canonical_json(
                {
                    **provenance_base,
                    "source_record_type": "verification_evidence",
                    "source_record_id": attachment.id,
                    "verification_record_id": record.id,
                    "kind": attachment.kind,
                }
            ),
            policy_version=EVIDENCE_POLICY,
            schema_version=1,
            external_reference=attachment.reference,
            authoritative_for_downgrade=False,
        )
        db.add(context)
        db.flush()
        context_link = _add_link(
            db,
            context,
            EvidenceLinkCreate(
                competency_identity_id=record.competency_identity_id,
                effect="context_only",
                relevance="supporting",
            ),
            provenance={**provenance_base, "verification_record_id": record.id},
        )
        _queue_evidence_invalidations(db, source_fact_id=context.id, links=[context_link])
    status = {"passed": "verified", "partial": "practicing", "failed": "needs_review"}[
        payload.result
    ]
    transition_status(
        db,
        payload.competency_identity_id,
        status,
        reason=f"{lifecycle_reason_prefix}: {payload.result}",
        source=lifecycle_source,
        verification_record_id=record.id if payload.result == "passed" else None,
    )
    return record, status


def qualifying_session(item: LearningSession) -> bool:
    return bool(
        item.duration_ms is not None
        and item.duration_ms > 0
        and item.outcome in {"completed", "partial"}
        and item.timed_state != "cancelled"
        and item.tombstoned_at is None
    )


def create_session_evidence(
    db: Session,
    item: LearningSession,
    *,
    source_role: str = "session_result",
    supersedes_evidence_id: str | None = None,
) -> Evidence | None:
    if not qualifying_session(item):
        return None
    existing = db.scalar(
        select(Evidence).where(
            Evidence.source_type == "learning_session",
            Evidence.source_id == item.id,
            Evidence.source_role == source_role,
            Evidence.policy_version == EVIDENCE_POLICY,
        )
    )
    if existing is not None:
        return existing
    performance = item.activity_type in {
        "practice",
        "coding",
        "debugging",
        "project",
        "review",
        "verification",
    }
    independence = (
        {
            "none": "independent",
            "docs_only": "independent",
            "ai_hint": "assisted",
            "ai_assisted": "assisted",
            "agent_led": "guided",
        }[item.assistance_mode]
        if performance
        else "not_applicable"
    )
    evidence = Evidence(
        evidence_type="session",
        source_type="learning_session",
        source_id=item.id,
        source_role=source_role,
        title=f"{item.activity_type.replace('_', ' ').title()} session",
        strength="unknown",
        strength_unknown_reason="source_policy_unspecified",
        independence=independence,
        source_confidence="unknown",
        source_confidence_unknown_reason="source_policy_unspecified",
        occurred_at=item.started_at,
        created_at=utc_now_ms(),
        provenance_json=_canonical_json(
            {
                "origin_kind": "local",
                "creator_kind": "system",
                "source_record_type": "learning_session",
                "source_record_id": item.id,
                "capture_method": "session_evidence_normalization",
                "policy_version": EVIDENCE_POLICY,
                "activity_id": item.activity_id,
                "activity_type": item.activity_type,
                "assistance_mode": item.assistance_mode,
                "outcome": item.outcome,
                "duration_ms": item.duration_ms,
            }
        ),
        policy_version=EVIDENCE_POLICY,
        schema_version=1,
        supersedes_evidence_id=supersedes_evidence_id,
        authoritative_for_downgrade=False,
    )
    db.add(evidence)
    db.flush()
    contributions = db.scalars(
        select(SessionContribution)
        .outerjoin(
            ContributionRetraction,
            ContributionRetraction.contribution_id == SessionContribution.id,
        )
        .where(
            SessionContribution.session_id == item.id,
            ContributionRetraction.id.is_(None),
        )
    ).all()
    links = [
        create_session_contribution_link(db, evidence, contribution)
        for contribution in contributions
    ]
    _queue_evidence_invalidations(db, source_fact_id=evidence.id, links=links)
    return evidence


def create_session_contribution_link(
    db: Session, evidence: Evidence, contribution: SessionContribution
) -> EvidenceLink:
    existing = db.scalar(
        select(EvidenceLink).where(
            EvidenceLink.evidence_id == evidence.id,
            EvidenceLink.source_contribution_id == contribution.id,
        )
    )
    if existing is not None:
        return existing
    return _add_link(
        db,
        evidence,
        EvidenceLinkCreate(
            competency_identity_id=contribution.competency_identity_id,
            criterion_identity_id=contribution.criterion_identity_id,
            effect="supports",
            relevance=contribution.relevance,
        ),
        source_contribution_id=contribution.id,
        provenance={
            "capture_method": "session_contribution_normalization",
            "policy_version": EVIDENCE_POLICY,
            "session_contribution_id": contribution.id,
        },
    )


def active_session_evidence(db: Session, session_id: str) -> list[Evidence]:
    return list(
        db.scalars(
            select(Evidence)
            .outerjoin(EvidenceRetraction, EvidenceRetraction.evidence_id == Evidence.id)
            .outerjoin(EvidenceInvalidation, EvidenceInvalidation.evidence_id == Evidence.id)
            .where(
                Evidence.source_type == "learning_session",
                Evidence.source_id == session_id,
                EvidenceRetraction.id.is_(None),
                EvidenceInvalidation.id.is_(None),
            )
        ).all()
    )


def retract_session_evidence(db: Session, session_id: str, reason: str) -> None:
    for evidence in active_session_evidence(db, session_id):
        retraction = EvidenceRetraction(
            evidence_id=evidence.id,
            reason=reason,
            actor_kind="system",
        )
        db.add(retraction)
        db.flush()
        links = db.scalars(
            select(EvidenceLink).where(EvidenceLink.evidence_id == evidence.id)
        ).all()
        _queue_evidence_invalidations(db, source_fact_id=retraction.id, links=links)


def replace_session_evidence(
    db: Session, item: LearningSession, *, source_role: str, reason: str
) -> Evidence | None:
    previous = active_session_evidence(db, item.id)
    supersedes_id = (
        max(previous, key=lambda evidence: (evidence.created_at, evidence.id)).id
        if previous
        else None
    )
    replacement = create_session_evidence(
        db,
        item,
        source_role=source_role,
        supersedes_evidence_id=supersedes_id,
    )
    for evidence in previous:
        retraction = EvidenceRetraction(
            evidence_id=evidence.id,
            replacement_evidence_id=replacement.id if replacement else None,
            reason=reason,
            actor_kind="system",
        )
        db.add(retraction)
        db.flush()
        links = db.scalars(
            select(EvidenceLink).where(EvidenceLink.evidence_id == evidence.id)
        ).all()
        _queue_evidence_invalidations(db, source_fact_id=retraction.id, links=links)
    return replacement


def link_contribution_to_session_evidence(
    db: Session, session_id: str, contribution: SessionContribution
) -> None:
    for evidence in active_session_evidence(db, session_id):
        link = create_session_contribution_link(db, evidence, contribution)
        _queue_evidence_invalidations(db, source_fact_id=link.id, links=[link])


def retract_contribution_evidence_links(
    db: Session,
    contribution: SessionContribution,
    replacement: SessionContribution | None = None,
) -> None:
    links = db.scalars(
        select(EvidenceLink)
        .outerjoin(
            EvidenceLinkRetraction,
            EvidenceLinkRetraction.evidence_link_id == EvidenceLink.id,
        )
        .where(
            EvidenceLink.source_contribution_id == contribution.id,
            EvidenceLinkRetraction.id.is_(None),
        )
    ).all()
    for link in links:
        evidence = db.get(Evidence, link.evidence_id)
        if evidence is None:
            raise AppError(409, "EVIDENCE_LINK_SOURCE_MISSING", "Evidence source is missing.")
        replacement_link = (
            create_session_contribution_link(db, evidence, replacement)
            if replacement is not None
            else None
        )
        fact = EvidenceLinkRetraction(
            evidence_link_id=link.id,
            replacement_link_id=replacement_link.id if replacement_link else None,
            reason="Session contribution attribution was corrected.",
            actor_kind="system",
        )
        db.add(fact)
        db.flush()
        _queue_evidence_invalidations(db, source_fact_id=fact.id, links=[link])


def _serialize_evidence(db: Session, item: Evidence) -> dict[str, Any]:
    redaction = db.scalar(select(EvidenceRedaction).where(EvidenceRedaction.evidence_id == item.id))
    redacted_fields = set(json.loads(redaction.redacted_fields_json)) if redaction else set()
    links = db.scalars(select(EvidenceLink).where(EvidenceLink.evidence_id == item.id)).all()
    link_retractions = {
        retraction.evidence_link_id: retraction
        for retraction in db.scalars(
            select(EvidenceLinkRetraction).where(
                EvidenceLinkRetraction.evidence_link_id.in_([link.id for link in links])
            )
        ).all()
        if links
    }
    retraction = db.scalar(
        select(EvidenceRetraction).where(EvidenceRetraction.evidence_id == item.id)
    )
    invalidation = db.scalar(
        select(EvidenceInvalidation).where(EvidenceInvalidation.evidence_id == item.id)
    )
    return {
        "id": item.id,
        "evidenceType": item.evidence_type,
        "sourceType": item.source_type,
        "sourceId": item.source_id,
        "sourceRole": item.source_role,
        "title": item.title,
        "description": None if "description" in redacted_fields else item.description,
        "strength": item.strength,
        "strengthUnknownReason": item.strength_unknown_reason,
        "independence": item.independence,
        "independenceUnknownReason": item.independence_unknown_reason,
        "sourceConfidence": item.source_confidence,
        "sourceConfidenceUnknownReason": item.source_confidence_unknown_reason,
        "occurredAt": epoch_ms_to_rfc3339(item.occurred_at)
        if item.occurred_at is not None
        else None,
        "occurredAtUnknownReason": item.occurred_at_unknown_reason,
        "createdAt": epoch_ms_to_rfc3339(item.created_at),
        "policyVersion": item.policy_version,
        "schemaVersion": item.schema_version,
        "artifactHash": item.artifact_hash,
        "externalReference": None
        if "external_reference" in redacted_fields
        else item.external_reference,
        "supersedesEvidenceId": item.supersedes_evidence_id,
        "authoritativeForDowngrade": item.authoritative_for_downgrade,
        "provenance": json.loads(item.provenance_json),
        "retracted": retraction is not None,
        "invalidated": invalidation is not None,
        "redacted": redaction is not None,
        "retraction": (
            {
                "replacementEvidenceId": retraction.replacement_evidence_id,
                "reason": retraction.reason,
                "actorKind": retraction.actor_kind,
                "createdAt": epoch_ms_to_rfc3339(retraction.created_at),
            }
            if retraction
            else None
        ),
        "invalidation": (
            {
                "reason": invalidation.reason,
                "actorKind": invalidation.actor_kind,
                "createdAt": epoch_ms_to_rfc3339(invalidation.created_at),
            }
            if invalidation
            else None
        ),
        "redaction": (
            {
                "fields": sorted(redacted_fields),
                "reason": redaction.reason,
                "actorKind": redaction.actor_kind,
                "effect": redaction.effect,
                "createdAt": epoch_ms_to_rfc3339(redaction.created_at),
            }
            if redaction
            else None
        ),
        "links": [
            {
                "id": link.id,
                "competencyIdentityId": link.competency_identity_id,
                "criterionIdentityId": link.criterion_identity_id,
                "criterionDefinitionId": link.criterion_definition_id,
                "scaleVersionId": link.scale_version_id,
                "dimensionId": link.dimension_id,
                "levelId": link.level_id,
                "effect": link.effect,
                "relevance": link.relevance,
                "retracted": link.id in link_retractions,
                "retraction": (
                    {
                        "replacementLinkId": link_retractions[link.id].replacement_link_id,
                        "reason": link_retractions[link.id].reason,
                        "actorKind": link_retractions[link.id].actor_kind,
                        "createdAt": epoch_ms_to_rfc3339(link_retractions[link.id].created_at),
                    }
                    if link.id in link_retractions
                    else None
                ),
            }
            for link in links
        ],
    }


def _create_native_evidence(
    db: Session, payload: EvidenceCreate, supersedes_evidence_id: str | None = None
) -> Evidence:
    if payload.evidence_type == "project":
        raise AppError(
            422, "FEATURE_NOT_AVAILABLE", "Project Evidence is unavailable until Project exists."
        )
    existing = db.scalar(
        select(Evidence).where(
            Evidence.source_type == "native_evidence_command",
            Evidence.source_id == payload.idempotency_key,
            Evidence.source_role == "fact",
            Evidence.policy_version == EVIDENCE_POLICY,
        )
    )
    if existing is not None:
        provenance = json.loads(existing.provenance_json)
        if provenance.get("command_hash") != _command_hash(payload):
            raise AppError(
                409,
                "EVIDENCE_IDEMPOTENCY_CONFLICT",
                "The idempotency key has already been used for another Evidence fact.",
            )
        return existing
    for link in payload.links:
        _validate_link(db, link)
    rubric_references: dict[str, str] = {}
    if payload.authoritative_reassessment:
        declared_level = db.get(CapabilityScaleLevel, payload.maximum_supported_level_id)
        definitions = [
            db.get(CriterionDefinition, link.criterion_definition_id) for link in payload.links
        ]
        linked_levels = [
            db.get(CapabilityScaleLevel, definition.level_id)
            for definition in definitions
            if definition is not None
        ]
        valid_authoritative = (
            payload.evidence_type == "assessment"
            and payload.strength == "strong"
            and payload.independence == "independent"
            and payload.capture_method == "explicit_authoritative_reassessment"
            and declared_level is not None
            and len(definitions) == len(payload.links)
            and len(linked_levels) == len(payload.links)
            and all(link.effect == "contradicts" for link in payload.links)
            and all(
                definition is not None and definition.verification_rubric is not None
                for definition in definitions
            )
            and all(
                level is not None
                and level.scale_version_id == declared_level.scale_version_id
                and level.ordinal_rank > declared_level.ordinal_rank
                for level in linked_levels
            )
        )
        if not valid_authoritative:
            raise AppError(
                422,
                "AUTHORITATIVE_REASSESSMENT_INVALID",
                "Authoritative reassessment requires a strong independent assessment, "
                "rubric-bound contradiction links, and a lower maximum supported level.",
            )
        rubric_references = {
            definition.id: definition.verification_rubric
            for definition in definitions
            if definition is not None and definition.verification_rubric is not None
        }
    evidence = Evidence(
        evidence_type=payload.evidence_type,
        source_type="native_evidence_command",
        source_id=payload.idempotency_key,
        source_role="fact",
        title=payload.title,
        description=payload.description,
        strength=payload.strength,
        strength_unknown_reason=payload.strength_unknown_reason,
        independence=payload.independence,
        independence_unknown_reason=payload.independence_unknown_reason,
        source_confidence="high" if payload.authoritative_reassessment else "low",
        occurred_at=(
            datetime_to_epoch_ms(payload.occurred_at) if payload.occurred_at is not None else None
        ),
        occurred_at_unknown_reason=payload.occurred_at_unknown_reason,
        provenance_json=_canonical_json(
            {
                "origin_kind": "local",
                "creator_kind": "user",
                "source_record_type": "native_evidence_command",
                "source_record_id": payload.idempotency_key,
                "capture_method": payload.capture_method,
                "policy_version": EVIDENCE_POLICY,
                "source_confidence_assignment": (
                    "explicit_authoritative_reassessment_high"
                    if payload.authoritative_reassessment
                    else "user_evidence_defaults_low"
                ),
                "downgrade_authority": (
                    "local_user_confirmed" if payload.authoritative_reassessment else None
                ),
                "maximum_supported_level_id": payload.maximum_supported_level_id,
                "rubric_references": rubric_references,
                "command_hash": _command_hash(payload),
            }
        ),
        policy_version=EVIDENCE_POLICY,
        schema_version=1,
        artifact_hash=payload.artifact_hash,
        external_reference=payload.external_reference,
        supersedes_evidence_id=supersedes_evidence_id,
        authoritative_for_downgrade=payload.authoritative_reassessment,
    )
    db.add(evidence)
    db.flush()
    links = [
        _add_link(
            db,
            evidence,
            link,
            provenance={
                "capture_method": "explicit_user_link",
                "policy_version": EVIDENCE_POLICY,
            },
        )
        for link in payload.links
    ]
    _queue_evidence_invalidations(db, source_fact_id=evidence.id, links=links)
    return evidence


@router.post("/verification-attempts", status_code=201)
async def create_verification_attempt(
    payload: VerificationCreate,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    record, lifecycle_status = create_verification_with_evidence(
        db,
        payload,
        origin_kind="local",
        lifecycle_source="verification",
    )
    result_evidence = db.scalar(
        select(Evidence).where(
            Evidence.source_type == "verification_record",
            Evidence.source_id == record.id,
            Evidence.source_role == "result",
            Evidence.policy_version == EVIDENCE_POLICY,
        )
    )
    assert result_evidence is not None
    commit_source_and_drain(db)
    return {
        "id": record.id,
        "result": record.result,
        "learningLifecycleStatus": lifecycle_status,
        "resultEvidence": _serialize_evidence(db, result_evidence),
    }


@router.post("/evidence", status_code=201)
async def create_evidence(
    payload: EvidenceCreate,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    evidence = _create_native_evidence(db, payload)
    commit_source_and_drain(db)
    return _serialize_evidence(db, evidence)


@router.get("/evidence")
async def list_evidence(
    competency_identity_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    query = select(Evidence).order_by(Evidence.created_at.desc(), Evidence.id)
    if competency_identity_id:
        query = query.join(EvidenceLink).where(
            EvidenceLink.competency_identity_id == competency_identity_id
        )
    items = db.scalars(query.offset(offset).limit(limit)).unique().all()
    return {
        "items": [_serialize_evidence(db, item) for item in items],
        "limit": limit,
        "offset": offset,
    }


@router.get("/evidence/{evidence_id}")
async def get_evidence(
    evidence_id: str,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    evidence = db.get(Evidence, evidence_id)
    if evidence is None:
        raise AppError(404, "EVIDENCE_NOT_FOUND", "The Evidence does not exist.")
    return _serialize_evidence(db, evidence)


@router.post("/evidence/{evidence_id}/supersede", status_code=201)
async def supersede_evidence(
    evidence_id: str,
    payload: EvidenceCreate,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    old = db.get(Evidence, evidence_id)
    if old is None:
        raise AppError(404, "EVIDENCE_NOT_FOUND", "The Evidence does not exist.")
    old_retraction = db.scalar(
        select(EvidenceRetraction).where(EvidenceRetraction.evidence_id == old.id)
    )
    if old_retraction is not None:
        replacement = (
            db.get(Evidence, old_retraction.replacement_evidence_id)
            if old_retraction.replacement_evidence_id
            else None
        )
        if (
            replacement is not None
            and replacement.source_type == "native_evidence_command"
            and replacement.source_id == payload.idempotency_key
        ):
            replay = _create_native_evidence(db, payload, supersedes_evidence_id=old.id)
            return _serialize_evidence(db, replay)
        raise AppError(
            409,
            "EVIDENCE_ALREADY_SUPERSEDED",
            "The Evidence already has a terminal retraction or replacement.",
        )
    replacement = _create_native_evidence(db, payload, supersedes_evidence_id=old.id)
    if replacement.supersedes_evidence_id != old.id:
        raise AppError(409, "EVIDENCE_IDEMPOTENCY_CONFLICT", "Idempotency key has another meaning.")
    if (
        db.scalar(select(EvidenceRetraction.id).where(EvidenceRetraction.evidence_id == old.id))
        is None
    ):
        retraction = EvidenceRetraction(
            evidence_id=old.id,
            replacement_evidence_id=replacement.id,
            reason="Evidence superseded by explicit correction.",
            actor_kind="user",
        )
        db.add(retraction)
        db.flush()
        links = db.scalars(select(EvidenceLink).where(EvidenceLink.evidence_id == old.id)).all()
        _queue_evidence_invalidations(db, source_fact_id=retraction.id, links=links)
    commit_source_and_drain(db)
    return _serialize_evidence(db, replacement)


@router.post("/evidence/{evidence_id}/retract")
async def retract_evidence(
    evidence_id: str,
    payload: EvidenceLifecycleRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    evidence = db.get(Evidence, evidence_id)
    if evidence is None:
        raise AppError(404, "EVIDENCE_NOT_FOUND", "The Evidence does not exist.")
    if (
        db.scalar(
            select(EvidenceRetraction.id).where(EvidenceRetraction.evidence_id == evidence_id)
        )
        is None
    ):
        fact = EvidenceRetraction(evidence_id=evidence_id, reason=payload.reason, actor_kind="user")
        db.add(fact)
        db.flush()
        links = db.scalars(
            select(EvidenceLink).where(EvidenceLink.evidence_id == evidence_id)
        ).all()
        _queue_evidence_invalidations(db, source_fact_id=fact.id, links=links)
        commit_source_and_drain(db)
    return {"retracted": True}


@router.post("/evidence/{evidence_id}/invalidate")
async def invalidate_evidence(
    evidence_id: str,
    payload: EvidenceLifecycleRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    evidence = db.get(Evidence, evidence_id)
    if evidence is None:
        raise AppError(404, "EVIDENCE_NOT_FOUND", "The Evidence does not exist.")
    if (
        db.scalar(
            select(EvidenceInvalidation.id).where(EvidenceInvalidation.evidence_id == evidence_id)
        )
        is None
    ):
        fact = EvidenceInvalidation(
            evidence_id=evidence_id, reason=payload.reason, actor_kind="user"
        )
        db.add(fact)
        db.flush()
        links = db.scalars(
            select(EvidenceLink).where(EvidenceLink.evidence_id == evidence_id)
        ).all()
        _queue_evidence_invalidations(db, source_fact_id=fact.id, links=links)
        commit_source_and_drain(db)
    return {"invalidated": True}


@router.post("/evidence/{evidence_id}/links", status_code=201)
async def link_evidence(
    evidence_id: str,
    payload: EvidenceLinkCommand,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    evidence = db.get(Evidence, evidence_id)
    if evidence is None:
        raise AppError(404, "EVIDENCE_NOT_FOUND", "The Evidence does not exist.")
    existing = db.scalar(
        select(EvidenceLink).where(
            EvidenceLink.evidence_id == evidence_id,
            EvidenceLink.idempotency_key == payload.idempotency_key,
        )
    )
    link_payload = EvidenceLinkCreate.model_validate(
        payload.model_dump(exclude={"idempotency_key"})
    )
    if existing is not None:
        fields = (
            "competency_identity_id",
            "criterion_identity_id",
            "criterion_definition_id",
            "scale_version_id",
            "dimension_id",
            "level_id",
            "effect",
            "relevance",
        )
        if any(getattr(existing, field) != getattr(link_payload, field) for field in fields):
            raise AppError(
                409,
                "EVIDENCE_LINK_IDEMPOTENCY_CONFLICT",
                "The idempotency key has already been used for another EvidenceLink.",
            )
        return {"id": existing.id}
    link = _add_link(
        db,
        evidence,
        link_payload,
        idempotency_key=payload.idempotency_key,
        provenance={"capture_method": "explicit_user_link", "policy_version": EVIDENCE_POLICY},
    )
    _queue_evidence_invalidations(db, source_fact_id=link.id, links=[link])
    commit_source_and_drain(db)
    return {"id": link.id}


@router.post("/evidence-links/{link_id}/retract")
async def retract_evidence_link(
    link_id: str,
    payload: EvidenceLifecycleRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    link = db.get(EvidenceLink, link_id)
    if link is None:
        raise AppError(404, "EVIDENCE_LINK_NOT_FOUND", "The EvidenceLink does not exist.")
    if (
        db.scalar(
            select(EvidenceLinkRetraction.id).where(
                EvidenceLinkRetraction.evidence_link_id == link_id
            )
        )
        is None
    ):
        fact = EvidenceLinkRetraction(
            evidence_link_id=link_id, reason=payload.reason, actor_kind="user"
        )
        db.add(fact)
        db.flush()
        _queue_evidence_invalidations(db, source_fact_id=fact.id, links=[link])
        commit_source_and_drain(db)
    return {"retracted": True}


@router.post("/evidence/{evidence_id}/redact")
async def redact_evidence(
    evidence_id: str,
    payload: EvidenceRedactionRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    evidence = db.get(Evidence, evidence_id)
    if evidence is None:
        raise AppError(404, "EVIDENCE_NOT_FOUND", "The Evidence does not exist.")
    if db.scalar(select(EvidenceRedaction.id).where(EvidenceRedaction.evidence_id == evidence_id)):
        return {"redacted": True}
    fact = EvidenceRedaction(
        evidence_id=evidence_id,
        redacted_fields_json=_canonical_json(sorted(set(payload.redacted_fields))),
        reason=payload.reason,
        actor_kind="user",
        effect="payload_hidden_from_public_reads",
    )
    db.add(fact)
    db.flush()
    links = db.scalars(select(EvidenceLink).where(EvidenceLink.evidence_id == evidence_id)).all()
    _queue_evidence_invalidations(db, source_fact_id=fact.id, links=links)
    commit_source_and_drain(db)
    return {"redacted": True}
