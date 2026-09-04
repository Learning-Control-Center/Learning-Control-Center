from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api_serialization import serialize_api_instants
from app.auth import AuthContext, get_auth_context
from app.database import get_db
from app.domain import CONCEPTUAL_ACTIVITIES, PRACTICAL_ACTIVITIES, SUCCESSFUL_OUTCOMES
from app.models import (
    CompetencyDefinition,
    CompetencyIdentity,
    CompetencyState,
    LearningSession,
    Roadmap,
    Track,
    VerificationRecord,
)
from app.settings_api import get_or_create_profile
from app.time_utils import local_date_for_ms, utc_now_ms

router = APIRouter(prefix="/analytics", tags=["analytics"])

ANALYTICS_VERSION = 1
FRESHNESS_THRESHOLDS = {
    "learning": 7,
    "practicing": 10,
    "ready_for_verification": 7,
    "verified": 30,
}


@dataclass(frozen=True)
class SessionFact:
    id: str
    competency_identity_id: str | None
    track_id: str | None
    activity_type: str
    assistance_mode: str
    started_at: int
    duration_ms: int
    outcome: str
    local_date: date

    @property
    def successful(self) -> bool:
        return self.outcome in SUCCESSFUL_OUTCOMES

    @property
    def conceptual(self) -> bool:
        return self.successful and self.activity_type in CONCEPTUAL_ACTIVITIES

    @property
    def practical(self) -> bool:
        return self.successful and self.activity_type in PRACTICAL_ACTIVITIES

    @property
    def independent_practical(self) -> bool:
        return self.practical and self.assistance_mode in {"none", "docs_only"}


def session_facts(db: Session, timezone_name: str) -> list[SessionFact]:
    sessions = db.scalars(select(LearningSession).order_by(LearningSession.started_at)).all()
    facts: list[SessionFact] = []
    for item in sessions:
        if item.duration_ms is None or item.duration_ms <= 0 or item.outcome == "cancelled":
            continue
        if item.session_mode == "timed" and item.timed_state != "completed":
            continue
        if item.outcome is None:
            continue
        facts.append(
            SessionFact(
                id=item.id,
                competency_identity_id=item.competency_identity_id,
                track_id=item.track_id,
                activity_type=item.activity_type,
                assistance_mode=item.assistance_mode,
                started_at=item.started_at,
                duration_ms=item.duration_ms,
                outcome=item.outcome,
                local_date=local_date_for_ms(item.started_at, timezone_name),
            )
        )
    return facts


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    return numerator / denominator if denominator else None


def _distribution(facts: Iterable[SessionFact], attribute: str) -> list[dict[str, Any]]:
    totals: dict[str, int] = defaultdict(int)
    overall = 0
    for fact in facts:
        value = getattr(fact, attribute)
        if value is None:
            value = "unassigned"
        totals[value] += fact.duration_ms
        overall += fact.duration_ms
    return [
        {"key": key, "durationMs": duration, "ratio": _ratio(duration, overall)}
        for key, duration in sorted(totals.items())
    ]


def _completed_week_adherence(
    active_dates: set[date], target_days: int, start_date: date, end_date: date, today: date
) -> list[dict[str, Any]]:
    week_start = start_date - timedelta(days=start_date.weekday())
    results: list[dict[str, Any]] = []
    while week_start + timedelta(days=6) < min(end_date, today):
        week_end = week_start + timedelta(days=6)
        active = sum(1 for day in active_dates if week_start <= day <= week_end)
        results.append(
            {
                "weekStart": week_start.isoformat(),
                "weekEnd": week_end.isoformat(),
                "activeDays": active,
                "targetDays": target_days,
                "adherence": min(active, target_days) / target_days,
            }
        )
        week_start += timedelta(days=7)
    return results


def _regularity(facts: list[SessionFact], days: list[date]) -> dict[str, Any]:
    by_day: dict[date, int] = defaultdict(int)
    for fact in facts:
        by_day[fact.local_date] += fact.duration_ms
    values = [by_day[day] for day in days]
    if not values or statistics.fmean(values) == 0:
        return {
            "meanDurationMs": None,
            "populationStdDevMs": None,
            "coefficientOfVariation": None,
            "band": None,
        }
    mean = statistics.fmean(values)
    deviation = statistics.pstdev(values)
    coefficient = deviation / mean
    band = (
        "regular"
        if coefficient <= 0.25
        else "moderately_variable"
        if coefficient <= 0.50
        else "irregular"
    )
    return {
        "meanDurationMs": round(mean),
        "populationStdDevMs": round(deviation),
        "coefficientOfVariation": coefficient,
        "band": band,
    }


