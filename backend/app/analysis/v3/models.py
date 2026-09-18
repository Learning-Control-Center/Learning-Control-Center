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
    update,
)
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models import ProjectionInvalidation


class AnalysisV3RunLineage(Base):
    __tablename__ = "analysis_v3_run_lineages"
    __table_args__ = (
        CheckConstraint("source_generation >= 0", name="ck_analysis_v3_source_generation"),
    )

    run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="RESTRICT"), primary_key=True
    )
    analysis_algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    normalization_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    analyzer_bundle_json: Mapped[str] = mapped_column(Text, nullable=False)
    policy_bundle_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    replay_of_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="RESTRICT")
    )


class AnalysisV3SnapshotDetail(Base):
    __tablename__ = "analysis_v3_snapshot_details"

    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_snapshots.id", ondelete="RESTRICT"), primary_key=True
    )
    purpose_matrix_version: Mapped[str] = mapped_column(String(64), nullable=False)
    facts_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    gaps_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    signals_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    unknowns_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class AnalysisV3NormalizedFact(Base):
    __tablename__ = "analysis_v3_normalized_facts"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "ordinal", name="uq_analysis_v3_fact_ordinal"),
        UniqueConstraint("snapshot_id", "stable_key", name="uq_analysis_v3_fact_key"),
        CheckConstraint("ordinal >= 0", name="ck_analysis_v3_fact_ordinal"),
        Index("ix_analysis_v3_fact_type", "snapshot_id", "fact_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    stable_key: Mapped[str] = mapped_column(String(512), nullable=False)
    fact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class AnalysisV3CompetencyGap(Base):
    __tablename__ = "analysis_v3_competency_gaps"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "ordinal", name="uq_analysis_v3_gap_ordinal"),
        UniqueConstraint("snapshot_id", "stable_key", name="uq_analysis_v3_gap_key"),
        CheckConstraint("ordinal >= 0", name="ck_analysis_v3_gap_ordinal"),
        CheckConstraint(
            "severity IN ('unknown','none','low','medium','high','critical')",
            name="ck_analysis_v3_gap_severity",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    stable_key: Mapped[str] = mapped_column(String(512), nullable=False)
    competency_identity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    dimension_key: Mapped[str | None] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    comparison_status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    input_lineage_json: Mapped[str] = mapped_column(Text, nullable=False)


class AnalysisV3Signal(Base):
    __tablename__ = "analysis_v3_signals"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "ordinal", name="uq_analysis_v3_signal_ordinal"),
        UniqueConstraint("snapshot_id", "stable_key", name="uq_analysis_v3_signal_key"),
        CheckConstraint("ordinal >= 0", name="ck_analysis_v3_signal_ordinal"),
        CheckConstraint(
            "severity IN ('info','attention','high','critical')",
            name="ck_analysis_v3_signal_severity",
        ),
        Index("ix_analysis_v3_signal_type", "snapshot_id", "signal_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    stable_key: Mapped[str] = mapped_column(String(512), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(255), nullable=False)
    dimension_key: Mapped[str | None] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_codes_json: Mapped[str] = mapped_column(Text, nullable=False)
    decisive_facts_json: Mapped[str] = mapped_column(Text, nullable=False)
    analyzer_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_cutoff_at: Mapped[int] = mapped_column(Integer, nullable=False)


class AnalysisV3UnknownMarker(Base):
    __tablename__ = "analysis_v3_unknown_markers"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "ordinal", name="uq_analysis_v3_unknown_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_analysis_v3_unknown_ordinal"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    field_path: Mapped[str] = mapped_column(String(512), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(255), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(128), nullable=False)


class AnalysisV3CurrentState(Base):
    __tablename__ = "analysis_v3_current_states"
    __table_args__ = (
        CheckConstraint(
            "status IN ('current','stale','pending','failed')", name="ck_analysis_v3_current_status"
        ),
        CheckConstraint("source_generation >= 0", name="ck_analysis_v3_current_generation"),
    )

    scope_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="RESTRICT"), nullable=False
    )
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    exclusive_cutoff_at: Mapped[int] = mapped_column(Integer, nullable=False)
    source_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_bundle_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)


def _reject_analysis_v3_history_mutation(*_args: object) -> None:
    raise ValueError("Analysis V3 history is immutable.")


for _immutable_model in (
    AnalysisV3RunLineage,
    AnalysisV3SnapshotDetail,
    AnalysisV3NormalizedFact,
    AnalysisV3CompetencyGap,
    AnalysisV3Signal,
    AnalysisV3UnknownMarker,
):
    event.listen(_immutable_model, "before_update", _reject_analysis_v3_history_mutation)
    event.listen(_immutable_model, "before_delete", _reject_analysis_v3_history_mutation)


@event.listens_for(ProjectionInvalidation, "after_insert")
def _mark_analysis_v3_current_stale(
    _mapper: object, connection: Connection, target: ProjectionInvalidation
) -> None:
    if (
        target.projection_kind == "analysis"
        and target.target_policy_version == "analysis-policy/v3.0"
    ):
        connection.execute(update(AnalysisV3CurrentState).values(status="stale"))
