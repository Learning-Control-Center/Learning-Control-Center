from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.analysis.contracts import AnalysisEnvelope

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
    definition: dict[str, Any]
    facts: dict[str, Any]
    independence_gap: int = 0
    track_balance: int = 0
    repetition_penalty: int = 0
    review_urgency: int = 0
    score: int = 0


def _activity(candidate: Candidate) -> tuple[str, str, dict[str, Any]]:
    item, facts = candidate.definition, candidate.facts
    base = {
        "status": item["legacyStatus"],
        "conceptualEvidenceDurationMs": facts["conceptualEvidenceDurationMs"],
        "conceptualThresholdMs": CONCEPTUAL_EXPOSURE_THRESHOLD_MS,
        "practicalEvidenceDurationMs": facts["practicalEvidenceDurationMs"],
        "practicalThresholdMs": PRACTICAL_EVIDENCE_THRESHOLD_MS,
        "exitCriteriaReady": item["exitCriteriaReady"],
        "independenceGapContribution": candidate.independence_gap,
        "latestBlockedSessionAt": facts["latestBlockedSessionAt"],
        "latestLaterSuccessfulPracticalAt": facts["latestLaterSuccessfulPracticalAt"],
    }
    if facts["unresolvedTechnicalBlocker"]:
        return "research", "ACTIVITY_RESEARCH_UNRESOLVED_BLOCKER", {**base, "precedenceRule": 1}
    if item["legacyStatus"] == "needs_review" or facts["reviewDue"]:
        return (
            "review",
            "ACTIVITY_REVIEW_NEEDS_REVIEW"
            if item["legacyStatus"] == "needs_review"
            else "ACTIVITY_REVIEW_DUE",
            {**base, "precedenceRule": 2},
        )
    if item["exitCriteriaReady"] and item["legacyStatus"] != "verified":
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


