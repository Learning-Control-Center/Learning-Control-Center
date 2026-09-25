from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, replace
from typing import cast

from app.determinism import content_hash
from app.recommendation.v2.candidates import (
    STRUCTURAL_FACT_KEYS,
    build_candidates,
    build_candidates_v1,
    build_candidates_v3,
    build_candidates_v4,
)
from app.recommendation.v2.contracts import (
    CandidateInputDTO,
    EligibilityRuleResultDTO,
    EvaluatedCandidateDTO,
    RecommendationPolicyOutputDTO,
    RecommendationReasonDTO,
    ScoreComponentDTO,
    SelectedRecommendationDTO,
    SelectionDecisionDTO,
)

LEGACY_RECOMMENDATION_ALGORITHM_VERSION = "recommendation-algorithm/v2.0"
RECOMMENDATION_ALGORITHM_VERSION = "recommendation-algorithm/v2.3"
APPLICATION_VERSION = "2.0.0"
ELV_POLICY_VERSION = "expected-learning-value-policy/v1"
SCORE_POLICY_VERSION = "recommendation-score-policy/v1"
DURATION_POLICY_VERSION = "recommendation-duration-policy/v1"
ELIGIBILITY_POLICY_VERSION = "recommendation-eligibility-policy/v1"
PORTFOLIO_POLICY_VERSION = "recommendation-portfolio-policy/v2"
REASON_POLICY_VERSION = "recommendation-reason-policy/v4"
POLICY_REGISTRY_VERSION = "recommendation-policy-registry/v4"
CANDIDATE_POLICY_VERSION = "recommendation-candidate-policy/v4"
LEGACY_POLICY_REGISTRY_VERSION = "recommendation-policy-registry/v1"
PREVIOUS_POLICY_REGISTRY_VERSION = "recommendation-policy-registry/v2"
V3_POLICY_REGISTRY_VERSION = "recommendation-policy-registry/v3"
OWNER_APPROVED_POLICY_REGISTRY_VERSION = "recommendation-policy-registry/v4"

QUANTUM_MS = 300_000

_PRIORITY_SCORE = {"optional": 2, "supporting": 6, "important": 11, "core": 16, "critical": 20}
_PRIORITY_RANK = {"critical": 0, "core": 1, "important": 2, "supporting": 3, "optional": 4}
_DEADLINE_SCORE = {"none": 0, "later": 0, "due_soon": 6, "due": 10, "overdue": 12}
_DEADLINE_RANK = {"overdue": 0, "due": 1, "due_soon": 2, "later": 3, "none": 4}
_ELV_SCORE = {"unknown": 0, "low": 0, "normal": 5, "high": 8, "very_high": 12}
_ELV_RANK = {"very_high": 0, "high": 1, "normal": 2, "low": 3, "unknown": 4}
_SIGNAL_SCORE = {None: 0, "none": 0, "info": 2, "attention": 4, "high": 7, "critical": 10}
_CONTEXT_SCORE = {"none": 0, "low": 0, "unknown": 0, "moderate": -2, "high": -4}

_REASON_TITLES = {
    "ELIGIBLE": "Eligible action",
    "TARGET_INACTIVE": "Target is not active",
    "PREREQUISITE_UNKNOWN": "Prerequisite is unknown",
    "PREREQUISITE_UNSATISFIED": "Prerequisite is not satisfied",
    "READINESS_GATE_UNKNOWN": "Readiness is unknown",
    "READINESS_GATE_NOT_MET": "Readiness gate is not met",
    "ACTION_UNAVAILABLE": "Action is unavailable",
    "CONTEXT_UNAVAILABLE": "Required context is unavailable",
    "CAPABILITY_UNKNOWN": "Capability suitability is unknown",
    "CAPABILITY_UNSUITABLE": "Capability is outside the suitable range",
    "CURRICULUM_ACTION_MISSING": "Curriculum action is unavailable",
    "PROJECT_UNAVAILABLE": "Project task is unavailable",
    "CANDIDATE_NEED_INVALID": "Candidate need is invalid",
    "ACTION_BLOCKED": "Action is blocked",
    "TARGET_ALREADY_REACHED": "Target is already reached",
    "REVIEW_NOT_DUE": "Review is not due",
    "VERIFICATION_NOT_READY": "Verification is not ready",
    "VERIFICATION_TEMPLATE_MISSING": "Verification template is missing",
    "ASSESSMENT_RUBRIC_MISSING": "Assessment rubric is missing",
    "ASSESSMENT_NOT_NEEDED": "Assessment is not needed",
    "ASSESSMENT_SCOPE_INVALID": "Assessment scope is invalid",
    "BLOCKER_NOT_ACTIONABLE": "Blocker is not actionable",
    "BLOCKER_REFERENCE_MISSING": "Blocker reference is missing",
    "UNBLOCK_ACTION_UNAVAILABLE": "Unblock action is unavailable",
    "DURATION_RANGE_INVALID": "Duration range is invalid",
    "DURATION_FIT_UNKNOWN": "Duration fit is unknown",
    "DURATION_MINIMUM_NOT_FIT": "Minimum duration does not fit",
    "UNKNOWN_BLOCKS_CRITICAL_PLANNING": "Unknown blocks critical planning",
    "ASSESSMENT_REDUCES_UNKNOWN": "Assessment reduces an important unknown",
    "MULTI_TARGET_HARD_UNBLOCK": "Action unblocks multiple targets",
    "REQUIRED_GAP_AND_MISSING_EVIDENCE_MODE": "Action supplies required evidence",
    "UNMET_REQUIRED_OUTCOME": "Action addresses an unmet required outcome",
    "MISSING_INDEPENDENT_EVIDENCE": "Action supplies independent evidence",
    "MULTI_TARGET_SUPPORTS_TRANSFER": "Learning transfers to multiple targets",
    "ACTIONABLE_HARD_UNBLOCK": "Action resolves a hard blocker",
    "REPEATED_WITHOUT_NEW_EVIDENCE": "Action repeats without new evidence",
    "RECENT_SATURATION": "Action is recently saturated",
    "DECISIVE_LEARNING_VALUE_FACTS_MISSING": "Learning value is unknown",
    "NORMAL_EXPECTED_LEARNING_VALUE": "Normal expected learning value",
    "SELECTED_PRIMARY": "Selected as Primary",
    "SELECTED_COMPLEMENTARY": "Selected as Complementary",
    "SELECTED_MAINTENANCE": "Selected as Maintenance",
    "PORTFOLIO_NOT_SELECTED": "Not selected for this portfolio",
    "NOT_USEFUL": "Not useful for the current learning state",
    "DURATION_UNKNOWN": "Advisory duration is unknown",
    "TARGET_PRIORITY": "Target priority",
    "PRIMARY_NEED": "Primary learning need",
    "DEADLINE_PRESSURE": "Deadline pressure",
    "ALLOCATION_BALANCE": "Allocation balance",
    "NEGLECT_OR_STALL": "Neglect or progression stall",
    "EXPECTED_LEARNING_VALUE": "Expected learning value",
    "CONTEXT_COST": "Context cost",
    "ASSESSMENT_STRUCTURAL_ORDER": "Assessment sequence leverage",
}


