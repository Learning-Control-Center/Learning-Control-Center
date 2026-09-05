from __future__ import annotations

import asyncio
import calendar
import json
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.analytics import ANALYTICS_VERSION, SessionFact, build_analytics, session_facts
from app.api_serialization import serialize_api_instants
from app.auth import AuthContext, get_auth_context
from app.database import SessionLocal, get_db
from app.historical import HistoricalEvaluationContext, historical_evaluation_context
from app.models import (
    CompetencyIdentity,
    CompetencyStatusEvent,
    GeneratedReport,
    RoadmapScopeEvent,
    VerificationRecord,
)
from app.settings_api import get_or_create_profile
from app.time_utils import local_date_for_ms, local_day_bounds_ms, utc_now_ms

router = APIRouter(prefix="/reports", tags=["reports"])


def _render_markdown(report_type: str, start: date, end: date, payload: dict[str, Any]) -> str:
    title = f"{report_type.title()} learning report"
    lines = [
        f"# {title}",
        "",
        f"Period: {start.isoformat()} to {end.isoformat()}",
        f"Analytics version: {payload['analyticsVersion']}",
        "",
        "## Activity",
        "",
        f"- Total duration: {payload['totalDurationMs']} ms",
        f"- Active days: {payload['activeDays']}",
        f"- Review debt: {payload['reviewDebt']['count']} competencies",
        "",
        f"## {report_type.title()} details",
        "",
    ]
    details = payload["reportDetails"]
    lines.extend(f"- {key}: {json.dumps(value, sort_keys=True)}" for key, value in details.items())
    lines.extend(
        [
            "",
            "## Verification coverage",
            "",
            (
                f"- Core: {payload['coverage']['verifiedCore']} / "
                f"{payload['coverage']['applicableCore']}"
            ),
            (
                f"- Important: {payload['coverage']['verifiedImportant']} / "
                f"{payload['coverage']['applicableImportant']}"
            ),
            "",
            "## Deterministic signals",
            "",
        ]
    )
    if payload["signals"]:
        lines.extend(f"- {signal['code']}" for signal in payload["signals"])
    else:
        lines.append("- No signals for this period.")
    return "\n".join(lines) + "\n"


def _period_facts(
    db: Session, context: HistoricalEvaluationContext, start: date, end: date
) -> list[SessionFact]:
    return [
        fact
        for fact in session_facts(
            db, context.timezone, exclusive_cutoff_ms=context.exclusive_cutoff_ms
        )
        if start <= fact.local_date <= end
    ]


def _duration_distribution(facts: list[SessionFact], attribute: str) -> list[dict[str, Any]]:
    totals: dict[str, int] = defaultdict(int)
    overall = sum(fact.duration_ms for fact in facts)
    for fact in facts:
        value = getattr(fact, attribute) or "unassigned"
        totals[value] += fact.duration_ms
    return [
        {
            "key": key,
            "durationMs": value,
            "ratio": value / overall if overall else None,
        }
        for key, value in sorted(totals.items())
    ]


def _status_transitions(db: Session, context: HistoricalEvaluationContext) -> list[dict[str, Any]]:
    start_ms = local_day_bounds_ms(context.period_start, context.timezone)[0]
    identities = {item.id: item.stable_key for item in db.scalars(select(CompetencyIdentity)).all()}
    events = db.scalars(
        select(CompetencyStatusEvent)
        .where(
            CompetencyStatusEvent.created_at >= start_ms,
            CompetencyStatusEvent.created_at < context.exclusive_cutoff_ms,
        )
        .order_by(CompetencyStatusEvent.created_at, CompetencyStatusEvent.id)
    ).all()
    return [
        {
            "competencyIdentityId": item.competency_identity_id,
            "stableKey": identities.get(item.competency_identity_id),
            "fromStatus": item.from_status,
            "toStatus": item.to_status,
            "source": item.source,
            "reason": item.reason,
            "createdAt": item.created_at,
        }
        for item in events
    ]


def _verification_activity(
    db: Session, context: HistoricalEvaluationContext
) -> list[dict[str, Any]]:
    start_ms = local_day_bounds_ms(context.period_start, context.timezone)[0]
    records = db.scalars(
        select(VerificationRecord)
        .where(
            VerificationRecord.created_at >= start_ms,
            VerificationRecord.created_at < context.exclusive_cutoff_ms,
        )
        .order_by(VerificationRecord.created_at, VerificationRecord.id)
    ).all()
    return [
        {
            "id": item.id,
            "competencyIdentityId": item.competency_identity_id,
            "source": item.verification_source,
            "result": item.result,
            "method": item.method,
            "createdAt": item.created_at,
        }
        for item in records
    ]


