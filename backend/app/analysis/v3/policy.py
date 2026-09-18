from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.analysis.contracts import content_hash
from app.analysis.v3.contracts import (
    AnalysisComputationDTO,
    AnalysisSignalDTO,
    CompetencyGapDTO,
    NormalizedFactDTO,
    UnknownMarkerDTO,
)
from app.capability_views import capability_policy_bundle
from app.learning_graph.service import GRAPH_SATISFACTION_POLICY
from app.requirements.contracts import READINESS_POLICY_VERSION

ANALYSIS_ALGORITHM_VERSION = "analysis-algorithm/v3.0"
ANALYSIS_POLICY_VERSION = "analysis-policy/v3.0"
NORMALIZATION_SCHEMA_VERSION = "analysis-normalization/v3.0"
PURPOSE_MATRIX_VERSION = "analysis-purpose-matrix/v3.0"
APPLICATION_VERSION = "2.0.0"


def analysis_policy_bundle() -> dict[str, str]:
    """Return the complete immutable producer/upstream policy lineage for V3."""
    return {
        "algorithm": ANALYSIS_ALGORITHM_VERSION,
        "analysis": ANALYSIS_POLICY_VERSION,
        "normalization": NORMALIZATION_SCHEMA_VERSION,
        "purposeMatrix": PURPOSE_MATRIX_VERSION,
        "gap": ANALYSIS_POLICY_VERSION,
        "readiness": READINESS_POLICY_VERSION,
        "learningGraphSatisfaction": GRAPH_SATISFACTION_POLICY,
        **capability_policy_bundle(),
    }


SIGNAL_TYPES = frozenset(
    {
        "CAPABILITY_GAP",
        "NEGLECT",
        "UNDER_ALLOCATION",
        "OVER_ALLOCATION",
        "READINESS_BLOCK",
        "PREREQUISITE_BLOCK",
        "EVIDENCE_WEAKNESS",
        "REVIEW_DUE",
        "LOW_CONFIDENCE",
        "PROGRESSION_STALL",
        "DEADLINE_PRESSURE",
        "MAINTENANCE_DUE",
        "DISCIPLINE_VARIANCE",
        "WORKLOAD_RISK",
    }
)


def _signal(
    signal_type: str,
    subject_type: str,
    subject_id: str,
    severity: str,
    reasons: tuple[str, ...],
    facts: dict[str, Any],
    *,
    generated_cutoff_at: int = 0,
    dimension_key: str | None = None,
) -> AnalysisSignalDTO:
    if signal_type not in SIGNAL_TYPES:
        raise ValueError(f"Unknown Analysis signal type: {signal_type}")
    return AnalysisSignalDTO(
        stable_key="|".join((signal_type, subject_type, subject_id, dimension_key or "overall")),
        signal_type=signal_type,
        subject_type=subject_type,
        subject_id=subject_id,
        dimension_key=dimension_key,
        severity=severity,  # type: ignore[arg-type]
        reason_codes=tuple(sorted(reasons)),
        decisive_facts=facts,
        analyzer_policy_version=ANALYSIS_POLICY_VERSION,
        generated_cutoff_at=generated_cutoff_at,
    )


def _gap(target: dict[str, Any]) -> CompetencyGapDTO:
    critical_gate = any(
        gate["state"] in {"not_met", "unknown"} and gate["deadlineStatus"] in {"due", "overdue"}
        for gate in target["readinessGates"]
        if gate["effect"] == "hard_eligibility"
    )
    comparison = target["comparisonStatus"]
    required_bad = bool(
        target["unmetRequiredCriterionIds"] or target["contradictedRequiredCriterionIds"]
    )
    partial_required = bool(target["partialRequiredCriterionIds"])
    lesser_open = bool(target["openLesserCriterionIds"])
    if comparison in {"unknown", "incomparable"}:
        if critical_gate:
            severity, reasons = "critical", ("CRITICAL_GATE_BLOCKED",)
        elif target["priority"] == "critical" and comparison == "unknown":
            # The explicit Critical-target-unassessed clause is the sole priority exception
            # to the otherwise Unknown-first gap ordering.
            severity, reasons = "high", ("CRITICAL_TARGET_UNASSESSED",)
        else:
            severity, reasons = "unknown", ("CAPABILITY_UNKNOWN",)
    elif critical_gate:
        severity, reasons = "critical", ("CRITICAL_GATE_BLOCKED",)
    elif comparison == "below_target" and required_bad:
        severity, reasons = "high", ("REQUIRED_CAPABILITY_GAP",)
    elif comparison == "below_target":
        severity, reasons = (
            "medium",
            ("PARTIAL_REQUIRED_EVIDENCE" if partial_required else "BELOW_TARGET",),
        )
    elif target["confidence"] in {"low", "unknown"} or target["reviewDue"] is True:
        severity, reasons = "medium", ("AT_TARGET_QUALITY_WEAKNESS",)
    elif lesser_open or target["freshness"] == "aging":
        severity, reasons = "low", ("MAINTENANCE_REMAINS",)
    else:
        severity, reasons = "none", ("TARGET_SATISFIED",)
    payload = {
        **target,
        "severity": severity,
        "reasonCodes": reasons,
        "gapPolicyVersion": ANALYSIS_POLICY_VERSION,
    }
    return CompetencyGapDTO(
        stable_key=f"gap|{target['targetIdentityId']}",
        competency_identity_id=target["competencyIdentityId"],
        dimension_key=target["dimensionKey"],
        severity=severity,  # type: ignore[arg-type]
        comparison_status=comparison,
        payload=payload,
        input_lineage=tuple(target["inputLineage"]),
    )


