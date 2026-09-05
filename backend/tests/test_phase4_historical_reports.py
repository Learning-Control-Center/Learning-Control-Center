from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from app.analytics import ANALYTICS_VERSION, build_analytics
from app.historical import historical_evaluation_context
from app.models import (
    CompetencyIdentity,
    CompetencyState,
    CompetencyStatusEvent,
    DisciplineProfile,
    GeneratedReport,
    LearningSession,
    Phase,
    RoadmapScopeEvent,
    VerificationRecord,
)
from app.reports import backfill_reports, generate_due_reports, generate_report
from app.time_utils import datetime_to_epoch_ms
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session


def _instant(local_day: date, timezone_name: str, hour: int = 12, minute: int = 0) -> int:
    return datetime_to_epoch_ms(
        datetime.combine(local_day, time(hour, minute), tzinfo=ZoneInfo(timezone_name))
    )


def _add_session(
    db: Session,
    identity_id: str,
    local_day: date,
    timezone_name: str,
    *,
    duration_ms: int = 600_000,
    activity: str = "practice",
    outcome: str = "completed",
    assistance: str = "none",
    hour: int = 12,
) -> LearningSession:
    started_at = _instant(local_day, timezone_name, hour)
    item = LearningSession(
        competency_identity_id=identity_id,
        session_mode="manual",
        activity_type=activity,
        assistance_mode=assistance,
        started_at=started_at,
        ended_at=started_at + duration_ms,
        accumulated_duration_ms=duration_ms,
        duration_ms=duration_ms,
        outcome=outcome,
    )
    db.add(item)
    return item


def _identity(db: Session, key: str = "python.basics") -> CompetencyIdentity:
    item = db.scalar(select(CompetencyIdentity).where(CompetencyIdentity.stable_key == key))
    assert item is not None
    return item


@pytest.mark.parametrize("timezone_name", ["Asia/Tokyo", "America/Los_Angeles"])
async def test_selected_window_regularity_and_completed_week_use_local_dates(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
    db: Session,
    timezone_name: str,
) -> None:
    profile = db.get(DisciplineProfile, 1)
    assert profile is not None
    profile.timezone = timezone_name
    identity = _identity(db)
    monday = date(2026, 8, 17)
    for offset in range(7):
        _add_session(db, identity.id, monday + timedelta(days=offset), timezone_name)
    db.commit()

    now = _instant(date(2026, 8, 24), timezone_name, 0, 30)
    result = build_analytics(
        db, now_ms=now, range_name="custom", start_date=monday, end_date=monday + timedelta(days=6)
    )

    assert result["regularity"] == {
        "meanDurationMs": 600_000,
        "populationStdDevMs": 0,
        "coefficientOfVariation": 0.0,
        "band": "regular",
    }
    assert len(result["weeklyTarget"]["completedWeeks"]) == 1
    assert result["weeklyTarget"]["completedWeeks"][0]["weekEnd"] == "2026-08-23"


