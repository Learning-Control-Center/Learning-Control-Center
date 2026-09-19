from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.analysis.v3.contracts import (
    PublicActivityContributionAttributionDTO,
    PublicActivityEvidenceQualificationDTO,
    PublicActualActivitySummaryDTO,
    PublicAllocationFactDTO,
    PublicAnalysisSnapshotDTO,
    PublicCriterionEvaluationFactDTO,
    PublicReadinessGateFactDTO,
    PublicReadinessPredicateFactDTO,
    PublicTargetStateFactDTO,
    freeze_json,
)
from app.analysis.v3.public import load_public_analysis_snapshot
from app.analysis.v3.service import run_analysis
from app.curriculum.contracts import (
    AssessmentRubricPublicDTO,
    CurriculumActionPublicDTO,
    CurriculumAvailabilityPublicDTO,
    CurriculumCatalogPublicDTO,
    CurriculumTargetPublicDTO,
    CurriculumUnitPublicDTO,
    EvidenceOpportunityPublicDTO,
)
from app.determinism import canonical_json, content_hash
from app.domain_integrity import validate_domain_integrity
from app.errors import AppError
from app.import_export import _apply_portable_restore, _portable_payload, _validate_portable_payload
from app.learning_graph.contracts import (
    ActiveLearningGraphProjectionPublicDTO,
    CompetencyEdgeProjectionPublicDTO,
)
from app.models import AnalysisSnapshot
from app.portability.registry import (
    PORTABLE_V6_MANIFEST,
    PORTABLE_V7_RECOMMENDATION_TABLES,
    PORTABLE_V8_TODAY_TABLES,
    PORTABLE_V9_AUTHORITY_TABLES,
)
from app.profile_views import ActiveProfileProjectionPublicDTO, ProfileTargetProjectionPublicDTO
from app.projects.contracts import (
    ProjectBlockerFactPublicDTO,
    ProjectCandidatePublicDTO,
    ProjectCatalogPublicDTO,
    ProjectEvidenceOpportunityPublicDTO,
    ProjectTargetFactPublicDTO,
)
from app.recommendation.v2.candidates import (
    _assessment_blocks_due_hard_unknown,
    _base_candidate,
    _supplies_demonstration_mode,
    _three_valued_and,
    build_candidates,
    canonicalize_candidates,
)
from app.recommendation.v2.contracts import (
    CandidateInputDTO,
    RecommendationRunRequest,
    candidate_from_payload,
)
from app.recommendation.v2.models import (
    RecommendationV2Candidate,
    RecommendationV2Run,
    RecommendationV2ScoreComponent,
    RecommendationV2SelectionDecision,
)
from app.recommendation.v2.policy import (
    LEGACY_POLICY_REGISTRY_VERSION,
    POLICY_REGISTRY_VERSION,
    evaluate,
    evaluate_registered,
    expected_learning_value,
    registered_candidate_builder,
    registered_policy_bundle,
    score,
)
from app.recommendation.v2.service import (
    RecommendationGenerationFailure,
    _persist_completed_run,
    record_failed_recommendation_run,
    replay_recommendations,
)
from app.recommendation.v2.service import (
    generate_recommendations as generate_live_recommendations,
)
from app.time_utils import utc_now_ms
from app.today.models import TodaySuggestion
from app.today.service import (
    correct_interaction,
    correct_relation,
    generate_today,
    link_activity,
    record_interaction,
)
from app.v2_activities import create_activity_in_uow
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

MINUTE = 60_000


def migration_config(database_path: Path) -> Config:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


def candidate(stable_id: str, **overrides: object) -> CandidateInputDTO:
    values: dict[str, object] = {
        "candidate_type": "curriculum_unit",
        "candidate_key": f"candidate-key/v1|{stable_id}",
        "stable_id": stable_id,
        "stable_tie_key": f"candidate|{stable_id}",
        "source_type": "curriculum_unit",
        "source_entity_id": stable_id,
        "source_version_id": "version-1",
        "title": stable_id,
        "description": "Deterministic candidate",
        "target_identity_id": f"target-{stable_id}",
        "served_target_identity_ids": (f"target-{stable_id}",),
        "primary_outcome_kind": "target",
        "primary_outcome_id": f"target-{stable_id}",
        "primary_need_identity": f"need-{stable_id}",
        "competency_identity_id": f"competency-{stable_id}",
        "criterion_definition_id": None,
        "project_id": None,
        "target_priority": "important",
        "gap_severity": "medium",
        "deadline_status": "none",
        "allocation_miss_basis_points": None,
        "neglect_or_stall_severity": None,
        "maintenance_severity": None,
        "context_cost": "none",
        "context_available": True,
        "last_meaningful_activity_at": None,
        "review_due": False,
        "assessment_unknown": False,
        "primary_need_kind": "capability_gap",
        "hard_blocker_count": 0,
        "multi_target_unblock": False,
        "missing_independence": False,
        "missing_evidence_mode": False,
        "supports_transfer": False,
        "addresses_unmet_required_criterion": False,
        "supplies_missing_required_mode": False,
        "supplies_missing_independent_mode": False,
        "supports_transfer_target_ids": (),
        "assessment_blocks_critical_planning": False,
        "repeated_without_new_evidence": False,
        "recently_saturated": False,
        "active_target": True,
        "prerequisites_satisfied": True,
        "hard_readiness_satisfied": True,
        "prerequisite_reference_ids": (),
        "readiness_reference_ids": (),
        "availability_requirement_ids": (),
        "availability_satisfied": True,
        "capability_suitability_satisfied": True,
        "blocker_clear": True,
        "active_source": True,
        "target_reached": False,
        "blocker_actionable": False,
        "blocker_reference_id": f"blocker-{stable_id}",
        "unblock_action_available": True,
        "verification_due": False,
        "verification_template_present": False,
        "assessment_rubric_present": False,
        "assessment_scope_valid": True,
        "intended_evidence_modes": ("independent",),
        "duration_range_ms": (10 * MINUTE, 20 * MINUTE, 30 * MINUTE),
        "usefulness": True,
        "explanation_facts": (("fixture", stable_id),),
    }
    values.update(overrides)
    return CandidateInputDTO(**values)  # type: ignore[arg-type]


def public_target(target_id: str, **overrides: object) -> PublicTargetStateFactDTO:
    values: dict[str, object] = {
        "stable_key": f"target|{target_id}",
        "fact_type": "target_state",
        "target_identity_id": target_id,
        "competency_identity_id": f"competency-{target_id}",
        "dimension_key": None,
        "profile_target_id": f"profile-target-{target_id}",
        "semantic_definition_id": f"semantic-{target_id}",
        "scale_version_id": "scale-v1",
        "target_level_id": "level-2",
        "target_level_ordinal": 2,
        "current_level_id": "level-1",
        "current_level_ordinal": 1,
        "assessment_status": "unknown",
        "priority": "core",
        "comparison_status": "below_target",
        "confidence": "medium",
        "freshness": "current",
        "review_due": False,
        "deadline_status": "none",
        "deadline_date": None,
        "unmet_required_criterion_ids": (),
        "partial_required_criterion_ids": (),
        "contradicted_required_criterion_ids": (),
        "contradicted_important_criterion_ids": (),
        "open_lesser_criterion_ids": (),
        "missing_evidence_requirement_ids": (),
        "missing_independent_criterion_ids": (),
        "important_supporting_missing_criterion_ids": (),
        "supporting_evidence_ids": (),
        "contradicting_evidence_ids": (),
        "independent_evidence_ids": (),
        "criterion_evaluations": (),
        "readiness_gates": (),
        "prerequisites": (),
        "days_since_meaningful_activity": 5,
        "last_meaningful_activity_at": 1,
        "exposure_days_42": 2,
        "stall_eligible": False,
        "days_since_positive_transition": None,
        "critical_gate_due": False,
        "input_lineage": (),
        "payload": (),
    }
    values.update(overrides)
    return PublicTargetStateFactDTO(**values)  # type: ignore[arg-type]


def profile_for_targets(
    *targets: PublicTargetStateFactDTO,
) -> ActiveProfileProjectionPublicDTO:
    projections = tuple(
        ProfileTargetProjectionPublicDTO(
            item.profile_target_id,
            item.target_identity_id,
            item.stable_key,
            item.competency_identity_id,
            item.dimension_key,
            item.dimension_id,
            f"domain-{item.target_identity_id}",
            item.scale_version_id,
            item.target_level_id,
            item.target_level_ordinal,
            item.priority,
            item.deadline_date,
            None,
            None,
            1,
        )
        for item in targets
    )
    return ActiveProfileProjectionPublicDTO(
        "profile", "profile-v1", 1, 1, "activation", 1, (), projections, (), ()
    )


