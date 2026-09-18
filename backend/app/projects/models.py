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
from app.models import new_id
from app.time_utils import utc_now_ms


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("stable_key", name="uq_project_stable_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    creation_source: Mapped[str] = mapped_column(String(64), nullable=False)


class ProjectVersion(Base):
    __tablename__ = "project_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_project_version"),
        CheckConstraint("version > 0", name="ck_project_version_positive"),
        Index("ix_project_version_supersedes", "supersedes_version_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
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
        ForeignKey("project_versions.id", ondelete="RESTRICT")
    )


class _ProjectIdentityMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)


class ProjectGoalIdentity(_ProjectIdentityMixin, Base):
    __tablename__ = "project_goal_identities"
    __table_args__ = (UniqueConstraint("project_id", "stable_key", name="uq_project_goal_key"),)


class ProjectTaskIdentity(_ProjectIdentityMixin, Base):
    __tablename__ = "project_task_identities"
    __table_args__ = (UniqueConstraint("project_id", "stable_key", name="uq_project_task_key"),)


class ProjectCriterionIdentity(_ProjectIdentityMixin, Base):
    __tablename__ = "project_criterion_identities"
    __table_args__ = (
        UniqueConstraint("project_id", "stable_key", name="uq_project_criterion_key"),
    )


class ProjectMilestoneIdentity(_ProjectIdentityMixin, Base):
    __tablename__ = "project_milestone_identities"
    __table_args__ = (
        UniqueConstraint("project_id", "stable_key", name="uq_project_milestone_key"),
    )


