from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.capability_views import capability_projection_as_of
from app.curriculum.contracts import CurriculumCatalogPublicDTO
from app.curriculum.service import build_catalog as build_curriculum_catalog
from app.determinism import canonical_json, content_hash
from app.learning_graph.contracts import (
    ActiveLearningGraphProjectionPublicDTO,
    CompetencyEdgeProjectionPublicDTO,
)
from app.learning_graph.service import active_projection_graph_as_of
from app.models import (
    CapabilityScaleDimension,
    CapabilityScaleLevel,
    CapabilityScaleVersion,
    ProjectionInvalidation,
)
from app.profile_views import (
    ActiveProfileProjectionPublicDTO,
    ProfileDomainPublicDTO,
    ProfileTargetProjectionPublicDTO,
    SemanticDefinitionPublicDTO,
    active_profile_projection_as_of,
    active_semantic_definition_ids_as_of,
    semantic_definitions_public,
)
from app.projects.contracts import ProjectCatalogPublicDTO
from app.projects.service import build_catalog as build_project_catalog
from app.roadmap_projection.layout import (
    ACTIVE_LAYOUT_POLICY,
    FROZEN_LAYOUT_POLICY,
    LayoutNode,
    build_layout,
    layout_policy_contract,
    require_registered_layout_policy,
)
from app.roadmap_projection.models import (
    RoadmapNodePositionOverride,
    RoadmapProjectionCache,
    RoadmapProjectionCheckpoint,
    RoadmapProjectionPreference,
)
from app.roadmap_projection.policies import (
    ACTIVE_PROJECTION_POLICY,
    FROZEN_PROJECTION_POLICY,
)
from app.time_utils import utc_now_ms
from app.today.public import current_roadmap_overlay

logger = logging.getLogger(__name__)

PROJECTION_POLICY = ACTIVE_PROJECTION_POLICY
LAYOUT_POLICY = ACTIVE_LAYOUT_POLICY


def projection_policy_for_layout(layout_policy_version: str) -> str:
    require_registered_layout_policy(layout_policy_version)
    return (
        FROZEN_PROJECTION_POLICY
        if layout_policy_version == FROZEN_LAYOUT_POLICY
        else PROJECTION_POLICY
    )


def _current_analysis_gap_references(
    db: Session, *, profile_version_id: str
) -> dict[str, dict[str, str]]:
    """Expose references only to a compatible, current public Analysis snapshot."""
    from app.analysis.v3.public import load_public_analysis_snapshot
    from app.analysis.v3.service import current_analysis

    current = current_analysis(db, purpose="learning_control")
    snapshot = current.get("snapshot")
    if (
        current.get("status") != "current"
        or not isinstance(snapshot, dict)
        or not isinstance(snapshot.get("id"), str)
    ):
        return {}
    public = load_public_analysis_snapshot(db, str(snapshot["id"]))
    if public.target_profile_version_id != profile_version_id:
        return {}
    references: dict[str, dict[str, str]] = {}
    for gap in public.gaps:
        references[gap.target_fact.target_identity_id] = {
            "snapshotId": public.snapshot_id,
            "gapStableKey": gap.stable_key,
            "comparisonStatus": gap.comparison_status,
        }
    return references


def _catalog_projection_hash(
    catalog: CurriculumCatalogPublicDTO | ProjectCatalogPublicDTO,
) -> str:
    """Hash catalog facts without read-time cutoff/hash wrappers.

    Curriculum and Project catalogs are independently replayable snapshots, so
    their own public input hashes include their exact cutoff. Roadmap's current
    projection is instead content-addressed: it hashes the complete catalog DTO
    after removing only those read-envelope fields.
    """

    def semantic(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: semantic(item)
                for key, item in value.items()
                if key not in {"cutoff_at", "input_hash"}
            }
        if isinstance(value, (list, tuple)):
            return [semantic(item) for item in value]
        return value

    return content_hash(semantic(asdict(catalog)))


def projection_scope_key(profile_version_id: str, graph_version_id: str) -> str:
    return f"profile:{profile_version_id}:graph:{graph_version_id}"


def _active_inputs(
    db: Session, cutoff_at: int
) -> tuple[ActiveProfileProjectionPublicDTO, ActiveLearningGraphProjectionPublicDTO] | None:
    profile = active_profile_projection_as_of(db, exclusive_cutoff_at=cutoff_at)
    graph = active_projection_graph_as_of(db, exclusive_cutoff_at=cutoff_at)
    if profile is None or graph is None:
        return None
    return profile, graph


