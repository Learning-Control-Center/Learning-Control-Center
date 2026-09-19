from __future__ import annotations

import json
from collections.abc import Callable
from functools import wraps
from typing import Any, Concatenate, cast

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.determinism import canonical_json, content_hash
from app.errors import AppError
from app.models import Activity, DisciplineProfile, LearningSession, new_id
from app.recommendation.v2.public import (
    PublicRecommendationItemDTO,
    generate_public_recommendation_run,
    load_public_recommendation_item,
    thaw_public_json,
)
from app.schemas import SessionContributionCreate
from app.time_utils import epoch_ms_to_rfc3339, local_date_for_ms, local_day_bounds_ms, utc_now_ms
from app.today.contracts import TODAY_POLICY_VERSION, TODAY_PRESENTATION_VERSION
from app.today.models import (
    SuggestionActivityRelation,
    SuggestionActivityRelationCorrection,
    TodayGeneration,
    TodayInteraction,
    TodayInteractionCorrection,
    TodaySuggestion,
    TodaySuggestionCurrentState,
)
from app.v2_activities import create_activity_in_uow, start_timed_session_in_uow

TERMINAL_STATUSES = frozenset(
    {"completed", "partially_completed", "skipped", "replaced", "expired"}
)
VALID_TRANSITIONS = {
    "suggested": frozenset({"viewed", "accepted", "started", "skipped", "replaced", "expired"}),
    "viewed": frozenset({"accepted", "started", "skipped", "replaced", "expired"}),
    "accepted": frozenset({"started", "skipped", "replaced", "expired"}),
    "started": frozenset({"completed", "partially_completed", "replaced", "expired"}),
}
ROLE_ORDER = {"primary": 1, "complementary": 2, "maintenance": 3}
CATEGORY_BY_CANDIDATE_TYPE = {
    "curriculum_unit": "learning",
    "practice_task": "practice",
    "verification": "verification",
    "assessment": "verification",
    "review": "review",
    "maintenance": "review",
    "project_task": "project",
    "unblock_task": "learning",
}

def _translate_concurrent_conflict[**P, T](
    command: Callable[Concatenate[Session, P], T],
) -> Callable[Concatenate[Session, P], T]:
    @wraps(command)
    def wrapped(db: Session, *args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return command(db, *args, **kwargs)
        except (IntegrityError, OperationalError) as exc:
            db.rollback()
            raise AppError(
                409,
                "TODAY_CONCURRENT_CONFLICT",
                "Another Today or active-Session command won the concurrent race.",
            ) from exc

    return cast(Callable[Concatenate[Session, P], T], wrapped)


def _begin_immediate(db: Session) -> None:
    if not db.in_transaction():
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")


def _profile_timezone(db: Session) -> str:
    profile = db.get(DisciplineProfile, 1)
    if profile is None:
        raise AppError(
            409,
            "TODAY_CONFIGURATION_MISSING",
            "Discipline settings are not initialized.",
        )
    return profile.timezone


def _generation_request(
    *,
    analysis_snapshot_id: str,
    available_time_ms: int | None,
    context_costs: tuple[tuple[str, str, str], ...],
    regenerate: bool,
) -> dict[str, Any]:
    return {
        "analysisSnapshotId": analysis_snapshot_id,
        "availableTimeMs": available_time_ms,
        "contextCosts": [
            {"sourceType": source_type, "sourceEntityId": source_id, "cost": cost}
            for source_type, source_id, cost in sorted(context_costs)
        ],
        "regenerate": regenerate,
        "todayPolicyVersion": TODAY_POLICY_VERSION,
    }


def _suggestion_presentation(
    item: PublicRecommendationItemDTO,
) -> dict[str, Any]:
    return {
        "candidateType": item.candidate_type,
        "source": {
            "type": item.source_type,
            "entityId": item.source_entity_id,
            "versionId": item.source_version_id,
        },
        "candidateStableId": item.candidate_stable_id,
        "title": item.title,
        "description": item.description,
        "portfolioRole": item.portfolio_role,
        "rank": item.rank,
        "score": item.score,
        "reasonSummary": item.reason_summary,
        "reasons": [
            {
                "code": reason.code,
                "title": reason.title,
                "text": reason.text,
                "facts": thaw_public_json(reason.facts),
            }
            for reason in item.reasons
        ],
        "advisoryDurationMs": item.advisory_duration_ms,
        "durationRangeMs": (
            [
                item.duration_minimum_ms,
                item.duration_preferred_ms,
                item.duration_maximum_ms,
            ]
            if item.duration_minimum_ms is not None
            else None
        ),
    }


def _recommendation_idempotency_key(today_idempotency_key: str) -> str:
    return f"internal:today-v2:{content_hash({'todayIdempotencyKey': today_idempotency_key})}"


def _prepare_suggestion_rows(
    items: tuple[PublicRecommendationItemDTO, ...], *, expires_at: int
) -> tuple[
    list[tuple[PublicRecommendationItemDTO, dict[str, Any]]],
    list[dict[str, Any]],
]:
    prepared: list[tuple[PublicRecommendationItemDTO, dict[str, Any]]] = []
    output_rows: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda value: ROLE_ORDER[value.portfolio_role]):
        presentation = _suggestion_presentation(item)
        prepared.append((item, presentation))
        output_rows.append(
            {
                "ordinal": ROLE_ORDER[item.portfolio_role],
                "recommendationId": item.recommendation_id,
                "candidateId": item.candidate_id,
                "presentationHash": content_hash(presentation),
                "expiresAt": expires_at,
            }
        )
    return prepared, output_rows


