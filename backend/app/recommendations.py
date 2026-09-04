from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import build_analytics, session_facts
from app.api_serialization import serialize_api_instants
from app.auth import AuthContext, get_auth_context, require_csrf
from app.database import get_db
from app.errors import AppError
from app.models import (
    CompetencyDefinition,
    CompetencyIdentity,
    CompetencyPrerequisite,
    CompetencyState,
    ExitCriterionDefinition,
    ExitCriterionIdentity,
    Phase,
    RecommendationSnapshot,
    Roadmap,
    Track,
)
from app.schemas import RecommendationDecision
from app.settings_api import get_or_create_profile
from app.time_utils import local_date_for_ms, utc_now_ms

router = APIRouter(prefix="/recommendations", tags=["recommendations"])

RECOMMENDATION_VERSION = 1
CONCEPTUAL_EXPOSURE_THRESHOLD_MS = 1_800_000
PRACTICAL_EVIDENCE_THRESHOLD_MS = 1_800_000
PRIORITY_SCORE = {"core": 30, "important": 20, "supporting": 10, "optional": 0}
STATUS_SCORE = {
    "needs_review": 35,
    "ready_for_verification": 30,
    "practicing": 20,
    "learning": 12,
    "not_started": 5,
    "verified": 0,
}


def round_half_up(value: float) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass
class Candidate:
    definition: CompetencyDefinition
    identity: CompetencyIdentity
    state: CompetencyState
    phase: Phase
    track: Track
    facts: dict[str, Any]
    exit_ready: bool
    direct_unlock_count: int
    independence_gap: int = 0
    track_balance: int = 0
    repetition_penalty: int = 0
    review_urgency: int = 0
    score: int = 0


def _required_exit_ready(
    db: Session, definitions: Sequence[CompetencyDefinition]
) -> dict[str, bool]:
    definition_ids = [item.id for item in definitions]
    items = db.scalars(
        select(ExitCriterionDefinition).where(
            ExitCriterionDefinition.competency_definition_id.in_(definition_ids),
            ExitCriterionDefinition.required.is_(True),
        )
    ).all()
    identities = {
        item.id: item
        for item in db.scalars(
            select(ExitCriterionIdentity).where(
                ExitCriterionIdentity.id.in_([item.exit_criterion_identity_id for item in items])
            )
        ).all()
    }
    grouped: dict[str, list[bool]] = {definition.id: [] for definition in definitions}
    for item in items:
        grouped[item.competency_definition_id].append(
            identities[item.exit_criterion_identity_id].current_state == "met"
        )
    return {
        definition_id: bool(values) and all(values) for definition_id, values in grouped.items()
    }


def _activity(candidate: Candidate) -> tuple[str, str, dict[str, Any]]:
    facts = candidate.facts
    base = {
        "status": candidate.state.current_status,
        "conceptualEvidenceDurationMs": facts["conceptualEvidenceDurationMs"],
        "conceptualThresholdMs": CONCEPTUAL_EXPOSURE_THRESHOLD_MS,
        "practicalEvidenceDurationMs": facts["practicalEvidenceDurationMs"],
        "practicalThresholdMs": PRACTICAL_EVIDENCE_THRESHOLD_MS,
        "exitCriteriaReady": candidate.exit_ready,
        "independenceGapContribution": candidate.independence_gap,
        "latestBlockedSessionAt": facts["latestBlockedSessionAt"],
        "latestLaterSuccessfulPracticalAt": facts["latestLaterSuccessfulPracticalAt"],
    }
    if facts["unresolvedTechnicalBlocker"]:
        return "research", "ACTIVITY_RESEARCH_UNRESOLVED_BLOCKER", {**base, "precedenceRule": 1}
    if candidate.state.current_status == "needs_review":
        return "review", "ACTIVITY_REVIEW_NEEDS_REVIEW", {**base, "precedenceRule": 2}
    if facts["reviewDue"]:
        return "review", "ACTIVITY_REVIEW_DUE", {**base, "precedenceRule": 2}
    if candidate.exit_ready and candidate.state.current_status != "verified":
        return (
            "verification",
            "ACTIVITY_VERIFICATION_EXIT_CRITERIA_MET",
            {**base, "precedenceRule": 3},
        )
    if facts["conceptualEvidenceDurationMs"] < CONCEPTUAL_EXPOSURE_THRESHOLD_MS:
        return (
            "learning",
            "ACTIVITY_LEARNING_CONCEPTUAL_THRESHOLD_UNMET",
            {**base, "precedenceRule": 4},
        )
    if facts["practicalEvidenceDurationMs"] < PRACTICAL_EVIDENCE_THRESHOLD_MS:
        return (
            "independent_practice",
            "ACTIVITY_INDEPENDENT_PRACTICE_PRACTICAL_THRESHOLD_UNMET",
            {**base, "precedenceRule": 5},
        )
    if candidate.independence_gap > 0:
        return (
            "independent_practice",
            "ACTIVITY_INDEPENDENT_PRACTICE_INDEPENDENCE_GAP",
            {**base, "precedenceRule": 5},
        )
    return (
        "independent_practice",
        "ACTIVITY_INDEPENDENT_PRACTICE_DEFAULT",
        {**base, "precedenceRule": 6},
    )