def _specialization_depths(
    node_ids: set[str], edges: Sequence[CompetencyEdgeProjectionPublicDTO]
) -> dict[str, int]:
    parents: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for edge in edges:
        if edge.edge_type == "specialization":
            parents.setdefault(edge.target_competency_identity_id, set()).add(
                edge.source_competency_identity_id
            )
    memo: dict[str, int] = {}

    def depth(node_id: str) -> int:
        if node_id in memo:
            return memo[node_id]
        memo[node_id] = max(
            (depth(parent) + 1 for parent in parents.get(node_id, set())), default=0
        )
        return memo[node_id]

    return {node_id: depth(node_id) for node_id in node_ids}


def _specialization_parents(
    edges: Sequence[CompetencyEdgeProjectionPublicDTO],
    *,
    domain_by_identity: dict[str, ProfileDomainPublicDTO],
    target_by_identity: dict[str, ProfileTargetProjectionPublicDTO],
    specialization_depths: dict[str, int],
) -> dict[str, str]:
    candidates: dict[str, list[str]] = {}
    for edge in edges:
        if edge.edge_type == "specialization":
            candidates.setdefault(edge.target_competency_identity_id, []).append(
                edge.source_competency_identity_id
            )
    priority = {
        "critical": 0,
        "core": 1,
        "important": 2,
        "supporting": 3,
        "optional": 4,
    }
    result: dict[str, str] = {}
    for child, parents in candidates.items():
        child_domain = domain_by_identity.get(child)
        result[child] = min(
            parents,
            key=lambda parent: (
                0
                if child_domain is not None
                and domain_by_identity.get(parent) is not None
                and domain_by_identity[parent].id == child_domain.id
                else 1,
                specialization_depths.get(parent, 0),
                priority.get(
                    target_by_identity[parent].priority
                    if parent in target_by_identity
                    else "optional",
                    4,
                ),
                parent,
            ),
        )
    return result


def _prerequisite_depths(
    node_ids: set[str], edges: Sequence[CompetencyEdgeProjectionPublicDTO]
) -> dict[str, int]:
    predecessors: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for edge in edges:
        if edge.edge_type == "prerequisite":
            predecessors.setdefault(edge.target_competency_identity_id, set()).add(
                edge.source_competency_identity_id
            )
    memo: dict[str, int] = {}

    def depth(node_id: str, visiting: set[str]) -> int:
        if node_id in memo:
            return memo[node_id]
        if node_id in visiting:
            return 0
        values = [
            depth(item, visiting | {node_id}) + 1 for item in predecessors.get(node_id, set())
        ]
        memo[node_id] = max(values, default=0)
        return memo[node_id]

    return {node_id: depth(node_id, set()) for node_id in node_ids}


def _capability_display(
    db: Session, semantic: SemanticDefinitionPublicDTO, cutoff_at: int
) -> dict[str, Any]:
    scopes = capability_projection_as_of(
        db, semantic_definition_id=semantic.id, exclusive_cutoff_at=cutoff_at
    )
    return {
        "scopes": [
            {
                "scopeKey": scope.scope_key,
                "evaluationRunId": scope.evaluation_run_id,
                "evaluationOutputHash": scope.evaluation_output_hash,
                "assessmentStatus": scope.assessment_status,
                "levelId": scope.selected_level_id,
                "confidence": scope.aggregate_confidence,
                "freshness": scope.freshness,
                "reviewDue": scope.review_due,
                "reviewEventId": scope.review_event_id,
            }
            for scope in scopes
        ]
    }


