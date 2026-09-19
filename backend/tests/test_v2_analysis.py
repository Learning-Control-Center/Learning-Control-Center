from __future__ import annotations

import ast
import copy
import json
import sqlite3
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from app import database
from app.analysis.v3 import service as analysis_service
from app.analysis.v3.contracts import AnalysisRunRequest, NormalizedFactDTO, freeze_json
from app.analysis.v3.models import (
    AnalysisV3CurrentState,
    AnalysisV3NormalizedFact,
    AnalysisV3RunLineage,
)
from app.analysis.v3.policy import (
    ANALYSIS_ALGORITHM_VERSION,
    ANALYSIS_POLICY_VERSION,
    LEGACY_NORMALIZATION_SCHEMA_VERSION,
    _signal,
    analyze_normalized_facts,
)
from app.analysis.v3.public import (
    _typed_fact,
    _typed_signal_facts,
    load_legacy_recommendation_analysis_snapshot,
    load_public_analysis_snapshot,
)
from app.analysis.v3.service import (
    _deadline,
    _discipline_facts,
    current_analysis,
    drain_analysis_invalidations,
    initialize_analysis_v3,
    record_failed_analysis_run,
    replay_analysis,
    run_analysis,
)
from app.analysis_sources import (
    EvidenceQualificationPublicDTO,
    actual_session_summaries_as_of,
    evidence_qualification_matches_target,
    readiness_evaluations_as_of,
)
from app.determinism import canonical_json, content_hash
from app.domain_integrity import validate_domain_integrity
from app.import_export import _apply_portable_restore, _portable_payload, _validate_portable_payload
from app.models import (
    ActiveCompetencyDefinitionState,
    Activity,
    AnalysisRun,
    AnalysisSnapshot,
    CapabilityEvaluationRun,
    CapabilityScaleLevel,
    CapabilityScaleVersion,
    CapabilityStateEvent,
    CompetencyDefinitionActivationEvent,
    CriterionDefinition,
    CriterionEvaluationResult,
    CriterionIdentity,
    DisciplineConfigurationEvent,
    Evidence,
    EvidenceInvalidation,
    EvidenceLink,
    EvidenceLinkRetraction,
    LearningSession,
    ProjectionInvalidation,
    SemanticCompetencyDefinition,
    SessionContribution,
    SessionCorrection,
    TargetProfileActivationEvent,
)
from app.portability.registry import (
    PORTABLE_SCHEMA_CURRENT,
    PORTABLE_V6_ANALYSIS_TABLES,
    PORTABLE_V9_MANIFEST,
    upgrade_v5_to_v6_tables,
)
from app.profile_views import (
    ReadinessGatePublicDTO,
    ReadinessPredicatePublicDTO,
    active_profile_projection_as_of,
)
from app.recommendation.v2.policy import LEGACY_POLICY_REGISTRY_VERSION, POLICY_REGISTRY_VERSION
from app.recommendation.v2.service import (
    _persist_completed_run,
    record_failed_recommendation_run,
    replay_recommendations,
)
from app.time_utils import utc_now_ms
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session


def _config(database_path: Path) -> Config:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


def _target_fact(**overrides: object) -> NormalizedFactDTO:
    payload: dict[str, object] = {
        "targetIdentityId": "target-1",
        "profileTargetId": "profile-target-1",
        "competencyIdentityId": "competency-1",
        "semanticDefinitionId": "definition-1",
        "dimensionKey": None,
        "scaleVersionId": "technical-v1",
        "targetLevelId": "independent",
        "targetLevelOrdinal": 3,
        "currentLevelId": "independent",
        "currentLevelOrdinal": 3,
        "assessmentStatus": "evaluated",
        "comparisonStatus": "at_target",
        "confidence": "high",
        "freshness": "current",
        "reviewDue": False,
        "unmetRequiredCriterionIds": [],
        "partialRequiredCriterionIds": [],
        "contradictedRequiredCriterionIds": [],
        "contradictedImportantCriterionIds": [],
        "openLesserCriterionIds": [],
        "supportingEvidenceIds": ["evidence-1"],
        "contradictingEvidenceIds": [],
        "independentEvidenceIds": ["evidence-1"],
        "missingEvidenceRequirements": [],
        "missingIndependentCriterionIds": [],
        "importantSupportingMissingCriterionIds": [],
        "criterionEvaluationFacts": [],
        "readinessGates": [],
        "prerequisites": [],
        "deadlineStatus": "later",
        "deadlineDate": "2027-12-31",
        "priority": "core",
        "daysSinceMeaningfulActivity": 0,
        "lastMeaningfulActivityAt": 1,
        "exposureDays42": 0,
        "stallEligible": False,
        "daysSincePositiveTransition": 0,
        "criticalGateDue": False,
        "inputLineage": [{"kind": "profile_target", "id": "profile-target-1"}],
    }
    payload.update(overrides)
    return NormalizedFactDTO(
        "target|target-1", "target_state", "profile_target", "target-1", payload
    )


@pytest.mark.parametrize(
    ("overrides", "severity"),
    [
        (
            {"currentLevelId": None, "currentLevelOrdinal": None, "comparisonStatus": "unknown"},
            "unknown",
        ),
        (
            {
                "currentLevelId": None,
                "currentLevelOrdinal": None,
                "comparisonStatus": "unknown",
                "priority": "critical",
            },
            "high",
        ),
        (
            {
                "currentLevelId": None,
                "currentLevelOrdinal": None,
                "comparisonStatus": "unknown",
                "readinessGates": [
                    {
                        "gateId": "gate-1",
                        "state": "unknown",
                        "effect": "hard_eligibility",
                        "deadlineStatus": "due",
                        "deadlineDate": "2026-01-01",
                        "predicates": [],
                    }
                ],
            },
            "critical",
        ),
        (
            {
                "currentLevelOrdinal": 2,
                "comparisonStatus": "below_target",
                "unmetRequiredCriterionIds": ["criterion-1"],
            },
            "high",
        ),
        (
            {
                "currentLevelOrdinal": 2,
                "comparisonStatus": "below_target",
                "partialRequiredCriterionIds": ["criterion-1"],
            },
            "medium",
        ),
        ({"confidence": "low"}, "medium"),
        ({"openLesserCriterionIds": ["criterion-2"]}, "low"),
        ({}, "none"),
    ],
)
def test_analysis_v3_gap_policy_order(overrides: dict[str, object], severity: str) -> None:
    result = analyze_normalized_facts((_target_fact(**overrides),), ())
    assert result.gaps[0].severity == severity
    assert not any(signal.signal_type == "DEADLINE_PRESSURE" for signal in result.signals), (
        "A later deadline is a normalized fact, not pressure."
    )


def test_analysis_v3_public_and_policy_boundary_cases() -> None:
    with pytest.raises(Exception, match="timezone offset"):
        AnalysisRunRequest(
            idempotency_key="naive-cutoff",
            cutoff_at=datetime(2026, 9, 12),
        )
    with pytest.raises(TypeError, match="Unsupported public Analysis JSON"):
        freeze_json(1.25)
    assert _deadline(None, None, date(2026, 9, 12)) == ("none", None)
    assert _deadline(None, "2026-12", date(2026, 9, 12)) == ("later", "2026-12-31")
    with pytest.raises(ValueError, match="Unknown Analysis signal type"):
        _signal("NOT_A_SIGNAL", "fixture", "fixture", "info", (), {})

    edge = {
        "edgeId": "edge-unknown",
        "aggregateState": "unknown",
        "eligibilitySatisfied": False,
        "unknownReasons": [],
    }
    result = analyze_normalized_facts(
        (
            _target_fact(
                priority="critical",
                comparisonStatus="at_target",
                freshness="aging",
                prerequisites=[edge],
            ),
            NormalizedFactDTO(
                "discipline|unknown",
                "discipline_variance",
                "discipline",
                "global",
                {"severity": None, "reasonCode": "WEEKLY_TARGET_UNKNOWN"},
            ),
            NormalizedFactDTO(
                "workload|high",
                "workload_risk",
                "discipline",
                "global",
                {
                    "severity": "high",
                    "reasonCode": "ACTIVE_DAY_DURATION_ABOVE_TARGET",
                    "surgeComparisonStatus": "evaluated",
                },
            ),
        ),
        (),
    )
    assert {item.signal_type for item in result.signals} >= {
        "MAINTENANCE_DUE",
        "PREREQUISITE_BLOCK",
        "WORKLOAD_RISK",
    }
    assert {item.reason_code for item in result.unknown_markers} >= {
        "PREREQUISITE_UNKNOWN",
        "WEEKLY_TARGET_UNKNOWN",
    }
    typed_edge = _typed_signal_facts("PREREQUISITE_BLOCK", edge)
    assert typed_edge.prerequisite is not None
    assert typed_edge.prerequisite.aggregate_state == "unknown"


def test_analysis_v3_signal_boundaries_and_unknowns_are_explicit() -> None:
    result = analyze_normalized_facts(
        (
            _target_fact(
                daysSinceMeaningfulActivity=14,
                reviewDue=True,
                confidence="low",
                deadlineStatus="due_soon",
                deadlineDate="2026-03-01",
            ),
            NormalizedFactDTO(
                "allocation|domain-1",
                "allocation",
                "profile_domain",
                "domain-1",
                {
                    "sufficientData": True,
                    "missBasisPoints": -500,
                },
            ),
            NormalizedFactDTO(
                "discipline|four-completed-weeks",
                "discipline_variance",
                "discipline",
                "global",
                {"severity": None, "reasonCode": "WEEKLY_TARGET_UNKNOWN"},
            ),
            NormalizedFactDTO(
                "discipline|workload-seven-days",
                "workload_risk",
                "discipline",
                "global",
                {"severity": None, "reasonCode": "WORKLOAD_INSUFFICIENT_DATA"},
            ),
        ),
        (),
    )
    by_type = {item.signal_type: item for item in result.signals}
    assert by_type["NEGLECT"].severity == "high"
    assert by_type["UNDER_ALLOCATION"].severity == "attention"
    assert by_type["DEADLINE_PRESSURE"].severity == "attention"
    assert by_type["REVIEW_DUE"].severity == "high"
    assert by_type["LOW_CONFIDENCE"].severity == "attention"
    assert {item.reason_code for item in result.unknown_markers} == {
        "WEEKLY_TARGET_UNKNOWN",
        "WORKLOAD_INSUFFICIENT_DATA",
    }


def test_workload_zero_surge_denominator_is_unknown_without_risk_signal() -> None:
    result = analyze_normalized_facts(
        (
            NormalizedFactDTO(
                "discipline|workload-seven-days",
                "workload_risk",
                "discipline",
                "global",
                {
                    "severity": None,
                    "reasonCode": "WORKLOAD_WITHIN_TARGET",
                    "surgeComparisonStatus": "unknown",
                },
            ),
        ),
        (),
    )
    assert not result.signals
    assert [item.reason_code for item in result.unknown_markers] == [
        "WORKLOAD_SURGE_DENOMINATOR_UNKNOWN"
    ]
    assert result.completeness == "partial"


