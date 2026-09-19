from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.auth import AuthContext, get_auth_context
from app.database import get_db
from app.errors import AppError
from app.models import (
    ActiveCompetencyDefinitionState,
    ActiveTargetProfileState,
    CapabilityEvaluationRun,
    CapabilityScaleDimension,
    CapabilityScaleLevel,
    CapabilityScaleVersion,
    CapabilityStateEvent,
    CompetencyCapabilityState,
    CompetencyReviewState,
    CriterionDefinition,
    CriterionEvaluationResult,
    CriterionIdentity,
    Evidence,
    EvidenceInvalidation,
    EvidenceLink,
    EvidenceLinkRetraction,
    EvidenceRetraction,
    ProfileTarget,
    ProfileTargetIdentity,
    ProjectionInvalidation,
    ReviewEvent,
    SemanticCompetencyDefinition,
    SemanticDefinitionDimension,
    new_id,
)
from app.roadmap_projection.policies import ACTIVE_PROJECTION_POLICY
from app.settings_api import get_or_create_profile
from app.time_utils import epoch_ms_to_rfc3339, utc_now_ms

router = APIRouter(tags=["v2 capability"])
logger = logging.getLogger(__name__)

CRITERION_POLICY = "criterion-evaluation-policy/v1"
CAPABILITY_POLICY = "capability-policy/v1"
EVIDENCE_POLICY = "evidence-policy/v1"
DOWNGRADE_POLICY = "capability-downgrade-policy/v1"
FRESHNESS_POLICY = "freshness-policy/v1"

_STRENGTH = {"unknown": 0, "weak": 1, "moderate": 2, "strong": 3}
_SOURCE_CONFIDENCE = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
_INDEPENDENCE = {"unknown": 0, "guided": 1, "assisted": 2, "independent": 3}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _evidence_payload(facts: list[EvidenceFact]) -> list[dict[str, Any]]:
    return [
        {
            "evidenceId": item.evidence.id,
            "linkId": item.link.id,
            "criterionDefinitionId": item.link.criterion_definition_id,
            "effect": item.link.effect,
            "strength": item.evidence.strength,
            "independence": item.evidence.independence,
            "sourceConfidence": item.evidence.source_confidence,
            "occurredAt": item.evidence.occurred_at,
            "occurrence": item.occurrence_key,
            "context": item.context_key,
        }
        for item in facts
    ]


def capability_evidence_set_hash(
    db: Session, competency_id: str, dimension_id: str | None, cutoff_at: int
) -> str:
    facts = [
        item
        for item in _active_evidence_facts(db, competency_id, cutoff_at)
        if item.link.dimension_id == dimension_id
    ]
    return _hash(_evidence_payload(facts))


@dataclass(frozen=True)
class EvidenceFact:
    evidence: Evidence
    link: EvidenceLink
    occurrence_key: str
    context_key: str


@dataclass(frozen=True)
class CriterionResultValue:
    definition: CriterionDefinition
    state: str
    decisive_ids: tuple[str, ...]
    support_ids: tuple[str, ...]
    contradiction_ids: tuple[str, ...]
    facts: dict[str, Any]


@dataclass(frozen=True)
class CapabilityPriorState:
    competency_identity_id: str
    scope_key: str
    semantic_definition_id: str
    capability_level_id: str | None
    assessment_status: str
    aggregate_confidence: str
    evaluation_run_id: str
    last_meaningful_evidence_at: int | None


def _scope_key(dimension_id: str | None) -> str:
    return f"dimension:{dimension_id}" if dimension_id else "overall"


def _active_evidence_facts(db: Session, competency_id: str, cutoff_at: int) -> list[EvidenceFact]:
    rows = db.execute(
        select(Evidence, EvidenceLink)
        .join(EvidenceLink, EvidenceLink.evidence_id == Evidence.id)
        .where(
            EvidenceLink.competency_identity_id == competency_id,
            Evidence.created_at <= cutoff_at,
            EvidenceLink.created_at <= cutoff_at,
        )
        .order_by(Evidence.created_at, Evidence.id, EvidenceLink.id)
    ).all()
    result: list[EvidenceFact] = []
    for evidence, link in rows:
        if evidence.occurred_at is not None and evidence.occurred_at > cutoff_at:
            continue
        if db.scalar(
            select(EvidenceRetraction.id).where(
                EvidenceRetraction.evidence_id == evidence.id,
                EvidenceRetraction.created_at <= cutoff_at,
            )
        ) or db.scalar(
            select(EvidenceInvalidation.id).where(
                EvidenceInvalidation.evidence_id == evidence.id,
                EvidenceInvalidation.created_at <= cutoff_at,
            )
        ):
            continue
        if db.scalar(
            select(EvidenceLinkRetraction.id).where(
                EvidenceLinkRetraction.evidence_link_id == link.id,
                EvidenceLinkRetraction.created_at <= cutoff_at,
            )
        ):
            continue
        provenance = json.loads(evidence.provenance_json)
        occurrence_key = (
            f"artifact:{evidence.artifact_hash}"
            if evidence.artifact_hash
            else f"source:{evidence.source_type}:{evidence.source_id}:{evidence.occurred_at}"
        )
        context = provenance.get("context_id") or provenance.get("activity_id")
        context_key = str(context) if context else "legacy_unknown"
        result.append(EvidenceFact(evidence, link, occurrence_key, context_key))
    return result


def _criterion_facts(
    definition: CriterionDefinition, all_facts: list[EvidenceFact]
) -> list[EvidenceFact]:
    return [item for item in all_facts if item.link.criterion_definition_id == definition.id]


def _support_qualifies(
    rule: str,
    facts: list[EvidenceFact],
    definition: CriterionDefinition | None = None,
) -> tuple[bool, set[str]]:
    supports = [item for item in facts if item.link.effect == "supports"]
    weak = [item for item in supports if _STRENGTH[item.evidence.strength] >= 1]
    moderate = [
        item
        for item in supports
        if _STRENGTH[item.evidence.strength] >= 2
        and _SOURCE_CONFIDENCE[item.evidence.source_confidence] >= 2
        and item.evidence.independence in {"guided", "assisted", "independent"}
    ]
    independent_moderate = [
        item
        for item in supports
        if _STRENGTH[item.evidence.strength] >= 2
        and _SOURCE_CONFIDENCE[item.evidence.source_confidence] >= 2
        and item.evidence.independence == "independent"
    ]
    independent_strong = [
        item for item in independent_moderate if item.evidence.strength == "strong"
    ]
    if rule == "exposure":
        return bool(weak), {item.evidence.id for item in weak[:1]}
    if rule == "guided_performance":
        return bool(moderate), {item.evidence.id for item in moderate[:1]}
    if rule == "independent_performance":
        if independent_strong:
            return True, {independent_strong[0].evidence.id}
        occurrences = {item.occurrence_key: item for item in independent_moderate}
        selected = list(occurrences.values())[:2]
        return len(selected) >= 2, {item.evidence.id for item in selected}
    if rule == "repeated_independent_performance":
        strong_occurrences = {item.occurrence_key: item for item in independent_strong}
        strong_contexts = {
            item.context_key
            for item in strong_occurrences.values()
            if item.context_key != "legacy_unknown"
        }
        if len(strong_occurrences) >= 2 and len(strong_contexts) >= 2:
            return True, {item.evidence.id for item in list(strong_occurrences.values())[:2]}
        all_occurrences = {item.occurrence_key: item for item in independent_moderate}
        contexts = {
            item.context_key
            for item in all_occurrences.values()
            if item.context_key != "legacy_unknown"
        }
        if independent_strong and len(all_occurrences) >= 3 and len(contexts) >= 2:
            return True, {item.evidence.id for item in list(all_occurrences.values())[:3]}
        return False, set()
    if rule == "authoritative_assessment":
        qualifying = [
            item
            for item in supports
            if item.evidence.evidence_type in {"assessment", "verification"}
            and item.evidence.strength == "strong"
            and item.evidence.independence == "independent"
            and item.evidence.source_confidence == "high"
            and definition is not None
            and definition.verification_rubric is not None
            and (
                json.loads(item.evidence.provenance_json)
                .get("rubric_references", {})
                .get(definition.id)
                == definition.verification_rubric
                or (
                    json.loads(item.evidence.provenance_json).get("rubric_criterion_definition_id")
                    == definition.id
                    and json.loads(item.evidence.provenance_json).get("rubric_reference")
                    == definition.verification_rubric
                )
            )
        ]
        return bool(qualifying), {item.evidence.id for item in qualifying[:1]}
    raise ValueError(f"Unsupported demonstration rule: {rule}")


