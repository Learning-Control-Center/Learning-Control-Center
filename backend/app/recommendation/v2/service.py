from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.v3.contracts import PublicAnalysisSnapshotDTO
from app.analysis.v3.public import (
    load_current_public_analysis_snapshot,
    load_legacy_recommendation_analysis_snapshot,
    validate_public_analysis_envelope,
)
from app.curriculum.service import build_catalog as build_curriculum_catalog
from app.curriculum.service import unit_availability_as_of
from app.determinism import canonical_json, content_hash
from app.errors import AppError
from app.learning_graph.service import active_projection_graph_as_of
from app.models import AnalysisSnapshot, new_id
from app.profile_views import active_profile_projection_as_of
from app.projects.service import build_catalog as build_project_catalog
from app.recommendation.v2.contracts import (
    CandidateInputDTO,
    RecommendationPolicyOutputDTO,
)
from app.recommendation.v2.input_replay import regenerate_candidates_from_frozen_input
from app.recommendation.v2.models import (
    RecommendationV2Candidate,
    RecommendationV2EligibilityDecision,
    RecommendationV2EligibilityRuleResult,
    RecommendationV2ExpectedValue,
    RecommendationV2Reason,
    RecommendationV2Recommendation,
    RecommendationV2Run,
    RecommendationV2ScoreComponent,
    RecommendationV2SelectionDecision,
)
from app.recommendation.v2.policy import (
    POLICY_REGISTRY_VERSION,
    evaluate_registered,
    registered_candidate_builder,
    registered_policy_bundle,
)
from app.time_utils import utc_now_ms


class RecommendationGenerationFailure(Exception):
    def __init__(
        self,
        original_error: Exception,
        frozen_input: dict[str, Any],
        policy_registry_version: str,
    ) -> None:
        super().__init__(str(original_error))
        self.original_error = original_error
        self.frozen_input = frozen_input
        self.policy_registry_version = policy_registry_version


def _analysis_reference(snapshot: PublicAnalysisSnapshotDTO) -> dict[str, Any]:
    return {
        "id": snapshot.snapshot_id,
        "inputHash": snapshot.input_hash,
        "outputHash": snapshot.output_hash,
        "cutoffAt": snapshot.cutoff_at,
    }


def _require_frozen_analysis_binding(
    db: Session, frozen_input: dict[str, Any], snapshot: PublicAnalysisSnapshotDTO
) -> None:
    if frozen_input.get("analysisSnapshot") != _analysis_reference(snapshot):
        raise AppError(
            409,
            "RECOMMENDATION_ANALYSIS_LINEAGE_INVALID",
            "The frozen Recommendation input is not bound to the referenced Analysis snapshot.",
        )
    public_payload = frozen_input.get("analysisPublicSnapshot")
    if public_payload is not None:
        current_payload = json.loads(canonical_json(asdict(snapshot)))
        if public_payload != current_payload:
            legacy_payload = json.loads(
                canonical_json(
                    asdict(load_legacy_recommendation_analysis_snapshot(db, snapshot.snapshot_id))
                )
            )
            if public_payload != legacy_payload:
                raise AppError(
                    409,
                    "RECOMMENDATION_ANALYSIS_LINEAGE_INVALID",
                    "The frozen public Analysis contract does not match the referenced snapshot.",
                )