def _suggested_durations(
    target_ms: int | None, completed_today_ms: int, has_secondary: bool
) -> tuple[int, int | None]:
    minute = 60_000
    if target_ms is None:
        return 45 * minute, 20 * minute if has_secondary else None
    remaining = max(0, target_ms - completed_today_ms)
    if remaining == 0:
        return 20 * minute, 10 * minute if has_secondary else None
    if not has_secondary:
        return min(remaining, 90 * minute), None
    primary = min(round_half_up(remaining * 0.70), 90 * minute)
    secondary = remaining - primary
    if remaining >= 30 * minute:
        primary = max(primary, 20 * minute)
        secondary = max(min(secondary, 30 * minute), 10 * minute)
        if primary + secondary > remaining:
            primary = max(20 * minute, remaining - secondary)
    return primary, secondary


def build_recommendation(db: Session, *, now_ms: int | None = None) -> dict[str, Any]:
    now = now_ms if now_ms is not None else utc_now_ms()
    profile = get_or_create_profile(db)
    today = local_date_for_ms(now, profile.timezone)
    roadmap = db.scalar(select(Roadmap).where(Roadmap.is_current.is_(True)))
    if roadmap is None or roadmap.active_version_id is None or roadmap.current_phase_id is None:
        return {
            "recommendationVersion": RECOMMENDATION_VERSION,
            "generatedAt": now,
            "setupRequired": True,
            "guidance": "Import a roadmap package to receive recommendations.",
            "primary": None,
            "secondary": None,
        }
    phases = db.scalars(
        select(Phase)
        .where(Phase.roadmap_version_id == roadmap.active_version_id)
        .order_by(Phase.order_index)
    ).all()
    phase_by_id = {item.id: item for item in phases}
    current_phase = phase_by_id[roadmap.current_phase_id]
    tracks = db.scalars(
        select(Track).where(Track.roadmap_version_id == roadmap.active_version_id)
    ).all()
    track_by_id = {item.id: item for item in tracks}
    definitions = db.scalars(
        select(CompetencyDefinition).where(
            CompetencyDefinition.roadmap_version_id == roadmap.active_version_id,
            CompetencyDefinition.archived.is_(False),
        )
    ).all()
    identities = {
        item.id: item
        for item in db.scalars(
            select(CompetencyIdentity).where(
                CompetencyIdentity.id.in_(
                    [definition.competency_identity_id for definition in definitions]
                )
            )
        ).all()
    }
    states = {
        item.competency_identity_id: item
        for item in db.scalars(
            select(CompetencyState).where(
                CompetencyState.competency_identity_id.in_(
                    [definition.competency_identity_id for definition in definitions]
                )
            )
        ).all()
    }
    analytics = build_analytics(db, now_ms=now, range_name="30d")
    facts = analytics["competencies"]
    prerequisite_items = db.scalars(
        select(CompetencyPrerequisite).where(
            CompetencyPrerequisite.competency_definition_id.in_([item.id for item in definitions])
        )
    ).all()
    prerequisites: dict[str, list[CompetencyPrerequisite]] = {}
    for item in prerequisite_items:
        prerequisites.setdefault(item.competency_definition_id, []).append(item)
    direct_dependents: dict[str, int] = {}
    for item in prerequisite_items:
        if item.kind == "required":
            direct_dependents[item.prerequisite_competency_identity_id] = (
                direct_dependents.get(item.prerequisite_competency_identity_id, 0) + 1
            )
    exit_ready = _required_exit_ready(db, definitions)

    candidates: list[Candidate] = []
    for definition in definitions:
        state = states[definition.competency_identity_id]
        phase = phase_by_id[definition.phase_id]
        is_current_phase = phase.id == current_phase.id
        is_earlier_review = phase.order_index < current_phase.order_index and (
            state.current_status == "needs_review"
            or facts[definition.competency_identity_id]["reviewDue"]
        )
        if not is_current_phase and not is_earlier_review:
            continue
        required = [
            item for item in prerequisites.get(definition.id, []) if item.kind == "required"
        ]
        if any(
            states[item.prerequisite_competency_identity_id].current_status != "verified"
            for item in required
        ):
            continue
        if (
            state.current_status == "verified"
            and not facts[definition.competency_identity_id]["reviewDue"]
        ):
            continue
        candidates.append(
            Candidate(
                definition=definition,
                identity=identities[definition.competency_identity_id],
                state=state,
                phase=phase,
                track=track_by_id[definition.track_id],
                facts=facts[definition.competency_identity_id],
                exit_ready=exit_ready[definition.id],
                direct_unlock_count=direct_dependents.get(definition.competency_identity_id, 0),
            )
        )
    if not candidates:
        return {
            "recommendationVersion": RECOMMENDATION_VERSION,
            "generatedAt": now,
            "setupRequired": False,
            "primary": None,
            "secondary": None,
            "guidance": (
                "No eligible competency is available. Review prerequisites or choose the next "
                "phase explicitly."
            ),
        }

    all_session_facts = session_facts(db, profile.timezone)
    previous_30_start = today - timedelta(days=30)
    previous_7_start = today - timedelta(days=7)
    previous_day = today - timedelta(days=1)
    eligible_current_tracks = sorted(
        {
            candidate.track.stable_key: candidate.track.id
            for candidate in candidates
            if candidate.phase.id == current_phase.id
        }.items()
    )
    track_time = {
        track_id: sum(
            fact.duration_ms
            for fact in all_session_facts
            if previous_7_start <= fact.local_date < today and fact.track_id == track_id
        )
        for _stable_key, track_id in eligible_current_tracks
    }
    track_order = sorted(eligible_current_tracks, key=lambda item: (track_time[item[1]], item[0]))
    previous_day_total = sum(
        fact.duration_ms for fact in all_session_facts if fact.local_date == previous_day
    )

    for candidate in candidates:
        recent_practical = [
            fact
            for fact in all_session_facts
            if fact.competency_identity_id == candidate.identity.id
            and previous_30_start <= fact.local_date < today
            and fact.practical
        ]
        if candidate.state.current_status in {"practicing", "ready_for_verification"}:
            total = sum(fact.duration_ms for fact in recent_practical)
            independent = sum(
                fact.duration_ms for fact in recent_practical if fact.independent_practical
            )
            candidate.independence_gap = (
                20
                if total == 0
                else max(
                    0, min(20, round_half_up(max(0, (0.50 - independent / total) / 0.50) * 20))
                )
            )
        if candidate.phase.id == current_phase.id and len(track_order) > 1:
            index = [track_id for _key, track_id in track_order].index(candidate.track.id)
            candidate.track_balance = 10 if index == 0 else 5 if index == 1 else 0
        candidate_duration = sum(
            fact.duration_ms
            for fact in all_session_facts
            if fact.local_date == previous_day
            and fact.competency_identity_id == candidate.identity.id
        )
        share = candidate_duration / previous_day_total if previous_day_total else 0
        candidate.repetition_penalty = -20 if share > 0.50 else -10 if share >= 0.25 else 0
        if candidate.facts["reviewDue"]:
            if candidate.state.current_status == "needs_review":
                candidate.review_urgency = 25
            else:
                threshold = candidate.facts["freshnessThresholdDays"]
                candidate.review_urgency = min(
                    25, round_half_up(candidate.facts["daysOverdue"] / threshold * 25)
                )
        candidate.score = (
            PRIORITY_SCORE[candidate.definition.priority]
            + candidate.definition.weight * 4
            + STATUS_SCORE[candidate.state.current_status]
            + candidate.review_urgency
            + min(20, candidate.direct_unlock_count * 5)
            + candidate.independence_gap
            + candidate.track_balance
            + candidate.repetition_penalty
        )

    candidates.sort(
        key=lambda item: (
            -item.score,
            -item.direct_unlock_count,
            item.facts["lastSuccessfulEvidenceAt"]
            if item.facts["lastSuccessfulEvidenceAt"] is not None
            else -1,
            item.identity.stable_key,
        )
    )
    primary = candidates[0]
    secondary = next(
        (
            item
            for item in candidates[1:]
            if item.facts["reviewDue"]
            or item.exit_ready
            or item.definition.priority == "supporting"
        ),
        None,
    )
    today_completed = sum(
        fact.duration_ms for fact in all_session_facts if fact.local_date == today
    )
    primary_duration, secondary_duration = _suggested_durations(
        profile.target_duration_ms_per_active_day, today_completed, secondary is not None
    )

    def serialize(candidate: Candidate, duration: int | None) -> dict[str, Any]:
        activity, activity_reason, activity_facts = _activity(candidate)
        reasons = []
        if candidate.definition.priority == "core":
            reasons.append("CORE_COMPETENCY")
        if candidate.state.current_status == "practicing":
            reasons.append("PRACTICING_CONTINUATION")
        if candidate.direct_unlock_count:
            reasons.append("BLOCKS_DOWNSTREAM_SKILLS")
        if candidate.independence_gap:
            reasons.append("INDEPENDENT_EVIDENCE_LOW")
        if candidate.facts["reviewDue"]:
            reasons.append("REVIEW_DUE")
        if candidate.exit_ready:
            reasons.append("READY_FOR_VERIFICATION")
        if candidate.repetition_penalty:
            reasons.append("RECENT_REPETITION_PENALTY")
        return {
            "competencyIdentityId": candidate.identity.id,
            "stableKey": candidate.identity.stable_key,
            "title": candidate.definition.title,
            "activity": activity,
            "suggestedDurationMs": duration,
            "reasonCodes": reasons,
            "activityReasonCode": activity_reason,
            "explanation": {
                "priority": candidate.definition.priority,
                "weight": candidate.definition.weight,
                "status": candidate.state.current_status,
                "directRequiredDependents": candidate.direct_unlock_count,
                "lastSuccessfulEvidenceAt": candidate.facts["lastSuccessfulEvidenceAt"],
                "reviewUrgency": candidate.review_urgency,
                "independenceGap": candidate.independence_gap,
                "trackBalance": candidate.track_balance,
                "recentRepetitionPenalty": candidate.repetition_penalty,
                "activityFacts": activity_facts,
            },
        }

    return {
        "recommendationVersion": RECOMMENDATION_VERSION,
        "generatedAt": now,
        "localDate": today.isoformat(),
        "setupRequired": False,
        "limitedTelemetry": not bool(all_session_facts),
        "todayCompletedDurationMs": today_completed,
        "todayTargetDurationMs": profile.target_duration_ms_per_active_day,
        "primary": serialize(primary, primary_duration),
        "secondary": serialize(secondary, secondary_duration) if secondary else None,
    }