def public_snapshot(
    *facts: PublicTargetStateFactDTO | PublicAllocationFactDTO,
    input_lineage: object = (),
    actual_activity_summaries: tuple[PublicActualActivitySummaryDTO, ...] = (),
) -> PublicAnalysisSnapshotDTO:
    return PublicAnalysisSnapshotDTO(
        "snapshot",
        "analysis-run",
        "learning_control",
        3,
        1,
        2,
        "exclusive",
        "UTC",
        "1970-01-01",
        "complete",
        "input",
        "output",
        (),
        "profile",
        "profile-v1",
        None,
        "curriculum",
        tuple(
            sorted(
                {
                    item.semantic_definition_id
                    for item in facts
                    if isinstance(item, PublicTargetStateFactDTO)
                    and item.semantic_definition_id is not None
                }
            )
        ),
        tuple(
            sorted(
                {
                    item.scale_version_id
                    for item in facts
                    if isinstance(item, PublicTargetStateFactDTO)
                }
            )
        ),
        "discipline",
        "configuration",
        "2.0.0",
        input_lineage,  # type: ignore[arg-type]
        facts,
        (),
        (),
        (),
        actual_activity_summaries,
    )


def test_approved_scoring_goldens_and_bounds() -> None:
    later = candidate("later", deadline_status="later")
    components = score(later, "normal")
    assert {item.code: item.value for item in components}["DEADLINE_PRESSURE"] == 0
    assert len(components) == 7

    minimum = candidate(
        "minimum",
        target_priority="optional",
        gap_severity="unknown",
        allocation_miss_basis_points=-1_500,
        context_cost="high",
        intended_evidence_modes=(),
    )
    minimum_output = evaluate((minimum,), None).candidates[0]
    assert minimum_output.expected_learning_value == "unknown"
    assert minimum_output.score_total == -10

    maximum = candidate(
        "maximum",
        target_priority="critical",
        gap_severity="critical",
        deadline_status="overdue",
        allocation_miss_basis_points=1_500,
        neglect_or_stall_severity="critical",
        missing_evidence_mode=True,
        addresses_unmet_required_criterion=True,
        supplies_missing_required_mode=True,
    )
    maximum_output = evaluate((maximum,), None).candidates[0]
    assert maximum_output.expected_learning_value == "very_high"
    assert maximum_output.score_total == 86


def test_primary_need_is_one_mutually_exclusive_component() -> None:
    item = candidate(
        "assessment",
        candidate_type="assessment",
        assessment_unknown=True,
        primary_need_kind="assessment_unknown",
        assessment_rubric_present=True,
        target_priority="critical",
        gap_severity="critical",
        hard_blocker_count=1,
        duration_range_ms=None,
    )
    output = evaluate((item,), None).candidates[0]
    primary_need = [
        component for component in output.score_components if component.code == "PRIMARY_NEED"
    ]
    assert len(primary_need) == 1
    assert primary_need[0].value == 17

    mappings = (
        (
            candidate(
                "readiness",
                candidate_type="unblock_task",
                primary_need_kind="readiness_unblock",
                blocker_actionable=True,
            ),
            13,
        ),
        (
            candidate(
                "prerequisite",
                candidate_type="unblock_task",
                primary_need_kind="prerequisite_unblock",
                blocker_actionable=True,
                hard_blocker_count=1,
            ),
            17,
        ),
        (
            candidate(
                "multi",
                candidate_type="unblock_task",
                primary_need_kind="multi_target_unblock",
                blocker_actionable=True,
                multi_target_unblock=True,
            ),
            22,
        ),
        (
            candidate(
                "aging",
                candidate_type="maintenance",
                primary_need_kind="maintenance_aging_critical",
                target_reached=True,
                review_due=False,
            ),
            4,
        ),
    )
    for mapped_candidate, expected in mappings:
        evaluated = evaluate((mapped_candidate,), None).candidates[0]
        component = next(item for item in evaluated.score_components if item.code == "PRIMARY_NEED")
        assert component.value == expected


@pytest.mark.parametrize(
    ("overrides", "available_time_ms", "reason_code"),
    (
        ({"active_target": False}, None, "TARGET_INACTIVE"),
        ({"prerequisites_satisfied": False}, None, "PREREQUISITE_UNSATISFIED"),
        ({"prerequisites_satisfied": None}, None, "PREREQUISITE_UNKNOWN"),
        ({"hard_readiness_satisfied": False}, None, "READINESS_GATE_NOT_MET"),
        ({"hard_readiness_satisfied": None}, None, "READINESS_GATE_UNKNOWN"),
        ({"availability_satisfied": False}, None, "ACTION_UNAVAILABLE"),
        ({"availability_satisfied": None}, None, "ACTION_UNAVAILABLE"),
        ({"capability_suitability_satisfied": False}, None, "CAPABILITY_UNSUITABLE"),
        ({"capability_suitability_satisfied": None}, None, "CAPABILITY_UNKNOWN"),
        ({"active_source": False}, None, "CURRICULUM_ACTION_MISSING"),
        ({"context_available": False}, None, "CONTEXT_UNAVAILABLE"),
        ({"target_reached": True}, None, "TARGET_ALREADY_REACHED"),
        (
            {
                "candidate_type": "verification",
                "verification_due": False,
                "verification_template_present": True,
            },
            None,
            "VERIFICATION_NOT_READY",
        ),
        (
            {
                "candidate_type": "assessment",
                "primary_need_kind": "assessment_unknown",
                "assessment_unknown": True,
                "assessment_rubric_present": False,
                "duration_range_ms": None,
            },
            None,
            "ASSESSMENT_RUBRIC_MISSING",
        ),
        (
            {
                "candidate_type": "assessment",
                "primary_need_kind": "assessment_unknown",
                "assessment_unknown": True,
                "assessment_rubric_present": True,
                "assessment_scope_valid": False,
                "duration_range_ms": None,
            },
            None,
            "ASSESSMENT_SCOPE_INVALID",
        ),
        (
            {
                "candidate_type": "unblock_task",
                "primary_need_kind": "readiness_unblock",
                "blocker_actionable": False,
            },
            None,
            "BLOCKER_NOT_ACTIONABLE",
        ),
        (
            {
                "candidate_type": "unblock_task",
                "primary_need_kind": "readiness_unblock",
                "blocker_actionable": True,
                "blocker_reference_id": None,
            },
            None,
            "BLOCKER_REFERENCE_MISSING",
        ),
        (
            {
                "candidate_type": "unblock_task",
                "primary_need_kind": "readiness_unblock",
                "blocker_actionable": True,
                "unblock_action_available": False,
            },
            None,
            "UNBLOCK_ACTION_UNAVAILABLE",
        ),
        ({"duration_range_ms": None}, 30 * MINUTE, "ACTION_UNAVAILABLE"),
    ),
)
def test_every_hard_eligibility_rejection_is_explicit(
    overrides: dict[str, object], available_time_ms: int | None, reason_code: str
) -> None:
    output = evaluate((candidate("eligibility", **overrides),), available_time_ms)
    evaluated = output.candidates[0]
    assert evaluated.eligible is False
    assert evaluated.score_components == ()
    assert output.decisions[0].reason_code == reason_code
    assert [item.reason_code for item in output.reasons].count(reason_code) == 1


def test_expected_learning_value_ordered_rules_are_exact() -> None:
    assert expected_learning_value(
        candidate(
            "assessment-critical",
            candidate_type="assessment",
            primary_need_kind="assessment_unknown",
            assessment_unknown=True,
            assessment_blocks_critical_planning=True,
        )
    ) == ("high", ("UNKNOWN_BLOCKS_CRITICAL_PLANNING",))
    assert expected_learning_value(
        candidate("multi-unblock", multi_target_unblock=True, repeated_without_new_evidence=True)
    ) == ("very_high", ("MULTI_TARGET_HARD_UNBLOCK",))
    assert expected_learning_value(
        candidate(
            "required-mode",
            target_priority="core",
            addresses_unmet_required_criterion=True,
            supplies_missing_required_mode=True,
        )
    ) == ("very_high", ("REQUIRED_GAP_AND_MISSING_EVIDENCE_MODE",))
    assert expected_learning_value(
        candidate("required", addresses_unmet_required_criterion=True)
    ) == ("high", ("UNMET_REQUIRED_OUTCOME",))
    assert expected_learning_value(candidate("repeated", repeated_without_new_evidence=True)) == (
        "low",
        ("REPEATED_WITHOUT_NEW_EVIDENCE",),
    )
    assert expected_learning_value(
        candidate("unknown-elv", gap_severity="unknown", intended_evidence_modes=())
    ) == ("unknown", ("DECISIVE_LEARNING_VALUE_FACTS_MISSING",))
    assert expected_learning_value(candidate("normal-elv")) == (
        "normal",
        ("NORMAL_EXPECTED_LEARNING_VALUE",),
    )

    assert expected_learning_value(
        candidate(
            "generic-required-mode",
            target_priority="important",
            addresses_unmet_required_criterion=True,
            supplies_missing_required_mode=True,
        )
    ) == (
        "high",
        ("UNMET_REQUIRED_OUTCOME",),
    )
    assert expected_learning_value(
        candidate(
            "independent-mode",
            supplies_missing_independent_mode=True,
        )
    ) == ("high", ("MISSING_INDEPENDENT_EVIDENCE",))

    not_useful = evaluate((candidate("not-useful", usefulness=False),), None)
    assert not_useful.decisions[0].reason_code == "NOT_USEFUL"
    assert dict(not_useful.decisions[0].decisive_facts) == {
        "selectionConstraint": "usefulness",
        "usefulness": False,
        "primaryOutcomeId": "target-not-useful",
    }