def _scope_movements(db: Session, context: HistoricalEvaluationContext) -> list[dict[str, Any]]:
    start_ms = local_day_bounds_ms(context.period_start, context.timezone)[0]
    events = db.scalars(
        select(RoadmapScopeEvent)
        .where(
            RoadmapScopeEvent.occurred_at >= start_ms,
            RoadmapScopeEvent.occurred_at < context.exclusive_cutoff_ms,
            RoadmapScopeEvent.source.not_in({"migration_baseline", "restore_baseline"}),
        )
        .order_by(RoadmapScopeEvent.occurred_at, RoadmapScopeEvent.event_sequence)
    ).all()
    return [
        {
            "roadmapId": item.roadmap_id,
            "roadmapVersionId": item.roadmap_version_id,
            "phaseId": item.phase_id,
            "source": item.source,
            "reason": item.reason,
            "occurredAt": item.occurred_at,
            "eventSequence": item.event_sequence,
        }
        for item in events
    ]


def _independence(facts: list[SessionFact]) -> dict[str, Any]:
    practical = [fact for fact in facts if fact.practical]
    independent = [fact for fact in practical if fact.independent_practical]
    numerator = sum(fact.duration_ms for fact in independent)
    denominator = sum(fact.duration_ms for fact in practical)
    return {
        "independentDurationMs": numerator,
        "practicalDurationMs": denominator,
        "ratio": numerator / denominator if denominator else None,
    }


def _report_details(
    db: Session,
    report_type: str,
    context: HistoricalEvaluationContext,
    analytics: dict[str, Any],
) -> dict[str, Any]:
    facts = _period_facts(db, context, context.period_start, context.period_end)
    transitions = _status_transitions(db, context)
    verifications = _verification_activity(db, context)
    blockers = [fact for fact in facts if fact.outcome == "blocked"]
    reviews = [fact for fact in facts if fact.activity_type == "review" and fact.successful]
    distributions = {
        "activity": _duration_distribution(facts, "activity_type"),
        "assistance": _duration_distribution(facts, "assistance_mode"),
        "track": _duration_distribution(facts, "track_id"),
    }
    if report_type == "daily":
        target = get_or_create_profile(db).target_duration_ms_per_active_day
        focus = max(
            distributions["activity"],
            key=lambda item: (item["durationMs"], item["key"]),
            default=None,
        )
        return {
            "targetVsActual": {
                "targetDurationMs": target,
                "actualDurationMs": analytics["totalDurationMs"],
                "adherence": (min(analytics["totalDurationMs"] / target, 1.0) if target else None),
            },
            "sessionCount": len(facts),
            "durationMs": analytics["totalDurationMs"],
            "focus": focus,
            "distributions": distributions,
            "statusTransitions": transitions,
            "reviews": {
                "sessionCount": len(reviews),
                "durationMs": sum(f.duration_ms for f in reviews),
            },
            "verifications": verifications,
            "blockers": {"count": len(blockers)},
            "signals": analytics["signals"],
        }
    if report_type == "weekly":
        previous_end = context.period_start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=6)
        previous_context = historical_evaluation_context(
            db, previous_start, previous_end, context.timezone
        )
        previous_facts = _period_facts(db, previous_context, previous_start, previous_end)
        previous_duration = sum(item.duration_ms for item in previous_facts)
        return {
            "activeDaysVsTarget": {
                "activeDays": analytics["activeDays"],
                "targetActiveDays": get_or_create_profile(db).weekly_target_active_days,
            },
            "totalDurationMs": analytics["totalDurationMs"],
            "previousWeekComparison": {
                "periodStart": previous_start.isoformat(),
                "periodEnd": previous_end.isoformat(),
                "previousDurationMs": previous_duration,
                "differenceMs": analytics["totalDurationMs"] - previous_duration,
                "changeRatio": (
                    (analytics["totalDurationMs"] - previous_duration) / previous_duration
                    if previous_duration
                    else None
                ),
            },
            "distributions": distributions,
            "independence": _independence(facts),
            "statusTransitions": transitions,
            "verifications": verifications,
            "reviewDebt": analytics["reviewDebt"],
            "regularity": analytics["regularity"],
            "recoveryGaps": analytics["gaps"],
            "signals": analytics["signals"],
        }
    by_week: dict[str, list[SessionFact]] = defaultdict(list)
    for fact in facts:
        monday = fact.local_date - timedelta(days=fact.local_date.weekday())
        by_week[monday.isoformat()].append(fact)
    week_starts: list[date] = []
    week_start = context.period_start - timedelta(days=context.period_start.weekday())
    while week_start <= context.period_end:
        week_starts.append(week_start)
        week_start += timedelta(days=7)
    workload_trend = [
        {
            "weekStart": week.isoformat(),
            "durationMs": sum(item.duration_ms for item in by_week.get(week.isoformat(), [])),
            "activeDays": len({item.local_date for item in by_week.get(week.isoformat(), [])}),
        }
        for week in week_starts
    ]
    independence_trend = [
        {
            "weekStart": week.isoformat(),
            **_independence(by_week.get(week.isoformat(), [])),
        }
        for week in week_starts
    ]
    retention_review_trend: list[dict[str, Any]] = []
    for week in week_starts:
        segment_start = max(week, context.period_start)
        segment_end = min(week + timedelta(days=6), context.period_end)
        segment_facts = by_week.get(week.isoformat(), [])
        segment_reviews = [
            item for item in segment_facts if item.activity_type == "review" and item.successful
        ]
        segment_context = historical_evaluation_context(
            db, segment_start, segment_end, context.timezone
        )
        segment_analytics = build_analytics(
            db,
            range_name="custom",
            start_date=segment_start,
            end_date=segment_end,
            historical_context=segment_context,
        )
        retention_review_trend.append(
            {
                "periodStart": segment_start.isoformat(),
                "periodEnd": segment_end.isoformat(),
                "reviewSessionCount": len(segment_reviews),
                "reviewDurationMs": sum(item.duration_ms for item in segment_reviews),
                "reviewDebtCount": segment_analytics["reviewDebt"]["count"],
                "reviewDebtWeightedCount": segment_analytics["reviewDebt"]["weightedCount"],
            }
        )
    blocker_counts = Counter(fact.competency_identity_id or "unassigned" for fact in blockers)
    return {
        "consistencyTrend": [
            {"weekStart": item["weekStart"], "activeDays": item["activeDays"]}
            for item in workload_trend
        ],
        "workloadTrend": workload_trend,
        "independenceTrend": independence_trend,
        "weightedVerificationCoverage": analytics["coverage"],
        "roadmapPhaseMovement": _scope_movements(db, context),
        "verificationSourceMix": dict(Counter(item["source"] for item in verifications)),
        "retentionReviewTrend": retention_review_trend,
        "repeatedBlockers": [
            {"competencyIdentityId": key, "count": count}
            for key, count in sorted(blocker_counts.items())
            if count >= 2
        ],
        "statusTransitions": transitions,
        "verifications": verifications,
        "signals": analytics["signals"],
    }