def policy_bundle() -> dict[str, str]:
    return {
        "application": APPLICATION_VERSION,
        "algorithm": RECOMMENDATION_ALGORITHM_VERSION,
        "candidate": CANDIDATE_POLICY_VERSION,
        "eligibility": ELIGIBILITY_POLICY_VERSION,
        "expectedLearningValue": ELV_POLICY_VERSION,
        "score": SCORE_POLICY_VERSION,
        "duration": DURATION_POLICY_VERSION,
        "portfolio": PORTFOLIO_POLICY_VERSION,
        "reason": REASON_POLICY_VERSION,
        "registry": POLICY_REGISTRY_VERSION,
    }


def _rule(code: str, value: bool | None, *, decisive: bool = True) -> EligibilityRuleResultDTO:
    return EligibilityRuleResultDTO(
        code,
        "unknown" if value is None else ("pass" if value else "fail"),
        decisive,
    )


def _duration_valid(candidate: CandidateInputDTO) -> bool:
    values = candidate.duration_range_ms
    return values is None or (
        all(isinstance(value, int) and value > 0 and value % QUANTUM_MS == 0 for value in values)
        and values[0] <= values[1] <= values[2]
    )


def _rejection_code(candidate: CandidateInputDTO, rule: EligibilityRuleResultDTO) -> str:
    if rule.code == "ACTIVE_TARGET":
        return "TARGET_INACTIVE"
    if rule.code == "PREREQUISITES":
        return "PREREQUISITE_UNKNOWN" if rule.outcome == "unknown" else "PREREQUISITE_UNSATISFIED"
    if rule.code == "HARD_READINESS":
        return "READINESS_GATE_UNKNOWN" if rule.outcome == "unknown" else "READINESS_GATE_NOT_MET"
    if rule.code == "AVAILABILITY":
        return "ACTION_UNAVAILABLE"
    if rule.code == "CONTEXT_AVAILABLE":
        return "CONTEXT_UNAVAILABLE"
    if rule.code == "CAPABILITY_SUITABILITY":
        return "CAPABILITY_UNKNOWN" if rule.outcome == "unknown" else "CAPABILITY_UNSUITABLE"
    if rule.code == "ACTIVE_SOURCE":
        if candidate.candidate_type in {"curriculum_unit", "practice_task", "verification"}:
            return "CURRICULUM_ACTION_MISSING"
        if candidate.candidate_type == "project_task":
            return "PROJECT_UNAVAILABLE"
        return "ACTION_UNAVAILABLE"
    if rule.code == "PRIMARY_NEED_KIND":
        return "CANDIDATE_NEED_INVALID"
    if rule.code == "BLOCKER_CLEAR":
        return "ACTION_BLOCKED"
    if rule.code == "TARGET_NOT_REACHED":
        return "TARGET_ALREADY_REACHED"
    if rule.code == "REVIEW_DUE":
        return "REVIEW_NOT_DUE"
    if rule.code == "VERIFICATION_DUE":
        return "VERIFICATION_NOT_READY"
    if rule.code == "VERIFICATION_TEMPLATE":
        return "VERIFICATION_TEMPLATE_MISSING"
    if rule.code == "ASSESSMENT_RUBRIC":
        return "ASSESSMENT_RUBRIC_MISSING"
    if rule.code == "ASSESSMENT_UNKNOWN_NEED":
        return "ASSESSMENT_NOT_NEEDED"
    if rule.code == "ASSESSMENT_SCOPE":
        return "ASSESSMENT_SCOPE_INVALID"
    if rule.code == "ACTIONABLE_BLOCKER":
        return "BLOCKER_NOT_ACTIONABLE"
    if rule.code == "BLOCKER_REFERENCE":
        return "BLOCKER_REFERENCE_MISSING"
    if rule.code == "UNBLOCK_ACTION_AVAILABLE":
        return "UNBLOCK_ACTION_UNAVAILABLE"
    if rule.code == "DURATION_RANGE_VALID":
        return "DURATION_RANGE_INVALID"
    if rule.code == "DURATION_FIT":
        return "ACTION_UNAVAILABLE"
    raise ValueError(f"Unhandled Recommendation eligibility rule: {rule.code}")


