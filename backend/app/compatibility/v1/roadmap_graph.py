from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import AuthContext, get_auth_context
from app.database import get_db
from app.errors import AppError
from app.models import (
    CompetencyDefinition,
    CompetencyIdentity,
    CompetencyPrerequisite,
    RoadmapVersion,
)

LEGACY_GRAPH_POLICY = "legacy-roadmap-graph/v1"
_NAMESPACE = uuid.UUID("de7c1fe7-801e-51bb-9a48-0174972534f7")
router = APIRouter(
    prefix="/roadmap-projection/legacy-roadmap-graph",
    tags=["v1 roadmap graph compatibility"],
)


def legacy_graph_dto(db: Session, roadmap_version_id: str) -> dict[str, Any]:
    version = db.get(RoadmapVersion, roadmap_version_id)
    if version is None:
        raise ValueError("Roadmap version does not exist")
    definitions = db.scalars(
        select(CompetencyDefinition).where(
            CompetencyDefinition.roadmap_version_id == roadmap_version_id
        )
    ).all()
    definition_ids = [item.id for item in definitions]
    identities = {
        item.id: item
        for item in db.scalars(
            select(CompetencyIdentity).where(
                CompetencyIdentity.id.in_([item.competency_identity_id for item in definitions])
            )
        ).all()
    }
    edges: list[dict[str, Any]] = []
    for row in db.scalars(
        select(CompetencyPrerequisite)
        .where(CompetencyPrerequisite.competency_definition_id.in_(definition_ids))
        .order_by(
            CompetencyPrerequisite.competency_definition_id,
            CompetencyPrerequisite.prerequisite_competency_identity_id,
            CompetencyPrerequisite.kind,
        )
    ).all():
        dependent = next(item for item in definitions if item.id == row.competency_definition_id)
        source = identities[row.prerequisite_competency_identity_id]
        target = identities[dependent.competency_identity_id]
        material = (
            f"{LEGACY_GRAPH_POLICY}:{roadmap_version_id}:{row.competency_definition_id}:"
            f"{row.kind}:{source.id}:{target.id}"
        )
        edges.append(
            {
                "identityId": str(uuid.uuid5(_NAMESPACE, material)),
                "sourceCompetencyIdentityId": source.id,
                "targetCompetencyIdentityId": target.id,
                "sourceStableKey": source.stable_key,
                "targetStableKey": target.stable_key,
                "edgeType": "prerequisite" if row.kind == "required" else "recommended_before",
                "satisfactionRule": "legacy_verified" if row.kind == "required" else None,
                "hardEligibility": row.kind == "required",
                "provenance": {
                    "sourceType": "v1_roadmap_prerequisite",
                    "roadmapVersionId": roadmap_version_id,
                    "competencyDefinitionId": row.competency_definition_id,
                    "originalKind": row.kind,
                    "policyVersion": LEGACY_GRAPH_POLICY,
                },
            }
        )
    return {
        "authority": "legacy_v1_compatibility_only",
        "roadmapVersionId": version.id,
        "policyVersion": LEGACY_GRAPH_POLICY,
        "edges": edges,
    }


@router.get("/{roadmap_version_id}")
async def legacy_compatibility_graph(
    roadmap_version_id: str,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if db.get(RoadmapVersion, roadmap_version_id) is None:
        raise AppError(404, "ROADMAP_VERSION_NOT_FOUND", "The Roadmap version does not exist.")
    return legacy_graph_dto(db, roadmap_version_id)
