from __future__ import annotations

import copy
import json
from typing import Any, cast

import pytest
from app.capability import (
    EvidenceFact,
    _candidate_level,
    _evaluate_criterion,
    _freshness_thresholds,
    _support_qualifies,
    drain_projection_invalidations,
    evaluate_capability,
)
from app.errors import AppError
from app.import_export import _portable_payload, _validate_portable_payload
from app.models import (
    CapabilityScaleLevel,
    CapabilityScaleVersion,
    CapabilityStateEvent,
    CompetencyCapabilityState,
    CompetencyReviewState,
    CriterionDefinition,
    CriterionIdentity,
    Evidence,
    EvidenceLink,
    ProjectionInvalidation,
    SemanticCompetencyDefinition,
)
from app.time_utils import epoch_ms_to_rfc3339, utc_now_ms
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _fact(
    key: str,
    *,
    strength: str = "moderate",
    independence: str = "independent",
    confidence: str = "high",
    effect: str = "supports",
    occurred_at: int = 1_780_000_000_000,
    context: str | None = None,
    evidence_type: str = "assessment",
) -> EvidenceFact:
    evidence = Evidence(
        id=f"e-{key}",
        evidence_type=evidence_type,
        source_type="test",
        source_id=key,
        source_role="fact",
        title=key,
        strength=strength,
        strength_unknown_reason="legacy_unspecified" if strength == "unknown" else None,
        independence=independence,
        independence_unknown_reason="legacy_unspecified" if independence == "unknown" else None,
        source_confidence=confidence,
        source_confidence_unknown_reason=(
            "legacy_unspecified" if confidence == "unknown" else None
        ),
        occurred_at=occurred_at,
        created_at=occurred_at,
        provenance_json=json.dumps({"context_id": context} if context else {}),
        policy_version="evidence-policy/v1",
        schema_version=1,
        authoritative_for_downgrade=False,
    )
    link = EvidenceLink(
        id=f"l-{key}",
        evidence_id=evidence.id,
        competency_identity_id="competency",
        criterion_identity_id="criterion",
        criterion_definition_id="definition",
        scale_version_id="scale",
        level_id="level",
        effect=effect,
        relevance="primary",
        provenance_json="{}",
        created_at=occurred_at,
    )
    return EvidenceFact(evidence, link, f"test:{key}:{occurred_at}", context or "legacy_unknown")


@pytest.mark.parametrize(
    ("rule", "facts", "expected"),
    [
        ("exposure", [_fact("weak", strength="weak", independence="unknown")], True),
        ("guided_performance", [_fact("guided", independence="guided")], True),
        ("independent_performance", [_fact("strong", strength="strong")], True),
        (
            "independent_performance",
            [_fact("m1"), _fact("m2", occurred_at=1_780_000_000_001)],
            True,
        ),
        (
            "repeated_independent_performance",
            [
                _fact("s1", strength="strong", context="one"),
                _fact("s2", strength="strong", context="two"),
            ],
            True,
        ),
        (
            "repeated_independent_performance",
            [
                _fact("s3", strength="strong", context="one"),
                _fact("m3", context="one"),
                _fact("m4", context="two"),
            ],
            True,
        ),
        ("authoritative_assessment", [_fact("authority", strength="strong")], True),
    ],
)
def test_every_criterion_demonstration_rule(
    rule: str, facts: list[EvidenceFact], expected: bool
) -> None:
    definition = _criterion(rule)
    definition.verification_rubric = "test-rubric"
    if rule == "authoritative_assessment":
        facts[0].evidence.provenance_json = json.dumps(
            {
                "rubric_criterion_definition_id": definition.id,
                "rubric_reference": definition.verification_rubric,
            }
        )
    assert _support_qualifies(rule, facts, definition)[0] is expected


def _criterion(rule: str) -> CriterionDefinition:
    return CriterionDefinition(
        id="definition",
        criterion_identity_id="criterion",
        semantic_definition_id="semantic",
        definition_version=1,
        level_id="level",
        requirement_type="required",
        demonstration_rule_json=json.dumps({"rule": rule}),
        description="Criterion",
        created_at=1,
    )