def eligibility(
    candidate: CandidateInputDTO, available_time_ms: int | None
) -> tuple[bool, str, tuple[EligibilityRuleResultDTO, ...]]:
    unblock = candidate.candidate_type == "unblock_task"
    rules = [
        _rule("ACTIVE_TARGET", candidate.active_target),
        EligibilityRuleResultDTO("PREREQUISITES", "not_applicable", False)
        if unblock
        else _rule("PREREQUISITES", candidate.prerequisites_satisfied),
        EligibilityRuleResultDTO("HARD_READINESS", "not_applicable", False)
        if unblock
        else _rule("HARD_READINESS", candidate.hard_readiness_satisfied),
        EligibilityRuleResultDTO("AVAILABILITY", "not_applicable", False)
        if unblock
        else _rule("AVAILABILITY", candidate.availability_satisfied),
        EligibilityRuleResultDTO("CAPABILITY_SUITABILITY", "not_applicable", False)
        if unblock
        else _rule("CAPABILITY_SUITABILITY", candidate.capability_suitability_satisfied),
        _rule("CONTEXT_AVAILABLE", candidate.context_available),
        _rule("ACTIVE_SOURCE", candidate.active_source),
        _rule(
            "PRIMARY_NEED_KIND",
            (
                candidate.primary_need_kind == "assessment_unknown"
                if candidate.candidate_type == "assessment"
                else (
                    candidate.primary_need_kind
                    in {
                        "readiness_unblock",
                        "prerequisite_unblock",
                        "multi_target_unblock",
                    }
                    if candidate.candidate_type == "unblock_task"
                    else (
                        candidate.primary_need_kind in {"review_due", "maintenance_aging_critical"}
                        if candidate.candidate_type in {"review", "maintenance"}
                        else candidate.primary_need_kind == "capability_gap"
                    )
                )
            ),
        ),
    ]
    if candidate.candidate_type == "unblock_task":
        rules.append(EligibilityRuleResultDTO("BLOCKER_CLEAR", "not_applicable", False))
    else:
        rules.append(_rule("BLOCKER_CLEAR", candidate.blocker_clear))
    if candidate.candidate_type in {"review", "maintenance", "verification"}:
        rules.append(EligibilityRuleResultDTO("TARGET_NOT_REACHED", "not_applicable", False))
    else:
        rules.append(_rule("TARGET_NOT_REACHED", not candidate.target_reached))
    if candidate.candidate_type in {"review", "maintenance"}:
        rules.append(
            _rule(
                "REVIEW_DUE",
                bool(candidate.review_due)
                or candidate.primary_need_kind == "maintenance_aging_critical",
            )
        )
    else:
        rules.append(EligibilityRuleResultDTO("REVIEW_DUE", "not_applicable", False))
    if candidate.candidate_type == "verification":
        rules.extend(
            (
                _rule("VERIFICATION_DUE", candidate.verification_due),
                _rule("VERIFICATION_TEMPLATE", candidate.verification_template_present),
            )
        )
    else:
        rules.extend(
            (
                EligibilityRuleResultDTO("VERIFICATION_DUE", "not_applicable", False),
                EligibilityRuleResultDTO("VERIFICATION_TEMPLATE", "not_applicable", False),
            )
        )
    if candidate.candidate_type == "assessment":
        rules.extend(
            (
                _rule("ASSESSMENT_RUBRIC", candidate.assessment_rubric_present),
                _rule("ASSESSMENT_UNKNOWN_NEED", candidate.assessment_unknown),
                _rule("ASSESSMENT_SCOPE", candidate.assessment_scope_valid),
            )
        )
    else:
        rules.extend(
            (
                EligibilityRuleResultDTO("ASSESSMENT_RUBRIC", "not_applicable", False),
                EligibilityRuleResultDTO("ASSESSMENT_UNKNOWN_NEED", "not_applicable", False),
                EligibilityRuleResultDTO("ASSESSMENT_SCOPE", "not_applicable", False),
            )
        )
    if candidate.candidate_type == "unblock_task":
        rules.extend(
            (
                _rule("ACTIONABLE_BLOCKER", candidate.blocker_actionable),
                _rule("BLOCKER_REFERENCE", candidate.blocker_reference_id is not None),
                _rule("UNBLOCK_ACTION_AVAILABLE", candidate.unblock_action_available),
            )
        )
    else:
        rules.extend(
            (
                EligibilityRuleResultDTO("ACTIONABLE_BLOCKER", "not_applicable", False),
                EligibilityRuleResultDTO("BLOCKER_REFERENCE", "not_applicable", False),
                EligibilityRuleResultDTO("UNBLOCK_ACTION_AVAILABLE", "not_applicable", False),
            )
        )
    duration_ok = _duration_valid(candidate)
    rules.append(_rule("DURATION_RANGE_VALID", duration_ok))
    if available_time_ms is None:
        rules.append(EligibilityRuleResultDTO("DURATION_FIT", "not_applicable", False))
    elif candidate.duration_range_ms is None:
        rules.append(_rule("DURATION_FIT", None))
    else:
        budget = available_time_ms - (available_time_ms % QUANTUM_MS)
        rules.append(_rule("DURATION_FIT", candidate.duration_range_ms[0] <= budget))

    subjects = tuple(
        sorted(
            {
                item
                for item in (
                    *candidate.served_target_identity_ids,
                    candidate.target_identity_id,
                    candidate.source_entity_id,
                    candidate.source_version_id,
                )
                if item is not None
            }
        )
    )
    values: dict[str, str | int | bool | None] = {
        "ACTIVE_TARGET": candidate.active_target,
        "PREREQUISITES": candidate.prerequisites_satisfied,
        "HARD_READINESS": candidate.hard_readiness_satisfied,
        "AVAILABILITY": candidate.availability_satisfied,
        "CAPABILITY_SUITABILITY": candidate.capability_suitability_satisfied,
        "CONTEXT_AVAILABLE": candidate.context_available,
        "ACTIVE_SOURCE": candidate.active_source,
        "PRIMARY_NEED_KIND": candidate.primary_need_kind,
        "BLOCKER_CLEAR": candidate.blocker_clear,
        "TARGET_NOT_REACHED": not candidate.target_reached,
        "REVIEW_DUE": candidate.review_due,
        "VERIFICATION_DUE": candidate.verification_due,
        "VERIFICATION_TEMPLATE": candidate.verification_template_present,
        "ASSESSMENT_RUBRIC": candidate.assessment_rubric_present,
        "ASSESSMENT_UNKNOWN_NEED": candidate.assessment_unknown,
        "ASSESSMENT_SCOPE": candidate.assessment_scope_valid,
        "ACTIONABLE_BLOCKER": candidate.blocker_actionable,
        "BLOCKER_REFERENCE": candidate.blocker_reference_id is not None,
        "UNBLOCK_ACTION_AVAILABLE": candidate.unblock_action_available,
        "DURATION_RANGE_VALID": duration_ok,
        "DURATION_FIT": (
            None
            if available_time_ms is None or candidate.duration_range_ms is None
            else candidate.duration_range_ms[0]
            <= available_time_ms - (available_time_ms % QUANTUM_MS)
        ),
    }
    reference_ids = {
        "PREREQUISITES": candidate.prerequisite_reference_ids,
        "HARD_READINESS": candidate.readiness_reference_ids,
        "AVAILABILITY": candidate.availability_requirement_ids,
        "BLOCKER_CLEAR": tuple(
            item for item in (candidate.blocker_reference_id,) if item is not None
        ),
        "ACTIONABLE_BLOCKER": tuple(
            item for item in (candidate.blocker_reference_id,) if item is not None
        ),
        "BLOCKER_REFERENCE": tuple(
            item for item in (candidate.blocker_reference_id,) if item is not None
        ),
        "UNBLOCK_ACTION_AVAILABLE": tuple(
            item for item in (candidate.blocker_reference_id,) if item is not None
        ),
        "ASSESSMENT_RUBRIC": (candidate.source_entity_id,),
        "ASSESSMENT_SCOPE": (candidate.source_entity_id, *candidate.served_target_identity_ids),
    }
    rules = [
        replace(
            item,
            facts=(
                ("candidateType", candidate.candidate_type),
                ("evaluatedValue", values[item.code]),
                *(
                    (
                        (
                            "durationReasonCode",
                            "DURATION_FIT_UNKNOWN"
                            if item.outcome == "unknown"
                            else "DURATION_MINIMUM_NOT_FIT",
                        ),
                    )
                    if item.code == "DURATION_FIT" and item.outcome in {"unknown", "fail"}
                    else ()
                ),
            ),
            subject_ids=tuple(sorted(set(reference_ids.get(item.code, subjects)))),
        )
        for item in rules
    ]

    for item in rules:
        if not item.decisive or item.outcome == "pass" or item.outcome == "not_applicable":
            continue
        return False, _rejection_code(candidate, item), tuple(rules)
    return True, "ELIGIBLE", tuple(rules)


