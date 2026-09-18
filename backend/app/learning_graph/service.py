from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.capability_views import capability_as_of, criterion_evaluation_as_of, review_as_of
from app.determinism import canonical_json, content_hash
from app.errors import AppError
from app.learning_graph.contracts import (
    ActiveLearningGraphProjectionPublicDTO,
    CompetencyEdgeInput,
    CompetencyEdgeProjectionPublicDTO,
    CriterionSatisfactionProjectionPublicDTO,
    EdgeSatisfactionProjectionPublicDTO,
    LearningGraphVersionInput,
)
from app.learning_graph.models import (
    ActiveLearningGraphState,
    CompetencyEdgeDefinition,
    CompetencyEdgeIdentity,
    LearningGraph,
    LearningGraphActivationEvent,
    LearningGraphVersion,
)
from app.models import (
    CapabilityScaleDimension,
    CapabilityScaleLevel,
    CriterionDefinition,
    ProjectionInvalidation,
    SemanticCompetencyDefinition,
    SemanticDefinitionDimension,
)
from app.time_utils import datetime_to_epoch_ms, utc_now_ms

GRAPH_SCHEMA_VERSION = "learning-graph-schema/v1"
GRAPH_SATISFACTION_POLICY = "learning-graph-satisfaction/v1"


@dataclass(frozen=True)
class EdgeSatisfactionDTO:
    edge_definition_id: str
    edge_identity_id: str
    edge_type: str
    source_competency_identity_id: str
    target_competency_identity_id: str
    source_semantic_definition_id: str
    target_semantic_definition_id: str
    aggregate_state: str
    eligibility_satisfied: bool | None
    capability_state: str
    review_state: str
    criterion_states: tuple[dict[str, str], ...]
    unknown_reasons: tuple[str, ...]
    policy_version: str
    cutoff_at: int