def test_generated_candidate_uses_exact_primary_outcome_lineage() -> None:
    criterion = PublicCriterionEvaluationFactDTO(
        "criterion-a",
        "criterion-identity-a",
        "required",
        "level-2",
        "unknown",
        "independent_performance",
        (),
        (),
    )
    gate = PublicReadinessGateFactDTO(
        "gate-a",
        "unknown",
        "hard_eligibility",
        "due",
        "2026-01-01",
        (
            PublicReadinessPredicateFactDTO(
                "predicate-a",
                "criterion_demonstrated",
                "required",
                "unknown",
                "CRITERION_UNKNOWN",
                freeze_json({"criterionIdentityId": "criterion-identity-a"}),
            ),
        ),
    )
    primary = public_target(
        "primary",
        competency_identity_id="competency-primary",
        dimension_key="backend",
        dimension_id="dimension-a",
        scale_stable_key="technical",
        scale_version="v1",
        unmet_required_criterion_ids=("criterion-a",),
        missing_evidence_requirement_ids=("criterion-a",),
        missing_independent_criterion_ids=("criterion-a",),
        criterion_evaluations=(criterion,),
        readiness_gates=(gate,),
        last_meaningful_activity_at=999,
    )
    secondary = public_target("secondary", priority="critical")
    activity_summaries = (
        PublicActualActivitySummaryDTO(
            "activity-a",
            150,
            "competency-primary",
            ("competency-primary",),
            (
                PublicActivityEvidenceQualificationDTO(
                    "competency-primary", "dimension-a", "criterion-a"
                ),
            ),
        ),
        PublicActualActivitySummaryDTO(
            "activity-attributed-no-evidence",
            200,
            "competency-primary",
            ("competency-primary",),
            (),
            (
                PublicActivityContributionAttributionDTO(
                    "competency-primary", "dimension-a", "criterion-a", "primary"
                ),
            ),
        ),
        PublicActualActivitySummaryDTO(
            "activity-other",
            999,
            "competency-primary",
            ("competency-primary",),
            (
                PublicActivityEvidenceQualificationDTO(
                    "competency-primary", "dimension-a", "criterion-other"
                ),
            ),
        ),
        PublicActualActivitySummaryDTO(
            "activity-wrong-dimension",
            1_500,
            "competency-primary",
            ("competency-primary",),
            (
                PublicActivityEvidenceQualificationDTO(
                    "competency-primary", "dimension-other", "criterion-a"
                ),
            ),
        ),
    )
    primary_allocation = PublicAllocationFactDTO(
        "allocation-primary", "allocation", "domain-primary", 1, 1, 1, 1, 1, -700, True, ()
    )
    secondary_allocation = PublicAllocationFactDTO(
        "allocation-secondary",
        "allocation",
        "domain-secondary",
        1,
        1,
        1,
        1,
        1,
        -2_000,
        True,
        (),
    )
    snapshot = public_snapshot(
        primary,
        secondary,
        primary_allocation,
        secondary_allocation,
        actual_activity_summaries=activity_summaries,
    )
    profile = profile_for_targets(primary, secondary)
    generated = _base_candidate(
        snapshot=snapshot,
        profile=profile,
        target=primary,
        candidate_type="assessment",
        stable_id="assessment|a",
        source_type="assessment_rubric",
        source_entity_id="rubric-a",
        source_version_id="curriculum-v1",
        title="Assess criterion A",
        description="Assess exact criterion.",
        criterion_definition_id="criterion-a",
        project_id=None,
        duration_range_ms=None,
        active_source=True,
        availability=True,
        readiness=True,
        intended_modes=("independent",),
        assessment_rubric_present=True,
        assessment_scope_valid=True,
        served_targets=(secondary,),
    )
    assert generated.last_meaningful_activity_at == 200
    assert generated.allocation_miss_basis_points == 700
    assert generated.assessment_blocks_critical_planning is True
    assert generated.supplies_missing_required_mode is True
    assert generated.supplies_missing_independent_mode is True
    saturated_target = replace(
        primary,
        assessment_status="assessed",
        unmet_required_criterion_ids=(),
        missing_evidence_requirement_ids=(),
        missing_independent_criterion_ids=(),
        criterion_evaluations=(replace(criterion, state="demonstrated"),),
        readiness_gates=(),
        days_since_meaningful_activity=2,
        exposure_days_42=3,
    )
    repeated = _base_candidate(
        snapshot=public_snapshot(saturated_target),
        profile=profile_for_targets(saturated_target),
        target=saturated_target,
        candidate_type="practice_task",
        stable_id="practice|repeated",
        source_type="curriculum_unit",
        source_entity_id="unit-repeated",
        source_version_id="curriculum-v1",
        title="Repeat criterion A",
        description="Repeat already demonstrated material.",
        criterion_definition_id="criterion-a",
        project_id=None,
        duration_range_ms=(10 * MINUTE, 20 * MINUTE, 30 * MINUTE),
        active_source=True,
        availability=True,
        readiness=True,
        intended_modes=("independent",),
    )
    assert repeated.repeated_without_new_evidence is True
    assert repeated.recently_saturated is True
    assert expected_learning_value(repeated) == (
        "low",
        ("REPEATED_WITHOUT_NEW_EVIDENCE",),
    )
    evaluated = evaluate((repeated,), None).candidates[0]
    elv_facts = dict(evaluated.expected_learning_value_facts)
    assert elv_facts["repeatedWithoutNewEvidence"] is True
    assert elv_facts["recentlySaturated"] is True
    assert elv_facts["daysSinceMeaningfulActivity"] == 2
    assert elv_facts["exposureDays42"] == 3
    assert elv_facts["recentSaturationMaximumDays"] == 2
    assert elv_facts["recentSaturationMinimumExposureDays"] == 3
    assert (
        _supplies_demonstration_mode("curriculum_unit", "independent_performance", ("guided",))
        is False
    )
    supporting_gate = replace(
        gate,
        predicates=(replace(gate.predicates[0], requirement_type="supporting"),),
    )
    assert (
        _assessment_blocks_due_hard_unknown(
            replace(primary, readiness_gates=(supporting_gate,)), "criterion-a"
        )
        is False
    )
    capability_gate = replace(
        gate,
        predicates=(
            PublicReadinessPredicateFactDTO(
                "predicate-capability",
                "capability_at_least",
                "required",
                "unknown",
                "CAPABILITY_UNKNOWN",
                freeze_json(
                    {
                        "competencyIdentityId": "competency-primary",
                        "dimensionKey": "backend",
                        "scaleStableKey": "technical",
                        "scaleVersion": "v1",
                        "levelStableKey": "guided",
                    }
                ),
            ),
        ),
    )
    assert (
        _assessment_blocks_due_hard_unknown(
            replace(primary, readiness_gates=(capability_gate,)), None
        )
        is True
    )
    wrong_scale_gate = replace(
        capability_gate,
        predicates=(
            replace(
                capability_gate.predicates[0],
                subject=freeze_json(
                    {
                        "competencyIdentityId": "competency-primary",
                        "dimensionKey": "backend",
                        "scaleStableKey": "technical",
                        "scaleVersion": "v2",
                        "levelStableKey": "guided",
                    }
                ),
            ),
        ),
    )
    assert (
        _assessment_blocks_due_hard_unknown(
            replace(primary, readiness_gates=(wrong_scale_gate,)), None
        )
        is False
    )
    assert (
        _assessment_blocks_due_hard_unknown(
            replace(primary, readiness_gates=(replace(gate, state="not_met"),)),
            "criterion-a",
        )
        is False
    )


def test_project_candidate_preserves_authored_evidence_independence_modes() -> None:
    criterion = PublicCriterionEvaluationFactDTO(
        "criterion-a",
        "criterion-identity-a",
        "required",
        "level-2",
        "unknown",
        "independent_performance",
        (),
        (),
    )
    target = public_target(
        "project-target",
        semantic_definition_id="semantic-project",
        unmet_required_criterion_ids=("criterion-a",),
        missing_evidence_requirement_ids=("criterion-a",),
        missing_independent_criterion_ids=("criterion-a",),
        criterion_evaluations=(criterion,),
    )
    project_target = ProjectTargetFactPublicDTO(
        "project-target-fact",
        "semantic-project",
        "criterion-a",
        "scale-v1",
        None,
        None,
        "Deliver criterion A",
        "primary",
    )
    project = ProjectCandidatePublicDTO(
        "project_task",
        "project",
        "project",
        "project-v1",
        "task-identity",
        "task-definition",
        "task",
        "Build project",
        "Deliver criterion A",
        "Build it",
        (10 * MINUTE, 20 * MINUTE, 30 * MINUTE),
        "active",
        "met",
        "met",
        "met",
        (),
        (),
        (),
        (project_target.target_id,),
        (project_target,),
        "project-hash",
        ("opportunity-a",),
        ("independent",),
        (
            ProjectEvidenceOpportunityPublicDTO(
                "opportunity-a", "task-definition", None, ("independent",)
            ),
        ),
    )
    generated = build_candidates(
        public_snapshot(target),
        profile_for_targets(target),
        CurriculumCatalogPublicDTO.build(
            cutoff_at=2,
            active_version_references=(),
            objectives=(),
            units=(),
            assessment_rubrics=(),
        ),
        (),
        ProjectCatalogPublicDTO.build(
            cutoff_at=2,
            active_version_references=(),
            candidates=(project,),
        ),
        None,
    )
    project_result = next(item for item in generated if item.candidate_type == "project_task")
    assert project_result.intended_evidence_modes == ("independent",)
    assert project_result.supplies_missing_required_mode is True
    assert project_result.supplies_missing_independent_mode is True