@router.get("/today")
async def today_recommendation(
    _auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
) -> dict[str, Any]:
    payload = build_recommendation(db)
    if payload.get("primary") is not None:
        snapshot = RecommendationSnapshot(
            local_date=payload["localDate"],
            engine_version=RECOMMENDATION_VERSION,
            primary_competency_identity_id=payload["primary"]["competencyIdentityId"],
            primary_activity_type=payload["primary"]["activity"],
            secondary_competency_identity_id=(
                payload["secondary"]["competencyIdentityId"] if payload.get("secondary") else None
            ),
            structured_payload_json=json.dumps(payload, separators=(",", ":")),
        )
        db.add(snapshot)
        db.commit()
        payload["snapshotId"] = snapshot.id
    return serialize_api_instants(payload)


@router.post("/{snapshot_id}/decision")
async def record_decision(
    snapshot_id: str,
    payload: RecommendationDecision,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    snapshot = db.get(RecommendationSnapshot, snapshot_id)
    if snapshot is None:
        raise AppError(
            404, "RECOMMENDATION_NOT_FOUND", "The recommendation snapshot does not exist."
        )
    snapshot.accepted_primary = payload.accepted_primary
    snapshot.chosen_competency_identity_id = payload.chosen_competency_identity_id
    db.commit()
    return {
        "snapshotId": snapshot.id,
        "acceptedPrimary": snapshot.accepted_primary,
        "chosenCompetencyIdentityId": snapshot.chosen_competency_identity_id,
    }