def _persist_output(
    db: Session,
    run: RecommendationV2Run,
    output: RecommendationPolicyOutputDTO,
    policy_bundle: dict[str, str],
) -> None:
    decisions = {item.candidate_stable_id: item for item in output.decisions}
    reasons: dict[str, list[Any]] = {}
    for reason in output.reasons:
        reasons.setdefault(reason.candidate_stable_id, []).append(reason)
    recommendations = {item.candidate_stable_id: item for item in output.recommendations}
    for ordinal, evaluated in enumerate(output.candidates):
        item = evaluated.candidate
        duration = item.duration_range_ms or (None, None, None)
        candidate = RecommendationV2Candidate(
            id=new_id(),
            run_id=run.id,
            ordinal=ordinal,
            stable_id=item.stable_id,
            candidate_key=item.candidate_key,
            stable_tie_key=item.stable_tie_key,
            candidate_type=item.candidate_type,
            source_type=item.source_type,
            source_entity_id=item.source_entity_id,
            source_version_id=item.source_version_id,
            title=item.title,
            description=item.description,
            target_identity_id=item.target_identity_id,
            served_target_identity_ids_json=canonical_json(item.served_target_identity_ids),
            primary_need_kind=item.primary_need_kind,
            primary_need_identity=item.primary_need_identity,
            competency_identity_id=item.competency_identity_id,
            criterion_definition_id=item.criterion_definition_id,
            project_id=item.project_id,
            duration_minimum_ms=duration[0],
            duration_preferred_ms=duration[1],
            duration_maximum_ms=duration[2],
            candidate_json=canonical_json(asdict(item)),
        )
        db.add(candidate)
        db.flush()
        db.add(
            RecommendationV2EligibilityDecision(
                candidate_id=candidate.id,
                eligible=evaluated.eligible,
                reason_code=evaluated.eligibility_reason,
                policy_version=policy_bundle["eligibility"],
            )
        )
        for rule_ordinal, rule in enumerate(evaluated.eligibility_rules):
            db.add(
                RecommendationV2EligibilityRuleResult(
                    id=new_id(),
                    candidate_id=candidate.id,
                    ordinal=rule_ordinal,
                    rule_code=rule.code,
                    outcome=rule.outcome,
                    decisive=rule.decisive,
                    facts_json=canonical_json(rule.facts),
                    subject_ids_json=canonical_json(rule.subject_ids),
                    policy_version=policy_bundle["eligibility"],
                )
            )
        db.add(
            RecommendationV2ExpectedValue(
                candidate_id=candidate.id,
                value=evaluated.expected_learning_value,
                reason_codes_json=canonical_json(evaluated.expected_learning_value_reasons),
                matched_facts_json=canonical_json(evaluated.expected_learning_value_facts),
                deciding_rule_code=(
                    evaluated.expected_learning_value_reasons[0]
                    if evaluated.expected_learning_value_reasons
                    else "DECISIVE_LEARNING_VALUE_FACTS_MISSING"
                ),
                policy_version=policy_bundle["expectedLearningValue"],
            )
        )
        for component in evaluated.score_components:
            db.add(
                RecommendationV2ScoreComponent(
                    id=new_id(),
                    candidate_id=candidate.id,
                    component_code=component.code,
                    value=component.value,
                    allowed_minimum=component.allowed_minimum,
                    allowed_maximum=component.allowed_maximum,
                    decisive_facts_json=canonical_json(component.decisive_facts),
                    source_ids_json=canonical_json(component.source_ids),
                    policy_version=policy_bundle["score"],
                )
            )
        decision = decisions[item.stable_id]
        db.add(
            RecommendationV2SelectionDecision(
                candidate_id=candidate.id,
                decision=decision.decision,
                portfolio_role=decision.portfolio_role,
                reason_code=decision.reason_code,
                rank_ordinal=evaluated.rank_ordinal,
                score_total=evaluated.score_total,
                advisory_duration_ms=decision.advisory_duration_ms,
                duration_reason_code=decision.duration_reason_code,
                admission_ordinal=decision.admission_ordinal,
                displaced_by_candidate_stable_id=decision.displaced_by_candidate_stable_id,
                decisive_facts_json=canonical_json(decision.decisive_facts),
                policy_version=policy_bundle["portfolio"],
            )
        )
        for reason in reasons[item.stable_id]:
            db.add(
                RecommendationV2Reason(
                    id=new_id(),
                    candidate_id=candidate.id,
                    ordinal=reason.ordinal,
                    reason_code=reason.reason_code,
                    explanation_facts_json=canonical_json(reason.facts),
                    title=reason.title,
                    score_contribution=reason.score_contribution,
                    template_key=reason.template_key,
                    template_version=reason.template_version,
                    rendered_text=reason.rendered_text,
                    source_ids_json=canonical_json(reason.source_ids),
                    policy_version=policy_bundle["reason"],
                )
            )
        if decision.decision == "selected":
            selected = recommendations[item.stable_id]
            selected_duration = selected.duration_range_ms or (None, None, None)
            db.add(
                RecommendationV2Recommendation(
                    id=new_id(),
                    run_id=run.id,
                    candidate_id=candidate.id,
                    portfolio_role=cast(str, decision.portfolio_role),
                    advisory_duration_ms=decision.advisory_duration_ms,
                    duration_minimum_ms=selected_duration[0],
                    duration_preferred_ms=selected_duration[1],
                    duration_maximum_ms=selected_duration[2],
                    rank_ordinal=selected.rank_ordinal,
                    score_total=selected.score_total,
                    score_breakdown_hash=selected.score_breakdown_hash,
                    selection_reason_code=selected.selection_reason_code,
                    algorithm_version=policy_bundle["algorithm"],
                    analysis_snapshot_id=run.analysis_snapshot_id,
                    presentation_version="recommendation-presentation/v1",
                    reason_summary=(
                        f"{item.title}: {evaluated.expected_learning_value} expected "
                        f"learning value; score {evaluated.score_total}."
                    ),
                )
            )
    db.flush()