async def test_regularity_includes_inactive_days_and_week_boundaries_are_inclusive(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    identity = _identity(db)
    monday = date(2026, 8, 17)
    _add_session(db, identity.id, monday, "UTC")
    _add_session(db, identity.id, monday + timedelta(days=6), "UTC")
    db.commit()

    historical = build_analytics(
        db,
        range_name="custom",
        start_date=monday,
        end_date=monday + timedelta(days=6),
        historical_context=historical_evaluation_context(
            db, monday, monday + timedelta(days=6), "UTC"
        ),
    )
    assert historical["regularity"]["meanDurationMs"] == round(1_200_000 / 7)
    assert historical["regularity"]["populationStdDevMs"] > 0
    assert len(historical["weeklyTarget"]["completedWeeks"]) == 1

    live_sunday = build_analytics(
        db,
        now_ms=_instant(monday + timedelta(days=6), "UTC"),
        range_name="custom",
        start_date=monday,
        end_date=monday + timedelta(days=6),
    )
    assert live_sunday["weeklyTarget"]["completedWeeks"] == []

    midweek = build_analytics(
        db,
        now_ms=_instant(date(2026, 8, 24), "UTC"),
        range_name="custom",
        start_date=monday + timedelta(days=2),
        end_date=monday + timedelta(days=6),
    )
    assert midweek["weeklyTarget"]["completedWeeks"] == []


def _prepare_historical_timeline(db: Session) -> tuple[CompetencyIdentity, int, int]:
    identity = _identity(db)
    july = _instant(date(2026, 7, 1), "UTC")
    august_31 = _instant(date(2026, 8, 31), "UTC")
    for event in db.scalars(select(CompetencyStatusEvent)).all():
        event.created_at = july
    first_scope = db.scalar(select(RoadmapScopeEvent).order_by(RoadmapScopeEvent.event_sequence))
    assert first_scope is not None
    first_scope.occurred_at = july
    phase_two = db.scalar(select(Phase).where(Phase.stable_key == "phase-2"))
    assert phase_two is not None
    db.add(
        RoadmapScopeEvent(
            roadmap_id=first_scope.roadmap_id,
            roadmap_version_id=first_scope.roadmap_version_id,
            phase_id=phase_two.id,
            source="current_phase_change",
            reason="Later phase",
            occurred_at=august_31,
            event_sequence=2,
        )
    )
    _add_session(db, identity.id, date(2026, 8, 1), "UTC", duration_ms=900_000)
    _add_session(
        db,
        identity.id,
        date(2026, 8, 31),
        "UTC",
        duration_ms=1_800_000,
        activity="debugging",
        outcome="blocked",
        assistance="ai_assisted",
    )
    _add_session(db, identity.id, date(2026, 8, 31), "UTC", duration_ms=300_000)
    verification = VerificationRecord(
        competency_identity_id=identity.id,
        verification_source="external",
        method="Later assessment",
        result="passed",
        created_at=august_31,
    )
    db.add(verification)
    db.flush()
    db.add(
        CompetencyStatusEvent(
            competency_identity_id=identity.id,
            from_status="not_started",
            to_status="verified",
            reason="Later verification",
            source="verification",
            verification_record_id=verification.id,
            created_at=august_31,
        )
    )
    state = db.get(CompetencyState, identity.id)
    assert state is not None
    state.current_status = "verified"
    db.commit()
    return identity, july, august_31


async def test_historical_report_excludes_later_state_evidence_and_scope(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    identity, _, august_31 = _prepare_historical_timeline(db)

    report = generate_report(db, "daily", date(2026, 8, 1), date(2026, 8, 1))
    payload = json.loads(report.structured_payload_json)
    competency = payload["competencies"][identity.id]

    assert payload["analyticsVersion"] == ANALYTICS_VERSION == 2
    assert payload["totalDurationMs"] == 900_000
    assert competency["status"] == "not_started"
    assert competency["lastSuccessfulEvidenceAt"] < august_31
    assert competency["lastPassedVerificationAt"] is None
    assert competency["latestBlockedSessionAt"] is None
    assert competency["unresolvedTechnicalBlocker"] is False
    assert payload["historicalContext"]["scope"]["phaseStableKey"] == "phase-1"
    assert payload["reportDetails"]["statusTransitions"] == []
    assert payload["reportDetails"]["verifications"] == []
    assert payload["reportDetails"]["blockers"] == {"count": 0}


async def test_history_before_a_scope_baseline_remains_unknown(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    report = generate_report(db, "daily", date(2026, 8, 1), date(2026, 8, 1))
    payload = json.loads(report.structured_payload_json)

    assert payload["historicalContext"]["scope"] is None
    assert payload["coverage"]["weightedVerificationCoverage"] is None
    assert payload["coverage"]["unavailableStatusCount"] == 0


async def test_missing_status_prehistory_is_unavailable_not_current_state(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    first_scope = db.scalar(select(RoadmapScopeEvent).order_by(RoadmapScopeEvent.event_sequence))
    assert first_scope is not None
    first_scope.occurred_at = _instant(date(2026, 7, 1), "UTC")
    state = db.get(CompetencyState, _identity(db).id)
    assert state is not None
    state.current_status = "practicing"
    db.commit()

    report = generate_report(db, "daily", date(2026, 8, 1), date(2026, 8, 1))
    payload = json.loads(report.structured_payload_json)

    assert payload["historicalContext"]["scope"]["phaseStableKey"] == "phase-1"
    assert payload["competencies"][_identity(db).id]["status"] is None
    assert payload["coverage"]["weightedVerificationCoverage"] is None
    assert payload["coverage"]["unavailableStatusCount"] == 3


async def test_report_types_have_distinct_canonical_structured_sections(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    _prepare_historical_timeline(db)
    daily = json.loads(
        generate_report(db, "daily", date(2026, 8, 1), date(2026, 8, 1)).structured_payload_json
    )["reportDetails"]
    weekly = json.loads(
        generate_report(db, "weekly", date(2026, 8, 3), date(2026, 8, 9)).structured_payload_json
    )["reportDetails"]
    monthly = json.loads(
        generate_report(db, "monthly", date(2026, 8, 1), date(2026, 8, 31)).structured_payload_json
    )["reportDetails"]

    assert {
        "targetVsActual",
        "sessionCount",
        "durationMs",
        "focus",
        "distributions",
        "statusTransitions",
        "reviews",
        "verifications",
        "blockers",
        "signals",
    } == set(daily)
    assert {
        "activeDaysVsTarget",
        "totalDurationMs",
        "previousWeekComparison",
        "distributions",
        "independence",
        "statusTransitions",
        "verifications",
        "reviewDebt",
        "regularity",
        "recoveryGaps",
        "signals",
    } == set(weekly)
    assert {
        "consistencyTrend",
        "workloadTrend",
        "independenceTrend",
        "weightedVerificationCoverage",
        "roadmapPhaseMovement",
        "verificationSourceMix",
        "retentionReviewTrend",
        "repeatedBlockers",
        "statusTransitions",
        "verifications",
        "signals",
    } == set(monthly)
    assert len(monthly["roadmapPhaseMovement"]) == 1
    assert monthly["verificationSourceMix"] == {"external": 1}


async def test_report_versions_coexist_and_same_version_is_immutable(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    _prepare_historical_timeline(db)
    old_payload = '{"legacy":true,"bytes":"unchanged"}'
    old_markdown = "# Legacy\n\nExact bytes.\n"
    old = GeneratedReport(
        report_type="daily",
        period_start="2026-08-01",
        period_end="2026-08-01",
        analytics_version=1,
        structured_payload_json=old_payload,
        rendered_markdown=old_markdown,
    )
    db.add(old)
    db.commit()

    first = generate_report(db, "daily", date(2026, 8, 1), date(2026, 8, 1))
    original = first.structured_payload_json
    _add_session(db, _identity(db).id, date(2026, 8, 1), "UTC", duration_ms=7_000_000)
    db.commit()
    second = generate_report(db, "daily", date(2026, 8, 1), date(2026, 8, 1))

    assert second.id == first.id
    assert second.structured_payload_json == original
    db.refresh(old)
    assert old.structured_payload_json == old_payload
    assert old.rendered_markdown == old_markdown


async def test_scheduler_and_backfill_generate_completed_periods_idempotently(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    identity = _identity(db)
    _add_session(db, identity.id, date(2026, 8, 30), "UTC")
    db.commit()
    monday = date(2026, 9, 7)
    before_daily = _instant(monday, "UTC", 0, 4)
    at_daily = _instant(monday, "UTC", 0, 5)

    assert generate_due_reports(db, now_ms=before_daily) == 0
    assert generate_due_reports(db, now_ms=at_daily) == 1
    assert generate_due_reports(db, now_ms=at_daily) == 0

    later = _instant(date(2026, 9, 8), "UTC")
    first_backfill = backfill_reports(db, now_ms=later)
    assert first_backfill > 0
    assert backfill_reports(db, now_ms=later) == 0
    reports = db.scalars(select(GeneratedReport)).all()
    assert all(date.fromisoformat(item.period_end) < date(2026, 9, 8) for item in reports)
    assert all(item.analytics_version == ANALYTICS_VERSION for item in reports)


async def test_weekly_and_monthly_scheduler_boundaries_remain_exact(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    monday = date(2026, 9, 7)
    assert generate_due_reports(db, now_ms=_instant(monday, "UTC", 0, 9)) == 1
    assert generate_due_reports(db, now_ms=_instant(monday, "UTC", 0, 10)) == 1
    assert generate_due_reports(db, now_ms=_instant(monday, "UTC", 0, 10)) == 0

    first_of_month = date(2026, 10, 1)
    assert generate_due_reports(db, now_ms=_instant(first_of_month, "UTC", 0, 14)) == 1
    assert generate_due_reports(db, now_ms=_instant(first_of_month, "UTC", 0, 15)) == 1
    assert generate_due_reports(db, now_ms=_instant(first_of_month, "UTC", 0, 15)) == 0
