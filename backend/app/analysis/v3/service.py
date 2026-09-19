from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.analysis.contracts import canonical_json, content_hash
from app.analysis.v3.contracts import NormalizedFactDTO, UnknownMarkerDTO
from app.analysis.v3.models import (
    AnalysisV3CompetencyGap,
    AnalysisV3CurrentState,
    AnalysisV3NormalizedFact,
    AnalysisV3RunLineage,
    AnalysisV3Signal,
    AnalysisV3SnapshotDetail,
    AnalysisV3UnknownMarker,
)
from app.analysis.v3.policy import (
    ANALYSIS_ALGORITHM_VERSION,
    ANALYSIS_POLICY_VERSION,
    APPLICATION_VERSION,
    LEGACY_NORMALIZATION_SCHEMA_VERSION,
    NORMALIZATION_SCHEMA_VERSION,
    PURPOSE_MATRIX_VERSION,
    analysis_policy_bundle,
    analyze_normalized_facts,
)
from app.analysis_sources import (
    actual_session_summaries_as_of,
    analysis_source_generation,
    discipline_configuration_as_of,
    evidence_coverage_as_of,
    evidence_qualification_matches_target,
    last_positive_capability_transition_as_of,
    readiness_evaluations_as_of,
)
from app.capability_views import (
    evaluate_capability_as_of,
)
from app.curriculum.service import build_catalog as build_curriculum_catalog
from app.errors import AppError
from app.learning_graph.service import active_projection_graph_as_of
from app.models import AnalysisRun, AnalysisSnapshot, ProjectionInvalidation
from app.profile_views import (
    active_profile_projection_as_of,
    active_semantic_definition_ids_as_of,
)
from app.projects.service import build_catalog as build_project_catalog
from app.time_utils import datetime_to_epoch_ms, utc_now_ms

SCOPE_KEY = "learning-control"


def _stable_id(namespace: str, stable_key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"lcc:{namespace}:{stable_key}"))


def _source_generation(db: Session) -> int:
    return analysis_source_generation(db)


def _deadline(
    target_date: str | None, target_month: str | None, today: date
) -> tuple[str, str | None]:
    if target_date:
        deadline = date.fromisoformat(target_date)
    elif target_month:
        year, month = (int(part) for part in target_month.split("-"))
        next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        deadline = next_month - timedelta(days=1)
    else:
        return "none", None
    days = (deadline - today).days
    status = (
        "overdue" if days < 0 else "due" if days == 0 else "due_soon" if days <= 90 else "later"
    )
    return status, deadline.isoformat()


def _comparison(current: int | None, target: int) -> str:
    if current is None:
        return "unknown"
    return (
        "below_target" if current < target else "above_target" if current > target else "at_target"
    )


