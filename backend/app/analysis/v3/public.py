from __future__ import annotations

import json
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.v3.contracts import (
    PublicAllocationFactDTO,
    PublicAnalysisFactDTO,
    PublicAnalysisGapDTO,
    PublicAnalysisSignalDTO,
    PublicAnalysisSignalFactsDTO,
    PublicAnalysisSnapshotDTO,
    PublicCompletedWeekDTO,
    PublicCriterionEvaluationFactDTO,
    PublicCurriculumCatalogFactDTO,
    PublicDisciplineVarianceFactDTO,
    PublicPrerequisiteFactDTO,
    PublicProjectTaskStateFactDTO,
    PublicReadinessGateFactDTO,
    PublicReadinessPredicateFactDTO,
    PublicTargetStateFactDTO,
    PublicWorkloadRiskFactDTO,
    UnknownMarkerDTO,
    freeze_json,
)
from app.analysis.v3.models import (
    AnalysisV3CompetencyGap,
    AnalysisV3NormalizedFact,
    AnalysisV3Signal,
    AnalysisV3SnapshotDetail,
    AnalysisV3UnknownMarker,
)
from app.errors import AppError
from app.models import AnalysisSnapshot


def _readiness_gate(payload: dict[str, Any]) -> PublicReadinessGateFactDTO:
    return PublicReadinessGateFactDTO(
        gate_id=str(payload["gateId"]),
        state=cast(Any, payload["state"]),
        effect=cast(Any, payload["effect"]),
        deadline_status=cast(Any, payload["deadlineStatus"]),
        deadline_date=payload["deadlineDate"],
        predicates=tuple(
            PublicReadinessPredicateFactDTO(
                predicate_id=str(predicate["predicate_id"]),
                predicate_type=cast(Any, predicate["predicate_type"]),
                requirement_type=cast(Any, predicate["requirement_type"]),
                state=cast(Any, predicate["state"]),
                reason_code=str(predicate["reason_code"]),
                subject=freeze_json(predicate["subject"]),
            )
            for predicate in payload["predicates"]
        ),
    )


def _prerequisite(payload: dict[str, Any]) -> PublicPrerequisiteFactDTO:
    return PublicPrerequisiteFactDTO(
        edge_id=str(payload["edgeId"]),
        aggregate_state=cast(Any, payload["aggregateState"]),
        eligibility_satisfied=bool(payload["eligibilitySatisfied"]),
        unknown_reasons=tuple(payload["unknownReasons"]),
    )