def analyze_normalized_facts(
    facts: tuple[NormalizedFactDTO, ...],
    initial_unknowns: tuple[UnknownMarkerDTO, ...],
    *,
    generated_cutoff_at: int = 0,
) -> AnalysisComputationDTO:
    target_facts = [item for item in facts if item.fact_type == "target_state"]
    signals: list[AnalysisSignalDTO] = []
    gaps: list[CompetencyGapDTO] = []
    unknowns = list(initial_unknowns)
    for fact in target_facts:
        target = fact.payload
        gap = _gap(target)
        gaps.append(gap)
        competency_id = target["competencyIdentityId"]
        if gap.severity == "unknown":
            unknowns.append(
                UnknownMarkerDTO(
                    f"targets.{target['targetIdentityId']}.capability",
                    "competency",
                    competency_id,
                    "CAPABILITY_UNKNOWN",
                )
            )
        elif gap.severity != "none":
            severity = {
                "low": "info",
                "medium": "attention",
                "high": "high",
                "critical": "critical",
            }[gap.severity]
            signals.append(
                _signal(
                    "CAPABILITY_GAP",
                    "competency",
                    competency_id,
                    severity,
                    tuple(gap.payload["reasonCodes"]),
                    {"gapKey": gap.stable_key, "comparisonStatus": gap.comparison_status},
                    dimension_key=target["dimensionKey"],
                )
            )
        deadline = target["deadlineStatus"]
        if deadline in {"due_soon", "due", "overdue"}:
            signals.append(
                _signal(
                    "DEADLINE_PRESSURE",
                    "profile_target",
                    target["targetIdentityId"],
                    {"due_soon": "attention", "due": "high", "overdue": "critical"}[deadline],
                    (f"DEADLINE_{deadline.upper()}",),
                    {"deadlineStatus": deadline, "deadlineDate": target["deadlineDate"]},
                )
            )
        for gate in target["readinessGates"]:
            for predicate in gate["predicates"]:
                if predicate["requirement_type"] == "required" and predicate["state"] == "unknown":
                    unknowns.append(
                        UnknownMarkerDTO(
                            f"readinessGates.{gate['gateId']}.predicates.{predicate['predicate_id']}",
                            "readiness_predicate",
                            predicate["predicate_id"],
                            predicate["reason_code"],
                        )
                    )
            if gate["effect"] == "hard_eligibility" and gate["state"] in {
                "not_met",
                "unknown",
            }:
                gate_severity = (
                    "critical"
                    if gate["deadlineStatus"] in {"due", "overdue"}
                    else "high"
                    if target["priority"] in {"critical", "core"}
                    else "attention"
                )
                signals.append(
                    _signal(
                        "READINESS_BLOCK",
                        "readiness_gate",
                        gate["gateId"],
                        gate_severity,
                        (
                            "READINESS_GATE_UNKNOWN"
                            if gate["state"] == "unknown"
                            else "READINESS_GATE_NOT_MET",
                        ),
                        gate,
                    )
                )
        for edge in target["prerequisites"]:
            if edge["aggregateState"] == "unknown":
                for reason in edge["unknownReasons"] or ["PREREQUISITE_UNKNOWN"]:
                    unknowns.append(
                        UnknownMarkerDTO(
                            f"learningGraph.edges.{edge['edgeId']}",
                            "learning_graph_edge",
                            edge["edgeId"],
                            reason,
                        )
                    )
            if edge["aggregateState"] in {"not_met", "unknown"}:
                severity = "high" if target["priority"] in {"critical", "core"} else "attention"
                signals.append(
                    _signal(
                        "PREREQUISITE_BLOCK",
                        "learning_graph_edge",
                        edge["edgeId"],
                        severity,
                        tuple(edge["unknownReasons"] or ["PREREQUISITE_NOT_MET"]),
                        edge,
                    )
                )
        days = target["daysSinceMeaningfulActivity"]
        if days is not None:
            priority = target["priority"]
            thresholds = (
                (7, 14, 28)
                if priority in {"critical", "core"}
                else (14, 28, 56)
                if priority == "important"
                else (28, 56, None)
            )
            neglect_severity: str | None = None
            if thresholds[2] is not None and days >= thresholds[2]:
                neglect_severity = "critical"
            elif days >= thresholds[1]:
                neglect_severity = (
                    "high" if priority in {"critical", "core", "important"} else "attention"
                )
            elif days >= thresholds[0]:
                neglect_severity = (
                    "attention" if priority in {"critical", "core", "important"} else "info"
                )
            if neglect_severity:
                signals.append(
                    _signal(
                        "NEGLECT",
                        "competency",
                        competency_id,
                        neglect_severity,
                        ("MEANINGFUL_ACTIVITY_NEGLECTED",),
                        {"days": days, "priority": priority},
                        dimension_key=target["dimensionKey"],
                    )
                )
        if target["reviewDue"] is True:
            review_severity = (
                "high"
                if target["priority"] in {"critical", "core"}
                else "attention"
                if target["priority"] == "important"
                else "info"
            )
            signals.append(
                _signal(
                    "REVIEW_DUE",
                    "competency",
                    competency_id,
                    review_severity,
                    ("REVIEW_DUE",),
                    {"freshness": target["freshness"]},
                    dimension_key=target["dimensionKey"],
                )
            )
        elif target["freshness"] == "aging" and target["priority"] == "critical":
            signals.append(
                _signal(
                    "MAINTENANCE_DUE",
                    "competency",
                    competency_id,
                    "info",
                    ("AGING_CRITICAL_TARGET",),
                    {"freshness": "aging"},
                    dimension_key=target["dimensionKey"],
                )
            )
        if target["confidence"] == "low":
            signals.append(
                _signal(
                    "LOW_CONFIDENCE",
                    "competency",
                    competency_id,
                    "attention",
                    ("CAPABILITY_CONFIDENCE_LOW",),
                    {"confidence": "low"},
                    dimension_key=target["dimensionKey"],
                )
            )
        contradicted_required = target["contradictedRequiredCriterionIds"]
        contradicted_lesser = target["contradictedImportantCriterionIds"]
        missing_required = target["missingEvidenceRequirements"]
        missing_independence = target["missingIndependentCriterionIds"]
        missing_lesser = target["importantSupportingMissingCriterionIds"]
        if (
            contradicted_required
            or contradicted_lesser
            or missing_required
            or missing_independence
            or missing_lesser
        ):
            evidence_severity = (
                "critical"
                if target["criticalGateDue"] and contradicted_required
                else "high"
                if target["priority"] in {"critical", "core"}
                and (contradicted_required or missing_required)
                else "attention"
                if missing_independence
                else "info"
            )
            reason = (
                "REQUIRED_EVIDENCE_CONTRADICTED"
                if contradicted_required
                else "REQUIRED_EVIDENCE_MISSING"
                if missing_required
                else "REQUIRED_INDEPENDENCE_MISSING"
                if missing_independence
                else "IMPORTANT_SUPPORTING_EVIDENCE_WEAK"
            )
            signals.append(
                _signal(
                    "EVIDENCE_WEAKNESS",
                    "competency",
                    competency_id,
                    evidence_severity,
                    (reason,),
                    {
                        "contradictedRequiredCriterionIds": contradicted_required,
                        "contradictedImportantCriterionIds": contradicted_lesser,
                        "missingRequiredCriterionIds": missing_required,
                        "missingIndependentCriterionIds": missing_independence,
                        "importantSupportingMissingCriterionIds": missing_lesser,
                        "supportingEvidenceIds": target["supportingEvidenceIds"],
                        "contradictingEvidenceIds": target["contradictingEvidenceIds"],
                        "independentEvidenceIds": target["independentEvidenceIds"],
                    },
                    dimension_key=target["dimensionKey"],
                )
            )
        if target["stallEligible"] and target["daysSincePositiveTransition"] is not None:
            stall_days = target["daysSincePositiveTransition"]
            stall_severity: str | None = (
                "critical"
                if stall_days >= 168
                else "high"
                if stall_days >= 84
                else "attention"
                if stall_days >= 42
                else None
            )
            if stall_severity:
                signals.append(
                    _signal(
                        "PROGRESSION_STALL",
                        "competency",
                        competency_id,
                        stall_severity,
                        ("PROGRESSION_STALLED",),
                        {"days": stall_days, "exposureDays": target["exposureDays42"]},
                        dimension_key=target["dimensionKey"],
                    )
                )

    for fact in facts:
        payload = fact.payload
        if fact.fact_type == "allocation":
            if not payload["sufficientData"]:
                unknowns.append(
                    UnknownMarkerDTO(
                        f"domains.{fact.subject_id}.allocation",
                        "profile_domain",
                        fact.subject_id,
                        "ALLOCATION_DENOMINATOR_INSUFFICIENT",
                    )
                )
            elif payload["missBasisPoints"]:
                miss = abs(payload["missBasisPoints"])
                severity = (
                    "info"
                    if miss < 500
                    else "attention"
                    if miss < 1500
                    else "high"
                    if miss < 3000
                    else "critical"
                )
                signal_type = (
                    "UNDER_ALLOCATION" if payload["missBasisPoints"] < 0 else "OVER_ALLOCATION"
                )
                signals.append(
                    _signal(
                        signal_type,
                        "profile_domain",
                        fact.subject_id,
                        severity,
                        (signal_type,),
                        payload,
                    )
                )
        elif fact.fact_type == "discipline_variance" and payload["severity"]:
            signals.append(
                _signal(
                    "DISCIPLINE_VARIANCE",
                    "discipline",
                    "global",
                    payload["severity"],
                    (payload["reasonCode"],),
                    payload,
                )
            )
        elif (
            fact.fact_type == "discipline_variance"
            and payload["reasonCode"] == "WEEKLY_TARGET_UNKNOWN"
        ):
            unknowns.append(
                UnknownMarkerDTO(
                    "discipline.weeklyTargetActiveDays",
                    "discipline",
                    "global",
                    "WEEKLY_TARGET_UNKNOWN",
                )
            )
        elif fact.fact_type == "workload_risk" and payload["severity"]:
            signals.append(
                _signal(
                    "WORKLOAD_RISK",
                    "discipline",
                    "global",
                    payload["severity"],
                    (payload["reasonCode"],),
                    payload,
                )
            )
        elif (
            fact.fact_type == "workload_risk"
            and payload["reasonCode"] == "WORKLOAD_INSUFFICIENT_DATA"
        ):
            unknowns.append(
                UnknownMarkerDTO(
                    "discipline.workload",
                    "discipline",
                    "global",
                    "WORKLOAD_INSUFFICIENT_DATA",
                )
            )
        if fact.fact_type == "workload_risk" and payload.get("surgeComparisonStatus") == "unknown":
            unknowns.append(
                UnknownMarkerDTO(
                    "discipline.workload.surgeBasisPoints",
                    "discipline",
                    "global",
                    "WORKLOAD_SURGE_DENOMINATOR_UNKNOWN",
                )
            )
    ordered_gaps = tuple(sorted(gaps, key=lambda item: item.stable_key))
    severity_order = {"info": 0, "attention": 1, "high": 2, "critical": 3}
    signals_by_key: dict[str, AnalysisSignalDTO] = {}
    for item in signals:
        prior = signals_by_key.get(item.stable_key)
        if (
            prior is None
            or severity_order[item.severity] > severity_order[prior.severity]
            or (
                item.severity == prior.severity
                and content_hash(item.decisive_facts) < content_hash(prior.decisive_facts)
            )
        ):
            signals_by_key[item.stable_key] = item
    ordered_signals = tuple(
        replace(item, generated_cutoff_at=generated_cutoff_at)
        for item in sorted(signals_by_key.values(), key=lambda item: item.stable_key)
    )
    ordered_unknowns = tuple(
        sorted(
            {
                (item.field_path, item.subject_type, item.subject_id, item.reason_code): item
                for item in unknowns
            }.values(),
            key=lambda item: (
                item.field_path,
                item.subject_type,
                item.subject_id,
                item.reason_code,
            ),
        )
    )
    return AnalysisComputationDTO(
        facts=tuple(sorted(facts, key=lambda item: item.stable_key)),
        gaps=ordered_gaps,
        signals=ordered_signals,
        unknown_markers=ordered_unknowns,
        completeness="partial" if ordered_unknowns else "complete",
    )