def test_three_valued_hard_readiness_preserves_known_failure() -> None:
    assert _three_valued_and(False, None) is False
    assert _three_valued_and(True, None) is None
    assert _three_valued_and(True, True) is True


def test_hard_rule_audit_preserves_exact_requirement_and_blocker_references() -> None:
    blocked = candidate(
        "audit-references",
        prerequisites_satisfied=False,
        prerequisite_reference_ids=("edge-2", "edge-1"),
        readiness_reference_ids=("gate-1", "predicate-1"),
        availability_requirement_ids=("requirement-1",),
    )
    result = evaluate((blocked,), None).candidates[0]
    prerequisite = next(rule for rule in result.eligibility_rules if rule.code == "PREREQUISITES")
    assert prerequisite.subject_ids == ("edge-1", "edge-2")

    unblock = candidate(
        "audit-blocker",
        candidate_type="unblock_task",
        primary_need_kind="readiness_unblock",
        blocker_actionable=False,
        blocker_reference_id="blocker-event",
    )
    unblock_result = evaluate((unblock,), None).candidates[0]
    blocker_rule = next(
        rule for rule in unblock_result.eligibility_rules if rule.code == "ACTIONABLE_BLOCKER"
    )
    assert blocker_rule.subject_ids == ("blocker-event",)


def test_contract_tie_break_order_is_total_and_deterministic() -> None:
    pairs = (
        (
            candidate("core", target_priority="core"),
            candidate("critical", target_priority="critical", context_cost="high"),
            "critical",
        ),
        (
            candidate("no-deadline"),
            candidate(
                "due-soon",
                deadline_status="due_soon",
                context_cost="high",
                allocation_miss_basis_points=-1,
            ),
            "due-soon",
        ),
        (
            candidate("unknown-elv-tie", gap_severity="unknown", intended_evidence_modes=()),
            candidate(
                "normal-elv-tie",
                gap_severity="unknown",
                allocation_miss_basis_points=-500,
            ),
            "normal-elv-tie",
        ),
        (
            candidate("practiced", last_meaningful_activity_at=1),
            candidate("never", last_meaningful_activity_at=None),
            "never",
        ),
        (
            candidate("newer", last_meaningful_activity_at=2),
            candidate("older", last_meaningful_activity_at=1),
            "older",
        ),
        (candidate("z-stable"), candidate("a-stable"), "a-stable"),
    )
    for left, right, expected_first in pairs:
        ranked = evaluate((left, right), None).candidates
        first = min(ranked, key=lambda item: item.rank_ordinal or 999)
        assert first.candidate.stable_id == expected_first


def test_candidate_key_collision_fails_closed() -> None:
    original = candidate("collision")
    conflict = replace(original, title="Different meaning")
    with pytest.raises(AppError, match="canonical key"):
        canonicalize_candidates((original, conflict))


def test_candidate_key_merge_uses_only_deterministic_sorted_set_unions() -> None:
    original = candidate(
        "merge",
        served_target_identity_ids=("target-b",),
        supports_transfer_target_ids=("target-z",),
        prerequisite_reference_ids=("edge-b",),
        explanation_facts=(("shared", True),),
    )
    duplicate = replace(
        original,
        served_target_identity_ids=("target-a",),
        supports_transfer_target_ids=("target-y",),
        prerequisite_reference_ids=("edge-a",),
    )
    merged = canonicalize_candidates((original, duplicate))
    assert len(merged) == 1
    assert merged[0].served_target_identity_ids == ("target-a", "target-b")
    assert merged[0].supports_transfer_target_ids == ("target-y", "target-z")
    assert merged[0].prerequisite_reference_ids == ("edge-a", "edge-b")


def test_candidate_builder_covers_all_eight_contract_types() -> None:
    def target(target_id: str, *, review_due: bool, freshness: str) -> PublicTargetStateFactDTO:
        return PublicTargetStateFactDTO(
            stable_key=f"target|{target_id}",
            fact_type="target_state",
            target_identity_id=target_id,
            competency_identity_id=f"competency-{target_id}",
            dimension_key=None,
            profile_target_id=f"profile-target-{target_id}",
            semantic_definition_id=f"semantic-{target_id}",
            scale_version_id="scale-v1",
            target_level_id="level-2",
            target_level_ordinal=2,
            current_level_id="level-1",
            current_level_ordinal=1,
            assessment_status="unknown",
            priority="critical",
            comparison_status="below_target",
            confidence="medium",
            freshness=freshness,  # type: ignore[arg-type]
            review_due=review_due,
            deadline_status="none",
            deadline_date=None,
            unmet_required_criterion_ids=(),
            partial_required_criterion_ids=(),
            contradicted_required_criterion_ids=(),
            contradicted_important_criterion_ids=(),
            open_lesser_criterion_ids=(),
            missing_evidence_requirement_ids=(),
            missing_independent_criterion_ids=(),
            important_supporting_missing_criterion_ids=(),
            supporting_evidence_ids=(),
            contradicting_evidence_ids=(),
            independent_evidence_ids=(),
            criterion_evaluations=(),
            readiness_gates=(),
            prerequisites=(),
            days_since_meaningful_activity=5,
            last_meaningful_activity_at=1,
            exposure_days_42=2,
            stall_eligible=False,
            days_since_positive_transition=None,
            critical_gate_due=False,
            input_lineage=(),
            payload=(),
        )

    review_target = target("review", review_due=True, freshness="stale")
    maintenance_target = target("maintenance", review_due=False, freshness="aging")
    snapshot = PublicAnalysisSnapshotDTO(
        snapshot_id="snapshot",
        run_id="analysis-run",
        purpose="learning_control",
        schema_version=3,
        generated_at=1,
        cutoff_at=2,
        cutoff_semantics="exclusive",
        timezone="UTC",
        completed_through_date="1970-01-01",
        completeness="complete",
        input_hash="input",
        output_hash="output",
        policy_versions=(),
        target_profile_id="profile",
        target_profile_version_id="profile-v1",
        learning_graph_reference=None,
        curriculum_reference="curriculum",
        semantic_definition_references=("semantic-review", "semantic-maintenance"),
        capability_scale_version_references=("scale-v1",),
        discipline_configuration_reference="discipline",
        configuration_hash="configuration",
        application_version="2.0.0",
        input_lineage=(),
        facts=(review_target, maintenance_target),
        gaps=(),
        signals=(),
        unknown_markers=(),
    )
    profile_targets = tuple(
        ProfileTargetProjectionPublicDTO(
            id=item.profile_target_id,
            target_identity_id=item.target_identity_id,
            stable_key=item.stable_key,
            competency_identity_id=item.competency_identity_id,
            dimension_key=None,
            dimension_id=None,
            profile_domain_id="domain",
            scale_version_id="scale-v1",
            target_level_id="level-2",
            target_level_ordinal=2,
            priority="critical",
            target_date=None,
            target_month=None,
            freshness_override_days=None,
            activated_at=1,
        )
        for item in (review_target, maintenance_target)
    )
    profile = ActiveProfileProjectionPublicDTO(
        "profile", "profile-v1", 1, 1, "activation", 1, (), profile_targets, (), ()
    )
    evidence = EvidenceOpportunityPublicDTO(
        "opportunity",
        "exercise",
        ("normal",),
        ("independent",),
        True,
        False,
        0,
        "curriculum-evidence-policy/v1",
    )

    def unit(index: int, candidate_type: str) -> CurriculumUnitPublicDTO:
        target_fact = CurriculumTargetPublicDTO(
            f"unit-target-{index}",
            "semantic-review",
            None,
            "scale-v1",
            None,
            "Practice the target",
            None,
            None,
            True,
            "primary",
            0,
        )
        return CurriculumUnitPublicDTO(
            "curriculum",
            "curriculum",
            "curriculum-v1",
            1,
            f"unit-identity-{index}",
            f"unit-{index}",
            f"unit-definition-{index}",
            "lesson",
            candidate_type,
            f"Unit {index}",
            "Authored action",
            None,
            None,
            "active",
            "manual",
            CurriculumActionPublicDTO("exercise", None, "Practice", None),
            index,
            (10 * MINUTE, 20 * MINUTE, 30 * MINUTE),
            (target_fact,),
            (),
            (evidence,),
        )

    units = tuple(
        unit(index, candidate_type)
        for index, candidate_type in enumerate(
            ("curriculum_unit", "practice_task", "verification"), start=1
        )
    )
    curriculum = CurriculumCatalogPublicDTO.build(
        cutoff_at=2,
        active_version_references=(),
        objectives=(),
        units=units,
        assessment_rubrics=(
            AssessmentRubricPublicDTO(
                "curriculum",
                "curriculum-v1",
                "rubric-definition",
                "rubric-identity",
                "rubric",
                "Assess baseline",
                "Complete the rubric",
                "{}",
                "semantic-review",
                None,
            ),
        ),
    )
    availability = tuple(
        CurriculumAvailabilityPublicDTO(
            item.unit_definition_id,
            2,
            "exclusive",
            "met",
            "met",
            "met",
            (),
            (),
            "curriculum-availability-policy/v1",
            f"availability-{item.unit_definition_id}",
        )
        for item in units
    )
    project_target = ProjectTargetFactPublicDTO(
        "project-target",
        "semantic-review",
        None,
        "scale-v1",
        None,
        None,
        "Deliver outcome",
        "primary",
    )
    project_candidate = ProjectCandidatePublicDTO(
        "project_task",
        "project",
        "project",
        "project-v1",
        "task-identity",
        "task-definition",
        "task",
        "Build project",
        "Deliver the project task",
        "Build it",
        (10 * MINUTE, 20 * MINUTE, 30 * MINUTE),
        "active",
        "met",
        "met",
        "met",
        ("blocked",),
        (
            ProjectBlockerFactPublicDTO(
                "blocker-event", "blocked", True, {"action": "Resolve blocker"}, 1, None
            ),
        ),
        (),
        ("project-target",),
        (project_target,),
        "project-candidate-hash",
    )
    projects = ProjectCatalogPublicDTO.build(
        cutoff_at=2,
        active_version_references=(),
        candidates=(project_candidate,),
    )
    graph = ActiveLearningGraphProjectionPublicDTO(
        "graph",
        "graph-v1",
        "activation",
        1,
        "hash",
        (
            CompetencyEdgeProjectionPublicDTO(
                "edge-definition",
                "edge-identity",
                "supports",
                review_target.competency_identity_id,
                maintenance_target.competency_identity_id,
                "semantic-review",
                "semantic-maintenance",
                0,
            ),
        ),
        (),
    )
    generated = build_candidates(
        snapshot,
        profile,
        curriculum,
        availability,
        projects,
        graph,
        (("curriculum_unit", units[0].unit_definition_id, "moderate"),),
    )
    assert {item.candidate_type for item in generated} == {
        "curriculum_unit",
        "practice_task",
        "verification",
        "assessment",
        "review",
        "maintenance",
        "project_task",
        "unblock_task",
    }
    expected_source_types = {
        "curriculum_unit": "curriculum_unit",
        "practice_task": "curriculum_unit",
        "verification": "curriculum_unit",
        "assessment": "assessment_rubric",
        "review": "analysis_target",
        "maintenance": "analysis_target",
        "project_task": "project_task",
        "unblock_task": "project_blocker",
    }
    assert len({item.candidate_key for item in generated}) == 8
    for item in generated:
        assert item.candidate_key.startswith(
            f"candidate-key/v1|{item.candidate_type}|{expected_source_types[item.candidate_type]}|"
        )
        assert item.source_type == expected_source_types[item.candidate_type]
        assert item.primary_outcome_id
        assert item.served_target_identity_ids
    contextual = next(
        item for item in generated if item.source_entity_id == units[0].unit_definition_id
    )
    assert contextual.context_cost == "moderate"
    assert contextual.supports_transfer_target_ids == ()
    unblock = next(item for item in generated if item.candidate_type == "unblock_task")
    assert unblock.blocker_reference_id == "blocker-event"
    assert unblock.unblock_action_available is True
    legacy_candidates = registered_candidate_builder(LEGACY_POLICY_REGISTRY_VERSION)(
        snapshot,
        profile,
        curriculum,
        availability,
        projects,
        graph,
        (("curriculum_unit", units[0].unit_definition_id, "moderate"),),
    )
    legacy_candidate_hash = content_hash([asdict(item) for item in legacy_candidates])
    legacy_output = evaluate_registered(LEGACY_POLICY_REGISTRY_VERSION, legacy_candidates, None)
    assert legacy_candidate_hash == (
        "1debb1a61402e327da3600adffe4611db5898945fdbd533613ee74c4357f0977"
    )
    assert legacy_output.output_hash == (
        "f4143b7a4e5b39b85684a02064444ff9ad06134a2184bd8d0ca9b66cb8a6483f"
    )


