from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

NAMESPACE = uuid.UUID("c20b6799-bc36-5db4-92af-3eae1f16bcb3")
EVIDENCE_POLICY = "evidence-policy/v1"
POLICY_KEY = "unified-evidence-legacy-backfill/v1"
RUN_ID = str(uuid.uuid5(NAMESPACE, f"backfill-run:evidence:{POLICY_KEY}"))
RECORDED_AT = 1_788_912_000_000


def stable_id(value: str) -> str:
    return str(uuid.uuid5(NAMESPACE, value))


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def rows_hash(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(canonical_json(rows).encode()).hexdigest()


def result_rows_hash(rows: list[dict[str, Any]]) -> str:
    auditable_rows: list[dict[str, Any]] = []
    for row in rows:
        projected = dict(row)
        if "evidence_type" in projected:
            projected.pop("description", None)
            projected.pop("external_reference", None)
        auditable_rows.append(projected)
    return rows_hash(auditable_rows)


def _base_evidence(**values: Any) -> dict[str, Any]:
    return {
        "description": None,
        "strength": "unknown",
        "strength_unknown_reason": "legacy_unspecified",
        "independence": "unknown",
        "independence_unknown_reason": "legacy_unspecified",
        "source_confidence": "unknown",
        "source_confidence_unknown_reason": "legacy_unspecified",
        "occurred_at": None,
        "occurred_at_unknown_reason": "legacy_unspecified",
        "policy_version": EVIDENCE_POLICY,
        "schema_version": 1,
        "artifact_hash": None,
        "external_reference": None,
        "supersedes_evidence_id": None,
        "authoritative_for_downgrade": False,
        **values,
    }


@dataclass(frozen=True)
class EvidenceBackfill:
    evidence: list[dict[str, Any]]
    links: list[dict[str, Any]]
    source_row_count: int
    source_hash: str
    result_hash: str


def build_evidence_backfill(
    tables: dict[str, list[dict[str, Any]]], *, import_package_id: str | None = None
) -> EvidenceBackfill:
    provenance_origin = "import" if import_package_id is not None else "local"
    provenance_creator = "import" if import_package_id is not None else "legacy_source"
    import_provenance = (
        {"import_package_id": import_package_id} if import_package_id is not None else {}
    )
    verification_records = sorted(
        (dict(row) for row in tables.get("verification_records", [])),
        key=lambda row: str(row["id"]),
    )
    verification_context = sorted(
        (dict(row) for row in tables.get("verification_evidence", [])),
        key=lambda row: str(row["id"]),
    )
    sessions = sorted(
        (dict(row) for row in tables.get("learning_sessions", [])),
        key=lambda row: str(row["id"]),
    )
    retracted_contributions = {
        str(row["contribution_id"]) for row in tables.get("contribution_retractions", [])
    }
    contributions = sorted(
        (
            dict(row)
            for row in tables.get("session_contributions", [])
            if str(row["id"]) not in retracted_contributions
        ),
        key=lambda row: str(row["id"]),
    )
    records_by_id = {str(row["id"]): row for row in verification_records}
    evidence_rows: list[dict[str, Any]] = []
    link_rows: list[dict[str, Any]] = []

    for record in verification_records:
        record_id = str(record["id"])
        evidence_id = stable_id(f"evidence:verification_record:{record_id}:result:{POLICY_KEY}")
        evidence_rows.append(
            _base_evidence(
                id=evidence_id,
                evidence_type="verification",
                source_type="verification_record",
                source_id=record_id,
                source_role="result",
                title=f"Legacy verification: {record['method']}",
                description=record["evidence_summary"],
                created_at=record["created_at"],
                provenance_json=canonical_json(
                    {
                        "origin_kind": provenance_origin,
                        "creator_kind": provenance_creator,
                        **import_provenance,
                        "source_record_type": "verification_record",
                        "source_record_id": record_id,
                        "capture_method": "deterministic_legacy_backfill",
                        "policy_version": POLICY_KEY,
                        "description_hash": rows_hash([{"value": record["evidence_summary"]}]),
                        "external_reference_hash": rows_hash([{"value": None}]),
                        "original_attributes": {
                            "verification_source": record["verification_source"],
                            "method": record["method"],
                            "result": record["result"],
                            "confidence": record["confidence"],
                        },
                        "legacy_status": "preserved",
                        "missing_field_reasons": {
                            "occurred_at": "legacy_unspecified",
                            "strength": "legacy_unspecified",
                            "independence": "legacy_unspecified",
                            "source_confidence": "legacy_unspecified",
                        },
                    }
                ),
            )
        )
        competency_id = str(record["competency_identity_id"])
        link_rows.append(
            {
                "id": stable_id(f"evidence-link:{evidence_id}:{competency_id}:result"),
                "evidence_id": evidence_id,
                "source_contribution_id": None,
                "idempotency_key": None,
                "competency_identity_id": competency_id,
                "criterion_identity_id": None,
                "criterion_definition_id": None,
                "scale_version_id": None,
                "dimension_id": None,
                "level_id": None,
                "effect": "contradicts" if record["result"] == "failed" else "supports",
                "relevance": "primary",
                "provenance_json": canonical_json(
                    {
                        "capture_method": "deterministic_legacy_backfill",
                        "policy_version": POLICY_KEY,
                        "evidence_id": evidence_id,
                        "source_result": record["result"],
                    }
                ),
                "created_at": record["created_at"],
            }
        )

    for context in verification_context:
        context_id = str(context["id"])
        record = records_by_id[str(context["verification_record_id"])]
        evidence_id = stable_id(f"evidence:verification_evidence:{context_id}:context:{POLICY_KEY}")
        evidence_rows.append(
            _base_evidence(
                id=evidence_id,
                evidence_type="verification",
                source_type="verification_evidence",
                source_id=context_id,
                source_role="context",
                title=f"Legacy verification attachment: {context['kind']}",
                description=context["description"],
                external_reference=context["reference"],
                created_at=context["created_at"],
                provenance_json=canonical_json(
                    {
                        "origin_kind": provenance_origin,
                        "creator_kind": provenance_creator,
                        **import_provenance,
                        "source_record_type": "verification_evidence",
                        "source_record_id": context_id,
                        "verification_record_id": record["id"],
                        "capture_method": "deterministic_legacy_backfill",
                        "policy_version": POLICY_KEY,
                        "description_hash": rows_hash([{"value": context["description"]}]),
                        "external_reference_hash": rows_hash([{"value": context["reference"]}]),
                        "original_attributes": {"kind": context["kind"]},
                        "legacy_status": "preserved",
                        "missing_field_reasons": {
                            "occurred_at": "legacy_unspecified",
                            "strength": "legacy_unspecified",
                            "independence": "legacy_unspecified",
                            "source_confidence": "legacy_unspecified",
                        },
                    }
                ),
            )
        )
        competency_id = str(record["competency_identity_id"])
        link_rows.append(
            {
                "id": stable_id(f"evidence-link:{evidence_id}:{competency_id}:context"),
                "evidence_id": evidence_id,
                "source_contribution_id": None,
                "idempotency_key": None,
                "competency_identity_id": competency_id,
                "criterion_identity_id": None,
                "criterion_definition_id": None,
                "scale_version_id": None,
                "dimension_id": None,
                "level_id": None,
                "effect": "context_only",
                "relevance": "supporting",
                "provenance_json": canonical_json(
                    {
                        "capture_method": "deterministic_legacy_backfill",
                        "policy_version": POLICY_KEY,
                        "evidence_id": evidence_id,
                        "verification_record_id": record["id"],
                    }
                ),
                "created_at": context["created_at"],
            }
        )

    contributions_by_session: dict[str, list[dict[str, Any]]] = {}
    for contribution in contributions:
        contributions_by_session.setdefault(str(contribution["session_id"]), []).append(
            contribution
        )
    performance_categories = {
        "practice",
        "coding",
        "debugging",
        "project",
        "review",
        "verification",
    }
    independence_map = {
        "none": "independent",
        "docs_only": "independent",
        "ai_hint": "assisted",
        "ai_assisted": "assisted",
        "agent_led": "guided",
    }
    for session in sessions:
        if (
            session["duration_ms"] is None
            or session["duration_ms"] <= 0
            or session["outcome"] not in {"completed", "partial"}
            or session["timed_state"] == "cancelled"
            or session["tombstoned_at"] is not None
        ):
            continue
        session_id = str(session["id"])
        evidence_id = stable_id(f"evidence:learning_session:{session_id}:session:{POLICY_KEY}")
        performance = session["activity_type"] in performance_categories
        evidence_rows.append(
            _base_evidence(
                id=evidence_id,
                evidence_type="session",
                source_type="learning_session",
                source_id=session_id,
                source_role="session_result",
                title="Legacy learning session",
                occurred_at=session["started_at"],
                occurred_at_unknown_reason=None,
                independence=(
                    independence_map[session["assistance_mode"]]
                    if performance
                    else "not_applicable"
                ),
                independence_unknown_reason=None,
                created_at=session["created_at"],
                provenance_json=canonical_json(
                    {
                        "origin_kind": provenance_origin,
                        "creator_kind": provenance_creator,
                        **import_provenance,
                        "source_record_type": "learning_session",
                        "source_record_id": session_id,
                        "capture_method": "deterministic_legacy_backfill",
                        "policy_version": POLICY_KEY,
                        "description_hash": rows_hash([{"value": None}]),
                        "external_reference_hash": rows_hash([{"value": None}]),
                        "original_attributes": {
                            "activity_type": session["activity_type"],
                            "assistance_mode": session["assistance_mode"],
                            "outcome": session["outcome"],
                            "duration_ms": session["duration_ms"],
                        },
                        "legacy_status": "preserved",
                        "missing_field_reasons": {
                            "strength": "legacy_unspecified",
                            "source_confidence": "legacy_unspecified",
                        },
                    }
                ),
            )
        )
        for contribution in contributions_by_session.get(session_id, []):
            contribution_id = str(contribution["id"])
            link_rows.append(
                {
                    "id": stable_id(
                        f"evidence-link:{evidence_id}:session-contribution:{contribution_id}"
                    ),
                    "evidence_id": evidence_id,
                    "source_contribution_id": contribution_id,
                    "idempotency_key": None,
                    "competency_identity_id": contribution["competency_identity_id"],
                    "criterion_identity_id": contribution["criterion_identity_id"],
                    "criterion_definition_id": None,
                    "scale_version_id": None,
                    "dimension_id": None,
                    "level_id": None,
                    "effect": "supports",
                    "relevance": contribution["relevance"],
                    "provenance_json": canonical_json(
                        {
                            "capture_method": "deterministic_legacy_backfill",
                            "policy_version": POLICY_KEY,
                            "evidence_id": evidence_id,
                            "session_contribution_id": contribution_id,
                        }
                    ),
                    "created_at": session["created_at"],
                }
            )

    source_rows = sorted(
        verification_records + verification_context + sessions + contributions,
        key=lambda row: (str(row.get("id")), len(row)),
    )
    result_rows = sorted(
        evidence_rows + link_rows,
        key=lambda row: (str(row["id"]), len(row)),
    )
    return EvidenceBackfill(
        evidence=evidence_rows,
        links=link_rows,
        source_row_count=len(source_rows),
        source_hash=rows_hash(source_rows),
        result_hash=result_rows_hash(result_rows),
    )
