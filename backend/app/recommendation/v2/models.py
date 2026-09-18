from __future__ import annotations

from sqlalchemy import (
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


class RecommendationV2Run(Base):
    __tablename__ = "recommendation_v2_runs"
    __table_args__ = (
        CheckConstraint("status IN ('completed','failed')", name="ck_recommendation_v2_run_status"),
        CheckConstraint(
            "available_time_ms IS NULL OR available_time_ms >= 0",
            name="ck_recommendation_v2_available_time",
        ),
        CheckConstraint(
            "(status = 'completed' AND output_hash IS NOT NULL "
            "AND failure_metadata_json IS NULL) OR "
            "(status = 'failed' AND output_hash IS NULL "
            "AND failure_metadata_json IS NOT NULL)",
            name="ck_recommendation_v2_run_outcome",
        ),
        CheckConstraint(
            "completeness IN ('complete','partial')",
            name="ck_recommendation_v2_run_completeness",
        ),
        CheckConstraint(
            "length(policy_bundle_hash) = 64 AND length(input_hash) = 64 "
            "AND (output_hash IS NULL OR length(output_hash) = 64) "
            "AND length(user_constraints_hash) = 64",
            name="ck_recommendation_v2_run_hashes",
        ),
        Index("ix_recommendation_v2_run_generated", "generated_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    analysis_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    generated_at: Mapped[int] = mapped_column(Integer, nullable=False)
    cutoff_at: Mapped[int] = mapped_column(Integer, nullable=False)
    local_date: Mapped[str] = mapped_column(String(10), nullable=False)
    available_time_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_registry_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_bundle_json: Mapped[str] = mapped_column(Text, nullable=False)
    policy_bundle_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen_input_json: Mapped[str] = mapped_column(Text, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str | None] = mapped_column(String(64))
    target_profile_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("target_profile_versions.id", ondelete="RESTRICT")
    )
    learning_graph_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("learning_graph_versions.id", ondelete="RESTRICT")
    )
    curriculum_reference: Mapped[str | None] = mapped_column(String(64))
    project_reference: Mapped[str | None] = mapped_column(String(64))
    user_constraints_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    semantic_definition_references_json: Mapped[str] = mapped_column(Text, nullable=False)
    capability_scale_version_references_json: Mapped[str] = mapped_column(Text, nullable=False)
    completeness: Mapped[str] = mapped_column(String(16), nullable=False)
    application_version: Mapped[str] = mapped_column(String(32), nullable=False)
    replay_of_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("recommendation_v2_runs.id", ondelete="RESTRICT")
    )
    failure_metadata_json: Mapped[str | None] = mapped_column(Text)


class RecommendationV2Candidate(Base):
    __tablename__ = "recommendation_v2_candidates"
    __table_args__ = (
        UniqueConstraint("run_id", "ordinal", name="uq_recommendation_v2_candidate_ordinal"),
        UniqueConstraint("run_id", "stable_id", name="uq_recommendation_v2_candidate_stable"),
        CheckConstraint("ordinal >= 0", name="ck_recommendation_v2_candidate_ordinal"),
        CheckConstraint(
            "candidate_type IN ('curriculum_unit','practice_task','verification','assessment',"
            "'review','maintenance','project_task','unblock_task')",
            name="ck_recommendation_v2_candidate_type",
        ),
        CheckConstraint(
            "(duration_minimum_ms IS NULL AND duration_preferred_ms IS NULL "
            "AND duration_maximum_ms IS NULL) OR "
            "(duration_minimum_ms > 0 AND duration_minimum_ms <= duration_preferred_ms "
            "AND duration_preferred_ms <= duration_maximum_ms)",
            name="ck_recommendation_v2_candidate_duration",
        ),
        Index("ix_recommendation_v2_candidate_run_type", "run_id", "candidate_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("recommendation_v2_runs.id", ondelete="RESTRICT"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    stable_id: Mapped[str] = mapped_column(String(512), nullable=False)
    candidate_key: Mapped[str] = mapped_column(String(512), nullable=False)
    stable_tie_key: Mapped[str] = mapped_column(String(512), nullable=False)
    candidate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_version_id: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    target_identity_id: Mapped[str | None] = mapped_column(String(36))
    served_target_identity_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    primary_need_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    primary_need_identity: Mapped[str] = mapped_column(String(512), nullable=False)
    competency_identity_id: Mapped[str | None] = mapped_column(String(36))
    criterion_definition_id: Mapped[str | None] = mapped_column(String(36))
    project_id: Mapped[str | None] = mapped_column(String(36))
    duration_minimum_ms: Mapped[int | None] = mapped_column(Integer)
    duration_preferred_ms: Mapped[int | None] = mapped_column(Integer)
    duration_maximum_ms: Mapped[int | None] = mapped_column(Integer)
    candidate_json: Mapped[str] = mapped_column(Text, nullable=False)


class RecommendationV2EligibilityDecision(Base):
    __tablename__ = "recommendation_v2_eligibility_decisions"

    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("recommendation_v2_candidates.id", ondelete="RESTRICT"), primary_key=True
    )
    eligible: Mapped[bool] = mapped_column(nullable=False)
    reason_code: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)