def test_policy_registry_dispatch_is_explicit_copy_safe_and_version_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = registered_policy_bundle(POLICY_REGISTRY_VERSION)
    assert bundle["candidate"] == "recommendation-candidate-policy/v2"
    assert bundle["portfolio"] == "recommendation-portfolio-policy/v2"
    assert bundle["reason"] == "recommendation-reason-policy/v2"
    legacy_bundle = registered_policy_bundle(LEGACY_POLICY_REGISTRY_VERSION)
    assert legacy_bundle["algorithm"] == "recommendation-algorithm/v2.0"
    assert legacy_bundle["candidate"] == "recommendation-candidate-policy/v1"
    assert legacy_bundle["portfolio"] == "recommendation-portfolio-policy/v1"
    assert legacy_bundle["reason"] == "recommendation-reason-policy/v1"
    current_output = evaluate_registered(
        POLICY_REGISTRY_VERSION, (candidate("registered-current"),), None
    )
    legacy_output = evaluate_registered(
        LEGACY_POLICY_REGISTRY_VERSION, (candidate("registered-legacy"),), None
    )
    assert {item.policy_version for item in current_output.reasons} == {
        "recommendation-reason-policy/v2"
    }
    assert {item.policy_version for item in legacy_output.reasons} == {
        "recommendation-reason-policy/v1"
    }
    assert dict(current_output.decisions[0].decisive_facts)["usefulness"] is True
    assert "usefulness" not in dict(legacy_output.decisions[0].decisive_facts)
    bundle["algorithm"] = "tampered"
    assert registered_policy_bundle(POLICY_REGISTRY_VERSION)["algorithm"] != "tampered"
    assert evaluate_registered(POLICY_REGISTRY_VERSION, (candidate("registered"),), None)
    retained_builder = registered_candidate_builder(POLICY_REGISTRY_VERSION)
    monkeypatch.setattr(
        "app.recommendation.v2.candidates.build_candidates",
        lambda *_args, **_kwargs: (),
    )
    assert registered_candidate_builder(POLICY_REGISTRY_VERSION) is retained_builder
    with pytest.raises(KeyError):
        registered_policy_bundle("recommendation-policy-registry/unknown")


def test_duplicate_context_cost_sources_fail_before_generation() -> None:
    with pytest.raises(ValueError, match="each source exactly once"):
        RecommendationRunRequest(
            idempotency_key="duplicate-context",
            analysis_snapshot_id="snapshot",
            context_costs=[
                {
                    "source_type": "curriculum_unit",
                    "source_entity_id": "unit-a",
                    "cost": "low",
                },
                {
                    "source_type": "curriculum_unit",
                    "source_entity_id": "unit-a",
                    "cost": "high",
                },
            ],
        )


def _portfolio() -> tuple[CandidateInputDTO, CandidateInputDTO, CandidateInputDTO]:
    primary = candidate(
        "primary",
        target_priority="critical",
        gap_severity="critical",
        deadline_status="due_soon",
    )
    complementary = candidate(
        "complementary",
        candidate_type="project_task",
        source_type="project_task",
        target_priority="important",
        gap_severity="medium",
    )
    maintenance = candidate(
        "maintenance",
        candidate_type="maintenance",
        source_type="analysis_target",
        target_priority="optional",
        gap_severity="none",
        review_due=True,
        primary_need_kind="review_due",
        target_reached=True,
        neglect_or_stall_severity="attention",
        duration_range_ms=(5 * MINUTE, 10 * MINUTE, 15 * MINUTE),
    )
    return primary, complementary, maintenance


def test_unknown_time_selects_full_portfolio_with_per_item_preferred_durations() -> None:
    output = evaluate(_portfolio(), None)
    selected = [item for item in output.decisions if item.decision == "selected"]
    by_role = {item.portfolio_role: item for item in selected}
    assert set(by_role) == {"primary", "complementary", "maintenance"}
    assert by_role["primary"].advisory_duration_ms == 20 * MINUTE
    assert by_role["complementary"].advisory_duration_ms == 20 * MINUTE
    assert by_role["maintenance"].advisory_duration_ms == 10 * MINUTE
    assert all(item.duration_reason_code is None for item in selected)


def test_unknown_time_allows_unknown_duration_but_known_window_rejects_it() -> None:
    primary, complementary, maintenance = _portfolio()
    complementary = replace(complementary, duration_range_ms=None)
    unknown = evaluate((primary, complementary, maintenance), None)
    unknown_complementary = next(
        item for item in unknown.decisions if item.candidate_stable_id == complementary.stable_id
    )
    assert unknown_complementary.decision == "selected"
    assert unknown_complementary.portfolio_role == "complementary"
    assert unknown_complementary.advisory_duration_ms is None
    assert unknown_complementary.duration_reason_code == "DURATION_UNKNOWN"

    known = evaluate((primary, complementary, maintenance), 60 * MINUTE)
    known_complementary = next(
        item for item in known.decisions if item.candidate_stable_id == complementary.stable_id
    )
    assert known_complementary.decision == "ineligible"
    assert known_complementary.reason_code == "ACTION_UNAVAILABLE"
    known_rules = next(
        item for item in known.candidates if item.candidate.stable_id == complementary.stable_id
    ).eligibility_rules
    duration_rule = next(item for item in known_rules if item.code == "DURATION_FIT")
    assert ("durationReasonCode", "DURATION_FIT_UNKNOWN") in duration_rule.facts