def _typed_signal_facts(
    signal_type: str, payload: dict[str, Any]
) -> PublicAnalysisSignalFactsDTO:
    return PublicAnalysisSignalFactsDTO(
        signal_type=cast(Any, signal_type),
        gap_key=payload.get("gapKey"),
        comparison_status=payload.get("comparisonStatus"),
        deadline_status=payload.get("deadlineStatus"),
        deadline_date=payload.get("deadlineDate"),
        readiness_gate=(
            _readiness_gate(payload) if signal_type == "READINESS_BLOCK" else None
        ),
        prerequisite=(
            _prerequisite(payload) if signal_type == "PREREQUISITE_BLOCK" else None
        ),
        days=payload.get("days"),
        priority=payload.get("priority"),
        freshness=payload.get("freshness"),
        confidence=payload.get("confidence"),
        contradicted_required_criterion_ids=tuple(
            payload.get("contradictedRequiredCriterionIds", ())
        ),
        contradicted_important_criterion_ids=tuple(
            payload.get("contradictedImportantCriterionIds", ())
        ),
        missing_required_criterion_ids=tuple(
            payload.get("missingRequiredCriterionIds", ())
        ),
        missing_independent_criterion_ids=tuple(
            payload.get("missingIndependentCriterionIds", ())
        ),
        important_supporting_missing_criterion_ids=tuple(
            payload.get("importantSupportingMissingCriterionIds", ())
        ),
        supporting_evidence_ids=tuple(payload.get("supportingEvidenceIds", ())),
        contradicting_evidence_ids=tuple(payload.get("contradictingEvidenceIds", ())),
        independent_evidence_ids=tuple(payload.get("independentEvidenceIds", ())),
        exposure_days=payload.get("exposureDays"),
        allocation_duration_ms=payload.get("durationMs"),
        allocation_assigned_total_duration_ms=payload.get("assignedTotalDurationMs"),
        allocation_actual_basis_points=payload.get("actualBasisPoints"),
        allocation_minimum_basis_points=payload.get("minimumBasisPoints"),
        allocation_maximum_basis_points=payload.get("maximumBasisPoints"),
        allocation_miss_basis_points=payload.get("missBasisPoints"),
        allocation_sufficient_data=payload.get("sufficientData"),
        discipline_reason_code=(
            payload.get("reasonCode") if signal_type == "DISCIPLINE_VARIANCE" else None
        ),
        discipline_severity=(
            payload.get("severity") if signal_type == "DISCIPLINE_VARIANCE" else None
        ),
        discipline_completed_weeks=(
            tuple(
                PublicCompletedWeekDTO(
                    str(item["weekStart"]), str(item["weekEnd"]), int(item["activeDays"])
                )
                for item in payload.get("weeks", ())
            )
            if signal_type == "DISCIPLINE_VARIANCE"
            else ()
        ),
        discipline_target_active_days=(
            payload.get("targetActiveDays")
            if signal_type == "DISCIPLINE_VARIANCE"
            else None
        ),
        workload_reason_code=(
            payload.get("reasonCode") if signal_type == "WORKLOAD_RISK" else None
        ),
        workload_severity=(
            payload.get("severity") if signal_type == "WORKLOAD_RISK" else None
        ),
        workload_target_duration_ms=(
            payload.get("targetDurationMs") if signal_type == "WORKLOAD_RISK" else None
        ),
        workload_active_day_durations_ms=(
            tuple(payload.get("activeDayDurationsMs", ()))
            if signal_type == "WORKLOAD_RISK"
            else ()
        ),
        workload_median_duration_ms=(
            payload.get("medianDurationMs") if signal_type == "WORKLOAD_RISK" else None
        ),
        workload_median_basis_points_of_target=(
            payload.get("medianBasisPointsOfTarget")
            if signal_type == "WORKLOAD_RISK"
            else None
        ),
        workload_current_seven_day_total_duration_ms=(
            payload.get("currentSevenDayTotalDurationMs")
            if signal_type == "WORKLOAD_RISK"
            else None
        ),
        workload_preceding_twenty_one_day_total_duration_ms=(
            payload.get("precedingTwentyOneDayTotalDurationMs")
            if signal_type == "WORKLOAD_RISK"
            else None
        ),
        workload_preceding_weekly_equivalent_duration_ms=(
            payload.get("precedingWeeklyEquivalentDurationMs")
            if signal_type == "WORKLOAD_RISK"
            else None
        ),
        workload_surge_basis_points=(
            payload.get("surgeBasisPoints") if signal_type == "WORKLOAD_RISK" else None
        ),
        surge_comparison_status=payload.get("surgeComparisonStatus"),
    )