def expected_learning_value(candidate: CandidateInputDTO) -> tuple[str, tuple[str, ...]]:
    if candidate.assessment_unknown and candidate.candidate_type == "assessment":
        if candidate.assessment_blocks_critical_planning:
            return "high", ("UNKNOWN_BLOCKS_CRITICAL_PLANNING",)
        return "normal", ("ASSESSMENT_REDUCES_UNKNOWN",)
    if candidate.multi_target_unblock:
        return "very_high", ("MULTI_TARGET_HARD_UNBLOCK",)
    if (
        candidate.target_priority in {"critical", "core"}
        and candidate.addresses_unmet_required_criterion
        and candidate.supplies_missing_required_mode
    ):
        return "very_high", ("REQUIRED_GAP_AND_MISSING_EVIDENCE_MODE",)
    high_reasons: list[str] = []
    if candidate.addresses_unmet_required_criterion:
        high_reasons.append("UNMET_REQUIRED_OUTCOME")
    if candidate.supplies_missing_independent_mode:
        high_reasons.append("MISSING_INDEPENDENT_EVIDENCE")
    if len(candidate.supports_transfer_target_ids) > 1:
        high_reasons.append("MULTI_TARGET_SUPPORTS_TRANSFER")
    if candidate.hard_blocker_count == 1 and candidate.blocker_actionable:
        high_reasons.append("ACTIONABLE_HARD_UNBLOCK")
    if high_reasons:
        return "high", tuple(high_reasons)
    if candidate.repeated_without_new_evidence:
        return "low", ("REPEATED_WITHOUT_NEW_EVIDENCE",)
    if candidate.recently_saturated and not candidate.review_due:
        return "low", ("RECENT_SATURATION",)
    if not candidate.intended_evidence_modes and candidate.gap_severity == "unknown":
        return "unknown", ("DECISIVE_LEARNING_VALUE_FACTS_MISSING",)
    return "normal", ("NORMAL_EXPECTED_LEARNING_VALUE",)


def _elv_audit(
    candidate: CandidateInputDTO,
    reasons: tuple[str, ...],
    *,
    include_proxy_facts: bool,
) -> tuple[
    tuple[tuple[str, str | int | bool | None], ...],
    tuple[str, ...],
]:
    facts: tuple[tuple[str, str | int | bool | None], ...] = (
        ("primaryOutcomeKind", candidate.primary_outcome_kind),
        ("primaryOutcomeId", candidate.primary_outcome_id),
        ("targetPriority", candidate.target_priority),
        ("addressesUnmetRequiredCriterion", candidate.addresses_unmet_required_criterion),
        ("suppliesMissingRequiredMode", candidate.supplies_missing_required_mode),
        (
            "suppliesMissingIndependentMode",
            candidate.supplies_missing_independent_mode,
        ),
        ("multiTargetUnblock", candidate.multi_target_unblock),
        ("assessmentBlocksCriticalPlanning", candidate.assessment_blocks_critical_planning),
        ("supportsTransferTargetCount", len(candidate.supports_transfer_target_ids)),
        ("reasonCodes", ",".join(reasons)),
    )
    if include_proxy_facts:
        explanation = dict(candidate.explanation_facts)
        facts += (
            ("repeatedWithoutNewEvidence", candidate.repeated_without_new_evidence),
            ("recentlySaturated", candidate.recently_saturated),
            ("reviewDue", candidate.review_due),
            ("daysSinceMeaningfulActivity", explanation.get("daysSinceMeaningfulActivity")),
            ("exposureDays42", explanation.get("exposureDays42")),
            (
                "recentSaturationMaximumDays",
                explanation.get("recentSaturationMaximumDays"),
            ),
            (
                "recentSaturationMinimumExposureDays",
                explanation.get("recentSaturationMinimumExposureDays"),
            ),
        )
    sources = tuple(
        sorted(
            {
                candidate.primary_outcome_id,
                *candidate.served_target_identity_ids,
                *candidate.supports_transfer_target_ids,
                *((candidate.blocker_reference_id,) if candidate.blocker_reference_id else ()),
            }
        )
    )
    return facts, sources


def _primary_need(candidate: CandidateInputDTO) -> int:
    if candidate.primary_need_kind == "assessment_unknown":
        return {
            "optional": 8,
            "supporting": 8,
            "important": 12,
            "core": 17,
            "critical": 17,
        }[candidate.target_priority]
    if candidate.primary_need_kind == "multi_target_unblock":
        return 22
    if candidate.primary_need_kind == "prerequisite_unblock":
        return 17
    if candidate.primary_need_kind == "readiness_unblock":
        return 13
    if candidate.primary_need_kind == "review_due":
        return {
            "optional": 6,
            "supporting": 6,
            "important": 10,
            "core": 15,
            "critical": 15,
        }[candidate.target_priority]
    if candidate.primary_need_kind == "maintenance_aging_critical":
        return 4
    return {"unknown": 0, "none": 0, "low": 6, "medium": 13, "high": 19, "critical": 24}[
        candidate.gap_severity
    ]


