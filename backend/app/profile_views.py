from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    CapabilityScaleDimension,
    CapabilityScaleLevel,
    CompetencyDefinitionActivationEvent,
    CompetencyIdentity,
    ProfileDomain,
    ProfileMilestone,
    ProfileMilestoneTarget,
    ProfileTarget,
    ProfileTargetIdentity,
    ReadinessGate,
    ReadinessGateIdentity,
    ReadinessGatePredicate,
    ReadinessGateTarget,
    SemanticCompetencyDefinition,
    TargetProfile,
    TargetProfileActivationEvent,
    TargetProfileVersion,
)


@dataclass(frozen=True)
class ProfileTargetRelevancePublicDTO:
    state: str
    target_profile_version_id: str | None
    profile_target_identity_ids: tuple[str, ...]


@dataclass(frozen=True)
class ProfileDomainPublicDTO:
    id: str
    stable_key: str
    title: str
    description: str
    minimum_percent: int | None
    maximum_percent: int | None
    order_index: int


@dataclass(frozen=True)
class ProfileTargetProjectionPublicDTO:
    id: str
    target_identity_id: str
    stable_key: str
    competency_identity_id: str
    dimension_key: str | None
    dimension_id: str | None
    profile_domain_id: str
    scale_version_id: str
    target_level_id: str
    target_level_ordinal: int
    priority: str
    target_date: str | None
    target_month: str | None
    freshness_override_days: int | None
    activated_at: int


@dataclass(frozen=True)
class ProfileMilestonePublicDTO:
    id: str
    title: str
    description: str
    target_date: str | None
    order_index: int
    profile_target_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReadinessPredicatePublicDTO:
    id: str
    predicate_type: str
    requirement_type: str
    subject_json: str
    order_index: int


@dataclass(frozen=True)
class ReadinessGatePublicDTO:
    id: str
    identity_id: str
    stable_key: str
    title: str
    effect: str
    milestone_id: str | None
    target_ids: tuple[str, ...]
    predicates: tuple[ReadinessPredicatePublicDTO, ...]
    order_index: int


@dataclass(frozen=True)
class ActiveProfileProjectionPublicDTO:
    profile_id: str
    profile_version_id: str
    version: int
    effective_at: int
    activation_event_id: str
    activation_sequence: int
    domains: tuple[ProfileDomainPublicDTO, ...]
    targets: tuple[ProfileTargetProjectionPublicDTO, ...]
    milestones: tuple[ProfileMilestonePublicDTO, ...]
    readiness_gates: tuple[ReadinessGatePublicDTO, ...]


@dataclass(frozen=True)
class SemanticDefinitionPublicDTO:
    id: str
    competency_identity_id: str
    competency_stable_key: str
    title: str
    description: str
    effective_at: int


def target_profile_relevance_as_of(
    db: Session,
    *,
    competency_identity_id: str,
    dimension_key: str | None,
    exclusive_cutoff_at: int,
) -> ProfileTargetRelevancePublicDTO:
    """Resolve exact active-profile relevance known strictly before a cutoff."""
    event = db.scalar(
        select(TargetProfileActivationEvent)
        .join(
            TargetProfileVersion,
            TargetProfileVersion.id == TargetProfileActivationEvent.to_profile_version_id,
        )
        .where(
            TargetProfileActivationEvent.activated_at < exclusive_cutoff_at,
            TargetProfileVersion.effective_at < exclusive_cutoff_at,
        )
        .order_by(
            TargetProfileActivationEvent.activated_at.desc(),
            TargetProfileActivationEvent.event_sequence.desc(),
            TargetProfileActivationEvent.id.desc(),
        )
        .limit(1)
    )
    if event is None:
        return ProfileTargetRelevancePublicDTO("unknown", None, ())
    target_ids = tuple(
        sorted(
            db.scalars(
                select(ProfileTargetIdentity.id)
                .join(ProfileTarget, ProfileTarget.target_identity_id == ProfileTargetIdentity.id)
                .where(
                    ProfileTarget.profile_version_id == event.to_profile_version_id,
                    ProfileTargetIdentity.competency_identity_id == competency_identity_id,
                    ProfileTargetIdentity.dimension_key == dimension_key,
                )
            ).all()
        )
    )
    return ProfileTargetRelevancePublicDTO(
        "targeted" if target_ids else "not_targeted",
        event.to_profile_version_id,
        target_ids,
    )


