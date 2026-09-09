from __future__ import annotations

from typing import Any

import pytest
from app.analysis.contracts import AnalysisEnvelope, content_hash, immutable
from app.recommendation.v1_policy import evaluate


def _candidate(stable_key: str = "candidate.a") -> dict[str, Any]:
    return {
        "definitionId": f"definition-{stable_key}",
        "competencyIdentityId": f"identity-{stable_key}",
        "stableKey": stable_key,
        "title": stable_key,
        "priority": "core",
        "weight": 3,
        "phaseId": "phase-1",
        "phaseOrderIndex": 1,
        "trackId": "track-1",
        "trackStableKey": "track-1",
        "legacyStatus": "learning",
        "analyticsFacts": {
            "conceptualEvidenceDurationMs": 0,
            "practicalEvidenceDurationMs": 0,
            "latestBlockedSessionAt": None,
            "latestLaterSuccessfulPracticalAt": None,
            "unresolvedTechnicalBlocker": False,
            "reviewDue": False,
            "freshnessThresholdDays": 7,
            "daysOverdue": 0,
            "lastSuccessfulEvidenceAt": None,
        },
        "exitCriteriaReady": False,
        "directRequiredDependentCount": 0,
        "requiredPrerequisiteIdentityIds": [],
        "requiredPrerequisites": [],
        "allPrerequisites": [],
    }


def _input(*candidates: dict[str, Any]) -> dict[str, Any]:
    return {
        "setupRequired": False,
        "guidance": None,
        "localDate": "2026-09-01",
        "currentPhaseId": "phase-1",
        "currentPhaseOrderIndex": 1,
        "todayTargetDurationMs": 3_600_000,
        "todayCompletedDurationMs": 0,
        "legacyLifecycleStates": {},
        "candidateDefinitions": list(candidates),
        "sessionFacts": [],
        "sessionWindowFacts": {
            "previous30PracticalByCompetency": {},
            "previous7DurationByTrack": {},
            "previousDayTotalDurationMs": 0,
            "previousDayDurationByCompetency": {},
            "todayCompletedDurationMs": 0,
            "telemetryPresent": False,
        },
        "disciplineConstraints": {
            "weeklyTargetActiveDays": 5,
            "targetDurationMsPerActiveDay": 3_600_000,
            "timezone": "Europe/Istanbul",
        },
    }


def _envelope(data: dict[str, Any]) -> AnalysisEnvelope:
    normalized = {"legacy_v1_recommendation_input": data}
    return AnalysisEnvelope(
        purpose="v1_recommendation_compat",
        schema_version=1,
        generated_at=1_788_264_000_000,
        cutoff_at=1_788_264_000_001,
        cutoff_semantics="exclusive",
        timezone="Europe/Istanbul",
        completed_through_date="2026-09-01",
        target_profile_id=None,
        target_profile_version_id=None,
        capability_scale_version_references=(),
        learning_graph_reference=None,
        curriculum_reference=None,
        semantic_definition_references=(),
        policy_versions=immutable({"recommendation": "recommendation-v1"}),
        discipline_configuration_reference="discipline_profile:1",
        configuration_hash=content_hash(data["disciplineConstraints"]),
        application_version="1.0.0",
        input_lineage=(),
        input_hash=content_hash(normalized),
        normalized_facts=immutable(normalized),
        signals=(),
        completeness="partial",
        unknown_markers=(),
        output_hash=content_hash(normalized),
    )


@pytest.mark.parametrize(
    ("status", "exit_ready", "conceptual_ms", "practical_ms", "activity"),
    [
        ("needs_review", False, 0, 0, "review"),
        ("ready_for_verification", True, 1_800_000, 1_800_000, "verification"),
        ("learning", False, 1_799_999, 1_800_000, "learning"),
        ("learning", False, 1_800_000, 1_799_999, "independent_practice"),
        ("learning", False, 1_800_000, 1_800_000, "independent_practice"),
        ("not_started", False, 0, 0, "learning"),
        ("practicing", False, 0, 0, "learning"),
        ("verified", False, 1_800_000, 1_800_000, None),
    ],
)
def test_status_and_threshold_golden_matrix(
    status: str,
    exit_ready: bool,
    conceptual_ms: int,
    practical_ms: int,
    activity: str | None,
) -> None:
    candidate = _candidate()
    candidate["legacyStatus"] = status
    candidate["exitCriteriaReady"] = exit_ready
    candidate["analyticsFacts"]["conceptualEvidenceDurationMs"] = conceptual_ms
    candidate["analyticsFacts"]["practicalEvidenceDurationMs"] = practical_ms
    primary = evaluate(_envelope(_input(candidate)))["primary"]
    assert (primary["activity"] if primary else None) == activity


