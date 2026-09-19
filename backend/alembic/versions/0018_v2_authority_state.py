"""Add monotonic V2 learning-control authority state."""

from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision = "0018_v2_authority_state"
down_revision = "0017_today_v2"
branch_labels = None
depends_on = None


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def upgrade() -> None:
    op.create_table(
        "learning_control_authority_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_sequence", sa.Integer(), nullable=False, unique=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("command_type", sa.String(32), nullable=False),
        sa.Column("prior_state_json", sa.Text()),
        sa.Column("resulting_state_json", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(16), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("event_sequence > 0", name="ck_authority_event_sequence"),
        sa.CheckConstraint(
            "command_type IN ('bootstrap','activate_v2','surface_change')",
            name="ck_authority_event_command",
        ),
        sa.CheckConstraint("actor IN ('system','user')", name="ck_authority_event_actor"),
        sa.CheckConstraint("length(reason) > 0", name="ck_authority_event_reason"),
        sa.CheckConstraint("length(payload_hash) = 64", name="ck_authority_event_hash"),
    )
    op.create_table(
        "learning_control_authority_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("canonical_learning_authority", sa.String(16), nullable=False),
        sa.Column("roadmap_presentation", sa.String(24), nullable=False),
        sa.Column("recommendation_presentation", sa.String(24), nullable=False),
        sa.Column("today_presentation", sa.String(24), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("last_event_id", sa.String(36), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["last_event_id"], ["learning_control_authority_events.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("id = 1", name="ck_authority_state_singleton"),
        sa.CheckConstraint("event_sequence > 0", name="ck_authority_state_sequence"),
        sa.CheckConstraint(
            "canonical_learning_authority IN ('legacy_v1','v2')",
            name="ck_authority_canonical",
        ),
        sa.CheckConstraint(
            "roadmap_presentation IN ('legacy_v1','v2','v1_read_only')",
            name="ck_authority_roadmap_surface",
        ),
        sa.CheckConstraint(
            "recommendation_presentation IN ('legacy_v1','v2','v1_read_only')",
            name="ck_authority_recommendation_surface",
        ),
        sa.CheckConstraint(
            "today_presentation IN ('legacy_v1','v2','v1_read_only')",
            name="ck_authority_today_surface",
        ),
        sa.CheckConstraint("length(state_hash) = 64", name="ck_authority_state_hash"),
        sa.CheckConstraint(
            "(canonical_learning_authority = 'legacy_v1' AND "
            "roadmap_presentation = 'legacy_v1' AND "
            "recommendation_presentation = 'legacy_v1' AND "
            "today_presentation = 'legacy_v1') OR "
            "(canonical_learning_authority = 'v2' AND "
            "roadmap_presentation IN ('v2','v1_read_only') AND "
            "recommendation_presentation IN ('v2','v1_read_only') AND "
            "today_presentation IN ('v2','v1_read_only'))",
            name="ck_authority_state_semantics",
        ),
    )
    state = {
        "canonicalLearningAuthority": "legacy_v1",
        "recommendationPresentation": "legacy_v1",
        "roadmapPresentation": "legacy_v1",
        "todayPresentation": "legacy_v1",
    }
    payload = {
        "commandType": "bootstrap",
        "reason": "Initial legacy authority baseline",
        "resultingState": state,
    }
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "INSERT INTO learning_control_authority_events "
            "(id,event_sequence,idempotency_key,command_type,prior_state_json,"
            "resulting_state_json,reason,actor,source,payload_hash,occurred_at) "
            "VALUES (:id,1,:key,'bootstrap',NULL,:state,:reason,'system','migration',:hash,0)"
        ),
        {
            "id": "authority-bootstrap-legacy-v1",
            "key": "authority-bootstrap-legacy-v1",
            "state": _canonical(state),
            "reason": payload["reason"],
            "hash": _hash(payload),
        },
    )
    connection.execute(
        sa.text(
            "INSERT INTO learning_control_authority_state "
            "(id,canonical_learning_authority,roadmap_presentation,recommendation_presentation,"
            "today_presentation,event_sequence,last_event_id,state_hash,updated_at) "
            "VALUES (1,'legacy_v1','legacy_v1','legacy_v1','legacy_v1',1,:event,:hash,0)"
        ),
        {"event": "authority-bootstrap-legacy-v1", "hash": _hash(state)},
    )


def downgrade() -> None:
    connection = op.get_bind()
    state = (
        connection.execute(
            sa.text(
                "SELECT id,canonical_learning_authority,roadmap_presentation,"
                "recommendation_presentation,today_presentation,event_sequence,last_event_id,"
                "state_hash,updated_at FROM "
                "learning_control_authority_state WHERE id=1"
            )
        )
        .mappings()
        .first()
    )
    baseline = {
        "canonicalLearningAuthority": "legacy_v1",
        "recommendationPresentation": "legacy_v1",
        "roadmapPresentation": "legacy_v1",
        "todayPresentation": "legacy_v1",
    }
    event = (
        connection.execute(
            sa.text(
                "SELECT id,event_sequence,idempotency_key,command_type,prior_state_json,"
                "resulting_state_json,reason,actor,source,payload_hash,occurred_at "
                "FROM learning_control_authority_events ORDER BY event_sequence"
            )
        )
        .mappings()
        .all()
    )
    expected_payload = {
        "commandType": "bootstrap",
        "reason": "Initial legacy authority baseline",
        "resultingState": baseline,
    }
    if (
        state is None
        or state["id"] != 1
        or state["canonical_learning_authority"] != "legacy_v1"
        or state["roadmap_presentation"] != "legacy_v1"
        or state["recommendation_presentation"] != "legacy_v1"
        or state["today_presentation"] != "legacy_v1"
        or state["event_sequence"] != 1
        or state["last_event_id"] != "authority-bootstrap-legacy-v1"
        or state["state_hash"] != _hash(baseline)
        or state["updated_at"] != 0
        or len(event) != 1
        or event[0]["id"] != "authority-bootstrap-legacy-v1"
        or event[0]["event_sequence"] != 1
        or event[0]["idempotency_key"] != "authority-bootstrap-legacy-v1"
        or event[0]["command_type"] != "bootstrap"
        or event[0]["prior_state_json"] is not None
        or event[0]["resulting_state_json"] != _canonical(baseline)
        or event[0]["reason"] != expected_payload["reason"]
        or event[0]["actor"] != "system"
        or event[0]["source"] != "migration"
        or event[0]["payload_hash"] != _hash(expected_payload)
        or event[0]["occurred_at"] != 0
    ):
        raise RuntimeError(
            "Authority history is populated; restore a verified pre-cutover backup instead."
        )
    op.drop_table("learning_control_authority_state")
    op.drop_table("learning_control_authority_events")