def _target_fact(stable_key: str, payload: dict[str, Any]) -> PublicTargetStateFactDTO:
    frozen = freeze_json(payload)
    criteria = tuple(
        PublicCriterionEvaluationFactDTO(
            criterion_definition_id=str(item["criterion_definition_id"]),
            criterion_identity_id=str(item["criterion_identity_id"]),
            requirement_type=cast(Any, item["requirement_type"]),
            level_id=str(item["level_id"]),
            state=cast(Any, item["state"]),
            demonstration_rule=str(item["demonstration_rule"]),
            decisive_evidence_ids=tuple(item["decisive_evidence_ids"]),
            evaluation_facts=freeze_json(item["evaluation_facts"]),
        )
        for item in payload["criterionEvaluationFacts"]
    )
    gates = tuple(_readiness_gate(gate) for gate in payload["readinessGates"])
    prerequisites = tuple(_prerequisite(edge) for edge in payload["prerequisites"])
    return PublicTargetStateFactDTO(
        stable_key=stable_key,
        fact_type="target_state",
        target_identity_id=str(payload["targetIdentityId"]),
        competency_identity_id=str(payload["competencyIdentityId"]),
        dimension_key=(
            str(payload["dimensionKey"]) if payload["dimensionKey"] is not None else None
        ),
        profile_target_id=str(payload["profileTargetId"]),
        semantic_definition_id=(
            str(payload["semanticDefinitionId"])
            if payload["semanticDefinitionId"] is not None
            else None
        ),
        scale_version_id=str(payload["scaleVersionId"]),
        target_level_id=str(payload["targetLevelId"]),
        target_level_ordinal=int(payload["targetLevelOrdinal"]),
        current_level_id=(
            str(payload["currentLevelId"]) if payload["currentLevelId"] is not None else None
        ),
        current_level_ordinal=(
            int(payload["currentLevelOrdinal"])
            if payload["currentLevelOrdinal"] is not None
            else None
        ),
        assessment_status=cast(Any, payload["assessmentStatus"]),
        priority=cast(Any, payload["priority"]),
        comparison_status=cast(Any, payload["comparisonStatus"]),
        confidence=cast(Any, payload["confidence"]),
        freshness=cast(Any, payload["freshness"]),
        review_due=(payload["reviewDue"] if isinstance(payload["reviewDue"], bool) else None),
        deadline_status=cast(Any, payload["deadlineStatus"]),
        deadline_date=payload["deadlineDate"],
        unmet_required_criterion_ids=tuple(payload["unmetRequiredCriterionIds"]),
        partial_required_criterion_ids=tuple(payload["partialRequiredCriterionIds"]),
        contradicted_required_criterion_ids=tuple(payload["contradictedRequiredCriterionIds"]),
        contradicted_important_criterion_ids=tuple(payload["contradictedImportantCriterionIds"]),
        open_lesser_criterion_ids=tuple(payload["openLesserCriterionIds"]),
        missing_evidence_requirement_ids=tuple(payload["missingEvidenceRequirements"]),
        missing_independent_criterion_ids=tuple(payload["missingIndependentCriterionIds"]),
        important_supporting_missing_criterion_ids=tuple(
            payload["importantSupportingMissingCriterionIds"]
        ),
        supporting_evidence_ids=tuple(payload["supportingEvidenceIds"]),
        contradicting_evidence_ids=tuple(payload["contradictingEvidenceIds"]),
        independent_evidence_ids=tuple(payload["independentEvidenceIds"]),
        criterion_evaluations=criteria,
        readiness_gates=gates,
        prerequisites=prerequisites,
        days_since_meaningful_activity=int(payload["daysSinceMeaningfulActivity"]),
        last_meaningful_activity_at=payload["lastMeaningfulActivityAt"],
        exposure_days_42=int(payload["exposureDays42"]),
        stall_eligible=bool(payload["stallEligible"]),
        days_since_positive_transition=payload["daysSincePositiveTransition"],
        critical_gate_due=bool(payload["criticalGateDue"]),
        input_lineage=freeze_json(payload["inputLineage"]),
        payload=frozen,
    )


def _typed_fact(item: AnalysisV3NormalizedFact) -> PublicAnalysisFactDTO:
    payload = json.loads(item.payload_json)
    frozen = freeze_json(payload)
    if item.fact_type == "target_state":
        return _target_fact(item.stable_key, payload)
    if item.fact_type == "allocation":
        return PublicAllocationFactDTO(
            item.stable_key,
            "allocation",
            item.subject_id,
            int(payload["durationMs"]),
            int(payload["assignedTotalDurationMs"]),
            int(payload["actualBasisPoints"]) if payload["actualBasisPoints"] is not None else None,
            int(payload["minimumBasisPoints"])
            if payload["minimumBasisPoints"] is not None
            else None,
            int(payload["maximumBasisPoints"])
            if payload["maximumBasisPoints"] is not None
            else None,
            int(payload["missBasisPoints"]),
            bool(payload["sufficientData"]),
            frozen,
        )
    if item.fact_type == "curriculum_catalog":
        return PublicCurriculumCatalogFactDTO(
            item.stable_key,
            "curriculum_catalog",
            freeze_json(payload["activeVersionReferences"]),
            int(payload["unitCount"]),
            int(payload["assessmentRubricCount"]),
            str(payload["inputHash"]),
            frozen,
        )
    if item.fact_type == "project_task_state":
        return PublicProjectTaskStateFactDTO(
            item.stable_key,
            "project_task_state",
            item.subject_id,
            str(payload["projectId"]),
            str(payload["projectVersionId"]),
            str(payload["availabilityState"]),
            str(payload["readinessState"]),
            str(payload["candidateUsabilityState"]),
            str(payload["inputHash"]),
            frozen,
        )
    if item.fact_type == "discipline_variance":
        return PublicDisciplineVarianceFactDTO(
            item.stable_key,
            "discipline_variance",
            str(payload["severity"]) if payload["severity"] is not None else None,
            str(payload["reasonCode"]),
            frozen,
        )
    if item.fact_type == "workload_risk":
        return PublicWorkloadRiskFactDTO(
            item.stable_key,
            "workload_risk",
            str(payload["severity"]) if payload["severity"] is not None else None,
            str(payload["reasonCode"]),
            str(payload["surgeComparisonStatus"]),
            frozen,
        )
    raise AppError(500, "ANALYSIS_V3_FACT_INVALID", "The snapshot has an unknown fact type.")