def test_prerequisite_phase_tie_secondary_duration_and_timezone_matrix() -> None:
    blocked = _candidate("candidate.blocked")
    blocked["requiredPrerequisites"] = [
        {"competencyIdentityId": "prerequisite", "legacyVerified": False}
    ]
    assert evaluate(_envelope(_input(blocked)))["primary"] is None
    allowed = _candidate("candidate.allowed")
    allowed["requiredPrerequisites"] = [
        {"competencyIdentityId": "prerequisite", "legacyVerified": True}
    ]
    allowed["allPrerequisites"] = [
        {"competencyIdentityId": "recommended", "kind": "recommended", "legacyVerified": False}
    ]
    assert evaluate(_envelope(_input(allowed)))["primary"] is not None

    verified_review = _candidate("candidate.verified-review")
    verified_review["legacyStatus"] = "verified"
    verified_review["analyticsFacts"]["reviewDue"] = True
    assert evaluate(_envelope(_input(verified_review)))["primary"]["activity"] == "review"

    later = _candidate("candidate.later")
    later["phaseId"] = "phase-2"
    later["phaseOrderIndex"] = 2
    assert evaluate(_envelope(_input(later)))["primary"] is None

    earlier_review = _candidate("candidate.earlier")
    earlier_review["phaseId"] = "phase-0"
    earlier_review["phaseOrderIndex"] = 0
    earlier_review["analyticsFacts"]["reviewDue"] = True
    assert (
        evaluate(_envelope(_input(earlier_review)))["primary"]["stableKey"] == "candidate.earlier"
    )

    a = _candidate("candidate.a")
    b = _candidate("candidate.b")
    b["priority"] = "supporting"
    result = evaluate(_envelope(_input(b, a)))
    assert result["localDate"] == "2026-09-01"
    assert result["primary"]["stableKey"] == "candidate.a"
    assert result["secondary"]["stableKey"] == "candidate.b"
    assert result["primary"]["suggestedDurationMs"] == 2_520_000
    assert result["secondary"]["suggestedDurationMs"] == 1_080_000

    completed = _input(a)
    completed["todayCompletedDurationMs"] = 3_600_000
    completed["sessionWindowFacts"]["todayCompletedDurationMs"] = 3_600_000
    assert evaluate(_envelope(completed))["primary"]["suggestedDurationMs"] == 1_200_000


def test_independence_track_repetition_review_and_duration_boundaries() -> None:
    independence = _candidate("candidate.independence")
    independence["legacyStatus"] = "practicing"
    independence["analyticsFacts"]["conceptualEvidenceDurationMs"] = 1_800_000
    independence["analyticsFacts"]["practicalEvidenceDurationMs"] = 1_800_000
    data = _input(independence)
    data["sessionWindowFacts"]["previous30PracticalByCompetency"] = {
        independence["competencyIdentityId"]: {
            "durationMs": 2_000_000,
            "independentDurationMs": 1_000_000,
        }
    }
    assert evaluate(_envelope(data))["primary"]["explanation"]["independenceGap"] == 0
    data["sessionWindowFacts"]["previous30PracticalByCompetency"][
        independence["competencyIdentityId"]
    ]["independentDurationMs"] = 900_000
    assert evaluate(_envelope(data))["primary"]["explanation"]["independenceGap"] > 0

    first = _candidate("candidate.a")
    second = _candidate("candidate.b")
    second["trackId"] = "track-2"
    second["trackStableKey"] = "track-2"
    second["exitCriteriaReady"] = True
    tracks = _input(first, second)
    tracks["sessionWindowFacts"]["previous7DurationByTrack"] = {
        "track-1": 100,
        "track-2": 200,
    }
    track_result = evaluate(_envelope(tracks))
    assert track_result["primary"]["stableKey"] == "candidate.a"
    assert track_result["primary"]["explanation"]["trackBalance"] == 10
    tracks["sessionWindowFacts"]["previous7DurationByTrack"] = {
        "track-1": 100,
        "track-2": 100,
    }
    tied = evaluate(_envelope(tracks))
    assert tied["primary"]["stableKey"] == "candidate.a"
    assert tied["primary"]["explanation"]["trackBalance"] == 10
    assert tied["secondary"]["explanation"]["trackBalance"] == 5

    repetition = _input(first)
    repetition["sessionWindowFacts"]["previousDayTotalDurationMs"] = 100
    repetition["sessionWindowFacts"]["previousDayDurationByCompetency"] = {
        first["competencyIdentityId"]: 24
    }
    assert evaluate(_envelope(repetition))["primary"]["explanation"]["recentRepetitionPenalty"] == 0
    repetition["sessionWindowFacts"]["previousDayDurationByCompetency"][
        first["competencyIdentityId"]
    ] = 25
    assert (
        evaluate(_envelope(repetition))["primary"]["explanation"]["recentRepetitionPenalty"] == -10
    )
    repetition["sessionWindowFacts"]["previousDayDurationByCompetency"][
        first["competencyIdentityId"]
    ] = 50
    assert (
        evaluate(_envelope(repetition))["primary"]["explanation"]["recentRepetitionPenalty"] == -10
    )
    repetition["sessionWindowFacts"]["previousDayDurationByCompetency"][
        first["competencyIdentityId"]
    ] = 51
    assert (
        evaluate(_envelope(repetition))["primary"]["explanation"]["recentRepetitionPenalty"] == -20
    )

    review = _candidate("candidate.review")
    review["analyticsFacts"].update(
        {"reviewDue": True, "daysOverdue": 3, "freshnessThresholdDays": 7}
    )
    assert evaluate(_envelope(_input(review)))["primary"]["explanation"]["reviewUrgency"] == 11

    no_target = _input(first, second)
    no_target["candidateDefinitions"][1]["priority"] = "supporting"
    no_target["todayTargetDurationMs"] = None
    no_target["disciplineConstraints"]["targetDurationMsPerActiveDay"] = None
    no_target_result = evaluate(_envelope(no_target))
    assert no_target_result["primary"]["suggestedDurationMs"] == 2_700_000
    assert no_target_result["secondary"]["suggestedDurationMs"] == 1_200_000
    remaining = _input(first)
    remaining["todayCompletedDurationMs"] = 3_000_000
    assert evaluate(_envelope(remaining))["primary"]["suggestedDurationMs"] == 600_000