def build_projection(
    db: Session,
    *,
    cutoff_at: int | None = None,
    project_catalog: ProjectCatalogPublicDTO | None = None,
    include_current_presentation: bool | None = None,
    layout_policy_version: str = LAYOUT_POLICY,
) -> dict[str, Any]:
    require_registered_layout_policy(layout_policy_version)
    projection_policy_version = projection_policy_for_layout(layout_policy_version)
    frozen_v2 = layout_policy_version == FROZEN_LAYOUT_POLICY
    cutoff = cutoff_at if cutoff_at is not None else utc_now_ms() + 1
    use_current_presentation = (
        cutoff_at is None if include_current_presentation is None else include_current_presentation
    )
    inputs = _active_inputs(db, cutoff)
    if inputs is None:
        return {
            "configured": False,
            "guidance": "Activate a Target Profile and native Learning Graph to build Roadmap V2.",
            "authority": "v2_projection",
        }
    profile, graph = inputs
    scope_key = projection_scope_key(profile.profile_version_id, graph.learning_graph_version_id)
    edges = graph.edges
    domains = {item.id: item for item in profile.domains}
    active_semantics = active_semantic_definition_ids_as_of(db, exclusive_cutoff_at=cutoff)
    targets = profile.targets
    targets_by_identity: dict[str, list[ProfileTargetProjectionPublicDTO]] = {}
    competency_by_target_identity: dict[str, str] = {}
    domains_by_identity: dict[str, list[ProfileDomainPublicDTO]] = {}
    for target in targets:
        competency_id = target.competency_identity_id
        targets_by_identity.setdefault(competency_id, []).append(target)
        competency_by_target_identity[target.target_identity_id] = competency_id
        domain = domains[target.profile_domain_id]
        if domain.id not in {item.id for item in domains_by_identity.setdefault(competency_id, [])}:
            domains_by_identity[competency_id].append(domain)
    if not frozen_v2:
        for values in targets_by_identity.values():
            values.sort(
                key=lambda item: (
                    domains[item.profile_domain_id].order_index,
                    item.dimension_key or "",
                    item.stable_key,
                    item.id,
                )
            )
    target_by_identity = {
        competency_id: values[-1] if frozen_v2 else values[0]
        for competency_id, values in targets_by_identity.items()
    }
    domain_by_identity = {
        competency_id: (
            domains[target_by_identity[competency_id].profile_domain_id] if frozen_v2 else values[0]
        )
        for competency_id, values in domains_by_identity.items()
    }
    node_ids = set(targets_by_identity)
    definition_by_identity = dict(active_semantics)
    for edge in edges:
        node_ids.add(edge.source_competency_identity_id)
        node_ids.add(edge.target_competency_identity_id)
        definition_by_identity[edge.source_competency_identity_id] = (
            edge.source_semantic_definition_id
        )
        definition_by_identity[edge.target_competency_identity_id] = (
            edge.target_semantic_definition_id
        )
    definition_ids = {
        definition_by_identity[node_id] for node_id in node_ids if node_id in definition_by_identity
    }
    semantics = semantic_definitions_public(db, definition_ids=definition_ids)
    overrides = (
        {
            item.node_key: item
            for item in db.scalars(
                select(RoadmapNodePositionOverride)
                .where(RoadmapNodePositionOverride.scope_key == scope_key)
                .order_by(RoadmapNodePositionOverride.node_key)
            ).all()
        }
        if use_current_presentation
        else {}
    )
    specialization_depths = _specialization_depths(node_ids, edges)
    parents = _specialization_parents(
        edges,
        domain_by_identity=domain_by_identity,
        target_by_identity=target_by_identity,
        specialization_depths=specialization_depths,
    )
    depths = _prerequisite_depths(node_ids, edges)
    domain_order = {
        item.id: index
        for index, item in enumerate(
            sorted(domains.values(), key=lambda row: (row.order_index, row.id))
        )
    }
    priority_order = {
        "critical": 0,
        "core": 1,
        "important": 2,
        "supporting": 3,
        "optional": 4,
    }
    stable_node_ids = sorted(
        (node_id for node_id in node_ids if node_id in definition_by_identity),
        key=lambda node_id: (
            domain_order.get(domain_by_identity[node_id].id, len(domain_order))
            if node_id in domain_by_identity
            else len(domain_order),
            specialization_depths.get(node_id, 0),
            priority_order.get(
                (
                    target_by_identity[node_id].priority
                    if frozen_v2 and node_id in target_by_identity
                    else min(
                        (item.priority for item in targets_by_identity.get(node_id, ())),
                        key=lambda value: priority_order[value],
                        default="optional",
                    )
                ),
                4,
            ),
            semantics[definition_by_identity[node_id]].competency_stable_key,
            node_id,
        ),
    )
    today_overlay = current_roadmap_overlay(db, now_ms=cutoff) if use_current_presentation else None
    today_competency_ids = (
        {
            item.competency_identity_id
            for item in today_overlay.items
            if item.competency_identity_id is not None
        }
        if today_overlay is not None
        else set()
    )
    if today_overlay is not None:
        today_competency_ids.update(
            competency_by_target_identity[target_id]
            for item in today_overlay.items
            for target_id in item.target_identity_ids
            if target_id in competency_by_target_identity
        )
    level_ids = {target.target_level_id for target in targets}
    levels = {
        item.id: item
        for item in db.scalars(
            select(CapabilityScaleLevel).where(CapabilityScaleLevel.id.in_(level_ids))
        ).all()
    }
    scale_ids = {target.scale_version_id for target in targets}
    scales = {
        item.id: item
        for item in db.scalars(
            select(CapabilityScaleVersion).where(CapabilityScaleVersion.id.in_(scale_ids))
        ).all()
    }
    dimension_ids = {target.dimension_id for target in targets if target.dimension_id is not None}
    dimensions = {
        item.id: item
        for item in db.scalars(
            select(CapabilityScaleDimension).where(CapabilityScaleDimension.id.in_(dimension_ids))
        ).all()
    }
    analysis_gap_references = (
        {}
        if frozen_v2 or cutoff_at is not None
        else _current_analysis_gap_references(db, profile_version_id=profile.profile_version_id)
    )
    layout_nodes = [
        LayoutNode(
            node_id=competency_id,
            stable_key=semantics[definition_by_identity[competency_id]].competency_stable_key,
            prerequisite_depth=depths.get(competency_id, 0),
            specialization_depth=specialization_depths.get(competency_id, 0),
            presentation_parent_id=parents.get(competency_id),
            targets=tuple(targets_by_identity.get(competency_id, ())),
        )
        for competency_id in stable_node_ids
    ]
    layout = build_layout(
        layout_policy_version,
        layout_nodes,
        domains=tuple(sorted(domains.values(), key=lambda item: (item.order_index, item.id))),
        edges=edges,
    )
    nodes: list[dict[str, Any]] = []
    for competency_id in stable_node_ids:
        semantic = semantics[definition_by_identity[competency_id]]
        node_domain = domain_by_identity.get(competency_id)
        if not frozen_v2 and len(domains_by_identity.get(competency_id, ())) > 1:
            node_domain = None
        layout_position = layout[competency_id]
        canonical_position = {
            "x": layout_position.x,
            "y": layout_position.y,
        }
        override = overrides.get(competency_id)
        node_target = target_by_identity.get(competency_id)
        node_targets = targets_by_identity.get(competency_id, [])
        node_payload: dict[str, Any] = {
            "id": competency_id,
            "nodeKey": competency_id,
            "competencyIdentityId": competency_id,
            "semanticDefinitionId": semantic.id,
            "stableKey": semantic.competency_stable_key,
            "title": semantic.title,
            "description": semantic.description,
            "profileDomain": {
                "id": node_domain.id,
                "stableKey": node_domain.stable_key,
                "title": node_domain.title,
                "orderIndex": node_domain.order_index,
            }
            if node_domain
            else None,
            "profileTarget": {
                "id": node_target.id,
                "identityId": node_target.target_identity_id,
                "priority": node_target.priority,
                "targetLevelId": node_target.target_level_id,
                "targetLevelOrdinal": node_target.target_level_ordinal,
                "targetDate": node_target.target_date,
                "targetMonth": node_target.target_month,
            }
            if node_target
            else None,
            "presentationParentId": parents.get(competency_id),
            "capability": _capability_display(db, semantic, cutoff),
            "canonicalPosition": canonical_position,
            "position": {
                "x": override.position_x,
                "y": override.position_y,
            }
            if override
            else canonical_position,
            "positionSource": override.provenance if override else layout_policy_version,
            "isCurrent": bool(node_target),
            "isToday": competency_id in today_competency_ids,
        }
        if not frozen_v2:
            node_payload.update(
                {
                    "profileTargets": [
                        {
                            "id": target.id,
                            "identityId": target.target_identity_id,
                            "stableKey": target.stable_key,
                            "competencyIdentityId": target.competency_identity_id,
                            "dimensionKey": target.dimension_key,
                            "dimensionId": target.dimension_id,
                            "dimensionTitle": (
                                dimensions[target.dimension_id].display_label
                                if target.dimension_id in dimensions
                                else None
                            ),
                            "profileDomain": {
                                "id": domains[target.profile_domain_id].id,
                                "stableKey": domains[target.profile_domain_id].stable_key,
                                "title": domains[target.profile_domain_id].title,
                                "orderIndex": domains[target.profile_domain_id].order_index,
                            },
                            "scaleVersionId": target.scale_version_id,
                            "scaleStableKey": scales[target.scale_version_id].scale_stable_key,
                            "scaleVersion": scales[target.scale_version_id].scale_version,
                            "targetLevelId": target.target_level_id,
                            "targetLevelKey": levels[target.target_level_id].stable_key,
                            "targetLevelTitle": levels[target.target_level_id].display_label,
                            "targetLevelOrdinal": target.target_level_ordinal,
                            "priority": target.priority,
                            "targetDate": target.target_date,
                            "targetMonth": target.target_month,
                            "comparisonStateReference": analysis_gap_references.get(
                                target.target_identity_id
                            ),
                        }
                        for target in node_targets
                    ],
                    "isTargeted": bool(node_targets),
                    "layoutLane": {
                        "id": layout_position.lane_id,
                        "title": layout_position.lane_title,
                        "orderIndex": layout_position.lane_order,
                    },
                    "layoutColumn": layout_position.column,
                    "layoutRow": layout_position.row,
                }
            )
        nodes.append(node_payload)
    satisfaction = {
        item.edge_definition_id: {
            **{key: value for key, value in asdict(item).items() if key != "criterion_states"},
            "criterion_states": [
                {
                    "criterionDefinitionId": criterion.criterion_definition_id,
                    "state": criterion.state,
                }
                for criterion in item.criterion_states
            ],
            # The public projection records the semantic cutoff contract. A
            # current read is identified by ``None`` rather than the transient
            # wall-clock value used internally to obtain a consistent view.
            "cutoff_at": cutoff_at,
        }
        for item in graph.satisfactions
    }
    preference = (
        db.get(RoadmapProjectionPreference, scope_key) if use_current_presentation else None
    )
    relationship_visibility = {
        "prerequisite": preference.show_prerequisites if preference else False,
        "recommended_before": preference.show_recommended_before if preference else True,
        "supports": preference.show_supports if preference else True,
        "specialization": True,
        "related": preference.show_related if preference else True,
    }
    projected_edges = [
        {
            "id": edge.id,
            "identityId": edge.edge_identity_id,
            "edgeType": edge.edge_type,
            "source": edge.source_competency_identity_id,
            "target": edge.target_competency_identity_id,
            "visibleByDefault": relationship_visibility[edge.edge_type],
            "eligibilityAuthority": edge.edge_type == "prerequisite",
            "satisfaction": satisfaction[edge.id],
        }
        for edge in edges
    ]
    milestones = [
        {
            "id": milestone.id,
            "title": milestone.title,
            "description": milestone.description,
            "targetDate": milestone.target_date,
            "orderIndex": milestone.order_index,
            "profileTargetIds": list(milestone.profile_target_ids),
        }
        for milestone in profile.milestones
    ]
    curricula = build_curriculum_catalog(db, cutoff)
    projects = project_catalog or build_project_catalog(db, cutoff)
    capability_lineage = [
        {
            "competencyIdentityId": node["competencyIdentityId"],
            "semanticDefinitionId": node["semanticDefinitionId"],
            "scopes": [
                {
                    "scopeKey": scope["scopeKey"],
                    "evaluationRunId": scope["evaluationRunId"],
                    "evaluationOutputHash": scope["evaluationOutputHash"],
                    "reviewEventId": scope["reviewEventId"],
                }
                for scope in node["capability"]["scopes"]
            ],
        }
        for node in nodes
    ]
    source_lineage = {
        "targetProfileId": profile.profile_id,
        "targetProfileVersionId": profile.profile_version_id,
        "targetProfileInputHash": content_hash(
            {
                "version": {
                    "id": profile.profile_version_id,
                    "version": profile.version,
                    "effectiveAt": profile.effective_at,
                },
                "groups": [
                    {
                        "id": item.id,
                        "stableKey": item.stable_key,
                        "minimumPercent": item.minimum_percent,
                        "maximumPercent": item.maximum_percent,
                        "orderIndex": item.order_index,
                    }
                    for item in sorted(domains.values(), key=lambda row: (row.order_index, row.id))
                ],
                "targets": [
                    {
                        "id": target.id,
                        "targetIdentityId": target.target_identity_id,
                        "domainId": target.profile_domain_id,
                        "levelId": target.target_level_id,
                        **(
                            {
                                "stableKey": target.stable_key,
                                "competencyIdentityId": target.competency_identity_id,
                                "dimensionKey": target.dimension_key,
                                "dimensionId": target.dimension_id,
                                "scaleVersionId": target.scale_version_id,
                                "levelOrdinal": target.target_level_ordinal,
                                "levelKey": levels[target.target_level_id].stable_key,
                                "levelTitle": levels[target.target_level_id].display_label,
                            }
                            if not frozen_v2
                            else {}
                        ),
                        "priority": target.priority,
                        "targetDate": target.target_date,
                        "targetMonth": target.target_month,
                    }
                    for target in targets
                ],
                "milestones": milestones,
            }
        ),
        "targetProfileActivationEventId": profile.activation_event_id,
        "targetProfileActivationSequence": profile.activation_sequence,
        "learningGraphId": graph.learning_graph_id,
        "learningGraphVersionId": graph.learning_graph_version_id,
        "learningGraphActivationEventId": graph.activation_event_id,
        "learningGraphActivationSequence": graph.activation_sequence,
        "learningGraphContentHash": graph.content_hash,
        "learningGraphSatisfactionHash": content_hash(satisfaction),
        "capabilityLineage": capability_lineage,
        "curriculumCatalogHash": _catalog_projection_hash(curricula),
        "curriculumVersions": [
            {
                "curriculumId": item.curriculum_id,
                "versionId": item.version_id,
                "contentHash": item.content_hash,
                "activationSequence": item.activation_sequence,
            }
            for item in curricula.active_version_references
        ],
        "projectVersions": [
            {
                "projectId": item.project_id,
                "versionId": item.version_id,
                "contentHash": item.content_hash,
                "activationSequence": item.activation_sequence,
                "latestProjectEventId": item.latest_project_event_id,
                "projectEventSequenceCutoff": item.project_event_sequence_cutoff,
            }
            for item in projects.active_version_references
        ],
        "projectCatalogHash": _catalog_projection_hash(projects),
        "todayOverlay": (
            {
                "inputHash": today_overlay.input_hash,
                "items": [asdict(item) for item in today_overlay.items],
            }
            if today_overlay is not None
            else {"mode": "excluded_historical"}
        ),
        **(
            {
                "analysisGapReferences": [
                    {"targetIdentityId": target_id, **reference}
                    for target_id, reference in sorted(analysis_gap_references.items())
                ],
                "layoutPolicy": layout_policy_contract(),
            }
            if not frozen_v2
            else {}
        ),
        "presentationInputs": {
            "mode": "current" if use_current_presentation else "excluded_historical",
            "positionOverrides": [
                {
                    "nodeKey": item.node_key,
                    "positionX": item.position_x,
                    "positionY": item.position_y,
                    "provenance": item.provenance,
                    "updatedAt": item.updated_at,
                }
                for item in sorted(overrides.values(), key=lambda row: row.node_key)
            ],
            "preference": {
                "showPrerequisites": preference.show_prerequisites,
                "showRecommendedBefore": preference.show_recommended_before,
                "showSupports": preference.show_supports,
                "showRelated": preference.show_related,
                "updatedAt": preference.updated_at,
            }
            if preference
            else None,
        },
        # A current projection is content-addressed by the exact authoritative
        # facts above, not by the wall clock used to take the consistent read.
        # Historical projections retain their caller-supplied cutoff because it
        # is part of the replay contract.
        "cutoffAt": cutoff_at,
        "cutoffMode": "historical" if cutoff_at is not None else "current",
        "cutoffSemantics": "exclusive",
    }
    output = {
        "configured": True,
        "authority": "v2_projection",
        "scopeKey": scope_key,
        "projectionPolicyVersion": projection_policy_version,
        "layoutPolicyVersion": layout_policy_version,
        "sourceLineage": source_lineage,
        "relationshipVisibility": relationship_visibility,
        "groups": [
            {
                "id": item.id,
                "stableKey": item.stable_key,
                "title": item.title,
                "description": item.description,
                "orderIndex": item.order_index,
            }
            for item in sorted(domains.values(), key=lambda row: (row.order_index, row.id))
        ],
        "nodes": nodes,
        "edges": projected_edges,
        "milestones": milestones,
        "legacyPhaseAuthority": False,
    }
    return output