def _has_cycle(edges: dict[str, set[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for successor in edges.get(node, set()):
            if visit(successor):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in edges)


def _edge_definitions(
    db: Session, item: CompetencyEdgeInput
) -> tuple[SemanticCompetencyDefinition, SemanticCompetencyDefinition]:
    source = db.get(SemanticCompetencyDefinition, item.source_semantic_definition_id)
    target = db.get(SemanticCompetencyDefinition, item.target_semantic_definition_id)
    if source is None or target is None:
        raise AppError(422, "LEARNING_GRAPH_ENDPOINT_INVALID", "An edge endpoint is missing.")
    if source.competency_identity_id == target.competency_identity_id:
        raise AppError(
            422,
            "LEARNING_GRAPH_SELF_EDGE",
            "Learning Graph self-edges are prohibited at stable Competency identity.",
        )
    return source, target


def _normalized_edge(db: Session, item: CompetencyEdgeInput) -> dict[str, Any]:
    value = item.model_dump(mode="json")
    source, target = _edge_definitions(db, item)
    value["source_competency_identity_id"] = source.competency_identity_id
    value["target_competency_identity_id"] = target.competency_identity_id
    if (
        item.edge_type == "related"
        and source.competency_identity_id > target.competency_identity_id
    ):
        value["source_semantic_definition_id"], value["target_semantic_definition_id"] = (
            value["target_semantic_definition_id"],
            value["source_semantic_definition_id"],
        )
        value["source_competency_identity_id"], value["target_competency_identity_id"] = (
            value["target_competency_identity_id"],
            value["source_competency_identity_id"],
        )
    requirement = value.get("requirement")
    if requirement and requirement["kind"] == "criterion_set_demonstrated":
        requirement["criterion_definition_ids"] = sorted(requirement["criterion_definition_ids"])
    return value


def normalized_payload(db: Session, payload: LearningGraphVersionInput) -> dict[str, Any]:
    result = payload.model_dump(mode="json")
    result["effective_at"] = datetime_to_epoch_ms(payload.effective_at)
    result["schema_version"] = GRAPH_SCHEMA_VERSION
    result["satisfaction_policy_version"] = GRAPH_SATISFACTION_POLICY
    result["edges"] = [
        _normalized_edge(db, item)
        for item in sorted(payload.edges, key=lambda edge: (edge.order_index, edge.stable_key))
    ]
    return result


def _validate_requirement(db: Session, edge: CompetencyEdgeInput) -> None:
    source, _target = _edge_definitions(db, edge)
    requirement = edge.requirement
    if requirement is None:
        return
    if requirement.kind == "capability_at_least":
        level = db.get(CapabilityScaleLevel, requirement.minimum_level_id)
        dimension = (
            db.get(CapabilityScaleDimension, requirement.dimension_id)
            if requirement.dimension_id
            else None
        )
        if (
            source.scale_version_id != requirement.scale_version_id
            or level is None
            or level.scale_version_id != requirement.scale_version_id
            or (
                dimension is not None and dimension.scale_version_id != requirement.scale_version_id
            )
            or (requirement.dimension_id is not None and dimension is None)
        ):
            raise AppError(
                422,
                "LEARNING_GRAPH_SCALE_MISMATCH",
                "Native prerequisite capability requirements require an exact scale version.",
            )
        if requirement.dimension_id is None:
            if edge.satisfaction_scope_key != "overall":
                raise AppError(
                    422,
                    "LEARNING_GRAPH_SCOPE_MISMATCH",
                    "Overall capability requirements must use the overall scope.",
                )
        else:
            assert dimension is not None
            allowed = db.get(
                SemanticDefinitionDimension,
                (source.id, requirement.dimension_id),
            )
            if (
                allowed is None
                or edge.satisfaction_scope_key != f"dimension:{requirement.dimension_id}"
            ):
                raise AppError(
                    422,
                    "LEARNING_GRAPH_SCOPE_MISMATCH",
                    "A dimension requirement must use a dimension declared by the competency.",
                )
    else:
        for criterion_id in requirement.criterion_definition_ids:
            criterion = db.get(CriterionDefinition, criterion_id)
            if criterion is None or criterion.semantic_definition_id != source.id:
                raise AppError(
                    422,
                    "LEARNING_GRAPH_CRITERION_INVALID",
                    "A prerequisite criterion must belong to the source competency definition.",
                )


def validate_version(
    db: Session, payload: LearningGraphVersionInput, *, graph_id: str | None = None
) -> dict[str, Any]:
    stable_keys = [item.stable_key for item in payload.edges]
    orders = [item.order_index for item in payload.edges]
    if len(stable_keys) != len(set(stable_keys)) or len(orders) != len(set(orders)):
        raise AppError(422, "LEARNING_GRAPH_EDGE_DUPLICATE", "Edge keys and order must be unique.")
    semantic_keys: set[tuple[str, str, str, str]] = set()
    prerequisite_graph: dict[str, set[str]] = {}
    specialization_graph: dict[str, set[str]] = {}
    pinned_definitions: dict[str, str] = {}
    graph_effective_at = datetime_to_epoch_ms(payload.effective_at)
    for edge in payload.edges:
        _validate_requirement(db, edge)
        normalized = _normalized_edge(db, edge)
        for competency_key, definition_key in (
            ("source_competency_identity_id", "source_semantic_definition_id"),
            ("target_competency_identity_id", "target_semantic_definition_id"),
        ):
            competency_id = normalized[competency_key]
            definition_id = normalized[definition_key]
            prior_definition_id = pinned_definitions.setdefault(competency_id, definition_id)
            definition = db.get(SemanticCompetencyDefinition, definition_id)
            if prior_definition_id != definition_id or (
                definition is not None and definition.effective_at > graph_effective_at
            ):
                raise AppError(
                    422,
                    "LEARNING_GRAPH_DEFINITION_PIN_INVALID",
                    "A graph version must pin one already-effective definition per competency.",
                )
        semantic_key = (
            edge.edge_type,
            normalized["source_competency_identity_id"],
            normalized["target_competency_identity_id"],
            edge.satisfaction_scope_key,
        )
        if semantic_key in semantic_keys:
            raise AppError(
                422, "LEARNING_GRAPH_EDGE_DUPLICATE", "Duplicate edge semantics are prohibited."
            )
        semantic_keys.add(semantic_key)
        if graph_id is not None:
            existing_identity = db.scalar(
                select(CompetencyEdgeIdentity).where(
                    CompetencyEdgeIdentity.learning_graph_id == graph_id,
                    CompetencyEdgeIdentity.stable_key == edge.stable_key,
                )
            )
            if existing_identity is not None and (
                existing_identity.edge_type,
                existing_identity.source_competency_identity_id,
                existing_identity.target_competency_identity_id,
                existing_identity.meaning_key,
            ) != _identity_signature(db, edge):
                raise AppError(
                    422,
                    "LEARNING_GRAPH_EDGE_IDENTITY_INCOMPATIBLE",
                    "A stable edge identity cannot change endpoints, type, or meaning.",
                )
        if edge.edge_type == "prerequisite":
            prerequisite_graph.setdefault(normalized["source_competency_identity_id"], set()).add(
                normalized["target_competency_identity_id"]
            )
        if edge.edge_type == "specialization":
            specialization_graph.setdefault(normalized["source_competency_identity_id"], set()).add(
                normalized["target_competency_identity_id"]
            )
    if _has_cycle(prerequisite_graph):
        raise AppError(
            422, "LEARNING_GRAPH_PREREQUISITE_CYCLE", "Prerequisite edges must form a DAG."
        )
    if _has_cycle(specialization_graph):
        raise AppError(
            422, "LEARNING_GRAPH_SPECIALIZATION_CYCLE", "Specialization edges must be acyclic."
        )
    normalized = normalized_payload(db, payload)
    return {"normalizedPayload": normalized, "contentHash": content_hash(normalized)}


def _identity_signature(db: Session, edge: CompetencyEdgeInput) -> tuple[str, str, str, str]:
    normalized = _normalized_edge(db, edge)
    return (
        edge.edge_type,
        normalized["source_competency_identity_id"],
        normalized["target_competency_identity_id"],
        edge.meaning_key,
    )


def create_version(
    db: Session, graph: LearningGraph, payload: LearningGraphVersionInput
) -> LearningGraphVersion:
    validation = validate_version(db, payload, graph_id=graph.id)
    latest = db.scalar(
        select(LearningGraphVersion)
        .where(LearningGraphVersion.learning_graph_id == graph.id)
        .order_by(LearningGraphVersion.version.desc())
        .limit(1)
    )
    version = LearningGraphVersion(
        learning_graph_id=graph.id,
        version=(latest.version if latest else 0) + 1,
        title=payload.title,
        description=payload.description,
        schema_version=GRAPH_SCHEMA_VERSION,
        satisfaction_policy_version=GRAPH_SATISFACTION_POLICY,
        definition_payload_json=canonical_json(validation["normalizedPayload"]),
        content_hash=validation["contentHash"],
        effective_at=datetime_to_epoch_ms(payload.effective_at),
        creation_source=payload.creation_source,
        supersedes_version_id=latest.id if latest else None,
    )
    db.add(version)
    db.flush()
    for edge_input in sorted(payload.edges, key=lambda item: (item.order_index, item.stable_key)):
        normalized = _normalized_edge(db, edge_input)
        identity = db.scalar(
            select(CompetencyEdgeIdentity).where(
                CompetencyEdgeIdentity.learning_graph_id == graph.id,
                CompetencyEdgeIdentity.stable_key == edge_input.stable_key,
            )
        )
        signature = _identity_signature(db, edge_input)
        if identity is None:
            identity = CompetencyEdgeIdentity(
                learning_graph_id=graph.id,
                stable_key=edge_input.stable_key,
                edge_type=signature[0],
                source_competency_identity_id=signature[1],
                target_competency_identity_id=signature[2],
                meaning_key=signature[3],
            )
            db.add(identity)
            db.flush()
        elif (
            identity.edge_type,
            identity.source_competency_identity_id,
            identity.target_competency_identity_id,
            identity.meaning_key,
        ) != signature:
            raise AppError(
                422,
                "LEARNING_GRAPH_EDGE_IDENTITY_INCOMPATIBLE",
                "A stable edge identity cannot change endpoints, type, or meaning.",
            )
        requirement = normalized.get("requirement")
        db.add(
            CompetencyEdgeDefinition(
                learning_graph_version_id=version.id,
                edge_identity_id=identity.id,
                edge_type=edge_input.edge_type,
                source_competency_identity_id=normalized["source_competency_identity_id"],
                target_competency_identity_id=normalized["target_competency_identity_id"],
                source_semantic_definition_id=normalized["source_semantic_definition_id"],
                target_semantic_definition_id=normalized["target_semantic_definition_id"],
                satisfaction_scope_key=edge_input.satisfaction_scope_key,
                requirement_kind=requirement["kind"] if requirement else None,
                requirement_json=canonical_json(requirement or {}),
                provenance=edge_input.provenance,
                order_index=edge_input.order_index,
            )
        )
    db.commit()
    return version


def active_version_as_of(
    db: Session, graph_id: str, cutoff_at: int
) -> tuple[LearningGraphVersion, LearningGraphActivationEvent] | None:
    event = db.scalar(
        select(LearningGraphActivationEvent)
        .join(
            LearningGraphVersion,
            LearningGraphVersion.id == LearningGraphActivationEvent.to_learning_graph_version_id,
        )
        .where(
            LearningGraphActivationEvent.activated_at < cutoff_at,
            LearningGraphVersion.effective_at < cutoff_at,
        )
        .order_by(
            LearningGraphActivationEvent.activated_at.desc(),
            LearningGraphActivationEvent.event_sequence.desc(),
        )
        .limit(1)
    )
    if event is None or event.learning_graph_id != graph_id:
        return None
    version = db.get(LearningGraphVersion, event.to_learning_graph_version_id)
    return (version, event) if version is not None else None


def activate_version(
    db: Session,
    graph: LearningGraph,
    version: LearningGraphVersion,
    *,
    source: str,
    reason: str,
    idempotency_key: str,
) -> LearningGraphActivationEvent:
    existing = db.scalar(
        select(LearningGraphActivationEvent).where(
            LearningGraphActivationEvent.idempotency_key == idempotency_key
        )
    )
    if existing:
        if (
            existing.learning_graph_id != graph.id
            or existing.to_learning_graph_version_id != version.id
        ):
            raise AppError(409, "IDEMPOTENCY_KEY_REUSED", "The activation key was reused.")
        return existing
    state = db.get(ActiveLearningGraphState, 1)
    if state is not None and state.learning_graph_version_id == version.id:
        raise AppError(409, "LEARNING_GRAPH_VERSION_ALREADY_ACTIVE", "The version is active.")
    prior_for_graph = db.scalar(
        select(LearningGraphActivationEvent)
        .where(LearningGraphActivationEvent.learning_graph_id == graph.id)
        .order_by(LearningGraphActivationEvent.event_sequence.desc())
        .limit(1)
    )
    if prior_for_graph is not None:
        prior_version = db.get(LearningGraphVersion, prior_for_graph.to_learning_graph_version_id)
        if prior_version is not None and version.version < prior_version.version:
            raise AppError(
                409,
                "LEARNING_GRAPH_ACTIVATION_INVALID",
                "Learning Graph activation cannot move a graph backward.",
            )
    latest_sequence = db.scalar(select(func.max(LearningGraphActivationEvent.event_sequence)))
    now = utc_now_ms()
    if version.effective_at > now:
        raise AppError(
            409,
            "LEARNING_GRAPH_VERSION_NOT_EFFECTIVE",
            "A Learning Graph version cannot be activated before its effective time.",
        )
    item = LearningGraphActivationEvent(
        learning_graph_id=graph.id,
        from_learning_graph_version_id=state.learning_graph_version_id if state else None,
        to_learning_graph_version_id=version.id,
        activated_at=now,
        source=source,
        reason=reason,
        event_sequence=int(latest_sequence or 0) + 1,
        idempotency_key=idempotency_key,
    )
    db.add(item)
    if state is None:
        db.add(
            ActiveLearningGraphState(
                id=1,
                learning_graph_id=graph.id,
                learning_graph_version_id=version.id,
                activated_at=now,
            )
        )
    else:
        state.learning_graph_id = graph.id
        state.learning_graph_version_id = version.id
        state.activated_at = now
    db.flush()
    db.add(
        ProjectionInvalidation(
            projection_kind="roadmap_projection_v2",
            subject_type="learning_graph",
            subject_id=graph.id,
            source_fact_id=item.id,
            target_policy_version="roadmap-projection/v2.0",
            status="pending",
            attempt_count=0,
            requested_at=now,
        )
    )
    db.add(
        ProjectionInvalidation(
            projection_kind="analysis",
            subject_type="learning_graph",
            subject_id=graph.id,
            source_fact_id=item.id,
            target_policy_version="analysis-policy/v3.0",
            status="pending",
            attempt_count=0,
            requested_at=now,
        )
    )
    db.commit()
    return item


def serialize_version(db: Session, version: LearningGraphVersion) -> dict[str, Any]:
    graph = db.get(LearningGraph, version.learning_graph_id)
    identities = {
        item.id: item
        for item in db.scalars(
            select(CompetencyEdgeIdentity).where(
                CompetencyEdgeIdentity.learning_graph_id == version.learning_graph_id
            )
        ).all()
    }
    edges = db.scalars(
        select(CompetencyEdgeDefinition)
        .where(CompetencyEdgeDefinition.learning_graph_version_id == version.id)
        .order_by(CompetencyEdgeDefinition.order_index, CompetencyEdgeDefinition.id)
    ).all()
    return {
        "id": version.id,
        "learningGraphId": version.learning_graph_id,
        "learningGraphStableKey": graph.stable_key if graph else None,
        "version": version.version,
        "title": version.title,
        "description": version.description,
        "schemaVersion": version.schema_version,
        "satisfactionPolicyVersion": version.satisfaction_policy_version,
        "contentHash": version.content_hash,
        "effectiveAt": version.effective_at,
        "createdAt": version.created_at,
        "supersedesVersionId": version.supersedes_version_id,
        "edges": [
            {
                "id": edge.id,
                "identityId": edge.edge_identity_id,
                "stableKey": identities[edge.edge_identity_id].stable_key,
                "edgeType": edge.edge_type,
                "sourceCompetencyIdentityId": edge.source_competency_identity_id,
                "targetCompetencyIdentityId": edge.target_competency_identity_id,
                "sourceSemanticDefinitionId": edge.source_semantic_definition_id,
                "targetSemanticDefinitionId": edge.target_semantic_definition_id,
                "satisfactionScopeKey": edge.satisfaction_scope_key,
                "requirement": json.loads(edge.requirement_json) or None,
                "provenance": edge.provenance,
                "orderIndex": edge.order_index,
            }
            for edge in edges
        ],
    }


def evaluate_edge(
    db: Session, edge: CompetencyEdgeDefinition, cutoff_at: int
) -> EdgeSatisfactionDTO:
    if edge.edge_type != "prerequisite":
        return EdgeSatisfactionDTO(
            edge.id,
            edge.edge_identity_id,
            edge.edge_type,
            edge.source_competency_identity_id,
            edge.target_competency_identity_id,
            edge.source_semantic_definition_id,
            edge.target_semantic_definition_id,
            "not_applicable",
            None,
            "not_applicable",
            "not_applicable",
            (),
            (),
            GRAPH_SATISFACTION_POLICY,
            cutoff_at,
        )
    requirement = json.loads(edge.requirement_json)
    unknown: list[str] = []
    capability_state = "not_applicable"
    review_state = "not_applicable"
    criterion_states: list[dict[str, str]] = []
    aggregate = "met"
    if requirement["kind"] == "capability_at_least":
        fact = capability_as_of(
            db,
            semantic_definition_id=edge.source_semantic_definition_id,
            scale_version_id=requirement["scale_version_id"],
            dimension_id=requirement["dimension_id"],
            exclusive_cutoff_at=cutoff_at,
        )
        required_level = db.get(CapabilityScaleLevel, requirement["minimum_level_id"])
        if (
            fact is None
            or fact.assessment_status != "evaluated"
            or fact.selected_level_ordinal is None
        ):
            capability_state = "unknown"
            aggregate = "unknown"
            unknown.append("CAPABILITY_UNKNOWN")
        elif required_level is None:
            capability_state = "unknown"
            aggregate = "unknown"
            unknown.append("REQUIRED_LEVEL_UNKNOWN")
        elif fact.selected_level_ordinal >= required_level.ordinal_rank:
            capability_state = "met"
        else:
            capability_state = "not_met"
            aggregate = "not_met"
        if requirement.get("review_requirement") == "review_due_false":
            # Review is deliberately surfaced as its own predicate; it never changes capability.
            review = review_as_of(
                db,
                semantic_definition_id=edge.source_semantic_definition_id,
                scope_key=edge.satisfaction_scope_key,
                dimension_id=requirement.get("dimension_id"),
                exclusive_cutoff_at=cutoff_at,
            )
            if review is None:
                review_state = "unknown"
                if aggregate == "met":
                    aggregate = "unknown"
                unknown.append("REVIEW_STATE_UNKNOWN")
            elif review.review_due:
                review_state = "not_met"
                aggregate = "not_met"
            else:
                review_state = "met"
    else:
        for criterion_id in requirement["criterion_definition_ids"]:
            criterion_fact = criterion_evaluation_as_of(
                db, criterion_definition_id=criterion_id, exclusive_cutoff_at=cutoff_at
            )
            state = criterion_fact.state if criterion_fact else "unknown"
            criterion_states.append({"criterionDefinitionId": criterion_id, "state": state})
            if state == "unknown":
                if aggregate == "met":
                    aggregate = "unknown"
                unknown.append(f"CRITERION_UNKNOWN:{criterion_id}")
            elif state != "demonstrated":
                aggregate = "not_met"
    return EdgeSatisfactionDTO(
        edge.id,
        edge.edge_identity_id,
        edge.edge_type,
        edge.source_competency_identity_id,
        edge.target_competency_identity_id,
        edge.source_semantic_definition_id,
        edge.target_semantic_definition_id,
        aggregate,
        aggregate == "met",
        capability_state,
        review_state,
        tuple(criterion_states),
        tuple(sorted(unknown)),
        GRAPH_SATISFACTION_POLICY,
        cutoff_at,
    )


def satisfaction_for_version(db: Session, version_id: str, cutoff_at: int) -> list[dict[str, Any]]:
    return [
        asdict(evaluate_edge(db, edge, cutoff_at))
        for edge in db.scalars(
            select(CompetencyEdgeDefinition)
            .where(CompetencyEdgeDefinition.learning_graph_version_id == version_id)
            .order_by(CompetencyEdgeDefinition.order_index, CompetencyEdgeDefinition.id)
        ).all()
    ]


def active_projection_graph_as_of(
    db: Session, *, exclusive_cutoff_at: int
) -> ActiveLearningGraphProjectionPublicDTO | None:
    """Return the globally selected native Graph and derived satisfaction at a cutoff."""
    event = db.scalar(
        select(LearningGraphActivationEvent)
        .join(
            LearningGraphVersion,
            LearningGraphVersion.id == LearningGraphActivationEvent.to_learning_graph_version_id,
        )
        .where(
            LearningGraphActivationEvent.activated_at < exclusive_cutoff_at,
            LearningGraphVersion.effective_at < exclusive_cutoff_at,
        )
        .order_by(
            LearningGraphActivationEvent.activated_at.desc(),
            LearningGraphActivationEvent.event_sequence.desc(),
            LearningGraphActivationEvent.id.desc(),
        )
        .limit(1)
    )
    if event is None:
        return None
    version = db.get(LearningGraphVersion, event.to_learning_graph_version_id)
    if version is None:
        return None
    edges = db.scalars(
        select(CompetencyEdgeDefinition)
        .where(CompetencyEdgeDefinition.learning_graph_version_id == version.id)
        .order_by(CompetencyEdgeDefinition.order_index, CompetencyEdgeDefinition.id)
    ).all()
    satisfaction = [evaluate_edge(db, edge, exclusive_cutoff_at) for edge in edges]
    return ActiveLearningGraphProjectionPublicDTO(
        learning_graph_id=event.learning_graph_id,
        learning_graph_version_id=version.id,
        activation_event_id=event.id,
        activation_sequence=event.event_sequence,
        content_hash=version.content_hash,
        edges=tuple(
            CompetencyEdgeProjectionPublicDTO(
                id=edge.id,
                edge_identity_id=edge.edge_identity_id,
                edge_type=edge.edge_type,
                source_competency_identity_id=edge.source_competency_identity_id,
                target_competency_identity_id=edge.target_competency_identity_id,
                source_semantic_definition_id=edge.source_semantic_definition_id,
                target_semantic_definition_id=edge.target_semantic_definition_id,
                order_index=edge.order_index,
            )
            for edge in edges
        ),
        satisfactions=tuple(
            EdgeSatisfactionProjectionPublicDTO(
                edge_definition_id=item.edge_definition_id,
                edge_identity_id=item.edge_identity_id,
                edge_type=item.edge_type,
                source_competency_identity_id=item.source_competency_identity_id,
                target_competency_identity_id=item.target_competency_identity_id,
                source_semantic_definition_id=item.source_semantic_definition_id,
                target_semantic_definition_id=item.target_semantic_definition_id,
                aggregate_state=item.aggregate_state,
                eligibility_satisfied=item.eligibility_satisfied,
                capability_state=item.capability_state,
                review_state=item.review_state,
                criterion_states=tuple(
                    CriterionSatisfactionProjectionPublicDTO(
                        criterion_definition_id=criterion["criterionDefinitionId"],
                        state=criterion["state"],
                    )
                    for criterion in item.criterion_states
                ),
                unknown_reasons=item.unknown_reasons,
                policy_version=item.policy_version,
                cutoff_at=item.cutoff_at,
            )
            for item in satisfaction
        ),
    )
