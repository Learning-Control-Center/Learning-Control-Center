from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LearningControlAuthorityEvent(Base):
    __tablename__ = "learning_control_authority_events"
    __table_args__ = (
        CheckConstraint("event_sequence > 0", name="ck_authority_event_sequence"),
        CheckConstraint(
            "command_type IN ('bootstrap','activate_v2','surface_change')",
            name="ck_authority_event_command",
        ),
        CheckConstraint("actor IN ('system','user')", name="ck_authority_event_actor"),
        CheckConstraint("length(reason) > 0", name="ck_authority_event_reason"),
        CheckConstraint("length(payload_hash) = 64", name="ck_authority_event_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    command_type: Mapped[str] = mapped_column(String(32), nullable=False)
    prior_state_json: Mapped[str | None] = mapped_column(Text)
    resulting_state_json: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[int] = mapped_column(Integer, nullable=False)


class LearningControlAuthorityState(Base):
    __tablename__ = "learning_control_authority_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_authority_state_singleton"),
        CheckConstraint("event_sequence > 0", name="ck_authority_state_sequence"),
        CheckConstraint(
            "canonical_learning_authority IN ('legacy_v1','v2')",
            name="ck_authority_canonical",
        ),
        CheckConstraint(
            "roadmap_presentation IN ('legacy_v1','v2','v1_read_only')",
            name="ck_authority_roadmap_surface",
        ),
        CheckConstraint(
            "recommendation_presentation IN ('legacy_v1','v2','v1_read_only')",
            name="ck_authority_recommendation_surface",
        ),
        CheckConstraint(
            "today_presentation IN ('legacy_v1','v2','v1_read_only')",
            name="ck_authority_today_surface",
        ),
        CheckConstraint("length(state_hash) = 64", name="ck_authority_state_hash"),
        CheckConstraint(
            "(canonical_learning_authority = 'legacy_v1' AND "
            "roadmap_presentation = 'legacy_v1' AND "
            "recommendation_presentation = 'legacy_v1' AND "
            "today_presentation = 'legacy_v1') OR "
            "(canonical_learning_authority = 'v2' AND "
            "roadmap_presentation IN ('v2','v1_read_only') AND "
            "recommendation_presentation IN ('v2','v1_read_only') AND "
            "today_presentation IN ('v2','v1_read_only'))",
            name="ck_authority_state_semantics",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    canonical_learning_authority: Mapped[str] = mapped_column(String(16), nullable=False)
    roadmap_presentation: Mapped[str] = mapped_column(String(24), nullable=False)
    recommendation_presentation: Mapped[str] = mapped_column(String(24), nullable=False)
    today_presentation: Mapped[str] = mapped_column(String(24), nullable=False)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    last_event_id: Mapped[str] = mapped_column(
        ForeignKey("learning_control_authority_events.id", ondelete="RESTRICT"), nullable=False
    )
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)


def _reject_authority_history_mutation(*_args: object) -> None:
    raise ValueError("Learning-control authority history is immutable.")


event.listen(LearningControlAuthorityEvent, "before_update", _reject_authority_history_mutation)
event.listen(LearningControlAuthorityEvent, "before_delete", _reject_authority_history_mutation)