def _persist_completed_run(
    db: Session,
    *,
    idempotency_key: str,
    analysis_snapshot_id: str,
    available_time_ms: int | None,
    replay_of_run_id: str | None,
    policy_registry_version: str,
    snapshot: PublicAnalysisSnapshotDTO,
    frozen_input: dict[str, Any],
    candidates: tuple[CandidateInputDTO, ...],
) -> RecommendationV2Run:
    try:
        bundle = registered_policy_bundle(policy_registry_version)
        output = evaluate_registered(policy_registry_version, candidates, available_time_ms)
        generated_at = utc_now_ms()
        frozen_constraints = frozen_input.get(
            "userConstraints", {"availableTimeMs": available_time_ms}
        )
        frozen_projects = frozen_input.get("projects", {})
        project_reference = (
            frozen_projects.get("input_hash") if isinstance(frozen_projects, dict) else None
        )
        run = RecommendationV2Run(
            id=new_id(),
            idempotency_key=idempotency_key,
            analysis_snapshot_id=analysis_snapshot_id,
            generated_at=generated_at,
            cutoff_at=snapshot.cutoff_at,
            local_date=datetime.fromtimestamp(generated_at / 1000, tz=ZoneInfo(snapshot.timezone))
            .date()
            .isoformat(),
            available_time_ms=available_time_ms,
            status="completed",
            algorithm_version=bundle["algorithm"],
            policy_registry_version=policy_registry_version,
            policy_bundle_json=canonical_json(bundle),
            policy_bundle_hash=content_hash(bundle),
            frozen_input_json=canonical_json(frozen_input),
            input_hash=content_hash(frozen_input),
            output_hash=output.output_hash,
            target_profile_version_id=snapshot.target_profile_version_id,
            learning_graph_version_id=snapshot.learning_graph_reference,
            curriculum_reference=snapshot.curriculum_reference,
            project_reference=project_reference,
            user_constraints_hash=content_hash(frozen_constraints),
            semantic_definition_references_json=canonical_json(
                snapshot.semantic_definition_references
            ),
            capability_scale_version_references_json=canonical_json(
                snapshot.capability_scale_version_references
            ),
            completeness=snapshot.completeness,
            application_version=bundle["application"],
            replay_of_run_id=replay_of_run_id,
            failure_metadata_json=None,
        )
        db.add(run)
        db.flush()
        _persist_output(db, run, output, bundle)
        return run
    except Exception as exc:
        if isinstance(exc, RecommendationGenerationFailure):
            raise
        if isinstance(exc, KeyError) and str(exc).strip("'") == policy_registry_version:
            exc = AppError(
                409,
                "RECOMMENDATION_POLICY_UNAVAILABLE",
                "The exact Recommendation policy registry is unavailable.",
            )
        raise RecommendationGenerationFailure(exc, frozen_input, policy_registry_version) from exc


