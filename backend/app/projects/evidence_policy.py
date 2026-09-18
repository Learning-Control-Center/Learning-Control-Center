from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PROJECT_EVIDENCE_POLICY = "project-evidence-policy/v1"


@dataclass(frozen=True)
class ProjectEvidenceCharacteristics:
    strength: Literal["unknown", "weak", "moderate", "strong"]
    strength_unknown_reason: str | None
    independence: Literal["unknown", "guided", "assisted", "independent"]
    independence_unknown_reason: str | None
    source_confidence: Literal["low", "medium"]
    source_confidence_unknown_reason: None


def derive_project_evidence_characteristics(
    *,
    activity_outcome: str | None,
    has_artifact: bool,
    assistance_modes: tuple[str, ...],
    attribution_provenance: str,
    rubric_result: str | None,
    has_project_criterion: bool,
) -> ProjectEvidenceCharacteristics:
    """Apply the closed, conservative Project Evidence v1 policy.

    An artifact reference is not a rubric or reproducibility proof by itself. Strong
    Evidence therefore requires a completed actual Activity, an artifact, and an
    explicit passing result against the opportunity's ProjectCriterion. Attribution
    provenance controls source trust separately and can never produce High confidence.
    """
    if activity_outcome not in {"completed", "partial"}:
        strength: Literal["unknown", "weak", "moderate", "strong"] = "unknown"
        strength_unknown_reason = "activity_outcome_unknown"
    elif rubric_result == "not_met":
        strength = "weak"
        strength_unknown_reason = None
    elif (
        activity_outcome == "completed"
        and has_artifact
        and rubric_result == "passed"
        and has_project_criterion
    ):
        strength = "strong"
        strength_unknown_reason = None
    elif has_artifact and (activity_outcome == "completed" or rubric_result == "partially_met"):
        strength = "moderate"
        strength_unknown_reason = None
    else:
        strength = "weak"
        strength_unknown_reason = None

    assistance_rank = {
        "none": 0,
        "docs_only": 0,
        "ai_hint": 1,
        "ai_assisted": 2,
        "agent_led": 3,
    }
    if assistance_modes:
        worst = max(assistance_modes, key=lambda item: assistance_rank[item])
        independence: Literal["unknown", "guided", "assisted", "independent"]
        if worst in {"none", "docs_only"}:
            independence = "independent"
        elif worst in {"ai_hint", "ai_assisted"}:
            independence = "assisted"
        else:
            independence = "guided"
        independence_unknown_reason = None
    else:
        independence = "unknown"
        independence_unknown_reason = "session_assistance_unknown"

    source_confidence: Literal["low", "medium"] = (
        "medium" if attribution_provenance == "user_confirmed" else "low"
    )
    return ProjectEvidenceCharacteristics(
        strength=strength,
        strength_unknown_reason=strength_unknown_reason,
        independence=independence,
        independence_unknown_reason=independence_unknown_reason,
        source_confidence=source_confidence,
        source_confidence_unknown_reason=None,
    )