def _trend(facts: list[SessionFact], today: date, days: int) -> dict[str, Any]:
    recent_start = today - timedelta(days=days)
    comparison_start = today - timedelta(days=days * 2)
    recent = sum(fact.duration_ms for fact in facts if recent_start <= fact.local_date < today)
    comparison = sum(
        fact.duration_ms for fact in facts if comparison_start <= fact.local_date < recent_start
    )
    return {
        "recentDurationMs": recent,
        "comparisonDurationMs": comparison,
        "differenceMs": recent - comparison,
        "changeRatio": _ratio(recent - comparison, comparison),
    }


def _gaps(active_dates: set[date], start_date: date, end_date: date) -> dict[str, Any]:
    completed: list[int] = []
    previous_active = False
    current_gap = 0
    for offset in range((end_date - start_date).days + 1):
        day = start_date + timedelta(days=offset)
        if day in active_dates:
            if previous_active and current_gap:
                completed.append(current_gap)
            elif current_gap and any(active < day for active in active_dates):
                completed.append(current_gap)
            current_gap = 0
            previous_active = True
        elif previous_active:
            current_gap += 1
    sorted_gaps = sorted(completed)
    return {
        "latestCompletedGapDays": completed[-1] if completed else None,
        "longestCompletedGapDays": max(completed) if completed else None,
        "medianCompletedGapDays": statistics.median(sorted_gaps) if sorted_gaps else None,
        "ongoingGapDays": current_gap if previous_active else None,
    }