def _allocation_score(miss_basis_points: int | None) -> int:
    if miss_basis_points is None or miss_basis_points == 0:
        return 0
    magnitude = abs(miss_basis_points)
    value = 2 if magnitude <= 499 else (5 if magnitude <= 1499 else 8)
    return value if miss_basis_points > 0 else -value


def score(candidate: CandidateInputDTO, elv: str) -> tuple[ScoreComponentDTO, ...]:
    source_ids = tuple(
        sorted(
            {
                item
                for item in (
                    *candidate.served_target_identity_ids,
                    candidate.target_identity_id,
                    candidate.source_entity_id,
                    candidate.source_version_id,
                )
                if item is not None
            }
        )
    )
    components = (
        ScoreComponentDTO(
            "TARGET_PRIORITY",
            _PRIORITY_SCORE[candidate.target_priority],
            2,
            20,
            (("targetPriority", candidate.target_priority),),
            source_ids,
        ),
        ScoreComponentDTO(
            "PRIMARY_NEED",
            _primary_need(candidate),
            0,
            24,
            (("primaryNeedKind", candidate.primary_need_kind),),
            source_ids,
        ),
        ScoreComponentDTO(
            "DEADLINE_PRESSURE",
            _DEADLINE_SCORE[candidate.deadline_status],
            0,
            12,
            (("deadlineStatus", candidate.deadline_status),),
            source_ids,
        ),
        ScoreComponentDTO(
            "ALLOCATION_BALANCE",
            _allocation_score(candidate.allocation_miss_basis_points),
            -8,
            8,
            (("allocationMissBasisPoints", candidate.allocation_miss_basis_points),),
            source_ids,
        ),
        ScoreComponentDTO(
            "NEGLECT_OR_STALL",
            _SIGNAL_SCORE[candidate.neglect_or_stall_severity],
            0,
            10,
            (("severity", candidate.neglect_or_stall_severity),),
            source_ids,
        ),
        ScoreComponentDTO(
            "EXPECTED_LEARNING_VALUE",
            _ELV_SCORE[elv],
            0,
            12,
            (("expectedLearningValue", elv),),
            source_ids,
        ),
        ScoreComponentDTO(
            "CONTEXT_COST",
            _CONTEXT_SCORE[candidate.context_cost],
            -4,
            0,
            (("contextCost", candidate.context_cost),),
            source_ids,
        ),
    )
    total = sum(item.value for item in components)
    if not -10 <= total <= 86:
        raise ValueError("Recommendation score violated the approved theoretical bounds.")
    return components


def _structural_rank_facts(candidate: CandidateInputDTO) -> tuple[int, ...]:
    facts = dict(candidate.explanation_facts)
    return tuple(int(facts.get(key, 0) or 0) for key in STRUCTURAL_FACT_KEYS)


def _rank_key(item: EvaluatedCandidateDTO, *, structural_order: bool = False) -> tuple[object, ...]:
    candidate = item.candidate
    structural_key = (
        (
            tuple(-value for value in _structural_rank_facts(candidate))
            if candidate.candidate_type == "assessment"
            else (0,) * len(STRUCTURAL_FACT_KEYS)
        )
        if structural_order
        else ()
    )
    return (
        -(item.score_total or 0),
        _PRIORITY_RANK[candidate.target_priority],
        _DEADLINE_RANK[candidate.deadline_status],
        _ELV_RANK[item.expected_learning_value],
        0 if candidate.last_meaningful_activity_at is None else 1,
        candidate.last_meaningful_activity_at
        if candidate.last_meaningful_activity_at is not None
        else -1,
        *structural_key,
        candidate.stable_tie_key,
    )


def _outcome_identity(candidate: CandidateInputDTO) -> tuple[str, str, str]:
    return (
        candidate.primary_outcome_kind,
        candidate.primary_outcome_id,
        candidate.primary_need_identity,
    )


def _source_identity(candidate: CandidateInputDTO) -> tuple[str, str, str | None]:
    return candidate.source_type, candidate.source_entity_id, candidate.source_version_id


def _duration_allocate(
    selected: list[tuple[EvaluatedCandidateDTO, str]], available_time_ms: int | None
) -> tuple[dict[str, tuple[int | None, str | None]], tuple[str, ...]]:
    if available_time_ms is None:
        return (
            {
                item.candidate.stable_id: (
                    item.candidate.duration_range_ms[1]
                    if item.candidate.duration_range_ms is not None
                    else None,
                    None if item.candidate.duration_range_ms is not None else "DURATION_UNKNOWN",
                )
                for item, _role in selected
            },
            tuple(item.candidate.stable_id for item, _role in selected),
        )
    budget = available_time_ms - (available_time_ms % QUANTUM_MS)
    severity_rank = {"critical": 0, "high": 1, "attention": 2, "info": 3, None: 4}
    selected = sorted(
        selected,
        key=lambda pair: (
            0
            if pair[1] == "primary"
            else (
                1
                if pair[1] == "maintenance"
                and severity_rank[pair[0].candidate.maintenance_severity] <= 1
                else (2 if pair[1] == "complementary" else 3)
            ),
            pair[0].rank_ordinal or 0,
        ),
    )
    admitted = list(selected)

    def minimum_duration(item: EvaluatedCandidateDTO) -> int:
        duration = item.candidate.duration_range_ms
        assert duration is not None
        return duration[0]

    while admitted:
        minimum_total = sum(minimum_duration(item) for item, _role in admitted)
        if minimum_total <= budget:
            break
        admitted.pop()
    remaining = budget - sum(minimum_duration(item) for item, _role in admitted)
    allocations: dict[str, int] = {}
    for item, _role in admitted:
        duration = item.candidate.duration_range_ms
        assert duration is not None
        allocations[item.candidate.stable_id] = duration[0]
    for target_index in (1, 2):
        for item, _role in admitted:
            duration = item.candidate.duration_range_ms
            assert duration is not None
            need = duration[target_index] - allocations[item.candidate.stable_id]
            granted = min(need, remaining - (remaining % QUANTUM_MS))
            allocations[item.candidate.stable_id] += granted
            remaining -= granted
    return (
        {stable_id: (value, None) for stable_id, value in allocations.items()},
        tuple(item.candidate.stable_id for item, _role in admitted),
    )