def _evaluate_criterion(
    definition: CriterionDefinition, facts: list[EvidenceFact]
) -> CriterionResultValue:
    relevant = _criterion_facts(definition, facts)
    rule = str(json.loads(definition.demonstration_rule_json)["rule"])
    demonstrated, support_ids = _support_qualifies(rule, relevant, definition)
    supports = [item for item in relevant if item.link.effect == "supports"]
    meaningful_supports = [
        item
        for item in supports
        if _STRENGTH[item.evidence.strength] >= 2
        and _SOURCE_CONFIDENCE[item.evidence.source_confidence] >= 2
    ]
    independence_allowed = {
        "guided",
        "assisted",
        "independent",
        "not_applicable",
    }
    if rule in {
        "independent_performance",
        "repeated_independent_performance",
        "authoritative_assessment",
    }:
        independence_allowed = {"independent"}
    independence_compatible_supports = [
        item for item in meaningful_supports if item.evidence.independence in independence_allowed
    ]
    attempts = [
        item
        for item in relevant
        if item.evidence.evidence_type in {"assessment", "verification"}
        and item.link.effect != "context_only"
    ]
    qualifying_contradictions = [
        item
        for item in relevant
        if item.link.effect == "contradicts"
        and _STRENGTH[item.evidence.strength] >= 2
        and _SOURCE_CONFIDENCE[item.evidence.source_confidence] >= 2
    ]
    latest_support_at = max(
        (
            item.evidence.occurred_at
            for item in relevant
            if item.evidence.id in support_ids and item.evidence.occurred_at is not None
        ),
        default=None,
    )
    unresolved = [
        item
        for item in qualifying_contradictions
        if item.evidence.occurred_at is not None
        and (latest_support_at is None or item.evidence.occurred_at > latest_support_at)
    ]
    if demonstrated and unresolved:
        state = "contradicted"
    elif demonstrated:
        state = "demonstrated"
    elif supports:
        state = "partially_demonstrated"
    elif attempts:
        state = "not_demonstrated"
    else:
        state = "unknown"
    contradiction_ids = {item.evidence.id for item in unresolved}
    decisive = support_ids | contradiction_ids
    decisive_link_ids = {
        item.link.id
        for item in relevant
        if item.evidence.id in decisive and item.link.effect in {"supports", "contradicts"}
    }
    return CriterionResultValue(
        definition=definition,
        state=state,
        decisive_ids=tuple(sorted(decisive)),
        support_ids=tuple(sorted(support_ids)),
        contradiction_ids=tuple(sorted(contradiction_ids)),
        facts={
            "rule": rule,
            "supportOccurrenceCount": len({item.occurrence_key for item in supports}),
            "meaningfulSupportOccurrenceCount": len(
                {item.occurrence_key for item in meaningful_supports}
            ),
            "independenceCompatibleSupportOccurrenceCount": len(
                {item.occurrence_key for item in independence_compatible_supports}
            ),
            "qualifyingAttemptCount": len({item.occurrence_key for item in attempts}),
            "unresolvedContradictionIds": sorted(contradiction_ids),
            "decisiveLinkIds": sorted(decisive_link_ids),
        },
    )


def _high_confidence(
    cumulative: list[CriterionResultValue], facts: list[EvidenceFact], current_level_id: str
) -> tuple[bool, list[str]]:
    unmet: list[str] = []
    by_criterion = {item.definition.id: item for item in cumulative}
    for result in cumulative:
        definition = result.definition
        if definition.requirement_type == "important" and result.state != "demonstrated":
            unmet.append(f"important:{definition.id}")
        if definition.requirement_type != "required" or definition.level_id != current_level_id:
            continue
        supports = [
            item
            for item in _criterion_facts(definition, facts)
            if item.link.effect == "supports"
            and item.evidence.independence == "independent"
            and _SOURCE_CONFIDENCE[item.evidence.source_confidence] >= 2
        ]
        strong = [item for item in supports if item.evidence.strength == "strong"]
        moderate_contexts = {
            item.context_key
            for item in supports
            if _STRENGTH[item.evidence.strength] >= 2 and item.context_key != "legacy_unknown"
        }
        if not strong and len(moderate_contexts) < 2:
            unmet.append(f"robust_support:{definition.id}")
    if any(
        item.state == "contradicted"
        and item.definition.requirement_type in {"required", "important"}
        for item in by_criterion.values()
    ):
        unmet.append("unresolved_required_or_important_contradiction")
    return not unmet, sorted(unmet)


def _meaningful_evidence_at(
    facts: list[EvidenceFact],
    criteria: dict[str, CriterionDefinition],
    level: CapabilityScaleLevel | None,
) -> int | None:
    values = []
    for item in facts:
        criterion = criteria.get(item.link.criterion_definition_id or "")
        current_required_rules = {
            str(json.loads(definition.demonstration_rule_json)["rule"])
            for definition in criteria.values()
            if level is not None
            and definition.level_id == level.id
            and definition.requirement_type == "required"
        }
        independence_allowed = {
            "guided",
            "assisted",
            "independent",
            "not_applicable",
        }
        if current_required_rules & {
            "independent_performance",
            "repeated_independent_performance",
            "authoritative_assessment",
        }:
            independence_allowed = {"independent", "not_applicable"}
        if (
            item.link.effect == "supports"
            and item.evidence.occurred_at is not None
            and _STRENGTH[item.evidence.strength] >= 2
            and _SOURCE_CONFIDENCE[item.evidence.source_confidence] >= 2
            and (criterion is None or criterion.requirement_type in {"required", "important"})
            and item.evidence.independence in independence_allowed
        ):
            values.append(item.evidence.occurred_at)
    return max(values, default=None)


