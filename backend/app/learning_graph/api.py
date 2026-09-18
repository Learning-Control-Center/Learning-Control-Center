from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import AuthContext, get_auth_context, require_csrf
from app.database import get_db
from app.errors import AppError
from app.learning_graph.contracts import (
    LearningGraphActivationInput,
    LearningGraphCreate,
    LearningGraphVersionInput,
)
from app.learning_graph.models import (
    ActiveLearningGraphState,
    LearningGraph,
    LearningGraphActivationEvent,
    LearningGraphVersion,
)
from app.learning_graph.service import (
    activate_version,
    create_version,
    satisfaction_for_version,
    serialize_version,
    validate_version,
)
from app.time_utils import utc_now_ms

router = APIRouter(prefix="/learning-graphs", tags=["v2 learning graph"])


def _graph(db: Session, graph_id: str) -> LearningGraph:
    item = db.get(LearningGraph, graph_id)
    if item is None:
        raise AppError(404, "LEARNING_GRAPH_NOT_FOUND", "The Learning Graph does not exist.")
    return item


@router.post("", status_code=201)
async def create_graph(
    payload: LearningGraphCreate,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if db.scalar(select(LearningGraph.id).where(LearningGraph.stable_key == payload.stable_key)):
        raise AppError(409, "LEARNING_GRAPH_EXISTS", "The stable key already exists.")
    item = LearningGraph(stable_key=payload.stable_key, creation_source=payload.creation_source)
    db.add(item)
    db.commit()
    return {
        "id": item.id,
        "stableKey": item.stable_key,
        "createdAt": item.created_at,
        "creationSource": item.creation_source,
        "activeVersionId": None,
    }


@router.get("")
async def list_graphs(
    _auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    state = db.get(ActiveLearningGraphState, 1)
    states = {state.learning_graph_id: state.learning_graph_version_id} if state else {}
    return [
        {
            "id": item.id,
            "stableKey": item.stable_key,
            "createdAt": item.created_at,
            "creationSource": item.creation_source,
            "activeVersionId": states.get(item.id),
        }
        for item in db.scalars(select(LearningGraph).order_by(LearningGraph.stable_key)).all()
    ]


@router.post("/{graph_id}/versions/validate")
async def validate_graph_version(
    graph_id: str,
    payload: LearningGraphVersionInput,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _graph(db, graph_id)
    result = validate_version(db, payload, graph_id=graph_id)
    state = db.get(ActiveLearningGraphState, 1)
    if state is not None and state.learning_graph_id != graph_id:
        state = None
    active = db.get(LearningGraphVersion, state.learning_graph_version_id) if state else None
    result["diff"] = {
        "activeVersionId": active.id if active else None,
        "activeContentHash": active.content_hash if active else None,
        "contentChanged": active is None or active.content_hash != result["contentHash"],
    }
    return result


@router.post("/{graph_id}/versions", status_code=201)
async def create_graph_version(
    graph_id: str,
    payload: LearningGraphVersionInput,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return serialize_version(db, create_version(db, _graph(db, graph_id), payload))


@router.get("/{graph_id}/versions")
async def graph_history(
    graph_id: str,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    _graph(db, graph_id)
    return [
        serialize_version(db, item)
        for item in db.scalars(
            select(LearningGraphVersion)
            .where(LearningGraphVersion.learning_graph_id == graph_id)
            .order_by(LearningGraphVersion.version)
        ).all()
    ]


@router.post("/{graph_id}/versions/{version_id}/activate", status_code=201)
async def activate_graph_version(
    graph_id: str,
    version_id: str,
    payload: LearningGraphActivationInput,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    graph = _graph(db, graph_id)
    version = db.get(LearningGraphVersion, version_id)
    if version is None or version.learning_graph_id != graph.id:
        raise AppError(404, "LEARNING_GRAPH_VERSION_NOT_FOUND", "The version does not exist.")
    item = activate_version(
        db,
        graph,
        version,
        source=payload.source,
        reason=payload.reason,
        idempotency_key=payload.idempotency_key,
    )
    return {
        "id": item.id,
        "learningGraphId": item.learning_graph_id,
        "fromVersionId": item.from_learning_graph_version_id,
        "toVersionId": item.to_learning_graph_version_id,
        "activatedAt": item.activated_at,
        "eventSequence": item.event_sequence,
    }


@router.get("/{graph_id}/activations")
async def graph_activations(
    graph_id: str,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    _graph(db, graph_id)
    return [
        {
            "id": item.id,
            "fromVersionId": item.from_learning_graph_version_id,
            "toVersionId": item.to_learning_graph_version_id,
            "activatedAt": item.activated_at,
            "source": item.source,
            "reason": item.reason,
            "eventSequence": item.event_sequence,
        }
        for item in db.scalars(
            select(LearningGraphActivationEvent)
            .where(LearningGraphActivationEvent.learning_graph_id == graph_id)
            .order_by(LearningGraphActivationEvent.event_sequence)
        ).all()
    ]


@router.get("/{graph_id}/versions/{version_id}/satisfaction")
async def graph_satisfaction(
    graph_id: str,
    version_id: str,
    cutoff_at: int | None = Query(default=None),
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _graph(db, graph_id)
    version = db.get(LearningGraphVersion, version_id)
    if version is None or version.learning_graph_id != graph_id:
        raise AppError(404, "LEARNING_GRAPH_VERSION_NOT_FOUND", "The version does not exist.")
    cutoff = cutoff_at if cutoff_at is not None else utc_now_ms() + 1
    return {
        "learningGraphVersionId": version.id,
        "cutoffAt": cutoff,
        "cutoffSemantics": "exclusive",
        "edges": satisfaction_for_version(db, version.id, cutoff),
    }
