from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import CompetencyState, CompetencyStatusEvent, LearningSession

CONCEPTUAL_ACTIVITIES = {"learning", "reading", "research"}
PRACTICAL_ACTIVITIES = {"coding", "practice", "debugging", "project", "verification"}
SUCCESSFUL_OUTCOMES = {"completed", "partial"}


def get_competency_state(db: Session, competency_identity_id: str) -> CompetencyState:
    state = db.get(CompetencyState, competency_identity_id)
    if state is None:
        raise AppError(404, "COMPETENCY_NOT_FOUND", "The competency does not exist.")
    return state


def transition_status(
    db: Session,
    competency_identity_id: str,
    to_status: str,
    *,
    reason: str,
    source: str,
    verification_record_id: str | None = None,
) -> CompetencyState:
    state = get_competency_state(db, competency_identity_id)
    if to_status == "verified" and verification_record_id is None:
        raise AppError(
            422,
            "VERIFICATION_RECORD_REQUIRED",
            "A passed verification record is required to set verified status.",
        )
    if state.current_status == to_status:
        return state
    event = CompetencyStatusEvent(
        competency_identity_id=competency_identity_id,
        from_status=state.current_status,
        to_status=to_status,
        reason=reason,
        source=source,
        verification_record_id=verification_record_id,
    )
    state.current_status = to_status
    db.add(event)
    db.flush()
    return state


def apply_session_promotion(db: Session, learning_session: LearningSession) -> None:
    if (
        learning_session.tombstoned_at is not None
        or learning_session.competency_identity_id is None
        or learning_session.duration_ms is None
        or learning_session.duration_ms <= 0
        or learning_session.outcome not in SUCCESSFUL_OUTCOMES
        or learning_session.timed_state == "cancelled"
    ):
        return
    state = get_competency_state(db, learning_session.competency_identity_id)
    target: str | None = None
    if (
        learning_session.activity_type in CONCEPTUAL_ACTIVITIES
        and state.current_status == "not_started"
    ):
        target = "learning"
    if learning_session.activity_type in PRACTICAL_ACTIVITIES and state.current_status in {
        "not_started",
        "learning",
        "needs_review",
    }:
        target = "practicing"
    if learning_session.activity_type == "review" and state.current_status == "needs_review":
        target = "practicing"
    if target is not None:
        transition_status(
            db,
            learning_session.competency_identity_id,
            target,
            reason=f"Qualifying {learning_session.activity_type} session",
            source="session",
        )


def has_required_dependency_cycle(edges: dict[str, set[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for prerequisite in edges.get(node, set()):
            if visit(prerequisite):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in edges)


def active_timed_session(db: Session) -> LearningSession | None:
    return db.scalar(
        select(LearningSession).where(
            LearningSession.session_mode == "timed",
            LearningSession.timed_state.in_(["running", "paused"]),
        )
    )