def _generate_recommendations(
    db: Session,
    *,
    idempotency_key: str,
    analysis_snapshot_id: str,
    available_time_ms: int | None,
    replay_of_run_id: str | None = None,
    frozen_input: dict[str, Any] | None = None,
    policy_registry_version: str = POLICY_REGISTRY_VERSION,
    context_costs: tuple[tuple[str, str, str], ...] = (),
) -> RecommendationV2Run:
    existing = db.scalar(
        select(RecommendationV2Run).where(RecommendationV2Run.idempotency_key == idempotency_key)
    )
    if existing is not None:
        requested_constraints = (
            frozen_input.get("userConstraints", {})
            if frozen_input is not None
            else {
                "availableTimeMs": available_time_ms,
                "contextCostPolicy": "explicit-only",
                "contextCosts": [
                    {
                        "sourceType": source_type,
                        "sourceEntityId": source_entity_id,
                        "cost": cost,
                    }
                    for source_type, source_entity_id, cost in sorted(context_costs)
                ],
            }
        )
        if (
            existing.analysis_snapshot_id != analysis_snapshot_id
            or existing.available_time_ms != available_time_ms
            or existing.replay_of_run_id != replay_of_run_id
            or existing.policy_registry_version != policy_registry_version
            or existing.user_constraints_hash != content_hash(requested_constraints)
            or frozen_input is not None
            and existing.input_hash != content_hash(frozen_input)
        ):
            raise AppError(409, "RECOMMENDATION_IDEMPOTENCY_CONFLICT", "The key was already used.")
        if existing.status == "failed":
            raise AppError(
                409,
                "RECOMMENDATION_RUN_PREVIOUSLY_FAILED",
                "The idempotency key belongs to an immutable failed Recommendation run.",
            )
        return existing
    snapshot, analysis_lineage = validate_public_analysis_envelope(db, analysis_snapshot_id)
    if snapshot.purpose not in {"learning_control", "candidate_readiness"}:
        raise AppError(
            422, "RECOMMENDATION_ANALYSIS_PURPOSE_INVALID", "Unsupported Analysis purpose."
        )
    if frozen_input is None:
        snapshot = load_current_public_analysis_snapshot(
            db, analysis_snapshot_id, purpose=snapshot.purpose
        )
        profile = active_profile_projection_as_of(db, exclusive_cutoff_at=snapshot.cutoff_at)
        if (
            (profile is None) != (snapshot.target_profile_version_id is None)
            or profile is not None
            and profile.profile_version_id != snapshot.target_profile_version_id
        ):
            raise AppError(
                409,
                "RECOMMENDATION_PROFILE_LINEAGE_MISMATCH",
                "The exact Analysis TargetProfileVersion is unavailable at its cutoff.",
            )
        graph = active_projection_graph_as_of(db, exclusive_cutoff_at=snapshot.cutoff_at)
        curriculum = build_curriculum_catalog(db, snapshot.cutoff_at)
        projects = build_project_catalog(db, snapshot.cutoff_at)
        availability = tuple(
            unit_availability_as_of(
                db,
                learning_unit_definition_id=unit.unit_definition_id,
                exclusive_cutoff_at=snapshot.cutoff_at,
            )
            for unit in curriculum.units
        )
        lineage_mismatches = [
            name
            for name, current, expected in (
                (
                    "targetProfileVersion",
                    asdict(profile) if profile else None,
                    analysis_lineage.get("profile"),
                ),
                ("learningGraph", asdict(graph) if graph else None, analysis_lineage.get("graph")),
                ("curriculum", asdict(curriculum), analysis_lineage.get("curriculumCatalog")),
                ("projects", asdict(projects), analysis_lineage.get("projectCatalog")),
            )
            if content_hash(current) != content_hash(expected)
        ]
        if (
            lineage_mismatches
            or snapshot.learning_graph_reference
            != (graph.learning_graph_version_id if graph else None)
            or snapshot.curriculum_reference != content_hash(asdict(curriculum))
        ):
            raise AppError(
                409,
                "RECOMMENDATION_INPUT_LINEAGE_MISMATCH",
                "Recommendation inputs do not match the immutable Analysis envelope: "
                + ", ".join(lineage_mismatches or ("declared reference",)),
                {"mismatches": lineage_mismatches},
            )
        frozen_input = {
            "analysisSnapshot": _analysis_reference(snapshot),
            "analysisPublicSnapshot": json.loads(canonical_json(asdict(snapshot))),
            "targetProfileVersion": asdict(profile) if profile else None,
            "learningGraph": asdict(graph) if graph else None,
            "curriculum": asdict(curriculum),
            "curriculumAvailability": [asdict(item) for item in availability],
            "projects": asdict(projects),
            "userConstraints": {
                "availableTimeMs": available_time_ms,
                "contextCostPolicy": "explicit-only",
                "contextCosts": [
                    {
                        "sourceType": source_type,
                        "sourceEntityId": source_entity_id,
                        "cost": cost,
                    }
                    for source_type, source_entity_id, cost in sorted(context_costs)
                ],
            },
            "availableTimeMs": available_time_ms,
        }
        try:
            candidate_builder = registered_candidate_builder(policy_registry_version)
            candidates = (
                candidate_builder(
                    snapshot,
                    profile,
                    curriculum,
                    availability,
                    projects,
                    graph,
                    context_costs,
                )
                if profile is not None
                else ()
            )
        except Exception as exc:
            raise RecommendationGenerationFailure(
                exc, frozen_input, policy_registry_version
            ) from exc
        frozen_input["candidates"] = [asdict(item) for item in candidates]
    else:
        _require_frozen_analysis_binding(db, frozen_input, snapshot)
        candidates = regenerate_candidates_from_frozen_input(frozen_input, policy_registry_version)
    return _persist_completed_run(
        db,
        idempotency_key=idempotency_key,
        analysis_snapshot_id=analysis_snapshot_id,
        available_time_ms=available_time_ms,
        replay_of_run_id=replay_of_run_id,
        policy_registry_version=policy_registry_version,
        snapshot=snapshot,
        frozen_input=frozen_input,
        candidates=candidates,
    )