@pytest.mark.parametrize(
    ("miss_basis_points", "severity"),
    [
        (-1, "info"),
        (-499, "info"),
        (-500, "attention"),
        (-1499, "attention"),
        (-1500, "high"),
        (-2999, "high"),
        (-3000, "critical"),
        (3000, "critical"),
    ],
)
def test_analysis_v3_allocation_exact_boundaries(miss_basis_points: int, severity: str) -> None:
    fact = NormalizedFactDTO(
        "allocation|domain",
        "allocation",
        "profile_domain",
        "domain",
        {"sufficientData": True, "missBasisPoints": miss_basis_points},
    )
    result = analyze_normalized_facts((fact,), (), generated_cutoff_at=123)
    signal = result.signals[0]
    assert signal.severity == severity
    assert signal.signal_type == (
        "UNDER_ALLOCATION" if miss_basis_points < 0 else "OVER_ALLOCATION"
    )
    assert signal.generated_cutoff_at == 123


@pytest.mark.parametrize(
    ("priority", "days", "severity"),
    [
        ("core", 6, None),
        ("core", 7, "attention"),
        ("core", 14, "high"),
        ("core", 28, "critical"),
        ("important", 14, "attention"),
        ("important", 28, "high"),
        ("important", 56, "critical"),
        ("supporting", 28, "info"),
        ("supporting", 56, "attention"),
        ("supporting", 168, "attention"),
    ],
)
def test_analysis_v3_neglect_exact_boundaries(
    priority: str, days: int, severity: str | None
) -> None:
    result = analyze_normalized_facts(
        (_target_fact(priority=priority, daysSinceMeaningfulActivity=days),), ()
    )
    signals = [item for item in result.signals if item.signal_type == "NEGLECT"]
    assert (signals[0].severity if signals else None) == severity


@pytest.mark.parametrize(
    ("days", "severity"),
    [(41, None), (42, "attention"), (84, "high"), (168, "critical")],
)
def test_analysis_v3_stall_exact_boundaries(days: int, severity: str | None) -> None:
    result = analyze_normalized_facts(
        (
            _target_fact(
                currentLevelOrdinal=2,
                comparisonStatus="below_target",
                stallEligible=True,
                exposureDays42=3,
                daysSincePositiveTransition=days,
            ),
        ),
        (),
    )
    signals = [item for item in result.signals if item.signal_type == "PROGRESSION_STALL"]
    assert (signals[0].severity if signals else None) == severity


@pytest.mark.parametrize(
    ("effect", "expected_block"),
    [("hard_eligibility", True), ("urgency", False), ("display_only", False)],
)
def test_analysis_v3_readiness_effects_remain_distinct(effect: str, expected_block: bool) -> None:
    gate = {
        "gateId": f"gate-{effect}",
        "state": "unknown",
        "effect": effect,
        "deadlineStatus": "later",
        "deadlineDate": None,
        "predicates": [
            {
                "predicate_id": "predicate-1",
                "predicate_type": "evidence_present",
                "requirement_type": "required",
                "state": "unknown",
                "reason_code": "EVIDENCE_POLICY_UNAVAILABLE",
                "subject": {},
            }
        ],
    }
    result = analyze_normalized_facts((_target_fact(readinessGates=[gate]),), ())
    assert any(item.signal_type == "READINESS_BLOCK" for item in result.signals) is expected_block
    assert "EVIDENCE_POLICY_UNAVAILABLE" in {item.reason_code for item in result.unknown_markers}
    assert result.completeness == "partial"


def test_analysis_v3_keeps_dimension_signals_distinct() -> None:
    result = analyze_normalized_facts(
        (
            _target_fact(
                targetIdentityId="speaking-target",
                dimensionKey="speaking",
                daysSinceMeaningfulActivity=14,
            ),
            _target_fact(
                targetIdentityId="writing-target",
                dimensionKey="writing",
                daysSinceMeaningfulActivity=14,
            ),
        ),
        (),
        generated_cutoff_at=999,
    )
    neglect = [item for item in result.signals if item.signal_type == "NEGLECT"]
    assert {item.dimension_key for item in neglect} == {"speaking", "writing"}
    assert {item.generated_cutoff_at for item in neglect} == {999}


@pytest.mark.parametrize(
    ("overrides", "severity", "reason"),
    [
        (
            {
                "contradictedRequiredCriterionIds": ["required-1"],
                "criticalGateDue": True,
            },
            "critical",
            "REQUIRED_EVIDENCE_CONTRADICTED",
        ),
        (
            {"missingEvidenceRequirements": ["required-1"], "priority": "core"},
            "high",
            "REQUIRED_EVIDENCE_MISSING",
        ),
        (
            {"missingIndependentCriterionIds": ["required-1"]},
            "attention",
            "REQUIRED_INDEPENDENCE_MISSING",
        ),
        (
            {"importantSupportingMissingCriterionIds": ["important-1"]},
            "info",
            "IMPORTANT_SUPPORTING_EVIDENCE_WEAK",
        ),
    ],
)
def test_analysis_v3_evidence_weakness_exact_rules(
    overrides: dict[str, object], severity: str, reason: str
) -> None:
    result = analyze_normalized_facts((_target_fact(**overrides),), ())
    signal = next(item for item in result.signals if item.signal_type == "EVIDENCE_WEAKNESS")
    assert signal.severity == severity
    assert signal.reason_codes == (reason,)