def _reason_records(
    evaluated: tuple[EvaluatedCandidateDTO, ...],
    decisions: tuple[SelectionDecisionDTO, ...],
    *,
    reason_policy_version: str,
    deduplicate_reason_codes: bool,
) -> tuple[RecommendationReasonDTO, ...]:
    decisions_by_id = {item.candidate_stable_id: item for item in decisions}
    rows: list[RecommendationReasonDTO] = []
    for candidate_result in evaluated:
        candidate = candidate_result.candidate
        decision = decisions_by_id[candidate.stable_id]
        decisive_rule = next(
            (
                item
                for item in candidate_result.eligibility_rules
                if item.decisive and item.outcome in {"fail", "unknown"}
            ),
            None,
        )
        default_sources = tuple(
            sorted(
                {
                    item
                    for item in (
                        *candidate.served_target_identity_ids,
                        candidate.target_identity_id,
                        candidate.source_entity_id,
                        candidate.source_version_id,
                    )
                    if item is not None
                }
            )
        )
        reason_items: list[
            tuple[
                str,
                int | None,
                tuple[tuple[str, str | int | bool | None], ...],
                tuple[str, ...],
            ]
        ] = [
            (
                candidate_result.eligibility_reason,
                None,
                decisive_rule.facts if decisive_rule else (("eligibility", "passed"),),
                decisive_rule.subject_ids if decisive_rule else default_sources,
            ),
            *(
                (
                    code,
                    None,
                    candidate_result.expected_learning_value_facts,
                    candidate_result.expected_learning_value_source_ids,
                )
                for code in candidate_result.expected_learning_value_reasons
            ),
            *(
                (
                    component.code,
                    component.value,
                    component.decisive_facts,
                    component.source_ids,
                )
                for component in candidate_result.score_components
            ),
            (decision.reason_code, None, decision.decisive_facts, default_sources),
        ]
        if (
            reason_policy_version in {"recommendation-reason-policy/v3", REASON_POLICY_VERSION}
            and candidate.candidate_type == "assessment"
            and candidate_result.eligible
        ):
            structural_facts = dict(candidate.explanation_facts)
            reason_items.insert(
                -1,
                (
                    "ASSESSMENT_STRUCTURAL_ORDER",
                    None,
                    tuple(
                        (key, int(structural_facts.get(key, 0) or 0))
                        for key in STRUCTURAL_FACT_KEYS
                    )
                    + (("rankOrdinal", candidate_result.rank_ordinal),)
                    + (
                        (("semanticTieKey", candidate.stable_tie_key),)
                        if reason_policy_version == REASON_POLICY_VERSION
                        else ()
                    ),
                    default_sources,
                ),
            )
        if decision.duration_reason_code:
            reason_items.append(
                (
                    decision.duration_reason_code,
                    None,
                    decision.decisive_facts,
                    default_sources,
                )
            )
        if deduplicate_reason_codes:
            seen_reason_codes: set[str] = set()
            distinct_reason_items = []
            for item in reason_items:
                if item[0] in seen_reason_codes:
                    continue
                seen_reason_codes.add(item[0])
                distinct_reason_items.append(item)
            reason_items = distinct_reason_items
        for ordinal, (reason_code, contribution, facts, source_ids) in enumerate(reason_items):
            title = _REASON_TITLES.get(reason_code)
            if title is None:
                raise ValueError(f"Recommendation reason has no registered template: {reason_code}")
            if reason_code == "ASSESSMENT_STRUCTURAL_ORDER":
                rank_facts = dict(facts)
                rendered = (
                    "Equal-score assessment sequence signals: "
                    f"{rank_facts['soleHardUnitCount']} sole hard-unit blockers, "
                    f"{rank_facts['unresolvedHardUnitCount']} units with unresolved hard needs, "
                    f"{rank_facts['hardPrerequisiteTargetCount']} hard downstream targets, "
                    f"{rank_facts['recommendedBeforeTargetCount']} recommended-before targets, "
                    f"and {rank_facts['supportsTargetCount']} supported targets. "
                    "These are advisory ranking facts, not satisfied requirements."
                )
            else:
                rendered = (
                    f"{title} contributed {contribution:+d} to the deterministic ordering score."
                    if contribution is not None
                    else f"{title}."
                )
            rows.append(
                RecommendationReasonDTO(
                    candidate.stable_id,
                    ordinal,
                    reason_code,
                    title,
                    facts,
                    contribution,
                    f"recommendation-reason/{reason_code.lower()}",
                    reason_policy_version,
                    rendered,
                    source_ids,
                    reason_policy_version,
                )
            )
    return tuple(rows)


