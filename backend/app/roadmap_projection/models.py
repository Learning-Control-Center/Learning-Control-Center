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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models import new_id
from app.time_utils import utc_now_ms


class LegacyRoadmapActiveState(Base):
    __tablename__ = "legacy_roadmap_active_states"
    __table_args__ = (
        Index(
            "uq_legacy_one_current_roadmap",
            "is_current",
            unique=True,
            sqlite_where=text("is_current = 1"),
        ),
        Index("ix_legacy_roadmap_active_version", "active_version_id"),
        Index("ix_legacy_roadmap_current_phase", "current_phase_id"),
    )

    roadmap_id: Mapped[str] = mapped_column(
        ForeignKey("roadmaps.id", ondelete="RESTRICT"), primary_key=True
    )
    active_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("roadmap_versions.id", ondelete="RESTRICT")
    )
    current_phase_id: Mapped[str | None] = mapped_column(
        ForeignKey("phases.id", ondelete="RESTRICT")
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)


class RoadmapNodePositionOverride(Base):
    __tablename__ = "roadmap_node_position_overrides"
    __table_args__ = (
        UniqueConstraint("scope_key", "node_key", name="uq_roadmap_v2_position_scope_node"),
        CheckConstraint("position_x BETWEEN -1000000 AND 1000000", name="ck_roadmap_v2_x"),
        CheckConstraint("position_y BETWEEN -1000000 AND 1000000", name="ck_roadmap_v2_y"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scope_key: Mapped[str] = mapped_column(String(255), nullable=False)
    node_key: Mapped[str] = mapped_column(String(255), nullable=False)
    position_x: Mapped[int] = mapped_column(Integer, nullable=False)
    position_y: Mapped[int] = mapped_column(Integer, nullable=False)
    provenance: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)


class RoadmapProjectionPreference(Base):
    __tablename__ = "roadmap_projection_preferences"

    scope_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    show_prerequisites: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    show_recommended_before: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    show_supports: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    show_related: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)


class RoadmapProjectionCache(Base):
    __tablename__ = "roadmap_projection_caches"

    scope_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    projection_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    layout_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_lineage_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_json: Mapped[str] = mapped_column(Text, nullable=False)
    output_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    built_at: Mapped[int] = mapped_column(Integer, nullable=False)


class RoadmapProjectionCheckpoint(Base):
    __tablename__ = "roadmap_projection_checkpoints"

    scope_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    source_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_bundle_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rebuilt_at: Mapped[int] = mapped_column(Integer, nullable=False)