def load_public_analysis_snapshot(db: Session, snapshot_id: str) -> PublicAnalysisSnapshotDTO:
    """Load the stable deep-immutable Analysis contract consumed by downstream contexts."""
    snapshot = db.get(AnalysisSnapshot, snapshot_id)
    if snapshot is None or db.get(AnalysisV3SnapshotDetail, snapshot_id) is None:
        raise AppError(404, "ANALYSIS_V3_SNAPSHOT_NOT_FOUND", "The snapshot does not exist.")
    facts = db.scalars(
        select(AnalysisV3NormalizedFact)
        .where(AnalysisV3NormalizedFact.snapshot_id == snapshot_id)
        .order_by(AnalysisV3NormalizedFact.ordinal)
    ).all()
    gaps = db.scalars(
        select(AnalysisV3CompetencyGap)
        .where(AnalysisV3CompetencyGap.snapshot_id == snapshot_id)
        .order_by(AnalysisV3CompetencyGap.ordinal)
    ).all()
    signals = db.scalars(
        select(AnalysisV3Signal)
        .where(AnalysisV3Signal.snapshot_id == snapshot_id)
        .order_by(AnalysisV3Signal.ordinal)
    ).all()
    unknowns = db.scalars(
        select(AnalysisV3UnknownMarker)
        .where(AnalysisV3UnknownMarker.snapshot_id == snapshot_id)
        .order_by(AnalysisV3UnknownMarker.ordinal)
    ).all()
    return PublicAnalysisSnapshotDTO(
        snapshot.id,
        snapshot.run_id,
        snapshot.purpose,
        snapshot.schema_version,
        snapshot.generated_at,
        snapshot.cutoff_at,
        snapshot.cutoff_semantics,
        snapshot.timezone,
        snapshot.completed_through_date,
        snapshot.completeness,
        snapshot.input_hash,
        snapshot.output_hash,
        freeze_json(json.loads(snapshot.policy_versions_json)),
        snapshot.target_profile_id,
        snapshot.target_profile_version_id,
        snapshot.learning_graph_reference,
        snapshot.curriculum_reference,
        tuple(json.loads(snapshot.semantic_definition_references_json)),
        tuple(json.loads(snapshot.capability_scale_version_references_json)),
        snapshot.discipline_configuration_reference,
        snapshot.configuration_hash,
        snapshot.application_version,
        freeze_json(json.loads(snapshot.input_lineage_json)),
        tuple(_typed_fact(item) for item in facts),
        tuple(
            PublicAnalysisGapDTO(
                item.stable_key,
                item.competency_identity_id,
                item.dimension_key,
                cast(Any, item.severity),
                cast(Any, item.comparison_status),
                tuple(json.loads(item.payload_json)["reasonCodes"]),
                str(json.loads(item.payload_json)["gapPolicyVersion"]),
                _target_fact(
                    f"target|{json.loads(item.payload_json)['targetIdentityId']}",
                    json.loads(item.payload_json),
                ),
                freeze_json(json.loads(item.payload_json)),
                freeze_json(json.loads(item.input_lineage_json)),
            )
            for item in gaps
        ),
        tuple(
            PublicAnalysisSignalDTO(
                item.stable_key,
                cast(Any, item.signal_type),
                item.subject_type,
                item.subject_id,
                item.dimension_key,
                cast(Any, item.severity),
                tuple(json.loads(item.reason_codes_json)),
                tuple(sorted(json.loads(item.decisive_facts_json))),
                _typed_signal_facts(
                    item.signal_type, json.loads(item.decisive_facts_json)
                ),
                freeze_json(json.loads(item.decisive_facts_json)),
                item.analyzer_policy_version,
                item.generated_cutoff_at,
            )
            for item in signals
        ),
        tuple(
            UnknownMarkerDTO(
                item.field_path,
                item.subject_type,
                item.subject_id,
                item.reason_code,
            )
            for item in unknowns
        ),
    )