def test_all_five_criterion_states_and_occurrence_context_rules() -> None:
    criterion = _criterion("independent_performance")
    assert _evaluate_criterion(criterion, []).state == "unknown"
    attempt = _fact("attempt", strength="weak", independence="guided")
    assert _evaluate_criterion(criterion, [attempt]).state == "partially_demonstrated"
    failed = _fact("failed", strength="weak", independence="guided", effect="contradicts")
    assert _evaluate_criterion(criterion, [failed]).state == "not_demonstrated"
    support = _fact("pass", strength="strong")
    assert _evaluate_criterion(criterion, [support]).state == "demonstrated"
    assert support.evidence.occurred_at is not None
    contradiction = _fact(
        "contradiction", effect="contradicts", occurred_at=support.evidence.occurred_at + 1
    )
    assert _evaluate_criterion(criterion, [support, contradiction]).state == "contradicted"

    moderate_support = _fact("same-moderate")
    same_occurrence = [
        EvidenceFact(moderate_support.evidence, moderate_support.link, "same", "one"),
        EvidenceFact(moderate_support.evidence, moderate_support.link, "same", "one"),
    ]
    assert not _support_qualifies("independent_performance", same_occurrence)[0]
    missing_context = [_fact("cx1", strength="strong"), _fact("cx2", strength="strong")]
    assert not _support_qualifies("repeated_independent_performance", missing_context)[0]


def test_freshness_policy_boundaries_are_exact() -> None:
    assert _freshness_thresholds("technical", "unexposed", None) == (None, None)
    assert _freshness_thresholds("technical", "independent", None) == (45, 90)
    assert _freshness_thresholds("technical", "advanced", None) == (90, 180)
    assert _freshness_thresholds("cefr", "b2", "speaking") == (14, 45)
    assert _freshness_thresholds("cefr", "b2", "reading") == (21, 60)
    assert _freshness_thresholds("cefr", "b2", "grammar") == (30, 90)
    assert _freshness_thresholds("cefr", "b2", None) == (30, 90)


def test_technical_unexposed_requires_an_explicit_assessment() -> None:
    scale = CapabilityScaleVersion(
        id="scale",
        scale_stable_key="technical",
        scale_version="v1",
        display_name="Technical",
        description="",
        created_at=1,
    )
    level = CapabilityScaleLevel(
        id="level",
        scale_version_id=scale.id,
        stable_key="unexposed",
        ordinal_rank=0,
        display_label="Unexposed",
        description="",
        criterion_policy_reference="criterion-evaluation-policy/v1",
        evidence_policy_reference="evidence-policy/v1",
    )
    criterion = _criterion("exposure")
    evidence = _fact("explicit-unexposed", strength="weak", independence="unknown")
    result = _evaluate_criterion(criterion, [evidence])
    assert _candidate_level(scale, [level], [result], [evidence])[0] is None
    evidence.evidence.provenance_json = json.dumps(
        {"capture_method": "explicit_unexposed_assessment"}
    )
    assert _candidate_level(scale, [level], [result], [evidence])[0] is level


