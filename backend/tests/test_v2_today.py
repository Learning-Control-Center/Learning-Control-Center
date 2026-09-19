from __future__ import annotations

import ast
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier

import pytest
from app.analysis.v3.public import load_public_analysis_snapshot
from app.analysis.v3.service import run_analysis
from app.determinism import content_hash
from app.domain_integrity import _validate_today_v2
from app.errors import AppError
from app.evidence import create_session_evidence
from app.import_export import (
    _portable_payload,
    _validate_portable_payload,
    _validate_today_v2_checkpoint,
)
from app.models import (
    Activity,
    AnalysisSnapshot,
    DisciplineProfile,
    Evidence,
    LearningSession,
    ProjectionInvalidation,
    new_id,
)
from app.portability.registry import (
    PORTABLE_V7_MANIFEST,
    PORTABLE_V8_TODAY_TABLES,
    PORTABLE_V9_AUTHORITY_TABLES,
)
from app.recommendation.v2.contracts import CandidateInputDTO
from app.recommendation.v2.models import RecommendationV2Run
from app.recommendation.v2.policy import POLICY_REGISTRY_VERSION
from app.recommendation.v2.public import (
    FrozenJsonArray,
    FrozenJsonObject,
    PublicRecommendationItemDTO,
    freeze_public_json,
    load_public_recommendation_run,
    thaw_public_json,
)
from app.recommendation.v2.service import _persist_completed_run
from app.schemas import TimedSessionComplete
from app.sessions import _finalize_timed
from app.time_utils import (
    datetime_to_epoch_ms,
    epoch_ms_to_rfc3339,
    local_day_bounds_ms,
    utc_now_ms,
)
from app.today.contracts import LEGACY_TODAY_PRESENTATION_VERSION, TODAY_POLICY_VERSION
from app.today.models import (
    SuggestionActivityRelation,
    SuggestionActivityRelationCorrection,
    TodayGeneration,
    TodayInteraction,
    TodayInteractionCorrection,
    TodaySuggestion,
    TodaySuggestionCurrentState,
)
from app.today.public import current_roadmap_overlay
from app.today.service import (
    VALID_TRANSITIONS,
    _append_interaction,
    _prepare_suggestion_rows,
    _recommendation_idempotency_key,
    complete_suggestion,
    correct_interaction,
    correct_relation,
    current_today,
    expire_due_suggestions,
    generate_today,
    generation_detail,
    link_activity,
    rebuild_today_current_states,
    record_interaction,
    replace_suggestion,
    start_suggestion,
)
from app.v2_activities import create_activity_in_uow, start_timed_session_in_uow
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

MINUTE = 60_000


def _candidate(stable_id: str = "javascript-functions") -> CandidateInputDTO:
    return CandidateInputDTO(
        candidate_type="practice_task",
        candidate_key=f"candidate-key/v1|{stable_id}",
        stable_id=stable_id,
        stable_tie_key=f"candidate|{stable_id}",
        source_type="test_fixture",
        source_entity_id=stable_id,
        source_version_id="curriculum-version-1",
        title="JavaScript functions",
        description="Practice JavaScript functions",
        target_identity_id="target-js",
        served_target_identity_ids=("target-js",),
        primary_outcome_kind="target",
        primary_outcome_id="target-js",
        primary_need_identity="need-js",
        competency_identity_id=None,
        criterion_definition_id=None,
        project_id=None,
        target_priority="important",
        gap_severity="medium",
        deadline_status="none",
        allocation_miss_basis_points=None,
        neglect_or_stall_severity=None,
        maintenance_severity=None,
        context_cost="none",
        context_available=True,
        last_meaningful_activity_at=None,
        review_due=False,
        assessment_unknown=False,
        primary_need_kind="capability_gap",
        hard_blocker_count=0,
        multi_target_unblock=False,
        missing_independence=False,
        missing_evidence_mode=False,
        supports_transfer=False,
        addresses_unmet_required_criterion=True,
        supplies_missing_required_mode=False,
        supplies_missing_independent_mode=False,
        supports_transfer_target_ids=(),
        assessment_blocks_critical_planning=False,
        repeated_without_new_evidence=False,
        recently_saturated=False,
        active_target=True,
        prerequisites_satisfied=True,
        hard_readiness_satisfied=True,
        prerequisite_reference_ids=(),
        readiness_reference_ids=(),
        availability_requirement_ids=(),
        availability_satisfied=True,
        capability_suitability_satisfied=True,
        blocker_clear=True,
        active_source=True,
        target_reached=False,
        blocker_actionable=False,
        blocker_reference_id=None,
        unblock_action_available=None,
        verification_due=False,
        verification_template_present=False,
        assessment_rubric_present=False,
        assessment_scope_valid=None,
        intended_evidence_modes=("independent",),
        duration_range_ms=(15 * MINUTE, 30 * MINUTE, 45 * MINUTE),
        usefulness=True,
        explanation_facts=(("fixture", True),),
    )


def _recommendation_run(db: Session) -> RecommendationV2Run:
    snapshot = run_analysis(
        db,
        idempotency_key="today-analysis",
        purpose="learning_control",
        update_current=False,
    )
    db.flush()
    public = load_public_analysis_snapshot(db, snapshot.id)
    persisted_snapshot = db.get(AnalysisSnapshot, snapshot.id)
    assert persisted_snapshot is not None
    lineage = json.loads(persisted_snapshot.input_lineage_json)
    candidate = _candidate()
    frozen = {
        "analysisSnapshot": {
            "id": public.snapshot_id,
            "inputHash": public.input_hash,
            "outputHash": public.output_hash,
            "cutoffAt": public.cutoff_at,
        },
        "availableTimeMs": None,
        "userConstraints": {
            "availableTimeMs": None,
            "contextCostPolicy": "explicit-only",
            "contextCosts": [],
        },
        "targetProfileVersion": lineage.get("profile"),
        "learningGraph": lineage.get("graph"),
        "curriculum": lineage.get("curriculumCatalog"),
        "projects": lineage.get("projectCatalog"),
        "candidates": [asdict(candidate)],
    }
    run = _persist_completed_run(
        db,
        idempotency_key="today-source-recommendation",
        analysis_snapshot_id=snapshot.id,
        available_time_ms=None,
        replay_of_run_id=None,
        policy_registry_version=POLICY_REGISTRY_VERSION,
        snapshot=public,
        frozen_input=frozen,
        candidates=(candidate,),
    )
    db.commit()
    return run


def _generate(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    *,
    now_ms: int,
    key: str,
    regenerate: bool = False,
) -> TodayGeneration:
    run = db.scalar(select(RecommendationV2Run).limit(1))
    assert run is not None
    public_run = load_public_recommendation_run(db, run.id)
    monkeypatch.setattr(
        "app.today.service.generate_public_recommendation_run",
        lambda *args, **kwargs: public_run,
    )
    generation = generate_today(
        db,
        idempotency_key=key,
        analysis_snapshot_id=run.analysis_snapshot_id,
        available_time_ms=None,
        context_costs=(),
        regenerate=regenerate,
        now_ms=now_ms,
    )
    db.commit()
    return generation