def generate_report(db: Session, report_type: str, start: date, end: date) -> GeneratedReport:
    existing = db.scalar(
        select(GeneratedReport).where(
            GeneratedReport.report_type == report_type,
            GeneratedReport.period_start == start.isoformat(),
            GeneratedReport.period_end == end.isoformat(),
            GeneratedReport.analytics_version == ANALYTICS_VERSION,
        )
    )
    if existing is not None:
        return existing
    profile = get_or_create_profile(db)
    context = historical_evaluation_context(db, start, end, profile.timezone)
    payload = build_analytics(
        db,
        range_name="custom",
        start_date=start,
        end_date=end,
        historical_context=context,
    )
    payload["reportType"] = report_type
    payload["reportDetails"] = _report_details(db, report_type, context, payload)
    report = GeneratedReport(
        report_type=report_type,
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        analytics_version=ANALYTICS_VERSION,
        structured_payload_json=json.dumps(payload, separators=(",", ":")),
        rendered_markdown=_render_markdown(report_type, start, end, payload),
    )
    db.add(report)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        concurrent = db.scalar(
            select(GeneratedReport).where(
                GeneratedReport.report_type == report_type,
                GeneratedReport.period_start == start.isoformat(),
                GeneratedReport.period_end == end.isoformat(),
                GeneratedReport.analytics_version == ANALYTICS_VERSION,
            )
        )
        if concurrent is None:
            raise RuntimeError(
                "Generated report uniqueness conflict did not produce a report."
            ) from exc
        return concurrent
    return report


def _month_end(value: date) -> date:
    return date(value.year, value.month, calendar.monthrange(value.year, value.month)[1])


def _report_exists(db: Session, report_type: str, start: date, end: date) -> bool:
    return (
        db.scalar(
            select(GeneratedReport.id).where(
                GeneratedReport.report_type == report_type,
                GeneratedReport.period_start == start.isoformat(),
                GeneratedReport.period_end == end.isoformat(),
                GeneratedReport.analytics_version == ANALYTICS_VERSION,
            )
        )
        is not None
    )