def active_profile_projection_as_of(
    db: Session, *, exclusive_cutoff_at: int
) -> ActiveProfileProjectionPublicDTO | None:
    event = db.scalar(
        select(TargetProfileActivationEvent)
        .join(
            TargetProfileVersion,
            TargetProfileVersion.id == TargetProfileActivationEvent.to_profile_version_id,
        )
        .where(
            TargetProfileActivationEvent.activated_at < exclusive_cutoff_at,
            TargetProfileVersion.effective_at < exclusive_cutoff_at,
        )
        .order_by(
            TargetProfileActivationEvent.activated_at.desc(),
            TargetProfileActivationEvent.event_sequence.desc(),
            TargetProfileActivationEvent.id.desc(),
        )
        .limit(1)
    )
    if event is None:
        return None
    version = db.get(TargetProfileVersion, event.to_profile_version_id)
    profile = db.get(TargetProfile, version.target_profile_id) if version else None
    if version is None or profile is None:
        return None
    domains = tuple(
        ProfileDomainPublicDTO(
            item.id,
            item.stable_key,
            item.title,
            item.description,
            item.minimum_percent,
            item.maximum_percent,
            item.order_index,
        )
        for item in db.scalars(
            select(ProfileDomain)
            .where(ProfileDomain.profile_version_id == version.id)
            .order_by(ProfileDomain.order_index, ProfileDomain.id)
        ).all()
    )
    identities = {
        item.id: item
        for item in db.scalars(
            select(ProfileTargetIdentity).where(
                ProfileTargetIdentity.target_profile_id == profile.id
            )
        ).all()
    }
    targets: list[ProfileTargetProjectionPublicDTO] = []
    for item in db.scalars(
        select(ProfileTarget)
        .where(ProfileTarget.profile_version_id == version.id)
        .order_by(ProfileTarget.id)
    ).all():
        identity = identities[item.target_identity_id]
        level = db.get(CapabilityScaleLevel, item.target_level_id)
        if level is None:
            return None
        dimension_id = None
        if identity.dimension_key is not None:
            dimension_id = db.scalar(
                select(CapabilityScaleDimension.id).where(
                    CapabilityScaleDimension.scale_version_id == item.scale_version_id,
                    CapabilityScaleDimension.stable_key == identity.dimension_key,
                )
            )
        targets.append(
            ProfileTargetProjectionPublicDTO(
                item.id,
                item.target_identity_id,
                identity.stable_key,
                identity.competency_identity_id,
                identity.dimension_key,
                dimension_id,
                item.profile_domain_id,
                item.scale_version_id,
                item.target_level_id,
                level.ordinal_rank,
                item.priority,
                item.target_date,
                item.target_month,
                item.freshness_override_days,
                int(
                    db.scalar(
                        select(func.min(TargetProfileActivationEvent.activated_at))
                        .join(
                            ProfileTarget,
                            ProfileTarget.profile_version_id
                            == TargetProfileActivationEvent.to_profile_version_id,
                        )
                        .where(
                            ProfileTarget.target_identity_id == item.target_identity_id,
                            TargetProfileActivationEvent.activated_at < exclusive_cutoff_at,
                        )
                    )
                    or event.activated_at
                ),
            )
        )
    milestones = tuple(
        ProfileMilestonePublicDTO(
            item.id,
            item.title,
            item.description,
            item.target_date,
            item.order_index,
            tuple(
                sorted(
                    db.scalars(
                        select(ProfileMilestoneTarget.profile_target_id).where(
                            ProfileMilestoneTarget.milestone_id == item.id
                        )
                    ).all()
                )
            ),
        )
        for item in db.scalars(
            select(ProfileMilestone)
            .where(ProfileMilestone.profile_version_id == version.id)
            .order_by(ProfileMilestone.order_index, ProfileMilestone.id)
        ).all()
    )
    gates = db.scalars(
        select(ReadinessGate)
        .where(ReadinessGate.profile_version_id == version.id)
        .order_by(ReadinessGate.order_index, ReadinessGate.id)
    ).all()
    gate_identities = {
        item.id: item
        for item in db.scalars(
            select(ReadinessGateIdentity).where(
                ReadinessGateIdentity.id.in_([gate.gate_identity_id for gate in gates])
            )
        ).all()
    }
    gate_targets: dict[str, list[str]] = {gate.id: [] for gate in gates}
    for gate_id, target_id in db.execute(
        select(ReadinessGateTarget.gate_id, ReadinessGateTarget.profile_target_id).where(
            ReadinessGateTarget.gate_id.in_([gate.id for gate in gates])
        )
    ).all():
        gate_targets[gate_id].append(target_id)
    predicates: dict[str, list[ReadinessPredicatePublicDTO]] = {gate.id: [] for gate in gates}
    for predicate_row in db.scalars(
        select(ReadinessGatePredicate)
        .where(ReadinessGatePredicate.gate_id.in_([gate.id for gate in gates]))
        .order_by(ReadinessGatePredicate.order_index, ReadinessGatePredicate.id)
    ).all():
        predicates[predicate_row.gate_id].append(
            ReadinessPredicatePublicDTO(
                predicate_row.id,
                predicate_row.predicate_type,
                predicate_row.requirement_type,
                predicate_row.subject_json,
                predicate_row.order_index,
            )
        )
    readiness_gates = tuple(
        ReadinessGatePublicDTO(
            gate.id,
            gate.gate_identity_id,
            gate_identities[gate.gate_identity_id].stable_key,
            gate.title,
            gate.effect,
            gate.milestone_id,
            tuple(sorted(gate_targets[gate.id])),
            tuple(predicates[gate.id]),
            gate.order_index,
        )
        for gate in gates
    )
    return ActiveProfileProjectionPublicDTO(
        profile.id,
        version.id,
        version.version,
        version.effective_at,
        event.id,
        event.event_sequence,
        domains,
        tuple(targets),
        milestones,
        readiness_gates,
    )