def test_generation_get_purity_transitions_and_regeneration(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _recommendation_run(db)
    profile = db.get(DisciplineProfile, 1)
    assert profile is not None
    profile.timezone = "Europe/Berlin"
    db.commit()
    now = datetime_to_epoch_ms(datetime(2026, 3, 29, 0, 30, tzinfo=UTC))
    generation = _generate(db, monkeypatch, now_ms=now, key="today-generation-1")
    suggestion = db.scalar(
        select(TodaySuggestion).where(TodaySuggestion.generation_id == generation.id)
    )
    assert suggestion is not None
    assert (
        suggestion.expires_at
        == local_day_bounds_ms(
            datetime.fromisoformat(suggestion.local_date).date(), "Europe/Berlin"
        )[1]
    )
    assert generation.today_policy_version == TODAY_POLICY_VERSION
    recommendation_run = db.get(RecommendationV2Run, generation.recommendation_run_id)
    assert recommendation_run is not None
    same_generation = generate_today(
        db,
        idempotency_key="today-generation-1",
        analysis_snapshot_id=recommendation_run.analysis_snapshot_id,
        available_time_ms=None,
        context_costs=(),
        regenerate=False,
        now_ms=now + 1,
    )
    assert same_generation.id == generation.id
    with pytest.raises(AppError, match="key was already used"):
        generate_today(
            db,
            idempotency_key="today-generation-1",
            analysis_snapshot_id=recommendation_run.analysis_snapshot_id,
            available_time_ms=5 * MINUTE,
            context_costs=(),
            regenerate=False,
            now_ms=now + 2,
        )
    db.rollback()

    before = {
        model.__tablename__: db.scalar(select(func.count()).select_from(model))
        for model in (TodayGeneration, TodaySuggestion, TodayInteraction)
    }
    first = current_today(db, now_ms=now)
    second = current_today(db, now_ms=now)
    assert first == second
    assert before == {
        model.__tablename__: db.scalar(select(func.count()).select_from(model))
        for model in (TodayGeneration, TodaySuggestion, TodayInteraction)
    }

    viewed = record_interaction(
        db,
        suggestion_id=suggestion.id,
        interaction_type="viewed",
        idempotency_key="today-viewed-1",
        now_ms=now + 1,
    )
    accepted = record_interaction(
        db,
        suggestion_id=suggestion.id,
        interaction_type="accepted",
        idempotency_key="today-accepted-1",
        now_ms=now + 2,
    )
    assert (viewed.event_sequence, accepted.event_sequence) == (1, 2)
    db.commit()
    with pytest.raises(AppError, match="Cannot transition"):
        record_interaction(
            db,
            suggestion_id=suggestion.id,
            interaction_type="viewed",
            idempotency_key="today-viewed-invalid",
            now_ms=now + 3,
        )
    db.rollback()

    regenerated = _generate(
        db, monkeypatch, now_ms=now + 4, key="today-generation-2", regenerate=True
    )
    assert regenerated.explicit_generation_sequence == 2
    db.refresh(suggestion)
    state = db.get(TodaySuggestionCurrentState, suggestion.id)
    assert state is not None and state.status == "expired"
    assert db.scalar(select(func.count(TodayInteraction.id))) == 3


def test_today_recommendation_failure_persists_safe_lineage_without_today_history(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_run = _recommendation_run(db)

    def fail_generation(*_args: object, **_kwargs: object) -> None:
        raise AppError(409, "RECOMMENDATION_INPUT_STALE", "Forced public-command failure.")

    monkeypatch.setattr("app.recommendation.v2.public.generate_recommendations", fail_generation)
    context_costs = (
        ("curriculum_unit", "unit-z", "high"),
        ("project_task", "task-a", "low"),
    )
    with pytest.raises(AppError, match="Forced public-command failure"):
        generate_today(
            db,
            idempotency_key="today-recommendation-failure",
            analysis_snapshot_id=source_run.analysis_snapshot_id,
            available_time_ms=None,
            context_costs=context_costs,
            regenerate=False,
            now_ms=utc_now_ms(),
        )
    db.expire_all()
    failed = db.scalar(
        select(RecommendationV2Run).where(
            RecommendationV2Run.idempotency_key
            == _recommendation_idempotency_key("today-recommendation-failure")
        )
    )
    assert failed is not None and failed.status == "failed"
    frozen = json.loads(failed.frozen_input_json)
    expected_constraints = {
        "availableTimeMs": None,
        "contextCostPolicy": "explicit-only",
        "contextCosts": [
            {"sourceType": "curriculum_unit", "sourceEntityId": "unit-z", "cost": "high"},
            {"sourceType": "project_task", "sourceEntityId": "task-a", "cost": "low"},
        ],
    }
    assert frozen["userConstraints"] == expected_constraints
    assert failed.user_constraints_hash == content_hash(expected_constraints)
    assert db.scalar(select(func.count(TodayGeneration.id))) == 0
    assert db.scalar(select(func.count(TodaySuggestion.id))) == 0

    from app.recommendation.v2.service import generate_recommendations

    monkeypatch.setattr(
        "app.recommendation.v2.public.generate_recommendations", generate_recommendations
    )
    with pytest.raises(AppError, match="immutable failed Recommendation run"):
        generate_today(
            db,
            idempotency_key="today-recommendation-failure",
            analysis_snapshot_id=source_run.analysis_snapshot_id,
            available_time_ms=None,
            context_costs=context_costs,
            regenerate=False,
            now_ms=utc_now_ms(),
        )
    db.rollback()
    with pytest.raises(AppError, match="key was already used"):
        generate_today(
            db,
            idempotency_key="today-recommendation-failure",
            analysis_snapshot_id=source_run.analysis_snapshot_id,
            available_time_ms=None,
            context_costs=(("curriculum_unit", "unit-z", "low"),),
            regenerate=False,
            now_ms=utc_now_ms(),
        )
    db.rollback()


def test_public_recommendation_facts_are_deeply_immutable_and_losslessly_thawed() -> None:
    source = {"z": [["a", 1], {"nested": True}], "a": None}
    frozen = freeze_public_json(source)
    assert isinstance(frozen, FrozenJsonObject)
    assert isinstance(dict(frozen.items)["z"], FrozenJsonArray)
    assert thaw_public_json(frozen) == source
    with pytest.raises(FrozenInstanceError):
        frozen.items = ()  # type: ignore[misc]


def test_today_recommendation_idempotency_namespace_is_bounded_and_reserved() -> None:
    key = _recommendation_idempotency_key("x" * 128)
    assert key.startswith("internal:today-v2:")
    assert len(key) <= 128
    from app.recommendation.v2.contracts import (
        RecommendationReplayRequest,
        RecommendationRunRequest,
    )

    with pytest.raises(ValueError, match="reserved internal namespace"):
        RecommendationRunRequest(
            idempotency_key=key,
            analysis_snapshot_id="snapshot",
        )
    with pytest.raises(ValueError, match="reserved internal namespace"):
        RecommendationReplayRequest(idempotency_key=key)


def test_three_role_portfolio_presentation_is_stable_under_shuffled_input() -> None:
    def item(role: str, ordinal: int) -> PublicRecommendationItemDTO:
        return PublicRecommendationItemDTO(
            recommendation_id=f"recommendation-{role}",
            candidate_id=f"candidate-{role}",
            candidate_type="maintenance" if role == "maintenance" else "practice_task",
            source_type="curriculum_unit",
            source_entity_id=f"source-{role}",
            source_version_id="version-1",
            candidate_stable_id=f"stable-{role}",
            title=role.title(),
            description=f"{role} work",
            project_id=None,
            competency_identity_id=None,
            portfolio_role=role,
            rank=ordinal,
            score=10 - ordinal,
            reason_summary=f"{role} reason",
            advisory_duration_ms=ordinal * 5 * MINUTE,
            duration_minimum_ms=5 * MINUTE,
            duration_preferred_ms=ordinal * 5 * MINUTE,
            duration_maximum_ms=ordinal * 10 * MINUTE,
            reasons=(),
        )

    primary = item("primary", 1)
    complementary = item("complementary", 2)
    maintenance = item("maintenance", 3)
    first = _prepare_suggestion_rows((maintenance, primary, complementary), expires_at=123_456)
    second = _prepare_suggestion_rows((complementary, maintenance, primary), expires_at=123_456)
    assert [value.portfolio_role for value, _presentation in first[0]] == [
        "primary",
        "complementary",
        "maintenance",
    ]
    assert [row["ordinal"] for row in first[1]] == [1, 2, 3]
    assert first == second
    assert content_hash(first[1]) == content_hash(second[1])


def test_idempotent_interaction_retry_survives_expiry_and_dst_bounds_are_exact(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _recommendation_run(db)
    profile = db.get(DisciplineProfile, 1)
    assert profile is not None
    profile.timezone = "Europe/Berlin"
    db.commit()
    spring = datetime_to_epoch_ms(datetime(2026, 3, 29, 0, 30, tzinfo=UTC))
    generation = _generate(db, monkeypatch, now_ms=spring, key="today-dst-generation")
    suggestion = db.scalar(
        select(TodaySuggestion).where(TodaySuggestion.generation_id == generation.id)
    )
    assert suggestion is not None
    spring_bounds = local_day_bounds_ms(
        datetime.fromisoformat(suggestion.local_date).date(), "Europe/Berlin"
    )
    assert spring_bounds[1] - spring_bounds[0] == 23 * 60 * MINUTE
    autumn_bounds = local_day_bounds_ms(datetime(2026, 10, 25).date(), "Europe/Berlin")
    assert autumn_bounds[1] - autumn_bounds[0] == 25 * 60 * MINUTE

    first = record_interaction(
        db,
        suggestion_id=suggestion.id,
        interaction_type="viewed",
        idempotency_key="today-viewed-expiry-retry",
        now_ms=spring + 1,
    )
    db.commit()
    retried = record_interaction(
        db,
        suggestion_id=suggestion.id,
        interaction_type="viewed",
        idempotency_key="today-viewed-expiry-retry",
        now_ms=suggestion.expires_at + 1,
    )
    assert retried.id == first.id
    with pytest.raises(AppError, match="key was already used"):
        record_interaction(
            db,
            suggestion_id=suggestion.id,
            interaction_type="viewed",
            idempotency_key="today-viewed-expiry-retry",
            feedback="Changed feedback",
            now_ms=spring + 2,
        )
    db.rollback()


def test_stored_timezone_and_expiry_survive_profile_change_and_multi_day_downtime(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _recommendation_run(db)
    profile = db.get(DisciplineProfile, 1)
    assert profile is not None
    profile.timezone = "Europe/Berlin"
    db.commit()
    generated_at = datetime_to_epoch_ms(datetime(2026, 3, 28, 12, 0, tzinfo=UTC))
    generation = _generate(
        db,
        monkeypatch,
        now_ms=generated_at,
        key="today-timezone-change-generation",
    )
    suggestion = db.scalar(
        select(TodaySuggestion).where(TodaySuggestion.generation_id == generation.id)
    )
    assert suggestion is not None
    frozen_expiry = suggestion.expires_at
    assert suggestion.timezone == "Europe/Berlin"

    profile = db.get(DisciplineProfile, 1)
    assert profile is not None
    profile.timezone = "America/New_York"
    db.commit()
    historical = generation_detail(db, generation, now_ms=frozen_expiry + 3 * 24 * 60 * MINUTE)
    assert historical["timezone"] == "Europe/Berlin"
    assert historical["suggestions"][0]["timezone"] == "Europe/Berlin"
    assert historical["suggestions"][0]["expiresAt"] == epoch_ms_to_rfc3339(frozen_expiry)

    expired = expire_due_suggestions(
        db,
        idempotency_key="today-expire-after-downtime",
        now_ms=frozen_expiry + 3 * 24 * 60 * MINUTE,
    )
    db.commit()
    assert len(expired) == 1
    assert expired[0].occurred_at == frozen_expiry + 3 * 24 * 60 * MINUTE
    assert expired[0].reason_code == "next_local_midnight"


def test_generation_and_start_faults_roll_back_their_full_units_of_work(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_snapshot = run_analysis(
        db,
        idempotency_key="today-atomic-analysis",
        purpose="learning_control",
        update_current=True,
    )
    db.commit()
    from app.today import service as today_service

    real_generate = today_service.generate_public_recommendation_run

    def fail_after_recommendation(*args: object, **kwargs: object) -> None:
        real_generate(*args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("fault after Recommendation persistence")

    monkeypatch.setattr(
        "app.today.service.generate_public_recommendation_run", fail_after_recommendation
    )
    with pytest.raises(RuntimeError, match="after Recommendation"):
        generate_today(
            db,
            idempotency_key="today-atomic-generation-fault",
            analysis_snapshot_id=current_snapshot.id,
            available_time_ms=None,
            context_costs=(),
            regenerate=False,
            now_ms=utc_now_ms(),
        )
    db.rollback()
    assert (
        db.scalar(
            select(func.count(RecommendationV2Run.id)).where(
                RecommendationV2Run.idempotency_key
                == _recommendation_idempotency_key("today-atomic-generation-fault")
            )
        )
        == 0
    )
    assert db.scalar(select(func.count(TodayGeneration.id))) == 0

    source_run = _recommendation_run(db)
    public_run = load_public_recommendation_run(db, source_run.id)
    monkeypatch.setattr(
        "app.today.service.generate_public_recommendation_run",
        lambda *args, **kwargs: public_run,
    )
    now = utc_now_ms()
    generation = generate_today(
        db,
        idempotency_key="today-atomic-start-generation",
        analysis_snapshot_id=source_run.analysis_snapshot_id,
        available_time_ms=None,
        context_costs=(),
        regenerate=False,
        now_ms=now,
    )
    db.commit()
    suggestion = db.scalar(
        select(TodaySuggestion).where(TodaySuggestion.generation_id == generation.id)
    )
    assert suggestion is not None

    def fail_relation(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("fault before actual-work relation")

    monkeypatch.setattr("app.today.service._create_relation", fail_relation)
    with pytest.raises(RuntimeError, match="actual-work relation"):
        start_suggestion(
            db,
            suggestion_id=suggestion.id,
            idempotency_key="today-atomic-start-fault",
            assistance_mode="none",
            notes=None,
            contributions=[],
            now_ms=now + 1,
        )
    db.rollback()
    assert db.scalar(select(func.count(Activity.id))) == 0
    assert db.scalar(select(func.count(LearningSession.id))) == 0
    assert db.scalar(select(func.count(SuggestionActivityRelation.id))) == 0
    assert db.scalar(select(func.count(TodayInteraction.id))) == 0
    state = db.get(TodaySuggestionCurrentState, suggestion.id)
    assert state is not None and state.status == "suggested"
    with pytest.raises(AppError, match="past its expiry"):
        record_interaction(
            db,
            suggestion_id=suggestion.id,
            interaction_type="accepted",
            idempotency_key="today-new-after-expiry",
            now_ms=suggestion.expires_at + 1,
        )
    db.rollback()


def test_direct_start_completion_and_expiration_are_actuality_safe(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _recommendation_run(db)
    now = datetime_to_epoch_ms(datetime(2026, 1, 10, 10, 0, tzinfo=UTC))
    generation = _generate(db, monkeypatch, now_ms=now, key="today-start-generation")
    suggestion = db.scalar(
        select(TodaySuggestion).where(TodaySuggestion.generation_id == generation.id)
    )
    assert suggestion is not None
    started = start_suggestion(
        db,
        suggestion_id=suggestion.id,
        idempotency_key="today-start-1",
        assistance_mode="none",
        notes=None,
        contributions=[],
        now_ms=now + 1,
    )
    db.commit()
    assert started.activity_id is not None and started.session_id is not None
    activity = db.get(Activity, started.activity_id)
    session = db.get(LearningSession, started.session_id)
    assert activity is not None and activity.provenance == "today_v2_direct_start"
    assert session is not None and session.timed_state == "running"
    relation = db.scalar(
        select(SuggestionActivityRelation).where(
            SuggestionActivityRelation.suggestion_id == suggestion.id
        )
    )
    assert relation is not None and relation.relation_type == "matched" and relation.automatic

    _finalize_timed(
        session,
        cancel=False,
        payload=TimedSessionComplete(outcome="completed", difficulty=2, notes=None),
    )
    create_session_evidence(db, session)
    db.flush()
    completed = complete_suggestion(
        db,
        suggestion_id=suggestion.id,
        interaction_type="completed",
        idempotency_key="today-complete-1",
        session_id=session.id,
        feedback=None,
        now_ms=now + 2,
    )
    db.commit()
    assert completed.activity_id == activity.id
    state = db.get(TodaySuggestionCurrentState, suggestion.id)
    assert state is not None and state.status == "completed"
    corrected_relation = correct_relation(
        db,
        relation_id=relation.id,
        idempotency_key="today-complete-relation-correction",
        correction_type="retracted",
        replacement_activity_id=None,
        replacement_relation_type=None,
        reason="Correct the relation after completion without rewriting history.",
        now_ms=now + 3,
    )
    db.commit()
    assert corrected_relation.relation_id == relation.id
    retried = complete_suggestion(
        db,
        suggestion_id=suggestion.id,
        interaction_type="completed",
        idempotency_key="today-complete-1",
        session_id=session.id,
        feedback=None,
        now_ms=now + 4,
    )
    assert retried.id == completed.id
    with pytest.raises(AppError, match="key was already used"):
        complete_suggestion(
            db,
            suggestion_id=suggestion.id,
            interaction_type="completed",
            idempotency_key="today-complete-1",
            session_id=session.id,
            feedback="Changed feedback",
            now_ms=now + 5,
        )
    db.rollback()
    assert (
        expire_due_suggestions(
            db, idempotency_key="today-expire-terminal", now_ms=suggestion.expires_at + 1
        )
        == []
    )
    _validate_today_v2(db.connection())


@pytest.mark.parametrize(
    ("source_type", "source_entity_id", "expected_port"),
    (
        ("curriculum_unit", "unit-definition", "curriculum"),
        ("project_task", "task-definition", "project"),
    ),
)
def test_direct_start_atomically_confirms_canonical_source_attribution(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    source_type: str,
    source_entity_id: str,
    expected_port: str,
) -> None:
    _recommendation_run(db)
    now = utc_now_ms()
    generation = _generate(db, monkeypatch, now_ms=now, key=f"today-source-{source_type}")
    suggestion = db.scalar(
        select(TodaySuggestion).where(TodaySuggestion.generation_id == generation.id)
    )
    assert suggestion is not None
    public_item = next(
        item
        for item in load_public_recommendation_run(db, generation.recommendation_run_id).items
        if item.candidate_id == suggestion.candidate_id
    )
    monkeypatch.setattr(
        "app.today.service.load_public_recommendation_item",
        lambda *_args, **_kwargs: replace(
            public_item,
            source_type=source_type,
            source_entity_id=source_entity_id,
            candidate_type=("project_task" if source_type == "project_task" else "curriculum_unit"),
        ),
    )
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        "app.today.service.link_actual_activity_to_unit",
        lambda _db, **kwargs: calls.append(("curriculum", kwargs)),
    )
    monkeypatch.setattr(
        "app.today.service.link_actual_activity_to_project_task",
        lambda _db, **kwargs: calls.append(("project", kwargs)),
    )

    started = start_suggestion(
        db,
        suggestion_id=suggestion.id,
        idempotency_key=f"today-source-start-{source_type}",
        assistance_mode="none",
        notes=None,
        contributions=[],
        now_ms=now + 1,
    )
    persisted_activity = db.get(Activity, started.activity_id)
    assert persisted_activity is not None
    assert calls == [
        (
            expected_port,
            {
                "activity_id": started.activity_id,
                (
                    "task_definition_id"
                    if source_type == "project_task"
                    else "learning_unit_definition_id"
                ): source_entity_id,
                "provenance": "user_confirmed",
                "idempotency_key": (
                    f"today-project-link:today-source-start-{source_type}"
                    if source_type == "project_task"
                    else f"today-curriculum-link:today-source-start-{source_type}"
                ),
                "created_at": max(now + 1, persisted_activity.created_at),
            },
        )
    ]
    db.rollback()
    assert db.scalar(select(func.count(Activity.id))) == 0
    assert db.scalar(select(func.count(TodayInteraction.id))) == 0


def test_start_idempotency_covers_all_command_inputs(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _recommendation_run(db)
    now = utc_now_ms()
    generation = _generate(db, monkeypatch, now_ms=now, key="today-start-hash-generation")
    suggestion = db.scalar(
        select(TodaySuggestion).where(TodaySuggestion.generation_id == generation.id)
    )
    assert suggestion is not None
    started = start_suggestion(
        db,
        suggestion_id=suggestion.id,
        idempotency_key="today-start-command-hash",
        assistance_mode="none",
        notes="Original note",
        contributions=[],
        now_ms=now + 1,
    )
    db.commit()
    retried = start_suggestion(
        db,
        suggestion_id=suggestion.id,
        idempotency_key="today-start-command-hash",
        assistance_mode="none",
        notes="Original note",
        contributions=[],
        now_ms=now + 2,
    )
    assert retried.id == started.id
    with pytest.raises(AppError, match="key was already used"):
        start_suggestion(
            db,
            suggestion_id=suggestion.id,
            idempotency_key="today-start-command-hash",
            assistance_mode="none",
            notes="Changed note",
            contributions=[],
            now_ms=now + 3,
        )
    db.rollback()


def test_interaction_correction_is_append_only_and_restores_prior_status(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _recommendation_run(db)
    now = utc_now_ms()
    generation = _generate(
        db, monkeypatch, now_ms=now, key="today-interaction-correction-generation"
    )
    suggestion = db.scalar(
        select(TodaySuggestion).where(TodaySuggestion.generation_id == generation.id)
    )
    assert suggestion is not None
    viewed = record_interaction(
        db,
        suggestion_id=suggestion.id,
        interaction_type="viewed",
        idempotency_key="today-correct-viewed",
        now_ms=now + 1,
    )
    correction = correct_interaction(
        db,
        interaction_id=viewed.id,
        idempotency_key="today-correct-viewed-correction",
        reason="Viewed was recorded accidentally.",
        now_ms=now + 2,
    )
    db.commit()
    repeated_correction = correct_interaction(
        db,
        interaction_id=viewed.id,
        idempotency_key="today-correct-viewed-correction",
        reason="Viewed was recorded accidentally.",
        now_ms=now + 3,
    )
    assert repeated_correction.id == correction.id
    with pytest.raises(AppError, match="key was already used"):
        correct_interaction(
            db,
            interaction_id=viewed.id,
            idempotency_key="today-correct-viewed-correction",
            reason="Changed correction reason.",
            now_ms=now + 3,
        )
    db.rollback()
    assert correction.resulting_status == "suggested"
    state = db.get(TodaySuggestionCurrentState, suggestion.id)
    assert state is not None and state.status == "suggested"
    assert db.get(TodayInteraction, viewed.id) is not None
    assert db.get(TodayInteractionCorrection, correction.id) is not None
    accepted = record_interaction(
        db,
        suggestion_id=suggestion.id,
        interaction_type="accepted",
        idempotency_key="today-after-correction-accepted",
        now_ms=now + 3,
    )
    db.commit()
    assert accepted.prior_status == "suggested"
    _validate_today_v2(db.connection())
    db.connection().execute(
        TodayInteractionCorrection.__table__.update()
        .where(TodayInteractionCorrection.id == correction.id)
        .values(corrected_at=viewed.occurred_at - 1)
    )
    with pytest.raises(AppError, match="correction history"):
        _validate_today_v2(db.connection())
    db.rollback()


def test_transition_contract_is_explicit_and_terminal_states_have_no_outgoing_edges() -> None:
    assert VALID_TRANSITIONS == {
        "suggested": frozenset({"viewed", "accepted", "started", "skipped", "replaced", "expired"}),
        "viewed": frozenset({"accepted", "started", "skipped", "replaced", "expired"}),
        "accepted": frozenset({"started", "skipped", "replaced", "expired"}),
        "started": frozenset({"completed", "partially_completed", "replaced", "expired"}),
    }
    assert not {
        "completed",
        "partially_completed",
        "skipped",
        "replaced",
        "expired",
    } & set(VALID_TRANSITIONS)


def test_every_allowed_and_forbidden_transition_executes_through_the_state_machine(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _recommendation_run(db)
    now = utc_now_ms()
    base_generation = _generate(db, monkeypatch, now_ms=now, key="today-matrix-base")
    base = db.scalar(
        select(TodaySuggestion).where(TodaySuggestion.generation_id == base_generation.id)
    )
    assert base is not None
    activity = create_activity_in_uow(
        db,
        title="Transition matrix actual work",
        description="Shared actual-work fixture for event-shape constraints.",
        category_stable_key="practice",
        occurred_at=now,
        creator_source="user",
        provenance="user_recorded",
    )
    session = start_timed_session_in_uow(
        db,
        activity=activity,
        assistance_mode="none",
        notes=None,
        contributions=[],
        started_at=now,
    )
    states = (
        "suggested",
        "viewed",
        "accepted",
        "started",
        "completed",
        "partially_completed",
        "skipped",
        "replaced",
        "expired",
    )
    targets = states[1:]
    seed_paths = {
        "suggested": (),
        "viewed": ("viewed",),
        "accepted": ("accepted",),
        "started": ("started",),
        "completed": ("started", "completed"),
        "partially_completed": ("started", "partially_completed"),
        "skipped": ("skipped",),
        "replaced": ("replaced",),
        "expired": ("expired",),
    }

    def append_event(suggestion: TodaySuggestion, event_type: str, key: str, at: int) -> None:
        actual = event_type in {"started", "completed", "partially_completed", "replaced"}
        _append_interaction(
            db,
            suggestion,
            interaction_type=event_type,
            idempotency_key=key,
            actor="system" if event_type == "expired" else "user",
            source="transition_matrix",
            occurred_at=at,
            activity_id=activity.id if actual else None,
            session_id=(
                session.id
                if event_type in {"started", "completed", "partially_completed"}
                else None
            ),
        )

    case_index = 0
    for source_status in states:
        for target_status in targets:
            case_index += 1
            generation = TodayGeneration(
                id=new_id(),
                idempotency_key=f"matrix-generation-{case_index}",
                request_hash="a" * 64,
                local_date=base.local_date,
                timezone=base.timezone,
                recommendation_run_id=base.recommendation_run_id,
                recommendation_policy_version=base_generation.recommendation_policy_version,
                today_policy_version=base.today_policy_version,
                explicit_generation_sequence=case_index + 1,
                generation_key=f"matrix-generation-key-{case_index}",
                generated_at=base.created_at,
                output_hash="b" * 64,
                is_regeneration=True,
            )
            suggestion = TodaySuggestion(
                id=new_id(),
                generation_id=generation.id,
                ordinal=base.ordinal,
                local_date=base.local_date,
                timezone=base.timezone,
                recommendation_run_id=base.recommendation_run_id,
                recommendation_id=base.recommendation_id,
                candidate_id=base.candidate_id,
                portfolio_role=base.portfolio_role,
                advisory_duration_ms=base.advisory_duration_ms,
                duration_minimum_ms=base.duration_minimum_ms,
                duration_preferred_ms=base.duration_preferred_ms,
                duration_maximum_ms=base.duration_maximum_ms,
                presentation_json=base.presentation_json,
                presentation_hash=base.presentation_hash,
                presentation_version=base.presentation_version,
                today_policy_version=base.today_policy_version,
                generation_key=generation.generation_key,
                created_at=base.created_at,
                expires_at=base.expires_at,
                replaces_suggestion_id=None,
            )
            db.add_all(
                [
                    generation,
                    suggestion,
                    TodaySuggestionCurrentState(
                        suggestion_id=suggestion.id,
                        status="suggested",
                        event_sequence=0,
                        latest_interaction_id=None,
                        terminal=False,
                        updated_at=base.created_at,
                    ),
                ]
            )
            db.flush()
            event_time = base.created_at
            for seed_index, seed_event in enumerate(seed_paths[source_status], start=1):
                event_time += 1
                append_event(
                    suggestion,
                    seed_event,
                    f"matrix-seed-{case_index}-{seed_index}",
                    event_time,
                )
            if target_status in VALID_TRANSITIONS.get(source_status, frozenset()):
                append_event(
                    suggestion,
                    target_status,
                    f"matrix-target-{case_index}",
                    event_time + 1,
                )
                state = db.get(TodaySuggestionCurrentState, suggestion.id)
                assert state is not None and state.status == target_status
            else:
                with pytest.raises(AppError, match="Cannot transition"):
                    append_event(
                        suggestion,
                        target_status,
                        f"matrix-target-{case_index}",
                        event_time + 1,
                    )


def test_javascript_suggestion_german_replacement_creates_no_fake_work_or_debt(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _recommendation_run(db)
    now = datetime_to_epoch_ms(datetime(2026, 2, 1, 8, 0, tzinfo=UTC))
    generation = _generate(db, monkeypatch, now_ms=now, key="today-german-generation")
    suggestion = db.scalar(
        select(TodaySuggestion).where(TodaySuggestion.generation_id == generation.id)
    )
    assert suggestion is not None
    german = create_activity_in_uow(
        db,
        title="German speaking",
        description="Actual German practice",
        category_stable_key="practice",
        occurred_at=now + 1,
        creator_source="user",
        provenance="user_recorded",
    )
    db.commit()
    assert db.scalar(select(func.count(Activity.id))) == 1
    assert db.scalar(select(func.count(Evidence.id))) == 0
    assert db.scalar(select(func.count(SuggestionActivityRelation.id))) == 0

    replace_suggestion(
        db,
        suggestion_id=suggestion.id,
        idempotency_key="today-replace-german",
        activity_id=german.id,
        replacement_suggestion_id=None,
        reason_code="worked_on_something_else",
        feedback=None,
        now_ms=suggestion.expires_at + 1,
    )
    db.commit()
    repeated_replacement = replace_suggestion(
        db,
        suggestion_id=suggestion.id,
        idempotency_key="today-replace-german",
        activity_id=german.id,
        replacement_suggestion_id=None,
        reason_code="worked_on_something_else",
        feedback=None,
        now_ms=suggestion.expires_at + 2,
    )
    assert repeated_replacement.interaction_type == "replaced"
    with pytest.raises(AppError, match="key was already used"):
        replace_suggestion(
            db,
            suggestion_id=suggestion.id,
            idempotency_key="today-replace-german",
            activity_id=german.id,
            replacement_suggestion_id=None,
            reason_code="changed_reason",
            feedback=None,
            now_ms=suggestion.expires_at + 2,
        )
    db.rollback()
    assert db.scalar(select(func.count(Activity.id))) == 1
    assert db.scalar(select(func.count(Evidence.id))) == 0
    relation = db.scalar(select(SuggestionActivityRelation))
    assert relation is not None and relation.activity_id == german.id
    assert relation.relation_type == "replaced"
    state = db.get(TodaySuggestionCurrentState, suggestion.id)
    assert state is not None and state.status == "replaced"
    assert not hasattr(state, "debt")
    correction = correct_relation(
        db,
        relation_id=relation.id,
        idempotency_key="today-correct-german-relation",
        correction_type="retracted",
        replacement_activity_id=None,
        replacement_relation_type=None,
        reason="The explicit association was selected by mistake.",
        now_ms=suggestion.expires_at + 2,
    )
    db.commit()
    repeated_correction = correct_relation(
        db,
        relation_id=relation.id,
        idempotency_key="today-correct-german-relation",
        correction_type="retracted",
        replacement_activity_id=None,
        replacement_relation_type=None,
        reason="The explicit association was selected by mistake.",
        now_ms=suggestion.expires_at + 3,
    )
    assert repeated_correction.id == correction.id
    with pytest.raises(AppError, match="key was already used"):
        correct_relation(
            db,
            relation_id=relation.id,
            idempotency_key="today-correct-german-relation",
            correction_type="retracted",
            replacement_activity_id=None,
            replacement_relation_type=None,
            reason="Changed correction reason.",
            now_ms=suggestion.expires_at + 3,
        )
    db.rollback()
    assert db.get(SuggestionActivityRelationCorrection, correction.id) is not None
    _validate_today_v2(db.connection())


def test_started_survives_regeneration_active_timer_wins_and_cancel_is_not_skip(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _recommendation_run(db)
    now = datetime_to_epoch_ms(datetime(2026, 4, 4, 8, 0, tzinfo=UTC))
    first_generation = _generate(db, monkeypatch, now_ms=now, key="today-active-generation")
    first = db.scalar(
        select(TodaySuggestion).where(TodaySuggestion.generation_id == first_generation.id)
    )
    assert first is not None
    started = start_suggestion(
        db,
        suggestion_id=first.id,
        idempotency_key="today-active-start",
        assistance_mode="none",
        notes=None,
        contributions=[],
        now_ms=now + 1,
    )
    db.commit()
    second_generation = _generate(
        db,
        monkeypatch,
        now_ms=now + 2,
        key="today-active-regeneration",
        regenerate=True,
    )
    first_state = db.get(TodaySuggestionCurrentState, first.id)
    assert first_state is not None and first_state.status == "started"
    second = db.scalar(
        select(TodaySuggestion).where(TodaySuggestion.generation_id == second_generation.id)
    )
    assert second is not None
    overlay = current_roadmap_overlay(db, now_ms=now + 2)
    assert {item.suggestion_id: item.continuing_started for item in overlay.items} == {
        first.id: True,
        second.id: False,
    }
    assert {target for item in overlay.items for target in item.target_identity_ids} == {
        "target-js"
    }
    invalidations = db.scalars(
        select(ProjectionInvalidation).where(
            ProjectionInvalidation.projection_kind == "roadmap_projection_v2",
            ProjectionInvalidation.subject_type == "today_overlay",
        )
    ).all()
    assert first_state.latest_interaction_id is not None
    assert {item.source_fact_id for item in invalidations} >= {
        first_generation.id,
        second_generation.id,
        first_state.latest_interaction_id,
    }
    with pytest.raises(AppError, match="active Session"):
        start_suggestion(
            db,
            suggestion_id=second.id,
            idempotency_key="today-second-active-start",
            assistance_mode="none",
            notes=None,
            contributions=[],
            now_ms=now + 3,
        )
    db.rollback()

    session = db.get(LearningSession, started.session_id)
    assert session is not None
    _finalize_timed(session, cancel=True, payload=None)
    db.commit()
    with pytest.raises(AppError, match="finalized, non-cancelled"):
        complete_suggestion(
            db,
            suggestion_id=first.id,
            interaction_type="completed",
            idempotency_key="today-cancelled-completion",
            session_id=session.id,
            feedback=None,
            now_ms=now + 4,
        )
    db.rollback()
    state_after_cancel = db.get(TodaySuggestionCurrentState, first.id)
    assert state_after_cancel is not None and state_after_cancel.status == "started"
    expire_due_suggestions(
        db, idempotency_key="today-active-expiration", now_ms=first.expires_at + 1
    )
    db.commit()
    final_state = db.get(TodaySuggestionCurrentState, first.id)
    assert final_state is not None and final_state.status == "expired"
    assert current_roadmap_overlay(db, now_ms=first.expires_at + 1).items == ()
    assert db.scalar(
        select(func.count(ProjectionInvalidation.id)).where(
            ProjectionInvalidation.projection_kind == "roadmap_projection_v2",
            ProjectionInvalidation.subject_type == "today_overlay",
        )
    ) > len(invalidations)
    assert (
        db.scalar(
            select(func.count(TodayInteraction.id)).where(
                TodayInteraction.suggestion_id == first.id,
                TodayInteraction.interaction_type == "skipped",
            )
        )
        == 0
    )


def test_active_legacy_presentation_uses_immutable_recommendation_for_roadmap_overlay(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _recommendation_run(db)
    now = datetime_to_epoch_ms(datetime(2026, 4, 4, 8, 0, tzinfo=UTC))
    generation = _generate(db, monkeypatch, now_ms=now, key="today-legacy-roadmap-overlay")
    suggestion = db.scalar(
        select(TodaySuggestion).where(TodaySuggestion.generation_id == generation.id)
    )
    assert suggestion is not None
    presentation = json.loads(suggestion.presentation_json)
    for field in (
        "competencyIdentityId",
        "targetIdentityId",
        "servedTargetIdentityIds",
    ):
        presentation.pop(field)
    presentation_hash = content_hash(presentation)
    db.connection().execute(
        TodaySuggestion.__table__.update()
        .where(TodaySuggestion.id == suggestion.id)
        .values(
            presentation_json=json.dumps(presentation, sort_keys=True, separators=(",", ":")),
            presentation_hash=presentation_hash,
            presentation_version=LEGACY_TODAY_PRESENTATION_VERSION,
        )
    )
    db.connection().execute(
        TodayGeneration.__table__.update()
        .where(TodayGeneration.id == generation.id)
        .values(
            output_hash=content_hash(
                [
                    {
                        "ordinal": suggestion.ordinal,
                        "recommendationId": suggestion.recommendation_id,
                        "candidateId": suggestion.candidate_id,
                        "presentationHash": presentation_hash,
                        "expiresAt": suggestion.expires_at,
                    }
                ]
            )
        )
    )
    db.commit()

    overlay = current_roadmap_overlay(db, now_ms=now + 1)
    assert len(overlay.items) == 1
    assert overlay.items[0].competency_identity_id is None
    assert overlay.items[0].target_identity_ids == ("target-js",)
    _validate_today_v2(db.connection())


def test_two_connection_generation_and_terminal_races_are_linearizable(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _recommendation_run(db)
    public_run = load_public_recommendation_run(db, run.id)
    monkeypatch.setattr(
        "app.today.service.generate_public_recommendation_run",
        lambda *args, **kwargs: public_run,
    )
    maker = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    now = utc_now_ms()
    generation_barrier = Barrier(2)

    def generate_worker() -> str:
        with maker() as session:
            generation_barrier.wait()
            generated = generate_today(
                session,
                idempotency_key="today-concurrent-generation",
                analysis_snapshot_id=run.analysis_snapshot_id,
                available_time_ms=None,
                context_costs=(),
                regenerate=False,
                now_ms=now,
            )
            session.commit()
            return generated.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        generated_ids = list(executor.map(lambda _index: generate_worker(), range(2)))
    assert generated_ids[0] == generated_ids[1]
    db.expire_all()
    assert db.scalar(select(func.count(TodayGeneration.id))) == 1
    suggestion = db.scalar(select(TodaySuggestion))
    assert suggestion is not None

    terminal_barrier = Barrier(2)

    def terminal_worker(action: str) -> str:
        with maker() as session:
            terminal_barrier.wait()
            try:
                if action == "skip":
                    record_interaction(
                        session,
                        suggestion_id=suggestion.id,
                        interaction_type="skipped",
                        idempotency_key="today-concurrent-skip",
                        now_ms=suggestion.expires_at - 1,
                    )
                else:
                    expire_due_suggestions(
                        session,
                        idempotency_key="today-concurrent-expire",
                        now_ms=suggestion.expires_at + 1,
                    )
                session.commit()
                return "committed"
            except AppError:
                session.rollback()
                return "lost"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(terminal_worker, ("skip", "expire")))
    assert "committed" in outcomes
    db.expire_all()
    terminal_rows = db.scalars(
        select(TodayInteraction).where(
            TodayInteraction.suggestion_id == suggestion.id,
            TodayInteraction.interaction_type.in_(("skipped", "expired")),
        )
    ).all()
    assert len(terminal_rows) == 1
    state = db.get(TodaySuggestionCurrentState, suggestion.id)
    assert state is not None and state.status in {"skipped", "expired"}


def test_different_key_generation_race_converges_on_one_generation(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _recommendation_run(db)
    public_run = load_public_recommendation_run(db, run.id)
    monkeypatch.setattr(
        "app.today.service.generate_public_recommendation_run",
        lambda *args, **kwargs: public_run,
    )
    maker = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    barrier = Barrier(2)
    now = utc_now_ms()

    def worker(key: str) -> str:
        with maker() as session:
            barrier.wait()
            generated = generate_today(
                session,
                idempotency_key=key,
                analysis_snapshot_id=run.analysis_snapshot_id,
                available_time_ms=None,
                context_costs=(),
                regenerate=False,
                now_ms=now,
            )
            session.commit()
            return generated.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(worker, ("today-race-key-a", "today-race-key-b")))
    assert ids[0] == ids[1]
    db.expire_all()
    assert db.scalar(select(func.count(TodayGeneration.id))) == 1


def test_start_regenerate_race_preserves_actuality_and_generation_history(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _recommendation_run(db)
    now = utc_now_ms()
    generation = _generate(db, monkeypatch, now_ms=now, key="today-start-regen-seed")
    suggestion = db.scalar(
        select(TodaySuggestion).where(TodaySuggestion.generation_id == generation.id)
    )
    assert suggestion is not None
    public_run = load_public_recommendation_run(db, run.id)
    monkeypatch.setattr(
        "app.today.service.generate_public_recommendation_run",
        lambda *args, **kwargs: public_run,
    )
    maker = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    barrier = Barrier(2)

    def worker(action: str) -> str:
        with maker() as session:
            barrier.wait()
            try:
                if action == "start":
                    start_suggestion(
                        session,
                        suggestion_id=suggestion.id,
                        idempotency_key="today-race-start",
                        assistance_mode="none",
                        notes=None,
                        contributions=[],
                        now_ms=now + 1,
                    )
                else:
                    generate_today(
                        session,
                        idempotency_key="today-race-regenerate",
                        analysis_snapshot_id=run.analysis_snapshot_id,
                        available_time_ms=None,
                        context_costs=(),
                        regenerate=True,
                        now_ms=now + 2,
                    )
                session.commit()
                return "committed"
            except AppError:
                session.rollback()
                return "lost"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(worker, ("start", "regenerate")))
    assert outcomes.count("committed") >= 1
    db.expire_all()
    state = db.get(TodaySuggestionCurrentState, suggestion.id)
    assert state is not None and state.status in {"started", "expired"}
    assert db.scalar(select(func.count(TodayGeneration.id))) == 2
    session_count = db.scalar(select(func.count(LearningSession.id))) or 0
    activity_count = db.scalar(select(func.count(Activity.id))) or 0
    relation_count = db.scalar(select(func.count(SuggestionActivityRelation.id))) or 0
    started_count = (
        db.scalar(
            select(func.count(TodayInteraction.id)).where(
                TodayInteraction.suggestion_id == suggestion.id,
                TodayInteraction.interaction_type == "started",
            )
        )
        or 0
    )
    if state.status == "started":
        assert outcomes == ["committed", "committed"]
        assert (activity_count, session_count, relation_count, started_count) == (1, 1, 1, 1)
        continuing = current_today(db, now_ms=now + 3)["continuingStartedSuggestions"]
        assert [item["id"] for item in continuing] == [suggestion.id]
    else:
        assert outcomes.count("lost") == 1
        assert (activity_count, session_count, relation_count, started_count) == (0, 0, 0, 0)


def test_complete_expire_race_has_one_terminal_truth(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _recommendation_run(db)
    now = utc_now_ms()
    generation = _generate(db, monkeypatch, now_ms=now, key="today-complete-expire-seed")
    suggestion = db.scalar(
        select(TodaySuggestion).where(TodaySuggestion.generation_id == generation.id)
    )
    assert suggestion is not None
    started = start_suggestion(
        db,
        suggestion_id=suggestion.id,
        idempotency_key="today-complete-expire-start",
        assistance_mode="none",
        notes=None,
        contributions=[],
        now_ms=now + 1,
    )
    session = db.get(LearningSession, started.session_id)
    assert session is not None
    _finalize_timed(
        session,
        cancel=False,
        payload=TimedSessionComplete(outcome="completed", difficulty=2, notes=None),
    )
    db.commit()
    maker = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    barrier = Barrier(2)

    def worker(action: str) -> str:
        with maker() as worker_db:
            barrier.wait()
            try:
                if action == "complete":
                    complete_suggestion(
                        worker_db,
                        suggestion_id=suggestion.id,
                        interaction_type="completed",
                        idempotency_key="today-race-complete",
                        session_id=session.id,
                        feedback=None,
                        now_ms=now + 2,
                    )
                else:
                    expire_due_suggestions(
                        worker_db,
                        idempotency_key="today-race-complete-expire",
                        now_ms=suggestion.expires_at + 1,
                    )
                worker_db.commit()
                return "committed"
            except AppError:
                worker_db.rollback()
                return "lost"

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(worker, ("complete", "expire")))
    db.expire_all()
    terminal_rows = db.scalars(
        select(TodayInteraction).where(
            TodayInteraction.suggestion_id == suggestion.id,
            TodayInteraction.interaction_type.in_(("completed", "expired")),
        )
    ).all()
    assert len(terminal_rows) == 1
    state = db.get(TodaySuggestionCurrentState, suggestion.id)
    assert state is not None and state.status in {"completed", "expired"}


def test_start_expire_race_never_fabricates_completion_or_debt(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _recommendation_run(db)
    now = utc_now_ms()
    generation = _generate(db, monkeypatch, now_ms=now, key="today-start-expire-seed")
    suggestion = db.scalar(
        select(TodaySuggestion).where(TodaySuggestion.generation_id == generation.id)
    )
    assert suggestion is not None
    maker = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    barrier = Barrier(2)

    def worker(action: str) -> None:
        with maker() as worker_db:
            barrier.wait()
            try:
                if action == "start":
                    start_suggestion(
                        worker_db,
                        suggestion_id=suggestion.id,
                        idempotency_key="today-race-start-expire-start",
                        assistance_mode="none",
                        notes=None,
                        contributions=[],
                        now_ms=now + 1,
                    )
                else:
                    expire_due_suggestions(
                        worker_db,
                        idempotency_key="today-race-start-expire",
                        now_ms=suggestion.expires_at + 1,
                    )
                worker_db.commit()
            except AppError:
                worker_db.rollback()

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(worker, ("start", "expire")))
    db.expire_all()
    state = db.get(TodaySuggestionCurrentState, suggestion.id)
    assert state is not None and state.status == "expired"
    assert (db.scalar(select(func.count(LearningSession.id))) or 0) <= 1
    assert (
        db.scalar(
            select(func.count(TodayInteraction.id)).where(
                TodayInteraction.suggestion_id == suggestion.id,
                TodayInteraction.interaction_type.in_(
                    ("completed", "partially_completed", "skipped")
                ),
            )
        )
        == 0
    )


def test_replace_expire_race_keeps_exactly_one_terminal_advisory_state(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _recommendation_run(db)
    now = utc_now_ms()
    generation = _generate(db, monkeypatch, now_ms=now, key="today-replace-expire-seed")
    suggestion = db.scalar(
        select(TodaySuggestion).where(TodaySuggestion.generation_id == generation.id)
    )
    assert suggestion is not None
    actual = create_activity_in_uow(
        db,
        title="Actual alternate work",
        description="Truthful alternate Activity.",
        category_stable_key="practice",
        occurred_at=now + 1,
        creator_source="user",
        provenance="user_recorded",
    )
    db.commit()
    maker = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    barrier = Barrier(2)

    def worker(action: str) -> None:
        with maker() as worker_db:
            barrier.wait()
            try:
                if action == "replace":
                    replace_suggestion(
                        worker_db,
                        suggestion_id=suggestion.id,
                        idempotency_key="today-race-replace",
                        activity_id=actual.id,
                        replacement_suggestion_id=None,
                        reason_code="worked_on_something_else",
                        feedback=None,
                        now_ms=suggestion.expires_at + 1,
                    )
                else:
                    expire_due_suggestions(
                        worker_db,
                        idempotency_key="today-race-replace-expire",
                        now_ms=suggestion.expires_at + 1,
                    )
                worker_db.commit()
            except AppError:
                worker_db.rollback()

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(worker, ("replace", "expire")))
    db.expire_all()
    terminal_rows = db.scalars(
        select(TodayInteraction).where(
            TodayInteraction.suggestion_id == suggestion.id,
            TodayInteraction.interaction_type.in_(("replaced", "expired")),
        )
    ).all()
    assert len(terminal_rows) == 1
    state = db.get(TodaySuggestionCurrentState, suggestion.id)
    assert state is not None and state.status in {"replaced", "expired"}
    relation_count = db.scalar(select(func.count(SuggestionActivityRelation.id))) or 0
    assert relation_count == (1 if state.status == "replaced" else 0)


def test_portable_v8_adds_no_fake_today_history_and_rebuilds_current_state(db: Session) -> None:
    package = _portable_payload(db)
    tables, summary = _validate_portable_payload(package, "today-empty-v8", 9)
    assert all(tables[name] == [] for name in PORTABLE_V8_TODAY_TABLES)
    assert summary["compatibilityConversions"] == {}
    assert package["todayV2CurrentCheckpoint"] == {
        "states": [],
        "checkpointHash": content_hash([]),
    }

    db.add(
        TodaySuggestionCurrentState(
            suggestion_id="missing",
            status="suggested",
            event_sequence=0,
            latest_interaction_id=None,
            terminal=False,
            updated_at=0,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
    rebuild_today_current_states(db)
    assert db.scalar(select(func.count(TodaySuggestionCurrentState.suggestion_id))) == 0


def test_v7_to_v8_adapter_initializes_empty_today_without_inference(db: Session) -> None:
    payload = _portable_payload(db)
    payload["manifest"] = PORTABLE_V7_MANIFEST
    payload.pop("todayV2CurrentCheckpoint")
    payload.pop("authorityCheckpoint")
    for table_name in PORTABLE_V8_TODAY_TABLES:
        payload["tables"].pop(table_name)
    for table_name in PORTABLE_V9_AUTHORITY_TABLES:
        payload["tables"].pop(table_name)
    converted, summary = _validate_portable_payload(payload, "today-v7-adapter", 7)
    assert all(converted[name] == [] for name in PORTABLE_V8_TODAY_TABLES)
    assert summary["compatibilityConversions"] == {
        "initializedTodayV2Tables": len(PORTABLE_V8_TODAY_TABLES),
        "nativeTodayHistoryInferred": 0,
        "initializedAuthorityTables": len(PORTABLE_V9_AUTHORITY_TABLES),
        "nativeAuthorityHistoryInferred": 0,
    }


def test_populated_today_v8_serialization_and_checkpoint_tamper_rejection(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _recommendation_run(db)
    now = utc_now_ms()
    generation = _generate(db, monkeypatch, now_ms=now, key="today-portable-generation")
    suggestion = db.scalar(
        select(TodaySuggestion).where(TodaySuggestion.generation_id == generation.id)
    )
    assert suggestion is not None
    record_interaction(
        db,
        suggestion_id=suggestion.id,
        interaction_type="viewed",
        idempotency_key="today-portable-viewed",
        now_ms=now + 1,
    )
    db.commit()
    relation_activity = create_activity_in_uow(
        db,
        title="Portable actual work",
        description="Actual work related explicitly to the suggestion.",
        category_stable_key="practice",
        occurred_at=now + 2,
        creator_source="user",
        provenance="user_recorded",
    )
    relation = link_activity(
        db,
        suggestion_id=suggestion.id,
        activity_id=relation_activity.id,
        relation_type="partially_matched",
        idempotency_key="today-portable-relation",
        now_ms=now + 3,
    )
    repeated_relation = link_activity(
        db,
        suggestion_id=suggestion.id,
        activity_id=relation_activity.id,
        relation_type="partially_matched",
        idempotency_key="today-portable-relation",
        now_ms=now + 4,
    )
    assert repeated_relation.id == relation.id
    db.commit()
    with pytest.raises(AppError, match="key was already used"):
        link_activity(
            db,
            suggestion_id=suggestion.id,
            activity_id=relation_activity.id,
            relation_type="matched",
            idempotency_key="today-portable-relation",
            now_ms=now + 4,
        )
    db.rollback()
    correct_relation(
        db,
        relation_id=relation.id,
        idempotency_key="today-portable-relation-correction",
        correction_type="retracted",
        replacement_activity_id=None,
        replacement_relation_type=None,
        reason="Portable correction history.",
        now_ms=now + 5,
    )
    db.commit()
    payload = _portable_payload(db)
    tables = payload["tables"]
    _validate_today_v2_checkpoint(payload, tables, 8)
    assert len(tables["today_generations"]) == 1
    assert len(tables["today_suggestions"]) == 1
    assert len(tables["today_interactions"]) == 1
    assert len(tables["suggestion_activity_relation_corrections"]) == 1

    tampered = deepcopy(payload)
    tampered["todayV2CurrentCheckpoint"]["states"][0]["status"] = "accepted"
    tampered["todayV2CurrentCheckpoint"]["checkpointHash"] = content_hash(
        tampered["todayV2CurrentCheckpoint"]["states"]
    )
    with pytest.raises(AppError, match="current-state parity"):
        _validate_today_v2_checkpoint(tampered, tampered["tables"], 8)


def test_today_integrity_rejects_frozen_recommendation_and_reason_tampering(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _recommendation_run(db)
    now = utc_now_ms()
    generation = _generate(db, monkeypatch, now_ms=now, key="today-integrity-generation")
    suggestion = db.scalar(
        select(TodaySuggestion).where(TodaySuggestion.generation_id == generation.id)
    )
    assert suggestion is not None
    db.connection().execute(
        TodayGeneration.__table__.update()
        .where(TodayGeneration.id == generation.id)
        .values(request_hash="f" * 64)
    )
    with pytest.raises(AppError, match="generation lineage"):
        _validate_today_v2(db.connection())
    db.rollback()

    legacy_presentation = json.loads(suggestion.presentation_json)
    for field in (
        "competencyIdentityId",
        "targetIdentityId",
        "servedTargetIdentityIds",
    ):
        legacy_presentation.pop(field)
    legacy_presentation_hash = content_hash(legacy_presentation)
    db.connection().execute(
        TodaySuggestion.__table__.update()
        .where(TodaySuggestion.id == suggestion.id)
        .values(
            presentation_json=json.dumps(
                legacy_presentation, sort_keys=True, separators=(",", ":")
            ),
            presentation_hash=legacy_presentation_hash,
            presentation_version=LEGACY_TODAY_PRESENTATION_VERSION,
        )
    )
    db.connection().execute(
        TodayGeneration.__table__.update()
        .where(TodayGeneration.id == generation.id)
        .values(
            output_hash=content_hash(
                [
                    {
                        "ordinal": suggestion.ordinal,
                        "recommendationId": suggestion.recommendation_id,
                        "candidateId": suggestion.candidate_id,
                        "presentationHash": legacy_presentation_hash,
                        "expiresAt": suggestion.expires_at,
                    }
                ]
            )
        )
    )
    _validate_today_v2(db.connection())
    db.rollback()

    db.connection().execute(
        TodaySuggestion.__table__.update()
        .where(TodaySuggestion.id == suggestion.id)
        .values(portfolio_role="complementary")
    )
    with pytest.raises(AppError, match="lineage or expiration"):
        _validate_today_v2(db.connection())
    db.rollback()

    db.connection().execute(
        TodaySuggestion.__table__.update()
        .where(TodaySuggestion.id == suggestion.id)
        .values(advisory_duration_ms=suggestion.duration_minimum_ms)
    )
    with pytest.raises(AppError, match="lineage or expiration"):
        _validate_today_v2(db.connection())
    db.rollback()

    db.connection().execute(
        TodaySuggestion.__table__.update()
        .where(TodaySuggestion.id == suggestion.id)
        .values(presentation_version="today-presentation/tampered")
    )
    with pytest.raises(AppError, match="lineage or expiration"):
        _validate_today_v2(db.connection())
    db.rollback()

    db.connection().execute(
        TodaySuggestion.__table__.update()
        .where(TodaySuggestion.id == suggestion.id)
        .values(today_policy_version="today-policy/tampered")
    )
    with pytest.raises(AppError, match="lineage or expiration"):
        _validate_today_v2(db.connection())
    db.rollback()

    presentation = json.loads(suggestion.presentation_json)
    presentation["title"] = "Tampered presentation"
    db.connection().execute(
        TodaySuggestion.__table__.update()
        .where(TodaySuggestion.id == suggestion.id)
        .values(
            presentation_json=json.dumps(presentation, sort_keys=True, separators=(",", ":")),
            presentation_hash=content_hash(presentation),
        )
    )
    with pytest.raises(AppError, match="exactly freeze"):
        _validate_today_v2(db.connection())
    db.rollback()

    interaction = record_interaction(
        db,
        suggestion_id=suggestion.id,
        interaction_type="viewed",
        idempotency_key="today-integrity-viewed",
        now_ms=now + 1,
    )
    db.commit()
    db.connection().execute(
        TodayInteraction.__table__.update()
        .where(TodayInteraction.id == interaction.id)
        .values(occurred_at=suggestion.created_at - 1)
    )
    with pytest.raises(AppError, match="append-only transition sequence"):
        _validate_today_v2(db.connection())
    db.rollback()

    db.connection().execute(
        TodayInteraction.__table__.update()
        .where(TodayInteraction.id == interaction.id)
        .values(structured_reason_json='"not-an-object"')
    )
    with pytest.raises(AppError, match="structured reason"):
        _validate_today_v2(db.connection())
    db.rollback()


@pytest.mark.asyncio
async def test_today_api_get_is_pure_and_viewed_is_an_explicit_post(
    db: Session,
    authenticated_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, csrf = authenticated_client
    _recommendation_run(db)
    now = utc_now_ms()
    generation = _generate(db, monkeypatch, now_ms=now, key="today-api-generation")
    suggestion = db.scalar(
        select(TodaySuggestion).where(TodaySuggestion.generation_id == generation.id)
    )
    assert suggestion is not None
    before = db.scalar(select(func.count(TodayInteraction.id)))
    response = await client.get("/api/v2/today/current")
    assert response.status_code == 200, response.text
    assert response.json()["generation"]["suggestions"][0]["status"] == "suggested"
    assert db.scalar(select(func.count(TodayInteraction.id))) == before

    viewed = await client.post(
        f"/api/v2/today/suggestions/{suggestion.id}/viewed",
        json={"idempotency_key": "today-api-viewed"},
        headers={"X-CSRF-Token": csrf},
    )
    assert viewed.status_code == 201, viewed.text
    assert viewed.json()["status"] == "viewed"
    assert db.scalar(select(func.count(TodayInteraction.id))) == (before or 0) + 1


def test_today_dependency_boundary_uses_persisted_recommendations_not_policy_internals() -> None:
    source_root = Path(__file__).parents[1] / "app" / "today"
    forbidden_prefixes = (
        "app.analysis",
        "app.analytics",
        "app.capability",
        "app.recommendation.v2.candidates",
        "app.recommendation.v2.eligibility",
        "app.recommendation.v2.policy",
        "app.recommendation.v2.scoring",
    )
    imported: set[str] = set()
    for path in source_root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imported
        for prefix in forbidden_prefixes
    )
    recommendation_imports = {
        module for module in imported if module.startswith("app.recommendation.v2")
    }
    assert recommendation_imports == {"app.recommendation.v2.public"}
