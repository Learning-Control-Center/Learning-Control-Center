"""Strict, human-authored Master Import V1 wire models.

All references are semantic stable keys. UUIDs are absent from this contract.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas import DemonstrationRule, validate_external_reference

Key = Annotated[
    str, Field(min_length=1, max_length=255, pattern=r"^[a-z][a-z0-9]*(?:[._/-][a-z0-9]+)*$")
]
CriterionRef = Annotated[
    str,
    Field(
        min_length=3,
        max_length=511,
        pattern=r"^[a-z][a-z0-9]*(?:[._/-][a-z0-9]+)*::[a-z][a-z0-9]*(?:[._/-][a-z0-9]+)*$",
    ),
]
MAX_AUTHORED_ENTITIES = 40_000
MAX_AUTHORED_REFERENCES = 200_000


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


_UTC_INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|\+00:00)\Z")


def is_utc_rfc3339(value: object) -> bool:
    if not isinstance(value, str) or _UTC_INSTANT.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == UTC.utcoffset(None)


class Criterion(Strict):
    stableKey: Key
    levelKey: Key
    requirementType: Literal["required", "important", "supporting"]
    demonstrationRule: DemonstrationRule
    description: str = Field(min_length=1)
    verificationRubric: str | None = None
    importanceWeight: int | None = Field(default=None, ge=1, le=5)


class Competency(Strict):
    stableKey: Key
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    scope: str = Field(min_length=1)
    criteria: list[Criterion] = Field(min_length=1)
    freshnessCurrentThroughDays: int | None = Field(default=None, ge=0)
    freshnessStaleAfterDays: int | None = Field(default=None, ge=0)


class Domain(Strict):
    stableKey: Key
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    minimumPercent: int | None = Field(default=None, ge=0, le=100)
    maximumPercent: int | None = Field(default=None, ge=0, le=100)
    orderIndex: int = Field(ge=0)


class Target(Strict):
    stableKey: Key
    competencyRef: Key
    domainRef: Key
    levelKey: Key
    priority: Literal["critical", "core", "important", "supporting", "optional"]
    targetDate: str | None = None
    targetMonth: str | None = None
    dateInterpretation: str | None = None
    freshnessOverrideDays: int | None = Field(default=None, gt=0)


class Milestone(Strict):
    stableKey: Key
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    targetDate: str | None = None
    orderIndex: int = Field(ge=0)
    targetRefs: list[Key] = Field(default_factory=list)


class GatePredicate(Strict):
    predicateType: Literal["capability_at_least", "criterion_demonstrated"]
    requirementType: Literal["required", "supporting"]
    orderIndex: int = Field(ge=0)
    competencyRef: Key | None = None
    criterionRef: CriterionRef | None = None
    levelKey: Key | None = None

    @model_validator(mode="after")
    def exact_shape(self) -> GatePredicate:
        if self.predicateType == "capability_at_least":
            if self.competencyRef is None or self.levelKey is None or self.criterionRef is not None:
                raise ValueError("Capability gate predicates require competencyRef and levelKey")
        elif (
            self.criterionRef is None or self.competencyRef is not None or self.levelKey is not None
        ):
            raise ValueError("Criterion gate predicates require criterionRef only")
        return self


class Gate(Strict):
    stableKey: Key
    title: str = Field(min_length=1, max_length=255)
    effect: Literal["hard_eligibility", "urgency", "display_only"]
    orderIndex: int = Field(ge=0)
    milestoneRef: Key | None = None
    targetRefs: list[Key] = Field(default_factory=list)
    predicates: list[GatePredicate] = Field(default_factory=list)


class Profile(Strict):
    stableKey: Key
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    domains: list[Domain] = Field(min_length=1)
    targets: list[Target] = Field(min_length=1)
    milestones: list[Milestone] = Field(default_factory=list)
    readinessGates: list[Gate] = Field(default_factory=list)


class Objective(Strict):
    stableKey: Key
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    orderIndex: int = Field(ge=0)


class UnitTarget(Strict):
    competencyRef: Key
    criterionRef: CriterionRef | None = None
    intendedLearningOutcome: str = Field(min_length=1, max_length=2000)
    minimumLevelKey: Key | None = None
    maximumLevelKey: Key | None = None
    supportsUnassessed: bool = False
    role: Literal["primary", "secondary", "supporting"] = "primary"
    orderIndex: int = Field(ge=0)


class Requirement(Strict):
    stableKey: Key
    requirementType: Literal[
        "capability_at_least",
        "criterion_demonstrated",
        "learning_unit_completed",
        "resource_available",
        "user_constraint",
    ]
    effect: Literal["hard", "soft"]
    scope: Literal["learner", "curriculum", "environment", "user"]
    orderIndex: int = Field(ge=0)
    competencyRef: Key | None = None
    criterionRef: CriterionRef | None = None
    unitRef: Key | None = None
    levelKey: Key | None = None
    resourceKey: Key | None = None
    constraintKey: Key | None = None
    expectedValue: str | None = None

    @model_validator(mode="after")
    def exact_shape(self) -> Requirement:
        expected = {
            "capability_at_least": {"competencyRef", "levelKey"},
            "criterion_demonstrated": {"criterionRef"},
            "learning_unit_completed": {"unitRef"},
            "resource_available": {"resourceKey"},
            "user_constraint": {"constraintKey", "expectedValue"},
        }[self.requirementType]
        present = {
            name
            for name in (
                "competencyRef",
                "criterionRef",
                "unitRef",
                "levelKey",
                "resourceKey",
                "constraintKey",
                "expectedValue",
            )
            if getattr(self, name) is not None
        }
        if present != expected:
            raise ValueError("Requirement references do not match requirementType")
        return self


class Opportunity(Strict):
    stableKey: Key
    evidenceKind: Literal[
        "session", "verification", "project", "code", "assessment", "manual", "review"
    ]
    intendedStrengths: list[Literal["weak", "moderate", "strong"]] = Field(default_factory=list)
    intendedIndependenceModes: list[
        Literal["guided", "assisted", "independent", "not_applicable"]
    ] = Field(default_factory=list)
    requiresActualActivity: bool = True
    requiresArtifact: bool = False
    orderIndex: int = Field(ge=0)


class UnitAction(Strict):
    kind: Literal["resource", "exercise", "practice_task", "verification_template"]
    resourceReference: str | None = None
    instructions: str | None = None
    verificationMethod: str | None = None

    @model_validator(mode="after")
    def exact_shape(self) -> UnitAction:
        required = {
            "resource": "resourceReference",
            "exercise": "instructions",
            "practice_task": "instructions",
            "verification_template": "verificationMethod",
        }[self.kind]
        present = {
            name
            for name in ("resourceReference", "instructions", "verificationMethod")
            if getattr(self, name) is not None
        }
        if present != {required} or not getattr(self, required):
            raise ValueError("Learning Unit action does not match kind")
        if self.resourceReference is not None:
            validate_external_reference(self.resourceReference)
        return self


class Unit(Strict):
    stableKey: Key
    objectiveRef: Key | None = None
    kind: Literal["resource", "exercise", "practice_task", "verification_template"]
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    action: UnitAction
    status: Literal["active", "archived"] = "active"
    orderIndex: int = Field(ge=0)
    minimumUsefulDurationMs: int | None = None
    preferredDurationMs: int | None = None
    maximumUsefulDurationMs: int | None = None
    targets: list[UnitTarget] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    evidenceOpportunities: list[Opportunity] = Field(default_factory=list)

    @model_validator(mode="after")
    def action_matches_kind(self) -> Unit:
        if self.action.kind != self.kind:
            raise ValueError("Learning Unit kind and action kind differ")
        return self


class RubricCriterion(Strict):
    criterionRef: CriterionRef
    weight: int = Field(ge=1, le=100)
    description: str = Field(min_length=1)


class RubricPolicy(Strict):
    policyVersion: Literal["master-rubric-v1"]
    criteria: list[RubricCriterion] = Field(min_length=1)

    @model_validator(mode="after")
    def complete_weights(self) -> RubricPolicy:
        references = [item.criterionRef for item in self.criteria]
        if (
            len(set(references)) != len(references)
            or sum(item.weight for item in self.criteria) != 100
        ):
            raise ValueError("Rubric criteria must be unique and weights must total 100")
        return self


class Rubric(Strict):
    stableKey: Key
    title: str = Field(min_length=1, max_length=255)
    instructions: str = Field(min_length=1)
    competencyRef: Key
    criterionRef: CriterionRef | None = None
    rubric: RubricPolicy


class Curriculum(Strict):
    stableKey: Key
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    objectives: list[Objective] = Field(default_factory=list)
    units: list[Unit] = Field(min_length=1)
    assessmentRubrics: list[Rubric] = Field(default_factory=list)


class EdgeRequirement(Strict):
    kind: Literal["capability_at_least", "criterion_set_demonstrated"]
    minimumLevelKey: Key | None = None
    criterionRefs: list[CriterionRef] | None = None

    @model_validator(mode="after")
    def exact_shape(self) -> EdgeRequirement:
        if self.kind == "capability_at_least" and (
            self.minimumLevelKey is None or self.criterionRefs is not None
        ):
            raise ValueError("Capability edge requires minimumLevelKey")
        if self.kind == "criterion_set_demonstrated" and (
            not self.criterionRefs or self.minimumLevelKey is not None
        ):
            raise ValueError("Criterion edge requires criterionRefs")
        return self


class Edge(Strict):
    stableKey: Key
    edgeType: Literal["prerequisite", "recommended_before", "supports", "specialization", "related"]
    sourceRef: Key
    targetRef: Key
    requirement: EdgeRequirement | None = None
    meaningKey: Key = "default"
    orderIndex: int = Field(ge=0)


class Graph(Strict):
    stableKey: Key
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    edges: list[Edge] = Field(default_factory=list)


class ActivationIntent(Strict):
    competencies: bool
    targetProfile: bool
    curriculum: bool
    learningGraph: bool


class Provenance(Strict):
    sourceName: str = Field(min_length=1, max_length=255)
    authoredBy: str = Field(min_length=1, max_length=255)
    sourceRevision: str = Field(min_length=1, max_length=255)


class Removal(Strict):
    entityKind: Literal[
        "criterion",
        "profileTarget",
        "profileDomain",
        "profileMilestone",
        "readinessGate",
        "objective",
        "unit",
        "requirement",
        "opportunity",
        "rubric",
        "edge",
    ]
    stableKey: str = Field(min_length=1, max_length=511)
    reason: str = Field(min_length=1, max_length=1000)


class Payload(Strict):
    lineageKey: Key
    ownerKey: Key
    contentRevision: int = Field(ge=1)
    previousContentDigest: str | None
    canonicalizationVersion: Literal["mi-canon-v1"]
    provenance: Provenance
    effectiveAt: str
    activationIntent: ActivationIntent
    competencies: list[Competency] = Field(min_length=1)
    targetProfile: Profile
    curriculum: Curriculum
    learningGraph: Graph
    initialSpine: list[Key] = Field(min_length=1)
    removedFromActiveVersion: list[Removal]

    @model_validator(mode="after")
    def bounded_authored_structure(self) -> Payload:
        entities = (
            len(self.competencies)
            + sum(len(item.criteria) for item in self.competencies)
            + len(self.targetProfile.domains)
            + len(self.targetProfile.targets)
            + len(self.targetProfile.milestones)
            + len(self.targetProfile.readinessGates)
            + len(self.curriculum.objectives)
            + len(self.curriculum.units)
            + len(self.curriculum.assessmentRubrics)
            + len(self.learningGraph.edges)
            + len(self.removedFromActiveVersion)
            + sum(
                len(item.requirements) + len(item.evidenceOpportunities)
                for item in self.curriculum.units
            )
        )
        references = (
            len(self.initialSpine)
            + sum(len(item.targetRefs) for item in self.targetProfile.milestones)
            + sum(
                len(item.targetRefs) + len(item.predicates)
                for item in self.targetProfile.readinessGates
            )
            + sum(
                len(item.targets) + len(item.requirements) + len(item.evidenceOpportunities)
                for item in self.curriculum.units
            )
            + sum(len(item.rubric.criteria) for item in self.curriculum.assessmentRubrics)
            + sum(
                len(item.requirement.criterionRefs or [])
                for item in self.learningGraph.edges
                if item.requirement is not None
            )
        )
        if entities > MAX_AUTHORED_ENTITIES or references > MAX_AUTHORED_REFERENCES:
            raise ValueError("Master Import authored entity/reference count exceeds its limit")
        return self

    @field_validator("effectiveAt")
    @classmethod
    def aware_utc_time(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("effectiveAt must be an RFC 3339 instant") from exc
        if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(None):
            raise ValueError("effectiveAt must specify UTC")
        return value


def validate_payload(data: dict[str, Any]) -> Payload:
    return Payload.model_validate(data)