def test_known_window_uses_minimum_fit_and_deterministic_quantized_allocation() -> None:
    primary, complementary, maintenance = _portfolio()
    output = evaluate((primary, complementary, maintenance), 25 * MINUTE + 4 * MINUTE)
    selected = {
        item.portfolio_role: item for item in output.decisions if item.decision == "selected"
    }
    assert set(selected) == {"primary", "complementary", "maintenance"}
    assert selected["primary"].advisory_duration_ms == 10 * MINUTE
    assert selected["complementary"].advisory_duration_ms == 10 * MINUTE
    assert selected["maintenance"].advisory_duration_ms == 5 * MINUTE

    too_long = replace(complementary, duration_range_ms=(30 * MINUTE, 30 * MINUTE, 30 * MINUTE))
    rejected = evaluate((primary, too_long), 20 * MINUTE)
    decision = next(
        item for item in rejected.decisions if item.candidate_stable_id == too_long.stable_id
    )
    assert decision.decision == "ineligible"
    assert decision.reason_code == "ACTION_UNAVAILABLE"
    rejected_candidate = next(
        item for item in rejected.candidates if item.candidate.stable_id == too_long.stable_id
    )
    duration_rule = next(
        item for item in rejected_candidate.eligibility_rules if item.code == "DURATION_FIT"
    )
    assert ("durationReasonCode", "DURATION_MINIMUM_NOT_FIT") in duration_rule.facts

    long_primary = replace(primary, duration_range_ms=(30 * MINUTE, 45 * MINUTE, 60 * MINUTE))
    long_complementary = replace(
        complementary, duration_range_ms=(20 * MINUTE, 30 * MINUTE, 45 * MINUTE)
    )
    urgent_maintenance = replace(
        maintenance,
        maintenance_severity="high",
        duration_range_ms=(10 * MINUTE, 15 * MINUTE, 20 * MINUTE),
    )
    golden = evaluate((long_primary, long_complementary, urgent_maintenance), 50 * MINUTE)
    golden_decisions = {item.candidate_stable_id: item for item in golden.decisions}
    assert golden_decisions[long_primary.stable_id].admission_ordinal == 1
    assert golden_decisions[urgent_maintenance.stable_id].admission_ordinal == 2
    assert golden_decisions[long_complementary.stable_id].decision == "not_selected"
    assert golden_decisions[long_primary.stable_id].advisory_duration_ms == 40 * MINUTE
    assert golden_decisions[urgent_maintenance.stable_id].advisory_duration_ms == 10 * MINUTE


def test_mixed_or_unaligned_duration_range_is_rejected_before_ranking() -> None:
    mixed = candidate("mixed", duration_range_ms=(10 * MINUTE, None, 30 * MINUTE))
    unaligned = candidate("unaligned", duration_range_ms=(1, 20 * MINUTE, 30 * MINUTE))
    for item in (mixed, unaligned):
        evaluated = evaluate((item,), None).candidates[0]
        decision = evaluate((item,), None).decisions[0]
        assert evaluated.eligible is False
        assert evaluated.rank_ordinal is None
        assert evaluated.score_components == ()
        assert decision.decision == "ineligible"
        assert decision.reason_code == "DURATION_RANGE_INVALID"


def _analysis_snapshot(db: Session) -> str:
    snapshot = run_analysis(
        db,
        idempotency_key="analysis-for-recommendation",
        purpose="learning_control",
        update_current=False,
    )
    db.commit()
    return snapshot.id


async def _seed_live_recommendation_source(client: AsyncClient, csrf: str) -> None:
    headers = {"X-CSRF-Token": csrf}
    technical = next(
        item
        for item in (await client.get("/api/v2/capability-scales")).json()
        if item["stableKey"] == "technical"
    )
    levels = {item["stableKey"]: item for item in technical["levels"]}
    competency = await client.post(
        "/api/v2/competencies",
        json={"stable_key": "recommendation.live", "creation_source": "test"},
        headers=headers,
    )
    assert competency.status_code == 201, competency.text
    competency_id = competency.json()["id"]
    definition = await client.post(
        f"/api/v2/competencies/{competency_id}/definitions",
        json={
            "title": "Live Recommendation",
            "description": "Exact generation source",
            "scope": "Recommendation fixture",
            "scale_stable_key": "technical",
            "scale_version": technical["version"],
            "dimension_keys": [],
            "effective_at": "2026-01-01T00:00:00Z",
            "creation_source": "test",
            "criteria": [
                {
                    "stable_key": "deliver",
                    "level_stable_key": "guided",
                    "dimension_key": None,
                    "requirement_type": "required",
                    "demonstration_rule": "independent_performance",
                    "description": "Deliver the bounded result.",
                }
            ],
        },
        headers=headers,
    )
    assert definition.status_code == 201, definition.text
    definition_body = definition.json()
    activated = await client.post(
        f"/api/v2/competencies/{competency_id}/definitions/{definition_body['id']}/activate",
        json={
            "reason": "Recommendation fixture",
            "source": "test",
            "idempotency_key": "recommendation-semantic",
        },
        headers=headers,
    )
    assert activated.status_code == 200, activated.text
    profile = await client.post(
        "/api/v2/target-profiles",
        json={
            "stable_key": "recommendation-profile",
            "creation_source": "test",
            "version": {
                "title": "Recommendation profile",
                "effective_at": "2026-01-02T00:00:00Z",
                "creation_source": "test",
                "domains": [
                    {
                        "stable_key": "primary",
                        "title": "Primary",
                        "minimum_percent": 100,
                        "maximum_percent": 100,
                        "order_index": 0,
                    }
                ],
                "targets": [
                    {
                        "stable_key": "live-target",
                        "competency_identity_id": competency_id,
                        "dimension_key": None,
                        "domain_stable_key": "primary",
                        "scale_stable_key": "technical",
                        "scale_version": technical["version"],
                        "target_level_stable_key": "guided",
                        "priority": "core",
                    }
                ],
                "milestones": [],
                "readiness_gates": [],
            },
        },
        headers=headers,
    )
    assert profile.status_code == 201, profile.text
    profile_body = profile.json()
    activated_profile = await client.post(
        f"/api/v2/target-profiles/{profile_body['profileId']}/versions/"
        f"{profile_body['versionId']}/activate",
        json={
            "reason": "Recommendation fixture",
            "source": "test",
            "idempotency_key": "recommendation-profile",
        },
        headers=headers,
    )
    assert activated_profile.status_code == 200, activated_profile.text
    curriculum = await client.post(
        "/api/v2/curricula",
        json={"stable_key": "recommendation-curriculum", "creation_source": "test"},
        headers=headers,
    )
    assert curriculum.status_code == 201, curriculum.text
    curriculum_id = curriculum.json()["id"]
    version = await client.post(
        f"/api/v2/curricula/{curriculum_id}/versions",
        json={
            "title": "Recommendation curriculum",
            "effective_at": "2026-01-03T00:00:00Z",
            "creation_source": "test",
            "objectives": [],
            "units": [
                {
                    "stable_key": "live-practice",
                    "kind": "practice_task",
                    "title": "Live practice",
                    "action": {"kind": "practice_task", "instructions": "Practice."},
                    "provenance": "test",
                    "order_index": 0,
                    "minimum_useful_duration_ms": 600_000,
                    "preferred_duration_ms": 1_200_000,
                    "maximum_useful_duration_ms": 1_800_000,
                    "targets": [
                        {
                            "semantic_definition_id": definition_body["id"],
                            "criterion_definition_id": definition_body["criteria"][0]["id"],
                            "scale_version_id": technical["id"],
                            "dimension_id": None,
                            "intended_learning_outcome": "Deliver the result.",
                            "minimum_level_id": levels["unexposed"]["id"],
                            "maximum_level_id": levels["guided"]["id"],
                            "supports_unassessed": True,
                            "role": "primary",
                            "order_index": 0,
                        }
                    ],
                    "requirements": [],
                    "evidence_opportunities": [
                        {
                            "stable_key": "live-evidence",
                            "evidence_kind": "code",
                            "intended_strengths": ["moderate"],
                            "intended_independence_modes": ["independent"],
                            "requires_actual_activity": True,
                            "requires_artifact": True,
                            "order_index": 0,
                        }
                    ],
                }
            ],
            "assessment_rubrics": [],
        },
        headers=headers,
    )
    assert version.status_code == 201, version.text
    activated_curriculum = await client.post(
        f"/api/v2/curricula/{curriculum_id}/versions/{version.json()['id']}/activate",
        json={
            "reason": "Recommendation fixture",
            "source": "test",
            "idempotency_key": "recommendation-curriculum",
        },
        headers=headers,
    )
    assert activated_curriculum.status_code == 200, activated_curriculum.text