def backfill_reports(db: Session, now_ms: int | None = None) -> int:
    now = now_ms if now_ms is not None else utc_now_ms()
    profile = get_or_create_profile(db)
    facts = session_facts(db, profile.timezone)
    if not facts:
        return 0
    today = local_date_for_ms(now, profile.timezone)
    earliest = min(fact.local_date for fact in facts)
    generated = 0

    day = earliest
    while day < today:
        before = db.scalar(
            select(GeneratedReport.id).where(
                GeneratedReport.report_type == "daily",
                GeneratedReport.period_start == day.isoformat(),
                GeneratedReport.period_end == day.isoformat(),
                GeneratedReport.analytics_version == ANALYTICS_VERSION,
            )
        )
        generate_report(db, "daily", day, day)
        generated += int(before is None)
        day += timedelta(days=1)

    week_start = earliest - timedelta(days=earliest.weekday())
    while week_start + timedelta(days=6) < today:
        week_end = week_start + timedelta(days=6)
        before = db.scalar(
            select(GeneratedReport.id).where(
                GeneratedReport.report_type == "weekly",
                GeneratedReport.period_start == week_start.isoformat(),
                GeneratedReport.period_end == week_end.isoformat(),
                GeneratedReport.analytics_version == ANALYTICS_VERSION,
            )
        )
        generate_report(db, "weekly", week_start, week_end)
        generated += int(before is None)
        week_start += timedelta(days=7)

    month_start = earliest.replace(day=1)
    while _month_end(month_start) < today:
        month_end = _month_end(month_start)
        before = db.scalar(
            select(GeneratedReport.id).where(
                GeneratedReport.report_type == "monthly",
                GeneratedReport.period_start == month_start.isoformat(),
                GeneratedReport.period_end == month_end.isoformat(),
                GeneratedReport.analytics_version == ANALYTICS_VERSION,
            )
        )
        generate_report(db, "monthly", month_start, month_end)
        generated += int(before is None)
        month_start = (month_end + timedelta(days=1)).replace(day=1)
    return generated


def generate_due_reports(db: Session, now_ms: int | None = None) -> int:
    now = now_ms if now_ms is not None else utc_now_ms()
    profile = get_or_create_profile(db)
    local_now = datetime.fromtimestamp(now / 1000, tz=ZoneInfo(profile.timezone))
    generated = 0
    if local_now.time() >= time(0, 5):
        yesterday = local_now.date() - timedelta(days=1)
        existed = _report_exists(db, "daily", yesterday, yesterday)
        generate_report(db, "daily", yesterday, yesterday)
        generated += int(not existed)
    if local_now.weekday() == 0 and local_now.time() >= time(0, 10):
        start = local_now.date() - timedelta(days=7)
        end = start + timedelta(days=6)
        existed = _report_exists(db, "weekly", start, end)
        generate_report(db, "weekly", start, end)
        generated += int(not existed)
    if local_now.day == 1 and local_now.time() >= time(0, 15):
        end = local_now.date() - timedelta(days=1)
        start = end.replace(day=1)
        existed = _report_exists(db, "monthly", start, end)
        generate_report(db, "monthly", start, end)
        generated += int(not existed)
    return generated


async def report_scheduler(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        with SessionLocal() as db:
            generate_due_reports(db)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)
        except TimeoutError:
            continue


@router.get("")
async def list_reports(
    report_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    query = select(GeneratedReport).order_by(GeneratedReport.period_start.desc())
    if report_type:
        query = query.where(GeneratedReport.report_type == report_type)
    items = db.scalars(query.offset(max(offset, 0)).limit(min(max(limit, 1), 200))).all()
    return serialize_api_instants(
        {
            "items": [
                {
                    "id": item.id,
                    "type": item.report_type,
                    "periodStart": item.period_start,
                    "periodEnd": item.period_end,
                    "generatedAt": item.generated_at,
                    "analyticsVersion": item.analytics_version,
                    "payload": json.loads(item.structured_payload_json),
                    "markdown": item.rendered_markdown,
                }
                for item in items
            ],
            "limit": min(max(limit, 1), 200),
            "offset": max(offset, 0),
        }
    )


@router.get("/{report_id}")
async def get_report(
    report_id: str,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    item = db.get(GeneratedReport, report_id)
    if item is None:
        from app.errors import AppError

        raise AppError(404, "REPORT_NOT_FOUND", "The generated report does not exist.")
    return serialize_api_instants(
        {
            "id": item.id,
            "type": item.report_type,
            "periodStart": item.period_start,
            "periodEnd": item.period_end,
            "generatedAt": item.generated_at,
            "analyticsVersion": item.analytics_version,
            "payload": json.loads(item.structured_payload_json),
            "markdown": item.rendered_markdown,
        }
    )
