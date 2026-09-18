from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ProfileTarget,
    ProfileTargetIdentity,
    TargetProfileActivationEvent,
    TargetProfileVersion,
)


@dataclass(frozen=True)
class ProfileTargetRelevancePublicDTO:
    state: str
    target_profile_version_id: str | None
    profile_target_identity_ids: tuple[str, ...]


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