def evaluate(envelope: AnalysisEnvelope) -> dict[str, Any]:
    data = envelope.normalized_facts["legacy_v1_recommendation_input"]
    now = envelope.generated_at
    if data["setupRequired"]:
        return {
            "recommendationVersion": 1,
            "generatedAt": now,
            "setupRequired": True,
            "guidance": data["guidance"],
            "primary": None,
            "secondary": None,
        }
    current_phase_id = data["currentPhaseId"]
    candidates: list[Candidate] = []
    for item in data["candidateDefinitions"]:
        facts = item["analyticsFacts"]
        current = item["phaseId"] == current_phase_id
        earlier_review = item["phaseOrderIndex"] < data["currentPhaseOrderIndex"] and (
            item["legacyStatus"] == "needs_review" or facts["reviewDue"]
        )
        if not current and not earlier_review:
            continue
        if any(
            not prerequisite["legacyVerified"] for prerequisite in item["requiredPrerequisites"]
        ):
            continue
        if item["legacyStatus"] == "verified" and not facts["reviewDue"]:
            continue
        candidates.append(Candidate(item, facts))
    if not candidates:
        return {
            "recommendationVersion": 1,
            "generatedAt": now,
            "setupRequired": False,
            "primary": None,
            "secondary": None,
            "guidance": (
                "No eligible competency is available. Review prerequisites or choose the next "
                "phase explicitly."
            ),
        }

    windows = data["sessionWindowFacts"]
    track_pairs = sorted(
        {
            (item.definition["trackStableKey"], item.definition["trackId"])
            for item in candidates
            if item.definition["phaseId"] == current_phase_id
        }
    )
    track_time = {
        track_id: windows["previous7DurationByTrack"].get(track_id, 0)
        for _, track_id in track_pairs
    }
    track_order = sorted(track_pairs, key=lambda item: (track_time[item[1]], item[0]))
    previous_day_total = windows["previousDayTotalDurationMs"]
    for candidate in candidates:
        item, facts = candidate.definition, candidate.facts
        recent = windows["previous30PracticalByCompetency"].get(
            item["competencyIdentityId"],
            {"durationMs": 0, "independentDurationMs": 0},
        )
        if item["legacyStatus"] in {"practicing", "ready_for_verification"}:
            total = recent["durationMs"]
            independent = recent["independentDurationMs"]
            candidate.independence_gap = (
                20
                if total == 0
                else max(
                    0, min(20, round_half_up(max(0, (0.50 - independent / total) / 0.50) * 20))
                )
            )
        if item["phaseId"] == current_phase_id and len(track_order) > 1:
            index = [track_id for _, track_id in track_order].index(item["trackId"])
            candidate.track_balance = 10 if index == 0 else 5 if index == 1 else 0
        duration = windows["previousDayDurationByCompetency"].get(item["competencyIdentityId"], 0)
        share = duration / previous_day_total if previous_day_total else 0
        candidate.repetition_penalty = -20 if share > 0.50 else -10 if share >= 0.25 else 0
        if facts["reviewDue"]:
            candidate.review_urgency = (
                25
                if item["legacyStatus"] == "needs_review"
                else min(
                    25, round_half_up(facts["daysOverdue"] / facts["freshnessThresholdDays"] * 25)
                )
            )
        candidate.score = (
            PRIORITY_SCORE[item["priority"]]
            + item["weight"] * 4
            + STATUS_SCORE[item["legacyStatus"]]
            + candidate.review_urgency
            + min(20, item["directRequiredDependentCount"] * 5)
            + candidate.independence_gap
            + candidate.track_balance
            + candidate.repetition_penalty
        )
    candidates.sort(
        key=lambda item: (
            -item.score,
            -item.definition["directRequiredDependentCount"],
            item.facts["lastSuccessfulEvidenceAt"]
            if item.facts["lastSuccessfulEvidenceAt"] is not None
            else -1,
            item.definition["stableKey"],
        )
    )
    primary = candidates[0]
    secondary = next(
        (
            item
            for item in candidates[1:]
            if item.facts["reviewDue"]
            or item.definition["exitCriteriaReady"]
            or item.definition["priority"] == "supporting"
        ),
        None,
    )
    primary_duration, secondary_duration = _suggested_durations(
        data["todayTargetDurationMs"], data["todayCompletedDurationMs"], secondary is not None
    )

    def serialize(candidate: Candidate, duration: int | None) -> dict[str, Any]:
        item, facts = candidate.definition, candidate.facts
        activity, activity_reason, activity_facts = _activity(candidate)
        reasons: list[str] = []
        if item["priority"] == "core":
            reasons.append("CORE_COMPETENCY")
        if item["legacyStatus"] == "practicing":
            reasons.append("PRACTICING_CONTINUATION")
        if item["directRequiredDependentCount"]:
            reasons.append("BLOCKS_DOWNSTREAM_SKILLS")
        if candidate.independence_gap:
            reasons.append("INDEPENDENT_EVIDENCE_LOW")
        if facts["reviewDue"]:
            reasons.append("REVIEW_DUE")
        if item["exitCriteriaReady"]:
            reasons.append("READY_FOR_VERIFICATION")
        if candidate.repetition_penalty:
            reasons.append("RECENT_REPETITION_PENALTY")
        return {
            "competencyIdentityId": item["competencyIdentityId"],
            "stableKey": item["stableKey"],
            "title": item["title"],
            "activity": activity,
            "suggestedDurationMs": duration,
            "reasonCodes": reasons,
            "activityReasonCode": activity_reason,
            "explanation": {
                "priority": item["priority"],
                "weight": item["weight"],
                "status": item["legacyStatus"],
                "directRequiredDependents": item["directRequiredDependentCount"],
                "lastSuccessfulEvidenceAt": facts["lastSuccessfulEvidenceAt"],
                "reviewUrgency": candidate.review_urgency,
                "independenceGap": candidate.independence_gap,
                "trackBalance": candidate.track_balance,
                "recentRepetitionPenalty": candidate.repetition_penalty,
                "activityFacts": activity_facts,
            },
        }

    return {
        "recommendationVersion": 1,
        "generatedAt": now,
        "localDate": data["localDate"],
        "setupRequired": False,
        "limitedTelemetry": not windows["telemetryPresent"],
        "todayCompletedDurationMs": data["todayCompletedDurationMs"],
        "todayTargetDurationMs": data["todayTargetDurationMs"],
        "primary": serialize(primary, primary_duration),
        "secondary": serialize(secondary, secondary_duration) if secondary else None,
    }