def frozen_input(
    db: Session,
    snapshot_id: str,
    candidates: tuple[CandidateInputDTO, ...],
    available_time_ms: int | None = None,
) -> dict[str, object]:
    snapshot = db.get(AnalysisSnapshot, snapshot_id)
    assert snapshot is not None
    lineage = json.loads(snapshot.input_lineage_json)
    return {
        "analysisSnapshot": {
            "id": snapshot_id,
            "inputHash": snapshot.input_hash,
            "outputHash": snapshot.output_hash,
            "cutoffAt": snapshot.cutoff_at,
        },
        "targetProfileVersion": lineage["profile"],
        "learningGraph": lineage["graph"],
        "curriculum": lineage["curriculumCatalog"],
        "curriculumAvailability": [],
        "projects": lineage["projectCatalog"],
        "userConstraints": {
            "availableTimeMs": available_time_ms,
            "contextCostPolicy": "explicit-only",
            "contextCosts": [],
        },
        "availableTimeMs": available_time_ms,
        "candidates": [asdict(item) for item in candidates],
    }


def persist_fixture_recommendations(
    db: Session,
    *,
    idempotency_key: str,
    analysis_snapshot_id: str,
    available_time_ms: int | None,
    frozen_input: dict[str, object],
) -> RecommendationV2Run:
    return _persist_completed_run(
        db,
        idempotency_key=idempotency_key,
        analysis_snapshot_id=analysis_snapshot_id,
        available_time_ms=available_time_ms,
        replay_of_run_id=None,
        policy_registry_version=POLICY_REGISTRY_VERSION,
        snapshot=load_public_analysis_snapshot(db, analysis_snapshot_id),
        frozen_input=frozen_input,
        candidates=tuple(
            candidate_from_payload(item)
            for item in frozen_input["candidates"]  # type: ignore[union-attr]
        ),
    )


def test_runs_persist_complete_audit_without_mutating_history(db: Session) -> None:
    snapshot_id = _analysis_snapshot(db)
    candidates = _portfolio()
    frozen = frozen_input(db, snapshot_id, candidates)
    original = persist_fixture_recommendations(
        db,
        idempotency_key="recommendation-original",
        analysis_snapshot_id=snapshot_id,
        available_time_ms=None,
        frozen_input=frozen,
    )
    db.commit()
    assert (
        db.scalar(select(RecommendationV2Run).where(RecommendationV2Run.id == original.id))
        is not None
    )
    persisted_candidates = db.scalars(
        select(RecommendationV2Candidate).where(RecommendationV2Candidate.run_id == original.id)
    ).all()
    assert len(persisted_candidates) == 3
    for persisted in persisted_candidates:
        decision = db.get(RecommendationV2SelectionDecision, persisted.id)
        assert decision is not None
        if decision.score_total is not None:
            components = db.scalars(
                select(RecommendationV2ScoreComponent).where(
                    RecommendationV2ScoreComponent.candidate_id == persisted.id
                )
            ).all()
            assert len(components) == 7
            assert decision.score_total == sum(item.value for item in components)


def test_live_generation_requires_the_current_exact_analysis_envelope(db: Session) -> None:
    current = run_analysis(
        db,
        idempotency_key="analysis-current-recommendation",
        purpose="learning_control",
        update_current=True,
    )
    db.commit()
    generated = generate_live_recommendations(
        db,
        idempotency_key="live-recommendation",
        analysis_snapshot_id=current.id,
        available_time_ms=None,
    )
    db.commit()
    assert generated.status == "completed"

    replacement = run_analysis(
        db,
        idempotency_key="analysis-replaces-current",
        purpose="learning_control",
        update_current=True,
    )
    db.commit()
    assert replacement.id != current.id
    with pytest.raises(AppError, match="current"):
        generate_live_recommendations(
            db,
            idempotency_key="stale-analysis-recommendation",
            analysis_snapshot_id=current.id,
            available_time_ms=None,
        )


@pytest.mark.parametrize(
    ("column", "replacement"),
    (
        ("input_hash", "0" * 64),
        ("purpose", "candidate_readiness"),
        ("cutoff_at", 1),
        ("semantic_definition_references_json", '["tampered"]'),
        ("capability_scale_version_references_json", '["tampered"]'),
        ("discipline_configuration_reference", "tampered"),
        ("configuration_hash", "0" * 64),
    ),
)
def test_live_generation_rejects_analysis_envelope_mismatch(
    db: Session, column: str, replacement: object
) -> None:
    snapshot = run_analysis(
        db,
        idempotency_key=f"analysis-envelope-{column}",
        purpose="learning_control",
        update_current=True,
    )
    db.commit()
    value = snapshot.cutoff_at + 1 if column == "cutoff_at" else replacement
    db.execute(
        update(AnalysisSnapshot).where(AnalysisSnapshot.id == snapshot.id).values({column: value})
    )
    db.commit()
    with pytest.raises(AppError, match="input envelope"):
        generate_live_recommendations(
            db,
            idempotency_key=f"recommendation-envelope-{column}",
            analysis_snapshot_id=snapshot.id,
            available_time_ms=None,
        )