def rebuild_projection(db: Session, *, cutoff_at: int | None = None) -> dict[str, Any]:
    output = build_projection(db, cutoff_at=cutoff_at)
    if not output.get("configured"):
        return output
    source_hash = content_hash(output["sourceLineage"])
    output_hash = content_hash(output)
    now = utc_now_ms()
    if cutoff_at is not None:
        return {**output, "outputHash": output_hash, "rebuiltAt": now, "persisted": False}
    scope_key = output["scopeKey"]
    cache = db.get(RoadmapProjectionCache, scope_key)
    if cache is None:
        cache = RoadmapProjectionCache(scope_key=scope_key)
        db.add(cache)
    cache.projection_policy_version = PROJECTION_POLICY
    cache.layout_policy_version = LAYOUT_POLICY
    cache.source_lineage_json = canonical_json(output["sourceLineage"])
    cache.source_hash = source_hash
    cache.output_json = canonical_json(output)
    cache.output_hash = output_hash
    cache.built_at = now
    generation = int(
        db.scalar(
            select(func.count(ProjectionInvalidation.id)).where(
                ProjectionInvalidation.projection_kind == "roadmap_projection_v2"
            )
        )
        or 0
    )
    checkpoint = db.get(RoadmapProjectionCheckpoint, scope_key)
    if checkpoint is None:
        checkpoint = RoadmapProjectionCheckpoint(scope_key=scope_key)
        db.add(checkpoint)
    checkpoint.source_generation = generation
    checkpoint.source_hash = source_hash
    checkpoint.output_hash = output_hash
    checkpoint.policy_bundle_hash = content_hash(
        {"projection": PROJECTION_POLICY, "layout": LAYOUT_POLICY}
    )
    checkpoint.rebuilt_at = now
    for failed in db.scalars(
        select(ProjectionInvalidation).where(
            ProjectionInvalidation.projection_kind == "roadmap_projection_v2",
            ProjectionInvalidation.status == "permanent_failure",
        )
    ).all():
        failed.status = "completed"
        failed.completed_at = now
        failed.error_json = None
    db.flush()
    return {**output, "outputHash": output_hash, "rebuiltAt": now}


