from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest
from app.analysis.v1_compat import build_v1_recommendation_envelope
from app.analytics import build_analytics
from app.models import (
    AnalysisRun,
    AnalysisSnapshot,
    CompetencyIdentity,
    CompetencyState,
    CompetencyStatusEvent,
    DisciplineProfile,
    ExitCriterionIdentity,
    GeneratedReport,
    LearningSession,
    RecommendationSnapshot,
    VerificationRecord,
)
from app.recommendations import build_recommendation, round_half_up
from app.reports import generate_report
from app.time_utils import datetime_to_epoch_ms
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

NOW = datetime_to_epoch_ms(datetime(2026, 9, 1, 12, tzinfo=UTC))


def _at(days_before: int, hour: int = 12) -> int:
    instant = datetime(2026, 9, 1, hour, tzinfo=UTC) - timedelta(days=days_before)
    return datetime_to_epoch_ms(instant)


def _identity(db: Session, stable_key: str) -> CompetencyIdentity:
    return db.scalar(select(CompetencyIdentity).where(CompetencyIdentity.stable_key == stable_key))


async def test_analytics_qualifying_data_formulas_and_blocker_resolution(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    identity = _identity(db, "python.basics")
    db.add_all(
        [
            LearningSession(
                competency_identity_id=identity.id,
                session_mode="manual",
                activity_type="learning",
                assistance_mode="docs_only",
                started_at=_at(3),
                ended_at=_at(3) + 1_800_000,
                accumulated_duration_ms=1_800_000,
                duration_ms=1_800_000,
                outcome="completed",
            ),
            LearningSession(
                competency_identity_id=identity.id,
                session_mode="manual",
                activity_type="coding",
                assistance_mode="none",
                started_at=_at(2),
                ended_at=_at(2) + 1_800_000,
                accumulated_duration_ms=1_800_000,
                duration_ms=1_800_000,
                outcome="partial",
            ),
            LearningSession(
                competency_identity_id=identity.id,
                session_mode="manual",
                activity_type="debugging",
                assistance_mode="ai_assisted",
                started_at=_at(1),
                ended_at=_at(1) + 600_000,
                accumulated_duration_ms=600_000,
                duration_ms=600_000,
                outcome="blocked",
            ),
            LearningSession(
                competency_identity_id=identity.id,
                session_mode="timed",
                timed_state="cancelled",
                activity_type="reading",
                assistance_mode="docs_only",
                started_at=_at(1),
                ended_at=_at(1) + 9_000_000,
                accumulated_duration_ms=9_000_000,
                duration_ms=9_000_000,
                outcome="cancelled",
            ),
        ]
    )
    db.commit()
    analytics = build_analytics(db, now_ms=NOW, range_name="7d")
    assert analytics["totalDurationMs"] == 4_200_000
    assert analytics["activeDays"] == 3
    assert analytics["independentCoding"] == {
        "independentDurationMs": 1_800_000,
        "practicalDurationMs": 1_800_000,
        "ratio": 1.0,
    }
    facts = analytics["competencies"][identity.id]
    assert facts["conceptualEvidenceDurationMs"] == 1_800_000
    assert facts["practicalEvidenceDurationMs"] == 1_800_000
    assert facts["unresolvedTechnicalBlocker"] is True

    db.add(
        LearningSession(
            competency_identity_id=identity.id,
            session_mode="manual",
            activity_type="coding",
            assistance_mode="ai_hint",
            started_at=_at(0),
            ended_at=_at(0) + 300_000,
            accumulated_duration_ms=300_000,
            duration_ms=300_000,
            outcome="completed",
        )
    )
    db.commit()
    cleared = build_analytics(db, now_ms=NOW, range_name="7d")
    assert cleared["competencies"][identity.id]["unresolvedTechnicalBlocker"] is False
    assert cleared["independentCoding"]["ratio"] == 1_800_000 / 2_100_000


async def test_recommendation_is_deterministic_and_uses_activity_precedence(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    basics = _identity(db, "python.basics")
    first = build_recommendation(db, now_ms=NOW)
    second = build_recommendation(db, now_ms=NOW)
    assert first == second
    assert first["primary"]["stableKey"] == "python.basics"
    assert first["primary"]["activity"] == "learning"
    assert first["primary"]["activityReasonCode"] == "ACTIVITY_LEARNING_CONCEPTUAL_THRESHOLD_UNMET"
    assert "CORE_COMPETENCY" in first["primary"]["reasonCodes"]
    assert "BLOCKS_DOWNSTREAM_SKILLS" in first["primary"]["reasonCodes"]

    criterion = db.scalar(
        select(ExitCriterionIdentity).where(
            ExitCriterionIdentity.competency_identity_id == basics.id
        )
    )
    criterion.current_state = "met"
    db.commit()
    verification_ready = build_recommendation(db, now_ms=NOW)
    assert verification_ready["primary"]["activity"] == "verification"

    db.add(
        LearningSession(
            competency_identity_id=basics.id,
            session_mode="manual",
            activity_type="debugging",
            assistance_mode="ai_assisted",
            started_at=_at(1),
            ended_at=_at(1) + 600_000,
            accumulated_duration_ms=600_000,
            duration_ms=600_000,
            outcome="blocked",
        )
    )
    db.commit()
    blocked = build_recommendation(db, now_ms=NOW)
    assert blocked["primary"]["activity"] == "research"
    assert blocked["primary"]["activityReasonCode"] == "ACTIVITY_RESEARCH_UNRESOLVED_BLOCKER"


async def test_today_persists_complete_immutable_v1_analysis_envelope(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, _csrf, _roadmap = configured_client
    first = await client.get("/api/v1/recommendations/today")
    assert first.status_code == 200, first.text
    first_analysis = db.scalars(
        select(AnalysisSnapshot).order_by(AnalysisSnapshot.generated_at)
    ).first()
    assert first_analysis is not None
    first_bytes = {
        "normalized": first_analysis.normalized_facts_json,
        "lineage": first_analysis.input_lineage_json,
        "unknown": first_analysis.unknown_markers_json,
        "outputHash": first_analysis.output_hash,
    }
    facts = json.loads(first_analysis.normalized_facts_json)
    legacy = facts["legacy_v1_recommendation_input"]
    assert first_analysis.purpose == "v1_recommendation_compat"
    assert first_analysis.cutoff_semantics == "exclusive"
    assert first_analysis.cutoff_at > first_analysis.generated_at
    assert first_analysis.completeness == "partial"
    assert legacy["candidateDefinitions"]
    assert "disciplineConstraints" in legacy
    assert "sessionFacts" in legacy
    assert {marker["code"] for marker in json.loads(first_analysis.unknown_markers_json)} >= {
        "TARGET_PROFILE_MISSING",
        "LEARNING_GRAPH_MISSING",
        "CAPABILITY_POLICY_MISSING",
    }
    recommendation = db.scalar(
        select(RecommendationSnapshot).where(
            RecommendationSnapshot.analysis_snapshot_id == first_analysis.id
        )
    )
    assert recommendation is not None

    second = await client.get("/api/v1/recommendations/today")
    assert second.status_code == 200
    db.expire_all()
    assert db.get(AnalysisSnapshot, first_analysis.id) is not None
    unchanged = db.get(AnalysisSnapshot, first_analysis.id)
    assert unchanged is not None
    assert {
        "normalized": unchanged.normalized_facts_json,
        "lineage": unchanged.input_lineage_json,
        "unknown": unchanged.unknown_markers_json,
        "outputHash": unchanged.output_hash,
    } == first_bytes
    assert len(db.scalars(select(AnalysisRun)).all()) == 2
    assert len(db.scalars(select(AnalysisSnapshot)).all()) == 2


def test_v1_analysis_hashes_are_deterministic(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    first = build_v1_recommendation_envelope(db, now_ms=NOW)
    second = build_v1_recommendation_envelope(db, now_ms=NOW)
    assert first == second
    assert first.input_hash == second.input_hash
    assert first.output_hash == second.output_hash


async def test_setup_and_no_eligible_results_persist_analysis_only(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, _csrf = authenticated_client
    setup = await client.get("/api/v1/recommendations/today")
    assert setup.status_code == 200
    assert setup.json()["setupRequired"] is True
    assert len(db.scalars(select(AnalysisSnapshot)).all()) == 1
    assert db.scalars(select(RecommendationSnapshot)).all() == []


async def test_configured_no_eligible_result_persists_analysis_only(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, _csrf, _roadmap = configured_client
    for state in db.scalars(select(CompetencyState)).all():
        state.current_status = "verified"
    db.commit()
    response = await client.get("/api/v1/recommendations/today")
    assert response.status_code == 200
    assert response.json()["primary"] is None
    assert len(db.scalars(select(AnalysisSnapshot)).all()) == 1
    assert db.scalars(select(RecommendationSnapshot)).all() == []


def test_analysis_envelope_excludes_post_cutoff_sessions_and_is_deeply_immutable(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    identity = _identity(db, "python.basics")
    future = LearningSession(
        competency_identity_id=identity.id,
        session_mode="manual",
        activity_type="learning",
        assistance_mode="none",
        started_at=NOW + 10_000,
        ended_at=NOW + 20_000,
        accumulated_duration_ms=10_000,
        duration_ms=10_000,
        outcome="completed",
    )
    future_verification = VerificationRecord(
        competency_identity_id=identity.id,
        verification_source="self",
        method="Future verification",
        result="partial",
        created_at=NOW + 10_000,
    )
    future_event = CompetencyStatusEvent(
        competency_identity_id=identity.id,
        from_status="not_started",
        to_status="learning",
        reason="Future event",
        source="manual",
        created_at=NOW + 10_000,
    )
    db.add_all([future, future_verification, future_event])
    db.commit()
    envelope = build_v1_recommendation_envelope(db, now_ms=NOW)
    sessions = envelope.normalized_facts["legacy_v1_recommendation_input"]["sessionFacts"]
    assert all(item["sessionId"] != future.id for item in sessions)
    lineage_ids = {item["sourceId"] for item in envelope.input_lineage}
    assert future_verification.id not in lineage_ids
    assert future_event.id not in lineage_ids
    with pytest.raises(TypeError):
        envelope.normalized_facts["unexpected"] = True


def test_persisted_analysis_history_rejects_orm_mutation(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    from app.analysis.persistence import persist_envelope

    snapshot = persist_envelope(db, build_v1_recommendation_envelope(db, now_ms=NOW))
    db.commit()
    snapshot.output_hash = "0" * 64
    with pytest.raises((ValueError, StatementError)):
        db.commit()
    db.rollback()


def test_snapshot_windows_use_configured_timezone_across_dst_boundary(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    profile = db.get(DisciplineProfile, 1)
    assert profile is not None
    profile.timezone = "America/New_York"
    identity = _identity(db, "python.basics")
    started = datetime_to_epoch_ms(datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
    session = LearningSession(
        competency_identity_id=identity.id,
        session_mode="manual",
        activity_type="learning",
        assistance_mode="none",
        started_at=started,
        ended_at=started + 60_000,
        accumulated_duration_ms=60_000,
        duration_ms=60_000,
        outcome="completed",
    )
    db.add(session)
    db.commit()
    now = datetime_to_epoch_ms(datetime(2026, 11, 1, 7, 0, tzinfo=UTC))
    envelope = build_v1_recommendation_envelope(db, now_ms=now)
    data = envelope.normalized_facts["legacy_v1_recommendation_input"]
    assert envelope.timezone == "America/New_York"
    assert data["localDate"] == "2026-11-01"
    fact = next(item for item in data["sessionFacts"] if item["sessionId"] == session.id)
    assert fact["localDate"] == "2026-11-01"


def test_recommendation_rounding_is_only_the_canonical_half_up_behavior() -> None:
    assert round_half_up(0.5) == 1
    assert round_half_up(2.49) == 2
    assert round_half_up(2.5) == 3


async def test_reports_are_immutable_and_version_unique(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    identity = _identity(db, "python.basics")
    db.add(
        LearningSession(
            competency_identity_id=identity.id,
            session_mode="manual",
            activity_type="practice",
            assistance_mode="none",
            started_at=_at(2),
            ended_at=_at(2) + 1_200_000,
            accumulated_duration_ms=1_200_000,
            duration_ms=1_200_000,
            outcome="completed",
        )
    )
    db.commit()
    period = date(2026, 8, 30)
    first = generate_report(db, "daily", period, period)
    second = generate_report(db, "daily", period, period)
    assert first.id == second.id
    assert first.structured_payload_json == second.structured_payload_json
    assert db.scalars(select(GeneratedReport)).all().__len__() == 1
    assert first.rendered_markdown.startswith("# Daily learning report")
