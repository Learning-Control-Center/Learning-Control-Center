from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.determinism import content_hash
from app.models import DisciplineProfile
from app.recommendation.v2.public import load_public_recommendation_run
from app.time_utils import local_date_for_ms
from app.today.contracts import LEGACY_TODAY_PRESENTATION_VERSION
from app.today.models import TodayGeneration, TodaySuggestion, TodaySuggestionCurrentState


@dataclass(frozen=True)
class TodayRoadmapOverlayItemDTO:
    suggestion_id: str
    generation_id: str
    status: str
    competency_identity_id: str | None
    target_identity_ids: tuple[str, ...]
    continuing_started: bool
    expires_at: int


@dataclass(frozen=True)
class TodayRoadmapOverlayDTO:
    items: tuple[TodayRoadmapOverlayItemDTO, ...]
    input_hash: str


def current_roadmap_overlay(db: Session, *, now_ms: int) -> TodayRoadmapOverlayDTO:
    generations = db.scalars(
        select(TodayGeneration).order_by(
            TodayGeneration.generated_at.desc(),
            TodayGeneration.explicit_generation_sequence.desc(),
            TodayGeneration.id.desc(),
        )
    ).all()
    profile = db.get(DisciplineProfile, 1)
    current_local_date = (
        local_date_for_ms(now_ms, profile.timezone).isoformat() if profile is not None else None
    )
    current_generation_id = next(
        (item.id for item in generations if item.local_date == current_local_date),
        None,
    )
    rows = db.execute(
        select(TodaySuggestion, TodaySuggestionCurrentState)
        .join(
            TodaySuggestionCurrentState,
            TodaySuggestionCurrentState.suggestion_id == TodaySuggestion.id,
        )
        .order_by(TodaySuggestion.id)
    ).all()
    items: list[TodayRoadmapOverlayItemDTO] = []
    for suggestion, state in rows:
        continuing = state.status == "started"
        current_and_active = (
            suggestion.generation_id == current_generation_id
            and not state.terminal
            and suggestion.expires_at > now_ms
        )
        if not continuing and not current_and_active:
            continue
        presentation = json.loads(suggestion.presentation_json)
        if suggestion.presentation_version == LEGACY_TODAY_PRESENTATION_VERSION:
            generation = next(item for item in generations if item.id == suggestion.generation_id)
            recommendation = next(
                (
                    item
                    for item in load_public_recommendation_run(
                        db, generation.recommendation_run_id
                    ).items
                    if item.candidate_id == suggestion.candidate_id
                ),
                None,
            )
            if recommendation is not None:
                presentation = {
                    **presentation,
                    "competencyIdentityId": recommendation.competency_identity_id,
                    "targetIdentityId": recommendation.target_identity_id,
                    "servedTargetIdentityIds": list(recommendation.served_target_identity_ids),
                }
        target_identity_ids = tuple(
            sorted(
                {str(item) for item in presentation.get("servedTargetIdentityIds", ())}
                | (
                    {str(presentation["targetIdentityId"])}
                    if presentation.get("targetIdentityId") is not None
                    else set()
                )
            )
        )
        items.append(
            TodayRoadmapOverlayItemDTO(
                suggestion.id,
                suggestion.generation_id,
                state.status,
                presentation.get("competencyIdentityId"),
                target_identity_ids,
                continuing and suggestion.generation_id != current_generation_id,
                suggestion.expires_at,
            )
        )
    frozen = tuple(items)
    return TodayRoadmapOverlayDTO(frozen, content_hash([asdict(item) for item in frozen]))