def enqueue_projection_invalidation(
    db: Session, *, subject_type: str, subject_id: str, source_fact_id: str
) -> None:
    duplicate = db.scalar(
        select(ProjectionInvalidation.id).where(
            ProjectionInvalidation.projection_kind == "roadmap_projection_v2",
            ProjectionInvalidation.subject_type == subject_type,
            ProjectionInvalidation.subject_id == subject_id,
            ProjectionInvalidation.source_fact_id == source_fact_id,
            ProjectionInvalidation.target_policy_version == PROJECTION_POLICY,
        )
    )
    if duplicate is None:
        db.add(
            ProjectionInvalidation(
                projection_kind="roadmap_projection_v2",
                subject_type=subject_type,
                subject_id=subject_id,
                source_fact_id=source_fact_id,
                target_policy_version=PROJECTION_POLICY,
                status="pending",
                attempt_count=0,
                requested_at=utc_now_ms(),
            )
        )


def drain_projection_invalidations(db: Session, *, recover_running: bool = False) -> int:
    inputs = _active_inputs(db, utc_now_ms() + 1)
    if inputs is not None:
        profile, graph = inputs
        scope_key = projection_scope_key(
            profile.profile_version_id, graph.learning_graph_version_id
        )
        cache = db.get(RoadmapProjectionCache, scope_key)
        checkpoint = db.get(RoadmapProjectionCheckpoint, scope_key)
        expected_bundle_hash = content_hash(
            {"projection": PROJECTION_POLICY, "layout": LAYOUT_POLICY}
        )
        if (
            cache is not None
            and (
                cache.projection_policy_version != PROJECTION_POLICY
                or cache.layout_policy_version != LAYOUT_POLICY
            )
        ) or (checkpoint is not None and checkpoint.policy_bundle_hash != expected_bundle_hash):
            enqueue_projection_invalidation(
                db,
                subject_type="roadmap_projection_scope",
                subject_id=scope_key,
                source_fact_id=f"policy-transition:{PROJECTION_POLICY}:{LAYOUT_POLICY}",
            )
            # The application session disables autoflush. Make a transition
            # enqueued by this drain visible to the pending-row query below.
            db.flush()
    query = select(ProjectionInvalidation).where(
        ProjectionInvalidation.projection_kind == "roadmap_projection_v2",
        ProjectionInvalidation.status.in_(
            ["pending", "running"] if recover_running else ["pending"]
        ),
    )
    rows = db.scalars(query.order_by(ProjectionInvalidation.requested_at)).all()
    if not rows:
        return 0
    invalidation_ids = [row.id for row in rows]
    started_at = utc_now_ms()
    for row in rows:
        row.status = "running"
        row.target_policy_version = PROJECTION_POLICY
        row.attempt_count += 1
        row.started_at = started_at
        row.error_json = None
    db.commit()
    try:
        rebuild_projection(db)
        now = utc_now_ms()
        for invalidation_id in invalidation_ids:
            item = db.get(ProjectionInvalidation, invalidation_id)
            assert item is not None
            item.status = "completed"
            item.completed_at = now
            item.error_json = None
        db.commit()
        return len(invalidation_ids)
    except Exception as exc:
        db.rollback()
        for invalidation_id in invalidation_ids:
            item = db.get(ProjectionInvalidation, invalidation_id)
            assert item is not None
            item.status = "permanent_failure" if item.attempt_count >= 3 else "pending"
            item.error_json = canonical_json(
                {"type": type(exc).__name__, "message": str(exc)[:1000]}
            )
        db.commit()
        logger.exception("Roadmap Projection rebuild failed")
        return 0


