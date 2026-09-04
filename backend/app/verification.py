from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import AuthContext, get_auth_context, require_csrf
from app.database import get_db
from app.domain import transition_status
from app.errors import AppError
from app.models import VerificationEvidence, VerificationRecord
from app.schemas import VerificationCreate
from app.time_utils import datetime_to_epoch_ms, epoch_ms_to_rfc3339

router = APIRouter(prefix="/verification", tags=["verification"])


@router.post("", status_code=201)
async def create_verification(
    payload: VerificationCreate,
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    record = VerificationRecord(
        competency_identity_id=payload.competency_identity_id,
        verification_source=payload.verification_source,
        method=payload.method,
        result=payload.result,
        confidence=payload.confidence,
        reviewer_label=payload.reviewer_label,
        evidence_summary=payload.evidence_summary,
        notes=payload.notes,
    )
    db.add(record)
    db.flush()
    for evidence in payload.evidence:
        db.add(
            VerificationEvidence(
                verification_record_id=record.id,
                kind=evidence.kind,
                reference=evidence.reference,
                description=evidence.description,
            )
        )
    status = {"passed": "verified", "partial": "practicing", "failed": "needs_review"}[
        payload.result
    ]
    transition_status(
        db,
        payload.competency_identity_id,
        status,
        reason=f"Verification result: {payload.result}",
        source="verification",
        verification_record_id=record.id if payload.result == "passed" else None,
    )
    db.commit()
    return {"id": record.id, "result": record.result, "status": status}


@router.get("")
async def list_verification(
    competency_identity_id: str | None = None,
    source: Literal["self", "automated", "external"] | None = None,
    result: Literal["passed", "failed", "partial"] | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    query = select(VerificationRecord).order_by(VerificationRecord.created_at.desc())
    if competency_identity_id:
        query = query.where(VerificationRecord.competency_identity_id == competency_identity_id)
    if source:
        query = query.where(VerificationRecord.verification_source == source)
    if result:
        query = query.where(VerificationRecord.result == result)
    for name, value, comparison in (
        ("start_at", start_at, "start"),
        ("end_at", end_at, "end"),
    ):
        if value is None:
            continue
        if value.tzinfo is None or value.utcoffset() is None:
            raise AppError(
                422, "VERIFICATION_RANGE_INVALID", f"{name} must include a timezone offset."
            )
        instant = datetime_to_epoch_ms(value)
        query = query.where(
            VerificationRecord.created_at >= instant
            if comparison == "start"
            else VerificationRecord.created_at < instant
        )
    records = db.scalars(query.offset(offset).limit(limit)).all()
    evidence_by_record: dict[str, list[dict[str, str]]] = defaultdict(list)
    if records:
        for evidence in db.scalars(
            select(VerificationEvidence).where(
                VerificationEvidence.verification_record_id.in_([item.id for item in records])
            )
        ).all():
            evidence_by_record[evidence.verification_record_id].append(
                {
                    "kind": evidence.kind,
                    "reference": evidence.reference,
                    "description": evidence.description,
                }
            )
    return {
        "items": [
            {
                "id": item.id,
                "competencyIdentityId": item.competency_identity_id,
                "source": item.verification_source,
                "method": item.method,
                "result": item.result,
                "confidence": item.confidence,
                "reviewerLabel": item.reviewer_label,
                "evidenceSummary": item.evidence_summary,
                "notes": item.notes,
                "createdAt": epoch_ms_to_rfc3339(item.created_at),
                "evidence": evidence_by_record[item.id],
            }
            for item in records
        ],
        "limit": min(max(limit, 1), 200),
        "offset": max(offset, 0),
    }