async def test_analysis_v3_api_retry_replay_staleness_and_v1_isolation(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    v1_run_count = db.scalar(
        select(func.count())
        .select_from(AnalysisRun)
        .where(AnalysisRun.purpose == "v1_recommendation_compat")
    )
    response = await client.post(
        "/api/v2/analysis/runs",
        json={"idempotency_key": "analysis-v3-api-1", "purpose": "learning_control"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    snapshot = response.json()
    assert snapshot["schemaVersion"] == 3
    assert snapshot["completeness"] == "partial"
    assert {item["reasonCode"] for item in snapshot["unknownMarkers"]} >= {"TARGET_PROFILE_MISSING"}
    lineage = db.get(AnalysisV3RunLineage, snapshot["runId"])
    run = db.get(AnalysisRun, snapshot["runId"])
    assert lineage is not None and run is not None
    assert run.algorithm_version == ANALYSIS_ALGORITHM_VERSION
    assert lineage.analysis_policy_version == ANALYSIS_POLICY_VERSION
    assert json.loads(lineage.analyzer_bundle_json)["capabilityDowngrade"] == (
        "capability-downgrade-policy/v1"
    )
    assert json.loads(lineage.analyzer_bundle_json)["gap"] == ANALYSIS_POLICY_VERSION
    assert "recommendation" not in lineage.analyzer_bundle_json.lower()
    assert (
        db.scalar(
            select(func.count())
            .select_from(AnalysisRun)
            .where(AnalysisRun.purpose == "v1_recommendation_compat")
        )
        == v1_run_count
    )

    retry = await client.post(
        "/api/v2/analysis/runs",
        json={"idempotency_key": "analysis-v3-api-1", "purpose": "learning_control"},
        headers={"X-CSRF-Token": csrf},
    )
    assert retry.status_code == 201
    assert retry.json()["id"] == snapshot["id"]
    conflict = await client.post(
        "/api/v2/analysis/runs",
        json={"idempotency_key": "analysis-v3-api-1", "purpose": "candidate_readiness"},
        headers={"X-CSRF-Token": csrf},
    )
    assert conflict.status_code == 409

    replay = await client.post(
        f"/api/v2/analysis/runs/{snapshot['runId']}/replay",
        json={"idempotency_key": "analysis-v3-replay-1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] != snapshot["id"]
    assert replay.json()["inputHash"] == snapshot["inputHash"]
    assert replay.json()["outputHash"] == snapshot["outputHash"]

    history_response = await client.get("/api/v2/analysis/history")
    assert history_response.status_code == 200
    assert {item["id"] for item in history_response.json()} >= {
        snapshot["id"],
        replay.json()["id"],
    }
    detail_response = await client.get(f"/api/v2/analysis/snapshots/{snapshot['id']}")
    assert detail_response.status_code == 200
    assert detail_response.json()["outputHash"] == snapshot["outputHash"]
    missing_detail = await client.get("/api/v2/analysis/snapshots/missing")
    assert missing_detail.status_code == 404
    missing_replay = await client.post(
        "/api/v2/analysis/runs/missing/replay",
        json={"idempotency_key": "analysis-missing-replay"},
        headers={"X-CSRF-Token": csrf},
    )
    assert missing_replay.status_code == 404
    failed = db.scalar(
        select(AnalysisRun).where(AnalysisRun.idempotency_key == "analysis-missing-replay")
    )
    assert failed is not None and failed.status == "failed"

    candidate_run = await client.post(
        "/api/v2/analysis/runs",
        json={"idempotency_key": "analysis-candidate-1", "purpose": "candidate_readiness"},
        headers={"X-CSRF-Token": csrf},
    )
    assert candidate_run.status_code == 201
    candidate_current = await client.get("/api/v2/analysis/current?purpose=candidate_readiness")
    assert candidate_current.status_code == 200
    assert candidate_current.json()["snapshot"]["id"] == candidate_run.json()["id"]
    historical_run = await client.post(
        "/api/v2/analysis/runs",
        json={
            "idempotency_key": "analysis-historical-1",
            "purpose": "learning_control",
            "cutoff_at": "2025-01-01T00:00:00Z",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert historical_run.status_code == 201

    current_before = await client.get("/api/v2/analysis/current")
    assert current_before.json()["status"] == "current"
    assert current_before.json()["snapshot"]["id"] == snapshot["id"]
    run_count = db.scalar(select(func.count()).select_from(AnalysisRun))
    db.add(
        ProjectionInvalidation(
            projection_kind="analysis",
            subject_type="test_fact",
            subject_id="global",
            source_fact_id="analysis-stale-source",
            target_policy_version=ANALYSIS_POLICY_VERSION,
            status="pending",
            attempt_count=0,
            requested_at=9_999_999_999_999,
        )
    )
    db.commit()
    current_after = await client.get("/api/v2/analysis/current")
    assert current_after.json()["status"] == "stale"
    assert db.scalar(select(func.count()).select_from(AnalysisRun)) == run_count


async def test_analysis_session_actuality_and_discipline_boundaries(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    _client, _csrf, raw_roadmap = configured_client
    roadmap = cast(dict[str, Any], raw_roadmap)
    competency_id = str(roadmap["phases"][0]["tracks"][0]["competencies"][0]["identityId"])
    contribution_criterion = db.scalar(
        select(CriterionDefinition)
        .join(
            SemanticCompetencyDefinition,
            SemanticCompetencyDefinition.id == CriterionDefinition.semantic_definition_id,
        )
        .where(SemanticCompetencyDefinition.competency_identity_id == competency_id)
        .order_by(CriterionDefinition.definition_version, CriterionDefinition.id)
        .limit(1)
    )
    if contribution_criterion is None:
        scale = db.scalar(
            select(CapabilityScaleVersion).where(
                CapabilityScaleVersion.scale_stable_key == "technical"
            )
        )
        assert scale is not None
        level = db.scalar(
            select(CapabilityScaleLevel)
            .where(CapabilityScaleLevel.scale_version_id == scale.id)
            .order_by(CapabilityScaleLevel.ordinal_rank, CapabilityScaleLevel.id)
            .limit(1)
        )
        assert level is not None
        semantic = SemanticCompetencyDefinition(
            competency_identity_id=competency_id,
            definition_version=1,
            title="Analysis attribution fixture",
            description="Immutable contribution attribution fixture.",
            scope="Test-only semantic scope.",
            scale_version_id=scale.id,
            created_at=1_789_689_599_000,
            creation_source="test",
            effective_at=1_789_689_599_000,
        )
        identity = CriterionIdentity(
            competency_identity_id=competency_id,
            stable_key="analysis-attribution-fixture",
            created_at=1_789_689_599_000,
            creation_source="test",
        )
        db.add_all((semantic, identity))
        db.flush()
        contribution_criterion = CriterionDefinition(
            criterion_identity_id=identity.id,
            semantic_definition_id=semantic.id,
            definition_version=1,
            level_id=level.id,
            dimension_id=None,
            requirement_type="required",
            demonstration_rule_json='{"rule":"independent_performance"}',
            description="Attribution fixture criterion.",
            created_at=1_789_689_599_000,
        )
        db.add(contribution_criterion)
        db.add(
            CompetencyDefinitionActivationEvent(
                competency_identity_id=competency_id,
                from_definition_id=None,
                to_definition_id=semantic.id,
                activated_at=1_789_689_599_000,
                source="test",
                reason="Analysis attribution fixture",
                event_sequence=1,
                idempotency_key="analysis-attribution-definition",
            )
        )
        db.add(
            ActiveCompetencyDefinitionState(
                competency_identity_id=competency_id,
                semantic_definition_id=semantic.id,
                activated_at=1_789_689_599_000,
            )
        )
        db.flush()
    # The configured fixture does not create actual work; insert one canonical Activity/Session
    # pair so the public Analysis source view is exercised without involving wall-clock timers.
    activity = Activity(
        title="Analysis session",
        category_stable_key="practice",
        category_version="v1",
        occurred_at=1_789_689_600_000,
        creator_source="test",
        provenance="test",
        created_at=1_789_689_600_000,
    )
    db.add(activity)
    db.flush()
    session = LearningSession(
        activity_id=activity.id,
        competency_identity_id=competency_id,
        session_mode="manual",
        timed_state=None,
        activity_type="practice",
        assistance_mode="none",
        outcome="completed",
        started_at=1_789_689_600_000,
        ended_at=1_789_693_200_000,
        duration_ms=3_600_000,
        accumulated_duration_ms=3_600_000,
        created_at=1_789_693_200_000,
        updated_at=1_789_693_200_000,
    )
    db.add(session)
    db.flush()
    contribution = SessionContribution(
        session_id=session.id,
        competency_identity_id=competency_id,
        criterion_identity_id=contribution_criterion.criterion_identity_id,
        relevance="primary",
        provenance="user_selected",
        created_at=1_789_693_200_000,
    )
    db.add(contribution)
    db.commit()

    summaries = actual_session_summaries_as_of(
        db, exclusive_cutoff_at=1_789_776_000_000, timezone_name="UTC"
    )
    assert len(summaries) == 1
    assert summaries[0].primary_competency_identity_id == competency_id
    assert summaries[0].duration_ms == 3_600_000

    correction_at = 1_789_693_201_000
    legacy = run_analysis(
        db,
        idempotency_key="analysis-session-legacy-normalization",
        purpose="learning_control",
        cutoff_at=correction_at,
        normalization_schema_version=LEGACY_NORMALIZATION_SCHEMA_VERSION,
    )
    legacy_lineage = json.loads(legacy.input_lineage_json)
    assert "active_contribution_attributions" not in legacy_lineage["sessionSummaries"][0]
    assert (
        load_public_analysis_snapshot(db, legacy.id)
        .actual_activity_summaries[0]
        .active_contribution_attributions
        == ()
    )
    legacy_public = load_legacy_recommendation_analysis_snapshot(db, legacy.id)
    legacy_attributions = legacy_public.actual_activity_summaries[
        0
    ].active_contribution_attributions
    assert len(legacy_attributions) == 1
    assert legacy_attributions[0].criterion_definition_id == contribution_criterion.id
    legacy_replay = replay_analysis(
        db,
        run_id=legacy.run_id,
        idempotency_key="analysis-session-legacy-normalization-replay",
    )
    assert legacy_replay.input_hash == legacy.input_hash
    assert legacy_replay.output_hash == legacy.output_hash
    legacy_recommendation_input = {
        "analysisSnapshot": {
            "id": legacy.id,
            "inputHash": legacy.input_hash,
            "outputHash": legacy.output_hash,
            "cutoffAt": legacy.cutoff_at,
        },
        "analysisPublicSnapshot": json.loads(canonical_json(asdict(legacy_public))),
        "targetProfileVersion": legacy_lineage["profile"],
        "learningGraph": legacy_lineage["graph"],
        "curriculum": legacy_lineage["curriculumCatalog"],
        "curriculumAvailability": [],
        "projects": legacy_lineage["projectCatalog"],
        "userConstraints": {
            "availableTimeMs": None,
            "contextCostPolicy": "explicit-only",
            "contextCosts": [],
        },
        "availableTimeMs": None,
        "candidates": [],
    }
    legacy_recommendation = _persist_completed_run(
        db,
        idempotency_key="analysis-session-legacy-recommendation",
        analysis_snapshot_id=legacy.id,
        available_time_ms=None,
        replay_of_run_id=None,
        policy_registry_version=POLICY_REGISTRY_VERSION,
        snapshot=legacy_public,
        frozen_input=legacy_recommendation_input,
        candidates=(),
    )
    db.commit()
    replayed_recommendation = replay_recommendations(
        db,
        run_id=legacy_recommendation.id,
        idempotency_key="analysis-session-legacy-recommendation-replay",
    )
    assert replayed_recommendation.input_hash == legacy_recommendation.input_hash
    assert replayed_recommendation.output_hash == legacy_recommendation.output_hash
    failed_legacy_recommendation = record_failed_recommendation_run(
        db,
        idempotency_key="analysis-session-legacy-recommendation-failed",
        analysis_snapshot_id=legacy.id,
        available_time_ms=None,
        replay_of_run_id=None,
        error=RuntimeError("historical failure"),
        frozen_input=legacy_recommendation_input,
        policy_registry_version=LEGACY_POLICY_REGISTRY_VERSION,
    )
    assert failed_legacy_recommendation is not None
    db.commit()
    portable = _portable_payload(db)
    _validate_portable_payload(portable, "analysis-legacy-recommendation-v9", 9)
    validate_domain_integrity(db.connection())
    before_correction = run_analysis(
        db,
        idempotency_key="analysis-session-before-correction",
        purpose="learning_control",
        cutoff_at=correction_at,
    )
    frozen_public = load_public_analysis_snapshot(db, before_correction.id)
    frozen_attributions = frozen_public.actual_activity_summaries[
        0
    ].active_contribution_attributions
    assert len(frozen_attributions) == 1
    assert frozen_attributions[0].criterion_definition_id == contribution_criterion.id
    db.execute(
        text(
            "UPDATE session_contributions SET criterion_identity_id=NULL WHERE id=:contribution_id"
        ),
        {"contribution_id": contribution.id},
    )
    db.flush()
    assert (
        load_public_analysis_snapshot(db, before_correction.id)
        .actual_activity_summaries[0]
        .active_contribution_attributions
        == frozen_attributions
    )
    session.duration_ms = 1_800_000
    session.accumulated_duration_ms = 1_800_000
    session.ended_at = session.started_at + 1_800_000
    session.updated_at = correction_at
    db.add(
        SessionCorrection(
            session_id=session.id,
            corrected_at=correction_at,
            source="test",
            reason="Correct duration",
            changed_fields_json='["duration_ms","accumulated_duration_ms","ended_at"]',
            before_json=json.dumps(
                {
                    "duration_ms": 3_600_000,
                    "accumulated_duration_ms": 3_600_000,
                    "ended_at": 1_789_693_200_000,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            after_json=json.dumps(
                {
                    "duration_ms": 1_800_000,
                    "accumulated_duration_ms": 1_800_000,
                    "ended_at": session.ended_at,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )
    db.commit()
    after_correction = run_analysis(
        db,
        idempotency_key="analysis-session-after-correction",
        purpose="learning_control",
        cutoff_at=correction_at + 1,
    )
    db.commit()
    before_sessions = json.loads(db.get(AnalysisRun, before_correction.run_id).input_lineage_json)[
        "sessionSummaries"
    ]
    after_sessions = json.loads(db.get(AnalysisRun, after_correction.run_id).input_lineage_json)[
        "sessionSummaries"
    ]
    assert before_sessions[0]["duration_ms"] == 3_600_000
    assert after_sessions[0]["duration_ms"] == 1_800_000

    tombstoned_at = correction_at + 2
    session.tombstoned_at = tombstoned_at
    session.tombstone_reason = "Remove mistaken Session"
    session.updated_at = tombstoned_at
    db.commit()
    at_tombstone = run_analysis(
        db,
        idempotency_key="analysis-session-at-tombstone",
        purpose="learning_control",
        cutoff_at=tombstoned_at,
    )
    after_tombstone = run_analysis(
        db,
        idempotency_key="analysis-session-after-tombstone",
        purpose="learning_control",
        cutoff_at=tombstoned_at + 1,
    )
    db.commit()
    assert json.loads(db.get(AnalysisRun, at_tombstone.run_id).input_lineage_json)[
        "sessionSummaries"
    ]
    assert not json.loads(db.get(AnalysisRun, after_tombstone.run_id).input_lineage_json)[
        "sessionSummaries"
    ]

    sessions = tuple(
        SimpleNamespace(local_date=f"2026-09-{day:02d}", duration_ms=duration)
        for day, duration in (
            (1, 1_000),
            (2, 1_000),
            (3, 1_000),
            (10, 2_000),
            (11, 2_000),
            (12, 2_000),
        )
    )
    discipline, workload = _discipline_facts(
        sessions,
        date(2026, 9, 12),
        {"weeklyTargetActiveDays": 5, "targetDurationMsPerActiveDay": 1_000},
    )
    assert discipline.payload["severity"] == "critical"
    assert workload.payload["medianBasisPointsOfTarget"] == 20_000
    assert workload.payload["surgeComparisonStatus"] == "evaluated"
    assert workload.payload["severity"] == "critical"
    discipline_signal_facts = _typed_signal_facts("DISCIPLINE_VARIANCE", discipline.payload)
    workload_signal_facts = _typed_signal_facts("WORKLOAD_RISK", workload.payload)
    assert discipline_signal_facts.discipline_target_active_days == 5
    assert len(discipline_signal_facts.discipline_completed_weeks) == 4
    assert workload_signal_facts.workload_target_duration_ms == 1_000
    assert workload_signal_facts.workload_active_day_durations_ms
    assert workload_signal_facts.workload_median_basis_points_of_target == 20_000
    assert workload_signal_facts.workload_current_seven_day_total_duration_ms is not None


@pytest.mark.parametrize(
    ("weekly_active_days", "severity"),
    [
        ((5, 5, 5, 4), "attention"),
        ((5, 4, 5, 4), "high"),
        ((4, 4, 5, 4), "critical"),
        ((5, 0, 0, 5), "critical"),
        ((5, 5, 5, 5), None),
    ],
)
def test_analysis_v3_discipline_variance_exact_four_week_rules(
    weekly_active_days: tuple[int, int, int, int], severity: str | None
) -> None:
    week_starts = (date(2026, 8, 17), date(2026, 8, 24), date(2026, 8, 31), date(2026, 9, 7))
    sessions = tuple(
        SimpleNamespace(
            local_date=week_start.fromordinal(week_start.toordinal() + offset).isoformat(),
            duration_ms=1_000,
        )
        for week_start, count in zip(week_starts, weekly_active_days, strict=True)
        for offset in range(count)
    )
    discipline, _workload = _discipline_facts(
        sessions,
        date(2026, 9, 16),
        {"weeklyTargetActiveDays": 5, "targetDurationMsPerActiveDay": 1_000},
    )
    assert discipline.payload["severity"] == severity


async def test_analysis_v3_reconstructs_profile_capability_and_late_evidence_at_cutoff(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, raw_roadmap = configured_client
    roadmap = cast(dict[str, Any], raw_roadmap)
    competency_id = str(roadmap["phases"][0]["tracks"][0]["competencies"][0]["identityId"])
    semantic_response = await client.post(
        f"/api/v2/competencies/{competency_id}/definitions",
        json={
            "title": "Analysis capability",
            "description": "Cutoff-correct capability input.",
            "scope": "A bounded Analysis integration fixture.",
            "scale_stable_key": "technical",
            "scale_version": "v1",
            "dimension_keys": [],
            "effective_at": "2026-09-09T00:00:00Z",
            "creation_source": "test",
            "criteria": [
                {
                    "stable_key": "analysis.independent",
                    "level_stable_key": "independent",
                    "dimension_key": None,
                    "requirement_type": "required",
                    "demonstration_rule": "independent_performance",
                    "description": "Perform independently.",
                }
            ],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert semantic_response.status_code == 201, semantic_response.text
    semantic_payload = semantic_response.json()
    activated = await client.post(
        f"/api/v2/competencies/{competency_id}/definitions/{semantic_payload['id']}/activate",
        json={
            "reason": "Analysis integration",
            "source": "test",
            "idempotency_key": "analysis-semantic-active",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert activated.status_code == 200, activated.text
    profile_response = await client.post(
        "/api/v2/target-profiles",
        json={
            "stable_key": "analysis-profile",
            "creation_source": "test",
            "version": {
                "title": "Analysis profile",
                "description": "Analysis integration profile.",
                "creation_source": "test",
                "effective_at": "2026-09-09T00:00:00Z",
                "domains": [
                    {
                        "stable_key": "engineering",
                        "title": "Engineering",
                        "minimum_percent": 50,
                        "maximum_percent": 100,
                        "order_index": 0,
                    }
                ],
                "targets": [
                    {
                        "stable_key": "analysis-target",
                        "competency_identity_id": competency_id,
                        "dimension_key": None,
                        "domain_stable_key": "engineering",
                        "scale_stable_key": "technical",
                        "scale_version": "v1",
                        "target_level_stable_key": "independent",
                        "priority": "core",
                        "target_date": "2027-01-15",
                        "date_interpretation": "Reach by 2027-01-15 local time.",
                    }
                ],
                "milestones": [
                    {
                        "stable_key": "analysis-milestone",
                        "title": "Analysis milestone",
                        "target_date": "2027-01-15",
                        "order_index": 0,
                        "target_stable_keys": ["analysis-target"],
                    }
                ],
                "readiness_gates": [
                    {
                        "stable_key": "evidence-ready",
                        "title": "Evidence ready",
                        "effect": "urgency",
                        "order_index": 0,
                        "milestone_stable_key": "analysis-milestone",
                        "target_stable_keys": ["analysis-target"],
                        "predicates": [
                            {
                                "predicate_type": "evidence_present",
                                "requirement_type": "required",
                                "order_index": 0,
                                "subject": {"evidencePolicyStableKey": "evidence-policy/v1"},
                            }
                        ],
                    }
                ],
            },
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert profile_response.status_code == 201, profile_response.text
    profile = profile_response.json()
    profile_activation = await client.post(
        f"/api/v2/target-profiles/{profile['profileId']}/versions/{profile['versionId']}/activate",
        json={
            "reason": "Analysis integration",
            "source": "test",
            "idempotency_key": "analysis-profile-active",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert profile_activation.status_code == 200, profile_activation.text
    activation = db.scalar(
        select(TargetProfileActivationEvent)
        .where(TargetProfileActivationEvent.to_profile_version_id == profile["versionId"])
        .order_by(TargetProfileActivationEvent.activated_at.desc())
    )
    assert activation is not None
    profile_at_cutoffs = []
    for label, cutoff in (
        ("before", activation.activated_at - 1),
        ("equal", activation.activated_at),
        ("after", activation.activated_at + 1),
    ):
        snapshot_at_activation = run_analysis(
            db,
            idempotency_key=f"analysis-profile-activation-{label}",
            purpose="learning_control",
            cutoff_at=cutoff,
        )
        profile_at_cutoffs.append(
            json.loads(db.get(AnalysisRun, snapshot_at_activation.run_id).input_lineage_json)[
                "profile"
            ]
        )
    db.commit()
    assert profile_at_cutoffs[:2] == [None, None]
    assert profile_at_cutoffs[2]["profile_version_id"] == profile["versionId"]

    criterion = db.get(CriterionDefinition, semantic_payload["criteria"][0]["id"])
    semantic = db.get(SemanticCompetencyDefinition, semantic_payload["id"])
    assert criterion is not None and semantic is not None
    base = utc_now_ms()
    support = Evidence(
        evidence_type="assessment",
        source_type="analysis_test",
        source_id="support",
        source_role="fact",
        title="Independent support",
        strength="strong",
        independence="independent",
        source_confidence="high",
        occurred_at=base,
        created_at=base,
        provenance_json='{"context_id":"analysis-integration"}',
        policy_version="evidence-policy/v1",
        schema_version=1,
        authoritative_for_downgrade=False,
    )
    db.add(support)
    db.flush()
    db.add(
        EvidenceLink(
            evidence_id=support.id,
            competency_identity_id=competency_id,
            criterion_identity_id=criterion.criterion_identity_id,
            criterion_definition_id=criterion.id,
            scale_version_id=semantic.scale_version_id,
            level_id=criterion.level_id,
            effect="supports",
            relevance="primary",
            provenance_json="{}",
            created_at=base,
        )
    )
    db.commit()
    persisted_capability_runs = db.scalar(select(func.count()).select_from(CapabilityEvaluationRun))
    first = run_analysis(
        db,
        idempotency_key="analysis-real-cutoff-first",
        purpose="learning_control",
        cutoff_at=base + 1,
    )
    db.commit()
    first_facts = json.loads(first.normalized_facts_json)
    target = next(item["payload"] for item in first_facts if item["fact_type"] == "target_state")
    assert target["comparisonStatus"] == "at_target"
    assert target["readinessGates"][0]["state"] == "met"
    assert target["inputLineage"][1]["kind"] == "capability_computed_as_of"
    assert db.scalar(select(func.count()).select_from(CapabilityEvaluationRun)) == (
        persisted_capability_runs
    )

    profile_projection = active_profile_projection_as_of(db, exclusive_cutoff_at=base + 1)
    assert profile_projection is not None
    target_id = profile_projection.targets[0].id
    predicate_specs = (
        (
            "capability",
            "capability_at_least",
            {
                "scaleStableKey": "technical",
                "scaleVersion": "v1",
                "levelStableKey": "independent",
                "competencyIdentityId": competency_id,
                "dimensionKey": None,
            },
        ),
        (
            "criterion",
            "criterion_demonstrated",
            {"criterionIdentityId": criterion.criterion_identity_id},
        ),
        (
            "project",
            "project_criterion_demonstrated",
            {"projectCriterionIdentityId": "missing-project-criterion"},
        ),
        (
            "unsupported-evidence-policy",
            "evidence_present",
            {"evidencePolicyStableKey": "unknown-policy"},
        ),
    )
    synthetic_gate = ReadinessGatePublicDTO(
        "synthetic-gate",
        "synthetic-gate-identity",
        "synthetic",
        "Synthetic policy coverage",
        "display_only",
        None,
        (target_id,),
        tuple(
            ReadinessPredicatePublicDTO(
                predicate_id,
                predicate_type,
                "required",
                json.dumps(subject),
                index,
            )
            for index, (predicate_id, predicate_type, subject) in enumerate(predicate_specs)
        ),
        0,
    )
    empty_scope_gate = ReadinessGatePublicDTO(
        "empty-scope-gate",
        "empty-scope-gate-identity",
        "empty-scope",
        "Empty evidence scope",
        "display_only",
        None,
        (),
        (
            ReadinessPredicatePublicDTO(
                "empty-scope",
                "evidence_present",
                "required",
                json.dumps({"evidencePolicyStableKey": "evidence-policy/v1"}),
                0,
            ),
        ),
        1,
    )
    readiness = readiness_evaluations_as_of(
        db,
        profile=replace(
            profile_projection,
            readiness_gates=(synthetic_gate, empty_scope_gate),
        ),
        exclusive_cutoff_at=base + 1,
        timezone_name="UTC",
    )
    reasons = {item.reason_code for gate in readiness for item in gate.predicates}
    assert {
        "CAPABILITY_AT_LEAST_MET",
        "CRITERION_DEMONSTRATED",
        "PROJECT_CRITERION_UNKNOWN",
        "EVIDENCE_POLICY_UNAVAILABLE",
        "EVIDENCE_SCOPE_UNKNOWN",
    } <= reasons
    public_snapshot = load_public_analysis_snapshot(db, first.id)
    public_target = next(item for item in public_snapshot.facts if item.fact_type == "target_state")
    assert public_target.criterion_evaluations[0].demonstration_rule == ("independent_performance")
    assert public_target.readiness_gates[0].predicates[0].reason_code == (
        "QUALIFYING_EVIDENCE_PRESENT"
    )
    assert public_snapshot.gaps[0].target_fact.target_identity_id == (
        public_target.target_identity_id
    )
    support_link = db.scalar(select(EvidenceLink).where(EvidenceLink.evidence_id == support.id))
    assert support_link is not None
    qualification = EvidenceQualificationPublicDTO(
        support.id,
        support_link.id,
        competency_id,
        None,
        criterion.id,
        support.strength,
        support.independence,
        support.source_confidence,
        support.policy_version,
    )
    exact_target = profile_projection.targets[0]
    assert evidence_qualification_matches_target(
        db,
        qualification=qualification,
        target=exact_target,
        active_semantics={competency_id: semantic.id},
    )
    assert evidence_qualification_matches_target(
        db,
        qualification=replace(qualification, criterion_definition_id=None),
        target=exact_target,
        active_semantics={competency_id: semantic.id},
    )
    assert not evidence_qualification_matches_target(
        db,
        qualification=replace(qualification, competency_identity_id="other-competency"),
        target=exact_target,
        active_semantics={competency_id: semantic.id},
    )
    assert not evidence_qualification_matches_target(
        db,
        qualification=qualification,
        target=replace(exact_target, dimension_key="writing", dimension_id="dimension-writing"),
        active_semantics={competency_id: semantic.id},
    )
    assert not evidence_qualification_matches_target(
        db,
        qualification=qualification,
        target=exact_target,
        active_semantics={competency_id: "superseding-semantic-definition"},
    )

    exact_package = _portable_payload(db)
    snapshot_row = next(
        item for item in exact_package["tables"]["analysis_snapshots"] if item["id"] == first.id
    )
    tamper_cases = {
        "target_profile_id": None,
        "target_profile_version_id": None,
        "curriculum_reference": "f" * 64,
        "semantic_definition_references_json": "[]",
        "capability_scale_version_references_json": "[]",
    }
    for field, value in tamper_cases.items():
        tampered_package = copy.deepcopy(exact_package)
        tampered_row = next(
            item
            for item in tampered_package["tables"]["analysis_snapshots"]
            if item["id"] == first.id
        )
        tampered_row[field] = value
        with pytest.raises(Exception, match="Analysis V3|analysis"):
            _validate_portable_payload(tampered_package, f"analysis-envelope-{field}", 9)
    for field in (
        "semantic_definition_references_json",
        "capability_scale_version_references_json",
    ):
        tampered_package = copy.deepcopy(exact_package)
        tampered_row = next(
            item
            for item in tampered_package["tables"]["analysis_snapshots"]
            if item["id"] == first.id
        )
        references = json.loads(snapshot_row[field])
        tampered_row[field] = json.dumps(references + references)
        with pytest.raises(Exception, match="Analysis V3|analysis"):
            _validate_portable_payload(tampered_package, f"analysis-envelope-duplicate-{field}", 9)

    contradiction = Evidence(
        evidence_type="assessment",
        source_type="analysis_test",
        source_id="late-contradiction",
        source_role="fact",
        title="Late contradiction",
        strength="strong",
        independence="independent",
        source_confidence="high",
        occurred_at=base - 100,
        created_at=base - 100,
        provenance_json='{"context_id":"late"}',
        policy_version="evidence-policy/v1",
        schema_version=1,
        authoritative_for_downgrade=False,
    )
    db.add(contradiction)
    db.flush()
    contradiction_link = EvidenceLink(
        evidence_id=contradiction.id,
        competency_identity_id=competency_id,
        criterion_identity_id=criterion.criterion_identity_id,
        criterion_definition_id=criterion.id,
        scale_version_id=semantic.scale_version_id,
        level_id=criterion.level_id,
        effect="contradicts",
        relevance="primary",
        provenance_json="{}",
        created_at=base + 2,
    )
    db.add(contradiction_link)
    db.commit()
    same_cutoff = run_analysis(
        db,
        idempotency_key="analysis-real-cutoff-same",
        purpose="learning_control",
        cutoff_at=base + 1,
    )
    later = run_analysis(
        db,
        idempotency_key="analysis-real-cutoff-later",
        purpose="learning_control",
        cutoff_at=base + 3,
    )
    db.commit()
    assert same_cutoff.input_hash == first.input_hash
    assert same_cutoff.output_hash == first.output_hash
    assert later.output_hash != first.output_hash

    db.add(
        EvidenceLinkRetraction(
            evidence_link_id=contradiction_link.id,
            reason="Correction",
            actor_kind="user",
            created_at=base + 4,
        )
    )
    db.commit()
    after_retraction = run_analysis(
        db,
        idempotency_key="analysis-real-cutoff-retracted",
        purpose="learning_control",
        cutoff_at=base + 5,
    )
    db.commit()
    after_retraction_facts = json.loads(after_retraction.normalized_facts_json)
    after_retraction_target = next(
        item["payload"] for item in after_retraction_facts if item["fact_type"] == "target_state"
    )
    assert after_retraction_target["comparisonStatus"] == "at_target"
    assert after_retraction_target["readinessGates"][0]["state"] == "met"
    retraction_lineage = json.loads(db.get(AnalysisRun, after_retraction.run_id).input_lineage_json)
    retraction_history = retraction_lineage["capabilityInputs"][0]["inputPayload"]
    assert retraction_history["evidenceCorrectionHistory"]["linkRetractions"]

    db.add(
        EvidenceInvalidation(
            evidence_id=support.id,
            reason="Source invalidated",
            actor_kind="user",
            created_at=base + 6,
        )
    )
    db.commit()
    after_invalidation = run_analysis(
        db,
        idempotency_key="analysis-real-cutoff-invalidated",
        purpose="learning_control",
        cutoff_at=base + 7,
    )
    db.commit()
    invalidated_target = next(
        item["payload"]
        for item in json.loads(after_invalidation.normalized_facts_json)
        if item["fact_type"] == "target_state"
    )
    assert invalidated_target["comparisonStatus"] == "unknown"
    assert invalidated_target["readinessGates"][0]["state"] == "not_met"
    public_invalidated = load_public_analysis_snapshot(db, after_invalidation.id)
    assert public_invalidated.signals
    assert all(item.facts.signal_type == item.signal_type for item in public_invalidated.signals)
    invalidation_lineage = json.loads(
        db.get(AnalysisRun, after_invalidation.run_id).input_lineage_json
    )
    invalidation_history = invalidation_lineage["capabilityInputs"][0]["inputPayload"]
    assert invalidation_history["evidenceCorrectionHistory"]["invalidations"]

    def add_support_case(
        label: str,
        *,
        strength: str,
        independence: str,
        source_confidence: str,
        created_at: int,
    ) -> Evidence:
        item = Evidence(
            evidence_type="assessment",
            source_type="analysis_test",
            source_id=label,
            source_role="fact",
            title=label,
            strength=strength,
            independence=independence,
            source_confidence=source_confidence,
            occurred_at=created_at,
            created_at=created_at,
            provenance_json=json.dumps({"context_id": label}),
            policy_version="evidence-policy/v1",
            schema_version=1,
            authoritative_for_downgrade=False,
        )
        db.add(item)
        db.flush()
        db.add(
            EvidenceLink(
                evidence_id=item.id,
                competency_identity_id=competency_id,
                criterion_identity_id=criterion.criterion_identity_id,
                criterion_definition_id=criterion.id,
                scale_version_id=semantic.scale_version_id,
                level_id=criterion.level_id,
                effect="supports",
                relevance="primary",
                provenance_json="{}",
                created_at=created_at,
            )
        )
        db.commit()
        return item

    def target_at(label: str, cutoff: int) -> dict[str, Any]:
        snapshot_at_cutoff = run_analysis(
            db,
            idempotency_key=f"analysis-evidence-classification-{label}",
            purpose="learning_control",
            cutoff_at=cutoff,
        )
        db.commit()
        return next(
            item["payload"]
            for item in json.loads(snapshot_at_cutoff.normalized_facts_json)
            if item["fact_type"] == "target_state"
        )

    weak = add_support_case(
        "weak-independent",
        strength="weak",
        independence="independent",
        source_confidence="high",
        created_at=base + 8,
    )
    weak_target = target_at("weak-independent", base + 9)
    assert weak_target["missingEvidenceRequirements"] == [criterion.id]
    assert not weak_target["missingIndependentCriterionIds"]
    db.add(
        EvidenceInvalidation(
            evidence_id=weak.id,
            reason="Next classification case",
            actor_kind="test",
            created_at=base + 10,
        )
    )
    db.commit()

    low_confidence = add_support_case(
        "low-confidence-independent",
        strength="strong",
        independence="independent",
        source_confidence="low",
        created_at=base + 11,
    )
    low_target = target_at("low-confidence-independent", base + 12)
    assert low_target["missingEvidenceRequirements"] == [criterion.id]
    assert not low_target["missingIndependentCriterionIds"]
    db.add(
        EvidenceInvalidation(
            evidence_id=low_confidence.id,
            reason="Next classification case",
            actor_kind="test",
            created_at=base + 13,
        )
    )
    db.commit()

    add_support_case(
        "guided-strong",
        strength="strong",
        independence="guided",
        source_confidence="high",
        created_at=base + 14,
    )
    guided_target = target_at("guided-strong", base + 15)
    assert not guided_target["missingEvidenceRequirements"]
    assert guided_target["missingIndependentCriterionIds"] == [criterion.id]
    assert guided_target["readinessGates"][0]["state"] == "not_met"

    prior_run = CapabilityEvaluationRun(
        idempotency_key="analysis-prior-history-fixture",
        competency_identity_id=competency_id,
        semantic_definition_id=semantic.id,
        scale_version_id=semantic.scale_version_id,
        dimension_id=None,
        scope_key="overall",
        cutoff_at=base + 16,
        generated_at=base + 16,
        criterion_policy_version="criterion-evaluation-policy/v1",
        capability_policy_version="capability-policy/v1",
        evidence_policy_version="evidence-policy/v1",
        downgrade_policy_version="capability-downgrade-policy/v1",
        evidence_set_hash=content_hash(["prior"]),
        input_payload_json='{"fixture":"prior"}',
        input_hash=content_hash({"fixture": "prior"}),
        selected_level_id=criterion.level_id,
        assessment_status="evaluated",
        aggregate_confidence="high",
        confidence_facts_json="{}",
        downgrade_cause=None,
        decisive_evidence_ids_json="[]",
        passed_level_ids_json=json.dumps([criterion.level_id]),
        reasons_json="[]",
        output_hash=content_hash({"fixture": "prior-output"}),
    )
    db.add(prior_run)
    db.flush()
    db.add(
        CriterionEvaluationResult(
            run_id=prior_run.id,
            criterion_definition_id=criterion.id,
            state="demonstrated",
            evidence_set_hash=content_hash([support.id]),
            decisive_evidence_ids_json=json.dumps([support.id]),
            facts_json=json.dumps({"decisiveLinkIds": [support_link.id]}),
        )
    )
    next_event_sequence = (
        db.scalar(
            select(func.max(CapabilityStateEvent.event_sequence)).where(
                CapabilityStateEvent.competency_identity_id == competency_id,
                CapabilityStateEvent.scope_key == "overall",
            )
        )
        or 0
    ) + 1
    db.add(
        CapabilityStateEvent(
            competency_identity_id=competency_id,
            semantic_definition_id=semantic.id,
            dimension_id=None,
            scope_key="overall",
            event_sequence=next_event_sequence,
            previous_level_id=None,
            new_level_id=criterion.level_id,
            previous_assessment_status=None,
            new_assessment_status="evaluated",
            previous_confidence=None,
            new_confidence="high",
            cause_code="promotion",
            decisive_evidence_ids_json="[]",
            evaluation_run_id=prior_run.id,
            created_at=base + 17,
        )
    )
    db.commit()
    before_prior = run_analysis(
        db,
        idempotency_key="analysis-prior-history-before",
        purpose="learning_control",
        cutoff_at=base + 17,
    )
    after_prior = run_analysis(
        db,
        idempotency_key="analysis-prior-history-after",
        purpose="learning_control",
        cutoff_at=base + 18,
    )
    db.commit()
    before_prior_lineage = json.loads(db.get(AnalysisRun, before_prior.run_id).input_lineage_json)
    after_prior_lineage = json.loads(db.get(AnalysisRun, after_prior.run_id).input_lineage_json)
    before_prior_state = before_prior_lineage["capabilityInputs"][0]["inputPayload"]["priorState"]
    assert before_prior_state is None or before_prior_state["evaluation_run_id"] != prior_run.id
    assert (
        after_prior_lineage["capabilityInputs"][0]["inputPayload"]["priorState"][
            "evaluation_run_id"
        ]
        == prior_run.id
    )
    assert before_prior.input_hash != after_prior.input_hash
    prior_input = after_prior_lineage["capabilityInputs"][0]["inputPayload"]
    assert any(item["id"] == prior_run.id for item in prior_input["capabilityEvaluationRunHistory"])
    assert any(
        item["run_id"] == prior_run.id for item in prior_input["criterionEvaluationResultHistory"]
    )
    assert any(
        item["evaluation_run_id"] == prior_run.id
        for item in prior_input["capabilityStateEventHistory"]
    )


def test_analysis_v3_cutoff_replay_hash_and_portable_integrity(db: Session) -> None:
    early = run_analysis(
        db,
        idempotency_key="analysis-cutoff-early",
        purpose="learning_control",
        cutoff_at=1_700_000_000_000,
    )
    payload = {
        "adaptationPhaseConfig": {},
        "targetDurationMsPerActiveDay": 3_600_000,
        "timezone": "UTC",
        "weeklyTargetActiveDays": 5,
    }
    from app.analysis.contracts import canonical_json, content_hash

    db.add(
        DisciplineConfigurationEvent(
            event_sequence=1,
            idempotency_key="late-config",
            configuration_json=canonical_json(payload),
            configuration_hash=content_hash(payload),
            recorded_at=1_700_000_000_000,
            source="test",
        )
    )
    db.commit()
    assert "DISCIPLINE_CONFIGURATION_MISSING" in early.unknown_markers_json
    at_equal_cutoff = run_analysis(
        db,
        idempotency_key="analysis-cutoff-equal",
        purpose="learning_control",
        cutoff_at=1_700_000_000_000,
    )
    after_cutoff = run_analysis(
        db,
        idempotency_key="analysis-cutoff-after",
        purpose="learning_control",
        cutoff_at=1_700_000_000_001,
    )
    db.commit()
    assert "DISCIPLINE_CONFIGURATION_MISSING" in at_equal_cutoff.unknown_markers_json
    assert "DISCIPLINE_CONFIGURATION_MISSING" not in after_cutoff.unknown_markers_json
    live_current = run_analysis(
        db,
        idempotency_key="analysis-live-current",
        purpose="learning_control",
    )
    db.commit()
    validate_domain_integrity(db.connection())

    package = _portable_payload(db)
    assert PORTABLE_SCHEMA_CURRENT == 9
    assert package["manifest"] == PORTABLE_V9_MANIFEST
    tables, _summary = _validate_portable_payload(package, "analysis-v3-portable", 9)
    assert len(tables["analysis_v3_run_lineages"]) == 4
    tampered = copy.deepcopy(package)
    tampered["tables"]["analysis_v3_run_lineages"][0]["policy_bundle_hash"] = "0" * 64
    with pytest.raises(Exception, match="Analysis V3|analysis"):
        _validate_portable_payload(tampered, "analysis-v3-tampered", 9)

    relabeled_policy = copy.deepcopy(package)
    relabeled_lineage = relabeled_policy["tables"]["analysis_v3_run_lineages"][0]
    relabeled_bundle = json.loads(relabeled_lineage["analyzer_bundle_json"])
    relabeled_bundle.update(
        {
            "algorithm": "analysis-algorithm/forged",
            "analysis": "analysis-policy/forged",
            "normalization": "analysis-normalization/forged",
            "purposeMatrix": "analysis-purpose-matrix/forged",
        }
    )
    relabeled_lineage["analysis_algorithm_version"] = relabeled_bundle["algorithm"]
    relabeled_lineage["analysis_policy_version"] = relabeled_bundle["analysis"]
    relabeled_lineage["normalization_schema_version"] = relabeled_bundle["normalization"]
    relabeled_lineage["analyzer_bundle_json"] = canonical_json(relabeled_bundle)
    relabeled_lineage["policy_bundle_hash"] = content_hash(relabeled_bundle)
    relabeled_run = next(
        item
        for item in relabeled_policy["tables"]["analysis_runs"]
        if item["id"] == relabeled_lineage["run_id"]
    )
    relabeled_run["algorithm_version"] = relabeled_bundle["algorithm"]
    relabeled_snapshot = next(
        item
        for item in relabeled_policy["tables"]["analysis_snapshots"]
        if item["run_id"] == relabeled_lineage["run_id"]
    )
    relabeled_snapshot["policy_versions_json"] = canonical_json(relabeled_bundle)
    relabeled_detail = next(
        item
        for item in relabeled_policy["tables"]["analysis_v3_snapshot_details"]
        if item["snapshot_id"] == relabeled_snapshot["id"]
    )
    relabeled_detail["purpose_matrix_version"] = relabeled_bundle["purposeMatrix"]
    relabeled_gaps = [
        {
            "stable_key": item["stable_key"],
            "competency_identity_id": item["competency_identity_id"],
            "dimension_key": item["dimension_key"],
            "severity": item["severity"],
            "comparison_status": item["comparison_status"],
            "payload": json.loads(item["payload_json"]),
            "input_lineage": json.loads(item["input_lineage_json"]),
        }
        for item in sorted(
            (
                row
                for row in relabeled_policy["tables"]["analysis_v3_competency_gaps"]
                if row["snapshot_id"] == relabeled_snapshot["id"]
            ),
            key=lambda row: row["ordinal"],
        )
    ]
    relabeled_snapshot["output_hash"] = content_hash(
        {
            "purpose": relabeled_snapshot["purpose"],
            "cutoffAt": relabeled_snapshot["cutoff_at"],
            "inputHash": relabeled_snapshot["input_hash"],
            "policyBundle": relabeled_bundle,
            "facts": json.loads(relabeled_snapshot["normalized_facts_json"]),
            "gaps": relabeled_gaps,
            "signals": json.loads(relabeled_snapshot["signals_json"]),
            "unknownMarkers": json.loads(relabeled_snapshot["unknown_markers_json"]),
            "completeness": relabeled_snapshot["completeness"],
            "envelopeReferences": {
                "targetProfileId": relabeled_snapshot["target_profile_id"],
                "targetProfileVersionId": relabeled_snapshot["target_profile_version_id"],
                "learningGraphReference": relabeled_snapshot["learning_graph_reference"],
                "curriculumReference": relabeled_snapshot["curriculum_reference"],
                "semanticDefinitionReferences": json.loads(
                    relabeled_snapshot["semantic_definition_references_json"]
                ),
                "capabilityScaleVersionReferences": json.loads(
                    relabeled_snapshot["capability_scale_version_references_json"]
                ),
            },
        }
    )
    with pytest.raises(Exception, match="Analysis V3|analysis"):
        _validate_portable_payload(relabeled_policy, "analysis-v3-policy-relabel", 9)

    # Make stored facts and every public hash internally self-consistent. Restore must
    # still reject them because the pinned analyzer cannot reproduce the alteration.
    coherent_tamper = copy.deepcopy(package)
    fact_row = coherent_tamper["tables"]["analysis_v3_normalized_facts"][0]
    snapshot_id = fact_row["snapshot_id"]
    altered_payload = json.loads(fact_row["payload_json"])
    altered_payload["coherentTamper"] = True
    fact_row["payload_json"] = canonical_json(altered_payload)

    def snapshot_rows(table: str) -> list[dict[str, Any]]:
        return sorted(
            (
                item
                for item in coherent_tamper["tables"][table]
                if item["snapshot_id"] == snapshot_id
            ),
            key=lambda item: item["ordinal"],
        )

    facts = [
        {
            "stable_key": item["stable_key"],
            "fact_type": item["fact_type"],
            "subject_type": item["subject_type"],
            "subject_id": item["subject_id"],
            "payload": json.loads(item["payload_json"]),
        }
        for item in snapshot_rows("analysis_v3_normalized_facts")
    ]
    gaps = [
        {
            "stable_key": item["stable_key"],
            "competency_identity_id": item["competency_identity_id"],
            "dimension_key": item["dimension_key"],
            "severity": item["severity"],
            "comparison_status": item["comparison_status"],
            "payload": json.loads(item["payload_json"]),
            "input_lineage": json.loads(item["input_lineage_json"]),
        }
        for item in snapshot_rows("analysis_v3_competency_gaps")
    ]
    signals = [
        {
            "stable_key": item["stable_key"],
            "signal_type": item["signal_type"],
            "subject_type": item["subject_type"],
            "subject_id": item["subject_id"],
            "dimension_key": item["dimension_key"],
            "severity": item["severity"],
            "reason_codes": json.loads(item["reason_codes_json"]),
            "decisive_facts": json.loads(item["decisive_facts_json"]),
            "analyzer_policy_version": item["analyzer_policy_version"],
            "generated_cutoff_at": item["generated_cutoff_at"],
        }
        for item in snapshot_rows("analysis_v3_signals")
    ]
    unknowns = [
        {
            "field_path": item["field_path"],
            "subject_type": item["subject_type"],
            "subject_id": item["subject_id"],
            "reason_code": item["reason_code"],
        }
        for item in snapshot_rows("analysis_v3_unknown_markers")
    ]
    coherent_snapshot = next(
        item
        for item in coherent_tamper["tables"]["analysis_snapshots"]
        if item["id"] == snapshot_id
    )
    coherent_detail = next(
        item
        for item in coherent_tamper["tables"]["analysis_v3_snapshot_details"]
        if item["snapshot_id"] == snapshot_id
    )
    coherent_snapshot["normalized_facts_json"] = canonical_json(facts)
    coherent_detail["facts_hash"] = content_hash(facts)
    coherent_snapshot["output_hash"] = content_hash(
        {
            "purpose": coherent_snapshot["purpose"],
            "cutoffAt": coherent_snapshot["cutoff_at"],
            "inputHash": coherent_snapshot["input_hash"],
            "policyBundle": json.loads(coherent_snapshot["policy_versions_json"]),
            "facts": facts,
            "gaps": gaps,
            "signals": signals,
            "unknownMarkers": unknowns,
            "completeness": coherent_snapshot["completeness"],
            "envelopeReferences": {
                "targetProfileId": coherent_snapshot["target_profile_id"],
                "targetProfileVersionId": coherent_snapshot["target_profile_version_id"],
                "learningGraphReference": coherent_snapshot["learning_graph_reference"],
                "curriculumReference": coherent_snapshot["curriculum_reference"],
                "semanticDefinitionReferences": json.loads(
                    coherent_snapshot["semantic_definition_references_json"]
                ),
                "capabilityScaleVersionReferences": json.loads(
                    coherent_snapshot["capability_scale_version_references_json"]
                ),
            },
        }
    )
    with pytest.raises(Exception, match="Analysis V3|analysis"):
        _validate_portable_payload(coherent_tamper, "analysis-v3-coherent-tamper", 9)
    for field, value in (
        ("scopeKey", "other-scope"),
        ("purpose", "candidate_readiness"),
        ("exclusiveCutoffAt", 1),
        ("sourceGeneration", -1),
        ("policyBundleHash", "f" * 64),
    ):
        bad_checkpoint = copy.deepcopy(package)
        checkpoint = bad_checkpoint["analysisV3CurrentCheckpoint"]
        checkpoint["states"][0][field] = value
        checkpoint["checkpointHash"] = content_hash(
            {key: item for key, item in checkpoint.items() if key != "checkpointHash"}
        )
        with pytest.raises(Exception, match="Analysis V3|analysis"):
            _validate_portable_payload(bad_checkpoint, f"analysis-checkpoint-{field}", 9)

    bad_signal_cutoff = copy.deepcopy(package)
    bad_signal_cutoff["tables"]["analysis_v3_signals"][0]["generated_cutoff_at"] = 1
    with pytest.raises(Exception, match="Analysis V3|analysis"):
        _validate_portable_payload(bad_signal_cutoff, "analysis-signal-cutoff", 9)

    missing_lineage = copy.deepcopy(package)
    missing_lineage["tables"]["analysis_v3_run_lineages"] = missing_lineage["tables"][
        "analysis_v3_run_lineages"
    ][1:]
    with pytest.raises(Exception, match="Analysis V3|analysis"):
        _validate_portable_payload(missing_lineage, "analysis-missing-lineage", 9)

    expected_current_output = live_current.output_hash
    run_count = db.scalar(select(func.count()).select_from(AnalysisRun))
    _apply_portable_restore(
        db,
        package,
        True,
        package_id="analysis-v3-restore-parity",
        schema_version=9,
    )
    db.commit()
    assert db.scalar(select(func.count()).select_from(AnalysisRun)) == run_count
    # Immutable history and the pointer survive restore; restore never executes Analysis.
    assert db.scalar(select(func.count()).select_from(AnalysisV3RunLineage)) == run_count
    assert (
        db.scalar(select(AnalysisRun.id).where(AnalysisRun.id == live_current.run_id))
        == live_current.run_id
    )
    assert (
        db.scalar(
            select(AnalysisSnapshot.output_hash).where(
                AnalysisSnapshot.run_id == live_current.run_id
            )
        )
        == expected_current_output
    )
    restored_current = db.get(AnalysisV3CurrentState, ("learning-control", "learning_control"))
    assert restored_current is not None
    assert restored_current.run_id == live_current.run_id

    stale_package = copy.deepcopy(package)
    stale_checkpoint = stale_package["analysisV3CurrentCheckpoint"]
    stale_checkpoint["states"][0]["status"] = "stale"
    stale_checkpoint["checkpointHash"] = content_hash(
        {key: item for key, item in stale_checkpoint.items() if key != "checkpointHash"}
    )
    _apply_portable_restore(
        db,
        stale_package,
        True,
        package_id="analysis-v3-restore-stale",
        schema_version=9,
    )
    db.commit()
    assert db.scalar(select(func.count()).select_from(AnalysisRun)) == run_count
    assert (
        db.scalar(
            select(func.count())
            .select_from(ProjectionInvalidation)
            .where(
                ProjectionInvalidation.projection_kind == "analysis",
                ProjectionInvalidation.status == "pending",
            )
        )
        == 1
    )


@pytest.mark.parametrize(
    ("local_midnight", "expected_completed_day", "completed_day_hours"),
    [
        ((2026, 3, 9), "2026-03-08", 23),
        ((2026, 11, 2), "2026-11-01", 25),
    ],
)
def test_analysis_v3_completed_local_day_is_dst_correct(
    db: Session,
    local_midnight: tuple[int, int, int],
    expected_completed_day: str,
    completed_day_hours: int,
) -> None:
    timezone_name = "America/New_York"
    year, month, day = local_midnight
    cutoff_datetime = datetime(year, month, day, tzinfo=ZoneInfo(timezone_name))
    prior_midnight = datetime.fromisoformat(expected_completed_day).replace(
        tzinfo=ZoneInfo(timezone_name)
    )
    assert int((cutoff_datetime.timestamp() - prior_midnight.timestamp()) / 3600) == (
        completed_day_hours
    )
    cutoff = int(cutoff_datetime.timestamp() * 1000)
    configuration = {
        "adaptationPhaseConfig": {},
        "targetDurationMsPerActiveDay": 3_600_000,
        "timezone": timezone_name,
        "weeklyTargetActiveDays": 5,
    }
    db.add(
        DisciplineConfigurationEvent(
            event_sequence=1,
            idempotency_key=f"analysis-dst-{month}",
            configuration_json=json.dumps(configuration, sort_keys=True, separators=(",", ":")),
            configuration_hash=content_hash(configuration),
            recorded_at=cutoff - 1,
            source="test",
        )
    )
    db.commit()
    snapshot = run_analysis(
        db,
        idempotency_key=f"analysis-dst-run-{month}",
        purpose="learning_control",
        cutoff_at=cutoff,
    )
    db.commit()
    assert snapshot.timezone == timezone_name
    assert snapshot.completed_through_date == expected_completed_day


def test_public_analysis_snapshot_is_deeply_immutable(db: Session) -> None:
    assert current_analysis(db)["status"] == "missing"
    with pytest.raises(Exception, match="snapshot does not exist"):
        load_public_analysis_snapshot(db, "missing-snapshot")
    snapshot = run_analysis(
        db,
        idempotency_key="analysis-public-contract",
        purpose="learning_control",
    )
    db.commit()

    public = load_public_analysis_snapshot(db, snapshot.id)
    assert isinstance(public.facts, tuple)
    assert isinstance(public.policy_versions, tuple)
    assert all(isinstance(item.payload, tuple) for item in public.facts)
    assert {item.fact_type for item in public.facts} == {
        "curriculum_catalog",
        "discipline_variance",
        "workload_risk",
    }
    assert public.discipline_configuration_reference == "missing"
    assert isinstance(public.input_lineage, tuple)
    with pytest.raises(FrozenInstanceError):
        public.completeness = "complete"  # type: ignore[misc]


def test_analysis_v3_public_fact_variants_are_typed() -> None:
    def row(fact_type: str, payload: dict[str, object]) -> AnalysisV3NormalizedFact:
        return AnalysisV3NormalizedFact(
            id=f"fact-{fact_type}",
            snapshot_id="snapshot",
            ordinal=0,
            stable_key=f"{fact_type}|fixture",
            fact_type=fact_type,
            subject_type="fixture",
            subject_id="fixture",
            payload_json=json.dumps(payload),
        )

    allocation = _typed_fact(
        row(
            "allocation",
            {
                "durationMs": 1_000,
                "assignedTotalDurationMs": 2_000,
                "actualBasisPoints": 5_000,
                "minimumBasisPoints": None,
                "maximumBasisPoints": 8_000,
                "missBasisPoints": 0,
                "sufficientData": True,
            },
        )
    )
    assert allocation.actual_basis_points == 5_000
    assert allocation.minimum_basis_points is None
    assert allocation.maximum_basis_points == 8_000

    curriculum = _typed_fact(
        row(
            "curriculum_catalog",
            {
                "activeVersionReferences": [{"versionId": "curriculum-v1"}],
                "unitCount": 2,
                "assessmentRubricCount": 1,
                "inputHash": "a" * 64,
            },
        )
    )
    assert curriculum.unit_count == 2

    project = _typed_fact(
        row(
            "project_task_state",
            {
                "projectId": "project",
                "projectVersionId": "project-v1",
                "availabilityState": "available",
                "readinessState": "met",
                "candidateUsabilityState": "usable",
                "inputHash": "b" * 64,
            },
        )
    )
    assert project.candidate_usability_state == "usable"

    discipline = _typed_fact(
        row(
            "discipline_variance",
            {"severity": None, "reasonCode": "DISCIPLINE_ON_TARGET"},
        )
    )
    workload = _typed_fact(
        row(
            "workload_risk",
            {
                "severity": "attention",
                "reasonCode": "ACTIVE_DAY_DURATION_ABOVE_TARGET",
                "surgeComparisonStatus": "unknown",
            },
        )
    )
    assert discipline.severity is None
    assert workload.severity == "attention"
    with pytest.raises(Exception, match="unknown fact type"):
        _typed_fact(row("not_a_fact", {}))


def test_analysis_v3_failed_run_rejects_coherent_policy_relabel(db: Session) -> None:
    record_failed_analysis_run(
        db,
        idempotency_key="analysis-failed-policy-lineage",
        purpose="learning_control",
        cutoff_at=1_700_000_000_000,
        replay_of_run_id=None,
        error=RuntimeError("fixture"),
    )
    db.commit()
    package = _portable_payload(db)
    missing_lineage = copy.deepcopy(package)
    missing_lineage["tables"]["analysis_v3_run_lineages"] = []
    with pytest.raises(Exception, match="Analysis V3|analysis"):
        _validate_portable_payload(missing_lineage, "analysis-failed-missing-lineage", 9)
    lineage = package["tables"]["analysis_v3_run_lineages"][0]
    bundle = json.loads(lineage["analyzer_bundle_json"])
    bundle.update(
        {
            "algorithm": "analysis-algorithm/forged-failed",
            "analysis": "analysis-policy/forged-failed",
            "normalization": "analysis-normalization/forged-failed",
        }
    )
    lineage["analysis_algorithm_version"] = bundle["algorithm"]
    lineage["analysis_policy_version"] = bundle["analysis"]
    lineage["normalization_schema_version"] = bundle["normalization"]
    lineage["analyzer_bundle_json"] = json.dumps(bundle, sort_keys=True, separators=(",", ":"))
    lineage["policy_bundle_hash"] = content_hash(bundle)
    run = next(
        item for item in package["tables"]["analysis_runs"] if item["id"] == lineage["run_id"]
    )
    run["algorithm_version"] = bundle["algorithm"]
    with pytest.raises(Exception, match="Analysis V3|analysis"):
        _validate_portable_payload(package, "analysis-failed-policy-relabel", 9)


def test_analysis_v3_initialization_supersedes_v1_once(db: Session) -> None:
    db.add_all(
        [
            ProjectionInvalidation(
                projection_kind="analysis",
                subject_type="legacy",
                subject_id=status,
                source_fact_id=f"legacy-{status}",
                target_policy_version="analysis-policy/v1",
                status=status,
                attempt_count=0,
                requested_at=index + 1,
            )
            for index, status in enumerate(("pending", "running"))
        ]
    )
    db.commit()

    initialize_analysis_v3(db)
    initialize_analysis_v3(db)

    legacy = db.scalars(
        select(ProjectionInvalidation)
        .where(ProjectionInvalidation.target_policy_version == "analysis-policy/v1")
        .order_by(ProjectionInvalidation.subject_id)
    ).all()
    assert {item.status for item in legacy} == {"superseded_no_handler"}
    assert all(
        json.loads(item.error_json or "{}") == {"reason": "ANALYSIS_V1_NO_RECOMPUTE_HANDLER"}
        for item in legacy
    )
    bootstraps = db.scalars(
        select(ProjectionInvalidation).where(
            ProjectionInvalidation.projection_kind == "analysis",
            ProjectionInvalidation.target_policy_version == ANALYSIS_POLICY_VERSION,
        )
    ).all()
    assert len(bootstraps) == 1
    assert bootstraps[0].source_fact_id == "analysis-v3-bootstrap"


def test_analysis_normalization_upgrade_enqueues_and_recomputes_without_rewrite(
    db: Session,
) -> None:
    legacy = run_analysis(
        db,
        idempotency_key="analysis-normalization-upgrade-legacy",
        purpose="learning_control",
        normalization_schema_version=LEGACY_NORMALIZATION_SCHEMA_VERSION,
    )
    db.commit()
    legacy_input_hash = legacy.input_hash
    legacy_output_hash = legacy.output_hash
    assert current_analysis(db)["status"] == "stale"

    initialize_analysis_v3(db)
    initialize_analysis_v3(db)
    upgrades = db.scalars(
        select(ProjectionInvalidation).where(
            ProjectionInvalidation.source_fact_id
            == "analysis-normalization-upgrade:analysis-normalization/v3.1"
        )
    ).all()
    assert len(upgrades) == 1
    assert upgrades[0].status == "pending"
    assert drain_analysis_invalidations(db) >= 1

    state = db.get(AnalysisV3CurrentState, ("learning-control", "learning_control"))
    assert state is not None and state.status == "current"
    current_lineage = db.get(AnalysisV3RunLineage, state.run_id)
    assert current_lineage is not None
    assert current_lineage.normalization_schema_version == "analysis-normalization/v3.1"
    assert current_analysis(db)["status"] == "current"
    db.refresh(legacy)
    assert (legacy.input_hash, legacy.output_hash) == (legacy_input_hash, legacy_output_hash)


def test_analysis_v3_invalidation_drain_recomputes_purposes_and_records_failure(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize_analysis_v3(db)
    run_analysis(
        db,
        idempotency_key="analysis-candidate-before-drain",
        purpose="candidate_readiness",
    )
    scoped_invalidation = ProjectionInvalidation(
        projection_kind="analysis",
        subject_type="analysis_scope",
        subject_id="learning-control:candidate_readiness",
        source_fact_id="candidate-readiness-test",
        target_policy_version=ANALYSIS_POLICY_VERSION,
        status="pending",
        attempt_count=0,
        requested_at=utc_now_ms(),
    )
    db.add(scoped_invalidation)
    db.commit()

    assert drain_analysis_invalidations(db) == 2
    states = db.scalars(
        select(AnalysisV3CurrentState).order_by(AnalysisV3CurrentState.purpose)
    ).all()
    assert {item.purpose for item in states} == {"candidate_readiness", "learning_control"}
    assert {item.status for item in states} == {"current"}
    candidate_state = next(item for item in states if item.purpose == "candidate_readiness")
    db.refresh(scoped_invalidation)
    assert scoped_invalidation.result_run_id == candidate_state.run_id
    learning_state = next(item for item in states if item.purpose == "learning_control")
    monkeypatch.setattr(
        analysis_service,
        "utc_now_ms",
        lambda: learning_state.exclusive_cutoff_at + 2 * 86_400_000,
    )
    assert current_analysis(db)["status"] == "stale"
    monkeypatch.undo()

    db.add(
        ProjectionInvalidation(
            projection_kind="analysis",
            subject_type="test_fact",
            subject_id="global",
            source_fact_id="analysis-drain-failure-source",
            target_policy_version=ANALYSIS_POLICY_VERSION,
            status="pending",
            attempt_count=0,
            requested_at=utc_now_ms(),
        )
    )
    db.commit()

    def fail_run(*_args: object, **_kwargs: object) -> AnalysisSnapshot:
        raise RuntimeError("deterministic test failure")

    monkeypatch.setattr(analysis_service, "run_analysis", fail_run)
    assert drain_analysis_invalidations(db) == 0
    assert {item.status for item in db.scalars(select(AnalysisV3CurrentState)).all()} == {"failed"}
    failed = db.scalars(
        select(AnalysisRun)
        .where(AnalysisRun.status == "failed")
        .order_by(AnalysisRun.generated_at.desc())
    ).first()
    assert failed is not None
    assert json.loads(failed.failure_metadata_json or "{}")["type"] == "RuntimeError"


def test_0015_preserves_v1_history_and_refuses_populated_downgrade(tmp_path: Path) -> None:
    path = tmp_path / "analysis-v3.sqlite3"
    config = _config(path)
    command.upgrade(config, "0014_roadmap_projection_state")
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "INSERT INTO analysis_runs "
            "(id, idempotency_key, purpose, scope_json, generated_at, cutoff_at, status, "
            "algorithm_version, configuration_reference, configuration_hash, "
            "input_lineage_json, input_hash, application_version, failure_metadata_json, "
            "completeness_metadata_json) VALUES "
            "('v1-failed', 'v1-failed', 'v1_recommendation_compat', '{}', 1, 2, 'failed', "
            "'recommendation-policy/v1', 'legacy', 'legacy-hash', '[]', 'legacy-input', "
            "'1.0.0', '{}', '{}')"
        )
        connection.commit()
        before = connection.execute(
            "SELECT analysis_runs.id, algorithm_version, analysis_runs.input_hash, output_hash "
            "FROM analysis_runs "
            "LEFT JOIN analysis_snapshots ON analysis_snapshots.run_id = analysis_runs.id"
        ).fetchall()
    finally:
        connection.close()
    command.upgrade(config, "0015_analysis_v3")
    connection = sqlite3.connect(path)
    try:
        after = connection.execute(
            "SELECT analysis_runs.id, algorithm_version, analysis_runs.input_hash, "
            "analysis_snapshots.output_hash FROM analysis_runs LEFT JOIN analysis_snapshots "
            "ON analysis_snapshots.run_id = analysis_runs.id"
        ).fetchall()
        assert before == after
        connection.execute(
            "INSERT INTO analysis_runs "
            "(id, idempotency_key, purpose, scope_json, generated_at, cutoff_at, status, "
            "algorithm_version, configuration_reference, configuration_hash, "
            "input_lineage_json, input_hash, application_version, failure_metadata_json, "
            "completeness_metadata_json) VALUES "
            "('v3-failed', 'v3-failed', 'learning_control', '{}', 1, 2, 'failed', "
            "'analysis-algorithm/v3.0', 'missing', 'x', '{}', 'x', '2.0.0', '{}', '{}')"
        )
        connection.execute(
            "INSERT INTO analysis_v3_run_lineages VALUES "
            "('v3-failed', 'analysis-algorithm/v3.0', 'analysis-policy/v3.0', "
            "'analysis-normalization/v3.0', '{}', 'x', 0, NULL)"
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(RuntimeError, match="Refusing to downgrade immutable Analysis V3 history"):
        command.downgrade(config, "0014_roadmap_projection_state")


def test_0015_populated_upgrade_preserves_rows_and_builds_exact_config(
    tmp_path: Path,
) -> None:
    path = tmp_path / "analysis-v3-populated.sqlite3"
    config = _config(path)
    command.upgrade(config, "0014_roadmap_projection_state")
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "INSERT INTO discipline_profiles "
            "(id, weekly_target_active_days, target_duration_ms_per_active_day, timezone, "
            "adaptation_phase_config_json, updated_at) VALUES (1, 4, 2700000, "
            "'Europe/Istanbul', '{\"enabled\":true}', 1700000000000)"
        )
        connection.execute(
            "INSERT INTO projection_invalidations "
            "(id, projection_kind, subject_type, subject_id, source_fact_id, "
            "target_policy_version, subject_sequence, status, attempt_count, requested_at, "
            "started_at, completed_at, attempt_run_id, result_run_id, error_json) VALUES "
            "('legacy-invalidation', 'analysis', 'scope', 'global', 'legacy-source', "
            "'analysis-policy/v1', 1, 'pending', 2, 10, NULL, NULL, 'attempt-1', NULL, NULL)"
        )
        connection.commit()
        before = connection.execute("SELECT * FROM projection_invalidations ORDER BY id").fetchall()
    finally:
        connection.close()

    command.upgrade(config, "0015_analysis_v3")
    connection = sqlite3.connect(path)
    try:
        after = connection.execute("SELECT * FROM projection_invalidations ORDER BY id").fetchall()
        assert after == before
        event = connection.execute(
            "SELECT event_sequence, idempotency_key, configuration_json, configuration_hash, "
            "recorded_at, source FROM discipline_configuration_events"
        ).fetchone()
        assert event is not None
        expected = (
            '{"adaptationPhaseConfig":{"enabled":true},'
            '"targetDurationMsPerActiveDay":2700000,"timezone":"Europe/Istanbul",'
            '"weeklyTargetActiveDays":4}'
        )
        assert event == (
            1,
            "analysis-v3-config-baseline",
            expected,
            content_hash(json.loads(expected)),
            1_700_000_000_000,
            "migration_baseline",
        )
    finally:
        connection.close()

    command.upgrade(config, "head")
    command.check(config)


@pytest.mark.parametrize(
    "partial_table",
    ["analysis_v3_signals", "_alembic_tmp_projection_invalidations"],
)
def test_partial_0015_migration_is_refused(tmp_path: Path, partial_table: str) -> None:
    path = tmp_path / "analysis-v3-partial.sqlite3"
    config = _config(path)
    command.upgrade(config, "0014_roadmap_projection_state")
    connection = sqlite3.connect(path)
    try:
        connection.execute(f'CREATE TABLE "{partial_table}" (id TEXT PRIMARY KEY)')
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(RuntimeError, match="ambiguously partial Analysis V3 migration"):
        database.run_migrations(f"sqlite:///{path}")


def test_0015_downgrade_refuses_native_discipline_history(tmp_path: Path) -> None:
    path = tmp_path / "analysis-v3-discipline-history.sqlite3"
    config = _config(path)
    command.upgrade(config, "0015_analysis_v3")
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "INSERT INTO discipline_configuration_events "
            "(id, event_sequence, idempotency_key, configuration_json, configuration_hash, "
            "recorded_at, source) VALUES ('native', 1, 'native', '{}', 'hash', 1, 'settings')"
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(
        RuntimeError, match="Refusing to downgrade immutable discipline configuration history"
    ):
        command.downgrade(config, "0014_roadmap_projection_state")


def test_v5_to_v6_adapter_creates_only_exact_discipline_baseline() -> None:
    source = {
        "discipline_profiles": [
            {
                "weekly_target_active_days": 5,
                "target_duration_ms_per_active_day": 3_600_000,
                "timezone": "Europe/Istanbul",
                "adaptation_phase_config_json": '{"enabled":true}',
                "updated_at": 1_700_000_000_000,
            }
        ]
    }
    first = copy.deepcopy(source)
    second = copy.deepcopy(source)
    assert upgrade_v5_to_v6_tables(first) == upgrade_v5_to_v6_tables(second)
    assert first == second
    assert set(first) == {"discipline_profiles", *PORTABLE_V6_ANALYSIS_TABLES}
    assert all(
        first[name] == []
        for name in PORTABLE_V6_ANALYSIS_TABLES
        if name != "discipline_configuration_events"
    )
    event = first["discipline_configuration_events"][0]
    assert event["source"] == "portable_v5_compatibility_baseline"
    assert event["recorded_at"] == 1_700_000_000_000
    assert event["configuration_json"] == (
        '{"adaptationPhaseConfig":{"enabled":true},'
        '"targetDurationMsPerActiveDay":3600000,"timezone":"Europe/Istanbul",'
        '"weeklyTargetActiveDays":5}'
    )


def test_analysis_v3_has_no_reverse_recommendation_dependency() -> None:
    app_root = Path(__file__).resolve().parents[1] / "app"
    paths = [*app_root.joinpath("analysis").rglob("*.py"), app_root / "analysis_sources.py"]
    forbidden = {"app.recommendations", "app.recommendation", "app.today"}
    for path in paths:
        tree = ast.parse(path.read_text())
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        assert not any(
            imported == blocked or imported.startswith(f"{blocked}.")
            for imported in imports
            for blocked in forbidden
        )