def _discipline_facts(
    sessions: tuple[Any, ...], completed_through: date, config: dict[str, object]
) -> tuple[NormalizedFactDTO, NormalizedFactDTO]:
    by_date: dict[date, int] = {}
    for item in sessions:
        local_day = date.fromisoformat(item.local_date)
        if local_day <= completed_through:
            by_date[local_day] = by_date.get(local_day, 0) + item.duration_ms
    configured_target_days = config.get("weeklyTargetActiveDays")
    target_days = int(configured_target_days) if isinstance(configured_target_days, int) else None
    monday = completed_through.fromordinal(
        completed_through.toordinal() - completed_through.weekday()
    )
    completed_weeks: list[dict[str, Any]] = []
    for offset in (28, 21, 14, 7):
        start = monday.fromordinal(monday.toordinal() - offset)
        end = start.fromordinal(start.toordinal() + 6)
        active_days = sum(1 for day in by_date if start <= day <= end and by_date[day] > 0)
        completed_weeks.append(
            {"weekStart": start.isoformat(), "weekEnd": end.isoformat(), "activeDays": active_days}
        )
    below = (
        [item["activeDays"] < target_days for item in completed_weeks]
        if target_days is not None
        else []
    )
    zero_consecutive = any(
        completed_weeks[index]["activeDays"] == 0 and completed_weeks[index + 1]["activeDays"] == 0
        for index in range(3)
    )
    severity = (
        "critical"
        if target_days is not None and (sum(below) >= 3 or zero_consecutive)
        else "high"
        if target_days is not None and sum(below) >= 2
        else "attention"
        if target_days is not None and below[-1]
        else None
    )
    discipline = NormalizedFactDTO(
        "discipline|four-completed-weeks",
        "discipline_variance",
        "discipline",
        "global",
        {
            "weeks": completed_weeks,
            "targetActiveDays": target_days,
            "severity": severity,
            "reasonCode": (
                "WEEKLY_TARGET_UNKNOWN"
                if target_days is None
                else "COMPLETED_WEEK_TARGET_VARIANCE"
                if severity
                else "DISCIPLINE_ON_TARGET"
            ),
        },
    )
    target_duration = config.get("targetDurationMsPerActiveDay")
    last_seven = [
        duration
        for day, duration in by_date.items()
        if 0 <= (completed_through - day).days < 7 and duration > 0
    ]
    workload_payload: dict[str, object] = {
        "targetDurationMs": target_duration,
        "activeDayDurationsMs": sorted(last_seven),
        "severity": None,
        "reasonCode": "WORKLOAD_INSUFFICIENT_DATA",
        "surgeComparisonStatus": "not_evaluated",
    }
    if isinstance(target_duration, int) and target_duration > 0 and len(last_seven) >= 3:
        ordered = sorted(last_seven)
        median = (
            ordered[len(ordered) // 2]
            if len(ordered) % 2
            else (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) // 2
        )
        basis_points = median * 10_000 // target_duration
        severity = (
            "critical"
            if basis_points >= 20_000
            else "high"
            if basis_points >= 15_000
            else "attention"
            if basis_points >= 12_500
            else None
        )
        current_total = sum(last_seven)
        prior_start = completed_through - timedelta(days=27)
        prior_end = completed_through - timedelta(days=7)
        prior_total = sum(
            duration for day, duration in by_date.items() if prior_start <= day <= prior_end
        )
        weekly_equivalent = prior_total // 3 if prior_total else None
        surge_basis_points = (
            current_total * 10_000 // weekly_equivalent if weekly_equivalent else None
        )
        if surge_basis_points is not None and surge_basis_points >= 17_500:
            bands = (None, "attention", "high", "critical")
            severity = bands[min(bands.index(severity) + 1, 3)]
        workload_payload.update(
            {
                "medianDurationMs": median,
                "medianBasisPointsOfTarget": basis_points,
                "currentSevenDayTotalDurationMs": current_total,
                "precedingTwentyOneDayTotalDurationMs": prior_total,
                "precedingWeeklyEquivalentDurationMs": weekly_equivalent,
                "surgeBasisPoints": surge_basis_points,
                "surgeComparisonStatus": (
                    "evaluated" if surge_basis_points is not None else "unknown"
                ),
                "severity": severity,
                "reasonCode": "ACTIVE_DAY_DURATION_ABOVE_TARGET"
                if severity
                else "WORKLOAD_WITHIN_TARGET",
            }
        )
    workload = NormalizedFactDTO(
        "discipline|workload-seven-days",
        "workload_risk",
        "discipline",
        "global",
        workload_payload,
    )
    return discipline, workload


def build_analysis_inputs(
    db: Session,
    cutoff_at: int,
    purpose: str,
    *,
    normalization_schema_version: str = NORMALIZATION_SCHEMA_VERSION,
) -> tuple[
    tuple[NormalizedFactDTO, ...],
    tuple[UnknownMarkerDTO, ...],
    dict[str, Any],
]:
    configuration = discipline_configuration_as_of(db, exclusive_cutoff_at=cutoff_at)
    unknowns: list[UnknownMarkerDTO] = []
    if configuration is None:
        timezone_name = "UTC"
        config_payload: dict[str, object] = {
            "weeklyTargetActiveDays": None,
            "targetDurationMsPerActiveDay": None,
            "timezone": "UTC",
        }
        unknowns.append(
            UnknownMarkerDTO(
                "configuration", "discipline", "global", "DISCIPLINE_CONFIGURATION_MISSING"
            )
        )
    else:
        config_payload = configuration.configuration
        timezone_name = str(config_payload["timezone"])
    local_cutoff = datetime.fromtimestamp(cutoff_at / 1000, tz=ZoneInfo(timezone_name))
    analysis_local_date = local_cutoff.date()
    completed_through = analysis_local_date - timedelta(days=1)
    profile = active_profile_projection_as_of(db, exclusive_cutoff_at=cutoff_at)
    graph = active_projection_graph_as_of(db, exclusive_cutoff_at=cutoff_at)
    curricula = build_curriculum_catalog(db, cutoff_at)
    projects = build_project_catalog(db, cutoff_at)
    sessions = actual_session_summaries_as_of(
        db, exclusive_cutoff_at=cutoff_at, timezone_name=timezone_name
    )
    evidence = evidence_coverage_as_of(db, exclusive_cutoff_at=cutoff_at)
    evidence_by_competency = {item.competency_identity_id: item for item in evidence}
    facts: list[NormalizedFactDTO] = []
    active_semantics = active_semantic_definition_ids_as_of(db, exclusive_cutoff_at=cutoff_at)
    capability_inputs: list[dict[str, Any]] = []
    if graph is None and profile is not None:
        unknowns.append(
            UnknownMarkerDTO("learningGraph", "learning_graph", "active", "LEARNING_GRAPH_MISSING")
        )
    if purpose == "candidate_readiness" and not curricula.active_version_references:
        unknowns.append(
            UnknownMarkerDTO("curriculum", "curriculum", "active", "CURRICULUM_MISSING")
        )
    if profile is None:
        unknowns.append(
            UnknownMarkerDTO("targetProfile", "target_profile", "active", "TARGET_PROFILE_MISSING")
        )
    else:
        readiness = readiness_evaluations_as_of(
            db,
            profile=profile,
            exclusive_cutoff_at=cutoff_at,
            timezone_name=timezone_name,
        )
        gate_definitions = {item.id: item for item in profile.readiness_gates}
        milestone_deadlines = {
            item.id: item.target_date for item in profile.milestones if item.target_date
        }
        gates_by_target: dict[str, list[Any]] = {}
        for gate in readiness:
            for target_id in gate.target_ids:
                gates_by_target.setdefault(target_id, []).append(gate)
        graph_satisfaction = (
            {item.edge_definition_id: item for item in graph.satisfactions} if graph else {}
        )
        graph_edges = graph.edges if graph else ()
        sessions_by_competency: dict[str, list[Any]] = {}
        for item in sessions:
            if item.primary_competency_identity_id:
                sessions_by_competency.setdefault(item.primary_competency_identity_id, []).append(
                    item
                )
        for target in profile.targets:
            semantic_id = active_semantics.get(target.competency_identity_id)
            capability_evaluation = (
                evaluate_capability_as_of(
                    db,
                    competency_identity_id=target.competency_identity_id,
                    semantic_definition_id=semantic_id,
                    dimension_id=target.dimension_id,
                    exclusive_cutoff_at=cutoff_at,
                    timezone_name=timezone_name,
                )
                if semantic_id
                else None
            )
            capability = capability_evaluation.capability if capability_evaluation else None
            review = capability_evaluation.review if capability_evaluation else None
            criteria = capability_evaluation.criteria if capability_evaluation else ()
            if capability_evaluation is not None:
                capability_inputs.append(
                    {
                        "targetIdentityId": target.target_identity_id,
                        "competencyIdentityId": target.competency_identity_id,
                        "semanticDefinitionId": semantic_id,
                        "dimensionId": target.dimension_id,
                        "inputPayload": capability_evaluation.input_payload,
                        "inputHash": capability_evaluation.input_hash,
                        "criterionInputHashes": [
                            {
                                "criterionDefinitionId": item.criterion_definition_id,
                                "evidenceSetHash": item.evidence_set_hash,
                            }
                            for item in criteria
                        ],
                    }
                )
            deadline_status, deadline_date = _deadline(
                target.target_date, target.target_month, analysis_local_date
            )
            target_gates = []
            for gate in gates_by_target.get(target.id, []):
                gate_definition = gate_definitions[gate.gate_id]
                gate_deadline = milestone_deadlines.get(gate_definition.milestone_id or "")
                gate_deadline_status, resolved_gate_deadline = _deadline(
                    gate_deadline, None, analysis_local_date
                )
                target_gates.append(
                    {
                        "gateId": gate.gate_id,
                        "state": gate.state,
                        "effect": gate.effect,
                        "deadlineStatus": gate_deadline_status,
                        "deadlineDate": resolved_gate_deadline,
                        "predicates": [asdict(item) for item in gate.predicates],
                    }
                )
            prerequisites = []
            for edge in graph_edges:
                if (
                    edge.edge_type == "prerequisite"
                    and edge.target_competency_identity_id == target.competency_identity_id
                ):
                    satisfaction = graph_satisfaction[edge.id]
                    prerequisites.append(
                        {
                            "edgeId": edge.id,
                            "aggregateState": satisfaction.aggregate_state,
                            "eligibilitySatisfied": satisfaction.eligibility_satisfied,
                            "unknownReasons": list(satisfaction.unknown_reasons),
                        }
                    )
            target_sessions = sessions_by_competency.get(target.competency_identity_id, [])
            last_activity = max((item.ended_at for item in target_sessions), default=None)
            days_since_activity = (
                (
                    completed_through
                    - datetime.fromtimestamp(
                        last_activity / 1000, tz=ZoneInfo(timezone_name)
                    ).date()
                ).days
                if last_activity is not None
                else (
                    completed_through
                    - datetime.fromtimestamp(
                        target.activated_at / 1000, tz=ZoneInfo(timezone_name)
                    ).date()
                ).days
            )
            window_start = completed_through.fromordinal(completed_through.toordinal() - 41)
            exposure_days = len(
                {
                    item.local_date
                    for item in target_sessions
                    if window_start <= date.fromisoformat(item.local_date) <= completed_through
                    and any(
                        evidence_qualification_matches_target(
                            db,
                            qualification=qualification,
                            target=target,
                            active_semantics=active_semantics,
                        )
                        for qualification in item.active_evidence_qualifications
                    )
                }
            )
            coverage = evidence_by_competency.get(target.competency_identity_id)
            positive_transition_at = last_positive_capability_transition_as_of(
                db,
                competency_identity_id=target.competency_identity_id,
                dimension_id=target.dimension_id,
                exclusive_cutoff_at=cutoff_at,
            )
            comparison = _comparison(
                capability.selected_level_ordinal if capability else None,
                target.target_level_ordinal,
            )
            fact_payload = {
                "targetIdentityId": target.target_identity_id,
                "profileTargetId": target.id,
                "competencyIdentityId": target.competency_identity_id,
                "semanticDefinitionId": semantic_id,
                "dimensionKey": target.dimension_key,
                "scaleVersionId": target.scale_version_id,
                "targetLevelId": target.target_level_id,
                "targetLevelOrdinal": target.target_level_ordinal,
                "currentLevelId": capability.selected_level_id if capability else None,
                "currentLevelOrdinal": capability.selected_level_ordinal if capability else None,
                "assessmentStatus": capability.assessment_status if capability else "unknown",
                "comparisonStatus": comparison,
                "confidence": capability.aggregate_confidence if capability else "unknown",
                "freshness": review.freshness if review else "unknown",
                "reviewDue": review.review_due if review else None,
                "unmetRequiredCriterionIds": [
                    item.criterion_definition_id
                    for item in criteria
                    if item.requirement_type == "required" and item.state == "not_demonstrated"
                ],
                "partialRequiredCriterionIds": [
                    item.criterion_definition_id
                    for item in criteria
                    if item.requirement_type == "required"
                    and item.state in {"partially_demonstrated", "unknown"}
                ],
                "contradictedRequiredCriterionIds": [
                    item.criterion_definition_id
                    for item in criteria
                    if item.requirement_type == "required" and item.state == "contradicted"
                ],
                "contradictedImportantCriterionIds": [
                    item.criterion_definition_id
                    for item in criteria
                    if item.requirement_type != "required" and item.state == "contradicted"
                ],
                "openLesserCriterionIds": [
                    item.criterion_definition_id
                    for item in criteria
                    if item.requirement_type != "required" and item.state != "demonstrated"
                ],
                "supportingEvidenceIds": list(coverage.supporting_ids) if coverage else [],
                "contradictingEvidenceIds": list(coverage.contradicting_ids) if coverage else [],
                "independentEvidenceIds": list(coverage.independent_ids) if coverage else [],
                "missingEvidenceRequirements": [
                    item.criterion_definition_id
                    for item in criteria
                    if item.requirement_type == "required"
                    and item.state not in {"demonstrated", "contradicted"}
                    and (
                        "independent" not in item.demonstration_rule
                        or item.evaluation_facts.get("meaningfulSupportOccurrenceCount", 0) == 0
                        or item.evaluation_facts.get(
                            "independenceCompatibleSupportOccurrenceCount", 0
                        )
                        != 0
                    )
                ],
                "missingIndependentCriterionIds": [
                    item.criterion_definition_id
                    for item in criteria
                    if item.requirement_type == "required"
                    and "independent" in item.demonstration_rule
                    and item.state not in {"demonstrated", "contradicted"}
                    and item.evaluation_facts.get("meaningfulSupportOccurrenceCount", 0) != 0
                    and item.evaluation_facts.get("independenceCompatibleSupportOccurrenceCount", 0)
                    == 0
                ],
                "importantSupportingMissingCriterionIds": [
                    item.criterion_definition_id
                    for item in criteria
                    if item.requirement_type != "required" and item.state != "demonstrated"
                ],
                "criterionEvaluationFacts": [asdict(item) for item in criteria],
                "readinessGates": target_gates,
                "prerequisites": prerequisites,
                "deadlineStatus": deadline_status,
                "deadlineDate": deadline_date,
                "priority": target.priority,
                "daysSinceMeaningfulActivity": max(days_since_activity, 0),
                "lastMeaningfulActivityAt": last_activity,
                "exposureDays42": exposure_days,
                "stallEligible": comparison == "below_target" and exposure_days >= 3,
                "daysSincePositiveTransition": (
                    max(
                        (
                            completed_through
                            - datetime.fromtimestamp(
                                (
                                    positive_transition_at
                                    if positive_transition_at is not None
                                    else target.activated_at
                                )
                                / 1000,
                                tz=ZoneInfo(timezone_name),
                            ).date()
                        ).days,
                        0,
                    )
                ),
                "criticalGateDue": any(
                    gate["state"] in {"not_met", "unknown"}
                    and gate["effect"] == "hard_eligibility"
                    and gate["deadlineStatus"] in {"due", "overdue"}
                    for gate in target_gates
                ),
                "inputLineage": [
                    {"kind": "profile_target", "id": target.id},
                    {
                        "kind": "capability_computed_as_of",
                        "inputHash": (
                            capability_evaluation.input_hash if capability_evaluation else None
                        ),
                    },
                    {
                        "kind": "review_computed_as_of",
                        "inputHash": (
                            capability_evaluation.input_hash if capability_evaluation else None
                        ),
                    },
                    {
                        "kind": "evidence_coverage",
                        "hash": coverage.input_hash if coverage else None,
                    },
                    *[
                        {
                            "kind": "criterion_computed_as_of",
                            "criterionDefinitionId": item.criterion_definition_id,
                            "evidenceSetHash": item.evidence_set_hash,
                        }
                        for item in criteria
                    ],
                    *[
                        {
                            "kind": "readiness_predicate",
                            "gateId": gate["gateId"],
                            "predicateId": predicate["predicate_id"],
                            "state": predicate["state"],
                            "reasonCode": predicate["reason_code"],
                        }
                        for gate in target_gates
                        for predicate in gate["predicates"]
                    ],
                    *[
                        {
                            "kind": "learning_graph_satisfaction",
                            "edgeId": edge["edgeId"],
                            "state": edge["aggregateState"],
                        }
                        for edge in prerequisites
                    ],
                ],
            }
            facts.append(
                NormalizedFactDTO(
                    f"target|{target.target_identity_id}",
                    "target_state",
                    "profile_target",
                    target.target_identity_id,
                    fact_payload,
                )
            )
        domain_duration: dict[str, int] = {domain.id: 0 for domain in profile.domains}
        domain_by_competency = {
            target.competency_identity_id: target.profile_domain_id for target in profile.targets
        }
        assigned_total = 0
        total_duration = 0
        active_dates: set[str] = set()
        assigned_active_dates: set[str] = set()
        completed_start = completed_through.fromordinal(completed_through.toordinal() - 27)
        for item in sessions:
            day = date.fromisoformat(item.local_date)
            if not completed_start <= day <= completed_through:
                continue
            active_dates.add(item.local_date)
            total_duration += item.duration_ms
            domain_id = domain_by_competency.get(item.primary_competency_identity_id or "")
            if domain_id:
                domain_duration[domain_id] += item.duration_ms
                assigned_total += item.duration_ms
                assigned_active_dates.add(item.local_date)
        sufficient = len(assigned_active_dates) >= 3 and assigned_total >= 3_600_000
        for domain in profile.domains:
            actual_bp = (
                domain_duration[domain.id] * 10_000 // assigned_total if assigned_total else None
            )
            minimum_bp = (
                domain.minimum_percent * 100 if domain.minimum_percent is not None else None
            )
            maximum_bp = (
                domain.maximum_percent * 100 if domain.maximum_percent is not None else None
            )
            miss = 0
            if actual_bp is not None and minimum_bp is not None and actual_bp < minimum_bp:
                miss = actual_bp - minimum_bp
            elif actual_bp is not None and maximum_bp is not None and actual_bp > maximum_bp:
                miss = actual_bp - maximum_bp
            facts.append(
                NormalizedFactDTO(
                    f"allocation|{domain.stable_key}",
                    "allocation",
                    "profile_domain",
                    domain.id,
                    {
                        "windowStart": completed_start.isoformat(),
                        "windowEnd": completed_through.isoformat(),
                        "timezone": timezone_name,
                        "durationMs": domain_duration[domain.id],
                        "assignedTotalDurationMs": assigned_total,
                        "unassignedDurationMs": max(total_duration - assigned_total, 0),
                        "totalDurationMs": total_duration,
                        "activeDays": len(assigned_active_dates),
                        "allActiveDays": len(active_dates),
                        "actualBasisPoints": actual_bp,
                        "minimumBasisPoints": minimum_bp,
                        "maximumBasisPoints": maximum_bp,
                        "missBasisPoints": miss,
                        "sufficientData": sufficient
                        and (minimum_bp is not None or maximum_bp is not None),
                    },
                )
            )
    facts.append(
        NormalizedFactDTO(
            "curriculum|catalog",
            "curriculum_catalog",
            "curriculum",
            "active",
            {
                "activeVersionReferences": [
                    asdict(item) for item in curricula.active_version_references
                ],
                "unitCount": len(curricula.units),
                "assessmentRubricCount": len(curricula.assessment_rubrics),
                "inputHash": curricula.input_hash,
            },
        )
    )
    for candidate in projects.candidates:
        facts.append(
            NormalizedFactDTO(
                f"project-task|{candidate.task_definition_id}",
                "project_task_state",
                "project_task",
                candidate.task_definition_id,
                {
                    "projectId": candidate.project_id,
                    "projectVersionId": candidate.project_version_id,
                    "taskIdentityId": candidate.task_identity_id,
                    "lifecycleState": candidate.lifecycle_state,
                    "availabilityState": candidate.availability_state,
                    "readinessState": candidate.readiness_state,
                    "candidateUsabilityState": candidate.candidate_usability_state,
                    "actionableBlockerKeys": list(candidate.actionable_blocker_keys),
                    "inputHash": candidate.input_hash,
                },
            )
        )
    discipline, workload = _discipline_facts(sessions, completed_through, config_payload)
    facts.extend((discipline, workload))
    session_summaries = [asdict(item) for item in sessions]
    if normalization_schema_version == LEGACY_NORMALIZATION_SCHEMA_VERSION:
        for summary_payload in session_summaries:
            summary_payload.pop("active_contribution_attributions", None)
    elif normalization_schema_version != NORMALIZATION_SCHEMA_VERSION:
        raise AppError(
            409,
            "ANALYSIS_POLICY_UNAVAILABLE",
            "The requested Analysis normalization policy is unavailable.",
        )
    lineage = {
        "cutoffAt": cutoff_at,
        "cutoffSemantics": "exclusive",
        "timezone": timezone_name,
        "completedThroughDate": completed_through.isoformat(),
        "analysisLocalDate": analysis_local_date.isoformat(),
        "purpose": purpose,
        "activeSemanticDefinitions": [
            {"competencyIdentityId": key, "semanticDefinitionId": value}
            for key, value in sorted(active_semantics.items())
        ],
        "capabilityInputs": sorted(
            capability_inputs,
            key=lambda item: (str(item["targetIdentityId"]), str(item["dimensionId"])),
        ),
        "profile": asdict(profile) if profile else None,
        "graph": asdict(graph) if graph else None,
        "curriculumCatalog": asdict(curricula),
        "projectCatalog": asdict(projects),
        "disciplineConfiguration": asdict(configuration) if configuration else None,
        "sessionSummaries": session_summaries,
        "evidenceCoverage": [asdict(item) for item in evidence],
    }
    return tuple(facts), tuple(unknowns), lineage


def _serialize_snapshot(db: Session, snapshot: AnalysisSnapshot) -> dict[str, Any]:
    lineage = db.get(AnalysisV3RunLineage, snapshot.run_id)
    gaps = db.scalars(
        select(AnalysisV3CompetencyGap)
        .where(AnalysisV3CompetencyGap.snapshot_id == snapshot.id)
        .order_by(AnalysisV3CompetencyGap.ordinal)
    ).all()
    signals = db.scalars(
        select(AnalysisV3Signal)
        .where(AnalysisV3Signal.snapshot_id == snapshot.id)
        .order_by(AnalysisV3Signal.ordinal)
    ).all()
    unknowns = db.scalars(
        select(AnalysisV3UnknownMarker)
        .where(AnalysisV3UnknownMarker.snapshot_id == snapshot.id)
        .order_by(AnalysisV3UnknownMarker.ordinal)
    ).all()
    return {
        "id": snapshot.id,
        "runId": snapshot.run_id,
        "purpose": snapshot.purpose,
        "schemaVersion": snapshot.schema_version,
        "generatedAt": snapshot.generated_at,
        "cutoffAt": snapshot.cutoff_at,
        "cutoffSemantics": snapshot.cutoff_semantics,
        "timezone": snapshot.timezone,
        "completedThroughDate": snapshot.completed_through_date,
        "completeness": snapshot.completeness,
        "inputHash": snapshot.input_hash,
        "outputHash": snapshot.output_hash,
        "policyVersions": json.loads(snapshot.policy_versions_json),
        "lineage": (
            {
                column.name: getattr(lineage, column.name)
                for column in AnalysisV3RunLineage.__table__.columns
            }
            if lineage
            else None
        ),
        "normalizedFacts": json.loads(snapshot.normalized_facts_json),
        "gaps": [json.loads(item.payload_json) for item in gaps],
        "signals": [
            {
                "stableKey": item.stable_key,
                "type": item.signal_type,
                "subject": {
                    "subjectType": item.subject_type,
                    "subjectId": item.subject_id,
                    "dimensionKey": item.dimension_key,
                },
                "severity": item.severity,
                "reasonCodes": json.loads(item.reason_codes_json),
                "decisiveFacts": json.loads(item.decisive_facts_json),
                "analyzerPolicyVersion": item.analyzer_policy_version,
                "generatedCutoffAt": item.generated_cutoff_at,
            }
            for item in signals
        ],
        "unknownMarkers": [
            {
                "fieldPath": item.field_path,
                "subjectType": item.subject_type,
                "subjectId": item.subject_id,
                "reasonCode": item.reason_code,
            }
            for item in unknowns
        ],
    }


def run_analysis(
    db: Session,
    *,
    idempotency_key: str,
    purpose: str,
    cutoff_at: int | None = None,
    replay_of_run_id: str | None = None,
    update_current: bool = True,
    normalization_schema_version: str = NORMALIZATION_SCHEMA_VERSION,
) -> AnalysisSnapshot:
    existing = db.scalar(select(AnalysisRun).where(AnalysisRun.idempotency_key == idempotency_key))
    if existing is not None:
        requested = json.loads(existing.scope_json)
        if (
            requested.get("purpose") != purpose
            or requested.get("requestedCutoffAt") != cutoff_at
            or requested.get("replayOfRunId") != replay_of_run_id
        ):
            raise AppError(409, "ANALYSIS_IDEMPOTENCY_CONFLICT", "The key has different inputs.")
        snapshot = db.scalar(select(AnalysisSnapshot).where(AnalysisSnapshot.run_id == existing.id))
        if snapshot is None:
            raise AppError(409, "ANALYSIS_RUN_FAILED", "The prior Analysis run has no snapshot.")
        return snapshot
    generated_at = utc_now_ms()
    cutoff = cutoff_at if cutoff_at is not None else generated_at + 1
    facts, unknowns, lineage_payload = build_analysis_inputs(
        db,
        cutoff,
        purpose,
        normalization_schema_version=normalization_schema_version,
    )
    computation = analyze_normalized_facts(facts, unknowns, generated_cutoff_at=cutoff)
    input_hash = content_hash(lineage_payload)
    policy_bundle = analysis_policy_bundle(normalization_schema_version)
    policy_bundle_hash = content_hash(policy_bundle)
    fact_payloads = [asdict(item) for item in computation.facts]
    gap_payloads = [asdict(item) for item in computation.gaps]
    signal_payloads = [asdict(item) for item in computation.signals]
    unknown_payloads = [asdict(item) for item in computation.unknown_markers]
    semantic_references = sorted(
        {
            item.payload["semanticDefinitionId"]
            for item in computation.facts
            if item.fact_type == "target_state" and item.payload["semanticDefinitionId"] is not None
        }
    )
    scale_references = sorted(
        {
            item.payload["scaleVersionId"]
            for item in computation.facts
            if item.fact_type == "target_state"
        }
    )
    envelope_references = {
        "targetProfileId": (lineage_payload["profile"] or {}).get("profile_id"),
        "targetProfileVersionId": (lineage_payload["profile"] or {}).get("profile_version_id"),
        "learningGraphReference": (lineage_payload["graph"] or {}).get("learning_graph_version_id"),
        "curriculumReference": content_hash(lineage_payload["curriculumCatalog"]),
        "semanticDefinitionReferences": semantic_references,
        "capabilityScaleVersionReferences": scale_references,
    }
    hash_projection = {
        "purpose": purpose,
        "cutoffAt": cutoff,
        "inputHash": input_hash,
        "policyBundle": policy_bundle,
        "facts": fact_payloads,
        "gaps": gap_payloads,
        "signals": signal_payloads,
        "unknownMarkers": unknown_payloads,
        "completeness": computation.completeness,
        "envelopeReferences": envelope_references,
    }
    output_hash = content_hash(hash_projection)
    source_generation = _source_generation(db)
    run = AnalysisRun(
        idempotency_key=idempotency_key,
        purpose=purpose,
        scope_json=canonical_json(
            {
                "purpose": purpose,
                "scopeKey": SCOPE_KEY,
                "requestedCutoffAt": cutoff_at,
                "replayOfRunId": replay_of_run_id,
            }
        ),
        generated_at=generated_at,
        cutoff_at=cutoff,
        status="partial" if computation.completeness == "partial" else "completed",
        algorithm_version=ANALYSIS_ALGORITHM_VERSION,
        configuration_reference=(
            lineage_payload["disciplineConfiguration"]["event_id"]
            if lineage_payload["disciplineConfiguration"]
            else "missing"
        ),
        configuration_hash=(
            lineage_payload["disciplineConfiguration"]["configuration_hash"]
            if lineage_payload["disciplineConfiguration"]
            else content_hash({"missing": True})
        ),
        input_lineage_json=canonical_json(lineage_payload),
        input_hash=input_hash,
        application_version=APPLICATION_VERSION,
        failure_metadata_json=None,
        completeness_metadata_json=canonical_json(
            {"status": computation.completeness, "unknownMarkers": unknown_payloads}
        ),
    )
    db.add(run)
    db.flush()
    db.add(
        AnalysisV3RunLineage(
            run_id=run.id,
            analysis_algorithm_version=ANALYSIS_ALGORITHM_VERSION,
            analysis_policy_version=ANALYSIS_POLICY_VERSION,
            normalization_schema_version=normalization_schema_version,
            analyzer_bundle_json=canonical_json(policy_bundle),
            policy_bundle_hash=policy_bundle_hash,
            source_generation=source_generation,
            replay_of_run_id=replay_of_run_id,
        )
    )
    snapshot = AnalysisSnapshot(
        run_id=run.id,
        purpose=purpose,
        schema_version=3,
        generated_at=generated_at,
        cutoff_at=cutoff,
        cutoff_semantics="exclusive",
        timezone=lineage_payload["timezone"],
        completed_through_date=lineage_payload["completedThroughDate"],
        target_profile_id=envelope_references["targetProfileId"],
        target_profile_version_id=envelope_references["targetProfileVersionId"],
        capability_scale_version_references_json=canonical_json(scale_references),
        learning_graph_reference=envelope_references["learningGraphReference"],
        curriculum_reference=envelope_references["curriculumReference"],
        semantic_definition_references_json=canonical_json(semantic_references),
        policy_versions_json=canonical_json(policy_bundle),
        discipline_configuration_reference=run.configuration_reference,
        configuration_hash=run.configuration_hash,
        application_version=APPLICATION_VERSION,
        input_lineage_json=run.input_lineage_json,
        input_hash=input_hash,
        normalized_facts_json=canonical_json(fact_payloads),
        signals_json=canonical_json(signal_payloads),
        completeness=computation.completeness,
        unknown_markers_json=canonical_json(unknown_payloads),
        output_hash=output_hash,
    )
    db.add(snapshot)
    db.flush()
    for ordinal, fact in enumerate(computation.facts):
        db.add(
            AnalysisV3NormalizedFact(
                id=_stable_id(snapshot.id, f"fact:{fact.stable_key}"),
                snapshot_id=snapshot.id,
                ordinal=ordinal,
                stable_key=fact.stable_key,
                fact_type=fact.fact_type,
                subject_type=fact.subject_type,
                subject_id=fact.subject_id,
                payload_json=canonical_json(fact.payload),
            )
        )
    for ordinal, gap in enumerate(computation.gaps):
        db.add(
            AnalysisV3CompetencyGap(
                id=_stable_id(snapshot.id, f"gap:{gap.stable_key}"),
                snapshot_id=snapshot.id,
                ordinal=ordinal,
                stable_key=gap.stable_key,
                competency_identity_id=gap.competency_identity_id,
                dimension_key=gap.dimension_key,
                severity=gap.severity,
                comparison_status=gap.comparison_status,
                payload_json=canonical_json(gap.payload),
                input_lineage_json=canonical_json(gap.input_lineage),
            )
        )
    for ordinal, signal in enumerate(computation.signals):
        db.add(
            AnalysisV3Signal(
                id=_stable_id(snapshot.id, f"signal:{signal.stable_key}"),
                snapshot_id=snapshot.id,
                ordinal=ordinal,
                stable_key=signal.stable_key,
                signal_type=signal.signal_type,
                subject_type=signal.subject_type,
                subject_id=signal.subject_id,
                dimension_key=signal.dimension_key,
                severity=signal.severity,
                reason_codes_json=canonical_json(signal.reason_codes),
                decisive_facts_json=canonical_json(signal.decisive_facts),
                analyzer_policy_version=signal.analyzer_policy_version,
                generated_cutoff_at=signal.generated_cutoff_at,
            )
        )
    for ordinal, marker in enumerate(computation.unknown_markers):
        db.add(
            AnalysisV3UnknownMarker(
                id=_stable_id(snapshot.id, f"unknown:{ordinal}:{marker.reason_code}"),
                snapshot_id=snapshot.id,
                ordinal=ordinal,
                field_path=marker.field_path,
                subject_type=marker.subject_type,
                subject_id=marker.subject_id,
                reason_code=marker.reason_code,
            )
        )
    db.add(
        AnalysisV3SnapshotDetail(
            snapshot_id=snapshot.id,
            purpose_matrix_version=PURPOSE_MATRIX_VERSION,
            facts_hash=content_hash(fact_payloads),
            gaps_hash=content_hash(gap_payloads),
            signals_hash=content_hash(signal_payloads),
            unknowns_hash=content_hash(unknown_payloads),
        )
    )
    publish_current = update_current and cutoff_at is None and replay_of_run_id is None
    if publish_current:
        current = db.get(AnalysisV3CurrentState, (SCOPE_KEY, purpose))
        if current is None:
            current = AnalysisV3CurrentState(scope_key=SCOPE_KEY, purpose=purpose)
            db.add(current)
        current.run_id = run.id
        current.snapshot_id = snapshot.id
        current.status = "current"
        current.exclusive_cutoff_at = cutoff
        current.source_generation = source_generation
        current.input_hash = input_hash
        current.policy_bundle_hash = policy_bundle_hash
        current.updated_at = generated_at
    db.flush()
    return snapshot


def record_failed_analysis_run(
    db: Session,
    *,
    idempotency_key: str,
    purpose: str,
    cutoff_at: int | None,
    replay_of_run_id: str | None,
    error: Exception,
) -> None:
    if db.scalar(select(AnalysisRun.id).where(AnalysisRun.idempotency_key == idempotency_key)):
        return
    generated_at = utc_now_ms()
    cutoff = cutoff_at if cutoff_at is not None else generated_at + 1
    configuration = discipline_configuration_as_of(db, exclusive_cutoff_at=cutoff)
    failure_lineage = {
        "purpose": purpose,
        "scopeKey": SCOPE_KEY,
        "requestedCutoffAt": cutoff_at,
        "replayOfRunId": replay_of_run_id,
    }
    policy_bundle = analysis_policy_bundle()
    run = AnalysisRun(
        idempotency_key=idempotency_key,
        purpose=purpose,
        scope_json=canonical_json(failure_lineage),
        generated_at=generated_at,
        cutoff_at=cutoff,
        status="failed",
        algorithm_version=ANALYSIS_ALGORITHM_VERSION,
        configuration_reference=configuration.event_id if configuration else "missing",
        configuration_hash=(
            configuration.configuration_hash if configuration else content_hash({"missing": True})
        ),
        input_lineage_json=canonical_json(failure_lineage),
        input_hash=content_hash(failure_lineage),
        application_version=APPLICATION_VERSION,
        failure_metadata_json=canonical_json(
            {
                "code": error.code if isinstance(error, AppError) else "ANALYSIS_RUN_FAILED",
                "type": type(error).__name__,
            }
        ),
        completeness_metadata_json=canonical_json({"status": "failed"}),
    )
    db.add(run)
    db.flush()
    db.add(
        AnalysisV3RunLineage(
            run_id=run.id,
            analysis_algorithm_version=ANALYSIS_ALGORITHM_VERSION,
            analysis_policy_version=ANALYSIS_POLICY_VERSION,
            normalization_schema_version=NORMALIZATION_SCHEMA_VERSION,
            analyzer_bundle_json=canonical_json(policy_bundle),
            policy_bundle_hash=content_hash(policy_bundle),
            source_generation=_source_generation(db),
            replay_of_run_id=replay_of_run_id,
        )
    )
    db.flush()


def replay_analysis(db: Session, *, run_id: str, idempotency_key: str) -> AnalysisSnapshot:
    original = db.get(AnalysisRun, run_id)
    lineage = db.get(AnalysisV3RunLineage, run_id)
    if original is None or lineage is None:
        raise AppError(404, "ANALYSIS_V3_RUN_NOT_FOUND", "The Analysis V3 run does not exist.")
    if (
        lineage.analysis_algorithm_version != ANALYSIS_ALGORITHM_VERSION
        or lineage.analysis_policy_version != ANALYSIS_POLICY_VERSION
        or lineage.normalization_schema_version
        not in {LEGACY_NORMALIZATION_SCHEMA_VERSION, NORMALIZATION_SCHEMA_VERSION}
    ):
        raise AppError(
            409,
            "ANALYSIS_POLICY_UNAVAILABLE",
            "The exact historical Analysis policy implementation is unavailable.",
        )
    original_snapshot = db.scalar(
        select(AnalysisSnapshot).where(AnalysisSnapshot.run_id == original.id)
    )
    if original_snapshot is None:
        raise AppError(
            409,
            "ANALYSIS_REPLAY_UNAVAILABLE",
            "A failed Analysis run has no immutable snapshot to replay.",
        )
    replayed = run_analysis(
        db,
        idempotency_key=idempotency_key,
        purpose=original.purpose,
        cutoff_at=original.cutoff_at,
        replay_of_run_id=original.id,
        update_current=False,
        normalization_schema_version=lineage.normalization_schema_version,
    )
    if (
        replayed.input_hash != original_snapshot.input_hash
        or replayed.output_hash != original_snapshot.output_hash
    ):
        raise AppError(
            409,
            "ANALYSIS_REPLAY_MISMATCH",
            "Historical Analysis replay did not reproduce the immutable snapshot.",
        )
    return replayed


def current_analysis(db: Session, *, purpose: str = "learning_control") -> dict[str, Any]:
    current = db.get(AnalysisV3CurrentState, (SCOPE_KEY, purpose))
    if current is None:
        return {"configured": False, "status": "missing", "snapshot": None}
    current_generation = _source_generation(db)
    expected_policy_hash = content_hash(analysis_policy_bundle())
    valid = (
        current.source_generation == current_generation
        and current.policy_bundle_hash == expected_policy_hash
        and current.status == "current"
    )
    snapshot = db.get(AnalysisSnapshot, current.snapshot_id)
    assert snapshot is not None
    expected_completed_through = (
        datetime.fromtimestamp(utc_now_ms() / 1000, tz=ZoneInfo(snapshot.timezone)).date()
        - timedelta(days=1)
    ).isoformat()
    valid = valid and (
        current.purpose == purpose
        and snapshot.purpose == purpose
        and snapshot.completed_through_date == expected_completed_through
    )
    status = "current" if valid else current.status if current.status != "current" else "stale"
    return {
        "configured": True,
        "status": status,
        "validity": {
            "sourceGeneration": current.source_generation,
            "currentSourceGeneration": current_generation,
            "inputHash": current.input_hash,
            "policyBundleHash": current.policy_bundle_hash,
            "expectedCompletedThroughDate": expected_completed_through,
        },
        "snapshot": _serialize_snapshot(db, snapshot),
    }


def initialize_analysis_v3(db: Session) -> None:
    now = utc_now_ms()
    db.execute(
        update(ProjectionInvalidation)
        .where(
            ProjectionInvalidation.projection_kind == "analysis",
            ProjectionInvalidation.target_policy_version == "analysis-policy/v1",
            ProjectionInvalidation.status.in_(["pending", "running"]),
        )
        .values(
            status="superseded_no_handler",
            completed_at=now,
            error_json=canonical_json({"reason": "ANALYSIS_V1_NO_RECOMPUTE_HANDLER"}),
        )
    )
    exists = db.scalar(
        select(ProjectionInvalidation.id).where(
            ProjectionInvalidation.projection_kind == "analysis",
            ProjectionInvalidation.target_policy_version == ANALYSIS_POLICY_VERSION,
        )
    )
    if exists is None:
        db.add(
            ProjectionInvalidation(
                projection_kind="analysis",
                subject_type="analysis_scope",
                subject_id=SCOPE_KEY,
                source_fact_id="analysis-v3-bootstrap",
                target_policy_version=ANALYSIS_POLICY_VERSION,
                status="pending",
                attempt_count=0,
                requested_at=now,
            )
        )
        db.flush()
    normalization_upgrade_source = f"analysis-normalization-upgrade:{NORMALIZATION_SCHEMA_VERSION}"
    has_legacy_current = db.scalar(
        select(AnalysisV3CurrentState.scope_key)
        .join(
            AnalysisV3RunLineage,
            AnalysisV3RunLineage.run_id == AnalysisV3CurrentState.run_id,
        )
        .where(
            AnalysisV3RunLineage.normalization_schema_version == LEGACY_NORMALIZATION_SCHEMA_VERSION
        )
        .limit(1)
    )
    upgrade_exists = db.scalar(
        select(ProjectionInvalidation.id).where(
            ProjectionInvalidation.projection_kind == "analysis",
            ProjectionInvalidation.source_fact_id == normalization_upgrade_source,
        )
    )
    if has_legacy_current is not None and upgrade_exists is None:
        db.add(
            ProjectionInvalidation(
                projection_kind="analysis",
                subject_type="analysis_scope",
                subject_id=SCOPE_KEY,
                source_fact_id=normalization_upgrade_source,
                target_policy_version=ANALYSIS_POLICY_VERSION,
                status="pending",
                attempt_count=0,
                requested_at=now,
            )
        )
    db.commit()


def drain_analysis_invalidations(db: Session, *, recover_running: bool = False) -> int:
    statuses = ["pending", "running"] if recover_running else ["pending"]
    rows = db.scalars(
        select(ProjectionInvalidation)
        .where(
            ProjectionInvalidation.projection_kind == "analysis",
            ProjectionInvalidation.target_policy_version == ANALYSIS_POLICY_VERSION,
            ProjectionInvalidation.status.in_(statuses),
        )
        .order_by(ProjectionInvalidation.requested_at, ProjectionInvalidation.id)
    ).all()
    if not rows:
        return 0
    for row in rows:
        row.status = "running"
        row.attempt_count += 1
        row.started_at = utc_now_ms()
    current_purposes = set(
        db.scalars(
            select(AnalysisV3CurrentState.purpose).where(
                AnalysisV3CurrentState.scope_key == SCOPE_KEY
            )
        ).all()
    )
    purposes = sorted({"learning_control", *current_purposes})
    for purpose in purposes:
        current = db.get(AnalysisV3CurrentState, (SCOPE_KEY, purpose))
        if current is not None:
            current.status = "pending"
            current.updated_at = utc_now_ms()
    db.commit()
    key = f"analysis-v3-invalidation-{content_hash([(row.id, row.attempt_count) for row in rows])}"
    try:
        snapshots = {
            purpose: run_analysis(
                db,
                idempotency_key=f"{key}-{purpose}",
                purpose=purpose,
            )
            for purpose in purposes
        }
        now = utc_now_ms()
        for row in rows:
            scoped_purpose = (
                row.subject_id.removeprefix(f"{SCOPE_KEY}:")
                if row.subject_type == "analysis_scope"
                and row.subject_id.startswith(f"{SCOPE_KEY}:")
                else "learning_control"
            )
            row.status = "completed"
            row.completed_at = now
            row.result_run_id = snapshots.get(scoped_purpose, snapshots["learning_control"]).run_id
            row.error_json = None
        db.commit()
        return len(rows)
    except Exception as exc:
        db.rollback()
        record_failed_analysis_run(
            db,
            idempotency_key=key,
            purpose="learning_control",
            cutoff_at=None,
            replay_of_run_id=None,
            error=exc,
        )
        db.commit()
        for row_id in [row.id for row in rows]:
            failed_row = db.get(ProjectionInvalidation, row_id)
            assert failed_row is not None
            failed_row.status = "permanent_failure" if failed_row.attempt_count >= 3 else "pending"
            failed_row.error_json = canonical_json(
                {"type": type(exc).__name__, "message": str(exc)[:1000]}
            )
        for purpose in purposes:
            current = db.get(AnalysisV3CurrentState, (SCOPE_KEY, purpose))
            if current is not None:
                current.status = "failed"
                current.updated_at = utc_now_ms()
        db.commit()
        return 0


def snapshot_detail(db: Session, snapshot_id: str) -> dict[str, Any]:
    snapshot = db.get(AnalysisSnapshot, snapshot_id)
    if snapshot is None or db.get(AnalysisV3SnapshotDetail, snapshot_id) is None:
        raise AppError(404, "ANALYSIS_V3_SNAPSHOT_NOT_FOUND", "The snapshot does not exist.")
    return _serialize_snapshot(db, snapshot)


def history(db: Session) -> list[dict[str, Any]]:
    snapshots = db.scalars(
        select(AnalysisSnapshot)
        .join(AnalysisV3SnapshotDetail, AnalysisV3SnapshotDetail.snapshot_id == AnalysisSnapshot.id)
        .order_by(AnalysisSnapshot.generated_at.desc(), AnalysisSnapshot.id.desc())
    ).all()
    return [
        {
            "id": item.id,
            "runId": item.run_id,
            "purpose": item.purpose,
            "generatedAt": item.generated_at,
            "cutoffAt": item.cutoff_at,
            "completeness": item.completeness,
            "inputHash": item.input_hash,
            "outputHash": item.output_hash,
        }
        for item in snapshots
    ]


def cutoff_from_request(value: datetime | None) -> int | None:
    return datetime_to_epoch_ms(value) if value else None