def _candidate_level(
    scale: CapabilityScaleVersion,
    levels: list[CapabilityScaleLevel],
    results: list[CriterionResultValue],
    facts: list[EvidenceFact],
) -> tuple[CapabilityScaleLevel | None, list[str]]:
    passed: list[CapabilityScaleLevel] = []
    rank_by_level = {item.id: item.ordinal_rank for item in levels}
    for level in levels:
        direct_required = [
            item
            for item in results
            if item.definition.level_id == level.id
            and item.definition.requirement_type == "required"
        ]
        cumulative_required = [
            item
            for item in results
            if item.definition.requirement_type == "required"
            and rank_by_level[item.definition.level_id] <= level.ordinal_rank
        ]
        if not direct_required or any(item.state != "demonstrated" for item in cumulative_required):
            continue
        if scale.scale_stable_key == "technical" and level.stable_key == "unexposed":
            explicit = any(
                item.link.criterion_definition_id in {r.definition.id for r in direct_required}
                and item.evidence.evidence_type == "assessment"
                and item.link.effect == "supports"
                and json.loads(item.evidence.provenance_json).get("capture_method")
                == "explicit_unexposed_assessment"
                for item in facts
            )
            if not explicit:
                continue
        passed.append(level)
    return (passed[-1] if passed else None), [item.id for item in passed]


def _apply_downgrade_policy(
    db: Session,
    previous: CompetencyCapabilityState | CapabilityPriorState | None,
    candidate: CapabilityScaleLevel | None,
    levels: list[CapabilityScaleLevel],
    results: list[CriterionResultValue],
    facts: list[EvidenceFact],
    cutoff: int,
) -> tuple[CapabilityScaleLevel | None, str | None, list[str], bool]:
    if previous is None or previous.capability_level_id is None:
        return candidate, None, [], False
    prior_level = next(item for item in levels if item.id == previous.capability_level_id)
    if candidate is not None and candidate.ordinal_rank >= prior_level.ordinal_rank:
        last_downgrade = db.scalar(
            select(CapabilityStateEvent)
            .where(
                CapabilityStateEvent.competency_identity_id == previous.competency_identity_id,
                CapabilityStateEvent.scope_key == previous.scope_key,
                CapabilityStateEvent.cause_code == "sustained_contradictory_performance",
                CapabilityStateEvent.created_at <= cutoff,
            )
            .order_by(CapabilityStateEvent.event_sequence.desc())
        )
        if last_downgrade is not None:
            contradiction_ids = json.loads(last_downgrade.decisive_evidence_ids_json)
            contradiction_times = list(
                db.scalars(
                    select(Evidence.occurred_at).where(
                        Evidence.id.in_(contradiction_ids), Evidence.occurred_at.is_not(None)
                    )
                ).all()
            )
            last_contradiction_at = max(
                (value for value in contradiction_times if value is not None), default=None
            )
            has_new_support = any(
                item.link.effect == "supports"
                and item.evidence.occurred_at is not None
                and (
                    last_contradiction_at is None
                    or item.evidence.occurred_at > last_contradiction_at
                )
                for item in facts
            )
            if not has_new_support:
                return prior_level, None, [], True
        return candidate, None, [], False
    prior_run = db.get(CapabilityEvaluationRun, previous.evaluation_run_id)
    prior_decisive = set(json.loads(prior_run.decisive_evidence_ids_json)) if prior_run else set()
    inactive_decisive = {
        evidence_id
        for evidence_id in prior_decisive
        if db.scalar(
            select(EvidenceRetraction.id).where(
                EvidenceRetraction.evidence_id == evidence_id,
                EvidenceRetraction.created_at <= cutoff,
            )
        )
        or db.scalar(
            select(EvidenceInvalidation.id).where(
                EvidenceInvalidation.evidence_id == evidence_id,
                EvidenceInvalidation.created_at <= cutoff,
            )
        )
    }
    prior_results = db.scalars(
        select(CriterionEvaluationResult).where(
            CriterionEvaluationResult.run_id == previous.evaluation_run_id
        )
    ).all()
    prior_decisive_link_ids = {
        link_id
        for result in prior_results
        for link_id in json.loads(result.facts_json).get("decisiveLinkIds", [])
    }
    retracted_decisive_links = set(
        db.scalars(
            select(EvidenceLink.evidence_id)
            .join(
                EvidenceLinkRetraction,
                EvidenceLinkRetraction.evidence_link_id == EvidenceLink.id,
            )
            .where(
                EvidenceLink.id.in_(prior_decisive_link_ids),
                EvidenceLinkRetraction.created_at <= cutoff,
            )
        ).all()
    )
    inactive_decisive |= retracted_decisive_links
    if inactive_decisive:
        return candidate, "evidence_invalidation_correction", sorted(inactive_decisive), False
    required_ids = {
        item.definition.id
        for item in results
        if item.definition.requirement_type == "required"
        and item.definition.level_id == prior_level.id
    }
    authoritative_facts = []
    for item in facts:
        criterion = next(
            (
                result.definition
                for result in results
                if result.definition.id == item.link.criterion_definition_id
            ),
            None,
        )
        provenance = json.loads(item.evidence.provenance_json)
        if (
            item.evidence.authoritative_for_downgrade
            and item.evidence.evidence_type in {"assessment", "verification"}
            and item.evidence.strength == "strong"
            and item.evidence.independence == "independent"
            and item.evidence.source_confidence == "high"
            and item.link.effect == "contradicts"
            and item.link.criterion_definition_id in required_ids
            and criterion is not None
            and criterion.verification_rubric is not None
            and provenance.get("downgrade_authority")
            in {"local_user_confirmed", "trusted_external_assessor"}
            and (
                provenance.get("rubric_references", {}).get(criterion.id)
                == criterion.verification_rubric
                or (
                    provenance.get("rubric_criterion_definition_id") == criterion.id
                    and provenance.get("rubric_reference") == criterion.verification_rubric
                )
            )
        ):
            authoritative_facts.append(item)
    authoritative = {item.evidence.id for item in authoritative_facts}
    authoritative_coverage = {
        item.link.criterion_definition_id for item in facts if item.evidence.id in authoritative
    }
    if required_ids and required_ids <= authoritative_coverage:
        declared_levels = {
            json.loads(item.evidence.provenance_json).get("maximum_supported_level_id")
            for item in authoritative_facts
        }
        declared = (
            next((item for item in levels if item.id in declared_levels), None)
            if len(declared_levels) == 1
            else None
        )
        if declared is not None and declared.ordinal_rank < prior_level.ordinal_rank:
            return declared, "authoritative_reassessment", sorted(authoritative), False
    last_contradiction_downgrade = db.scalar(
        select(CapabilityStateEvent)
        .where(
            CapabilityStateEvent.competency_identity_id == previous.competency_identity_id,
            CapabilityStateEvent.scope_key == previous.scope_key,
            CapabilityStateEvent.cause_code == "sustained_contradictory_performance",
            CapabilityStateEvent.created_at <= cutoff,
        )
        .order_by(CapabilityStateEvent.event_sequence.desc())
    )
    last_used_contradiction_at = None
    if last_contradiction_downgrade is not None:
        used_ids = json.loads(last_contradiction_downgrade.decisive_evidence_ids_json)
        used_times = list(
            db.scalars(
                select(Evidence.occurred_at).where(
                    Evidence.id.in_(used_ids), Evidence.occurred_at.is_not(None)
                )
            ).all()
        )
        last_used_contradiction_at = max(
            (value for value in used_times if value is not None), default=None
        )
    contradictions = [
        item
        for item in facts
        if item.link.effect == "contradicts"
        and item.link.criterion_definition_id in required_ids
        and _STRENGTH[item.evidence.strength] >= 2
        and _SOURCE_CONFIDENCE[item.evidence.source_confidence] >= 2
        and item.evidence.occurred_at is not None
        and (
            previous.last_meaningful_evidence_at is None
            or item.evidence.occurred_at > previous.last_meaningful_evidence_at
        )
        and (
            last_used_contradiction_at is None
            or item.evidence.occurred_at > last_used_contradiction_at
        )
    ]
    occurrences = {item.occurrence_key: item for item in contradictions}
    if len(occurrences) >= 2 and any(
        item.evidence.independence == "independent" for item in occurrences.values()
    ):
        lower_rank = max(0, prior_level.ordinal_rank - 1)
        lower = next((item for item in levels if item.ordinal_rank == lower_rank), None)
        return (
            lower,
            "sustained_contradictory_performance",
            sorted(item.evidence.id for item in occurrences.values()),
            False,
        )
    return prior_level, None, [], True