def _state_for(db: Session, suggestion_id: str) -> TodaySuggestionCurrentState:
    state = db.get(TodaySuggestionCurrentState, suggestion_id)
    if state is None:
        raise AppError(409, "TODAY_STATE_MISSING", "The suggestion status projection is missing.")
    return state


def _interaction_payload(
    suggestion_id: str,
    interaction_type: str,
    *,
    actor: str,
    source: str,
    activity_id: str | None,
    session_id: str | None,
    replacement_suggestion_id: str | None,
    reason_code: str | None,
    feedback: str | None,
    command_facts: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "suggestionId": suggestion_id,
        "interactionType": interaction_type,
        "actor": actor,
        "source": source,
        "activityId": activity_id,
        "sessionId": session_id,
        "replacementSuggestionId": replacement_suggestion_id,
        "reasonCode": reason_code,
        "feedback": feedback,
        "command": command_facts,
    }


def _append_interaction(
    db: Session,
    suggestion: TodaySuggestion,
    *,
    interaction_type: str,
    idempotency_key: str,
    actor: str,
    source: str,
    occurred_at: int,
    activity_id: str | None = None,
    session_id: str | None = None,
    replacement_suggestion_id: str | None = None,
    reason_code: str | None = None,
    feedback: str | None = None,
    command_facts: dict[str, Any] | None = None,
) -> TodayInteraction:
    payload = _interaction_payload(
        suggestion.id,
        interaction_type,
        actor=actor,
        source=source,
        activity_id=activity_id,
        session_id=session_id,
        replacement_suggestion_id=replacement_suggestion_id,
        reason_code=reason_code,
        feedback=feedback,
        command_facts=command_facts,
    )
    payload_hash = content_hash(payload)
    existing = db.scalar(
        select(TodayInteraction).where(TodayInteraction.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if existing.payload_hash != payload_hash:
            raise AppError(409, "TODAY_IDEMPOTENCY_CONFLICT", "The key was already used.")
        return existing
    state = _state_for(db, suggestion.id)
    if interaction_type not in VALID_TRANSITIONS.get(state.status, frozenset()):
        raise AppError(
            409,
            "TODAY_TRANSITION_INVALID",
            f"Cannot transition a {state.status} suggestion to {interaction_type}.",
        )
    interaction = TodayInteraction(
        id=new_id(),
        suggestion_id=suggestion.id,
        event_sequence=state.event_sequence + 1,
        interaction_type=interaction_type,
        prior_status=state.status,
        resulting_status=interaction_type,
        occurred_at=occurred_at,
        actor=actor,
        source=source,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
        activity_id=activity_id,
        session_id=session_id,
        replacement_suggestion_id=replacement_suggestion_id,
        reason_code=reason_code,
        structured_reason_json=canonical_json(
            {"feedback": feedback, "command": command_facts}
        ),
    )
    db.add(interaction)
    db.flush()
    state.status = interaction_type
    state.event_sequence = interaction.event_sequence
    state.latest_interaction_id = interaction.id
    state.terminal = interaction_type in TERMINAL_STATUSES
    state.updated_at = occurred_at
    db.flush()
    return interaction


def _active_relations(
    db: Session, suggestion_id: str | None = None
) -> list[SuggestionActivityRelation]:
    corrected = set(
        db.scalars(select(SuggestionActivityRelationCorrection.relation_id)).all()
    )
    statement = select(SuggestionActivityRelation).order_by(
        SuggestionActivityRelation.created_at, SuggestionActivityRelation.id
    )
    if suggestion_id is not None:
        statement = statement.where(SuggestionActivityRelation.suggestion_id == suggestion_id)
    return [item for item in db.scalars(statement).all() if item.id not in corrected]


def _create_relation(
    db: Session,
    *,
    suggestion_id: str,
    activity_id: str,
    relation_type: str,
    actor: str,
    source: str,
    automatic: bool,
    idempotency_key: str,
    created_at: int,
) -> SuggestionActivityRelation:
    payload = {
        "suggestionId": suggestion_id,
        "activityId": activity_id,
        "relationType": relation_type,
        "actor": actor,
        "source": source,
        "automatic": automatic,
    }
    payload_hash = content_hash(payload)
    existing = db.scalar(
        select(SuggestionActivityRelation).where(
            SuggestionActivityRelation.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        if existing.payload_hash != payload_hash:
            raise AppError(409, "TODAY_IDEMPOTENCY_CONFLICT", "The key was already used.")
        return existing
    if db.get(TodaySuggestion, suggestion_id) is None:
        raise AppError(404, "TODAY_SUGGESTION_NOT_FOUND", "The suggestion does not exist.")
    if db.get(Activity, activity_id) is None:
        raise AppError(404, "ACTIVITY_NOT_FOUND", "The Activity does not exist.")
    if automatic and any(item.activity_id == activity_id for item in _active_relations(db)):
        raise AppError(
            409,
            "TODAY_AUTOMATIC_RELATION_EXISTS",
            "Automatic matching may attach an Activity to at most one suggestion.",
        )
    relation = SuggestionActivityRelation(
        id=new_id(),
        suggestion_id=suggestion_id,
        activity_id=activity_id,
        relation_type=relation_type,
        actor=actor,
        source=source,
        automatic=automatic,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
        created_at=created_at,
    )
    db.add(relation)
    db.flush()
    return relation


@_translate_concurrent_conflict
def generate_today(
    db: Session,
    *,
    idempotency_key: str,
    analysis_snapshot_id: str,
    available_time_ms: int | None,
    context_costs: tuple[tuple[str, str, str], ...],
    regenerate: bool,
    now_ms: int | None = None,
) -> TodayGeneration:
    _begin_immediate(db)
    now = now_ms if now_ms is not None else utc_now_ms()
    request = _generation_request(
        analysis_snapshot_id=analysis_snapshot_id,
        available_time_ms=available_time_ms,
        context_costs=context_costs,
        regenerate=regenerate,
    )
    request_hash = content_hash(request)
    existing = db.scalar(
        select(TodayGeneration).where(TodayGeneration.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise AppError(409, "TODAY_IDEMPOTENCY_CONFLICT", "The key was already used.")
        return existing
    timezone_name = _profile_timezone(db)
    local_date = local_date_for_ms(now, timezone_name)
    previous = db.scalar(
        select(TodayGeneration)
        .where(TodayGeneration.local_date == local_date.isoformat())
        .order_by(TodayGeneration.explicit_generation_sequence.desc())
        .limit(1)
    )
    if previous is not None and not regenerate:
        if previous.request_hash == request_hash:
            return previous
        raise AppError(
            409,
            "TODAY_GENERATION_EXISTS",
            "Today is already generated; use explicit regeneration for changed input.",
        )
    if previous is None and regenerate:
        raise AppError(
            409,
            "TODAY_GENERATION_MISSING",
            "Today has not been generated yet; create the initial generation first.",
        )
    sequence = (previous.explicit_generation_sequence if previous else 0) + 1
    recommendation_key = _recommendation_idempotency_key(idempotency_key)
    run = generate_public_recommendation_run(
        db,
        idempotency_key=recommendation_key,
        analysis_snapshot_id=analysis_snapshot_id,
        available_time_ms=available_time_ms,
        context_costs=context_costs,
    )
    if run.status != "completed":
        raise AppError(409, "TODAY_RECOMMENDATION_UNAVAILABLE", "Recommendation did not complete.")
    generation_key = (
        f"{local_date.isoformat()}|{run.policy_registry_version}|{sequence}"
    )
    expires_at = local_day_bounds_ms(local_date, timezone_name)[1]
    prepared, output_rows = _prepare_suggestion_rows(run.items, expires_at=expires_at)
    generation = TodayGeneration(
        id=new_id(),
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        local_date=local_date.isoformat(),
        timezone=timezone_name,
        recommendation_run_id=run.id,
        recommendation_policy_version=run.policy_registry_version,
        today_policy_version=TODAY_POLICY_VERSION,
        explicit_generation_sequence=sequence,
        generation_key=generation_key,
        generated_at=now,
        output_hash=content_hash(output_rows),
        is_regeneration=regenerate,
    )
    db.add(generation)
    db.flush()
    for item, presentation in prepared:
        suggestion = TodaySuggestion(
            id=new_id(),
            generation_id=generation.id,
            ordinal=ROLE_ORDER[item.portfolio_role],
            local_date=local_date.isoformat(),
            timezone=timezone_name,
            recommendation_run_id=run.id,
            recommendation_id=item.recommendation_id,
            candidate_id=item.candidate_id,
            portfolio_role=item.portfolio_role,
            advisory_duration_ms=item.advisory_duration_ms,
            duration_minimum_ms=item.duration_minimum_ms,
            duration_preferred_ms=item.duration_preferred_ms,
            duration_maximum_ms=item.duration_maximum_ms,
            presentation_json=canonical_json(presentation),
            presentation_hash=content_hash(presentation),
            presentation_version=TODAY_PRESENTATION_VERSION,
            today_policy_version=TODAY_POLICY_VERSION,
            generation_key=generation_key,
            created_at=now,
            expires_at=expires_at,
            replaces_suggestion_id=None,
        )
        db.add(suggestion)
        db.flush()
        db.add(
            TodaySuggestionCurrentState(
                suggestion_id=suggestion.id,
                status="suggested",
                event_sequence=0,
                latest_interaction_id=None,
                terminal=False,
                updated_at=now,
            )
        )
    if previous is not None and regenerate:
        prior_suggestions = db.scalars(
            select(TodaySuggestion).where(TodaySuggestion.generation_id == previous.id)
        ).all()
        for suggestion in prior_suggestions:
            state = _state_for(db, suggestion.id)
            if not state.terminal and state.status != "started":
                _append_interaction(
                    db,
                    suggestion,
                    interaction_type="expired",
                    idempotency_key=f"regen-expire:{generation.id}:{suggestion.id}",
                    actor="system",
                    source="today_regeneration",
                    occurred_at=now,
                    reason_code="superseded_by_regeneration",
                )
    db.flush()
    return generation


def _suggestion_or_404(db: Session, suggestion_id: str) -> TodaySuggestion:
    suggestion = db.get(TodaySuggestion, suggestion_id)
    if suggestion is None:
        raise AppError(404, "TODAY_SUGGESTION_NOT_FOUND", "The suggestion does not exist.")
    return suggestion


@_translate_concurrent_conflict
def record_interaction(
    db: Session,
    *,
    suggestion_id: str,
    interaction_type: str,
    idempotency_key: str,
    reason_code: str | None = None,
    feedback: str | None = None,
    now_ms: int | None = None,
) -> TodayInteraction:
    if interaction_type not in {"viewed", "accepted", "skipped"}:
        raise AppError(422, "TODAY_INTERACTION_INVALID", "Use the dedicated command endpoint.")
    _begin_immediate(db)
    now = now_ms if now_ms is not None else utc_now_ms()
    suggestion = _suggestion_or_404(db, suggestion_id)
    payload = _interaction_payload(
        suggestion.id,
        interaction_type,
        actor="user",
        source="today_ui",
        activity_id=None,
        session_id=None,
        replacement_suggestion_id=None,
        reason_code=reason_code,
        feedback=feedback,
        command_facts=None,
    )
    existing = db.scalar(
        select(TodayInteraction).where(TodayInteraction.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if existing.payload_hash != content_hash(payload):
            raise AppError(409, "TODAY_IDEMPOTENCY_CONFLICT", "The key was already used.")
        return existing
    if now >= suggestion.expires_at:
        raise AppError(409, "TODAY_SUGGESTION_EXPIRED", "The suggestion is past its expiry.")
    return _append_interaction(
        db,
        suggestion,
        interaction_type=interaction_type,
        idempotency_key=idempotency_key,
        actor="user",
        source="today_ui",
        occurred_at=now,
        reason_code=reason_code,
        feedback=feedback,
    )


def _derived_start_contributions(
    candidate: PublicRecommendationItemDTO,
) -> list[SessionContributionCreate]:
    if candidate.project_id is not None and candidate.source_version_id is not None:
        return [
            SessionContributionCreate(
                target_type="project",
                project_id=candidate.project_id,
                project_version_id=candidate.source_version_id,
                project_task_definition_id=(
                    candidate.source_entity_id
                    if candidate.candidate_type == "project_task"
                    else None
                ),
                relevance="primary",
                provenance="user_confirmed",
            )
        ]
    if candidate.competency_identity_id is not None:
        return [
            SessionContributionCreate(
                target_type="competency",
                competency_identity_id=candidate.competency_identity_id,
                relevance="primary",
                provenance="user_confirmed",
            )
        ]
    return []


def _start_command_facts(
    *,
    assistance_mode: str,
    notes: str | None,
    contributions: list[SessionContributionCreate],
) -> dict[str, Any]:
    rows = [item.model_dump(mode="json") for item in contributions]
    return {
        "assistanceMode": assistance_mode,
        "notes": notes,
        "contributions": sorted(rows, key=canonical_json),
    }


@_translate_concurrent_conflict
def start_suggestion(
    db: Session,
    *,
    suggestion_id: str,
    idempotency_key: str,
    assistance_mode: str,
    notes: str | None,
    contributions: list[SessionContributionCreate],
    now_ms: int | None = None,
) -> TodayInteraction:
    _begin_immediate(db)
    now = now_ms if now_ms is not None else utc_now_ms()
    command_facts = _start_command_facts(
        assistance_mode=assistance_mode,
        notes=notes,
        contributions=contributions,
    )
    existing = db.scalar(
        select(TodayInteraction).where(TodayInteraction.idempotency_key == idempotency_key)
    )
    if existing is not None:
        return _append_interaction(
            db,
            _suggestion_or_404(db, suggestion_id),
            interaction_type="started",
            idempotency_key=idempotency_key,
            actor="user",
            source="today_start",
            occurred_at=existing.occurred_at,
            activity_id=existing.activity_id,
            session_id=existing.session_id,
            command_facts=command_facts,
        )
    suggestion = _suggestion_or_404(db, suggestion_id)
    if now >= suggestion.expires_at:
        raise AppError(409, "TODAY_SUGGESTION_EXPIRED", "The suggestion is past its expiry.")
    candidate = load_public_recommendation_item(
        db,
        run_id=suggestion.recommendation_run_id,
        candidate_id=suggestion.candidate_id,
    )
    actual_contributions = contributions or _derived_start_contributions(candidate)
    activity = create_activity_in_uow(
        db,
        title=candidate.title,
        description=candidate.description or notes,
        category_stable_key=CATEGORY_BY_CANDIDATE_TYPE[candidate.candidate_type],
        occurred_at=now,
        creator_source="user",
        provenance="today_v2_direct_start",
    )
    session = start_timed_session_in_uow(
        db,
        activity=activity,
        assistance_mode=assistance_mode,
        notes=notes,
        contributions=actual_contributions,
        started_at=now,
    )
    _create_relation(
        db,
        suggestion_id=suggestion.id,
        activity_id=activity.id,
        relation_type="matched",
        actor="system",
        source="today_direct_start",
        automatic=True,
        idempotency_key=f"start-relation:{idempotency_key}",
        created_at=now,
    )
    return _append_interaction(
        db,
        suggestion,
        interaction_type="started",
        idempotency_key=idempotency_key,
        actor="user",
        source="today_start",
        occurred_at=now,
        activity_id=activity.id,
        session_id=session.id,
        command_facts=command_facts,
    )


@_translate_concurrent_conflict
def complete_suggestion(
    db: Session,
    *,
    suggestion_id: str,
    interaction_type: str,
    idempotency_key: str,
    session_id: str,
    feedback: str | None,
    now_ms: int | None = None,
) -> TodayInteraction:
    if interaction_type not in {"completed", "partially_completed"}:
        raise ValueError("Completion type is invalid")
    _begin_immediate(db)
    now = now_ms if now_ms is not None else utc_now_ms()
    existing = db.scalar(
        select(TodayInteraction).where(TodayInteraction.idempotency_key == idempotency_key)
    )
    if existing is not None:
        return _append_interaction(
            db,
            _suggestion_or_404(db, suggestion_id),
            interaction_type=interaction_type,
            idempotency_key=idempotency_key,
            actor="user",
            source="today_completion",
            occurred_at=existing.occurred_at,
            activity_id=existing.activity_id,
            session_id=session_id,
            feedback=feedback,
        )
    suggestion = _suggestion_or_404(db, suggestion_id)
    session = db.get(LearningSession, session_id)
    if (
        session is None
        or session.tombstoned_at is not None
        or (session.session_mode == "timed" and session.timed_state != "completed")
        or session.outcome not in {"completed", "partial", "blocked"}
    ):
        raise AppError(
            409,
            "TODAY_ACTUAL_WORK_NOT_FINALIZED",
            "Completion requires a finalized, non-cancelled actual Session.",
        )
    relations = _active_relations(db, suggestion.id)
    if not any(item.activity_id == session.activity_id for item in relations):
        raise AppError(
            409,
            "TODAY_ACTUAL_WORK_NOT_LINKED",
            "Completion requires an active relation to the actual Activity.",
        )
    if interaction_type == "completed" and session.outcome != "completed":
        raise AppError(
            409,
            "TODAY_COMPLETION_OUTCOME_MISMATCH",
            "Completed requires an actual Session outcome of completed.",
        )
    return _append_interaction(
        db,
        suggestion,
        interaction_type=interaction_type,
        idempotency_key=idempotency_key,
        actor="user",
        source="today_completion",
        occurred_at=now,
        activity_id=session.activity_id,
        session_id=session.id,
        feedback=feedback,
    )


@_translate_concurrent_conflict
def replace_suggestion(
    db: Session,
    *,
    suggestion_id: str,
    idempotency_key: str,
    activity_id: str,
    replacement_suggestion_id: str | None,
    reason_code: str,
    feedback: str | None,
    now_ms: int | None = None,
) -> TodayInteraction:
    _begin_immediate(db)
    now = now_ms if now_ms is not None else utc_now_ms()
    suggestion = _suggestion_or_404(db, suggestion_id)
    if replacement_suggestion_id is not None:
        replacement = _suggestion_or_404(db, replacement_suggestion_id)
        if replacement.id == suggestion.id:
            raise AppError(
                422,
                "TODAY_REPLACEMENT_SELF_REFERENCE",
                "A suggestion cannot replace itself.",
            )
        if replacement.local_date != suggestion.local_date:
            raise AppError(
                422,
                "TODAY_REPLACEMENT_DATE_MISMATCH",
                "A replacement suggestion must belong to the same local Today date.",
            )
        if replacement.created_at > now:
            raise AppError(
                422,
                "TODAY_REPLACEMENT_NOT_YET_CREATED",
                "A replacement suggestion must already exist when the interaction occurs.",
            )
    _create_relation(
        db,
        suggestion_id=suggestion.id,
        activity_id=activity_id,
        relation_type="replaced",
        actor="user",
        source="today_explicit_replacement",
        automatic=False,
        idempotency_key=f"replace-relation:{idempotency_key}",
        created_at=now,
    )
    return _append_interaction(
        db,
        suggestion,
        interaction_type="replaced",
        idempotency_key=idempotency_key,
        actor="user",
        source="today_explicit_replacement",
        occurred_at=now,
        activity_id=activity_id,
        replacement_suggestion_id=replacement_suggestion_id,
        reason_code=reason_code,
        feedback=feedback,
    )


@_translate_concurrent_conflict
def link_activity(
    db: Session,
    *,
    suggestion_id: str,
    activity_id: str,
    relation_type: str,
    idempotency_key: str,
    now_ms: int | None = None,
) -> SuggestionActivityRelation:
    _begin_immediate(db)
    return _create_relation(
        db,
        suggestion_id=suggestion_id,
        activity_id=activity_id,
        relation_type=relation_type,
        actor="user",
        source="today_explicit_relation",
        automatic=False,
        idempotency_key=idempotency_key,
        created_at=now_ms if now_ms is not None else utc_now_ms(),
    )


@_translate_concurrent_conflict
def correct_relation(
    db: Session,
    *,
    relation_id: str,
    idempotency_key: str,
    correction_type: str,
    replacement_activity_id: str | None,
    replacement_relation_type: str | None,
    reason: str,
    now_ms: int | None = None,
) -> SuggestionActivityRelationCorrection:
    _begin_immediate(db)
    now = now_ms if now_ms is not None else utc_now_ms()
    existing = db.scalar(
        select(SuggestionActivityRelationCorrection).where(
            SuggestionActivityRelationCorrection.idempotency_key == idempotency_key
        )
    )
    relation = db.get(SuggestionActivityRelation, relation_id)
    if relation is None:
        raise AppError(404, "TODAY_RELATION_NOT_FOUND", "The relation does not exist.")
    payload = {
        "relationId": relation_id,
        "correctionType": correction_type,
        "replacementActivityId": replacement_activity_id,
        "replacementRelationType": replacement_relation_type,
        "reason": reason,
    }
    if existing is not None:
        existing_replacement = (
            db.get(SuggestionActivityRelation, existing.replacement_relation_id)
            if existing.replacement_relation_id
            else None
        )
        existing_payload = {
            "relationId": existing.relation_id,
            "correctionType": existing.correction_type,
            "replacementActivityId": (
                existing_replacement.activity_id if existing_replacement else None
            ),
            "replacementRelationType": (
                existing_replacement.relation_type if existing_replacement else None
            ),
            "reason": existing.reason,
        }
        if existing_payload != payload:
            raise AppError(409, "TODAY_IDEMPOTENCY_CONFLICT", "The key was already used.")
        return existing
    if any(
        item.relation_id == relation.id
        for item in db.scalars(select(SuggestionActivityRelationCorrection)).all()
    ):
        raise AppError(
            409,
            "TODAY_RELATION_ALREADY_CORRECTED",
            "The relation is already corrected.",
        )
    replacement: SuggestionActivityRelation | None = None
    if correction_type == "replaced":
        assert replacement_activity_id is not None and replacement_relation_type is not None
        replacement = _create_relation(
            db,
            suggestion_id=relation.suggestion_id,
            activity_id=replacement_activity_id,
            relation_type=replacement_relation_type,
            actor="user",
            source="today_relation_correction",
            automatic=False,
            idempotency_key=f"correction-relation:{idempotency_key}",
            created_at=now,
        )
    correction = SuggestionActivityRelationCorrection(
        id=new_id(),
        relation_id=relation.id,
        correction_type=correction_type,
        replacement_relation_id=replacement.id if replacement else None,
        idempotency_key=idempotency_key,
        reason=reason,
        corrected_at=now,
    )
    db.add(correction)
    db.flush()
    return correction


@_translate_concurrent_conflict
def correct_interaction(
    db: Session,
    *,
    interaction_id: str,
    idempotency_key: str,
    reason: str,
    now_ms: int | None = None,
) -> TodayInteractionCorrection:
    _begin_immediate(db)
    now = now_ms if now_ms is not None else utc_now_ms()
    interaction = db.get(TodayInteraction, interaction_id)
    if interaction is None:
        raise AppError(404, "TODAY_INTERACTION_NOT_FOUND", "The interaction does not exist.")
    payload = {
        "interactionId": interaction_id,
        "correctionType": "retracted",
        "reason": reason,
        "actor": "user",
        "source": "today_ui_correction",
        "resultingStatus": interaction.prior_status,
    }
    payload_hash = content_hash(payload)
    existing = db.scalar(
        select(TodayInteractionCorrection).where(
            TodayInteractionCorrection.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        if existing.payload_hash != payload_hash:
            raise AppError(409, "TODAY_IDEMPOTENCY_CONFLICT", "The key was already used.")
        return existing
    prior_correction = db.scalar(
        select(TodayInteractionCorrection).where(
            TodayInteractionCorrection.interaction_id == interaction_id
        )
    )
    if prior_correction is not None:
        raise AppError(
            409,
            "TODAY_INTERACTION_ALREADY_CORRECTED",
            "The interaction is already corrected.",
        )
    state = _state_for(db, interaction.suggestion_id)
    if state.latest_interaction_id != interaction.id:
        raise AppError(
            409,
            "TODAY_CORRECTION_NOT_LATEST",
            "Only the latest interaction can be corrected.",
        )
    correction = TodayInteractionCorrection(
        id=new_id(),
        interaction_id=interaction.id,
        idempotency_key=idempotency_key,
        reason=reason,
        actor="user",
        source="today_ui_correction",
        resulting_status=interaction.prior_status,
        payload_hash=payload_hash,
        corrected_at=now,
    )
    db.add(correction)
    db.flush()
    state.status = correction.resulting_status
    state.terminal = correction.resulting_status in TERMINAL_STATUSES
    state.updated_at = now
    db.flush()
    return correction


@_translate_concurrent_conflict
def expire_due_suggestions(
    db: Session, *, idempotency_key: str, now_ms: int | None = None
) -> list[TodayInteraction]:
    _begin_immediate(db)
    now = now_ms if now_ms is not None else utc_now_ms()
    due = db.scalars(
        select(TodaySuggestion)
        .where(TodaySuggestion.expires_at <= now)
        .order_by(TodaySuggestion.expires_at, TodaySuggestion.id)
    ).all()
    interactions: list[TodayInteraction] = []
    for suggestion in due:
        state = _state_for(db, suggestion.id)
        if state.terminal:
            continue
        interactions.append(
            _append_interaction(
                db,
                suggestion,
                interaction_type="expired",
                idempotency_key=f"expire:{content_hash(idempotency_key)[:24]}:{suggestion.id}",
                actor="system",
                source="today_expiration_maintenance",
                occurred_at=now,
                reason_code="next_local_midnight",
            )
        )
    return interactions


def rebuild_today_current_states(db: Session) -> None:
    db.execute(delete(TodaySuggestionCurrentState))
    for suggestion in db.scalars(select(TodaySuggestion).order_by(TodaySuggestion.id)).all():
        interactions = db.scalars(
            select(TodayInteraction)
            .where(TodayInteraction.suggestion_id == suggestion.id)
            .order_by(TodayInteraction.event_sequence)
        ).all()
        corrections = {
            item.interaction_id: item
            for item in db.scalars(
                select(TodayInteractionCorrection).where(
                    TodayInteractionCorrection.interaction_id.in_(
                        [interaction.id for interaction in interactions]
                    )
                )
            ).all()
        }
        if interactions:
            latest = interactions[-1]
            latest_correction = corrections.get(latest.id)
            state = TodaySuggestionCurrentState(
                suggestion_id=suggestion.id,
                status=(
                    latest_correction.resulting_status
                    if latest_correction is not None
                    else latest.resulting_status
                ),
                event_sequence=latest.event_sequence,
                latest_interaction_id=latest.id,
                terminal=(
                    latest_correction.resulting_status in TERMINAL_STATUSES
                    if latest_correction is not None
                    else latest.resulting_status in TERMINAL_STATUSES
                ),
                updated_at=(
                    latest_correction.corrected_at
                    if latest_correction is not None
                    else latest.occurred_at
                ),
            )
        else:
            state = TodaySuggestionCurrentState(
                suggestion_id=suggestion.id,
                status="suggested",
                event_sequence=0,
                latest_interaction_id=None,
                terminal=False,
                updated_at=suggestion.created_at,
            )
        db.add(state)
    db.flush()


def _serialize_interaction(
    item: TodayInteraction, correction: TodayInteractionCorrection | None
) -> dict[str, Any]:
    return {
        "id": item.id,
        "sequence": item.event_sequence,
        "type": item.interaction_type,
        "priorStatus": item.prior_status,
        "status": item.resulting_status,
        "occurredAt": epoch_ms_to_rfc3339(item.occurred_at),
        "actor": item.actor,
        "source": item.source,
        "activityId": item.activity_id,
        "sessionId": item.session_id,
        "replacementSuggestionId": item.replacement_suggestion_id,
        "reasonCode": item.reason_code,
        "reason": json.loads(item.structured_reason_json),
        "correction": (
            {
                "id": correction.id,
                "type": "retracted",
                "resultingStatus": correction.resulting_status,
                "reason": correction.reason,
                "actor": correction.actor,
                "source": correction.source,
                "correctedAt": epoch_ms_to_rfc3339(correction.corrected_at),
            }
            if correction is not None
            else None
        ),
    }


def suggestion_detail(db: Session, suggestion: TodaySuggestion, *, now_ms: int) -> dict[str, Any]:
    state = _state_for(db, suggestion.id)
    interactions = db.scalars(
        select(TodayInteraction)
        .where(TodayInteraction.suggestion_id == suggestion.id)
        .order_by(TodayInteraction.event_sequence)
    ).all()
    corrections = {
        item.interaction_id: item
        for item in db.scalars(
            select(TodayInteractionCorrection).where(
                TodayInteractionCorrection.interaction_id.in_(
                    [interaction.id for interaction in interactions]
                )
            )
        ).all()
    }
    relations = _active_relations(db, suggestion.id)
    return {
        "id": suggestion.id,
        "generationId": suggestion.generation_id,
        "localDate": suggestion.local_date,
        "timezone": suggestion.timezone,
        "recommendationRunId": suggestion.recommendation_run_id,
        "recommendationId": suggestion.recommendation_id,
        "candidateId": suggestion.candidate_id,
        "portfolioRole": suggestion.portfolio_role,
        "advisoryDurationMs": suggestion.advisory_duration_ms,
        "durationRangeMs": (
            [
                suggestion.duration_minimum_ms,
                suggestion.duration_preferred_ms,
                suggestion.duration_maximum_ms,
            ]
            if suggestion.duration_minimum_ms is not None
            else None
        ),
        "presentation": json.loads(suggestion.presentation_json),
        "presentationHash": suggestion.presentation_hash,
        "todayPolicyVersion": suggestion.today_policy_version,
        "generationKey": suggestion.generation_key,
        "createdAt": epoch_ms_to_rfc3339(suggestion.created_at),
        "expiresAt": epoch_ms_to_rfc3339(suggestion.expires_at),
        "presentationExpired": now_ms >= suggestion.expires_at,
        "status": state.status,
        "terminal": state.terminal,
        "interactions": [
            _serialize_interaction(item, corrections.get(item.id)) for item in interactions
        ],
        "activityRelations": [
            {
                "id": relation.id,
                "activityId": relation.activity_id,
                "type": relation.relation_type,
                "actor": relation.actor,
                "source": relation.source,
                "automatic": relation.automatic,
                "createdAt": epoch_ms_to_rfc3339(relation.created_at),
            }
            for relation in relations
        ],
    }


def generation_detail(
    db: Session, generation: TodayGeneration, *, now_ms: int | None = None
) -> dict[str, Any]:
    now = now_ms if now_ms is not None else utc_now_ms()
    suggestions = db.scalars(
        select(TodaySuggestion)
        .where(TodaySuggestion.generation_id == generation.id)
        .order_by(TodaySuggestion.ordinal)
    ).all()
    return {
        "id": generation.id,
        "localDate": generation.local_date,
        "timezone": generation.timezone,
        "recommendationRunId": generation.recommendation_run_id,
        "recommendationPolicyVersion": generation.recommendation_policy_version,
        "todayPolicyVersion": generation.today_policy_version,
        "generationSequence": generation.explicit_generation_sequence,
        "generationKey": generation.generation_key,
        "generatedAt": epoch_ms_to_rfc3339(generation.generated_at),
        "outputHash": generation.output_hash,
        "regeneration": generation.is_regeneration,
        "suggestions": [suggestion_detail(db, item, now_ms=now) for item in suggestions],
    }


def current_today(db: Session, *, now_ms: int | None = None) -> dict[str, Any]:
    now = now_ms if now_ms is not None else utc_now_ms()
    timezone_name = _profile_timezone(db)
    local_date = local_date_for_ms(now, timezone_name).isoformat()
    generation = db.scalar(
        select(TodayGeneration)
        .where(TodayGeneration.local_date == local_date)
        .order_by(
            TodayGeneration.generated_at.desc(),
            TodayGeneration.explicit_generation_sequence.desc(),
        )
    )
    continuing = db.scalars(
        select(TodaySuggestion)
        .join(
            TodaySuggestionCurrentState,
            TodaySuggestionCurrentState.suggestion_id == TodaySuggestion.id,
        )
        .where(TodaySuggestionCurrentState.status == "started")
        .order_by(TodaySuggestion.created_at, TodaySuggestion.id)
    ).all()
    if generation is not None:
        continuing = [item for item in continuing if item.generation_id != generation.id]
    return {
        "generation": generation_detail(db, generation, now_ms=now) if generation else None,
        "continuingStartedSuggestions": [
            suggestion_detail(db, item, now_ms=now) for item in continuing
        ],
    }


def today_history(db: Session, *, now_ms: int | None = None) -> list[dict[str, Any]]:
    now = now_ms if now_ms is not None else utc_now_ms()
    generations = db.scalars(
        select(TodayGeneration).order_by(
            TodayGeneration.generated_at.desc(), TodayGeneration.explicit_generation_sequence.desc()
        )
    ).all()
    return [generation_detail(db, item, now_ms=now) for item in generations]


def today_current_checkpoint(db: Session) -> dict[str, Any]:
    rows = [
        {
            "suggestionId": item.suggestion_id,
            "status": item.status,
            "eventSequence": item.event_sequence,
            "latestInteractionId": item.latest_interaction_id,
            "terminal": item.terminal,
            "updatedAt": item.updated_at,
        }
        for item in db.scalars(
            select(TodaySuggestionCurrentState).order_by(TodaySuggestionCurrentState.suggestion_id)
        ).all()
    ]
    return {"states": rows, "checkpointHash": content_hash(rows)}