async def _activate_definition(
    client: AsyncClient, csrf: str, competency_id: str, version_key: str = "v1"
) -> tuple[str, str]:
    payload = {
        "title": "Capability test",
        "description": "A capability test definition.",
        "scope": "Bounded test work.",
        "scale_stable_key": "technical",
        "scale_version": "v1",
        "dimension_keys": [],
        "effective_at": "2026-09-09T00:00:00Z",
        "creation_source": "test",
        "criteria": [
            {
                "stable_key": "capability.independent",
                "level_stable_key": "independent",
                "dimension_key": None,
                "requirement_type": "required",
                "demonstration_rule": "independent_performance",
                "description": "Perform independently.",
                "verification_rubric": "Demonstrate the behavior without assistance.",
            }
        ],
    }
    created = await client.post(
        f"/api/v2/competencies/{competency_id}/definitions",
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    activated = await client.post(
        f"/api/v2/competencies/{competency_id}/definitions/{body['id']}/activate",
        json={
            "reason": "Test",
            "source": "test",
            "idempotency_key": f"capability-test-{version_key}",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert activated.status_code == 200, activated.text
    return body["id"], body["criteria"][0]["id"]


def _persist_fact(
    db: Session,
    *,
    key: str,
    competency_id: str,
    criterion: CriterionDefinition,
    effect: str = "supports",
    strength: str = "moderate",
    occurred_at: int,
    context: str,
) -> Evidence:
    evidence = Evidence(
        evidence_type="assessment",
        source_type="capability_test",
        source_id=key,
        source_role="fact",
        title=key,
        strength=strength,
        independence="independent",
        source_confidence="high",
        occurred_at=occurred_at,
        created_at=occurred_at,
        provenance_json=json.dumps(
            {
                "origin_kind": "local",
                "creator_kind": "test",
                "source_record_type": "capability_test",
                "source_record_id": key,
                "capture_method": "test_fixture",
                "policy_version": "evidence-policy/v1",
                "context_id": context,
            }
        ),
        policy_version="evidence-policy/v1",
        schema_version=1,
        authoritative_for_downgrade=False,
    )
    db.add(evidence)
    db.flush()
    identity = db.get(CriterionIdentity, criterion.criterion_identity_id)
    semantic_definition = db.get(SemanticCompetencyDefinition, criterion.semantic_definition_id)
    assert identity is not None
    assert semantic_definition is not None
    db.add(
        EvidenceLink(
            evidence_id=evidence.id,
            competency_identity_id=competency_id,
            criterion_identity_id=identity.id,
            criterion_definition_id=criterion.id,
            scale_version_id=semantic_definition.scale_version_id,
            level_id=criterion.level_id,
            effect=effect,
            relevance="primary",
            provenance_json=json.dumps(
                {
                    "capture_method": "test_fixture",
                    "policy_version": "evidence-policy/v1",
                    "evidence_id": evidence.id,
                }
            ),
            created_at=occurred_at,
        )
    )
    db.flush()
    return evidence


async def test_capability_promotion_confidence_freshness_and_downgrade_hysteresis(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, raw_roadmap = configured_client
    roadmap = cast(dict[str, Any], raw_roadmap)
    competency_id = str(roadmap["phases"][0]["tracks"][0]["competencies"][0]["identityId"])
    definition_id, criterion_id = await _activate_definition(client, csrf, competency_id)
    criterion = db.get(CriterionDefinition, criterion_id)
    assert criterion is not None
    base = utc_now_ms()
    _persist_fact(
        db,
        key="support-one",
        competency_id=competency_id,
        criterion=criterion,
        occurred_at=base,
        context="one",
    )
    _persist_fact(
        db,
        key="support-two",
        competency_id=competency_id,
        criterion=criterion,
        occurred_at=base + 1,
        context="two",
    )
    runs = evaluate_capability(db, competency_id, cutoff_at=base + 2)
    db.commit()
    independent = db.scalar(
        select(CapabilityScaleLevel).where(CapabilityScaleLevel.id == runs[0].selected_level_id)
    )
    assert independent is not None and independent.stable_key == "independent"
    state = db.get(CompetencyCapabilityState, (competency_id, "overall"))
    review = db.get(CompetencyReviewState, (competency_id, "overall"))
    assert state is not None
    assert review is not None
    assert state.aggregate_confidence == "high"
    assert review.freshness == "current"
    historical = evaluate_capability(db, competency_id, cutoff_at=base - 1)
    db.commit()
    assert historical[0].selected_level_id is None
    unchanged = db.get(CompetencyCapabilityState, (competency_id, "overall"))
    assert unchanged is not None
    assert unchanged.capability_level_id == independent.id
    assert unchanged.last_evaluated_at == base + 2

    _persist_fact(
        db,
        key="one-failure",
        competency_id=competency_id,
        criterion=criterion,
        effect="contradicts",
        occurred_at=base + 3,
        context="failure-one",
    )
    evaluate_capability(db, competency_id, cutoff_at=base + 4)
    db.commit()
    one_failure_state = db.get(CompetencyCapabilityState, (competency_id, "overall"))
    one_failure_review = db.get(CompetencyReviewState, (competency_id, "overall"))
    assert one_failure_state is not None
    assert one_failure_review is not None
    assert one_failure_state.capability_level_id == independent.id
    assert one_failure_state.aggregate_confidence == "low"
    assert one_failure_review.review_due is True

    _persist_fact(
        db,
        key="two-failures",
        competency_id=competency_id,
        criterion=criterion,
        effect="contradicts",
        occurred_at=base + 5,
        context="failure-two",
    )
    evaluate_capability(db, competency_id, cutoff_at=base + 6)
    db.commit()
    downgraded = db.get(CompetencyCapabilityState, (competency_id, "overall"))
    assert downgraded is not None
    downgraded_level = db.get(CapabilityScaleLevel, downgraded.capability_level_id)
    assert downgraded_level is not None
    assert downgraded_level.stable_key == "guided"
    downgrade_count = db.scalar(
        select(func.count())
        .select_from(CapabilityStateEvent)
        .where(CapabilityStateEvent.cause_code == "sustained_contradictory_performance")
    )
    evaluate_capability(db, competency_id, cutoff_at=base + 7)
    db.commit()
    repeated_state = db.get(CompetencyCapabilityState, (competency_id, "overall"))
    assert repeated_state is not None
    assert repeated_state.capability_level_id == downgraded_level.id
    assert (
        db.scalar(
            select(func.count())
            .select_from(CapabilityStateEvent)
            .where(CapabilityStateEvent.cause_code == "sustained_contradictory_performance")
        )
        == downgrade_count
    )
    evaluate_capability(db, competency_id, cutoff_at=base + 61 * 86_400_000)
    db.commit()
    stale_state = db.get(CompetencyCapabilityState, (competency_id, "overall"))
    stale_review = db.get(CompetencyReviewState, (competency_id, "overall"))
    assert stale_state is not None and stale_state.capability_level_id == downgraded_level.id
    assert stale_review is not None and stale_review.freshness == "stale"
    assert stale_review.review_due is True
    replacement_definition_id, _replacement_criterion_id = await _activate_definition(
        client, csrf, competency_id, "v2"
    )
    evaluate_capability(db, competency_id, cutoff_at=base + 62 * 86_400_000)
    db.commit()
    replacement_state = db.get(CompetencyCapabilityState, (competency_id, "overall"))
    assert replacement_state is not None
    assert replacement_state.semantic_definition_id == replacement_definition_id
    assert replacement_state.assessment_status == "unknown"
    assert replacement_state.capability_level_id is None
    historical_definitions = set(
        db.scalars(
            select(CapabilityStateEvent.semantic_definition_id).where(
                CapabilityStateEvent.competency_identity_id == competency_id
            )
        ).all()
    )
    assert {definition_id, replacement_definition_id} <= historical_definitions
    assert definition_id == criterion.semantic_definition_id


async def test_definition_activation_invalidations_are_durable_and_drained(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, raw_roadmap = configured_client
    roadmap = cast(dict[str, Any], raw_roadmap)
    competency_id = str(roadmap["phases"][0]["tracks"][0]["competencies"][0]["identityId"])
    await _activate_definition(client, csrf, competency_id)
    pending = db.scalars(
        select(ProjectionInvalidation).where(ProjectionInvalidation.status == "pending")
    ).all()
    assert {item.projection_kind for item in pending} == {
        "analysis",
        "roadmap_projection_v2",
    }
    completed = db.scalars(
        select(ProjectionInvalidation).where(ProjectionInvalidation.status == "completed")
    ).all()
    assert {item.projection_kind for item in completed} >= {
        "criterion_evaluation",
        "capability",
        "review",
    }
    sequences = [item.subject_sequence for item in pending if item.subject_id == competency_id]
    assert all(sequence > 0 for sequence in sequences)
    assert all(item.attempt_run_id for item in completed)
    assert all(item.result_run_id for item in completed)
    recovered = completed[0]
    prior_attempts = recovered.attempt_count
    recovered.status = "running"
    recovered.started_at = 1
    db.commit()
    assert drain_projection_invalidations(db, recover_running=True) == 1
    assert recovered.status == "completed"
    assert recovered.attempt_count == prior_attempts + 1


async def test_explicit_authoritative_reassessment_downgrades_and_drains(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, raw_roadmap = configured_client
    roadmap = cast(dict[str, Any], raw_roadmap)
    competency_id = str(roadmap["phases"][0]["tracks"][0]["competencies"][0]["identityId"])
    _definition_id, criterion_id = await _activate_definition(client, csrf, competency_id)
    criterion = db.get(CriterionDefinition, criterion_id)
    assert criterion is not None
    base = utc_now_ms()
    for offset, context in ((0, "one"), (1, "two")):
        _persist_fact(
            db,
            key=f"authoritative-support-{context}",
            competency_id=competency_id,
            criterion=criterion,
            occurred_at=base + offset,
            context=context,
        )
    evaluate_capability(db, competency_id, cutoff_at=base + 2)
    db.commit()
    semantic = db.get(SemanticCompetencyDefinition, criterion.semantic_definition_id)
    assert semantic is not None
    guided = db.scalar(
        select(CapabilityScaleLevel).where(
            CapabilityScaleLevel.scale_version_id == semantic.scale_version_id,
            CapabilityScaleLevel.stable_key == "guided",
        )
    )
    identity = db.get(CriterionIdentity, criterion.criterion_identity_id)
    assert guided is not None and identity is not None
    response = await client.post(
        "/api/v2/evidence",
        json={
            "idempotency_key": "authoritative-reassessment-1",
            "evidence_type": "assessment",
            "title": "Explicit reassessment",
            "strength": "strong",
            "independence": "independent",
            "occurred_at": epoch_ms_to_rfc3339(base + 3),
            "capture_method": "explicit_authoritative_reassessment",
            "authoritative_reassessment": True,
            "maximum_supported_level_id": guided.id,
            "links": [
                {
                    "competency_identity_id": competency_id,
                    "criterion_identity_id": identity.id,
                    "criterion_definition_id": criterion.id,
                    "scale_version_id": semantic.scale_version_id,
                    "level_id": criterion.level_id,
                    "effect": "contradicts",
                    "relevance": "primary",
                }
            ],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    state = db.get(CompetencyCapabilityState, (competency_id, "overall"))
    assert state is not None and state.capability_level_id == guided.id
    assert (
        db.scalar(
            select(func.count())
            .select_from(CapabilityStateEvent)
            .where(
                CapabilityStateEvent.competency_identity_id == competency_id,
                CapabilityStateEvent.cause_code == "authoritative_reassessment",
            )
        )
        == 1
    )
    assert not db.scalars(
        select(ProjectionInvalidation).where(
            ProjectionInvalidation.projection_kind.in_(
                ["criterion_evaluation", "capability", "review"]
            ),
            ProjectionInvalidation.status == "pending",
        )
    ).first()
    portable = _portable_payload(db)
    invalid_policy = copy.deepcopy(portable)
    invalid_policy["tables"]["capability_evaluation_runs"][-1]["capability_policy_version"] = (
        "capability-policy/v999"
    )
    with pytest.raises(AppError, match="Capability evaluation hashes or facts"):
        _validate_portable_payload(invalid_policy, "capability-policy-tampered", schema_version=10)
    invalid_event = copy.deepcopy(portable)
    authoritative_event = next(
        row
        for row in invalid_event["tables"]["capability_state_events"]
        if row["cause_code"] == "authoritative_reassessment"
    )
    authoritative_event["new_confidence"] = "high"
    with pytest.raises(AppError, match="Capability or review event history"):
        _validate_portable_payload(invalid_event, "capability-event-tampered", schema_version=10)
    invalid_checkpoint = copy.deepcopy(portable)
    invalid_checkpoint["capabilityProjectionCheckpoints"][0]["outputHash"] = "0" * 64
    with pytest.raises(AppError, match="checkpoint is disconnected"):
        _validate_portable_payload(
            invalid_checkpoint, "capability-checkpoint-tampered", schema_version=10
        )


async def test_capability_history_is_cursor_paginated(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, raw_roadmap = configured_client
    roadmap = cast(dict[str, Any], raw_roadmap)
    competency_id = str(roadmap["phases"][0]["tracks"][0]["competencies"][0]["identityId"])
    await _activate_definition(client, csrf, competency_id)
    first = await client.get(f"/api/v2/capabilities/{competency_id}/history?limit=1")
    assert first.status_code == 200
    first_body = first.json()
    assert len(first_body["runs"]) == 1
    if first_body["nextCursor"] is not None:
        second = await client.get(
            f"/api/v2/capabilities/{competency_id}/history",
            params={"limit": 1, "cursor": first_body["nextCursor"]},
        )
        assert second.status_code == 200
        assert second.json()["runs"][0]["id"] != first_body["runs"][0]["id"]