def competency_facts(
    db: Session,
    facts: list[SessionFact],
    timezone_name: str,
    now_ms: int,
    identity_scope: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    today = local_date_for_ms(now_ms, timezone_name)
    successful_by_competency: dict[str, list[SessionFact]] = defaultdict(list)
    blocked_by_competency: dict[str, list[SessionFact]] = defaultdict(list)
    for fact in facts:
        if fact.competency_identity_id is None:
            continue
        if fact.successful:
            successful_by_competency[fact.competency_identity_id].append(fact)
        if fact.outcome == "blocked":
            blocked_by_competency[fact.competency_identity_id].append(fact)
    states_query = select(CompetencyState)
    if identity_scope is not None:
        states_query = states_query.where(
            CompetencyState.competency_identity_id.in_(identity_scope)
        )
    states = db.scalars(states_query).all()
    passed_verifications: dict[str, list[VerificationRecord]] = defaultdict(list)
    for record in db.scalars(
        select(VerificationRecord).where(VerificationRecord.result == "passed")
    ).all():
        passed_verifications[record.competency_identity_id].append(record)
    results: dict[str, dict[str, Any]] = {}
    for state in states:
        successful = successful_by_competency[state.competency_identity_id]
        conceptual = [fact for fact in successful if fact.conceptual]
        practical = [fact for fact in successful if fact.practical]
        independent = [fact for fact in practical if fact.independent_practical]
        verification_dates = passed_verifications[state.competency_identity_id]
        evidence_timestamps = [fact.started_at for fact in successful]
        if state.current_status == "verified":
            evidence_timestamps.extend(record.created_at for record in verification_dates)
        last_evidence = max(evidence_timestamps, default=None)
        elapsed_days = (
            (today - local_date_for_ms(last_evidence, timezone_name)).days
            if last_evidence is not None
            else None
        )
        threshold = FRESHNESS_THRESHOLDS.get(state.current_status)
        review_due = state.current_status == "needs_review" or (
            threshold is not None and elapsed_days is not None and elapsed_days > threshold
        )
        days_overdue = (
            elapsed_days - threshold
            if threshold is not None and elapsed_days is not None and elapsed_days > threshold
            else 0
        )
        blockers = sorted(
            blocked_by_competency[state.competency_identity_id], key=lambda item: item.started_at
        )
        latest_blocker = blockers[-1].started_at if blockers else None
        later_practical = (
            max(
                (
                    fact.started_at
                    for fact in practical
                    if latest_blocker and fact.started_at > latest_blocker
                ),
                default=None,
            )
            if latest_blocker
            else None
        )
        blocker_recent = (
            latest_blocker is not None
            and local_date_for_ms(latest_blocker, timezone_name) >= today - timedelta(days=7)
            and local_date_for_ms(latest_blocker, timezone_name) < today
        )
        results[state.competency_identity_id] = {
            "status": state.current_status,
            "totalSuccessfulDurationMs": sum(fact.duration_ms for fact in successful),
            "conceptualEvidenceDurationMs": sum(fact.duration_ms for fact in conceptual),
            "practicalEvidenceDurationMs": sum(fact.duration_ms for fact in practical),
            "independentPracticalDurationMs": sum(fact.duration_ms for fact in independent),
            "successfulSessionCount": len(successful),
            "distinctActiveDays": len({fact.local_date for fact in successful}),
            "lastSuccessfulEvidenceAt": max((fact.started_at for fact in successful), default=None),
            "lastPassedVerificationAt": max(
                (item.created_at for item in verification_dates), default=None
            ),
            "freshnessThresholdDays": threshold,
            "elapsedLocalCalendarDays": elapsed_days,
            "daysOverdue": days_overdue,
            "reviewDue": review_due,
            "latestBlockedSessionAt": latest_blocker,
            "latestLaterSuccessfulPracticalAt": later_practical,
            "unresolvedTechnicalBlocker": bool(blocker_recent and later_practical is None),
        }
    return results


def build_analytics(
    db: Session,
    *,
    now_ms: int | None = None,
    range_name: str = "30d",
    start_date: date | None = None,
    end_date: date | None = None,
    competency_identity_ids: set[str] | None = None,
    track_ids: set[str] | None = None,
) -> dict[str, Any]:
    now = now_ms if now_ms is not None else utc_now_ms()
    profile = get_or_create_profile(db)
    timezone_name = profile.timezone
    today = local_date_for_ms(now, timezone_name)
    all_facts = session_facts(db, timezone_name)
    if competency_identity_ids is not None:
        all_facts = [
            fact for fact in all_facts if fact.competency_identity_id in competency_identity_ids
        ]
    if track_ids is not None:
        all_facts = [fact for fact in all_facts if fact.track_id in track_ids]
    window_days = {"7d": 7, "14d": 14, "30d": 30, "90d": 90}.get(range_name)
    if start_date is None:
        start_date = today - timedelta(days=(window_days - 1) if window_days else 365_000)
    if end_date is None:
        end_date = today
    facts = [fact for fact in all_facts if start_date <= fact.local_date <= end_date]
    active_dates = {fact.local_date for fact in facts}
    by_day: dict[date, int] = defaultdict(int)
    for fact in facts:
        by_day[fact.local_date] += fact.duration_ms
    current_week_start = today - timedelta(days=today.weekday())
    current_week_active = sum(1 for day in active_dates if current_week_start <= day <= today)
    duration_adherence = [
        {
            "localDate": day.isoformat(),
            "durationMs": duration,
            "targetDurationMs": profile.target_duration_ms_per_active_day,
            "adherence": (
                min(duration / profile.target_duration_ms_per_active_day, 1.0)
                if profile.target_duration_ms_per_active_day
                else None
            ),
        }
        for day, duration in sorted(by_day.items())
    ]
    practical = [fact for fact in facts if fact.practical]
    independent = [fact for fact in practical if fact.independent_practical]
    competency = competency_facts(
        db, all_facts, timezone_name, now, identity_scope=competency_identity_ids
    )

    roadmap = db.scalar(select(Roadmap).where(Roadmap.is_current.is_(True)))
    coverage: dict[str, Any] = {
        "verifiedCore": 0,
        "applicableCore": 0,
        "verifiedImportant": 0,
        "applicableImportant": 0,
        "weightedVerifiedNumerator": 0,
        "weightedApplicableDenominator": 0,
        "weightedVerificationCoverage": None,
    }
    status_distribution: Counter[str] = Counter()
    weighted_status: Counter[str] = Counter()
    review_debt: list[dict[str, Any]] = []
    applicable_identity_ids: set[str] = set()
    track_titles: dict[str, str] = {}
    if roadmap and roadmap.active_version_id:
        definitions_query = select(CompetencyDefinition).where(
            CompetencyDefinition.roadmap_version_id == roadmap.active_version_id,
            CompetencyDefinition.archived.is_(False),
        )
        if competency_identity_ids is not None:
            definitions_query = definitions_query.where(
                CompetencyDefinition.competency_identity_id.in_(competency_identity_ids)
            )
        if track_ids is not None:
            definitions_query = definitions_query.where(
                CompetencyDefinition.track_id.in_(track_ids)
            )
        definitions = db.scalars(definitions_query).all()
        identities = {
            item.id: item
            for item in db.scalars(
                select(CompetencyIdentity).where(
                    CompetencyIdentity.id.in_([item.competency_identity_id for item in definitions])
                )
            ).all()
        }
        track_titles = {
            item.id: item.title
            for item in db.scalars(
                select(Track).where(Track.roadmap_version_id == roadmap.active_version_id)
            ).all()
        }
        for definition in definitions:
            applicable_identity_ids.add(definition.competency_identity_id)
            item = competency[definition.competency_identity_id]
            status = item["status"]
            status_distribution[status] += 1
            weighted_status[status] += definition.weight
            coverage["weightedApplicableDenominator"] += definition.weight
            if status == "verified":
                coverage["weightedVerifiedNumerator"] += definition.weight
            if definition.priority == "core":
                coverage["applicableCore"] += 1
                coverage["verifiedCore"] += int(status == "verified")
            if definition.priority == "important":
                coverage["applicableImportant"] += 1
                coverage["verifiedImportant"] += int(status == "verified")
            if item["reviewDue"]:
                review_debt.append(
                    {
                        "competencyIdentityId": definition.competency_identity_id,
                        "stableKey": identities[definition.competency_identity_id].stable_key,
                        "priority": definition.priority,
                        "weight": definition.weight,
                        "daysOverdue": item["daysOverdue"],
                    }
                )
    coverage["weightedVerificationCoverage"] = _ratio(
        coverage["weightedVerifiedNumerator"], coverage["weightedApplicableDenominator"]
    )
    verification_sources: Counter[str] = Counter(
        record.verification_source
        for record in db.scalars(select(VerificationRecord)).all()
        if record.competency_identity_id in applicable_identity_ids
    )
    track_distribution = _distribution(facts, "track_id")
    for entry in track_distribution:
        entry["label"] = track_titles.get(entry["key"], "Unassigned")

    completed_days = [today - timedelta(days=offset) for offset in range(1, 31)]
    completed_days.reverse()
    signals: list[dict[str, Any]] = []
    for identity_id, item in competency.items():
        if item["reviewDue"]:
            signals.append(
                {
                    "code": "REVIEW_DUE",
                    "severity": "info",
                    "competencyIdentityId": identity_id,
                    "facts": {
                        "daysOverdue": item["daysOverdue"],
                        "freshnessThresholdDays": item["freshnessThresholdDays"],
                    },
                    "analyticsVersion": ANALYTICS_VERSION,
                }
            )
        if item["unresolvedTechnicalBlocker"]:
            signals.append(
                {
                    "code": "UNRESOLVED_TECHNICAL_BLOCKER",
                    "severity": "attention",
                    "competencyIdentityId": identity_id,
                    "facts": {"latestBlockedSessionAt": item["latestBlockedSessionAt"]},
                    "analyticsVersion": ANALYTICS_VERSION,
                }
            )
    return {
        "analyticsVersion": ANALYTICS_VERSION,
        "generatedAt": now,
        "timezone": timezone_name,
        "range": {
            "name": range_name,
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
        },
        "totalDurationMs": sum(fact.duration_ms for fact in facts),
        "activeDays": len(active_dates),
        "weeklyTarget": {
            "currentWeekActiveDays": current_week_active,
            "targetActiveDays": profile.weekly_target_active_days,
            "completedWeeks": _completed_week_adherence(
                active_dates, profile.weekly_target_active_days, start_date, end_date, today
            ),
        },
        "durationAdherence": duration_adherence,
        "distributions": {
            "activity": _distribution(facts, "activity_type"),
            "assistance": _distribution(facts, "assistance_mode"),
            "track": track_distribution,
        },
        "independentCoding": {
            "independentDurationMs": sum(fact.duration_ms for fact in independent),
            "practicalDurationMs": sum(fact.duration_ms for fact in practical),
            "ratio": _ratio(
                sum(fact.duration_ms for fact in independent),
                sum(fact.duration_ms for fact in practical),
            ),
        },
        "regularity": _regularity(all_facts, completed_days),
        "workloadTrend": {
            "short": _trend(all_facts, today, 7),
            "long": _trend(all_facts, today, 14),
        },
        "gaps": _gaps(active_dates, start_date, min(end_date, today)),
        "coverage": coverage,
        "statusDistribution": dict(status_distribution),
        "weightedStatusDistribution": dict(weighted_status),
        "verificationSourceDistribution": dict(verification_sources),
        "reviewDebt": {
            "count": len(review_debt),
            "weightedCount": sum(item["weight"] for item in review_debt),
            "items": sorted(
                review_debt, key=lambda item: (-item["daysOverdue"], item["stableKey"])
            ),
        },
        "competencies": competency,
        "signals": signals,
    }


@router.get("")
async def analytics_endpoint(
    range_name: str = Query(default="30d", alias="range", pattern="^(7d|14d|30d|90d|all)$"),
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return serialize_api_instants(build_analytics(db, range_name=range_name))