class ProjectGoalDefinition(Base):
    __tablename__ = "project_goal_definitions"
    __table_args__ = (
        UniqueConstraint("project_version_id", "goal_identity_id", name="uq_project_goal_def"),
        UniqueConstraint("project_version_id", "order_index", name="uq_project_goal_order"),
        CheckConstraint("order_index >= 0", name="ck_project_goal_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_version_id: Mapped[str] = mapped_column(
        ForeignKey("project_versions.id", ondelete="RESTRICT"), nullable=False
    )
    goal_identity_id: Mapped[str] = mapped_column(
        ForeignKey("project_goal_identities.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)


class ProjectMilestoneDefinition(Base):
    __tablename__ = "project_milestone_definitions"
    __table_args__ = (
        UniqueConstraint(
            "project_version_id", "milestone_identity_id", name="uq_project_milestone_def"
        ),
        UniqueConstraint("project_version_id", "order_index", name="uq_project_milestone_order"),
        CheckConstraint("order_index >= 0", name="ck_project_milestone_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_version_id: Mapped[str] = mapped_column(
        ForeignKey("project_versions.id", ondelete="RESTRICT"), nullable=False
    )
    milestone_identity_id: Mapped[str] = mapped_column(
        ForeignKey("project_milestone_identities.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)


class ProjectTaskDefinition(Base):
    __tablename__ = "project_task_definitions"
    __table_args__ = (
        UniqueConstraint("project_version_id", "task_identity_id", name="uq_project_task_def"),
        UniqueConstraint("project_version_id", "order_index", name="uq_project_task_order"),
        CheckConstraint("status IN ('active','archived')", name="ck_project_task_status"),
        CheckConstraint("order_index >= 0", name="ck_project_task_order"),
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
            name="ck_project_task_duration_range",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_version_id: Mapped[str] = mapped_column(
        ForeignKey("project_versions.id", ondelete="RESTRICT"), nullable=False
    )
    task_identity_id: Mapped[str] = mapped_column(
        ForeignKey("project_task_identities.id", ondelete="RESTRICT"), nullable=False
    )
    milestone_identity_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_milestone_identities.id", ondelete="RESTRICT")
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_useful_duration_ms: Mapped[int | None] = mapped_column(Integer)
    preferred_duration_ms: Mapped[int | None] = mapped_column(Integer)
    maximum_useful_duration_ms: Mapped[int | None] = mapped_column(Integer)


class ProjectCriterionDefinition(Base):
    __tablename__ = "project_criterion_definitions"
    __table_args__ = (
        UniqueConstraint(
            "project_version_id", "criterion_identity_id", name="uq_project_criterion_def"
        ),
        UniqueConstraint("project_version_id", "order_index", name="uq_project_criterion_order"),
        CheckConstraint("order_index >= 0", name="ck_project_criterion_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_version_id: Mapped[str] = mapped_column(
        ForeignKey("project_versions.id", ondelete="RESTRICT"), nullable=False
    )
    criterion_identity_id: Mapped[str] = mapped_column(
        ForeignKey("project_criterion_identities.id", ondelete="RESTRICT"), nullable=False
    )
    milestone_identity_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_milestone_identities.id", ondelete="RESTRICT")
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evaluation_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)


class ProjectTarget(Base):
    __tablename__ = "project_targets"
    __table_args__ = (
        UniqueConstraint("project_version_id", "order_index", name="uq_project_target_order"),
        CheckConstraint(
            "role IN ('primary','secondary','supporting')", name="ck_project_target_role"
        ),
        CheckConstraint("order_index >= 0", name="ck_project_target_order"),
        CheckConstraint(
            "NOT (task_definition_id IS NOT NULL AND project_criterion_definition_id IS NOT NULL)",
            name="ck_project_target_owner",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_version_id: Mapped[str] = mapped_column(
        ForeignKey("project_versions.id", ondelete="RESTRICT"), nullable=False
    )
    task_definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_task_definitions.id", ondelete="RESTRICT")
    )
    project_criterion_definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_criterion_definitions.id", ondelete="RESTRICT")
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
    level_id: Mapped[str | None] = mapped_column(
        ForeignKey("capability_scale_levels.id", ondelete="RESTRICT")
    )
    intended_outcome: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)


class ProjectRequirement(Base):
    __tablename__ = "project_requirements"
    __table_args__ = (
        UniqueConstraint("project_version_id", "stable_key", name="uq_project_requirement_key"),
        UniqueConstraint("project_version_id", "order_index", name="uq_project_requirement_order"),
        CheckConstraint(
            "requirement_type IN ('capability_at_least','criterion_demonstrated',"
            "'project_criterion_demonstrated','project_task_completed','resource_available',"
            "'user_constraint')",
            name="ck_project_requirement_type",
        ),
        CheckConstraint("effect IN ('hard','soft')", name="ck_project_requirement_effect"),
        CheckConstraint(
            "scope IN ('learner','project','environment','user')",
            name="ck_project_requirement_scope",
        ),
        CheckConstraint("order_index >= 0", name="ck_project_requirement_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_version_id: Mapped[str] = mapped_column(
        ForeignKey("project_versions.id", ondelete="RESTRICT"), nullable=False
    )
    task_definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_task_definitions.id", ondelete="RESTRICT")
    )
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    requirement_type: Mapped[str] = mapped_column(String(64), nullable=False)
    effect: Mapped[str] = mapped_column(String(16), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_json: Mapped[str] = mapped_column(Text, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)


class ProjectTaskDependency(Base):
    __tablename__ = "project_task_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "project_version_id",
            "dependent_task_identity_id",
            "prerequisite_task_identity_id",
            "dependency_type",
            name="uq_project_task_dependency",
        ),
        CheckConstraint(
            "dependency_type IN ('hard','recommended_before')", name="ck_project_dependency_type"
        ),
        CheckConstraint(
            "dependent_task_identity_id != prerequisite_task_identity_id",
            name="ck_project_dependency_self",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_version_id: Mapped[str] = mapped_column(
        ForeignKey("project_versions.id", ondelete="RESTRICT"), nullable=False
    )
    dependent_task_identity_id: Mapped[str] = mapped_column(
        ForeignKey("project_task_identities.id", ondelete="RESTRICT"), nullable=False
    )
    prerequisite_task_identity_id: Mapped[str] = mapped_column(
        ForeignKey("project_task_identities.id", ondelete="RESTRICT"), nullable=False
    )
    dependency_type: Mapped[str] = mapped_column(String(32), nullable=False)


class ProjectEvidenceOpportunity(Base):
    __tablename__ = "project_evidence_opportunities"
    __table_args__ = (
        UniqueConstraint("project_version_id", "stable_key", name="uq_project_opportunity_key"),
        UniqueConstraint("project_version_id", "order_index", name="uq_project_opportunity_order"),
        CheckConstraint("order_index >= 0", name="ck_project_opportunity_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_version_id: Mapped[str] = mapped_column(
        ForeignKey("project_versions.id", ondelete="RESTRICT"), nullable=False
    )
    task_definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_task_definitions.id", ondelete="RESTRICT")
    )
    project_criterion_definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_criterion_definitions.id", ondelete="RESTRICT")
    )
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    intended_characteristics_json: Mapped[str] = mapped_column(Text, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)


class ActiveProjectVersionState(Base):
    __tablename__ = "active_project_version_states"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), primary_key=True
    )
    project_version_id: Mapped[str] = mapped_column(
        ForeignKey("project_versions.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    activated_at: Mapped[int] = mapped_column(Integer, nullable=False)


class ProjectVersionActivationEvent(Base):
    __tablename__ = "project_version_activation_events"
    __table_args__ = (
        UniqueConstraint("project_id", "event_sequence", name="uq_project_activation_seq"),
        UniqueConstraint("idempotency_key", name="uq_project_activation_idempotency"),
        CheckConstraint("event_sequence > 0", name="ck_project_activation_sequence"),
        Index("ix_project_activation_cutoff", "project_id", "activated_at", "event_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    from_project_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_versions.id", ondelete="RESTRICT")
    )
    to_project_version_id: Mapped[str] = mapped_column(
        ForeignKey("project_versions.id", ondelete="RESTRICT"), nullable=False
    )
    activated_at: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


class ProjectEvent(Base):
    __tablename__ = "project_events"
    __table_args__ = (
        UniqueConstraint("project_id", "event_sequence", name="uq_project_event_sequence"),
        UniqueConstraint("idempotency_key", name="uq_project_event_idempotency"),
        CheckConstraint("event_sequence > 0", name="ck_project_event_sequence"),
        CheckConstraint(
            "event_type IN ('project_lifecycle','task_lifecycle','blocker_opened',"
            "'blocker_resolved','blocker_corrected')",
            name="ck_project_event_type",
        ),
        CheckConstraint(
            "project_lifecycle_state IS NULL OR project_lifecycle_state IN "
            "('planned','active','completed','archived')",
            name="ck_project_lifecycle_state",
        ),
        CheckConstraint(
            "task_lifecycle_state IS NULL OR task_lifecycle_state IN "
            "('not_started','started','completed','cancelled')",
            name="ck_project_task_state",
        ),
        Index("ix_project_event_cutoff", "project_id", "occurred_at", "event_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    project_version_id: Mapped[str] = mapped_column(
        ForeignKey("project_versions.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    task_identity_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_task_identities.id", ondelete="RESTRICT")
    )
    project_lifecycle_state: Mapped[str | None] = mapped_column(String(16))
    task_lifecycle_state: Mapped[str | None] = mapped_column(String(16))
    blocker_key: Mapped[str | None] = mapped_column(String(255))
    blocker_actionable: Mapped[bool | None] = mapped_column(Boolean)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    command_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[int] = mapped_column(Integer, nullable=False)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    corrects_event_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_events.id", ondelete="RESTRICT")
    )


class ActivityProjectTaskLink(Base):
    __tablename__ = "activity_project_task_links"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_activity_project_link_idempotency"),
        CheckConstraint(
            "provenance IN ('user_selected','user_confirmed','imported_asserted')",
            name="ck_activity_project_link_provenance",
        ),
        Index("ix_activity_project_link_activity", "activity_id", "created_at"),
        Index("ix_activity_project_link_task", "task_definition_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    activity_id: Mapped[str] = mapped_column(
        ForeignKey("activities.id", ondelete="RESTRICT"), nullable=False
    )
    task_definition_id: Mapped[str] = mapped_column(
        ForeignKey("project_task_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    provenance: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


class ActivityProjectTaskLinkCorrection(Base):
    __tablename__ = "activity_project_task_link_corrections"
    __table_args__ = (
        UniqueConstraint("activity_project_task_link_id", name="uq_activity_project_correction"),
        UniqueConstraint("idempotency_key", name="uq_activity_project_correction_idempotency"),
        Index("ix_activity_project_correction_replacement", "replacement_link_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    activity_project_task_link_id: Mapped[str] = mapped_column(
        ForeignKey("activity_project_task_links.id", ondelete="RESTRICT"), nullable=False
    )
    replacement_link_id: Mapped[str | None] = mapped_column(
        ForeignKey("activity_project_task_links.id", ondelete="RESTRICT")
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_at: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


class SessionProjectContribution(Base):
    __tablename__ = "session_project_contributions"
    __table_args__ = (
        CheckConstraint(
            "relevance IN ('primary','secondary','supporting')", name="ck_session_project_relevance"
        ),
        CheckConstraint(
            "provenance IN ('user_selected','user_confirmed','imported_asserted')",
            name="ck_session_project_provenance",
        ),
        CheckConstraint(
            "(idempotency_key IS NULL) = (command_hash IS NULL)",
            name="ck_session_project_command_identity",
        ),
        UniqueConstraint("idempotency_key", name="uq_session_project_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("learning_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    project_version_id: Mapped[str] = mapped_column(
        ForeignKey("project_versions.id", ondelete="RESTRICT"), nullable=False
    )
    task_definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_task_definitions.id", ondelete="RESTRICT")
    )
    relevance: Mapped[str] = mapped_column(String(16), nullable=False)
    provenance: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=utc_now_ms, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    command_hash: Mapped[str | None] = mapped_column(String(64))


class SessionProjectContributionRetraction(Base):
    __tablename__ = "session_project_contribution_retractions"
    __table_args__ = (
        UniqueConstraint("contribution_id", name="uq_session_project_retraction"),
        Index("ix_session_project_retraction_replacement", "replacement_contribution_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    contribution_id: Mapped[str] = mapped_column(
        ForeignKey("session_project_contributions.id", ondelete="RESTRICT"), nullable=False
    )
    replacement_contribution_id: Mapped[str | None] = mapped_column(
        ForeignKey("session_project_contributions.id", ondelete="RESTRICT")
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    retracted_at: Mapped[int] = mapped_column(Integer, nullable=False)


class ProjectCriterionEvaluation(Base):
    __tablename__ = "project_criterion_evaluations"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_project_criterion_eval_idempotency"),
        CheckConstraint(
            "state IN ('unknown','not_demonstrated','partially_demonstrated','demonstrated')",
            name="ck_project_criterion_eval_state",
        ),
        Index(
            "ix_project_criterion_eval_cutoff", "project_criterion_definition_id", "evaluated_at"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_criterion_definition_id: Mapped[str] = mapped_column(
        ForeignKey("project_criterion_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    evaluated_at: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    facts_json: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


class ProjectCriterionEvaluationEvidence(Base):
    __tablename__ = "project_criterion_evaluation_evidence"

    project_criterion_evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("project_criterion_evaluations.id", ondelete="RESTRICT"), primary_key=True
    )
    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("evidence.id", ondelete="RESTRICT"), primary_key=True
    )


def _reject_project_history_mutation(*_args: object) -> None:
    raise ValueError("Canonical Project definitions and history are immutable.")


for _immutable_model in (
    Project,
    ProjectGoalIdentity,
    ProjectTaskIdentity,
    ProjectCriterionIdentity,
    ProjectMilestoneIdentity,
    ProjectVersion,
    ProjectGoalDefinition,
    ProjectMilestoneDefinition,
    ProjectTaskDefinition,
    ProjectCriterionDefinition,
    ProjectTarget,
    ProjectRequirement,
    ProjectTaskDependency,
    ProjectEvidenceOpportunity,
    ProjectVersionActivationEvent,
    ProjectEvent,
    ActivityProjectTaskLink,
    ActivityProjectTaskLinkCorrection,
    SessionProjectContribution,
    SessionProjectContributionRetraction,
    ProjectCriterionEvaluation,
    ProjectCriterionEvaluationEvidence,
):
    event.listen(_immutable_model, "before_update", _reject_project_history_mutation)
    event.listen(_immutable_model, "before_delete", _reject_project_history_mutation)