def _prior_state_at(
    db: Session, competency_id: str, scope_key: str, cutoff: int
) -> CapabilityPriorState | None:
    row = db.execute(
        select(CapabilityStateEvent, CapabilityEvaluationRun)
        .join(
            CapabilityEvaluationRun,
            CapabilityEvaluationRun.id == CapabilityStateEvent.evaluation_run_id,
        )
        .where(
            CapabilityStateEvent.competency_identity_id == competency_id,
            CapabilityStateEvent.scope_key == scope_key,
            CapabilityEvaluationRun.cutoff_at <= cutoff,
            CapabilityEvaluationRun.generated_at <= cutoff,
            CapabilityStateEvent.created_at <= cutoff,
        )
        .order_by(
            CapabilityEvaluationRun.cutoff_at.desc(),
            CapabilityStateEvent.event_sequence.desc(),
        )
    ).first()
    if row is None:
        return None
    event, run = row
    decisive_ids = json.loads(run.decisive_evidence_ids_json)
    occurred = list(
        db.scalars(
            select(Evidence.occurred_at).where(
                Evidence.id.in_(decisive_ids), Evidence.occurred_at.is_not(None)
            )
        ).all()
    )
    return CapabilityPriorState(
        competency_identity_id=competency_id,
        scope_key=scope_key,
        semantic_definition_id=event.semantic_definition_id,
        capability_level_id=event.new_level_id,
        assessment_status=event.new_assessment_status,
        aggregate_confidence=event.new_confidence,
        evaluation_run_id=run.id,
        last_meaningful_evidence_at=max(
            (value for value in occurred if value is not None), default=None
        ),
    )


def _freshness_thresholds(
    scale_key: str, level_key: str | None, dimension_key: str | None
) -> tuple[int | None, int | None]:
    if scale_key == "technical":
        return {
            "unexposed": (None, None),
            "familiar": (30, 90),
            "guided": (30, 60),
            "independent": (45, 90),
            "strong": (60, 120),
            "advanced": (90, 180),
        }.get(level_key or "", (None, None))
    if dimension_key in {"speaking", "writing"}:
        return 14, 45
    if dimension_key in {"listening", "reading"}:
        return 21, 60
    if dimension_key in {"grammar", "vocabulary"}:
        return 30, 90
    return 30, 90


def _active_profile_target(
    db: Session, competency_id: str, dimension_id: str | None
) -> ProfileTarget | None:
    active = db.get(ActiveTargetProfileState, 1)
    if active is None:
        return None
    dimension_key = None
    if dimension_id is not None:
        dimension = db.get(CapabilityScaleDimension, dimension_id)
        dimension_key = dimension.stable_key if dimension else None
    return db.execute(
        select(ProfileTarget)
        .join(ProfileTargetIdentity, ProfileTargetIdentity.id == ProfileTarget.target_identity_id)
        .where(
            ProfileTarget.profile_version_id == active.target_profile_version_id,
            ProfileTargetIdentity.competency_identity_id == competency_id,
            ProfileTargetIdentity.dimension_key == dimension_key,
        )
    ).scalar_one_or_none()


