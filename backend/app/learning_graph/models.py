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
from app.models import new_id
from app.time_utils import utc_now_ms


class LearningGraph(Base):
    __tablename__ = "learning_graphs"
    __table_args__ = (UniqueConstraint("stable_key", name="uq_learning_graph_stable_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    creation_source: Mapped[str] = mapped_column(String(64), nullable=False)


class LearningGraphVersion(Base):
    __tablename__ = "learning_graph_versions"
    __table_args__ = (
        UniqueConstraint("learning_graph_id", "version", name="uq_learning_graph_version"),
        CheckConstraint("version > 0", name="ck_learning_graph_version_positive"),
        Index("ix_learning_graph_version_supersedes", "supersedes_version_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    learning_graph_id: Mapped[str] = mapped_column(
        ForeignKey("learning_graphs.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    satisfaction_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    definition_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_at: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    creation_source: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("learning_graph_versions.id", ondelete="RESTRICT")
    )


class CompetencyEdgeIdentity(Base):
    __tablename__ = "competency_edge_identities"
    __table_args__ = (
        UniqueConstraint("learning_graph_id", "stable_key", name="uq_competency_edge_key"),
        Index("ix_competency_edge_identity_source", "source_competency_identity_id"),
        Index("ix_competency_edge_identity_target", "target_competency_identity_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    learning_graph_id: Mapped[str] = mapped_column(
        ForeignKey("learning_graphs.id", ondelete="RESTRICT"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    edge_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_competency_identity_id: Mapped[str] = mapped_column(
        ForeignKey("competency_identities.id", ondelete="RESTRICT"), nullable=False
    )
    target_competency_identity_id: Mapped[str] = mapped_column(
        ForeignKey("competency_identities.id", ondelete="RESTRICT"), nullable=False
    )
    meaning_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)


class CompetencyEdgeDefinition(Base):
    __tablename__ = "competency_edge_definitions"
    __table_args__ = (
        UniqueConstraint(
            "learning_graph_version_id", "edge_identity_id", name="uq_competency_edge_definition"
        ),
        UniqueConstraint(
            "learning_graph_version_id",
            "edge_type",
            "source_competency_identity_id",
            "target_competency_identity_id",
            "satisfaction_scope_key",
            name="uq_competency_edge_semantics",
        ),
        UniqueConstraint(
            "learning_graph_version_id", "order_index", name="uq_competency_edge_order"
        ),
        CheckConstraint(
            "edge_type IN ('prerequisite','recommended_before','supports',"
            "'specialization','related')",
            name="ck_competency_edge_type",
        ),
        CheckConstraint("order_index >= 0", name="ck_competency_edge_order"),
        CheckConstraint(
            "requirement_kind IS NULL OR requirement_kind IN "
            "('capability_at_least','criterion_set_demonstrated')",
            name="ck_competency_edge_requirement_kind",
        ),
        CheckConstraint(
            "edge_type != 'prerequisite' OR requirement_kind IS NOT NULL",
            name="ck_native_prerequisite_requirement",
        ),
        Index("ix_competency_edge_source", "source_competency_identity_id"),
        Index("ix_competency_edge_target", "target_competency_identity_id"),
        Index("ix_competency_edge_definition_identity", "edge_identity_id"),
        Index("ix_competency_edge_source_definition", "source_semantic_definition_id"),
        Index("ix_competency_edge_target_definition", "target_semantic_definition_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    learning_graph_version_id: Mapped[str] = mapped_column(
        ForeignKey("learning_graph_versions.id", ondelete="RESTRICT"), nullable=False
    )
    edge_identity_id: Mapped[str] = mapped_column(
        ForeignKey("competency_edge_identities.id", ondelete="RESTRICT"), nullable=False
    )
    edge_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_competency_identity_id: Mapped[str] = mapped_column(
        ForeignKey("competency_identities.id", ondelete="RESTRICT"), nullable=False
    )
    target_competency_identity_id: Mapped[str] = mapped_column(
        ForeignKey("competency_identities.id", ondelete="RESTRICT"), nullable=False
    )
    source_semantic_definition_id: Mapped[str] = mapped_column(
        ForeignKey("semantic_competency_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    target_semantic_definition_id: Mapped[str] = mapped_column(
        ForeignKey("semantic_competency_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    satisfaction_scope_key: Mapped[str] = mapped_column(String(255), nullable=False)
    requirement_kind: Mapped[str | None] = mapped_column(String(40))
    requirement_json: Mapped[str] = mapped_column(Text, nullable=False)
    provenance: Mapped[str] = mapped_column(String(64), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)


class ActiveLearningGraphState(Base):
    __tablename__ = "active_learning_graph_states"
    __table_args__ = (CheckConstraint("id = 1", name="ck_active_learning_graph_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    learning_graph_id: Mapped[str] = mapped_column(
        ForeignKey("learning_graphs.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    learning_graph_version_id: Mapped[str] = mapped_column(
        ForeignKey("learning_graph_versions.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    activated_at: Mapped[int] = mapped_column(Integer, nullable=False)


class LearningGraphActivationEvent(Base):
    __tablename__ = "learning_graph_activation_events"
    __table_args__ = (
        UniqueConstraint("event_sequence", name="uq_graph_activation_seq"),
        UniqueConstraint("idempotency_key", name="uq_graph_activation_idempotency"),
        CheckConstraint("event_sequence > 0", name="ck_graph_activation_sequence"),
        Index("ix_graph_activation_cutoff", "activated_at", "event_sequence"),
        Index("ix_graph_activation_from", "from_learning_graph_version_id"),
        Index("ix_graph_activation_to", "to_learning_graph_version_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    learning_graph_id: Mapped[str] = mapped_column(
        ForeignKey("learning_graphs.id", ondelete="RESTRICT"), nullable=False
    )
    from_learning_graph_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("learning_graph_versions.id", ondelete="RESTRICT")
    )
    to_learning_graph_version_id: Mapped[str] = mapped_column(
        ForeignKey("learning_graph_versions.id", ondelete="RESTRICT"), nullable=False
    )
    activated_at: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


def _reject_graph_history_mutation(*_args: object) -> None:
    raise ValueError("Canonical Learning Graph definitions and history are immutable.")


for _immutable_model in (
    LearningGraph,
    LearningGraphVersion,
    CompetencyEdgeIdentity,
    CompetencyEdgeDefinition,
    LearningGraphActivationEvent,
):
    event.listen(_immutable_model, "before_update", _reject_graph_history_mutation)
    event.listen(_immutable_model, "before_delete", _reject_graph_history_mutation)
