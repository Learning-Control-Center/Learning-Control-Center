from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TodayGeneration(Base):
    __tablename__ = "today_generations"
    __table_args__ = (
        UniqueConstraint(
            "local_date",
            "explicit_generation_sequence",
            name="uq_today_generation_key",
        ),
        CheckConstraint(
            "explicit_generation_sequence > 0", name="ck_today_generation_sequence"
        ),
        CheckConstraint(
            "length(request_hash) = 64 AND length(output_hash) = 64",
            name="ck_today_generation_hashes",
        ),
        Index("ix_today_generation_date_sequence", "local_date", "explicit_generation_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    local_date: Mapped[str] = mapped_column(String(10), nullable=False)
    timezone: Mapped[str] = mapped_column(String(128), nullable=False)
    recommendation_run_id: Mapped[str] = mapped_column(
        ForeignKey("recommendation_v2_runs.id", ondelete="RESTRICT"), nullable=False
    )
    recommendation_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    today_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    explicit_generation_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    generation_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    generated_at: Mapped[int] = mapped_column(Integer, nullable=False)
    output_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_regeneration: Mapped[bool] = mapped_column(Boolean, nullable=False)


class TodaySuggestion(Base):
    __tablename__ = "today_suggestions"
    __table_args__ = (
        UniqueConstraint("generation_id", "ordinal", name="uq_today_suggestion_ordinal"),
        UniqueConstraint(
            "generation_id", "recommendation_id", name="uq_today_suggestion_recommendation"
        ),
        CheckConstraint("ordinal > 0", name="ck_today_suggestion_ordinal"),
        CheckConstraint(
            "portfolio_role IN ('primary','complementary','maintenance')",
            name="ck_today_suggestion_portfolio_role",
        ),
        CheckConstraint(
            "expires_at > created_at", name="ck_today_suggestion_expiration_order"
        ),
        CheckConstraint(
            "advisory_duration_ms IS NULL OR advisory_duration_ms > 0",
            name="ck_today_suggestion_advisory_duration",
        ),
        CheckConstraint(
            "(duration_minimum_ms IS NULL AND duration_preferred_ms IS NULL "
            "AND duration_maximum_ms IS NULL AND advisory_duration_ms IS NULL) OR "
            "(duration_minimum_ms > 0 AND duration_preferred_ms >= duration_minimum_ms "
            "AND duration_maximum_ms >= duration_preferred_ms "
            "AND advisory_duration_ms >= duration_minimum_ms "
            "AND advisory_duration_ms <= duration_maximum_ms)",
            name="ck_today_suggestion_duration_shape",
        ),
        CheckConstraint(
            "length(presentation_hash) = 64", name="ck_today_suggestion_presentation_hash"
        ),
        CheckConstraint(
            "replaces_suggestion_id IS NULL OR replaces_suggestion_id != id",
            name="ck_today_suggestion_replacement_distinct",
        ),
        Index("ix_today_suggestion_expiry", "expires_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    generation_id: Mapped[str] = mapped_column(
        ForeignKey("today_generations.id", ondelete="RESTRICT"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    local_date: Mapped[str] = mapped_column(String(10), nullable=False)
    timezone: Mapped[str] = mapped_column(String(128), nullable=False)
    recommendation_run_id: Mapped[str] = mapped_column(
        ForeignKey("recommendation_v2_runs.id", ondelete="RESTRICT"), nullable=False
    )
    recommendation_id: Mapped[str] = mapped_column(
        ForeignKey("recommendation_v2_recommendations.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("recommendation_v2_candidates.id", ondelete="RESTRICT"), nullable=False
    )
    portfolio_role: Mapped[str] = mapped_column(String(16), nullable=False)
    advisory_duration_ms: Mapped[int | None] = mapped_column(Integer)
    duration_minimum_ms: Mapped[int | None] = mapped_column(Integer)
    duration_preferred_ms: Mapped[int | None] = mapped_column(Integer)
    duration_maximum_ms: Mapped[int | None] = mapped_column(Integer)
    presentation_json: Mapped[str] = mapped_column(Text, nullable=False)
    presentation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    presentation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    today_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    generation_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)
    replaces_suggestion_id: Mapped[str | None] = mapped_column(
        ForeignKey("today_suggestions.id", ondelete="RESTRICT")
    )


class TodayInteraction(Base):
    __tablename__ = "today_interactions"
    __table_args__ = (
        UniqueConstraint("suggestion_id", "event_sequence", name="uq_today_interaction_sequence"),
        UniqueConstraint("idempotency_key", name="uq_today_interaction_idempotency"),
        CheckConstraint("event_sequence > 0", name="ck_today_interaction_sequence"),
        CheckConstraint(
            "interaction_type IN ('viewed','accepted','started','completed',"
            "'partially_completed','skipped','replaced','expired')",
            name="ck_today_interaction_type",
        ),
        CheckConstraint(
            "actor IN ('user','system')", name="ck_today_interaction_actor"
        ),
        CheckConstraint("length(payload_hash) = 64", name="ck_today_interaction_payload_hash"),
        CheckConstraint(
            "(interaction_type IN ('started','completed','partially_completed') "
            "AND activity_id IS NOT NULL AND session_id IS NOT NULL) OR "
            "(interaction_type = 'replaced' AND activity_id IS NOT NULL "
            "AND session_id IS NULL) OR "
            "(interaction_type IN ('viewed','accepted','skipped','expired') "
            "AND activity_id IS NULL AND session_id IS NULL)",
            name="ck_today_interaction_actuality_shape",
        ),
        CheckConstraint(
            "replacement_suggestion_id IS NULL OR replacement_suggestion_id != suggestion_id",
            name="ck_today_interaction_replacement_distinct",
        ),
        Index("ix_today_interaction_suggestion_time", "suggestion_id", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    suggestion_id: Mapped[str] = mapped_column(
        ForeignKey("today_suggestions.id", ondelete="RESTRICT"), nullable=False
    )
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    interaction_type: Mapped[str] = mapped_column(String(32), nullable=False)
    prior_status: Mapped[str] = mapped_column(String(32), nullable=False)
    resulting_status: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[int] = mapped_column(Integer, nullable=False)
    actor: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    activity_id: Mapped[str | None] = mapped_column(
        ForeignKey("activities.id", ondelete="RESTRICT")
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("learning_sessions.id", ondelete="RESTRICT")
    )
    replacement_suggestion_id: Mapped[str | None] = mapped_column(
        ForeignKey("today_suggestions.id", ondelete="RESTRICT")
    )
    reason_code: Mapped[str | None] = mapped_column(String(128))
    structured_reason_json: Mapped[str] = mapped_column(Text, nullable=False)


class SuggestionActivityRelation(Base):
    __tablename__ = "suggestion_activity_relations"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_suggestion_relation_idempotency"),
        CheckConstraint(
            "relation_type IN ('matched','partially_matched','replaced')",
            name="ck_suggestion_activity_relation_type",
        ),
        CheckConstraint(
            "actor IN ('user','system')", name="ck_suggestion_activity_relation_actor"
        ),
        CheckConstraint("length(payload_hash) = 64", name="ck_suggestion_relation_payload_hash"),
        Index("ix_suggestion_relation_suggestion", "suggestion_id", "created_at"),
        Index("ix_suggestion_relation_activity", "activity_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    suggestion_id: Mapped[str] = mapped_column(
        ForeignKey("today_suggestions.id", ondelete="RESTRICT"), nullable=False
    )
    activity_id: Mapped[str] = mapped_column(
        ForeignKey("activities.id", ondelete="RESTRICT"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    automatic: Mapped[bool] = mapped_column(Boolean, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)


class TodayInteractionCorrection(Base):
    __tablename__ = "today_interaction_corrections"
    __table_args__ = (
        UniqueConstraint("interaction_id", name="uq_today_interaction_correction_target"),
        UniqueConstraint("idempotency_key", name="uq_today_interaction_correction_key"),
        CheckConstraint("length(payload_hash) = 64", name="ck_today_correction_payload_hash"),
        CheckConstraint("length(reason) > 0", name="ck_today_correction_reason"),
        CheckConstraint(
            "actor IN ('user','system')", name="ck_today_interaction_correction_actor"
        ),
        CheckConstraint(
            "resulting_status IN ('suggested','viewed','accepted','started','completed',"
            "'partially_completed','skipped','replaced','expired')",
            name="ck_today_correction_resulting_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    interaction_id: Mapped[str] = mapped_column(
        ForeignKey("today_interactions.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    resulting_status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    corrected_at: Mapped[int] = mapped_column(Integer, nullable=False)


class SuggestionActivityRelationCorrection(Base):
    __tablename__ = "suggestion_activity_relation_corrections"
    __table_args__ = (
        UniqueConstraint("relation_id", name="uq_suggestion_relation_correction_target"),
        UniqueConstraint("idempotency_key", name="uq_suggestion_relation_correction_key"),
        CheckConstraint(
            "correction_type IN ('retracted','replaced')",
            name="ck_suggestion_relation_correction_type",
        ),
        CheckConstraint(
            "(correction_type = 'retracted' AND replacement_relation_id IS NULL) OR "
            "(correction_type = 'replaced' AND replacement_relation_id IS NOT NULL)",
            name="ck_suggestion_relation_correction_shape",
        ),
        CheckConstraint(
            "replacement_relation_id IS NULL OR replacement_relation_id != relation_id",
            name="ck_suggestion_relation_correction_distinct",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    relation_id: Mapped[str] = mapped_column(
        ForeignKey("suggestion_activity_relations.id", ondelete="RESTRICT"), nullable=False
    )
    correction_type: Mapped[str] = mapped_column(String(16), nullable=False)
    replacement_relation_id: Mapped[str | None] = mapped_column(
        ForeignKey("suggestion_activity_relations.id", ondelete="RESTRICT")
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_at: Mapped[int] = mapped_column(Integer, nullable=False)


class TodaySuggestionCurrentState(Base):
    __tablename__ = "today_suggestion_current_states"
    __table_args__ = (
        CheckConstraint(
            "status IN ('suggested','viewed','accepted','started','completed',"
            "'partially_completed','skipped','replaced','expired')",
            name="ck_today_current_status",
        ),
        CheckConstraint("event_sequence >= 0", name="ck_today_current_sequence"),
        CheckConstraint(
            "(event_sequence = 0 AND latest_interaction_id IS NULL AND status = 'suggested') OR "
            "(event_sequence > 0 AND latest_interaction_id IS NOT NULL)",
            name="ck_today_current_interaction_shape",
        ),
    )

    suggestion_id: Mapped[str] = mapped_column(
        ForeignKey("today_suggestions.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    latest_interaction_id: Mapped[str | None] = mapped_column(
        ForeignKey("today_interactions.id", ondelete="RESTRICT")
    )
    terminal: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)


def _reject_today_history_mutation(*_args: object) -> None:
    raise ValueError("Today V2 history is immutable.")


for _model in (
    TodayGeneration,
    TodaySuggestion,
    TodayInteraction,
    TodayInteractionCorrection,
    SuggestionActivityRelation,
    SuggestionActivityRelationCorrection,
):
    event.listen(_model, "before_update", _reject_today_history_mutation)
    event.listen(_model, "before_delete", _reject_today_history_mutation)