def active_semantic_definition_ids_as_of(
    db: Session, *, exclusive_cutoff_at: int
) -> dict[str, str]:
    rows = db.execute(
        select(CompetencyDefinitionActivationEvent, SemanticCompetencyDefinition)
        .join(
            SemanticCompetencyDefinition,
            SemanticCompetencyDefinition.id == CompetencyDefinitionActivationEvent.to_definition_id,
        )
        .where(
            CompetencyDefinitionActivationEvent.activated_at < exclusive_cutoff_at,
            SemanticCompetencyDefinition.effective_at < exclusive_cutoff_at,
        )
        .order_by(
            CompetencyDefinitionActivationEvent.competency_identity_id,
            CompetencyDefinitionActivationEvent.activated_at.desc(),
            CompetencyDefinitionActivationEvent.event_sequence.desc(),
            CompetencyDefinitionActivationEvent.id.desc(),
        )
    ).all()
    result: dict[str, str] = {}
    for event, definition in rows:
        result.setdefault(event.competency_identity_id, definition.id)
    return result


def semantic_definitions_public(
    db: Session, *, definition_ids: set[str]
) -> dict[str, SemanticDefinitionPublicDTO]:
    if not definition_ids:
        return {}
    rows = db.execute(
        select(SemanticCompetencyDefinition, CompetencyIdentity)
        .join(
            CompetencyIdentity,
            CompetencyIdentity.id == SemanticCompetencyDefinition.competency_identity_id,
        )
        .where(SemanticCompetencyDefinition.id.in_(sorted(definition_ids)))
    ).all()
    return {
        definition.id: SemanticDefinitionPublicDTO(
            definition.id,
            definition.competency_identity_id,
            competency.stable_key,
            definition.title,
            definition.description,
            definition.effective_at,
        )
        for definition, competency in rows
    }
