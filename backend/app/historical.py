from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Phase, Roadmap, RoadmapScopeEvent, RoadmapVersion
from app.time_utils import local_day_bounds_ms


@dataclass(frozen=True)
class HistoricalScope:
    roadmap_id: str
    roadmap_stable_key: str
    roadmap_version_id: str
    roadmap_version: str
    phase_id: str
    phase_stable_key: str
    phase_title: str
    source: str
    reason: str
    occurred_at: int
    event_sequence: int


@dataclass(frozen=True)
class HistoricalEvaluationContext:
    period_start: date
    period_end: date
    timezone: str
    exclusive_cutoff_ms: int
    completed_through: date
    scope: HistoricalScope | None


def resolve_scope_as_of(db: Session, exclusive_cutoff_ms: int) -> HistoricalScope | None:
    event = db.scalar(
        select(RoadmapScopeEvent)
        .where(RoadmapScopeEvent.occurred_at < exclusive_cutoff_ms)
        .order_by(RoadmapScopeEvent.occurred_at.desc(), RoadmapScopeEvent.event_sequence.desc())
        .limit(1)
    )
    if event is None:
        return None
    roadmap = db.get(Roadmap, event.roadmap_id)
    version = db.get(RoadmapVersion, event.roadmap_version_id)
    phase = db.get(Phase, event.phase_id)
    if roadmap is None or version is None or phase is None:
        return None
    return HistoricalScope(
        roadmap_id=roadmap.id,
        roadmap_stable_key=roadmap.stable_key,
        roadmap_version_id=version.id,
        roadmap_version=version.version,
        phase_id=phase.id,
        phase_stable_key=phase.stable_key,
        phase_title=phase.title,
        source=event.source,
        reason=event.reason,
        occurred_at=event.occurred_at,
        event_sequence=event.event_sequence,
    )


def historical_evaluation_context(
    db: Session, period_start: date, period_end: date, timezone_name: str
) -> HistoricalEvaluationContext:
    if period_end < period_start:
        raise ValueError("The historical period end must not precede its start.")
    _, exclusive_cutoff_ms = local_day_bounds_ms(period_end, timezone_name)
    return HistoricalEvaluationContext(
        period_start=period_start,
        period_end=period_end,
        timezone=timezone_name,
        exclusive_cutoff_ms=exclusive_cutoff_ms,
        completed_through=period_end,
        scope=resolve_scope_as_of(db, exclusive_cutoff_ms),
    )
