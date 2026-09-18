from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RequirementState(StrEnum):
    MET = "met"
    NOT_MET = "not_met"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RequirementDefinitionDTO:
    stable_key: str
    requirement_type: str
    effect: str
    subject_json: str
    order_index: int


@dataclass(frozen=True)
class RequirementFactDTO:
    stable_key: str
    state: RequirementState
    reason_code: str


@dataclass(frozen=True)
class RequirementEvaluationDTO:
    stable_key: str
    requirement_type: str
    effect: str
    state: RequirementState
    reason_code: str
    order_index: int


def evaluate_requirements(
    definitions: tuple[RequirementDefinitionDTO, ...],
    facts: tuple[RequirementFactDTO, ...],
) -> tuple[RequirementEvaluationDTO, ...]:
    """Join declared requirements to public facts without consulting persistence."""
    by_key = {fact.stable_key: fact for fact in facts}
    return tuple(
        RequirementEvaluationDTO(
            stable_key=item.stable_key,
            requirement_type=item.requirement_type,
            effect=item.effect,
            state=(
                fact.state if (fact := by_key.get(item.stable_key)) else RequirementState.UNKNOWN
            ),
            reason_code=(fact.reason_code if fact else "REQUIREMENT_FACT_MISSING"),
            order_index=item.order_index,
        )
        for item in sorted(definitions, key=lambda value: (value.order_index, value.stable_key))
    )
