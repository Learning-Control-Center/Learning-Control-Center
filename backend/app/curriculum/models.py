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


class Curriculum(Base):
    __tablename__ = "curricula"
    __table_args__ = (
        UniqueConstraint("stable_key", name="uq_curriculum_stable_key"),
        CheckConstraint(
            "(retired_at IS NULL) = (retirement_reason IS NULL)",
            name="ck_curriculum_retirement_pair",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    creation_source: Mapped[str] = mapped_column(String(64), nullable=False)
    retired_at: Mapped[int | None] = mapped_column(Integer)
    retirement_reason: Mapped[str | None] = mapped_column(Text)


class CurriculumVersion(Base):
    __tablename__ = "curriculum_versions"
    __table_args__ = (
        UniqueConstraint("curriculum_id", "version", name="uq_curriculum_version"),
        CheckConstraint("version > 0", name="ck_curriculum_version_positive"),
        Index("ix_curriculum_version_supersedes", "supersedes_version_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    curriculum_id: Mapped[str] = mapped_column(
        ForeignKey("curricula.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    definition_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_at: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    creation_source: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT")
    )


class CurriculumObjectiveIdentity(Base):
    __tablename__ = "curriculum_objective_identities"
    __table_args__ = (
        UniqueConstraint("curriculum_id", "stable_key", name="uq_curriculum_objective_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    curriculum_id: Mapped[str] = mapped_column(
        ForeignKey("curricula.id", ondelete="RESTRICT"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)


class CurriculumObjectiveDefinition(Base):
    __tablename__ = "curriculum_objective_definitions"
    __table_args__ = (
        UniqueConstraint(
            "curriculum_version_id", "objective_identity_id", name="uq_curriculum_objective_def"
        ),
        UniqueConstraint(
            "curriculum_version_id", "order_index", name="uq_curriculum_objective_order"
        ),
        CheckConstraint("order_index >= 0", name="ck_curriculum_objective_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    curriculum_version_id: Mapped[str] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"), nullable=False
    )
    objective_identity_id: Mapped[str] = mapped_column(
        ForeignKey("curriculum_objective_identities.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)


class LearningUnitIdentity(Base):
    __tablename__ = "learning_unit_identities"
    __table_args__ = (UniqueConstraint("curriculum_id", "stable_key", name="uq_learning_unit_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    curriculum_id: Mapped[str] = mapped_column(
        ForeignKey("curricula.id", ondelete="RESTRICT"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)


class LearningUnitDefinition(Base):
    __tablename__ = "learning_unit_definitions"
    __table_args__ = (
        UniqueConstraint(
            "curriculum_version_id", "unit_identity_id", name="uq_learning_unit_definition"
        ),
        UniqueConstraint("curriculum_version_id", "order_index", name="uq_learning_unit_order"),
        CheckConstraint(
            "kind IN ('resource','exercise','practice_task','verification_template')",
            name="ck_learning_unit_kind",
        ),
        CheckConstraint("order_index >= 0", name="ck_learning_unit_order"),
        CheckConstraint("status IN ('active','archived')", name="ck_learning_unit_status"),
        CheckConstraint(
            "(minimum_useful_duration_ms IS NULL AND preferred_duration_ms IS NULL "
            "AND maximum_useful_duration_ms IS NULL) OR "
            "(minimum_useful_duration_ms > 0 AND preferred_duration_ms > 0 "
            "AND maximum_useful_duration_ms > 0 "
            "AND minimum_useful_duration_ms <= preferred_duration_ms "
            "AND preferred_duration_ms <= maximum_useful_duration_ms "
            "AND minimum_useful_duration_ms % 300000 = 0 "
            "AND preferred_duration_ms % 300000 = 0 "
            "AND maximum_useful_duration_ms % 300000 = 0)",
            name="ck_learning_unit_duration_range",
        ),
        Index("ix_learning_unit_identity", "unit_identity_id"),
        Index("ix_learning_unit_objective", "objective_identity_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    curriculum_version_id: Mapped[str] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"), nullable=False
    )
    unit_identity_id: Mapped[str] = mapped_column(
        ForeignKey("learning_unit_identities.id", ondelete="RESTRICT"), nullable=False
    )
    objective_identity_id: Mapped[str | None] = mapped_column(
        ForeignKey("curriculum_objective_identities.id", ondelete="RESTRICT")
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    action_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    provenance: Mapped[str] = mapped_column(String(64), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_useful_duration_ms: Mapped[int | None] = mapped_column(Integer)
    preferred_duration_ms: Mapped[int | None] = mapped_column(Integer)
    maximum_useful_duration_ms: Mapped[int | None] = mapped_column(Integer)


class LearningUnitTarget(Base):
    __tablename__ = "learning_unit_targets"
    __table_args__ = (
        UniqueConstraint("learning_unit_definition_id", "order_index", name="uq_unit_target_order"),
        CheckConstraint("order_index >= 0", name="ck_unit_target_order"),
        CheckConstraint("role IN ('primary','secondary','supporting')", name="ck_unit_target_role"),
        CheckConstraint("supports_unassessed IN (0,1)", name="ck_unit_target_supports_unassessed"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    learning_unit_definition_id: Mapped[str] = mapped_column(
        ForeignKey("learning_unit_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    semantic_definition_id: Mapped[str] = mapped_column(
        ForeignKey("semantic_competency_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    criterion_definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("criterion_definitions.id", ondelete="RESTRICT")
    )
    scale_version_id: Mapped[str] = mapped_column(
        ForeignKey("capability_scale_versions.id", ondelete="RESTRICT"), nullable=False
    )
    dimension_id: Mapped[str | None] = mapped_column(
        ForeignKey("capability_scale_dimensions.id", ondelete="RESTRICT")
    )
    intended_learning_outcome: Mapped[str] = mapped_column(Text, nullable=False)
    minimum_level_id: Mapped[str | None] = mapped_column(
        ForeignKey("capability_scale_levels.id", ondelete="RESTRICT")
    )
    maximum_level_id: Mapped[str | None] = mapped_column(
        ForeignKey("capability_scale_levels.id", ondelete="RESTRICT")
    )
    supports_unassessed: Mapped[bool] = mapped_column(default=False, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)


class LearningUnitRequirement(Base):
    __tablename__ = "learning_unit_requirements"
    __table_args__ = (
        UniqueConstraint(
            "learning_unit_definition_id", "stable_key", name="uq_unit_requirement_key"
        ),
        UniqueConstraint(
            "learning_unit_definition_id", "order_index", name="uq_unit_requirement_order"
        ),
        CheckConstraint(
            "requirement_type IN ('capability_at_least','criterion_demonstrated',"
            "'learning_unit_completed','resource_available','user_constraint')",
            name="ck_unit_requirement_type",
        ),
        CheckConstraint("effect IN ('hard','soft')", name="ck_unit_requirement_effect"),
        CheckConstraint(
            "scope IN ('learner','curriculum','environment','user')",
            name="ck_unit_requirement_scope",
        ),
        CheckConstraint("order_index >= 0", name="ck_unit_requirement_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    learning_unit_definition_id: Mapped[str] = mapped_column(
        ForeignKey("learning_unit_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    requirement_type: Mapped[str] = mapped_column(String(64), nullable=False)
    effect: Mapped[str] = mapped_column(String(16), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_json: Mapped[str] = mapped_column(Text, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)


class EvidenceOpportunityDefinition(Base):
    __tablename__ = "curriculum_evidence_opportunities"
    __table_args__ = (
        UniqueConstraint(
            "learning_unit_definition_id", "stable_key", name="uq_curriculum_opportunity_key"
        ),
        UniqueConstraint(
            "learning_unit_definition_id", "order_index", name="uq_curriculum_opportunity_order"
        ),
        CheckConstraint("order_index >= 0", name="ck_curriculum_opportunity_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    learning_unit_definition_id: Mapped[str] = mapped_column(
        ForeignKey("learning_unit_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    possible_characteristics_json: Mapped[str] = mapped_column(Text, nullable=False)
    required_characteristics_json: Mapped[str] = mapped_column(Text, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)


class AssessmentRubricIdentity(Base):
    __tablename__ = "assessment_rubric_identities"
    __table_args__ = (
        UniqueConstraint("curriculum_id", "stable_key", name="uq_assessment_rubric_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    curriculum_id: Mapped[str] = mapped_column(
        ForeignKey("curricula.id", ondelete="RESTRICT"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)


class AssessmentRubricDefinition(Base):
    __tablename__ = "assessment_rubric_definitions"
    __table_args__ = (
        UniqueConstraint(
            "curriculum_version_id", "rubric_identity_id", name="uq_assessment_rubric_definition"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    curriculum_version_id: Mapped[str] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"), nullable=False
    )
    rubric_identity_id: Mapped[str] = mapped_column(
        ForeignKey("assessment_rubric_identities.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    rubric_json: Mapped[str] = mapped_column(Text, nullable=False)
    semantic_definition_id: Mapped[str] = mapped_column(
        ForeignKey("semantic_competency_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    criterion_definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("criterion_definitions.id", ondelete="RESTRICT")
    )


class ActiveCurriculumVersionState(Base):
    __tablename__ = "active_curriculum_version_states"

    curriculum_id: Mapped[str] = mapped_column(
        ForeignKey("curricula.id", ondelete="RESTRICT"), primary_key=True
    )
    curriculum_version_id: Mapped[str] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    activated_at: Mapped[int] = mapped_column(Integer, nullable=False)


class CurriculumActivationEvent(Base):
    __tablename__ = "curriculum_activation_events"
    __table_args__ = (
        UniqueConstraint("curriculum_id", "event_sequence", name="uq_curriculum_activation_seq"),
        UniqueConstraint("idempotency_key", name="uq_curriculum_activation_idempotency"),
        CheckConstraint("event_sequence > 0", name="ck_curriculum_activation_sequence"),
        Index(
            "ix_curriculum_activation_cutoff",
            "curriculum_id",
            "activated_at",
            "event_sequence",
        ),
        Index("ix_curriculum_activation_to_version", "to_curriculum_version_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    curriculum_id: Mapped[str] = mapped_column(
        ForeignKey("curricula.id", ondelete="RESTRICT"), nullable=False
    )
    from_curriculum_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT")
    )
    to_curriculum_version_id: Mapped[str] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"), nullable=False
    )
    activated_at: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


class ActivityCurriculumUnitLink(Base):
    __tablename__ = "activity_curriculum_unit_links"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_activity_curriculum_link_idempotency"),
        CheckConstraint(
            "provenance IN ('user_selected','user_confirmed','imported_asserted')",
            name="ck_activity_curriculum_link_provenance",
        ),
        Index("ix_activity_curriculum_link_activity", "activity_id", "created_at"),
        Index("ix_activity_curriculum_link_unit", "learning_unit_definition_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    activity_id: Mapped[str] = mapped_column(
        ForeignKey("activities.id", ondelete="RESTRICT"), nullable=False
    )
    learning_unit_definition_id: Mapped[str] = mapped_column(
        ForeignKey("learning_unit_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    provenance: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


class ActivityCurriculumLinkCorrection(Base):
    __tablename__ = "activity_curriculum_link_corrections"
    __table_args__ = (
        UniqueConstraint("activity_curriculum_unit_link_id", name="uq_curriculum_link_correction"),
        UniqueConstraint("idempotency_key", name="uq_curriculum_link_correction_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    activity_curriculum_unit_link_id: Mapped[str] = mapped_column(
        ForeignKey("activity_curriculum_unit_links.id", ondelete="RESTRICT"), nullable=False
    )
    replacement_link_id: Mapped[str | None] = mapped_column(
        ForeignKey("activity_curriculum_unit_links.id", ondelete="RESTRICT")
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_at: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


def _reject_curriculum_history_mutation(*_args: object) -> None:
    raise ValueError("Canonical Curriculum definitions and history are immutable.")


for _immutable_model in (
    CurriculumVersion,
    CurriculumObjectiveDefinition,
    LearningUnitDefinition,
    LearningUnitTarget,
    LearningUnitRequirement,
    EvidenceOpportunityDefinition,
    AssessmentRubricDefinition,
    CurriculumActivationEvent,
    ActivityCurriculumUnitLink,
    ActivityCurriculumLinkCorrection,
):
    event.listen(_immutable_model, "before_update", _reject_curriculum_history_mutation)