def evaluate_capability(
    db: Session,
    competency_id: str,
    *,
    cutoff_at: int | None = None,
) -> list[CapabilityEvaluationRun]:
    cutoff = cutoff_at or utc_now_ms()
    active = db.get(ActiveCompetencyDefinitionState, competency_id)
    if active is None:
        return []
    definition = db.get(SemanticCompetencyDefinition, active.semantic_definition_id)
    if definition is None:
        return []
    scale = db.get(CapabilityScaleVersion, definition.scale_version_id)
    assert scale is not None
    levels = list(
        db.scalars(
            select(CapabilityScaleLevel)
            .where(CapabilityScaleLevel.scale_version_id == scale.id)
            .order_by(CapabilityScaleLevel.ordinal_rank)
        ).all()
    )
    dimensions = list(
        db.scalars(
            select(CapabilityScaleDimension)
            .join(
                SemanticDefinitionDimension,
                SemanticDefinitionDimension.scale_dimension_id == CapabilityScaleDimension.id,
            )
            .where(SemanticDefinitionDimension.semantic_definition_id == definition.id)
            .order_by(CapabilityScaleDimension.order_index)
        ).all()
    )
    scopes: list[CapabilityScaleDimension | None] = [None, *dimensions]
    all_facts = _active_evidence_facts(db, competency_id, cutoff)
    created_runs: list[CapabilityEvaluationRun] = []
    for dimension in scopes:
        scope_key = _scope_key(dimension.id if dimension else None)
        criteria = list(
            db.scalars(
                select(CriterionDefinition).where(
                    CriterionDefinition.semantic_definition_id == definition.id,
                    CriterionDefinition.dimension_id == (dimension.id if dimension else None),
                )
            ).all()
        )
        scope_dimension_id = dimension.id if dimension else None
        scope_facts = [item for item in all_facts if item.link.dimension_id == scope_dimension_id]
        results = [_evaluate_criterion(item, scope_facts) for item in criteria]
        candidate, passed_level_ids = _candidate_level(scale, levels, results, scope_facts)
        previous = db.get(CompetencyCapabilityState, (competency_id, scope_key))
        historical_replay = previous is not None and previous.last_evaluated_at > cutoff
        policy_previous: CompetencyCapabilityState | CapabilityPriorState | None = previous
        if historical_replay:
            policy_previous = _prior_state_at(db, competency_id, scope_key, cutoff)
        previous_for_policy = (
            policy_previous
            if policy_previous and policy_previous.semantic_definition_id == definition.id
            else None
        )
        selected, downgrade_cause, downgrade_evidence, retained = _apply_downgrade_policy(
            db, previous_for_policy, candidate, levels, results, scope_facts, cutoff
        )
        assessment_status = "evaluated" if selected else "unknown"
        cumulative = []
        if selected:
            cumulative = [
                item
                for item in results
                if next(
                    level for level in levels if level.id == item.definition.level_id
                ).ordinal_rank
                <= selected.ordinal_rank
            ]
        high, unmet_high = (
            _high_confidence(cumulative, scope_facts, selected.id) if selected else (False, [])
        )
        qualifying_contradictions = sorted(
            {
                evidence_id
                for item in results
                if item.definition.requirement_type in {"required", "important"}
                for evidence_id in item.contradiction_ids
            }
        )
        if not selected:
            confidence = "unknown"
        elif retained or qualifying_contradictions:
            confidence = "low"
        elif high:
            confidence = "high"
        else:
            confidence = "medium"
        decisive_ids = sorted(
            {evidence_id for item in cumulative for evidence_id in item.decisive_ids}
            | set(downgrade_evidence)
        )
        evidence_payload = _evidence_payload(scope_facts)
        evidence_set_hash = _hash(evidence_payload)
        input_payload = {
            "competencyIdentityId": competency_id,
            "semanticDefinitionId": definition.id,
            "scaleVersionId": scale.id,
            "scopeKey": scope_key,
            "cutoffAt": cutoff,
            "evidence": evidence_payload,
            "criteria": [
                {
                    "id": item.definition.id,
                    "state": item.state,
                    "decisiveEvidenceIds": list(item.decisive_ids),
                    "facts": item.facts,
                }
                for item in results
            ],
            "policies": [CRITERION_POLICY, CAPABILITY_POLICY, EVIDENCE_POLICY, DOWNGRADE_POLICY],
        }
        input_hash = _hash(input_payload)
        idempotency_key = f"capability:{competency_id}:{scope_key}:{input_hash}"
        existing = db.scalar(
            select(CapabilityEvaluationRun).where(
                CapabilityEvaluationRun.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            created_runs.append(existing)
            continue
        confidence_facts = {
            "unmetHighConditions": unmet_high,
            "contradictionEvidenceIds": qualifying_contradictions,
            "decisiveEvidenceDiversity": len(
                {item.occurrence_key for item in scope_facts if item.evidence.id in decisive_ids}
            ),
            "policyVersion": CAPABILITY_POLICY,
            "retainedUnderHysteresis": retained,
        }
        reasons = ["NO_LEVEL_PASSED" if selected is None else "HIGHEST_CUMULATIVE_LEVEL_PASSED"]
        if retained:
            reasons.append("LEVEL_RETAINED_BY_DOWNGRADE_HYSTERESIS")
        output_payload = {
            "selectedLevelId": selected.id if selected else None,
            "assessmentStatus": assessment_status,
            "aggregateConfidence": confidence,
            "confidenceFacts": confidence_facts,
            "downgradeCause": downgrade_cause,
            "decisiveEvidenceIds": decisive_ids,
            "passedLevelIds": passed_level_ids,
            "reasons": reasons,
        }
        run = CapabilityEvaluationRun(
            idempotency_key=idempotency_key,
            competency_identity_id=competency_id,
            semantic_definition_id=definition.id,
            scale_version_id=scale.id,
            dimension_id=dimension.id if dimension else None,
            scope_key=scope_key,
            cutoff_at=cutoff,
            generated_at=utc_now_ms(),
            criterion_policy_version=CRITERION_POLICY,
            capability_policy_version=CAPABILITY_POLICY,
            evidence_policy_version=EVIDENCE_POLICY,
            downgrade_policy_version=DOWNGRADE_POLICY,
            evidence_set_hash=evidence_set_hash,
            input_payload_json=_canonical_json(input_payload),
            input_hash=input_hash,
            selected_level_id=selected.id if selected else None,
            assessment_status=assessment_status,
            aggregate_confidence=confidence,
            confidence_facts_json=_canonical_json(confidence_facts),
            downgrade_cause=downgrade_cause,
            decisive_evidence_ids_json=_canonical_json(decisive_ids),
            passed_level_ids_json=_canonical_json(passed_level_ids),
            reasons_json=_canonical_json(reasons),
            output_hash=_hash(output_payload),
        )
        db.add(run)
        db.flush()
        for item in results:
            db.add(
                CriterionEvaluationResult(
                    run_id=run.id,
                    criterion_definition_id=item.definition.id,
                    state=item.state,
                    evidence_set_hash=evidence_set_hash,
                    decisive_evidence_ids_json=_canonical_json(item.decisive_ids),
                    facts_json=_canonical_json(item.facts),
                )
            )
        if historical_replay:
            created_runs.append(run)
            continue
        db.add(
            ProjectionInvalidation(
                projection_kind="roadmap_projection_v2",
                subject_type="competency_capability",
                subject_id=competency_id,
                source_fact_id=run.id,
                target_policy_version=ACTIVE_PROJECTION_POLICY,
                status="pending",
                attempt_count=0,
                requested_at=run.generated_at,
            )
        )
        db.add(
            ProjectionInvalidation(
                projection_kind="analysis",
                subject_type="competency_capability",
                subject_id=competency_id,
                source_fact_id=run.id,
                target_policy_version="analysis-policy/v3.0",
                status="pending",
                attempt_count=0,
                requested_at=run.generated_at,
            )
        )
        meaningful_at = _meaningful_evidence_at(
            scope_facts, {item.id: item for item in criteria}, selected
        )
        latest_event = db.scalar(
            select(CapabilityStateEvent)
            .where(
                CapabilityStateEvent.competency_identity_id == competency_id,
                CapabilityStateEvent.scope_key == scope_key,
            )
            .order_by(CapabilityStateEvent.event_sequence.desc())
        )
        prior_level_id = (
            previous.capability_level_id
            if previous
            else latest_event.new_level_id
            if latest_event
            else None
        )
        prior_status = (
            previous.assessment_status
            if previous
            else latest_event.new_assessment_status
            if latest_event
            else None
        )
        prior_confidence = (
            previous.aggregate_confidence
            if previous
            else latest_event.new_confidence
            if latest_event
            else None
        )
        prior_decisive_json = latest_event.decisive_evidence_ids_json if latest_event else None
        changed = (
            previous.semantic_definition_id != definition.id
            if previous
            else latest_event is None or latest_event.semantic_definition_id != definition.id
        ) or (
            prior_level_id != run.selected_level_id
            or prior_status != assessment_status
            or prior_confidence != confidence
            or (
                previous is None
                and prior_decisive_json is not None
                and prior_decisive_json != _canonical_json(decisive_ids)
            )
            or (
                previous is not None and previous.confidence_facts_json != run.confidence_facts_json
            )
        )
        prior_version = (
            previous.state_version
            if previous
            else (latest_event.event_sequence if latest_event else 0)
        )
        next_version = prior_version + (1 if changed else 0)
        if changed:
            db.add(
                CapabilityStateEvent(
                    competency_identity_id=competency_id,
                    semantic_definition_id=definition.id,
                    dimension_id=dimension.id if dimension else None,
                    scope_key=scope_key,
                    event_sequence=next_version,
                    previous_level_id=prior_level_id,
                    new_level_id=run.selected_level_id,
                    previous_assessment_status=prior_status,
                    new_assessment_status=assessment_status,
                    previous_confidence=prior_confidence,
                    new_confidence=confidence,
                    cause_code=downgrade_cause
                    or (
                        "definition_changed"
                        if previous and previous.semantic_definition_id != definition.id
                        else "evaluation"
                    ),
                    decisive_evidence_ids_json=_canonical_json(decisive_ids),
                    evaluation_run_id=run.id,
                    created_at=run.generated_at,
                )
            )
        if previous is None:
            previous = CompetencyCapabilityState(
                competency_identity_id=competency_id,
                scope_key=scope_key,
                semantic_definition_id=definition.id,
                scale_version_id=scale.id,
                dimension_id=dimension.id if dimension else None,
                capability_level_id=run.selected_level_id,
                assessment_status=assessment_status,
                aggregate_confidence=confidence,
                evaluation_run_id=run.id,
                capability_policy_version=CAPABILITY_POLICY,
                criterion_policy_version=CRITERION_POLICY,
                evidence_policy_version=EVIDENCE_POLICY,
                evidence_set_hash=evidence_set_hash,
                last_evaluated_at=cutoff,
                last_meaningful_evidence_at=meaningful_at,
                state_version=next_version,
                confidence_facts_json=run.confidence_facts_json,
            )
            db.add(previous)
        else:
            previous.semantic_definition_id = definition.id
            previous.scale_version_id = scale.id
            previous.dimension_id = dimension.id if dimension else None
            previous.capability_level_id = run.selected_level_id
            previous.assessment_status = assessment_status
            previous.aggregate_confidence = confidence
            previous.evaluation_run_id = run.id
            previous.capability_policy_version = CAPABILITY_POLICY
            previous.criterion_policy_version = CRITERION_POLICY
            previous.evidence_policy_version = EVIDENCE_POLICY
            previous.evidence_set_hash = evidence_set_hash
            previous.last_evaluated_at = cutoff
            previous.last_meaningful_evidence_at = meaningful_at
            previous.state_version = next_version
            previous.confidence_facts_json = run.confidence_facts_json
        db.flush()
        _update_review_state(
            db,
            previous,
            run,
            scale,
            dimension,
            selected,
            qualifying_contradictions,
            cutoff,
        )
        created_runs.append(run)
    return created_runs


def _update_review_state(
    db: Session,
    capability: CompetencyCapabilityState,
    run: CapabilityEvaluationRun,
    scale: CapabilityScaleVersion,
    dimension: CapabilityScaleDimension | None,
    level: CapabilityScaleLevel | None,
    contradiction_ids: list[str],
    cutoff: int,
) -> None:
    current_days, stale_days = _freshness_thresholds(
        scale.scale_stable_key,
        level.stable_key if level else None,
        dimension.stable_key if dimension else None,
    )
    active_target = _active_profile_target(
        db, capability.competency_identity_id, capability.dimension_id
    )
    threshold_source = "freshness-policy-default"
    definition = db.get(SemanticCompetencyDefinition, capability.semantic_definition_id)
    if definition is not None and definition.freshness_current_through_days is not None:
        current_days = definition.freshness_current_through_days
        stale_days = definition.freshness_stale_after_days
        threshold_source = f"semantic-definition:{definition.id}"
    reasons: list[str] = []
    if level and scale.scale_stable_key == "technical" and level.stable_key == "unexposed":
        freshness = "unknown"
        reasons.append("level_unexposed")
        current_days = stale_days = None
    elif capability.last_meaningful_evidence_at is None:
        freshness = "unknown"
        reasons.append("no_meaningful_evidence_timestamp")
    else:
        timezone = ZoneInfo(get_or_create_profile(db).timezone)
        evidence_date = (
            datetime.fromtimestamp(capability.last_meaningful_evidence_at / 1000, UTC)
            .astimezone(timezone)
            .date()
        )
        cutoff_date = datetime.fromtimestamp(cutoff / 1000, UTC).astimezone(timezone).date()
        elapsed_days = (cutoff_date - evidence_date).days
        if stale_days is not None and elapsed_days > stale_days:
            freshness = "stale"
            reasons.append("freshness_stale")
        elif current_days is not None and elapsed_days > current_days:
            freshness = "aging"
            reasons.append("freshness_aging")
        else:
            freshness = "current"
    if contradiction_ids:
        reasons.append("qualifying_required_or_important_contradiction")
    if capability.aggregate_confidence == "low" and active_target is not None:
        reasons.append("active_target_low_confidence")
    review_due = freshness == "stale" or any(
        reason in {"qualifying_required_or_important_contradiction", "active_target_low_confidence"}
        for reason in reasons
    )
    scope_key = capability.scope_key
    previous = db.get(CompetencyReviewState, (capability.competency_identity_id, scope_key))
    latest_event = db.scalar(
        select(ReviewEvent)
        .where(
            ReviewEvent.competency_identity_id == capability.competency_identity_id,
            ReviewEvent.scope_key == scope_key,
        )
        .order_by(ReviewEvent.event_sequence.desc())
    )
    prior_freshness = (
        previous.freshness if previous else latest_event.new_freshness if latest_event else None
    )
    prior_due = (
        previous.review_due if previous else latest_event.new_review_due if latest_event else None
    )
    reason_json = _canonical_json(sorted(reasons))
    prior_reason_json = (
        previous.reason_codes_json
        if previous
        else latest_event.reason_codes_json
        if latest_event
        else None
    )
    changed = (
        prior_freshness != freshness or prior_due != review_due or prior_reason_json != reason_json
    )
    prior_version = (
        previous.state_version if previous else (latest_event.event_sequence if latest_event else 0)
    )
    next_version = prior_version + (1 if changed else 0)
    if changed:
        db.add(
            ReviewEvent(
                competency_identity_id=capability.competency_identity_id,
                semantic_definition_id=capability.semantic_definition_id,
                dimension_id=capability.dimension_id,
                scope_key=scope_key,
                event_sequence=next_version,
                previous_freshness=prior_freshness,
                new_freshness=freshness,
                previous_review_due=prior_due,
                new_review_due=review_due,
                reason_codes_json=_canonical_json(sorted(reasons)),
                last_meaningful_evidence_at=capability.last_meaningful_evidence_at,
                current_through_days=current_days,
                stale_after_days=stale_days,
                threshold_source=threshold_source,
                freshness_policy_version=FRESHNESS_POLICY,
                evaluation_run_id=run.id,
                created_at=run.generated_at,
            )
        )
    if previous is None:
        db.add(
            CompetencyReviewState(
                competency_identity_id=capability.competency_identity_id,
                scope_key=scope_key,
                semantic_definition_id=capability.semantic_definition_id,
                scale_version_id=capability.scale_version_id,
                dimension_id=capability.dimension_id,
                freshness=freshness,
                review_due=review_due,
                reason_codes_json=_canonical_json(sorted(reasons)),
                last_meaningful_evidence_at=capability.last_meaningful_evidence_at,
                current_through_days=current_days,
                stale_after_days=stale_days,
                threshold_source=threshold_source,
                freshness_policy_version=FRESHNESS_POLICY,
                evaluated_at=cutoff,
                evaluation_run_id=run.id,
                state_version=next_version,
            )
        )
    else:
        previous.semantic_definition_id = capability.semantic_definition_id
        previous.scale_version_id = capability.scale_version_id
        previous.dimension_id = capability.dimension_id
        previous.freshness = freshness
        previous.review_due = review_due
        previous.reason_codes_json = _canonical_json(sorted(reasons))
        previous.last_meaningful_evidence_at = capability.last_meaningful_evidence_at
        previous.current_through_days = current_days
        previous.stale_after_days = stale_days
        previous.threshold_source = threshold_source
        previous.freshness_policy_version = FRESHNESS_POLICY
        previous.evaluated_at = cutoff
        previous.evaluation_run_id = run.id
        previous.state_version = next_version


def _invalidation_competency(db: Session, item: ProjectionInvalidation) -> str | None:
    if item.subject_type == "competency":
        return item.subject_id
    if item.subject_type == "criterion":
        criterion = db.get(CriterionIdentity, item.subject_id)
        return criterion.competency_identity_id if criterion else None
    return None


def drain_projection_invalidations(
    db: Session,
    *,
    max_attempts: int = 3,
    atomic: bool = False,
    recover_running: bool = False,
) -> int:
    processed = 0
    if recover_running:
        db.execute(
            update(ProjectionInvalidation)
            .where(
                ProjectionInvalidation.status == "running",
                ProjectionInvalidation.projection_kind.in_(
                    ["criterion_evaluation", "capability", "review"]
                ),
            )
            .values(status="pending", started_at=None)
        )
        db.commit()
    ids = list(
        db.scalars(
            select(ProjectionInvalidation.id)
            .where(
                ProjectionInvalidation.status == "pending",
                ProjectionInvalidation.projection_kind.in_(
                    ["criterion_evaluation", "capability", "review"]
                ),
            )
            .order_by(
                ProjectionInvalidation.projection_kind,
                ProjectionInvalidation.subject_type,
                ProjectionInvalidation.subject_id,
                ProjectionInvalidation.subject_sequence,
                ProjectionInvalidation.id,
            )
        ).all()
    )
    for invalidation_id in ids:
        if atomic:
            item = db.get(ProjectionInvalidation, invalidation_id)
            assert item is not None
            item.status = "running"
            item.attempt_count += 1
            item.started_at = utc_now_ms()
            item.attempt_run_id = new_id()
            item.error_json = None
            competency_id = _invalidation_competency(db, item)
            runs = evaluate_capability(db, competency_id) if competency_id else []
            item.status = "completed"
            item.completed_at = utc_now_ms()
            item.result_run_id = runs[-1].id if runs else None
            processed += 1
            continue
        claimed = db.execute(
            update(ProjectionInvalidation)
            .where(
                ProjectionInvalidation.id == invalidation_id,
                ProjectionInvalidation.status == "pending",
            )
            .values(
                status="running",
                attempt_count=ProjectionInvalidation.attempt_count + 1,
                started_at=utc_now_ms(),
                attempt_run_id=new_id(),
                error_json=None,
            )
        )
        if getattr(claimed, "rowcount", 0) != 1:
            db.rollback()
            continue
        db.commit()
        item = db.get(ProjectionInvalidation, invalidation_id)
        assert item is not None
        try:
            competency_id = _invalidation_competency(db, item)
            runs = evaluate_capability(db, competency_id) if competency_id else []
            item.status = "completed"
            item.completed_at = utc_now_ms()
            item.result_run_id = runs[-1].id if runs else None
            item.error_json = None
            db.commit()
            processed += 1
        except Exception as exc:
            db.rollback()
            item = db.get(ProjectionInvalidation, invalidation_id)
            assert item is not None
            item.status = "permanent_failure" if item.attempt_count >= max_attempts else "pending"
            item.error_json = _canonical_json(
                {"type": type(exc).__name__, "message": str(exc)[:1000]}
            )
            db.commit()
            logger.exception("Capability invalidation %s failed", invalidation_id)
    return processed


def commit_source_and_drain(db: Session) -> int:
    """Commit authoritative facts first, then durably attempt derived recomputation."""
    db.commit()
    return drain_projection_invalidations(db)


def enqueue_full_capability_rebuild(db: Session, *, source_fact_id: str) -> int:
    count = 0
    for active in db.scalars(select(ActiveCompetencyDefinitionState)).all():
        for kind, policy in (
            ("criterion_evaluation", CRITERION_POLICY),
            ("capability", CAPABILITY_POLICY),
            ("review", FRESHNESS_POLICY),
        ):
            db.add(
                ProjectionInvalidation(
                    projection_kind=kind,
                    subject_type="competency",
                    subject_id=active.competency_identity_id,
                    source_fact_id=source_fact_id,
                    target_policy_version=policy,
                    status="pending",
                    attempt_count=0,
                    requested_at=utc_now_ms(),
                )
            )
            count += 1
    return count


def seed_capability_projections_from_history(db: Session) -> int:
    """Restore downgrade hysteresis from immutable history before a full rebuild."""
    created = 0
    events = db.scalars(
        select(CapabilityStateEvent).order_by(
            CapabilityStateEvent.competency_identity_id,
            CapabilityStateEvent.scope_key,
            CapabilityStateEvent.event_sequence.desc(),
        )
    ).all()
    latest: dict[tuple[str, str], CapabilityStateEvent] = {}
    for event in events:
        latest.setdefault((event.competency_identity_id, event.scope_key), event)
    for (competency_id, scope_key), event in latest.items():
        if db.get(ActiveCompetencyDefinitionState, competency_id) is None:
            continue
        run = db.get(CapabilityEvaluationRun, event.evaluation_run_id)
        if run is None:
            raise RuntimeError("Capability history references a missing evaluation run.")
        decisive_ids = json.loads(run.decisive_evidence_ids_json)
        meaningful_at = None
        if decisive_ids:
            occurred_values = list(
                db.scalars(
                    select(Evidence.occurred_at).where(
                        Evidence.id.in_(decisive_ids), Evidence.occurred_at.is_not(None)
                    )
                ).all()
            )
            meaningful_at = max(
                (value for value in occurred_values if value is not None), default=None
            )
        db.add(
            CompetencyCapabilityState(
                competency_identity_id=competency_id,
                scope_key=scope_key,
                semantic_definition_id=event.semantic_definition_id,
                scale_version_id=run.scale_version_id,
                dimension_id=event.dimension_id,
                capability_level_id=event.new_level_id,
                assessment_status=event.new_assessment_status,
                aggregate_confidence=event.new_confidence,
                evaluation_run_id=run.id,
                capability_policy_version=run.capability_policy_version,
                criterion_policy_version=run.criterion_policy_version,
                evidence_policy_version=run.evidence_policy_version,
                evidence_set_hash=run.evidence_set_hash,
                last_evaluated_at=run.cutoff_at,
                last_meaningful_evidence_at=meaningful_at,
                state_version=event.event_sequence,
                confidence_facts_json=run.confidence_facts_json,
            )
        )
        created += 1
    return created


def _serialize_current(db: Session, state: CompetencyCapabilityState) -> dict[str, Any]:
    level = (
        db.get(CapabilityScaleLevel, state.capability_level_id)
        if state.capability_level_id
        else None
    )
    scale = db.get(CapabilityScaleVersion, state.scale_version_id)
    dimension = db.get(CapabilityScaleDimension, state.dimension_id) if state.dimension_id else None
    review = db.get(CompetencyReviewState, (state.competency_identity_id, state.scope_key))
    return {
        "scopeKey": state.scope_key,
        "semanticDefinitionId": state.semantic_definition_id,
        "scaleStableKey": scale.scale_stable_key if scale else None,
        "scaleVersion": scale.scale_version if scale else None,
        "dimensionKey": dimension.stable_key if dimension else None,
        "capabilityLevelId": state.capability_level_id,
        "capabilityLevelKey": level.stable_key if level else None,
        "assessmentStatus": state.assessment_status,
        "aggregateConfidence": state.aggregate_confidence,
        "confidenceFacts": json.loads(state.confidence_facts_json),
        "freshness": (
            "not_applicable"
            if level and level.stable_key == "unexposed"
            else review.freshness
            if review
            else "unknown"
        ),
        "reviewDue": review.review_due if review else False,
        "reviewReasons": json.loads(review.reason_codes_json) if review else [],
        "lastMeaningfulEvidenceAt": (
            epoch_ms_to_rfc3339(state.last_meaningful_evidence_at)
            if state.last_meaningful_evidence_at is not None
            else None
        ),
        "lastEvaluatedAt": epoch_ms_to_rfc3339(state.last_evaluated_at),
        "evaluationRunId": state.evaluation_run_id,
    }


@router.get("/capabilities/{competency_id}")
async def get_capability(
    competency_id: str,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    states = list(
        db.scalars(
            select(CompetencyCapabilityState)
            .where(CompetencyCapabilityState.competency_identity_id == competency_id)
            .order_by(CompetencyCapabilityState.scope_key)
        ).all()
    )
    if not states:
        if db.get(ActiveCompetencyDefinitionState, competency_id) is None:
            raise AppError(404, "CAPABILITY_DEFINITION_NOT_FOUND", "No active definition exists.")
    items = [_serialize_current(db, item) for item in states]
    dimensions = [
        item
        for item in items
        if item["dimensionKey"] in {"speaking", "listening", "reading", "writing"}
    ]
    readiness_floor = None
    if len(dimensions) == 4 and all(item["capabilityLevelKey"] for item in dimensions):
        levels = {item.id: item for item in db.scalars(select(CapabilityScaleLevel)).all()}
        floor = min(dimensions, key=lambda item: levels[item["capabilityLevelId"]].ordinal_rank)
        readiness_floor = {
            "label": "communicative_readiness_floor",
            "levelKey": floor["capabilityLevelKey"],
        }
    return {
        "competencyIdentityId": competency_id,
        "states": items,
        "readinessFloor": readiness_floor,
    }


@router.get("/capabilities/{competency_id}/history")
async def get_capability_history(
    competency_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    _auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run_query = select(CapabilityEvaluationRun).where(
        CapabilityEvaluationRun.competency_identity_id == competency_id
    )
    if cursor is not None:
        cursor_run = db.get(CapabilityEvaluationRun, cursor)
        if cursor_run is None or cursor_run.competency_identity_id != competency_id:
            raise AppError(422, "CAPABILITY_HISTORY_CURSOR_INVALID", "History cursor is invalid.")
        run_query = run_query.where(
            or_(
                CapabilityEvaluationRun.generated_at < cursor_run.generated_at,
                and_(
                    CapabilityEvaluationRun.generated_at == cursor_run.generated_at,
                    CapabilityEvaluationRun.id < cursor_run.id,
                ),
            )
        )
    run_page = list(
        db.scalars(
            run_query.order_by(
                CapabilityEvaluationRun.generated_at.desc(), CapabilityEvaluationRun.id.desc()
            ).limit(limit + 1)
        ).all()
    )
    has_more = len(run_page) > limit
    runs = run_page[:limit]
    run_ids = [item.id for item in runs]
    events = (
        db.scalars(
            select(CapabilityStateEvent)
            .where(CapabilityStateEvent.evaluation_run_id.in_(run_ids))
            .order_by(CapabilityStateEvent.created_at.desc(), CapabilityStateEvent.id.desc())
        ).all()
        if run_ids
        else []
    )
    criterion_results = (
        db.scalars(
            select(CriterionEvaluationResult)
            .where(CriterionEvaluationResult.run_id.in_(run_ids))
            .order_by(CriterionEvaluationResult.run_id, CriterionEvaluationResult.id)
        ).all()
        if run_ids
        else []
    )
    review_events = (
        db.scalars(
            select(ReviewEvent)
            .where(ReviewEvent.evaluation_run_id.in_(run_ids))
            .order_by(ReviewEvent.created_at.desc(), ReviewEvent.id.desc())
        ).all()
        if run_ids
        else []
    )
    return {
        "limit": limit,
        "nextCursor": runs[-1].id if has_more and runs else None,
        "events": [
            {
                "id": item.id,
                "scopeKey": item.scope_key,
                "sequence": item.event_sequence,
                "previousLevelId": item.previous_level_id,
                "newLevelId": item.new_level_id,
                "causeCode": item.cause_code,
                "evaluationRunId": item.evaluation_run_id,
            }
            for item in events
        ],
        "runs": [
            {
                "id": item.id,
                "scopeKey": item.scope_key,
                "cutoffAt": epoch_ms_to_rfc3339(item.cutoff_at),
                "selectedLevelId": item.selected_level_id,
                "assessmentStatus": item.assessment_status,
                "aggregateConfidence": item.aggregate_confidence,
                "confidenceFacts": json.loads(item.confidence_facts_json),
                "downgradeCause": item.downgrade_cause,
                "decisiveEvidenceIds": json.loads(item.decisive_evidence_ids_json),
                "passedLevelIds": json.loads(item.passed_level_ids_json),
                "reasons": json.loads(item.reasons_json),
                "criterionPolicyVersion": item.criterion_policy_version,
                "capabilityPolicyVersion": item.capability_policy_version,
                "evidencePolicyVersion": item.evidence_policy_version,
                "downgradePolicyVersion": item.downgrade_policy_version,
                "outputHash": item.output_hash,
            }
            for item in runs
        ],
        "criterionResults": [
            {
                "id": item.id,
                "runId": item.run_id,
                "criterionDefinitionId": item.criterion_definition_id,
                "state": item.state,
                "decisiveEvidenceIds": json.loads(item.decisive_evidence_ids_json),
                "facts": json.loads(item.facts_json),
            }
            for item in criterion_results
        ],
        "reviewEvents": [
            {
                "id": item.id,
                "scopeKey": item.scope_key,
                "sequence": item.event_sequence,
                "previousFreshness": item.previous_freshness,
                "newFreshness": item.new_freshness,
                "previousReviewDue": item.previous_review_due,
                "newReviewDue": item.new_review_due,
                "reasonCodes": json.loads(item.reason_codes_json),
                "lastMeaningfulEvidenceAt": (
                    epoch_ms_to_rfc3339(item.last_meaningful_evidence_at)
                    if item.last_meaningful_evidence_at is not None
                    else None
                ),
                "currentThroughDays": item.current_through_days,
                "staleAfterDays": item.stale_after_days,
                "thresholdSource": item.threshold_source,
                "freshnessPolicyVersion": item.freshness_policy_version,
                "evaluationRunId": item.evaluation_run_id,
            }
            for item in review_events
        ],
    }