def generate_recommendations(
    db: Session,
    *,
    idempotency_key: str,
    analysis_snapshot_id: str,
    available_time_ms: int | None,
    context_costs: tuple[tuple[str, str, str], ...] = (),
) -> RecommendationV2Run:
    return _generate_recommendations(
        db,
        idempotency_key=idempotency_key,
        analysis_snapshot_id=analysis_snapshot_id,
        available_time_ms=available_time_ms,
        context_costs=context_costs,
    )


def record_failed_recommendation_run(
    db: Session,
    *,
    idempotency_key: str,
    analysis_snapshot_id: str,
    available_time_ms: int | None,
    replay_of_run_id: str | None,
    error: Exception,
    frozen_input: dict[str, Any] | None = None,
    context_costs: tuple[tuple[str, str, str], ...] = (),
    policy_registry_version: str = POLICY_REGISTRY_VERSION,
) -> RecommendationV2Run | None:
    existing = db.scalar(
        select(RecommendationV2Run).where(RecommendationV2Run.idempotency_key == idempotency_key)
    )
    if existing is not None:
        return existing
    snapshot = db.get(AnalysisSnapshot, analysis_snapshot_id)
    if snapshot is None:
        return None
    try:
        bundle = registered_policy_bundle(policy_registry_version)
    except KeyError:
        return None
    minimal_lineage: dict[str, Any] = {
        "analysisSnapshot": {
            "id": snapshot.id,
            "inputHash": snapshot.input_hash,
            "outputHash": snapshot.output_hash,
            "cutoffAt": snapshot.cutoff_at,
        },
        "availableTimeMs": available_time_ms,
        "userConstraints": {
            "availableTimeMs": available_time_ms,
            "contextCostPolicy": "explicit-only",
            "contextCosts": [
                {"sourceType": source_type, "sourceEntityId": source_id, "cost": cost}
                for source_type, source_id, cost in sorted(context_costs)
            ],
        },
        "replayOfRunId": replay_of_run_id,
    }
    supplied_reference = (frozen_input or {}).get("analysisSnapshot", {})
    failure_lineage = (
        frozen_input
        if frozen_input is not None
        and supplied_reference
        == {
            "id": snapshot.id,
            "inputHash": snapshot.input_hash,
            "outputHash": snapshot.output_hash,
            "cutoffAt": snapshot.cutoff_at,
        }
        and frozen_input.get("availableTimeMs") == available_time_ms
        else minimal_lineage
    )
    frozen_constraints = failure_lineage.get(
        "userConstraints", {"availableTimeMs": available_time_ms}
    )
    frozen_projects = failure_lineage.get("projects", {})
    generated_at = utc_now_ms()
    run = RecommendationV2Run(
        id=new_id(),
        idempotency_key=idempotency_key,
        analysis_snapshot_id=snapshot.id,
        generated_at=generated_at,
        cutoff_at=snapshot.cutoff_at,
        local_date=datetime.fromtimestamp(generated_at / 1000, tz=ZoneInfo(snapshot.timezone))
        .date()
        .isoformat(),
        available_time_ms=available_time_ms,
        status="failed",
        algorithm_version=bundle["algorithm"],
        policy_registry_version=policy_registry_version,
        policy_bundle_json=canonical_json(bundle),
        policy_bundle_hash=content_hash(bundle),
        frozen_input_json=canonical_json(failure_lineage),
        input_hash=content_hash(failure_lineage),
        output_hash=None,
        target_profile_version_id=snapshot.target_profile_version_id,
        learning_graph_version_id=snapshot.learning_graph_reference,
        curriculum_reference=snapshot.curriculum_reference,
        project_reference=(
            frozen_projects.get("input_hash") if isinstance(frozen_projects, dict) else None
        ),
        user_constraints_hash=content_hash(frozen_constraints),
        semantic_definition_references_json=canonical_json(
            json.loads(snapshot.semantic_definition_references_json)
        ),
        capability_scale_version_references_json=canonical_json(
            json.loads(snapshot.capability_scale_version_references_json)
        ),
        completeness=snapshot.completeness,
        application_version=bundle["application"],
        replay_of_run_id=replay_of_run_id,
        failure_metadata_json=canonical_json(
            {
                "code": error.code if isinstance(error, AppError) else "RECOMMENDATION_RUN_FAILED",
                "type": type(error).__name__,
            }
        ),
    )
    db.add(run)
    db.flush()
    return run


