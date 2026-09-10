from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.capability_scales import BUILTIN_CREATED_AT, stable_v2_id
from app.compatibility.v1.profile_competency_backfill import canonical_rows_hash

POLICY_KEY = "activity-legacy-backfill-policy/v1"
RUN_ID = stable_v2_id("backfill-run:activity-session:activity-legacy-backfill-policy/v1")
ACTIVITY_TYPES = (
    "learning",
    "reading",
    "practice",
    "coding",
    "debugging",
    "project",
    "review",
    "verification",
    "research",
)


def activity_category_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": stable_v2_id(f"activity-category:{key}:v1"),
            "stable_key": key,
            "vocabulary_version": "v1",
            "display_label": key.replace("_", " ").title(),
            "created_at": BUILTIN_CREATED_AT,
        }
        for key in ACTIVITY_TYPES
    ]


@dataclass(frozen=True)
class ActivitySessionBackfill:
    activities: list[dict[str, Any]]
    contributions: list[dict[str, Any]]
    session_activity_ids: dict[str, str]
    source_hash: str
    result_hash: str


def build_activity_session_backfill(
    sessions: list[dict[str, Any]],
) -> ActivitySessionBackfill:
    v2_session_columns = {"activity_id", "tombstoned_at", "tombstone_reason"}
    source_rows = sorted(
        (
            {key: value for key, value in row.items() if key not in v2_session_columns}
            for row in sessions
        ),
        key=lambda row: str(row["id"]),
    )
    activities: list[dict[str, Any]] = []
    contributions: list[dict[str, Any]] = []
    links: dict[str, str] = {}
    for session in source_rows:
        session_id = str(session["id"])
        activity_id = stable_v2_id(f"activity:learning_sessions:{session_id}:{POLICY_KEY}")
        links[session_id] = activity_id
        activities.append(
            {
                "id": activity_id,
                "title": "Legacy learning session",
                "description": None,
                "category_stable_key": session["activity_type"],
                "category_version": "v1",
                "occurred_at": session["started_at"],
                "context_started_at": session["started_at"],
                "context_ended_at": session["ended_at"],
                "creator_source": "migration",
                "provenance": POLICY_KEY,
                "created_at": session["created_at"],
                "supersedes_activity_id": None,
                "outcome_classification": session["outcome"],
            }
        )
        competency_id = session["competency_identity_id"]
        if competency_id is not None:
            contributions.append(
                {
                    "id": stable_v2_id(
                        f"session-contribution:{session_id}:{competency_id}:{POLICY_KEY}"
                    ),
                    "session_id": session_id,
                    "competency_identity_id": competency_id,
                    "criterion_identity_id": None,
                    "relevance": "primary",
                    "created_at": session["created_at"],
                    "provenance": "deterministic_legacy_backfill",
                }
            )
    result_rows = sorted(activities + contributions, key=lambda row: (str(row["id"]), len(row)))
    return ActivitySessionBackfill(
        activities=activities,
        contributions=contributions,
        session_activity_ids=links,
        source_hash=canonical_rows_hash(source_rows),
        result_hash=canonical_rows_hash(result_rows),
    )
