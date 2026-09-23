"""Master Import V1 validation, resolution, diff, and transaction-neutral writer."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, NoReturn, cast

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.curriculum.api import write_curriculum_activation
from app.curriculum.contracts import CurriculumActivationInput, CurriculumVersionInput
from app.curriculum.models import (
    ActiveCurriculumVersionState,
    AssessmentRubricIdentity,
    Curriculum,
    CurriculumActivationEvent,
    CurriculumObjectiveIdentity,
    CurriculumVersion,
    LearningUnitIdentity,
)
from app.curriculum.service import create_version as create_curriculum_version
from app.errors import AppError
from app.learning_graph.contracts import LearningGraphVersionInput
from app.learning_graph.models import (
    ActiveLearningGraphState,
    CompetencyEdgeIdentity,
    LearningGraph,
    LearningGraphActivationEvent,
    LearningGraphVersion,
)
from app.learning_graph.service import activate_version as activate_graph_version
from app.learning_graph.service import create_version as create_graph_version
from app.master_import.canonical import canonical_bytes, content_digest, package_digest
from app.master_import.contracts import Payload, is_utc_rfc3339, validate_payload
from app.models import (
    ActiveCompetencyDefinitionState,
    ActiveTargetProfileState,
    CapabilityScaleLevel,
    CapabilityScaleVersion,
    CompetencyDefinitionActivationEvent,
    CompetencyIdentity,
    CompetencyState,
    CompetencyStatusEvent,
    CriterionDefinition,
    CriterionIdentity,
    ImportRecord,
    MasterImportOwnedKey,
    MasterImportRevision,
    MilestoneIdentity,
    ProfileTargetIdentity,
    ReadinessGateIdentity,
    SemanticCompetencyDefinition,
    TargetProfile,
    TargetProfileActivationEvent,
    TargetProfileVersion,
    new_id,
)
from app.schemas import (
    ActivationRequest,
    SemanticCompetencyDefinitionCreate,
    TargetProfileVersionCreate,
)
from app.time_utils import utc_now_ms
from app.v2_profiles import (
    _create_profile_version,
    write_semantic_activation,
    write_semantic_definition,
    write_target_profile_activation,
)

SOURCE = "master_import"
TECHNICAL_SCALE = ("technical", "v1")
DIFF_DETAIL_LIMIT = 32


def _fail(code: str, message: str, **details: Any) -> NoReturn:
    raise AppError(422, code, message, details or None)


def _unique(values: list[Any], label: str) -> None:
    if len(values) != len(set(values)):
        _fail("MASTER_IMPORT_DUPLICATE_KEY", f"Duplicate {label} stable keys or order values.")


def _hash(value: Any) -> str:
    return "mi-meaning-v1:sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def _section_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _criterion_pair(reference: str) -> tuple[str, str]:
    try:
        competency, criterion = reference.split("::", 1)
    except ValueError:
        _fail(
            "MASTER_IMPORT_REFERENCE_INVALID",
            "Criterion references require competency::criterion syntax.",
        )
    return competency, criterion


def _children(payload: Payload) -> set[tuple[str, str]]:
    children: set[tuple[str, str]] = set()
    for competency in payload.competencies:
        children.update(
            ("criterion", f"{competency.stableKey}::{item.stableKey}")
            for item in competency.criteria
        )
    profile = payload.targetProfile
    children.update(("profileDomain", item.stableKey) for item in profile.domains)
    children.update(("profileTarget", item.stableKey) for item in profile.targets)
    children.update(("profileMilestone", item.stableKey) for item in profile.milestones)
    children.update(("readinessGate", item.stableKey) for item in profile.readinessGates)
    curriculum = payload.curriculum
    children.update(("objective", item.stableKey) for item in curriculum.objectives)
    children.update(("unit", item.stableKey) for item in curriculum.units)
    children.update(("rubric", item.stableKey) for item in curriculum.assessmentRubrics)
    for unit in curriculum.units:
        children.update(
            ("requirement", f"{unit.stableKey}::{item.stableKey}") for item in unit.requirements
        )
        children.update(
            ("opportunity", f"{unit.stableKey}::{item.stableKey}")
            for item in unit.evidenceOpportunities
        )
    children.update(("edge", item.stableKey) for item in payload.learningGraph.edges)
    return children


def _validate_authored(payload: Payload, prior: MasterImportRevision | None) -> dict[str, Any]:
    competencies = {item.stableKey: item for item in payload.competencies}
    _unique([item.stableKey for item in payload.competencies], "Competency")
    criteria = {
        f"{competency.stableKey}::{criterion.stableKey}"
        for competency in payload.competencies
        for criterion in competency.criteria
    }
    for competency in payload.competencies:
        _unique([item.stableKey for item in competency.criteria], "Criterion")
        if (competency.freshnessCurrentThroughDays is None) != (
            competency.freshnessStaleAfterDays is None
        ):
            _fail(
                "MASTER_IMPORT_FRESHNESS_INVALID", "Competency freshness requires a complete pair."
            )
        if (
            competency.freshnessCurrentThroughDays is not None
            and competency.freshnessStaleAfterDays is not None
            and competency.freshnessStaleAfterDays < competency.freshnessCurrentThroughDays
        ):
            _fail("MASTER_IMPORT_FRESHNESS_INVALID", "Competency freshness is reversed.")
    profile = payload.targetProfile
    domains = {item.stableKey for item in profile.domains}
    targets = {item.stableKey for item in profile.targets}
    _unique([item.stableKey for item in profile.domains], "Profile domain")
    _unique([item.orderIndex for item in profile.domains], "Profile domain")
    _unique([item.stableKey for item in profile.targets], "Profile target")
    for profile_target in profile.targets:
        if (
            profile_target.competencyRef not in competencies
            or profile_target.domainRef not in domains
        ):
            _fail("MASTER_IMPORT_REFERENCE_INVALID", "A Profile target reference is unresolved.")
    for milestone in profile.milestones:
        if not set(milestone.targetRefs) <= targets:
            _fail("MASTER_IMPORT_REFERENCE_INVALID", "A milestone target is unresolved.")
    for gate in profile.readinessGates:
        if not set(gate.targetRefs) <= targets or (
            gate.milestoneRef
            and gate.milestoneRef not in {item.stableKey for item in profile.milestones}
        ):
            _fail("MASTER_IMPORT_REFERENCE_INVALID", "A readiness gate reference is unresolved.")
        for predicate in gate.predicates:
            if (
                predicate.competencyRef
                and predicate.competencyRef not in competencies
                or predicate.criterionRef
                and predicate.criterionRef not in criteria
            ):
                _fail(
                    "MASTER_IMPORT_REFERENCE_INVALID",
                    "A readiness predicate reference is unresolved.",
                )
    curriculum = payload.curriculum
    objectives = {item.stableKey for item in curriculum.objectives}
    units = {item.stableKey for item in curriculum.units}
    _unique([item.stableKey for item in curriculum.objectives], "Objective")
    _unique([item.stableKey for item in curriculum.units], "Learning Unit")
    _unique([item.stableKey for item in curriculum.assessmentRubrics], "Assessment rubric")
    for unit in curriculum.units:
        if unit.objectiveRef and unit.objectiveRef not in objectives:
            _fail("MASTER_IMPORT_REFERENCE_INVALID", "A Learning Unit objective is unresolved.")
        for unit_target in unit.targets:
            if (
                unit_target.competencyRef not in competencies
                or unit_target.criterionRef
                and unit_target.criterionRef not in criteria
            ):
                _fail("MASTER_IMPORT_REFERENCE_INVALID", "A Learning Unit target is unresolved.")
            if (
                unit_target.criterionRef
                and _criterion_pair(unit_target.criterionRef)[0] != unit_target.competencyRef
            ):
                _fail(
                    "MASTER_IMPORT_CRITERION_MISMATCH",
                    "A Learning Unit criterion belongs to another Competency.",
                )
        for requirement in unit.requirements:
            if (
                requirement.competencyRef
                and requirement.competencyRef not in competencies
                or requirement.criterionRef
                and requirement.criterionRef not in criteria
                or requirement.unitRef
                and requirement.unitRef not in units
            ):
                _fail(
                    "MASTER_IMPORT_REFERENCE_INVALID", "A Learning Unit requirement is unresolved."
                )
    for rubric in curriculum.assessmentRubrics:
        if (
            rubric.competencyRef not in competencies
            or rubric.criterionRef
            and rubric.criterionRef not in criteria
        ):
            _fail("MASTER_IMPORT_REFERENCE_INVALID", "An assessment rubric target is unresolved.")
        if rubric.criterionRef and _criterion_pair(rubric.criterionRef)[0] != rubric.competencyRef:
            _fail(
                "MASTER_IMPORT_CRITERION_MISMATCH",
                "An assessment rubric criterion belongs to another Competency.",
            )
        for criterion in rubric.rubric.criteria:
            if (
                criterion.criterionRef not in criteria
                or _criterion_pair(criterion.criterionRef)[0] != rubric.competencyRef
            ):
                _fail(
                    "MASTER_IMPORT_CRITERION_MISMATCH",
                    "An assessment rubric criterion is incompatible.",
                )
    _unique([item.stableKey for item in payload.learningGraph.edges], "Graph edge")
    for edge in payload.learningGraph.edges:
        if edge.sourceRef not in competencies or edge.targetRef not in competencies:
            _fail("MASTER_IMPORT_REFERENCE_INVALID", "A Graph endpoint is unresolved.")
        if (
            edge.edgeType == "prerequisite"
            and edge.requirement is None
            or edge.edgeType != "prerequisite"
            and edge.requirement is not None
        ):
            _fail(
                "MASTER_IMPORT_GRAPH_INVALID", "Only hard prerequisites require satisfaction rules."
            )
        if (
            edge.requirement
            and edge.requirement.criterionRefs
            and any(
                _criterion_pair(ref)[0] != edge.sourceRef or ref not in criteria
                for ref in edge.requirement.criterionRefs
            )
        ):
            _fail(
                "MASTER_IMPORT_CRITERION_MISMATCH",
                "Graph prerequisite criteria must belong to its source.",
            )
    children = _children(payload)
    removal_items = [(item.entityKind, item.stableKey) for item in payload.removedFromActiveVersion]
    _unique(removal_items, "explicit removal")
    removal_set = set(removal_items)
    if prior:
        prior_selected = json.loads(prior.selected_versions_json)
        prior_roots = set(prior_selected.get("roots", []))
        roots = {f"competency:{key}" for key in competencies} | {
            f"profile:{profile.stableKey}",
            f"curriculum:{curriculum.stableKey}",
            f"graph:{payload.learningGraph.stableKey}",
        }
        if not prior_roots <= roots:
            _fail(
                "MASTER_IMPORT_ROOT_OMITTED",
                "A successor must include every previously owned root.",
            )
        prior_children = {tuple(item) for item in prior_selected.get("activeChildren", [])}
        if removal_set != prior_children - children:
            _fail(
                "MASTER_IMPORT_REMOVAL_INVALID",
                "Removed active children require an exact explicit declaration.",
            )
    elif removal_set:
        _fail("MASTER_IMPORT_REMOVAL_INVALID", "An initial revision cannot declare child removals.")
    return {
        "roots": sorted(
            {f"competency:{key}" for key in competencies}
            | {
                f"profile:{profile.stableKey}",
                f"curriculum:{curriculum.stableKey}",
                f"graph:{payload.learningGraph.stableKey}",
            }
        ),
        "activeChildren": [list(item) for item in sorted(children)],
    }


def _cold_start(payload: Payload, level_ranks: dict[str, int]) -> dict[str, Any]:
    units = {unit.stableKey: unit for unit in payload.curriculum.units if unit.status == "active"}
    rubrics: dict[str, list[Any]] = {}
    for rubric in payload.curriculum.assessmentRubrics:
        rubrics.setdefault(rubric.competencyRef, []).append(rubric)
    criteria = {
        f"{competency.stableKey}::{criterion.stableKey}": criterion
        for competency in payload.competencies
        for criterion in competency.criteria
    }
    required_by_competency = {
        competency.stableKey: [
            (f"{competency.stableKey}::{criterion.stableKey}", criterion)
            for criterion in competency.criteria
            if criterion.requirementType == "required"
        ]
        for competency in payload.competencies
    }
    hard_edges: dict[str, list[Any]] = {}
    for edge in payload.learningGraph.edges:
        if edge.edgeType == "prerequisite":
            hard_edges.setdefault(edge.targetRef, []).append(edge)
    graph_blocked = {
        edge.targetRef for edge in payload.learningGraph.edges if edge.edgeType == "prerequisite"
    }

    def reachable(unit: Any) -> bool:
        if any(req.effect == "hard" for req in unit.requirements):
            return False
        if any(target.competencyRef in graph_blocked for target in unit.targets):
            return False
        return any(
            target.supportsUnassessed
            or (unit.kind == "verification_template" and target.competencyRef in rubrics)
            for target in unit.targets
        )

    entries = [key for key, unit in units.items() if reachable(unit)]
    if not entries:
        _fail(
            "MASTER_IMPORT_COLD_START_DEADLOCK",
            "An all-Unknown learner has no immediately reachable first action.",
        )
    spine = payload.initialSpine
    if (
        any(key not in units for key in spine)
        or spine[0] not in entries
        or len(set(spine)) != len(spine)
    ):
        _fail(
            "MASTER_IMPORT_INITIAL_SPINE_INVALID",
            "The declared initial spine has an unreachable or repeated entry.",
        )
    possible_criteria: set[str] = set()
    possible_completed_units: set[str] = set()

    def possible_capability(competency_key: str, level_key: str) -> bool:
        threshold = level_ranks[level_key]
        required = required_by_competency[competency_key]
        direct = [
            ref for ref, criterion in required if level_ranks[criterion.levelKey] == threshold
        ]
        cumulative = {
            ref for ref, criterion in required if level_ranks[criterion.levelKey] <= threshold
        }
        return bool(direct) and cumulative <= possible_criteria

    def opportunity_can_demonstrate(unit: Any, criterion_ref: str) -> bool:
        criterion = criteria[criterion_ref]
        rule = criterion.demonstrationRule
        for opportunity in unit.evidenceOpportunities:
            if opportunity.evidenceKind == "project":
                continue
            if criterion.levelKey == "unexposed" and opportunity.evidenceKind != "assessment":
                continue
            if rule == "authoritative_assessment" and opportunity.evidenceKind != "assessment":
                continue
            if (
                rule in {"independent_performance", "repeated_independent_performance"}
                and opportunity.intendedIndependenceModes
                and "independent" not in opportunity.intendedIndependenceModes
            ):
                continue
            if (
                rule != "exposure"
                and opportunity.intendedStrengths
                and not set(opportunity.intendedStrengths) & {"moderate", "strong"}
            ):
                continue
            return True
        return False

    for unit_key in spine:
        unit = units[unit_key]
        for requirement in unit.requirements:
            if requirement.effect != "hard":
                continue
            if (
                (
                    requirement.requirementType == "learning_unit_completed"
                    and requirement.unitRef not in possible_completed_units
                )
                or (
                    requirement.requirementType == "criterion_demonstrated"
                    and requirement.criterionRef not in possible_criteria
                )
                or (
                    requirement.requirementType == "capability_at_least"
                    and not possible_capability(
                        requirement.competencyRef or "", requirement.levelKey or ""
                    )
                )
            ):
                _fail(
                    "MASTER_IMPORT_INITIAL_SPINE_INVALID",
                    "The initial spine has no structural route to a hard Unit prerequisite.",
                    unitKey=unit_key,
                    requirementKey=requirement.stableKey,
                )
        for target in unit.targets:
            for edge in hard_edges.get(target.competencyRef, []):
                requirement = edge.requirement
                assert requirement is not None
                if (
                    requirement.kind == "capability_at_least"
                    and not possible_capability(edge.sourceRef, requirement.minimumLevelKey or "")
                ) or (
                    requirement.kind == "criterion_set_demonstrated"
                    and not set(requirement.criterionRefs or []) <= possible_criteria
                ):
                    _fail(
                        "MASTER_IMPORT_INITIAL_SPINE_INVALID",
                        "The initial spine has no structural route to a hard Graph prerequisite.",
                        unitKey=unit_key,
                        edgeKey=edge.stableKey,
                    )
        if not any(
            target.supportsUnassessed
            or (unit.kind == "verification_template" and target.competencyRef in rubrics)
            or any(
                possible_capability(target.competencyRef, level)
                for level in level_ranks
                if target.minimumLevelKey is None
                or level_ranks[level] >= level_ranks[target.minimumLevelKey]
            )
            for target in unit.targets
        ):
            _fail(
                "MASTER_IMPORT_INITIAL_SPINE_INVALID",
                "The initial spine has no structurally usable target at this Unit.",
                unitKey=unit_key,
            )
        possible_completed_units.add(unit_key)
        for target in unit.targets:
            for rubric in rubrics.get(target.competencyRef, []):
                covered = {part.criterionRef for part in rubric.rubric.criteria}
                if rubric.criterionRef is not None:
                    covered &= {rubric.criterionRef}
                if target.criterionRef is not None:
                    covered &= {target.criterionRef}
                possible_criteria.update(covered)
            if target.criterionRef and opportunity_can_demonstrate(unit, target.criterionRef):
                possible_criteria.add(target.criterionRef)
    return {
        "entryUnitKeys": sorted(entries),
        "initialSpine": spine,
        "shortEntryUnitKeys": sorted(
            key
            for key in entries
            if units[key].minimumUsefulDurationMs is not None
            and (units[key].minimumUsefulDurationMs or 0) <= 900_000
        ),
    }


def prepare(db: Session, package: dict[str, Any]) -> tuple[Payload, dict[str, Any]]:
    if not is_utc_rfc3339(package.get("createdAt")):
        _fail(
            "MASTER_IMPORT_CREATED_AT_INVALID",
            "Master Import createdAt must be a timezone-aware UTC RFC 3339 instant.",
        )
    try:
        payload = validate_payload(package["payload"])
    except (ValidationError, KeyError) as exc:
        raise AppError(
            422, "MASTER_IMPORT_SCHEMA_INVALID", "The Master Import payload is invalid."
        ) from exc
    if package.get("packageType") != "master_import" or package.get("schemaVersion") != 1:
        _fail(
            "MASTER_IMPORT_SCHEMA_INVALID",
            "Master Import requires package type and schema version V1.",
        )
    digest = content_digest(package)
    package_hash = package_digest(package)
    prior = db.scalar(
        select(MasterImportRevision)
        .where(MasterImportRevision.lineage_key == payload.lineageKey)
        .order_by(MasterImportRevision.content_revision.desc())
        .limit(1)
    )
    record = db.scalar(select(ImportRecord).where(ImportRecord.package_id == package["packageId"]))
    if record:
        old = db.scalar(
            select(MasterImportRevision).where(MasterImportRevision.import_record_id == record.id)
        )
        if old and old.package_digest == package_hash:
            return payload, {
                "replay": True,
                "packageDigest": package_hash,
                "contentDigest": digest,
                "contentRevision": old.content_revision,
                "lineageKey": payload.lineageKey,
                "ownerKey": payload.ownerKey,
            }
        _fail(
            "MASTER_IMPORT_PACKAGE_ID_CONFLICT",
            "A package ID already identifies different content.",
        )
    if prior is None:
        if payload.contentRevision != 1 or payload.previousContentDigest is not None:
            _fail(
                "MASTER_IMPORT_LINEAGE_INVALID",
                "An initial lineage revision must be 1 without a predecessor.",
            )
    elif (
        payload.contentRevision != prior.content_revision + 1
        or payload.previousContentDigest != prior.content_digest
        or payload.ownerKey != prior.owner_key
    ):
        _fail(
            "MASTER_IMPORT_LINEAGE_INVALID",
            "A successor revision must follow the latest digest and owner exactly.",
        )
    elif digest == prior.content_digest:
        _fail(
            "MASTER_IMPORT_REDUNDANT_REVISION",
            "A successor revision cannot repeat unchanged semantic content.",
        )
    selected = _validate_authored(payload, prior)
    scale = db.scalar(
        select(CapabilityScaleVersion).where(
            CapabilityScaleVersion.scale_stable_key == TECHNICAL_SCALE[0],
            CapabilityScaleVersion.scale_version == TECHNICAL_SCALE[1],
        )
    )
    if scale is None:
        _fail("MASTER_IMPORT_SCALE_INVALID", "The seeded Technical v1 scale is missing.")
    level_ranks = {
        item.stable_key: item.ordinal_rank
        for item in db.scalars(
            select(CapabilityScaleLevel).where(CapabilityScaleLevel.scale_version_id == scale.id)
        ).all()
    }
    level_keys = set(level_ranks)
    referenced_levels = {
        criterion.levelKey
        for competency in payload.competencies
        for criterion in competency.criteria
    }
    referenced_levels |= {target.levelKey for target in payload.targetProfile.targets}
    referenced_levels |= {
        pred.levelKey
        for gate in payload.targetProfile.readinessGates
        for pred in gate.predicates
        if pred.levelKey
    }
    referenced_levels |= {
        level
        for unit in payload.curriculum.units
        for target in unit.targets
        for level in (target.minimumLevelKey, target.maximumLevelKey)
        if level
    }
    referenced_levels |= {
        req.levelKey
        for unit in payload.curriculum.units
        for req in unit.requirements
        if req.levelKey
    }
    referenced_levels |= {
        edge.requirement.minimumLevelKey
        for edge in payload.learningGraph.edges
        if edge.requirement and edge.requirement.minimumLevelKey
    }
    if not referenced_levels <= level_keys:
        _fail(
            "MASTER_IMPORT_SCALE_INVALID",
            "A Technical scale level reference is invalid.",
            unknownLevels=sorted(referenced_levels - level_keys),
        )
    cold = _cold_start(payload, level_ranks)
    if any(
        not getattr(payload.activationIntent, field)
        for field in ("competencies", "targetProfile", "curriculum", "learningGraph")
    ):
        _fail(
            "MASTER_IMPORT_ACTIVATION_INVALID",
            "V1 initialization requires explicit activation of all authored aggregates.",
        )
    if (
        datetime.fromisoformat(payload.effectiveAt.replace("Z", "+00:00")).timestamp() * 1000
        > utc_now_ms()
    ):
        _fail(
            "MASTER_IMPORT_ACTIVATION_INVALID",
            "A Master Import cannot activate before its effective time.",
        )
    return payload, {
        "replay": False,
        "packageDigest": package_hash,
        "contentDigest": digest,
        "contentRevision": payload.contentRevision,
        "lineageKey": payload.lineageKey,
        "ownerKey": payload.ownerKey,
        "snapshot": selected,
        "coldStart": cold,
    }


def base_state_digest(db: Session) -> str:
    """Bind preview to every relevant stable identity, owner, active selector and lineage."""

    def rows(model: Any, *fields: str) -> list[list[Any]]:
        return sorted(
            [
                [getattr(item, field) for field in fields]
                for item in db.scalars(select(model)).all()
            ],
            key=lambda row: canonical_bytes(row),
        )

    state = {
        "competencies": rows(CompetencyIdentity, "stable_key", "id"),
        "criteria": rows(CriterionIdentity, "competency_identity_id", "stable_key", "id"),
        "profiles": rows(TargetProfile, "stable_key", "id"),
        "profileTargets": rows(ProfileTargetIdentity, "target_profile_id", "stable_key", "id"),
        "profileMilestones": rows(MilestoneIdentity, "target_profile_id", "stable_key", "id"),
        "readinessGates": rows(ReadinessGateIdentity, "target_profile_id", "stable_key", "id"),
        "curricula": rows(Curriculum, "stable_key", "id"),
        "objectives": rows(CurriculumObjectiveIdentity, "curriculum_id", "stable_key", "id"),
        "units": rows(LearningUnitIdentity, "curriculum_id", "stable_key", "id"),
        "rubrics": rows(AssessmentRubricIdentity, "curriculum_id", "stable_key", "id"),
        "graphs": rows(LearningGraph, "stable_key", "id"),
        "edges": rows(CompetencyEdgeIdentity, "learning_graph_id", "stable_key", "id"),
        "scaleVersions": rows(CapabilityScaleVersion, "scale_stable_key", "scale_version", "id"),
        "scaleLevels": rows(CapabilityScaleLevel, "scale_version_id", "stable_key", "id"),
        "importPackageIds": rows(ImportRecord, "package_id", "id"),
        "owned": rows(
            MasterImportOwnedKey,
            "entity_kind",
            "scope_key",
            "stable_key",
            "canonical_id",
            "meaning_digest",
            "lineage_key",
        ),
        "revisions": rows(
            MasterImportRevision,
            "lineage_key",
            "content_revision",
            "content_digest",
            "package_digest",
        ),
        "activeDefinitions": rows(
            ActiveCompetencyDefinitionState, "competency_identity_id", "semantic_definition_id"
        ),
        "activeProfiles": rows(
            ActiveTargetProfileState, "target_profile_id", "target_profile_version_id"
        ),
        "activeCurricula": rows(
            ActiveCurriculumVersionState, "curriculum_id", "curriculum_version_id"
        ),
        "activeGraphs": rows(
            ActiveLearningGraphState, "learning_graph_id", "learning_graph_version_id"
        ),
    }
    return "mi-base-v1:sha256:" + hashlib.sha256(canonical_bytes(state)).hexdigest()


def _diff_detail(items: list[Any]) -> dict[str, Any]:
    shown = items[:DIFF_DETAIL_LIMIT]
    return {
        "total": len(items),
        "shown": len(shown),
        "omitted": len(items) - len(shown),
        "truncated": len(items) > DIFF_DETAIL_LIMIT,
        "items": shown,
    }


def semantic_diff(
    db: Session,
    package: dict[str, Any],
    prepared: dict[str, Any],
    selected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if prepared["replay"]:
        return {"replay": True, "contentRevision": prepared["contentRevision"], "changes": []}
    payload = validate_payload(package["payload"])
    latest = db.scalar(
        select(MasterImportRevision)
        .where(MasterImportRevision.lineage_key == payload.lineageKey)
        .order_by(MasterImportRevision.content_revision.desc())
        .limit(1)
    )
    old = json.loads(latest.selected_versions_json) if latest else {}
    old_owners = {
        (item.entity_kind, item.scope_key, item.stable_key)
        for item in db.scalars(select(MasterImportOwnedKey)).all()
    }
    proposed_owners = {("competency", "global", item.stableKey) for item in payload.competencies}
    proposed_owners |= {
        ("criterion", item.stableKey, criterion.stableKey)
        for item in payload.competencies
        for criterion in item.criteria
    }
    proposed_owners |= {
        ("profile", "global", payload.targetProfile.stableKey),
        ("curriculum", "global", payload.curriculum.stableKey),
        ("graph", "global", payload.learningGraph.stableKey),
    }
    proposed_owners |= {
        ("profileTarget", payload.targetProfile.stableKey, item.stableKey)
        for item in payload.targetProfile.targets
    }
    proposed_owners |= {
        ("profileDomain", payload.targetProfile.stableKey, item.stableKey)
        for item in payload.targetProfile.domains
    }
    proposed_owners |= {
        ("profileMilestone", payload.targetProfile.stableKey, item.stableKey)
        for item in payload.targetProfile.milestones
    }
    proposed_owners |= {
        ("readinessGate", payload.targetProfile.stableKey, item.stableKey)
        for item in payload.targetProfile.readinessGates
    }
    proposed_owners |= {
        ("objective", payload.curriculum.stableKey, item.stableKey)
        for item in payload.curriculum.objectives
    }
    proposed_owners |= {
        ("unit", payload.curriculum.stableKey, item.stableKey) for item in payload.curriculum.units
    }
    proposed_owners |= {
        ("rubric", payload.curriculum.stableKey, item.stableKey)
        for item in payload.curriculum.assessmentRubrics
    }
    proposed_owners |= {
        ("requirement", unit.stableKey, item.stableKey)
        for unit in payload.curriculum.units
        for item in unit.requirements
    }
    proposed_owners |= {
        ("opportunity", unit.stableKey, item.stableKey)
        for unit in payload.curriculum.units
        for item in unit.evidenceOpportunities
    }
    proposed_owners |= {
        ("edge", payload.learningGraph.stableKey, item.stableKey)
        for item in payload.learningGraph.edges
    }
    created_versions = []
    if selected:
        for key, entry in selected.get("competencies", {}).items():
            if entry["versionId"] != old.get("competencies", {}).get(key, {}).get("versionId"):
                created_versions.append(f"competency:{key}")
        for kind in ("profile", "curriculum", "graph"):
            if selected[kind]["versionId"] != old.get(kind, {}).get("versionId"):
                created_versions.append(kind)
    created = [list(item) for item in sorted(proposed_owners - old_owners)]
    reused = [list(item) for item in sorted(proposed_owners & old_owners)]
    unchanged_versions = (
        sorted(
            (
                {f"competency:{key}" for key in old.get("competencies", {})}
                | {"profile", "curriculum", "graph"}
            )
            - set(created_versions)
        )
        if old
        else []
    )
    removals = [item.model_dump(mode="json") for item in payload.removedFromActiveVersion]
    profile_groups = {
        "domains": sorted(item.stableKey for item in payload.targetProfile.domains),
        "targets": sorted(item.stableKey for item in payload.targetProfile.targets),
        "milestones": sorted(item.stableKey for item in payload.targetProfile.milestones),
        "readinessGates": sorted(item.stableKey for item in payload.targetProfile.readinessGates),
    }
    curriculum_groups = {
        "objectives": sorted(item.stableKey for item in payload.curriculum.objectives),
        "units": sorted(item.stableKey for item in payload.curriculum.units),
        "rubrics": sorted(item.stableKey for item in payload.curriculum.assessmentRubrics),
        "requirements": sorted(
            f"{unit.stableKey}::{item.stableKey}"
            for unit in payload.curriculum.units
            for item in unit.requirements
        ),
        "opportunities": sorted(
            f"{unit.stableKey}::{item.stableKey}"
            for unit in payload.curriculum.units
            for item in unit.evidenceOpportunities
        ),
    }
    graph_edges = [
        {
            "stableKey": item.stableKey,
            "sourceRef": item.sourceRef,
            "targetRef": item.targetRef,
            "edgeType": item.edgeType,
            "requirementKind": item.requirement.kind if item.requirement else None,
        }
        for item in sorted(payload.learningGraph.edges, key=lambda item: item.stableKey)
    ]

    def section(kind: str, stable_key: str, groups: dict[str, list[Any]]) -> dict[str, Any]:
        before = old.get(kind) or {}
        after = (selected or {}).get(kind) or {}
        return {
            "stableKey": stable_key,
            "changed": kind in created_versions,
            "beforeAuthoredHash": before.get("authoredHash"),
            "afterAuthoredHash": after.get("authoredHash"),
            "counts": {key: len(values) for key, values in groups.items()},
            "details": {key: _diff_detail(values) for key, values in groups.items()},
        }

    cold = prepared["coldStart"]
    count_items: dict[str, list[Any]] = {
        "stableIdentitiesCreated": created,
        "stableIdentitiesReused": reused,
        "immutableVersionsCreated": sorted(created_versions),
        "unchangedImmutableVersions": unchanged_versions,
        "activationsChanged": sorted(created_versions),
        "removedFromActiveVersion": removals,
    }
    return {
        "packageType": "master_import",
        "lineageKey": payload.lineageKey,
        "contentRevision": payload.contentRevision,
        "detailLimit": DIFF_DETAIL_LIMIT,
        "summaryCounts": {key: len(items) for key, items in count_items.items()},
        **{key: _diff_detail(items) for key, items in count_items.items()},
        "identityConflicts": [],
        "targetProfileChanges": section("profile", payload.targetProfile.stableKey, profile_groups),
        "curriculumChanges": section("curriculum", payload.curriculum.stableKey, curriculum_groups),
        "learningGraphChanges": section(
            "graph", payload.learningGraph.stableKey, {"edges": graph_edges}
        ),
        "semanticConflicts": [],
        "coldStartImpact": {
            "entryUnitKeys": _diff_detail(cold["entryUnitKeys"]),
            "initialSpine": _diff_detail(cold["initialSpine"]),
            "shortEntryUnitKeys": _diff_detail(cold["shortEntryUnitKeys"]),
        },
        "derivedImpact": [
            "capability",
            "review",
            "curriculum_availability",
            "roadmap_projection_v2",
            "analysis",
        ],
    }


def _claim(
    db: Session,
    revision: MasterImportRevision,
    pending: list[MasterImportOwnedKey],
    kind: str,
    scope: str,
    key: str,
    canonical_id: str,
    meaning: Any,
) -> None:
    digest = _hash(meaning)
    existing = db.scalar(
        select(MasterImportOwnedKey).where(
            MasterImportOwnedKey.entity_kind == kind,
            MasterImportOwnedKey.scope_key == scope,
            MasterImportOwnedKey.stable_key == key,
        )
    )
    if existing:
        if (
            existing.lineage_key != revision.lineage_key
            or existing.owner_key != revision.owner_key
            or existing.canonical_id != canonical_id
            or existing.meaning_digest != digest
        ):
            _fail(
                "MASTER_IMPORT_IDENTITY_COLLISION",
                "An authored stable identity conflicts with existing ownership or meaning.",
                entityKind=kind,
                stableKey=key,
            )
    else:
        pending.append(
            MasterImportOwnedKey(
                first_revision_id=revision.id,
                lineage_key=revision.lineage_key,
                owner_key=revision.owner_key,
                entity_kind=kind,
                scope_key=scope,
                stable_key=key,
                canonical_id=canonical_id,
                meaning_digest=digest,
            )
        )


def _existing_owned(
    db: Session, lineage: str, kind: str, scope: str, key: str, canonical_id: str | None
) -> None:
    if canonical_id is None:
        return
    owner = db.scalar(
        select(MasterImportOwnedKey).where(
            MasterImportOwnedKey.entity_kind == kind,
            MasterImportOwnedKey.scope_key == scope,
            MasterImportOwnedKey.stable_key == key,
        )
    )
    if owner is None or owner.lineage_key != lineage or owner.canonical_id != canonical_id:
        _fail(
            "MASTER_IMPORT_IDENTITY_COLLISION",
            "An existing stable identity is not owned by this lineage.",
            entityKind=kind,
            stableKey=key,
        )


def apply(
    db: Session,
    package: dict[str, Any],
    record: ImportRecord,
    prepared_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item: Any
    row: Any
    old: Any
    if prepared_state is None:
        payload, prepared = prepare(db, package)
    else:
        payload = validate_payload(package["payload"])
        prepared = prepared_state
    if prepared["replay"]:
        return prepared
    prior = db.scalar(
        select(MasterImportRevision)
        .where(MasterImportRevision.lineage_key == payload.lineageKey)
        .order_by(MasterImportRevision.content_revision.desc())
        .limit(1)
    )
    prior_selected = json.loads(prior.selected_versions_json) if prior else {}
    selected: dict[str, Any] = {
        "roots": prepared["snapshot"]["roots"],
        "activeChildren": prepared["snapshot"]["activeChildren"],
        "competencies": {},
    }
    now = utc_now_ms()
    revision = MasterImportRevision(
        id=new_id(),
        import_record_id=record.id,
        lineage_key=payload.lineageKey,
        owner_key=payload.ownerKey,
        content_revision=payload.contentRevision,
        package_digest=prepared["packageDigest"],
        content_digest=prepared["contentDigest"],
        previous_content_digest=payload.previousContentDigest,
        canonicalization_version="mi-canon-v1",
        provenance_json=json.dumps(payload.provenance.model_dump(), sort_keys=True),
        selected_versions_json="{}",
        activation_json="{}",
        applied_at=now,
    )
    pending_owned: list[MasterImportOwnedKey] = []
    scale = db.scalar(
        select(CapabilityScaleVersion).where(
            CapabilityScaleVersion.scale_stable_key == TECHNICAL_SCALE[0],
            CapabilityScaleVersion.scale_version == TECHNICAL_SCALE[1],
        )
    )
    if scale is None:
        _fail("MASTER_IMPORT_SCALE_INVALID", "The seeded Technical v1 scale is missing.")
    levels = {
        level.stable_key: level
        for level in db.scalars(
            select(CapabilityScaleLevel).where(CapabilityScaleLevel.scale_version_id == scale.id)
        ).all()
    }
    identities: dict[str, CompetencyIdentity] = {}
    definitions: dict[str, SemanticCompetencyDefinition] = {}
    criteria: dict[str, CriterionDefinition] = {}
    criterion_identities: dict[str, CriterionIdentity] = {}
    effective = datetime.fromisoformat(payload.effectiveAt.replace("Z", "+00:00"))
    for competency in payload.competencies:
        identity = db.scalar(
            select(CompetencyIdentity).where(CompetencyIdentity.stable_key == competency.stableKey)
        )
        _existing_owned(
            db,
            payload.lineageKey,
            "competency",
            "global",
            competency.stableKey,
            identity.id if identity else None,
        )
        if identity is None:
            identity = CompetencyIdentity(
                stable_key=competency.stableKey, identity_created_at=now, creation_source=SOURCE
            )
            db.add(identity)
            db.flush()
            db.add(
                CompetencyState(
                    competency_identity_id=identity.id, current_status="not_started", updated_at=now
                )
            )
            db.add(
                CompetencyStatusEvent(
                    competency_identity_id=identity.id,
                    from_status=None,
                    to_status="not_started",
                    reason="Competency introduced through Master Import",
                    source=SOURCE,
                    created_at=now,
                )
            )
            db.flush()
        _claim(
            db,
            revision,
            pending_owned,
            "competency",
            "global",
            competency.stableKey,
            identity.id,
            {"scope": competency.scope},
        )
        identities[competency.stableKey] = identity
        for criterion_input in competency.criteria:
            existing_criterion = db.scalar(
                select(CriterionIdentity).where(
                    CriterionIdentity.competency_identity_id == identity.id,
                    CriterionIdentity.stable_key == criterion_input.stableKey,
                )
            )
            _existing_owned(
                db,
                payload.lineageKey,
                "criterion",
                competency.stableKey,
                criterion_input.stableKey,
                existing_criterion.id if existing_criterion else None,
            )
        authored_hash = _section_hash(competency.model_dump(mode="json"))
        prior_item = prior_selected.get("competencies", {}).get(competency.stableKey)
        if prior_item and prior_item["authoredHash"] == authored_hash:
            definition = db.get(SemanticCompetencyDefinition, prior_item["versionId"])
            if definition is None:
                _fail("MASTER_IMPORT_LEDGER_INVALID", "A selected semantic version is missing.")
        else:
            definition_input = SemanticCompetencyDefinitionCreate.model_validate(
                {
                    "title": competency.title,
                    "description": competency.description,
                    "scope": competency.scope,
                    "scale_stable_key": "technical",
                    "scale_version": "v1",
                    "dimension_keys": [],
                    "effective_at": effective,
                    "creation_source": SOURCE,
                    "freshness_current_through_days": competency.freshnessCurrentThroughDays,
                    "freshness_stale_after_days": competency.freshnessStaleAfterDays,
                    "criteria": [
                        {
                            "stable_key": item.stableKey,
                            "level_stable_key": item.levelKey,
                            "dimension_key": None,
                            "requirement_type": item.requirementType,
                            "demonstration_rule": item.demonstrationRule,
                            "description": item.description,
                            "verification_rubric": item.verificationRubric,
                            "importance_weight": item.importanceWeight,
                        }
                        for item in competency.criteria
                    ],
                }
            )
            definition = write_semantic_definition(identity.id, definition_input, db)
        definitions[competency.stableKey] = definition
        for item in competency.criteria:
            criterion_identity = db.scalar(
                select(CriterionIdentity).where(
                    CriterionIdentity.competency_identity_id == identity.id,
                    CriterionIdentity.stable_key == item.stableKey,
                )
            )
            assert criterion_identity is not None
            _claim(
                db,
                revision,
                pending_owned,
                "criterion",
                competency.stableKey,
                item.stableKey,
                criterion_identity.id,
                {
                    "competency": competency.stableKey,
                    "level": item.levelKey,
                    "rule": item.demonstrationRule,
                    "requirementType": item.requirementType,
                },
            )
            criterion_identities[f"{competency.stableKey}::{item.stableKey}"] = criterion_identity
            criterion = db.scalar(
                select(CriterionDefinition).where(
                    CriterionDefinition.criterion_identity_id == criterion_identity.id,
                    CriterionDefinition.semantic_definition_id == definition.id,
                )
            )
            if criterion is None:
                _fail("MASTER_IMPORT_LEDGER_INVALID", "A selected criterion definition is missing.")
            criteria[f"{competency.stableKey}::{item.stableKey}"] = criterion
        selected["competencies"][competency.stableKey] = {
            "identityId": identity.id,
            "versionId": definition.id,
            "authoredHash": authored_hash,
        }
        active = db.get(ActiveCompetencyDefinitionState, identity.id)
        if active is None or active.semantic_definition_id != definition.id:
            write_semantic_activation(
                identity.id,
                definition.id,
                ActivationRequest(
                    reason="Master Import activation",
                    source=SOURCE,
                    idempotency_key=f"mi:{record.package_id}:definition:{identity.id}",
                ),
                db,
            )
    profile = payload.targetProfile
    profile_root = db.scalar(
        select(TargetProfile).where(TargetProfile.stable_key == profile.stableKey)
    )
    _existing_owned(
        db,
        payload.lineageKey,
        "profile",
        "global",
        profile.stableKey,
        profile_root.id if profile_root else None,
    )
    if profile_root is None:
        profile_root = TargetProfile(stable_key=profile.stableKey, creation_source=SOURCE)
        db.add(profile_root)
        db.flush()
    _claim(
        db,
        revision,
        pending_owned,
        "profile",
        "global",
        profile.stableKey,
        profile_root.id,
        {"stableKey": profile.stableKey},
    )
    for target in profile.targets:
        existing_target = db.scalar(
            select(ProfileTargetIdentity).where(
                ProfileTargetIdentity.target_profile_id == profile_root.id,
                ProfileTargetIdentity.stable_key == target.stableKey,
            )
        )
        _existing_owned(
            db,
            payload.lineageKey,
            "profileTarget",
            profile.stableKey,
            target.stableKey,
            existing_target.id if existing_target else None,
        )
    for item in profile.milestones:
        existing_milestone = db.scalar(
            select(MilestoneIdentity).where(
                MilestoneIdentity.target_profile_id == profile_root.id,
                MilestoneIdentity.stable_key == item.stableKey,
            )
        )
        _existing_owned(
            db,
            payload.lineageKey,
            "profileMilestone",
            profile.stableKey,
            item.stableKey,
            existing_milestone.id if existing_milestone else None,
        )
    for item in profile.readinessGates:
        existing_gate = db.scalar(
            select(ReadinessGateIdentity).where(
                ReadinessGateIdentity.target_profile_id == profile_root.id,
                ReadinessGateIdentity.stable_key == item.stableKey,
            )
        )
        _existing_owned(
            db,
            payload.lineageKey,
            "readinessGate",
            profile.stableKey,
            item.stableKey,
            existing_gate.id if existing_gate else None,
        )
    profile_hash = _section_hash(profile.model_dump(mode="json"))
    prior_profile = prior_selected.get("profile")
    if prior_profile and prior_profile["authoredHash"] == profile_hash:
        profile_version = db.get(TargetProfileVersion, prior_profile["versionId"])
    else:
        profile_input = TargetProfileVersionCreate.model_validate(
            {
                "title": profile.title,
                "description": profile.description,
                "creation_source": SOURCE,
                "effective_at": effective,
                "domains": [
                    {
                        "stable_key": item.stableKey,
                        "title": item.title,
                        "description": item.description,
                        "minimum_percent": item.minimumPercent,
                        "maximum_percent": item.maximumPercent,
                        "order_index": item.orderIndex,
                    }
                    for item in profile.domains
                ],
                "targets": [
                    {
                        "stable_key": item.stableKey,
                        "competency_identity_id": identities[item.competencyRef].id,
                        "dimension_key": None,
                        "domain_stable_key": item.domainRef,
                        "scale_stable_key": "technical",
                        "scale_version": "v1",
                        "target_level_stable_key": item.levelKey,
                        "priority": item.priority,
                        "target_date": item.targetDate,
                        "target_month": item.targetMonth,
                        "date_interpretation": item.dateInterpretation,
                        "freshness_override_days": item.freshnessOverrideDays,
                    }
                    for item in profile.targets
                ],
                "milestones": [
                    {
                        "stable_key": item.stableKey,
                        "title": item.title,
                        "description": item.description,
                        "target_date": item.targetDate,
                        "order_index": item.orderIndex,
                        "target_stable_keys": item.targetRefs,
                    }
                    for item in profile.milestones
                ],
                "readiness_gates": [
                    {
                        "stable_key": item.stableKey,
                        "title": item.title,
                        "effect": item.effect,
                        "order_index": item.orderIndex,
                        "milestone_stable_key": item.milestoneRef,
                        "target_stable_keys": item.targetRefs,
                        "predicates": [
                            {
                                "predicate_type": pred.predicateType,
                                "requirement_type": pred.requirementType,
                                "order_index": pred.orderIndex,
                                "subject": (
                                    {
                                        "competencyIdentityId": identities[
                                            pred.competencyRef or ""
                                        ].id,
                                        "dimensionKey": None,
                                        "scaleStableKey": "technical",
                                        "scaleVersion": "v1",
                                        "levelStableKey": pred.levelKey,
                                    }
                                    if pred.predicateType == "capability_at_least"
                                    else {
                                        "criterionIdentityId": criterion_identities[
                                            pred.criterionRef or ""
                                        ].id
                                    }
                                ),
                            }
                            for pred in item.predicates
                        ],
                    }
                    for item in profile.readinessGates
                ],
            }
        )
        profile_version = _create_profile_version(db, profile_root, profile_input)
    if profile_version is None:
        _fail("MASTER_IMPORT_LEDGER_INVALID", "A selected Profile version is missing.")
    selected["profile"] = {
        "rootId": profile_root.id,
        "versionId": profile_version.id,
        "authoredHash": profile_hash,
    }
    for item in profile.domains:
        _claim(
            db,
            revision,
            pending_owned,
            "profileDomain",
            profile.stableKey,
            item.stableKey,
            profile_root.id,
            {"profile": profile.stableKey, "domain": item.stableKey},
        )
    for item in profile.targets:
        row = db.scalar(
            select(ProfileTargetIdentity).where(
                ProfileTargetIdentity.target_profile_id == profile_root.id,
                ProfileTargetIdentity.stable_key == item.stableKey,
            )
        )
        assert row is not None
        _claim(
            db,
            revision,
            pending_owned,
            "profileTarget",
            profile.stableKey,
            item.stableKey,
            row.id,
            {"competency": item.competencyRef},
        )
    for item in profile.milestones:
        row = db.scalar(
            select(MilestoneIdentity).where(
                MilestoneIdentity.target_profile_id == profile_root.id,
                MilestoneIdentity.stable_key == item.stableKey,
            )
        )
        assert row is not None
        _claim(
            db,
            revision,
            pending_owned,
            "profileMilestone",
            profile.stableKey,
            item.stableKey,
            row.id,
            {"profile": profile.stableKey},
        )
    for item in profile.readinessGates:
        row = db.scalar(
            select(ReadinessGateIdentity).where(
                ReadinessGateIdentity.target_profile_id == profile_root.id,
                ReadinessGateIdentity.stable_key == item.stableKey,
            )
        )
        assert row is not None
        _claim(
            db,
            revision,
            pending_owned,
            "readinessGate",
            profile.stableKey,
            item.stableKey,
            row.id,
            {"profile": profile.stableKey},
        )
    active_profile = db.get(ActiveTargetProfileState, 1)
    if active_profile is None or active_profile.target_profile_version_id != profile_version.id:
        write_target_profile_activation(
            profile_root.id,
            profile_version.id,
            ActivationRequest(
                reason="Master Import activation",
                source=SOURCE,
                idempotency_key=f"mi:{record.package_id}:profile",
            ),
            db,
        )
    curriculum = payload.curriculum
    curriculum_root = db.scalar(
        select(Curriculum).where(Curriculum.stable_key == curriculum.stableKey)
    )
    _existing_owned(
        db,
        payload.lineageKey,
        "curriculum",
        "global",
        curriculum.stableKey,
        curriculum_root.id if curriculum_root else None,
    )
    if curriculum_root is None:
        curriculum_root = Curriculum(stable_key=curriculum.stableKey, creation_source=SOURCE)
        db.add(curriculum_root)
        db.flush()
    _claim(
        db,
        revision,
        pending_owned,
        "curriculum",
        "global",
        curriculum.stableKey,
        curriculum_root.id,
        {"stableKey": curriculum.stableKey},
    )
    for item in curriculum.objectives:
        old = db.scalar(
            select(CurriculumObjectiveIdentity).where(
                CurriculumObjectiveIdentity.curriculum_id == curriculum_root.id,
                CurriculumObjectiveIdentity.stable_key == item.stableKey,
            )
        )
        _existing_owned(
            db,
            payload.lineageKey,
            "objective",
            curriculum.stableKey,
            item.stableKey,
            old.id if old else None,
        )
    for item in curriculum.units:
        old = db.scalar(
            select(LearningUnitIdentity).where(
                LearningUnitIdentity.curriculum_id == curriculum_root.id,
                LearningUnitIdentity.stable_key == item.stableKey,
            )
        )
        _existing_owned(
            db,
            payload.lineageKey,
            "unit",
            curriculum.stableKey,
            item.stableKey,
            old.id if old else None,
        )
    for item in curriculum.assessmentRubrics:
        old = db.scalar(
            select(AssessmentRubricIdentity).where(
                AssessmentRubricIdentity.curriculum_id == curriculum_root.id,
                AssessmentRubricIdentity.stable_key == item.stableKey,
            )
        )
        _existing_owned(
            db,
            payload.lineageKey,
            "rubric",
            curriculum.stableKey,
            item.stableKey,
            old.id if old else None,
        )
    definition_pins = {key: value.id for key, value in definitions.items()}
    curriculum_hash = _section_hash(
        {"authored": curriculum.model_dump(mode="json"), "definitionPins": definition_pins}
    )
    prior_curriculum = prior_selected.get("curriculum")
    if prior_curriculum and prior_curriculum["authoredHash"] == curriculum_hash:
        curriculum_version = db.get(CurriculumVersion, prior_curriculum["versionId"])
    else:

        def subject(req: Any) -> dict[str, Any]:
            if req.requirementType == "capability_at_least":
                return {
                    "semanticDefinitionId": definitions[req.competencyRef].id,
                    "scaleVersionId": scale.id,
                    "levelId": levels[req.levelKey].id,
                    "dimensionId": None,
                }
            if req.requirementType == "criterion_demonstrated":
                return {"criterionDefinitionId": criteria[req.criterionRef].id}
            if req.requirementType == "learning_unit_completed":
                return {"learningUnitStableKey": req.unitRef}
            if req.requirementType == "resource_available":
                return {"resourceKey": req.resourceKey}
            return {"constraintKey": req.constraintKey, "expectedValue": req.expectedValue}

        curriculum_input = CurriculumVersionInput.model_validate(
            {
                "title": curriculum.title,
                "description": curriculum.description,
                "effective_at": effective,
                "creation_source": SOURCE,
                "objectives": [
                    {
                        "stable_key": item.stableKey,
                        "title": item.title,
                        "description": item.description,
                        "order_index": item.orderIndex,
                    }
                    for item in curriculum.objectives
                ],
                "units": [
                    {
                        "stable_key": item.stableKey,
                        "objective_stable_key": item.objectiveRef,
                        "kind": item.kind,
                        "title": item.title,
                        "description": item.description,
                        "action": {
                            "kind": item.kind,
                            **(
                                {"resource_reference": item.action.resourceReference}
                                if item.kind == "resource"
                                else {"verification_method": item.action.verificationMethod}
                                if item.kind == "verification_template"
                                else {"instructions": item.action.instructions}
                            ),
                        },
                        "status": item.status,
                        "provenance": SOURCE,
                        "order_index": item.orderIndex,
                        "minimum_useful_duration_ms": item.minimumUsefulDurationMs,
                        "preferred_duration_ms": item.preferredDurationMs,
                        "maximum_useful_duration_ms": item.maximumUsefulDurationMs,
                        "targets": [
                            {
                                "semantic_definition_id": definitions[target.competencyRef].id,
                                "criterion_definition_id": criteria[target.criterionRef].id
                                if target.criterionRef
                                else None,
                                "scale_version_id": scale.id,
                                "dimension_id": None,
                                "intended_learning_outcome": target.intendedLearningOutcome,
                                "minimum_level_id": levels[target.minimumLevelKey].id
                                if target.minimumLevelKey
                                else None,
                                "maximum_level_id": levels[target.maximumLevelKey].id
                                if target.maximumLevelKey
                                else None,
                                "supports_unassessed": target.supportsUnassessed,
                                "role": target.role,
                                "order_index": target.orderIndex,
                            }
                            for target in item.targets
                        ],
                        "requirements": [
                            {
                                "stable_key": req.stableKey,
                                "requirement_type": req.requirementType,
                                "effect": req.effect,
                                "scope": req.scope,
                                "subject": subject(req),
                                "order_index": req.orderIndex,
                            }
                            for req in item.requirements
                        ],
                        "evidence_opportunities": [
                            {
                                "stable_key": opportunity.stableKey,
                                "evidence_kind": opportunity.evidenceKind,
                                "intended_strengths": opportunity.intendedStrengths,
                                "intended_independence_modes": (
                                    opportunity.intendedIndependenceModes
                                ),
                                "requires_actual_activity": opportunity.requiresActualActivity,
                                "requires_artifact": opportunity.requiresArtifact,
                                "order_index": opportunity.orderIndex,
                            }
                            for opportunity in item.evidenceOpportunities
                        ],
                    }
                    for item in curriculum.units
                ],
                "assessment_rubrics": [
                    {
                        "stable_key": item.stableKey,
                        "title": item.title,
                        "instructions": item.instructions,
                        "rubric": {
                            "policyVersion": item.rubric.policyVersion,
                            "criteria": [
                                {
                                    "criterionDefinitionId": criteria[part.criterionRef].id,
                                    "weight": part.weight,
                                    "description": part.description,
                                }
                                for part in item.rubric.criteria
                            ],
                        },
                        "semantic_definition_id": definitions[item.competencyRef].id,
                        "criterion_definition_id": criteria[item.criterionRef].id
                        if item.criterionRef
                        else None,
                    }
                    for item in curriculum.assessmentRubrics
                ],
            }
        )
        curriculum_version = create_curriculum_version(db, curriculum_root, curriculum_input)
    if curriculum_version is None:
        _fail("MASTER_IMPORT_LEDGER_INVALID", "A selected Curriculum version is missing.")
    selected["curriculum"] = {
        "rootId": curriculum_root.id,
        "versionId": curriculum_version.id,
        "authoredHash": curriculum_hash,
    }
    for item in curriculum.objectives:
        row = db.scalar(
            select(CurriculumObjectiveIdentity).where(
                CurriculumObjectiveIdentity.curriculum_id == curriculum_root.id,
                CurriculumObjectiveIdentity.stable_key == item.stableKey,
            )
        )
        assert row is not None
        _claim(
            db,
            revision,
            pending_owned,
            "objective",
            curriculum.stableKey,
            item.stableKey,
            row.id,
            {"curriculum": curriculum.stableKey},
        )
    for item in curriculum.units:
        row = db.scalar(
            select(LearningUnitIdentity).where(
                LearningUnitIdentity.curriculum_id == curriculum_root.id,
                LearningUnitIdentity.stable_key == item.stableKey,
            )
        )
        assert row is not None
        _claim(
            db,
            revision,
            pending_owned,
            "unit",
            curriculum.stableKey,
            item.stableKey,
            row.id,
            {
                "kind": item.kind,
                "primaryTargets": [
                    list(pair)
                    for pair in sorted(
                        (
                            (target.competencyRef, target.criterionRef)
                            for target in item.targets
                            if target.role == "primary"
                        ),
                        key=lambda pair: (pair[0], pair[1] or ""),
                    )
                ],
            },
        )
        for requirement in item.requirements:
            _claim(
                db,
                revision,
                pending_owned,
                "requirement",
                item.stableKey,
                requirement.stableKey,
                row.id,
                {"type": requirement.requirementType, "scope": requirement.scope},
            )
        for opportunity in item.evidenceOpportunities:
            _claim(
                db,
                revision,
                pending_owned,
                "opportunity",
                item.stableKey,
                opportunity.stableKey,
                row.id,
                {"kind": opportunity.evidenceKind},
            )
    for item in curriculum.assessmentRubrics:
        row = db.scalar(
            select(AssessmentRubricIdentity).where(
                AssessmentRubricIdentity.curriculum_id == curriculum_root.id,
                AssessmentRubricIdentity.stable_key == item.stableKey,
            )
        )
        assert row is not None
        _claim(
            db,
            revision,
            pending_owned,
            "rubric",
            curriculum.stableKey,
            item.stableKey,
            row.id,
            {"competency": item.competencyRef, "criterion": item.criterionRef},
        )
    active_curriculum = db.get(ActiveCurriculumVersionState, curriculum_root.id)
    if (
        active_curriculum is None
        or active_curriculum.curriculum_version_id != curriculum_version.id
    ):
        write_curriculum_activation(
            curriculum_root.id,
            curriculum_version.id,
            CurriculumActivationInput(
                reason="Master Import activation",
                source=SOURCE,
                idempotency_key=f"mi:{record.package_id}:curriculum",
            ),
            db,
        )
    graph = payload.learningGraph
    graph_root = db.scalar(select(LearningGraph).where(LearningGraph.stable_key == graph.stableKey))
    _existing_owned(
        db,
        payload.lineageKey,
        "graph",
        "global",
        graph.stableKey,
        graph_root.id if graph_root else None,
    )
    if graph_root is None:
        graph_root = LearningGraph(stable_key=graph.stableKey, creation_source=SOURCE)
        db.add(graph_root)
        db.flush()
    _claim(
        db,
        revision,
        pending_owned,
        "graph",
        "global",
        graph.stableKey,
        graph_root.id,
        {"stableKey": graph.stableKey},
    )
    for edge in graph.edges:
        old = db.scalar(
            select(CompetencyEdgeIdentity).where(
                CompetencyEdgeIdentity.learning_graph_id == graph_root.id,
                CompetencyEdgeIdentity.stable_key == edge.stableKey,
            )
        )
        _existing_owned(
            db, payload.lineageKey, "edge", graph.stableKey, edge.stableKey, old.id if old else None
        )
    graph_hash = _section_hash(
        {"authored": graph.model_dump(mode="json"), "definitionPins": definition_pins}
    )
    prior_graph = prior_selected.get("graph")
    if prior_graph and prior_graph["authoredHash"] == graph_hash:
        graph_version = db.get(LearningGraphVersion, prior_graph["versionId"])
    else:
        graph_input = LearningGraphVersionInput.model_validate(
            {
                "title": graph.title,
                "description": graph.description,
                "effective_at": effective,
                "creation_source": SOURCE,
                "edges": [
                    {
                        "stable_key": edge.stableKey,
                        "edge_type": edge.edgeType,
                        "source_semantic_definition_id": definitions[edge.sourceRef].id,
                        "target_semantic_definition_id": definitions[edge.targetRef].id,
                        "satisfaction_scope_key": "overall",
                        "requirement": (
                            {
                                "kind": "capability_at_least",
                                "scale_version_id": scale.id,
                                "dimension_id": None,
                                "minimum_level_id": levels[
                                    edge.requirement.minimumLevelKey or ""
                                ].id,
                                "review_requirement": "none",
                            }
                            if edge.requirement and edge.requirement.kind == "capability_at_least"
                            else {
                                "kind": "criterion_set_demonstrated",
                                "criterion_definition_ids": [
                                    criteria[ref].id
                                    for ref in (edge.requirement.criterionRefs or [])
                                ],
                            }
                            if edge.requirement
                            else None
                        ),
                        "provenance": SOURCE,
                        "meaning_key": edge.meaningKey,
                        "order_index": edge.orderIndex,
                    }
                    for edge in graph.edges
                ],
            }
        )
        graph_version = create_graph_version(db, graph_root, graph_input, commit=False)
    if graph_version is None:
        _fail("MASTER_IMPORT_LEDGER_INVALID", "A selected Graph version is missing.")
    selected["graph"] = {
        "rootId": graph_root.id,
        "versionId": graph_version.id,
        "authoredHash": graph_hash,
    }
    for edge in graph.edges:
        row = db.scalar(
            select(CompetencyEdgeIdentity).where(
                CompetencyEdgeIdentity.learning_graph_id == graph_root.id,
                CompetencyEdgeIdentity.stable_key == edge.stableKey,
            )
        )
        assert row is not None
        _claim(
            db,
            revision,
            pending_owned,
            "edge",
            graph.stableKey,
            edge.stableKey,
            row.id,
            {
                "type": edge.edgeType,
                "source": edge.sourceRef,
                "target": edge.targetRef,
                "meaningKey": edge.meaningKey,
            },
        )
    active_graph = db.get(ActiveLearningGraphState, 1)
    if active_graph is None or active_graph.learning_graph_version_id != graph_version.id:
        activate_graph_version(
            db,
            graph_root,
            graph_version,
            source=SOURCE,
            reason="Master Import activation",
            idempotency_key=f"mi:{record.package_id}:graph",
            commit=False,
        )
    revision.selected_versions_json = json.dumps(selected, sort_keys=True, separators=(",", ":"))
    previous_activation = json.loads(prior.activation_json) if prior else {}

    def event_id(model: Any, idempotency_key: str, fallback: str | None) -> str:
        value = db.scalar(select(model.id).where(model.idempotency_key == idempotency_key))
        if value is None:
            if fallback is None:
                _fail(
                    "MASTER_IMPORT_ACTIVATION_INVALID", "An expected activation event is missing."
                )
            return fallback
        return cast(str, value)

    activation = {
        "competencies": {
            key: {
                "versionId": entry["versionId"],
                "eventId": event_id(
                    CompetencyDefinitionActivationEvent,
                    f"mi:{record.package_id}:definition:{entry['identityId']}",
                    previous_activation.get("competencies", {}).get(key, {}).get("eventId"),
                ),
            }
            for key, entry in selected["competencies"].items()
        },
        "targetProfile": {
            "versionId": profile_version.id,
            "eventId": event_id(
                TargetProfileActivationEvent,
                f"mi:{record.package_id}:profile",
                previous_activation.get("targetProfile", {}).get("eventId"),
            ),
        },
        "curriculum": {
            "versionId": curriculum_version.id,
            "eventId": event_id(
                CurriculumActivationEvent,
                f"mi:{record.package_id}:curriculum",
                previous_activation.get("curriculum", {}).get("eventId"),
            ),
        },
        "learningGraph": {
            "versionId": graph_version.id,
            "eventId": event_id(
                LearningGraphActivationEvent,
                f"mi:{record.package_id}:graph",
                previous_activation.get("learningGraph", {}).get("eventId"),
            ),
        },
    }
    revision.activation_json = json.dumps(activation, sort_keys=True, separators=(",", ":"))
    db.add(revision)
    db.flush()
    db.add_all(pending_owned)
    db.flush()
    return {**prepared, "selectedVersions": selected}