def replay_recommendations(
    db: Session, *, run_id: str, idempotency_key: str
) -> RecommendationV2Run:
    original = db.get(RecommendationV2Run, run_id)
    if original is None:
        raise AppError(404, "RECOMMENDATION_V2_RUN_NOT_FOUND", "The run does not exist.")
    if original.status != "completed":
        raise AppError(409, "RECOMMENDATION_REPLAY_UNAVAILABLE", "A failed run cannot be replayed.")
    try:
        historical_bundle = registered_policy_bundle(original.policy_registry_version)
    except KeyError as exc:
        raise AppError(
            409,
            "RECOMMENDATION_POLICY_UNAVAILABLE",
            "The exact historical Recommendation policy registry is unavailable.",
        ) from exc
    if (
        original.policy_bundle_hash != content_hash(historical_bundle)
        or json.loads(original.policy_bundle_json) != historical_bundle
        or original.algorithm_version != historical_bundle["algorithm"]
        or original.application_version != historical_bundle["application"]
        or original.input_hash != content_hash(json.loads(original.frozen_input_json))
    ):
        raise AppError(
            409,
            "RECOMMENDATION_REPLAY_LINEAGE_INVALID",
            "The historical Recommendation run has invalid frozen policy or input lineage.",
        )
    replayed = _generate_recommendations(
        db,
        idempotency_key=idempotency_key,
        analysis_snapshot_id=original.analysis_snapshot_id,
        available_time_ms=original.available_time_ms,
        replay_of_run_id=original.id,
        frozen_input=json.loads(original.frozen_input_json),
        policy_registry_version=original.policy_registry_version,
    )
    if replayed.input_hash != original.input_hash or replayed.output_hash != original.output_hash:
        raise AppError(
            409,
            "RECOMMENDATION_REPLAY_MISMATCH",
            "Historical Recommendation replay did not reproduce the immutable result.",
        )
    return replayed


