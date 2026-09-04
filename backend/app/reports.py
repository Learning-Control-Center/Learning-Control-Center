from __future__ import annotations

import asyncio
import calendar
import json
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.analytics import ANALYTICS_VERSION, build_analytics, session_facts
from app.api_serialization import serialize_api_instants
from app.auth import AuthContext, get_auth_context
from app.database import SessionLocal, get_db
from app.models import GeneratedReport
from app.settings_api import get_or_create_profile
from app.time_utils import local_date_for_ms, utc_now_ms

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
        "## Verification coverage",
        "",
        f"- Core: {payload['coverage']['verifiedCore']} / {payload['coverage']['applicableCore']}",
        (
            f"- Important: {payload['coverage']['verifiedImportant']} / "
            f"{payload['coverage']['applicableImportant']}"
        ),
        "",
        "## Deterministic signals",
        "",
    ]
    if payload["signals"]:
        lines.extend(f"- {signal['code']}" for signal in payload["signals"])
    else:
        lines.append("- No signals for this period.")
    return "\n".join(lines) + "\n"


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
    payload = build_analytics(db, range_name="custom", start_date=start, end_date=end)
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
        generate_report(db, "daily", yesterday, yesterday)
        generated += 1
    if local_now.weekday() == 0 and local_now.time() >= time(0, 10):
        start = local_now.date() - timedelta(days=7)
        generate_report(db, "weekly", start, start + timedelta(days=6))
        generated += 1
    if local_now.day == 1 and local_now.time() >= time(0, 15):
        end = local_now.date() - timedelta(days=1)
        generate_report(db, "monthly", end.replace(day=1), end)
        generated += 1
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
