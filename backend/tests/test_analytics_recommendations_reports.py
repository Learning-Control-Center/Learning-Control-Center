from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.analytics import build_analytics
from app.models import (
    CompetencyIdentity,
    ExitCriterionIdentity,
    GeneratedReport,
    LearningSession,
)
from app.recommendations import build_recommendation, round_half_up
from app.reports import generate_report
from app.time_utils import datetime_to_epoch_ms
from httpx import AsyncClient
from sqlalchemy import select
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
