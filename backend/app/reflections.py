from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import AuthContext, get_auth_context, require_csrf
from app.database import get_db
from app.models import DailyReflection
from app.schemas import ReflectionUpsert
from app.time_utils import epoch_ms_to_rfc3339

router = APIRouter(prefix="/reflections", tags=["reports"])


@router.put("/{local_date}")
async def upsert_reflection(
    local_date: date,
    payload: ReflectionUpsert,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    local_date_value = local_date.isoformat()
    item = db.scalar(select(DailyReflection).where(DailyReflection.local_date == local_date_value))
    if item is None:
        item = DailyReflection(local_date=local_date_value, text=payload.text)
        db.add(item)
    else:
        item.text = payload.text
    db.commit()
    return {
        "id": item.id,
        "localDate": item.local_date,
        "text": item.text,
        "updatedAt": epoch_ms_to_rfc3339(item.updated_at),
    }


@router.get("/{local_date}")
async def get_reflection(
    local_date: date,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, object | None]:
    item = db.scalar(
        select(DailyReflection).where(DailyReflection.local_date == local_date.isoformat())
    )
    return {
        "reflection": None
        if item is None
        else {
            "id": item.id,
            "localDate": item.local_date,
            "text": item.text,
            "updatedAt": epoch_ms_to_rfc3339(item.updated_at),
        }
    }
