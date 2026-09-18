from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth import AuthContext, get_auth_context, require_csrf
from app.database import get_db
from app.errors import AppError
from app.roadmap_projection.contracts import PositionOverrideInput, ProjectionPreferenceInput
from app.roadmap_projection.models import (
    RoadmapNodePositionOverride,
    RoadmapProjectionPreference,
)
from app.roadmap_projection.service import (
    build_projection,
    cached_projection,
    drain_projection_invalidations,
    enqueue_projection_invalidation,
    rebuild_projection,
)
from app.time_utils import utc_now_ms

router = APIRouter(prefix="/roadmap-projection", tags=["v2 roadmap projection"])


def _require_scope(db: Session, scope_key: str) -> dict[str, Any]:
    projection = build_projection(db)
    if not projection.get("configured") or projection["scopeKey"] != scope_key:
        raise AppError(
            409, "ROADMAP_PROJECTION_SCOPE_INVALID", "The projection scope is not active."
        )
    return projection


@router.get("/current")
async def current_projection(
    _auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return cached_projection(db)


@router.post("/rebuild")
async def rebuild_current_projection(
    cutoff_at: int | None = Query(default=None),
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    result = rebuild_projection(db, cutoff_at=cutoff_at)
    db.commit()
    if cutoff_at is None:
        drain_projection_invalidations(db, recover_running=True)
        return cached_projection(db)
    return result


@router.put("/{scope_key}/positions/{node_key}")
async def set_position(
    scope_key: str,
    node_key: str,
    payload: PositionOverrideInput,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    projection = _require_scope(db, scope_key)
    if payload.node_key != node_key or node_key not in {
        item["nodeKey"] for item in projection["nodes"]
    }:
        raise AppError(422, "ROADMAP_POSITION_NODE_INVALID", "The projection node is invalid.")
    item = db.scalar(
        select(RoadmapNodePositionOverride).where(
            RoadmapNodePositionOverride.scope_key == scope_key,
            RoadmapNodePositionOverride.node_key == node_key,
        )
    )
    now = utc_now_ms()
    if item is None:
        item = RoadmapNodePositionOverride(
            scope_key=scope_key,
            node_key=node_key,
            position_x=payload.position_x,
            position_y=payload.position_y,
            provenance="user_override",
            created_at=now,
            updated_at=now,
        )
        db.add(item)
    else:
        item.position_x = payload.position_x
        item.position_y = payload.position_y
        item.updated_at = now
    db.flush()
    enqueue_projection_invalidation(
        db,
        subject_type="roadmap_projection_scope",
        subject_id=scope_key,
        source_fact_id=f"position:{item.id}:{item.updated_at}",
    )
    db.commit()
    return {
        "id": item.id,
        "scopeKey": scope_key,
        "nodeKey": node_key,
        "position": {"x": item.position_x, "y": item.position_y},
        "provenance": item.provenance,
    }


@router.delete("/{scope_key}/positions")
async def reset_positions(
    scope_key: str,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_scope(db, scope_key)
    result = db.execute(
        delete(RoadmapNodePositionOverride).where(
            RoadmapNodePositionOverride.scope_key == scope_key
        )
    )
    enqueue_projection_invalidation(
        db,
        subject_type="roadmap_projection_scope",
        subject_id=scope_key,
        source_fact_id=f"reset:{utc_now_ms()}",
    )
    db.commit()
    drain_projection_invalidations(db, recover_running=True)
    return {
        "clearedCount": int(getattr(result, "rowcount", 0) or 0),
        "projection": cached_projection(db),
    }


@router.put("/{scope_key}/preferences")
async def set_preferences(
    scope_key: str,
    payload: ProjectionPreferenceInput,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_scope(db, scope_key)
    item = db.get(RoadmapProjectionPreference, scope_key)
    if item is None:
        item = RoadmapProjectionPreference(scope_key=scope_key)
        db.add(item)
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    item.updated_at = utc_now_ms()
    db.flush()
    enqueue_projection_invalidation(
        db,
        subject_type="roadmap_projection_scope",
        subject_id=scope_key,
        source_fact_id=f"preference:{item.updated_at}",
    )
    db.commit()
    drain_projection_invalidations(db, recover_running=True)
    return dict(cached_projection(db)["relationshipVisibility"])