def _evaluate(
    candidates: tuple[CandidateInputDTO, ...],
    available_time_ms: int | None,
    *,
    explicit_usefulness_decision: bool,
    enhanced_elv_audit: bool,
    reason_policy_version: str,
    deduplicate_reason_codes: bool,
    structural_order: bool = False,
) -> RecommendationPolicyOutputDTO:
    evaluated: list[EvaluatedCandidateDTO] = []
    for candidate in sorted(candidates, key=lambda item: item.stable_tie_key):
        eligible, reason, rules = eligibility(candidate, available_time_ms)
        if not eligible:
            evaluated.append(
                EvaluatedCandidateDTO(
                    candidate, False, reason, rules, "unknown", (), (), (), (), None, None
                )
            )
            continue
        elv, elv_reasons = expected_learning_value(candidate)
        elv_facts, elv_sources = _elv_audit(
            candidate,
            elv_reasons,
            include_proxy_facts=enhanced_elv_audit,
        )
        components = score(candidate, elv)
        evaluated.append(
            EvaluatedCandidateDTO(
                candidate,
                True,
                reason,
                rules,
                elv,  # type: ignore[arg-type]
                elv_reasons,
                elv_facts,
                elv_sources,
                components,
                sum(item.value for item in components),
                None,
            )
        )
    ranked = sorted(
        (item for item in evaluated if item.eligible),
        key=lambda item: _rank_key(item, structural_order=structural_order),
    )
    rank_by_id = {item.candidate.stable_id: index + 1 for index, item in enumerate(ranked)}
    evaluated = [
        replace(item, rank_ordinal=rank_by_id.get(item.candidate.stable_id)) for item in evaluated
    ]
    ranked = sorted(
        (item for item in evaluated if item.eligible), key=lambda item: item.rank_ordinal or 0
    )

    useful = [item for item in ranked if item.candidate.usefulness]
    provisional: list[tuple[EvaluatedCandidateDTO, str]] = []
    if useful:
        primary = useful[0]
        provisional.append((primary, "primary"))
        complementary = next(
            (
                item
                for item in useful[1:]
                if _source_identity(item.candidate) != _source_identity(primary.candidate)
                and _outcome_identity(item.candidate) != _outcome_identity(primary.candidate)
                and item.candidate.candidate_type not in {"review", "maintenance"}
            ),
            None,
        )
        maintenance = next(
            (
                item
                for item in useful[1:]
                if item.candidate.candidate_type in {"review", "maintenance"}
                and _source_identity(item.candidate) != _source_identity(primary.candidate)
                and _outcome_identity(item.candidate) != _outcome_identity(primary.candidate)
                and (
                    complementary is None
                    or _outcome_identity(item.candidate)
                    != _outcome_identity(complementary.candidate)
                )
            ),
            None,
        )
        if complementary is not None:
            provisional.append((complementary, "complementary"))
        if maintenance is not None and maintenance not in [item for item, _ in provisional]:
            provisional.append((maintenance, "maintenance"))

    duration_allocations, admission_order = _duration_allocate(provisional, available_time_ms)
    selected_ids = set(duration_allocations)
    role_by_id = {
        item.candidate.stable_id: role
        for item, role in provisional
        if item.candidate.stable_id in selected_ids
    }
    admission_by_id = {stable_id: index + 1 for index, stable_id in enumerate(admission_order)}
    provisional_by_id = {item.candidate.stable_id: (item, role) for item, role in provisional}
    selected_items = [
        (item, role) for item, role in provisional if item.candidate.stable_id in selected_ids
    ]

    def displacement_for(item: EvaluatedCandidateDTO, was_provisional: bool) -> str | None:
        if was_provisional and admission_order:
            return admission_order[-1]
        for chosen, _role in selected_items:
            if _source_identity(chosen.candidate) == _source_identity(
                item.candidate
            ) or _outcome_identity(chosen.candidate) == _outcome_identity(item.candidate):
                return chosen.candidate.stable_id
        if item.candidate.candidate_type in {"review", "maintenance"}:
            selected_maintenance = next(
                (
                    chosen.candidate.stable_id
                    for chosen, role in selected_items
                    if role == "maintenance"
                ),
                None,
            )
            if selected_maintenance is not None:
                return selected_maintenance
        selected_complementary = next(
            (
                chosen.candidate.stable_id
                for chosen, role in selected_items
                if role == "complementary"
            ),
            None,
        )
        return selected_complementary or (admission_order[0] if admission_order else None)

    decisions: list[SelectionDecisionDTO] = []
    for item in evaluated:
        stable_id = item.candidate.stable_id
        if not item.eligible:
            decisions.append(
                SelectionDecisionDTO(
                    stable_id,
                    "ineligible",
                    None,
                    item.eligibility_reason,
                    None,
                    None,
                    None,
                    None,
                    (("eligibilityReason", item.eligibility_reason),),
                )
            )
        elif stable_id in selected_ids:
            advisory, duration_reason = duration_allocations[stable_id]
            decisions.append(
                SelectionDecisionDTO(
                    stable_id,
                    "selected",
                    role_by_id[stable_id],  # type: ignore[arg-type]
                    f"SELECTED_{role_by_id[stable_id].upper()}",
                    advisory,
                    duration_reason,
                    admission_by_id[stable_id],
                    None,
                    (
                        ("portfolioRole", role_by_id[stable_id]),
                        ("rankOrdinal", item.rank_ordinal),
                        *(((("usefulness", True)),) if explicit_usefulness_decision else ()),
                    ),
                )
            )
        else:
            if explicit_usefulness_decision and not item.candidate.usefulness:
                decisions.append(
                    SelectionDecisionDTO(
                        stable_id,
                        "not_selected",
                        None,
                        "NOT_USEFUL",
                        None,
                        None,
                        None,
                        None,
                        (
                            ("selectionConstraint", "usefulness"),
                            ("usefulness", False),
                            ("primaryOutcomeId", item.candidate.primary_outcome_id),
                        ),
                    )
                )
                continue
            was_provisional = stable_id in provisional_by_id
            displaced_by = displacement_for(item, was_provisional)
            decisions.append(
                SelectionDecisionDTO(
                    stable_id,
                    "not_selected",
                    None,
                    "DURATION_MINIMUM_NOT_FIT" if was_provisional else "PORTFOLIO_NOT_SELECTED",
                    None,
                    None,
                    None,
                    displaced_by,
                    (
                        (
                            "selectionConstraint",
                            "duration" if was_provisional else "portfolio_diversity",
                        ),
                        ("displacedByCandidateStableId", displaced_by),
                        ("primaryOutcomeId", item.candidate.primary_outcome_id),
                        *(((("usefulness", True)),) if explicit_usefulness_decision else ()),
                    ),
                )
            )
    evaluated_output = tuple(evaluated)
    decision_output = tuple(decisions)
    reasons = _reason_records(
        evaluated_output,
        decision_output,
        reason_policy_version=reason_policy_version,
        deduplicate_reason_codes=deduplicate_reason_codes,
    )
    evaluated_by_id = {item.candidate.stable_id: item for item in evaluated_output}
    recommendations = tuple(
        SelectedRecommendationDTO(
            decision.candidate_stable_id,
            decision.portfolio_role,  # type: ignore[arg-type]
            decision.advisory_duration_ms,
            evaluated_by_id[decision.candidate_stable_id].candidate.duration_range_ms,
            evaluated_by_id[decision.candidate_stable_id].rank_ordinal or 0,
            evaluated_by_id[decision.candidate_stable_id].score_total or 0,
            content_hash(
                [
                    asdict(component)
                    for component in evaluated_by_id[decision.candidate_stable_id].score_components
                ]
            ),
            decision.reason_code,
        )
        for decision in decision_output
        if decision.decision == "selected"
    )
    payload = {
        "candidates": [asdict(item) for item in evaluated_output],
        "decisions": [asdict(item) for item in decision_output],
        "reasons": [asdict(item) for item in reasons],
        "recommendations": [asdict(item) for item in recommendations],
    }
    return RecommendationPolicyOutputDTO(
        evaluated_output,
        decision_output,
        reasons,
        recommendations,
        content_hash(payload),
    )