def recommendation_detail(
    db: Session, run_id: str, *, include_audit: bool = True
) -> dict[str, Any]:
    run = db.get(RecommendationV2Run, run_id)
    if run is None:
        raise AppError(404, "RECOMMENDATION_V2_RUN_NOT_FOUND", "The run does not exist.")
    candidates = db.scalars(
        select(RecommendationV2Candidate)
        .where(RecommendationV2Candidate.run_id == run.id)
        .order_by(RecommendationV2Candidate.ordinal)
    ).all()
    rows: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []
    role_rank = {"primary": 0, "complementary": 1, "maintenance": 2}
    for candidate in candidates:
        eligibility_row = db.get(RecommendationV2EligibilityDecision, candidate.id)
        elv = db.get(RecommendationV2ExpectedValue, candidate.id)
        decision = db.get(RecommendationV2SelectionDecision, candidate.id)
        assert eligibility_row is not None and elv is not None and decision is not None
        components = db.scalars(
            select(RecommendationV2ScoreComponent)
            .where(RecommendationV2ScoreComponent.candidate_id == candidate.id)
            .order_by(RecommendationV2ScoreComponent.component_code)
        ).all()
        rules = db.scalars(
            select(RecommendationV2EligibilityRuleResult)
            .where(RecommendationV2EligibilityRuleResult.candidate_id == candidate.id)
            .order_by(RecommendationV2EligibilityRuleResult.ordinal)
        ).all()
        reason_rows = db.scalars(
            select(RecommendationV2Reason)
            .where(RecommendationV2Reason.candidate_id == candidate.id)
            .order_by(RecommendationV2Reason.ordinal)
        ).all()
        item = {
            "candidateId": candidate.id,
            "stableId": candidate.stable_id,
            "candidateType": candidate.candidate_type,
            "source": {
                "type": candidate.source_type,
                "entityId": candidate.source_entity_id,
                "versionId": candidate.source_version_id,
            },
            "title": candidate.title,
            "description": candidate.description,
            "eligible": eligibility_row.eligible,
            "eligibilityReason": eligibility_row.reason_code,
            "eligibilityRules": [
                {
                    "code": rule.rule_code,
                    "outcome": rule.outcome,
                    "decisive": rule.decisive,
                    "facts": json.loads(rule.facts_json),
                    "subjectIds": json.loads(rule.subject_ids_json),
                }
                for rule in rules
            ],
            "expectedLearningValue": elv.value,
            "expectedLearningValueReasons": json.loads(elv.reason_codes_json),
            "score": decision.score_total,
            "rank": decision.rank_ordinal,
            "scoreComponents": [
                {"code": component.component_code, "value": component.value}
                for component in components
            ],
            "decision": decision.decision,
            "portfolioRole": decision.portfolio_role,
            "decisionReason": decision.reason_code,
            "durationRangeMs": [
                candidate.duration_minimum_ms,
                candidate.duration_preferred_ms,
                candidate.duration_maximum_ms,
            ]
            if candidate.duration_minimum_ms is not None
            else None,
            "advisoryDurationMs": decision.advisory_duration_ms,
            "durationReason": decision.duration_reason_code,
            "reasons": [
                {
                    "code": reason.reason_code,
                    "title": reason.title,
                    "facts": json.loads(reason.explanation_facts_json),
                    "scoreContribution": reason.score_contribution,
                    "templateKey": reason.template_key,
                    "templateVersion": reason.template_version,
                    "renderedText": reason.rendered_text,
                    "sourceIds": json.loads(reason.source_ids_json),
                    "policyVersion": reason.policy_version,
                }
                for reason in reason_rows
            ],
        }
        rows.append(item)
        if decision.decision == "selected":
            selected.append(item)
    selected.sort(key=lambda item: role_rank[cast(str, item["portfolioRole"])])
    payload = {
        "id": run.id,
        "analysisSnapshotId": run.analysis_snapshot_id,
        "generatedAt": run.generated_at,
        "cutoffAt": run.cutoff_at,
        "availableTimeMs": run.available_time_ms,
        "status": run.status,
        "algorithmVersion": run.algorithm_version,
        "policyVersions": json.loads(run.policy_bundle_json),
        "inputHash": run.input_hash,
        "outputHash": run.output_hash,
        "replayOfRunId": run.replay_of_run_id,
        "portfolio": selected,
    }
    if include_audit:
        payload["candidateAudit"] = rows
    return payload


def recommendation_history(db: Session) -> list[dict[str, Any]]:
    runs = db.scalars(
        select(RecommendationV2Run).order_by(
            RecommendationV2Run.generated_at.desc(), RecommendationV2Run.id.desc()
        )
    ).all()
    return [recommendation_detail(db, run.id, include_audit=False) for run in runs]
