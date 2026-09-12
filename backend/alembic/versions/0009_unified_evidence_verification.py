"""Add unified Evidence and normalize preserved V1 sources."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0009_unified_evidence_verification"
down_revision = "0008_activity_session_constraint"
branch_labels = None
depends_on = None

NAMESPACE = uuid.UUID("c20b6799-bc36-5db4-92af-3eae1f16bcb3")
POLICY = "evidence-policy/v1"
BACKFILL_POLICY = "unified-evidence-legacy-backfill/v1"
RUN_ID = str(uuid.uuid5(NAMESPACE, f"backfill-run:evidence:{BACKFILL_POLICY}"))
RECORDED_AT = 1_788_912_000_000


def _stable_id(value: str) -> str:
    return str(uuid.uuid5(NAMESPACE, value))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _rows_hash(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(_canonical_json(rows).encode("utf-8")).hexdigest()


def _result_rows_hash(rows: list[dict[str, Any]]) -> str:
    auditable_rows: list[dict[str, Any]] = []
    for row in rows:
        projected = dict(row)
        if "evidence_type" in projected:
            projected.pop("description", None)
            projected.pop("external_reference", None)
        auditable_rows.append(projected)
    return _rows_hash(auditable_rows)


def upgrade() -> None:
    op.create_table(
        "evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("evidence_type", sa.String(24), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("source_role", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("strength", sa.String(16), nullable=False),
        sa.Column("strength_unknown_reason", sa.String(64), nullable=True),
        sa.Column("independence", sa.String(24), nullable=False),
        sa.Column("independence_unknown_reason", sa.String(64), nullable=True),
        sa.Column("source_confidence", sa.String(16), nullable=False),
        sa.Column("source_confidence_unknown_reason", sa.String(64), nullable=True),
        sa.Column("occurred_at", sa.Integer(), nullable=True),
        sa.Column("occurred_at_unknown_reason", sa.String(64), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("provenance_json", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("artifact_hash", sa.String(64), nullable=True),
        sa.Column("external_reference", sa.Text(), nullable=True),
        sa.Column("supersedes_evidence_id", sa.String(36), nullable=True),
        sa.Column("authoritative_for_downgrade", sa.Boolean(), nullable=False),
        sa.UniqueConstraint(
            "source_type",
            "source_id",
            "source_role",
            "policy_version",
            name="uq_evidence_source_role_policy",
        ),
        sa.UniqueConstraint("supersedes_evidence_id", name="uq_evidence_supersedes"),
        sa.CheckConstraint(
            "evidence_type IN ('session','verification','project','code','assessment',"
            "'manual','imported','review')",
            name="ck_evidence_type",
        ),
        sa.CheckConstraint(
            "strength IN ('unknown','weak','moderate','strong')", name="ck_evidence_strength"
        ),
        sa.CheckConstraint(
            "independence IN ('unknown','guided','assisted','independent','not_applicable')",
            name="ck_evidence_independence",
        ),
        sa.CheckConstraint(
            "source_confidence IN ('unknown','low','medium','high')",
            name="ck_evidence_source_confidence",
        ),
        sa.CheckConstraint(
            "(occurred_at IS NULL) = (occurred_at_unknown_reason IS NOT NULL)",
            name="ck_evidence_occurred_unknown_reason",
        ),
        sa.CheckConstraint(
            "(strength = 'unknown') = (strength_unknown_reason IS NOT NULL)",
            name="ck_evidence_strength_unknown_reason",
        ),
        sa.CheckConstraint(
            "(independence = 'unknown') = (independence_unknown_reason IS NOT NULL)",
            name="ck_evidence_independence_unknown_reason",
        ),
        sa.CheckConstraint(
            "(source_confidence = 'unknown') = (source_confidence_unknown_reason IS NOT NULL)",
            name="ck_evidence_source_confidence_unknown_reason",
        ),
        sa.ForeignKeyConstraint(["supersedes_evidence_id"], ["evidence.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_evidence_source", "evidence", ["source_type", "source_id"])
    op.create_index("ix_evidence_created", "evidence", ["created_at"])
    op.create_table(
        "evidence_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("evidence_id", sa.String(36), nullable=False),
        sa.Column("source_contribution_id", sa.String(36), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("competency_identity_id", sa.String(36), nullable=False),
        sa.Column("criterion_identity_id", sa.String(36), nullable=True),
        sa.Column("criterion_definition_id", sa.String(36), nullable=True),
        sa.Column("scale_version_id", sa.String(36), nullable=True),
        sa.Column("dimension_id", sa.String(36), nullable=True),
        sa.Column("level_id", sa.String(36), nullable=True),
        sa.Column("effect", sa.String(16), nullable=False),
        sa.Column("relevance", sa.String(16), nullable=False),
        sa.Column("provenance_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "evidence_id",
            "source_contribution_id",
            name="uq_evidence_link_source_contribution",
        ),
        sa.UniqueConstraint("evidence_id", "idempotency_key", name="uq_evidence_link_idempotency"),
        sa.CheckConstraint(
            "effect IN ('supports','contradicts','context_only')", name="ck_evidence_link_effect"
        ),
        sa.CheckConstraint(
            "relevance IN ('primary','secondary','supporting')", name="ck_evidence_link_relevance"
        ),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["source_contribution_id"], ["session_contributions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["competency_identity_id"], ["competency_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["criterion_identity_id"], ["criterion_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["criterion_definition_id"], ["criterion_definitions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["scale_version_id"], ["capability_scale_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["dimension_id"], ["capability_scale_dimensions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["level_id"], ["capability_scale_levels.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "ix_evidence_link_competency",
        "evidence_links",
        ["competency_identity_id", "criterion_identity_id"],
    )
    _create_lifecycle_tables()
    _backfill(op.get_bind())


def _create_lifecycle_tables() -> None:
    for table_name, target_column, target_table in (
        ("evidence_retractions", "evidence_id", "evidence"),
        ("evidence_invalidations", "evidence_id", "evidence"),
    ):
        columns = [
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(target_column, sa.String(36), nullable=False, unique=True),
        ]
        if table_name == "evidence_retractions":
            columns.append(sa.Column("replacement_evidence_id", sa.String(36), nullable=True))
        columns.extend(
            [
                sa.Column("reason", sa.Text(), nullable=False),
                sa.Column("actor_kind", sa.String(64), nullable=False),
                sa.Column("created_at", sa.Integer(), nullable=False),
                sa.ForeignKeyConstraint(
                    [target_column], [f"{target_table}.id"], ondelete="RESTRICT"
                ),
            ]
        )
        if table_name == "evidence_retractions":
            columns.append(
                sa.ForeignKeyConstraint(
                    ["replacement_evidence_id"], ["evidence.id"], ondelete="RESTRICT"
                )
            )
        op.create_table(table_name, *columns)
    op.create_table(
        "evidence_link_retractions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("evidence_link_id", sa.String(36), nullable=False, unique=True),
        sa.Column("replacement_link_id", sa.String(36), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_kind", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["evidence_link_id"], ["evidence_links.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["replacement_link_id"], ["evidence_links.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "evidence_redactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("evidence_id", sa.String(36), nullable=False, unique=True),
        sa.Column("redacted_fields_json", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_kind", sa.String(64), nullable=False),
        sa.Column("effect", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"], ondelete="RESTRICT"),
    )


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
        "policy_version": POLICY,
        "schema_version": 1,
        "artifact_hash": None,
        "external_reference": None,
        "supersedes_evidence_id": None,
        "authoritative_for_downgrade": False,
        **values,
    }


def _backfill(connection: sa.Connection) -> None:
    verification_records = [
        dict(row)
        for row in connection.execute(
            sa.text("SELECT * FROM verification_records ORDER BY id")
        ).mappings()
    ]
    verification_context = [
        dict(row)
        for row in connection.execute(
            sa.text("SELECT * FROM verification_evidence ORDER BY id")
        ).mappings()
    ]
    sessions = [
        dict(row)
        for row in connection.execute(
            sa.text("SELECT * FROM learning_sessions ORDER BY id")
        ).mappings()
    ]
    contributions = [
        dict(row)
        for row in connection.execute(
            sa.text(
                "SELECT sc.* FROM session_contributions sc "
                "LEFT JOIN contribution_retractions cr ON cr.contribution_id=sc.id "
                "WHERE cr.id IS NULL ORDER BY sc.id"
            )
        ).mappings()
    ]
    records_by_id = {row["id"]: row for row in verification_records}
    evidence_rows: list[dict[str, Any]] = []
    link_rows: list[dict[str, Any]] = []
    for record in verification_records:
        evidence_id = _stable_id(
            f"evidence:verification_record:{record['id']}:result:{BACKFILL_POLICY}"
        )
        evidence_rows.append(
            _base_evidence(
                id=evidence_id,
                evidence_type="verification",
                source_type="verification_record",
                source_id=record["id"],
                source_role="result",
                title=f"Legacy verification: {record['method']}",
                description=record["evidence_summary"],
                created_at=record["created_at"],
                provenance_json=_canonical_json(
                    {
                        "origin_kind": "local",
                        "creator_kind": "legacy_source",
                        "source_record_type": "verification_record",
                        "source_record_id": record["id"],
                        "capture_method": "deterministic_legacy_backfill",
                        "policy_version": BACKFILL_POLICY,
                        "description_hash": _rows_hash([{"value": record["evidence_summary"]}]),
                        "external_reference_hash": _rows_hash([{"value": None}]),
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
        link_rows.append(
            {
                "id": _stable_id(
                    f"evidence-link:{evidence_id}:{record['competency_identity_id']}:result"
                ),
                "evidence_id": evidence_id,
                "source_contribution_id": None,
                "idempotency_key": None,
                "competency_identity_id": record["competency_identity_id"],
                "criterion_identity_id": None,
                "criterion_definition_id": None,
                "scale_version_id": None,
                "dimension_id": None,
                "level_id": None,
                "effect": "contradicts" if record["result"] == "failed" else "supports",
                "relevance": "primary",
                "provenance_json": _canonical_json(
                    {
                        "capture_method": "deterministic_legacy_backfill",
                        "policy_version": BACKFILL_POLICY,
                        "evidence_id": evidence_id,
                        "source_result": record["result"],
                    }
                ),
                "created_at": record["created_at"],
            }
        )
    for context in verification_context:
        record = records_by_id[context["verification_record_id"]]
        evidence_id = _stable_id(
            f"evidence:verification_evidence:{context['id']}:context:{BACKFILL_POLICY}"
        )
        evidence_rows.append(
            _base_evidence(
                id=evidence_id,
                evidence_type="verification",
                source_type="verification_evidence",
                source_id=context["id"],
                source_role="context",
                title=f"Legacy verification attachment: {context['kind']}",
                description=context["description"],
                external_reference=context["reference"],
                created_at=context["created_at"],
                provenance_json=_canonical_json(
                    {
                        "origin_kind": "local",
                        "creator_kind": "legacy_source",
                        "source_record_type": "verification_evidence",
                        "source_record_id": context["id"],
                        "verification_record_id": record["id"],
                        "capture_method": "deterministic_legacy_backfill",
                        "policy_version": BACKFILL_POLICY,
                        "description_hash": _rows_hash([{"value": context["description"]}]),
                        "external_reference_hash": _rows_hash([{"value": context["reference"]}]),
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
        link_rows.append(
            {
                "id": _stable_id(
                    f"evidence-link:{evidence_id}:{record['competency_identity_id']}:context"
                ),
                "evidence_id": evidence_id,
                "source_contribution_id": None,
                "idempotency_key": None,
                "competency_identity_id": record["competency_identity_id"],
                "criterion_identity_id": None,
                "criterion_definition_id": None,
                "scale_version_id": None,
                "dimension_id": None,
                "level_id": None,
                "effect": "context_only",
                "relevance": "supporting",
                "provenance_json": _canonical_json(
                    {
                        "capture_method": "deterministic_legacy_backfill",
                        "policy_version": BACKFILL_POLICY,
                        "evidence_id": evidence_id,
                        "verification_record_id": record["id"],
                    }
                ),
                "created_at": context["created_at"],
            }
        )
    contributions_by_session: dict[str, list[dict[str, Any]]] = {}
    for contribution in contributions:
        contributions_by_session.setdefault(contribution["session_id"], []).append(contribution)
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
        evidence_id = _stable_id(
            f"evidence:learning_session:{session['id']}:session:{BACKFILL_POLICY}"
        )
        performance = session["activity_type"] in performance_categories
        evidence_rows.append(
            _base_evidence(
                id=evidence_id,
                evidence_type="session",
                source_type="learning_session",
                source_id=session["id"],
                source_role="session_result",
                title="Legacy learning session",
                description=None,
                occurred_at=session["started_at"],
                occurred_at_unknown_reason=None,
                independence=independence_map[session["assistance_mode"]]
                if performance
                else "not_applicable",
                independence_unknown_reason=None,
                created_at=session["created_at"],
                provenance_json=_canonical_json(
                    {
                        "origin_kind": "local",
                        "creator_kind": "legacy_source",
                        "source_record_type": "learning_session",
                        "source_record_id": session["id"],
                        "capture_method": "deterministic_legacy_backfill",
                        "policy_version": BACKFILL_POLICY,
                        "description_hash": _rows_hash([{"value": None}]),
                        "external_reference_hash": _rows_hash([{"value": None}]),
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
        for contribution in contributions_by_session.get(session["id"], []):
            link_rows.append(
                {
                    "id": _stable_id(
                        f"evidence-link:{evidence_id}:session-contribution:{contribution['id']}"
                    ),
                    "evidence_id": evidence_id,
                    "source_contribution_id": contribution["id"],
                    "idempotency_key": None,
                    "competency_identity_id": contribution["competency_identity_id"],
                    "criterion_identity_id": contribution["criterion_identity_id"],
                    "criterion_definition_id": None,
                    "scale_version_id": None,
                    "dimension_id": None,
                    "level_id": None,
                    "effect": "supports",
                    "relevance": contribution["relevance"],
                    "provenance_json": _canonical_json(
                        {
                            "capture_method": "deterministic_legacy_backfill",
                            "policy_version": BACKFILL_POLICY,
                            "evidence_id": evidence_id,
                            "session_contribution_id": contribution["id"],
                        }
                    ),
                    "created_at": session["created_at"],
                }
            )
    evidence_table = sa.Table("evidence", sa.MetaData(), autoload_with=connection)
    link_table = sa.Table("evidence_links", sa.MetaData(), autoload_with=connection)
    if evidence_rows:
        connection.execute(evidence_table.insert(), evidence_rows)
    if link_rows:
        connection.execute(link_table.insert(), link_rows)
    source_rows = sorted(
        verification_records + verification_context + sessions + contributions,
        key=lambda row: (str(row.get("id")), len(row)),
    )
    result_rows = sorted(evidence_rows + link_rows, key=lambda row: (str(row["id"]), len(row)))
    connection.execute(
        sa.text(
            "INSERT INTO migration_backfill_runs "
            "(id,policy_key,source_kind,source_row_count,result_row_count,source_hash,"
            "result_hash,recorded_at) VALUES "
            "(:id,:policy,'v1_evidence_sources',:source_count,:result_count,:source_hash,"
            ":result_hash,:recorded_at)"
        ),
        {
            "id": RUN_ID,
            "policy": BACKFILL_POLICY,
            "source_count": len(source_rows),
            "result_count": len(result_rows),
            "source_hash": _rows_hash(source_rows),
            "result_hash": _result_rows_hash(result_rows),
            "recorded_at": RECORDED_AT,
        },
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("DELETE FROM migration_backfill_runs WHERE id=:id"), {"id": RUN_ID})
    op.drop_table("evidence_redactions")
    op.drop_table("evidence_link_retractions")
    op.drop_table("evidence_invalidations")
    op.drop_table("evidence_retractions")
    op.drop_index("ix_evidence_link_competency", table_name="evidence_links")
    op.drop_table("evidence_links")
    op.drop_index("ix_evidence_created", table_name="evidence")
    op.drop_index("ix_evidence_source", table_name="evidence")
    op.drop_table("evidence")
