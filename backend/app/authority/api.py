from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import AuthContext, get_auth_context, require_csrf
from app.authority.contracts import AuthorityActivationRequest, AuthoritySurfaceRequest
from app.authority.service import (
    activate_v2,
    authority_history,
    authority_state,
    change_surfaces,
    readiness_report,
)
from app.config import Settings, get_settings_dependency
from app.database import get_db

router = APIRouter(prefix="/authority", tags=["v2 authority"])


@router.get("")
async def get_authority(
    _auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return authority_state(db)


@router.get("/readiness")
async def get_readiness(
    _auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return readiness_report(db)


@router.get("/history")
async def get_authority_history(
    _auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return authority_history(db)


@router.post("/activate-v2")
async def activate_authority(
    payload: AuthorityActivationRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dependency),
) -> dict[str, Any]:
    result = activate_v2(
        db,
        idempotency_key=payload.idempotency_key,
        reason=payload.reason,
        settings=settings,
    )
    db.commit()
    return result


@router.put("/surfaces")
async def update_surfaces(
    payload: AuthoritySurfaceRequest,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    result = change_surfaces(
        db,
        idempotency_key=payload.idempotency_key,
        roadmap_presentation=payload.roadmap_presentation,
        recommendation_presentation=payload.recommendation_presentation,
        today_presentation=payload.today_presentation,
        reason=payload.reason,
    )
    db.commit()
    return result
