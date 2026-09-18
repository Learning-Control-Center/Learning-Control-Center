from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pydantic import TypeAdapter, ValidationError

from app.analysis.v3.contracts import PublicAnalysisSnapshotDTO
from app.curriculum.contracts import (
    CurriculumAvailabilityPublicDTO,
    CurriculumCatalogPublicDTO,
)
from app.errors import AppError
from app.learning_graph.contracts import ActiveLearningGraphProjectionPublicDTO
from app.profile_views import ActiveProfileProjectionPublicDTO
from app.projects.contracts import ProjectCatalogPublicDTO
from app.recommendation.v2.contracts import CandidateInputDTO, candidate_from_payload
from app.recommendation.v2.policy import registered_candidate_builder

_SNAPSHOT = TypeAdapter(PublicAnalysisSnapshotDTO)
_PROFILE = TypeAdapter(ActiveProfileProjectionPublicDTO)
_GRAPH = TypeAdapter(ActiveLearningGraphProjectionPublicDTO)
_CURRICULUM = TypeAdapter(CurriculumCatalogPublicDTO)
_AVAILABILITY = TypeAdapter(tuple[CurriculumAvailabilityPublicDTO, ...])
_PROJECTS = TypeAdapter(ProjectCatalogPublicDTO)


def regenerate_candidates_from_frozen_input(
    frozen_input: dict[str, Any],
    policy_registry_version: str,
) -> tuple[CandidateInputDTO, ...]:
    """Replay candidate generation from the complete immutable public input DTOs."""
    required = {
        "analysisPublicSnapshot",
        "targetProfileVersion",
        "learningGraph",
        "curriculum",
        "curriculumAvailability",
        "projects",
        "userConstraints",
        "availableTimeMs",
        "candidates",
        "analysisSnapshot",
    }
    if set(frozen_input) != required:
        raise AppError(
            422,
            "RECOMMENDATION_FROZEN_INPUT_INVALID",
            "The completed Recommendation input envelope has an invalid shape.",
        )
    try:
        snapshot = _SNAPSHOT.validate_python(frozen_input["analysisPublicSnapshot"])
        profile_payload = frozen_input["targetProfileVersion"]
        profile = _PROFILE.validate_python(profile_payload) if profile_payload is not None else None
        graph_payload = frozen_input["learningGraph"]
        graph = _GRAPH.validate_python(graph_payload) if graph_payload is not None else None
        curriculum = _CURRICULUM.validate_python(frozen_input["curriculum"])
        availability = _AVAILABILITY.validate_python(frozen_input["curriculumAvailability"])
        projects = _PROJECTS.validate_python(frozen_input["projects"])
        persisted = tuple(candidate_from_payload(item) for item in frozen_input["candidates"])
    except (ValidationError, KeyError, TypeError, ValueError) as exc:
        raise AppError(
            422,
            "RECOMMENDATION_FROZEN_INPUT_INVALID",
            "The frozen Recommendation input DTOs are invalid.",
        ) from exc
    try:
        candidate_builder = registered_candidate_builder(policy_registry_version)
    except KeyError as exc:
        raise AppError(
            409,
            "RECOMMENDATION_POLICY_UNAVAILABLE",
            "The exact Recommendation candidate policy is unavailable.",
        ) from exc
    regenerated = (
        candidate_builder(
            snapshot,
            profile,
            curriculum,
            availability,
            projects,
            graph,
            tuple(
                (
                    str(item["sourceType"]),
                    str(item["sourceEntityId"]),
                    str(item["cost"]),
                )
                for item in frozen_input["userConstraints"].get("contextCosts", [])
            ),
        )
        if profile is not None
        else ()
    )
    if [asdict(item) for item in regenerated] != [asdict(item) for item in persisted]:
        raise AppError(
            422,
            "RECOMMENDATION_CANDIDATE_REPLAY_MISMATCH",
            "Frozen Recommendation candidates do not match deterministic generation.",
        )
    return regenerated