def cached_projection(db: Session) -> dict[str, Any]:
    pending = db.scalar(
        select(func.count(ProjectionInvalidation.id)).where(
            ProjectionInvalidation.projection_kind == "roadmap_projection_v2",
            ProjectionInvalidation.status.in_(["pending", "running"]),
        )
    )
    if pending:
        return {**build_projection(db), "cacheState": "stale_bypassed"}
    permanent_failures = int(
        db.scalar(
            select(func.count(ProjectionInvalidation.id)).where(
                ProjectionInvalidation.projection_kind == "roadmap_projection_v2",
                ProjectionInvalidation.status == "permanent_failure",
            )
        )
        or 0
    )
    if permanent_failures:
        return {
            **build_projection(db),
            "cacheState": "rebuild_failed_bypassed",
            "rebuildFailureCount": permanent_failures,
        }
    inputs = _active_inputs(db, utc_now_ms() + 1)
    if inputs is None:
        return build_projection(db)
    profile, graph = inputs
    scope_key = projection_scope_key(profile.profile_version_id, graph.learning_graph_version_id)
    cache = db.get(RoadmapProjectionCache, scope_key)
    if cache is None:
        return {**build_projection(db), "cacheState": "not_built"}
    if (
        cache.projection_policy_version != PROJECTION_POLICY
        or cache.layout_policy_version != LAYOUT_POLICY
    ):
        return {**build_projection(db), "cacheState": "policy_stale_bypassed"}
    stored_lineage = json.loads(cache.source_lineage_json)
    output = json.loads(cache.output_json)
    if (
        content_hash(stored_lineage) != cache.source_hash
        or content_hash(output) != cache.output_hash
        or output.get("sourceLineage") != stored_lineage
    ):
        return {**build_projection(db), "cacheState": "cache_integrity_bypassed"}
    today_overlay = current_roadmap_overlay(db, now_ms=utc_now_ms() + 1)
    if stored_lineage.get("todayOverlay", {}).get("inputHash") != today_overlay.input_hash:
        return {**build_projection(db), "cacheState": "today_overlay_stale_bypassed"}
    current_gap_references = _current_analysis_gap_references(
        db, profile_version_id=profile.profile_version_id
    )
    expected_gap_references = [
        {"targetIdentityId": target_id, **reference}
        for target_id, reference in sorted(current_gap_references.items())
    ]
    if stored_lineage.get("analysisGapReferences", []) != expected_gap_references:
        return {**build_projection(db), "cacheState": "analysis_overlay_stale_bypassed"}
    return {**output, "outputHash": cache.output_hash, "rebuiltAt": cache.built_at}