def evaluate(
    candidates: tuple[CandidateInputDTO, ...], available_time_ms: int | None
) -> RecommendationPolicyOutputDTO:
    return _evaluate(
        candidates,
        available_time_ms,
        explicit_usefulness_decision=True,
        enhanced_elv_audit=True,
        reason_policy_version=REASON_POLICY_VERSION,
        deduplicate_reason_codes=True,
        structural_order=True,
    )


def evaluate_v3(
    candidates: tuple[CandidateInputDTO, ...], available_time_ms: int | None
) -> RecommendationPolicyOutputDTO:
    """Replay-only v3 evaluator with its original reason policy and audit shape."""
    return _evaluate(
        candidates,
        available_time_ms,
        explicit_usefulness_decision=True,
        enhanced_elv_audit=True,
        reason_policy_version="recommendation-reason-policy/v3",
        deduplicate_reason_codes=True,
        structural_order=True,
    )


def evaluate_v2(
    candidates: tuple[CandidateInputDTO, ...], available_time_ms: int | None
) -> RecommendationPolicyOutputDTO:
    return _evaluate(
        candidates,
        available_time_ms,
        explicit_usefulness_decision=True,
        enhanced_elv_audit=True,
        reason_policy_version="recommendation-reason-policy/v2",
        deduplicate_reason_codes=True,
    )


def evaluate_v1(
    candidates: tuple[CandidateInputDTO, ...], available_time_ms: int | None
) -> RecommendationPolicyOutputDTO:
    """Replay-only evaluator retained for registry/v1 historical output identity."""
    return _evaluate(
        candidates,
        available_time_ms,
        explicit_usefulness_decision=False,
        enhanced_elv_audit=False,
        reason_policy_version="recommendation-reason-policy/v1",
        deduplicate_reason_codes=False,
    )


_LEGACY_POLICY_BUNDLE = {
    "application": APPLICATION_VERSION,
    "algorithm": LEGACY_RECOMMENDATION_ALGORITHM_VERSION,
    "candidate": "recommendation-candidate-policy/v1",
    "eligibility": ELIGIBILITY_POLICY_VERSION,
    "expectedLearningValue": ELV_POLICY_VERSION,
    "score": SCORE_POLICY_VERSION,
    "duration": DURATION_POLICY_VERSION,
    "portfolio": "recommendation-portfolio-policy/v1",
    "reason": "recommendation-reason-policy/v1",
    "registry": LEGACY_POLICY_REGISTRY_VERSION,
}

_PREVIOUS_POLICY_BUNDLE = {
    "application": APPLICATION_VERSION,
    "algorithm": "recommendation-algorithm/v2.1",
    "candidate": "recommendation-candidate-policy/v2",
    "eligibility": ELIGIBILITY_POLICY_VERSION,
    "expectedLearningValue": ELV_POLICY_VERSION,
    "score": SCORE_POLICY_VERSION,
    "duration": DURATION_POLICY_VERSION,
    "portfolio": PORTFOLIO_POLICY_VERSION,
    "reason": "recommendation-reason-policy/v2",
    "registry": PREVIOUS_POLICY_REGISTRY_VERSION,
}

_V3_POLICY_BUNDLE = {
    "application": APPLICATION_VERSION,
    "algorithm": "recommendation-algorithm/v2.2",
    "candidate": "recommendation-candidate-policy/v3",
    "eligibility": ELIGIBILITY_POLICY_VERSION,
    "expectedLearningValue": ELV_POLICY_VERSION,
    "score": SCORE_POLICY_VERSION,
    "duration": DURATION_POLICY_VERSION,
    "portfolio": PORTFOLIO_POLICY_VERSION,
    "reason": "recommendation-reason-policy/v3",
    "registry": V3_POLICY_REGISTRY_VERSION,
}

_REGISTERED_POLICIES = {
    LEGACY_POLICY_REGISTRY_VERSION: (
        _LEGACY_POLICY_BUNDLE,
        evaluate_v1,
        build_candidates_v1,
    ),
    PREVIOUS_POLICY_REGISTRY_VERSION: (_PREVIOUS_POLICY_BUNDLE, evaluate_v2, build_candidates),
    V3_POLICY_REGISTRY_VERSION: (_V3_POLICY_BUNDLE, evaluate_v3, build_candidates_v3),
    POLICY_REGISTRY_VERSION: (policy_bundle(), evaluate, build_candidates_v4),
}


def registered_policy_bundle(registry_version: str) -> dict[str, str]:
    try:
        bundle, _evaluator, _candidate_builder = _REGISTERED_POLICIES[registry_version]
    except KeyError as exc:
        raise KeyError(registry_version) from exc
    return dict(bundle)


def evaluate_registered(
    registry_version: str,
    candidates: tuple[CandidateInputDTO, ...],
    available_time_ms: int | None,
) -> RecommendationPolicyOutputDTO:
    try:
        _bundle, evaluator, _candidate_builder = _REGISTERED_POLICIES[registry_version]
    except KeyError as exc:
        raise KeyError(registry_version) from exc
    return evaluator(candidates, available_time_ms)


def registered_candidate_builder(
    registry_version: str,
) -> Callable[..., tuple[CandidateInputDTO, ...]]:
    try:
        _bundle, _evaluator, candidate_builder = _REGISTERED_POLICIES[registry_version]
    except KeyError as exc:
        raise KeyError(registry_version) from exc
    return cast(Callable[..., tuple[CandidateInputDTO, ...]], candidate_builder)