class RecommendationV2EligibilityRuleResult(Base):
    __tablename__ = "recommendation_v2_eligibility_rule_results"
    __table_args__ = (
        UniqueConstraint("candidate_id", "ordinal", name="uq_recommendation_v2_rule_ordinal"),
        UniqueConstraint("candidate_id", "rule_code", name="uq_recommendation_v2_rule_code"),
        CheckConstraint("ordinal >= 0", name="ck_recommendation_v2_rule_ordinal"),
        CheckConstraint(
            "outcome IN ('pass','fail','unknown','not_applicable')",
            name="ck_recommendation_v2_rule_outcome",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("recommendation_v2_candidates.id", ondelete="RESTRICT"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_code: Mapped[str] = mapped_column(String(128), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    decisive: Mapped[bool] = mapped_column(nullable=False)
    facts_json: Mapped[str] = mapped_column(Text, nullable=False)
    subject_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)


class RecommendationV2ExpectedValue(Base):
    __tablename__ = "recommendation_v2_expected_values"
    __table_args__ = (
        CheckConstraint(
            "value IN ('unknown','low','normal','high','very_high')",
            name="ck_recommendation_v2_elv",
        ),
    )

    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("recommendation_v2_candidates.id", ondelete="RESTRICT"), primary_key=True
    )
    value: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_codes_json: Mapped[str] = mapped_column(Text, nullable=False)
    matched_facts_json: Mapped[str] = mapped_column(Text, nullable=False)
    deciding_rule_code: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)


class RecommendationV2ScoreComponent(Base):
    __tablename__ = "recommendation_v2_score_components"
    __table_args__ = (
        UniqueConstraint("candidate_id", "component_code", name="uq_recommendation_v2_component"),
        CheckConstraint("value >= -8 AND value <= 24", name="ck_recommendation_v2_component_value"),
        CheckConstraint(
            "component_code IN ('TARGET_PRIORITY','PRIMARY_NEED','DEADLINE_PRESSURE',"
            "'ALLOCATION_BALANCE','NEGLECT_OR_STALL','EXPECTED_LEARNING_VALUE','CONTEXT_COST')",
            name="ck_recommendation_v2_component_code",
        ),
        CheckConstraint(
            "(component_code = 'TARGET_PRIORITY' AND value BETWEEN 2 AND 20) OR "
            "(component_code = 'PRIMARY_NEED' AND value BETWEEN 0 AND 24) OR "
            "(component_code = 'DEADLINE_PRESSURE' AND value BETWEEN 0 AND 12) OR "
            "(component_code = 'ALLOCATION_BALANCE' AND value BETWEEN -8 AND 8) OR "
            "(component_code = 'NEGLECT_OR_STALL' AND value BETWEEN 0 AND 10) OR "
            "(component_code = 'EXPECTED_LEARNING_VALUE' AND value BETWEEN 0 AND 12) OR "
            "(component_code = 'CONTEXT_COST' AND value BETWEEN -4 AND 0)",
            name="ck_recommendation_v2_component_code_value",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("recommendation_v2_candidates.id", ondelete="RESTRICT"), nullable=False
    )
    component_code: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    allowed_minimum: Mapped[int] = mapped_column(Integer, nullable=False)
    allowed_maximum: Mapped[int] = mapped_column(Integer, nullable=False)
    decisive_facts_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)


class RecommendationV2SelectionDecision(Base):
    __tablename__ = "recommendation_v2_selection_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('selected','not_selected','ineligible')",
            name="ck_recommendation_v2_selection_decision",
        ),
        CheckConstraint(
            "portfolio_role IS NULL OR portfolio_role IN ('primary','complementary','maintenance')",
            name="ck_recommendation_v2_portfolio_role",
        ),
        CheckConstraint(
            "(decision = 'selected' AND portfolio_role IS NOT NULL AND rank_ordinal > 0 "
            "AND score_total IS NOT NULL AND admission_ordinal > 0) OR "
            "(decision = 'not_selected' AND portfolio_role IS NULL AND rank_ordinal > 0 "
            "AND score_total IS NOT NULL AND advisory_duration_ms IS NULL "
            "AND admission_ordinal IS NULL) OR "
            "(decision = 'ineligible' AND portfolio_role IS NULL AND rank_ordinal IS NULL "
            "AND score_total IS NULL AND advisory_duration_ms IS NULL "
            "AND admission_ordinal IS NULL)",
            name="ck_recommendation_v2_selection_shape",
        ),
        CheckConstraint(
            "score_total IS NULL OR score_total BETWEEN -10 AND 86",
            name="ck_recommendation_v2_selection_score",
        ),
    )

    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("recommendation_v2_candidates.id", ondelete="RESTRICT"), primary_key=True
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    portfolio_role: Mapped[str | None] = mapped_column(String(16))
    reason_code: Mapped[str] = mapped_column(String(128), nullable=False)
    rank_ordinal: Mapped[int | None] = mapped_column(Integer)
    score_total: Mapped[int | None] = mapped_column(Integer)
    advisory_duration_ms: Mapped[int | None] = mapped_column(Integer)
    duration_reason_code: Mapped[str | None] = mapped_column(String(128))
    admission_ordinal: Mapped[int | None] = mapped_column(Integer)
    displaced_by_candidate_stable_id: Mapped[str | None] = mapped_column(String(512))
    decisive_facts_json: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)