def test_mid_persist_failure_publishes_only_a_failed_lineage_run(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_id = _analysis_snapshot(db)
    frozen = frozen_input(db, snapshot_id, _portfolio())

    original_flush = db.flush
    fault_injected = False

    def fail_after_candidate_flush(*args: object, **kwargs: object) -> None:
        nonlocal fault_injected
        original_flush(*args, **kwargs)
        if not fault_injected and db.scalar(select(RecommendationV2Candidate.id)) is not None:
            fault_injected = True
            raise RuntimeError("fault injection after candidate insert")

    monkeypatch.setattr(db, "flush", fail_after_candidate_flush)
    with pytest.raises(RecommendationGenerationFailure) as captured:
        persist_fixture_recommendations(
            db,
            idempotency_key="faulted-recommendation",
            analysis_snapshot_id=snapshot_id,
            available_time_ms=None,
            frozen_input=frozen,
        )
    db.rollback()
    failure = captured.value
    failed = record_failed_recommendation_run(
        db,
        idempotency_key="faulted-recommendation",
        analysis_snapshot_id=snapshot_id,
        available_time_ms=None,
        replay_of_run_id=None,
        error=failure.original_error,
        frozen_input=failure.frozen_input,
        policy_registry_version=failure.policy_registry_version,
    )
    assert failed is not None
    db.commit()
    assert json.loads(failed.frozen_input_json) == json.loads(canonical_json(frozen))
    assert (
        db.scalars(
            select(RecommendationV2Candidate).where(RecommendationV2Candidate.run_id == failed.id)
        ).all()
        == []
    )


def test_recommendation_history_rejects_update_and_delete(db: Session) -> None:
    snapshot_id = _analysis_snapshot(db)
    run = persist_fixture_recommendations(
        db,
        idempotency_key="immutable-recommendation",
        analysis_snapshot_id=snapshot_id,
        available_time_ms=None,
        frozen_input=frozen_input(db, snapshot_id, _portfolio()),
    )
    db.commit()
    run.status = "failed"
    with pytest.raises(ValueError, match="immutable"):
        db.flush()
    db.rollback()
    candidate_row = db.scalar(
        select(RecommendationV2Candidate).where(RecommendationV2Candidate.run_id == run.id)
    )
    assert candidate_row is not None
    db.delete(candidate_row)
    with pytest.raises(ValueError, match="immutable"):
        db.flush()
    db.rollback()


def test_changed_available_time_creates_new_immutable_run(db: Session) -> None:
    snapshot_id = _analysis_snapshot(db)
    frozen = frozen_input(db, snapshot_id, _portfolio())
    first = persist_fixture_recommendations(
        db,
        idempotency_key="unknown-window",
        analysis_snapshot_id=snapshot_id,
        available_time_ms=None,
        frozen_input=frozen,
    )
    second_frozen = json.loads(json.dumps(frozen))
    second_frozen["availableTimeMs"] = 30 * MINUTE
    second_frozen["userConstraints"]["availableTimeMs"] = 30 * MINUTE
    second = persist_fixture_recommendations(
        db,
        idempotency_key="known-window",
        analysis_snapshot_id=snapshot_id,
        available_time_ms=30 * MINUTE,
        frozen_input=second_frozen,
    )
    db.commit()
    assert first.id != second.id
    assert first.available_time_ms is None
    assert second.available_time_ms == 30 * MINUTE
    assert first.input_hash != second.input_hash


def test_failed_run_persists_lineage_only_and_is_portable(db: Session) -> None:
    snapshot_id = _analysis_snapshot(db)
    failed = record_failed_recommendation_run(
        db,
        idempotency_key="recommendation-failed",
        analysis_snapshot_id=snapshot_id,
        available_time_ms=25 * MINUTE,
        replay_of_run_id=None,
        error=RuntimeError("private failure detail"),
    )
    assert failed is not None
    db.commit()

    assert failed.status == "failed"
    assert failed.output_hash is None
    assert failed.failure_metadata_json == (
        '{"code":"RECOMMENDATION_RUN_FAILED","type":"RuntimeError"}'
    )
    assert (
        db.scalars(
            select(RecommendationV2Candidate).where(RecommendationV2Candidate.run_id == failed.id)
        ).all()
        == []
    )
    validate_domain_integrity(db.connection())
    payload = _portable_payload(db)
    tables, _summary = _validate_portable_payload(payload, "recommendation-failed-v8", 9)
    portable = next(row for row in tables["recommendation_v2_runs"] if row["id"] == failed.id)
    assert portable["status"] == "failed"
    assert portable["output_hash"] is None


async def test_recommendation_history_is_portable_tamper_evident_and_not_backfilled(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    await _seed_live_recommendation_source(client, csrf)
    snapshot = run_analysis(
        db,
        idempotency_key="portable-analysis",
        purpose="learning_control",
        update_current=True,
    )
    db.commit()
    original = generate_live_recommendations(
        db,
        idempotency_key="portable-recommendation",
        analysis_snapshot_id=snapshot.id,
        available_time_ms=None,
    )
    db.commit()
    replay = replay_recommendations(
        db, run_id=original.id, idempotency_key="portable-recommendation-replay"
    )
    db.commit()
    now = utc_now_ms()
    generation = generate_today(
        db,
        idempotency_key="portable-today-generation",
        analysis_snapshot_id=snapshot.id,
        available_time_ms=None,
        context_costs=(),
        regenerate=False,
        now_ms=now,
    )
    suggestion = db.scalar(
        select(TodaySuggestion).where(TodaySuggestion.generation_id == generation.id)
    )
    assert suggestion is not None
    viewed = record_interaction(
        db,
        suggestion_id=suggestion.id,
        interaction_type="viewed",
        idempotency_key="portable-today-viewed",
        now_ms=now + 1,
    )
    correct_interaction(
        db,
        interaction_id=viewed.id,
        idempotency_key="portable-today-viewed-correction",
        reason="Portable correction round trip.",
        now_ms=now + 2,
    )
    replacement_generation = generate_today(
        db,
        idempotency_key="portable-today-regeneration",
        analysis_snapshot_id=snapshot.id,
        available_time_ms=None,
        context_costs=(),
        regenerate=True,
        now_ms=now + 3,
    )
    replacement_suggestion = db.scalar(
        select(TodaySuggestion).where(TodaySuggestion.generation_id == replacement_generation.id)
    )
    assert replacement_suggestion is not None
    db.connection().execute(
        TodaySuggestion.__table__.update()
        .where(TodaySuggestion.id == replacement_suggestion.id)
        .values(replaces_suggestion_id=suggestion.id)
    )
    actual = create_activity_in_uow(
        db,
        title="Portable Today actual work",
        description="Restore fixture for actual-Activity relation history.",
        category_stable_key="practice",
        occurred_at=now + 4,
        creator_source="user",
        provenance="user_recorded",
    )
    relation = link_activity(
        db,
        suggestion_id=replacement_suggestion.id,
        activity_id=actual.id,
        relation_type="partially_matched",
        idempotency_key="portable-today-relation",
        now_ms=now + 5,
    )
    correct_relation(
        db,
        relation_id=relation.id,
        idempotency_key="portable-today-relation-correction",
        correction_type="retracted",
        replacement_activity_id=None,
        replacement_relation_type=None,
        reason="Portable relation correction round trip.",
        now_ms=now + 6,
    )
    db.commit()
    db.expire_all()
    payload = _portable_payload(db)
    tables, _summary = _validate_portable_payload(payload, "recommendation-v8", 9)
    assert len(tables["recommendation_v2_runs"]) == 4
    assert len(tables["recommendation_v2_candidates"]) == 4
    assert len(tables["today_generations"]) == 2
    assert len(tables["today_suggestions"]) == 2
    assert len(tables["today_interaction_corrections"]) == 1
    assert len(tables["suggestion_activity_relations"]) == 1
    assert len(tables["suggestion_activity_relation_corrections"]) == 1
    assert any(
        row["replaces_suggestion_id"] == suggestion.id for row in tables["today_suggestions"]
    )
    original_today_tables = {
        name: sorted(deepcopy(tables[name]), key=lambda row: row["id"])
        for name in PORTABLE_V8_TODAY_TABLES
    }

    tampered = deepcopy(payload)
    tampered["tables"]["recommendation_v2_score_components"][0]["value"] += 1
    with pytest.raises(AppError, match="Recommendation V2 immutable history"):
        _validate_portable_payload(tampered, "recommendation-v8-tampered", 9)

    coherently_rehashed = deepcopy(tampered)
    history = {
        table_name: sorted(coherently_rehashed["tables"][table_name], key=canonical_json)
        for table_name in sorted(PORTABLE_V7_RECOMMENDATION_TABLES)
    }
    checkpoint = coherently_rehashed["recommendationV2HistoryCheckpoint"]
    checkpoint["historyHash"] = content_hash(history)
    checkpoint["checkpointHash"] = content_hash(
        {key: value for key, value in checkpoint.items() if key != "checkpointHash"}
    )
    with pytest.raises(AppError, match="persisted audit differs"):
        _validate_portable_payload(coherently_rehashed, "recommendation-v8-rehashed", 9)

    payload["tables"]["recommendation_v2_runs"].reverse()
    _apply_portable_restore(
        db,
        payload,
        True,
        package_id="recommendation-v8-restore",
        schema_version=9,
    )
    db.commit()
    restored = db.get(RecommendationV2Run, original.id)
    assert restored is not None
    assert restored.output_hash == original.output_hash
    restored_replay = db.get(RecommendationV2Run, replay.id)
    assert restored_replay is not None
    assert restored_replay.replay_of_run_id == original.id
    restored_payload = _portable_payload(db)
    assert {
        name: sorted(restored_payload["tables"][name], key=lambda row: row["id"])
        for name in PORTABLE_V8_TODAY_TABLES
    } == original_today_tables
    assert restored_payload["todayV2CurrentCheckpoint"] == payload["todayV2CurrentCheckpoint"]

    v6 = deepcopy(payload)
    v6["manifest"] = PORTABLE_V6_MANIFEST
    v6.pop("recommendationV2HistoryCheckpoint")
    v6.pop("todayV2CurrentCheckpoint")
    v6.pop("authorityCheckpoint")
    for table_name in PORTABLE_V7_RECOMMENDATION_TABLES:
        v6["tables"].pop(table_name)
    for table_name in PORTABLE_V8_TODAY_TABLES:
        v6["tables"].pop(table_name)
    for table_name in PORTABLE_V9_AUTHORITY_TABLES:
        v6["tables"].pop(table_name)
    converted, summary = _validate_portable_payload(v6, "recommendation-v6-adapter", 6)
    assert all(converted[name] == [] for name in PORTABLE_V7_RECOMMENDATION_TABLES)
    assert summary["compatibilityConversions"]["nativeRecommendationHistoryInferred"] == 0


def test_0016_is_additive_invents_no_history_and_refuses_populated_downgrade(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "recommendation-migration.sqlite3"
    config = migration_config(database_path)
    command.upgrade(config, "0015_analysis_v3")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO recommendation_snapshots "
            "(id,generated_at,local_date,engine_version,primary_competency_identity_id,"
            "primary_activity_type,secondary_competency_identity_id,structured_payload_json,"
            "accepted_primary,chosen_competency_identity_id,analysis_snapshot_id) VALUES "
            "('legacy-recommendation',1,'1970-01-01',1,NULL,NULL,NULL,'{\"legacy\":true}',"
            "NULL,NULL,NULL)"
        )
        connection.commit()
        before = connection.execute(
            "SELECT * FROM recommendation_snapshots WHERE id='legacy-recommendation'"
        ).fetchone()
    command.upgrade(config, "0016_recommendation_v2")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0016_recommendation_v2",
        )
        assert connection.execute("SELECT COUNT(*) FROM recommendation_v2_runs").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT * FROM recommendation_snapshots WHERE id='legacy-recommendation'"
            ).fetchone()
            == before
        )
    command.downgrade(config, "0015_analysis_v3")
    command.upgrade(config, "0016_recommendation_v2")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO recommendation_v2_runs "
            "(id,idempotency_key,analysis_snapshot_id,generated_at,cutoff_at,local_date,"
            "available_time_ms,"
            "status,algorithm_version,policy_registry_version,policy_bundle_json,"
            "policy_bundle_hash,frozen_input_json,input_hash,output_hash,"
            "target_profile_version_id,learning_graph_version_id,curriculum_reference,"
            "project_reference,user_constraints_hash,semantic_definition_references_json,"
            "capability_scale_version_references_json,completeness,application_version,"
            "replay_of_run_id,failure_metadata_json) VALUES "
            "('run','migration-fixture','missing',1,1,'1970-01-01',NULL,'failed',"
            "'algorithm','registry','{}','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',"
            "'{}','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',"
            "NULL,NULL,NULL,NULL,NULL,"
            "'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',"
            "'[]','[]','partial','2.0.0',NULL,'{}')"
        )
        connection.commit()
    with pytest.raises(RuntimeError, match="immutable Recommendation V2 history"):
        command.downgrade(config, "0015_analysis_v3")


def test_0016_refuses_downgrade_when_only_a_child_history_table_is_populated(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "recommendation-child-history.sqlite3"
    config = migration_config(database_path)
    command.upgrade(config, "0016_recommendation_v2")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO recommendation_v2_reasons "
            "(id,candidate_id,ordinal,reason_code,explanation_facts_json,title,"
            "score_contribution,template_key,template_version,rendered_text,source_ids_json,"
            "policy_version) VALUES "
            "('reason','missing',0,'ELIGIBLE','[]','Eligible action',NULL,'template','v1',"
            "'Eligible action.','[]','v1')"
        )
        connection.commit()
    with pytest.raises(RuntimeError, match="recommendation_v2_reasons"):
        command.downgrade(config, "0015_analysis_v3")