class RecommendationV2Recommendation(Base):
    __tablename__ = "recommendation_v2_recommendations"
    __table_args__ = (
        UniqueConstraint("run_id", "portfolio_role", name="uq_recommendation_v2_run_role"),
        CheckConstraint(
            "portfolio_role IN ('primary','complementary','maintenance')",
            name="ck_recommendation_v2_recommendation_role",
        ),
        CheckConstraint("rank_ordinal > 0", name="ck_recommendation_v2_recommendation_rank"),
        CheckConstraint(
            "score_total BETWEEN -10 AND 86",
            name="ck_recommendation_v2_recommendation_score",
        ),
        CheckConstraint(
            "(duration_minimum_ms IS NULL AND duration_preferred_ms IS NULL "
            "AND duration_maximum_ms IS NULL) OR "
            "(duration_minimum_ms > 0 AND duration_minimum_ms <= duration_preferred_ms "
            "AND duration_preferred_ms <= duration_maximum_ms)",
            name="ck_recommendation_v2_recommendation_duration",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("recommendation_v2_runs.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("recommendation_v2_candidates.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    portfolio_role: Mapped[str] = mapped_column(String(16), nullable=False)
    advisory_duration_ms: Mapped[int | None] = mapped_column(Integer)
    duration_minimum_ms: Mapped[int | None] = mapped_column(Integer)
    duration_preferred_ms: Mapped[int | None] = mapped_column(Integer)
    duration_maximum_ms: Mapped[int | None] = mapped_column(Integer)
    rank_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    score_total: Mapped[int] = mapped_column(Integer, nullable=False)
    score_breakdown_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    selection_reason_code: Mapped[str] = mapped_column(String(128), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    presentation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_summary: Mapped[str] = mapped_column(Text, nullable=False)


class RecommendationV2Reason(Base):
    __tablename__ = "recommendation_v2_reasons"
    __table_args__ = (
        UniqueConstraint("candidate_id", "ordinal", name="uq_recommendation_v2_reason_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_recommendation_v2_reason_ordinal"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("recommendation_v2_candidates.id", ondelete="RESTRICT"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(128), nullable=False)
    explanation_facts_json: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    score_contribution: Mapped[int | None] = mapped_column(Integer)
    template_key: Mapped[str] = mapped_column(String(128), nullable=False)
    template_version: Mapped[str] = mapped_column(String(64), nullable=False)
    rendered_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)


def _reject_recommendation_history_mutation(*_args: object) -> None:
    raise ValueError("Recommendation V2 history is immutable.")


for _model in (
    RecommendationV2Run,
    RecommendationV2Candidate,
    RecommendationV2EligibilityDecision,
    RecommendationV2EligibilityRuleResult,
    RecommendationV2ExpectedValue,
    RecommendationV2ScoreComponent,
    RecommendationV2SelectionDecision,
    RecommendationV2Recommendation,
    RecommendationV2Reason,
):
    event.listen(_model, "before_update", _reject_recommendation_history_mutation)
    event.listen(_model, "before_delete", _reject_recommendation_history_mutation)
