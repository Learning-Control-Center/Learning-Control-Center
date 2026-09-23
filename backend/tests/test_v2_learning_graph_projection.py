from __future__ import annotations

import ast
import copy
import hashlib
import json
import sqlite3
import time
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app import database
from app.determinism import content_hash
from app.errors import AppError
from app.import_export import _apply_portable_restore, _portable_payload, _validate_portable_payload
from app.learning_graph.models import (
    ActiveLearningGraphState,
    CompetencyEdgeDefinition,
    LearningGraph,
    LearningGraphActivationEvent,
)
from app.learning_graph.service import evaluate_edge
from app.models import (
    CapabilityEvaluationRun,
    CriterionDefinition,
    CriterionEvaluationResult,
    ProjectionInvalidation,
    ReviewEvent,
    SemanticCompetencyDefinition,
)
from app.portability.registry import PORTABLE_V10_MANIFEST
from app.roadmap_projection import service as projection_service
from app.roadmap_projection.models import (
    LegacyRoadmapActiveState,
    RoadmapNodePositionOverride,
    RoadmapProjectionCache,
    RoadmapProjectionCheckpoint,
    RoadmapProjectionPreference,
)
from app.today.public import TodayRoadmapOverlayDTO, TodayRoadmapOverlayItemDTO
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session


def _migration_config(database_path: Path) -> Config:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


async def _semantic(
    client: AsyncClient, csrf: str, stable_key: str, title: str
) -> tuple[dict[str, object], dict[str, object]]:
    competency = await client.post(
        "/api/v2/competencies",
        json={"stable_key": stable_key, "creation_source": "test"},
        headers={"X-CSRF-Token": csrf},
    )
    assert competency.status_code == 201, competency.text
    definition = await client.post(
        f"/api/v2/competencies/{competency.json()['id']}/definitions",
        json={
            "title": title,
            "description": f"Semantic definition for {title}",
            "scope": f"Exact scope for {title}",
            "scale_stable_key": "technical",
            "scale_version": "v1",
            "dimension_keys": [],
            "effective_at": "2026-01-01T00:00:00Z",
            "creation_source": "test",
            "criteria": [
                {
                    "stable_key": f"{stable_key}.criterion",
                    "level_stable_key": "independent",
                    "dimension_key": None,
                    "requirement_type": "required",
                    "demonstration_rule": "independent_performance",
                    "description": f"Demonstrate {title}",
                }
            ],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert definition.status_code == 201, definition.text
    activated = await client.post(
        f"/api/v2/competencies/{competency.json()['id']}/definitions/"
        f"{definition.json()['id']}/activate",
        json={
            "reason": "Graph test definition",
            "source": "test",
            "idempotency_key": f"activate-{stable_key}",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert activated.status_code == 200, activated.text
    return competency.json(), definition.json()


async def _active_profile(
    client: AsyncClient,
    csrf: str,
    first_competency_id: str,
    second_competency_id: str,
) -> dict[str, object]:
    response = await client.post(
        "/api/v2/target-profiles",
        json={
            "stable_key": "graph-profile",
            "creation_source": "test",
            "version": {
                "title": "Graph profile",
                "description": "Projection source",
                "creation_source": "test",
                "effective_at": "2026-01-01T00:00:00Z",
                "domains": [
                    {
                        "stable_key": "engineering",
                        "title": "Engineering",
                        "minimum_percent": 100,
                        "maximum_percent": 100,
                        "order_index": 0,
                    }
                ],
                "targets": [
                    {
                        "stable_key": "foundation",
                        "competency_identity_id": first_competency_id,
                        "dimension_key": None,
                        "domain_stable_key": "engineering",
                        "scale_stable_key": "technical",
                        "scale_version": "v1",
                        "target_level_stable_key": "independent",
                        "priority": "core",
                    },
                    {
                        "stable_key": "delivery",
                        "competency_identity_id": second_competency_id,
                        "dimension_key": None,
                        "domain_stable_key": "engineering",
                        "scale_stable_key": "technical",
                        "scale_version": "v1",
                        "target_level_stable_key": "strong",
                        "priority": "important",
                    },
                ],
                "milestones": [
                    {
                        "stable_key": "ready",
                        "title": "Ready",
                        "order_index": 0,
                        "target_stable_keys": ["delivery"],
                    }
                ],
                "readiness_gates": [],
            },
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    version = response.json()
    activated = await client.post(
        f"/api/v2/target-profiles/{version['profileId']}/versions/{version['versionId']}/activate",
        json={
            "reason": "Projection test",
            "source": "test",
            "idempotency_key": "activate-graph-profile",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert activated.status_code == 200, activated.text
    return version


def _edge(
    stable_key: str,
    edge_type: str,
    source_id: str,
    target_id: str,
    order_index: int,
    *,
    requirement: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "stable_key": stable_key,
        "edge_type": edge_type,
        "source_semantic_definition_id": source_id,
        "target_semantic_definition_id": target_id,
        "satisfaction_scope_key": "overall",
        "requirement": requirement,
        "provenance": "owner_curated",
        "meaning_key": stable_key,
        "order_index": order_index,
    }


def _capability_run(
    db: Session,
    *,
    semantic_definition_id: str,
    selected_level_id: str,
    generated_at: int,
    dimension_id: str | None = None,
) -> CapabilityEvaluationRun:
    semantic = db.get(SemanticCompetencyDefinition, semantic_definition_id)
    assert semantic is not None
    run = CapabilityEvaluationRun(
        idempotency_key=f"graph-run-{semantic_definition_id}-{generated_at}",
        competency_identity_id=semantic.competency_identity_id,
        semantic_definition_id=semantic.id,
        scale_version_id=semantic.scale_version_id,
        dimension_id=dimension_id,
        scope_key=f"dimension:{dimension_id}" if dimension_id else "overall",
        cutoff_at=generated_at - 1,
        generated_at=generated_at,
        criterion_policy_version="criterion-evaluation/v1",
        capability_policy_version="capability-evaluation/v1",
        evidence_policy_version="unified-evidence/v1",
        downgrade_policy_version="capability-downgrade/v1",
        evidence_set_hash="a" * 64,
        input_payload_json="{}",
        input_hash="b" * 64,
        selected_level_id=selected_level_id,
        assessment_status="evaluated",
        aggregate_confidence="high",
        confidence_facts_json="{}",
        downgrade_cause=None,
        decisive_evidence_ids_json="[]",
        passed_level_ids_json=json.dumps([selected_level_id]),
        reasons_json="[]",
        output_hash="c" * 64,
    )
    db.add(run)
    db.flush()
    return run


async def _setup_native_graph(
    client: AsyncClient, csrf: str
) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
    first_competency, first_definition = await _semantic(
        client, csrf, "graph.foundation", "Foundation"
    )
    second_competency, second_definition = await _semantic(
        client, csrf, "graph.delivery", "Delivery"
    )
    await _active_profile(client, csrf, str(first_competency["id"]), str(second_competency["id"]))
    scales = (await client.get("/api/v2/capability-scales")).json()
    technical = next(item for item in scales if item["stableKey"] == "technical")
    familiar = next(item for item in technical["levels"] if item["stableKey"] == "familiar")
    graph = await client.post(
        "/api/v2/learning-graphs",
        json={"stable_key": "native-learning", "creation_source": "test"},
        headers={"X-CSRF-Token": csrf},
    )
    assert graph.status_code == 201, graph.text
    payload = {
        "title": "Native learning relations",
        "description": "No legacy Roadmap authority",
        "effective_at": "2026-01-01T00:00:00Z",
        "creation_source": "test",
        "edges": [
            _edge(
                "foundation-before-delivery",
                "prerequisite",
                str(first_definition["id"]),
                str(second_definition["id"]),
                0,
                requirement={
                    "kind": "capability_at_least",
                    "scale_version_id": technical["id"],
                    "dimension_id": None,
                    "minimum_level_id": familiar["id"],
                    "review_requirement": "none",
                },
            ),
            _edge(
                "foundation-specializes-delivery",
                "specialization",
                str(first_definition["id"]),
                str(second_definition["id"]),
                1,
            ),
            _edge(
                "delivery-recommended-after-foundation",
                "recommended_before",
                str(second_definition["id"]),
                str(first_definition["id"]),
                2,
            ),
            _edge(
                "delivery-supports-foundation",
                "supports",
                str(second_definition["id"]),
                str(first_definition["id"]),
                3,
            ),
            _edge(
                "related-pair",
                "related",
                str(second_definition["id"]),
                str(first_definition["id"]),
                4,
            ),
        ],
    }
    version = await client.post(
        f"/api/v2/learning-graphs/{graph.json()['id']}/versions",
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert version.status_code == 201, version.text
    activated = await client.post(
        f"/api/v2/learning-graphs/{graph.json()['id']}/versions/{version.json()['id']}/activate",
        json={
            "reason": "Native authority test",
            "source": "test",
            "idempotency_key": "activate-native-learning",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert activated.status_code == 201, activated.text
    return graph.json(), version.json(), first_definition, second_definition


async def test_native_graph_semantics_satisfaction_and_projection_are_deterministic(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    graph, version, first, second = await _setup_native_graph(client, csrf)
    stored_related = db.scalar(
        select(CompetencyEdgeDefinition).where(CompetencyEdgeDefinition.edge_type == "related")
    )
    assert stored_related is not None
    assert (
        stored_related.source_competency_identity_id < stored_related.target_competency_identity_id
    )

    satisfaction = await client.get(
        f"/api/v2/learning-graphs/{graph['id']}/versions/{version['id']}/satisfaction"
    )
    assert satisfaction.status_code == 200, satisfaction.text
    prerequisite = next(
        item for item in satisfaction.json()["edges"] if item["edge_type"] == "prerequisite"
    )
    assert prerequisite["aggregate_state"] == "unknown"
    assert prerequisite["eligibility_satisfied"] is False
    assert prerequisite["unknown_reasons"] == ["CAPABILITY_UNKNOWN"]

    rebuilt = await client.post(
        "/api/v2/roadmap-projection/rebuild?cutoff_at=2000000000000",
        headers={"X-CSRF-Token": csrf},
    )
    assert rebuilt.status_code == 200, rebuilt.text
    projection = rebuilt.json()
    assert projection["authority"] == "v2_projection"
    assert projection["legacyPhaseAuthority"] is False
    assert len(projection["nodes"]) == 2
    assert projection["relationshipVisibility"]["prerequisite"] is False
    assert (
        next(item for item in projection["edges"] if item["edgeType"] == "prerequisite")[
            "eligibilityAuthority"
        ]
        is True
    )
    repeated = await client.post(
        "/api/v2/roadmap-projection/rebuild?cutoff_at=2000000000000",
        headers={"X-CSRF-Token": csrf},
    )
    assert repeated.json()["outputHash"] == projection["outputHash"]

    current_first = await client.post(
        "/api/v2/roadmap-projection/rebuild", headers={"X-CSRF-Token": csrf}
    )
    current_second = await client.post(
        "/api/v2/roadmap-projection/rebuild", headers={"X-CSRF-Token": csrf}
    )
    assert current_first.status_code == 200, current_first.text
    assert current_second.status_code == 200, current_second.text
    assert current_first.json()["sourceLineage"]["cutoffAt"] is None
    assert current_first.json()["sourceLineage"]["cutoffMode"] == "current"
    assert current_second.json()["outputHash"] == current_first.json()["outputHash"]

    node = next(
        item for item in projection["nodes"] if item["semanticDefinitionId"] == second["id"]
    )
    moved = await client.put(
        f"/api/v2/roadmap-projection/{projection['scopeKey']}/positions/{node['nodeKey']}",
        json={"node_key": node["nodeKey"], "position_x": 875, "position_y": 425},
        headers={"X-CSRF-Token": csrf},
    )
    assert moved.status_code == 200, moved.text
    historical_after_move = await client.post(
        "/api/v2/roadmap-projection/rebuild?cutoff_at=2000000000000",
        headers={"X-CSRF-Token": csrf},
    )
    assert historical_after_move.status_code == 200
    assert historical_after_move.json()["outputHash"] == projection["outputHash"]
    assert (
        historical_after_move.json()["sourceLineage"]["presentationInputs"]["mode"]
        == "excluded_historical"
    )
    unrelated_override = RoadmapNodePositionOverride(
        scope_key="profile:other:graph:other",
        node_key="other-node",
        position_x=-875,
        position_y=425,
        provenance="user_override",
        created_at=1,
        updated_at=1,
    )
    db.add(unrelated_override)
    db.commit()
    preferences = await client.put(
        f"/api/v2/roadmap-projection/{projection['scopeKey']}/preferences",
        json={
            "show_prerequisites": True,
            "show_recommended_before": False,
            "show_supports": True,
            "show_related": False,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert preferences.status_code == 200, preferences.text
    reset = await client.delete(
        f"/api/v2/roadmap-projection/{projection['scopeKey']}/positions",
        headers={"X-CSRF-Token": csrf},
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["clearedCount"] == 1
    reset_node = next(
        item
        for item in reset.json()["projection"]["nodes"]
        if item["semanticDefinitionId"] == second["id"]
    )
    assert reset_node["position"] == reset_node["canonicalPosition"]
    assert reset.json()["projection"]["relationshipVisibility"] == {
        "prerequisite": True,
        "recommended_before": False,
        "supports": True,
        "specialization": True,
        "related": False,
    }
    stored_preference = db.get(RoadmapProjectionPreference, str(projection["scopeKey"]))
    assert stored_preference is not None
    assert (
        stored_preference.show_prerequisites,
        stored_preference.show_recommended_before,
        stored_preference.show_supports,
        stored_preference.show_related,
    ) == (True, False, True, False)
    assert db.scalar(select(func.count()).select_from(RoadmapNodePositionOverride)) == 1
    assert db.get(RoadmapNodePositionOverride, unrelated_override.id) is not None
    assert (
        db.scalar(
            select(func.count())
            .select_from(ProjectionInvalidation)
            .where(ProjectionInvalidation.projection_kind == "roadmap_projection_v2")
        )
        >= 2
    )


async def test_projection_marks_multiple_current_today_competencies(
    authenticated_client: tuple[AsyncClient, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, csrf = authenticated_client
    await _setup_native_graph(client, csrf)
    baseline = projection_service.build_projection(db)
    node_ids = [str(item["competencyIdentityId"]) for item in baseline["nodes"]]
    overlay = TodayRoadmapOverlayDTO(
        items=tuple(
            TodayRoadmapOverlayItemDTO(
                suggestion_id=f"suggestion-{index}",
                generation_id="generation-current",
                status="pending",
                competency_identity_id=node_id,
                target_identity_ids=(),
                continuing_started=False,
                expires_at=2_000_000_000_000,
            )
            for index, node_id in enumerate(node_ids)
        ),
        input_hash="today-overlay-multiple",
    )
    monkeypatch.setattr(projection_service, "current_roadmap_overlay", lambda *_a, **_kw: overlay)

    projection = projection_service.build_projection(db)

    assert len(node_ids) == 2
    marked_today = {
        str(item["competencyIdentityId"]) for item in projection["nodes"] if item["isToday"]
    }
    assert marked_today == set(node_ids)


async def test_projection_preserves_public_multi_dimension_and_domain_targets(
    authenticated_client: tuple[AsyncClient, str],
) -> None:
    client, csrf = authenticated_client
    identity = await client.post(
        "/api/v2/competencies",
        json={"stable_key": "language.fixture", "creation_source": "test"},
        headers={"X-CSRF-Token": csrf},
    )
    assert identity.status_code == 201, identity.text
    competency_id = str(identity.json()["id"])
    definition = await client.post(
        f"/api/v2/competencies/{competency_id}/definitions",
        json={
            "title": "Language fixture",
            "description": "A multi-dimension Roadmap target.",
            "scope": "Speaking and reading",
            "scale_stable_key": "cefr",
            "scale_version": "v1",
            "dimension_keys": ["speaking", "reading"],
            "effective_at": "2026-01-01T00:00:00Z",
            "creation_source": "test",
            "criteria": [
                {
                    "stable_key": "language.fixture.speaking",
                    "level_stable_key": "b1",
                    "dimension_key": "speaking",
                    "requirement_type": "required",
                    "demonstration_rule": "independent_performance",
                    "description": "Demonstrate speaking.",
                },
                {
                    "stable_key": "language.fixture.reading",
                    "level_stable_key": "b2",
                    "dimension_key": "reading",
                    "requirement_type": "required",
                    "demonstration_rule": "authoritative_assessment",
                    "description": "Demonstrate reading.",
                },
            ],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert definition.status_code == 201, definition.text
    activated_definition = await client.post(
        f"/api/v2/competencies/{competency_id}/definitions/{definition.json()['id']}/activate",
        json={
            "reason": "Projection target test",
            "source": "test",
            "idempotency_key": "activate-language-fixture",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert activated_definition.status_code == 200, activated_definition.text
    profile = await client.post(
        "/api/v2/target-profiles",
        json={
            "stable_key": "language-fixture-profile",
            "creation_source": "test",
            "version": {
                "title": "Language fixture profile",
                "description": "Multi-target projection proof.",
                "creation_source": "test",
                "effective_at": "2026-01-01T00:00:00Z",
                "domains": [
                    {
                        "stable_key": "communication",
                        "title": "Communication",
                        "minimum_percent": 0,
                        "maximum_percent": 100,
                        "order_index": 0,
                    },
                    {
                        "stable_key": "comprehension",
                        "title": "Comprehension",
                        "minimum_percent": 0,
                        "maximum_percent": 100,
                        "order_index": 1,
                    },
                ],
                "targets": [
                    {
                        "stable_key": "language-speaking",
                        "competency_identity_id": competency_id,
                        "dimension_key": "speaking",
                        "domain_stable_key": "communication",
                        "scale_stable_key": "cefr",
                        "scale_version": "v1",
                        "target_level_stable_key": "b1",
                        "priority": "core",
                    },
                    {
                        "stable_key": "language-reading",
                        "competency_identity_id": competency_id,
                        "dimension_key": "reading",
                        "domain_stable_key": "comprehension",
                        "scale_stable_key": "cefr",
                        "scale_version": "v1",
                        "target_level_stable_key": "b2",
                        "priority": "important",
                    },
                ],
                "milestones": [],
                "readiness_gates": [],
            },
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert profile.status_code == 201, profile.text
    activated_profile = await client.post(
        f"/api/v2/target-profiles/{profile.json()['profileId']}/versions/"
        f"{profile.json()['versionId']}/activate",
        json={
            "reason": "Projection target test",
            "source": "test",
            "idempotency_key": "activate-language-profile",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert activated_profile.status_code == 200, activated_profile.text
    graph = await client.post(
        "/api/v2/learning-graphs",
        json={"stable_key": "language-fixture-graph", "creation_source": "test"},
        headers={"X-CSRF-Token": csrf},
    )
    assert graph.status_code == 201, graph.text
    graph_version = await client.post(
        f"/api/v2/learning-graphs/{graph.json()['id']}/versions",
        json={
            "title": "Language fixture graph",
            "effective_at": "2026-01-01T00:00:00Z",
            "creation_source": "test",
            "edges": [],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert graph_version.status_code == 201, graph_version.text
    activated_graph = await client.post(
        f"/api/v2/learning-graphs/{graph.json()['id']}/versions/"
        f"{graph_version.json()['id']}/activate",
        json={
            "reason": "Projection target test",
            "source": "test",
            "idempotency_key": "activate-language-graph",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert activated_graph.status_code == 201, activated_graph.text

    response = await client.post(
        "/api/v2/roadmap-projection/rebuild", headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 200, response.text
    node = response.json()["nodes"][0]
    assert node["isTargeted"] is True
    assert node["isCurrent"] is True
    assert node["profileDomain"] is None
    assert node["layoutLane"]["id"] == "cross-domain-targets"
    assert [item["dimensionKey"] for item in node["profileTargets"]] == [
        "speaking",
        "reading",
    ]
    assert [item["profileDomain"]["title"] for item in node["profileTargets"]] == [
        "Communication",
        "Comprehension",
    ]
    assert [item["targetLevelKey"] for item in node["profileTargets"]] == ["b1", "b2"]
    assert [item["targetLevelTitle"] for item in node["profileTargets"]] == ["B1", "B2"]


async def test_graph_validation_rejects_hard_cycles_duplicates_scale_mismatch_and_identity_drift(
    authenticated_client: tuple[AsyncClient, str],
) -> None:
    client, csrf = authenticated_client
    graph, version, first, second = await _setup_native_graph(client, csrf)
    base = {
        "title": "Validation",
        "effective_at": "2026-01-02T00:00:00Z",
        "creation_source": "test",
        "edges": [],
    }
    requirement = version["edges"][0]["requirement"]
    cycle = copy.deepcopy(base)
    cycle["edges"] = [
        _edge(
            "a-to-b",
            "prerequisite",
            str(first["id"]),
            str(second["id"]),
            0,
            requirement=requirement,
        ),
        _edge(
            "b-to-a",
            "prerequisite",
            str(second["id"]),
            str(first["id"]),
            1,
            requirement=requirement,
        ),
    ]
    rejected_cycle = await client.post(
        f"/api/v2/learning-graphs/{graph['id']}/versions/validate",
        json=cycle,
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected_cycle.status_code == 422
    assert rejected_cycle.json()["error"]["code"] == "LEARNING_GRAPH_PREREQUISITE_CYCLE"

    specialization_cycle = copy.deepcopy(base)
    specialization_cycle["edges"] = [
        _edge("specialization-a", "specialization", str(first["id"]), str(second["id"]), 0),
        _edge("specialization-b", "specialization", str(second["id"]), str(first["id"]), 1),
    ]
    rejected_specialization = await client.post(
        f"/api/v2/learning-graphs/{graph['id']}/versions/validate",
        json=specialization_cycle,
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected_specialization.status_code == 422
    assert rejected_specialization.json()["error"]["code"] == "LEARNING_GRAPH_SPECIALIZATION_CYCLE"

    related_duplicate = copy.deepcopy(base)
    related_duplicate["edges"] = [
        _edge("related-one", "related", str(first["id"]), str(second["id"]), 0),
        _edge("related-two", "related", str(second["id"]), str(first["id"]), 1),
    ]
    rejected_duplicate = await client.post(
        f"/api/v2/learning-graphs/{graph['id']}/versions/validate",
        json=related_duplicate,
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected_duplicate.status_code == 422
    assert rejected_duplicate.json()["error"]["code"] == "LEARNING_GRAPH_EDGE_DUPLICATE"

    scale_mismatch = copy.deepcopy(base)
    bad_requirement = copy.deepcopy(requirement)
    bad_requirement["scale_version_id"] = "missing-scale"
    scale_mismatch["edges"] = [
        _edge(
            "bad-scale",
            "prerequisite",
            str(first["id"]),
            str(second["id"]),
            0,
            requirement=bad_requirement,
        )
    ]
    rejected_scale = await client.post(
        f"/api/v2/learning-graphs/{graph['id']}/versions/validate",
        json=scale_mismatch,
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected_scale.status_code == 422
    assert rejected_scale.json()["error"]["code"] == "LEARNING_GRAPH_SCALE_MISMATCH"

    drift = copy.deepcopy(base)
    drift["edges"] = [
        _edge(
            "foundation-before-delivery",
            "supports",
            str(first["id"]),
            str(second["id"]),
            0,
        )
    ]
    rejected_drift = await client.post(
        f"/api/v2/learning-graphs/{graph['id']}/versions",
        json=drift,
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected_drift.status_code == 422
    assert rejected_drift.json()["error"]["code"] == "LEARNING_GRAPH_EDGE_IDENTITY_INCOMPATIBLE"


async def test_prerequisite_criterion_and_review_predicates_preserve_unknown_and_freshness(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    graph, version, first, second = await _setup_native_graph(client, csrf)
    criterion_id = str(first["criteria"][0]["id"])
    criterion_payload = {
        "title": "Criterion prerequisite",
        "effective_at": "2026-01-02T00:00:00Z",
        "creation_source": "test",
        "edges": [
            _edge(
                "criterion-prerequisite",
                "prerequisite",
                str(first["id"]),
                str(second["id"]),
                0,
                requirement={
                    "kind": "criterion_set_demonstrated",
                    "criterion_definition_ids": [criterion_id],
                },
            )
        ],
    }
    criterion_version = await client.post(
        f"/api/v2/learning-graphs/{graph['id']}/versions",
        json=criterion_payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert criterion_version.status_code == 201, criterion_version.text
    criterion_edge = db.scalar(
        select(CompetencyEdgeDefinition).where(
            CompetencyEdgeDefinition.learning_graph_version_id == criterion_version.json()["id"]
        )
    )
    assert criterion_edge is not None
    unknown = evaluate_edge(db, criterion_edge, 2_000_000_000_000)
    assert unknown.aggregate_state == "unknown"
    assert unknown.eligibility_satisfied is False
    assert unknown.unknown_reasons == (f"CRITERION_UNKNOWN:{criterion_id}",)

    requirement = copy.deepcopy(version["edges"][0]["requirement"])
    requirement["review_requirement"] = "review_due_false"
    review_payload = {
        "title": "Review-aware prerequisite",
        "effective_at": "2026-01-03T00:00:00Z",
        "creation_source": "test",
        "edges": [
            _edge(
                "foundation-before-delivery",
                "prerequisite",
                str(first["id"]),
                str(second["id"]),
                0,
                requirement=requirement,
            )
        ],
    }
    review_version = await client.post(
        f"/api/v2/learning-graphs/{graph['id']}/versions",
        json=review_payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert review_version.status_code == 201, review_version.text
    review_edge = db.scalar(
        select(CompetencyEdgeDefinition).where(
            CompetencyEdgeDefinition.learning_graph_version_id == review_version.json()["id"]
        )
    )
    assert review_edge is not None
    run = _capability_run(
        db,
        semantic_definition_id=str(first["id"]),
        selected_level_id=str(requirement["minimum_level_id"]),
        generated_at=1_800_000_000_000,
    )
    criterion = db.get(CriterionDefinition, criterion_id)
    assert criterion is not None
    prior_review_sequence = int(
        db.scalar(
            select(func.max(ReviewEvent.event_sequence)).where(
                ReviewEvent.competency_identity_id == run.competency_identity_id,
                ReviewEvent.scope_key == "overall",
            )
        )
        or 0
    )
    db.add(
        CriterionEvaluationResult(
            run_id=run.id,
            criterion_definition_id=criterion.id,
            state="demonstrated",
            evidence_set_hash="a" * 64,
            decisive_evidence_ids_json="[]",
            facts_json="{}",
        )
    )
    db.add(
        ReviewEvent(
            competency_identity_id=run.competency_identity_id,
            semantic_definition_id=run.semantic_definition_id,
            dimension_id=None,
            scope_key="overall",
            event_sequence=prior_review_sequence + 1,
            previous_freshness=None,
            new_freshness="stale",
            previous_review_due=None,
            new_review_due=False,
            reason_codes_json="[]",
            last_meaningful_evidence_at=None,
            current_through_days=None,
            stale_after_days=None,
            threshold_source="test",
            freshness_policy_version="freshness/v1",
            evaluation_run_id=run.id,
            created_at=1_800_000_000_001,
        )
    )
    db.commit()
    demonstrated = evaluate_edge(db, criterion_edge, 2_000_000_000_000)
    assert demonstrated.aggregate_state == "met"
    stale_but_not_due = evaluate_edge(db, review_edge, 2_000_000_000_000)
    assert stale_but_not_due.aggregate_state == "met"
    assert stale_but_not_due.review_state == "met"

    db.add(
        ReviewEvent(
            competency_identity_id=run.competency_identity_id,
            semantic_definition_id=run.semantic_definition_id,
            dimension_id=None,
            scope_key="overall",
            event_sequence=prior_review_sequence + 2,
            previous_freshness="stale",
            new_freshness="stale",
            previous_review_due=False,
            new_review_due=True,
            reason_codes_json='["REVIEW_DUE"]',
            last_meaningful_evidence_at=None,
            current_through_days=None,
            stale_after_days=None,
            threshold_source="test",
            freshness_policy_version="freshness/v1",
            evaluation_run_id=run.id,
            created_at=1_800_000_000_002,
        )
    )
    db.commit()
    due = evaluate_edge(db, review_edge, 2_000_000_000_000)
    assert due.aggregate_state == "not_met"
    assert due.review_state == "not_met"


async def test_dimension_prerequisite_uses_canonical_capability_and_review_scope(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    graph, _version, _first, target = await _setup_native_graph(client, csrf)
    identity = await client.post(
        "/api/v2/competencies",
        json={"stable_key": "graph.language", "creation_source": "test"},
        headers={"X-CSRF-Token": csrf},
    )
    assert identity.status_code == 201, identity.text
    definition = await client.post(
        f"/api/v2/competencies/{identity.json()['id']}/definitions",
        json={
            "title": "Language",
            "description": "Dimension prerequisite source",
            "scope": "Language use",
            "scale_stable_key": "cefr",
            "scale_version": "v1",
            "dimension_keys": ["speaking"],
            "effective_at": "2026-01-01T00:00:00Z",
            "creation_source": "test",
            "criteria": [
                {
                    "stable_key": "language.speaking.b1",
                    "level_stable_key": "b1",
                    "dimension_key": "speaking",
                    "requirement_type": "required",
                    "demonstration_rule": "independent_performance",
                    "description": "Speak at B1.",
                }
            ],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert definition.status_code == 201, definition.text
    activated = await client.post(
        f"/api/v2/competencies/{identity.json()['id']}/definitions/"
        f"{definition.json()['id']}/activate",
        json={
            "reason": "Dimension graph test",
            "source": "test",
            "idempotency_key": "activate-graph-language",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert activated.status_code == 200, activated.text
    scales = (await client.get("/api/v2/capability-scales")).json()
    cefr = next(item for item in scales if item["stableKey"] == "cefr")
    speaking = next(item for item in cefr["dimensions"] if item["stableKey"] == "speaking")
    b1 = next(item for item in cefr["levels"] if item["stableKey"] == "b1")
    canonical_scope = f"dimension:{speaking['id']}"
    requirement = {
        "kind": "capability_at_least",
        "scale_version_id": cefr["id"],
        "dimension_id": speaking["id"],
        "minimum_level_id": b1["id"],
        "review_requirement": "review_due_false",
    }
    edge = _edge(
        "speaking-before-delivery",
        "prerequisite",
        str(definition.json()["id"]),
        str(target["id"]),
        0,
        requirement=requirement,
    )
    edge["satisfaction_scope_key"] = canonical_scope
    graph_payload = {
        "title": "Dimension graph",
        "effective_at": "2026-01-04T00:00:00Z",
        "creation_source": "test",
        "edges": [edge],
    }
    invalid = copy.deepcopy(graph_payload)
    invalid["edges"][0]["satisfaction_scope_key"] = "speaking"
    rejected = await client.post(
        f"/api/v2/learning-graphs/{graph['id']}/versions/validate",
        json=invalid,
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "LEARNING_GRAPH_SCOPE_MISMATCH"
    created = await client.post(
        f"/api/v2/learning-graphs/{graph['id']}/versions",
        json=graph_payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    stored = db.scalar(
        select(CompetencyEdgeDefinition).where(
            CompetencyEdgeDefinition.learning_graph_version_id == created.json()["id"]
        )
    )
    assert stored is not None
    run = _capability_run(
        db,
        semantic_definition_id=str(definition.json()["id"]),
        selected_level_id=str(b1["id"]),
        generated_at=1_800_000_000_000,
        dimension_id=str(speaking["id"]),
    )
    prior_sequence = int(
        db.scalar(
            select(func.max(ReviewEvent.event_sequence)).where(
                ReviewEvent.competency_identity_id == run.competency_identity_id,
                ReviewEvent.scope_key == canonical_scope,
            )
        )
        or 0
    )
    db.add(
        ReviewEvent(
            competency_identity_id=run.competency_identity_id,
            semantic_definition_id=run.semantic_definition_id,
            dimension_id=str(speaking["id"]),
            scope_key=canonical_scope,
            event_sequence=prior_sequence + 1,
            previous_freshness=None,
            new_freshness="stale",
            previous_review_due=None,
            new_review_due=False,
            reason_codes_json="[]",
            last_meaningful_evidence_at=None,
            current_through_days=None,
            stale_after_days=None,
            threshold_source="test",
            freshness_policy_version="freshness/v1",
            evaluation_run_id=run.id,
            created_at=1_800_000_000_001,
        )
    )
    db.commit()
    result = evaluate_edge(db, stored, 2_000_000_000_000)
    assert result.aggregate_state == "met"
    assert result.capability_state == "met"
    assert result.review_state == "met"


async def test_singleton_graph_selection_and_historical_rebuild_do_not_poison_current_cache(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    first_graph, first_version, _first, _second = await _setup_native_graph(client, csrf)
    initial_event = db.scalar(
        select(LearningGraphActivationEvent).order_by(LearningGraphActivationEvent.event_sequence)
    )
    assert initial_event is not None
    time.sleep(0.002)
    second_graph = await client.post(
        "/api/v2/learning-graphs",
        json={"stable_key": "alternate-learning", "creation_source": "test"},
        headers={"X-CSRF-Token": csrf},
    )
    assert second_graph.status_code == 201
    second_version = await client.post(
        f"/api/v2/learning-graphs/{second_graph.json()['id']}/versions",
        json={
            "title": "Alternate",
            "effective_at": "2026-01-01T00:00:00Z",
            "creation_source": "test",
            "edges": [],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert second_version.status_code == 201, second_version.text
    selected = await client.post(
        f"/api/v2/learning-graphs/{second_graph.json()['id']}/versions/"
        f"{second_version.json()['id']}/activate",
        json={
            "reason": "Select alternate",
            "source": "test",
            "idempotency_key": "select-alternate",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert selected.status_code == 201, selected.text
    current = await client.post(
        "/api/v2/roadmap-projection/rebuild", headers={"X-CSRF-Token": csrf}
    )
    assert current.status_code == 200, current.text
    assert current.json()["sourceLineage"]["learningGraphId"] == second_graph.json()["id"]
    current_hash = current.json()["outputHash"]

    historical = await client.post(
        f"/api/v2/roadmap-projection/rebuild?cutoff_at={selected.json()['activatedAt']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert historical.status_code == 200, historical.text
    assert historical.json()["persisted"] is False
    assert historical.json()["sourceLineage"]["learningGraphId"] == first_graph["id"]
    still_current = await client.get("/api/v2/roadmap-projection/current")
    assert still_current.status_code == 200
    assert still_current.json()["outputHash"] == current_hash

    reselected = await client.post(
        f"/api/v2/learning-graphs/{first_graph['id']}/versions/{first_version['id']}/activate",
        json={
            "reason": "Reselect first graph",
            "source": "test",
            "idempotency_key": "reselect-first",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert reselected.status_code == 201, reselected.text
    state = db.get(ActiveLearningGraphState, 1)
    assert state is not None
    assert state.learning_graph_id == first_graph["id"]
    assert reselected.json()["fromVersionId"] == second_version.json()["id"]


async def test_graph_projection_portable_v6_and_legacy_compatibility_are_separate(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, roadmap = configured_client
    graph, _version, _first, _second = await _setup_native_graph(client, csrf)
    package = _portable_payload(db)
    assert package["manifest"] == PORTABLE_V10_MANIFEST
    assert package["roadmapProjectionCheckpoint"]["configured"] is True
    _validate_portable_payload(package, "graph-projection-v8", schema_version=10)
    tampered = copy.deepcopy(package)
    tampered["tables"]["learning_graph_versions"][0]["content_hash"] = "0" * 64
    with pytest.raises(AppError, match="Graph policy lineage or content hash"):
        _validate_portable_payload(tampered, "graph-projection-tampered", schema_version=10)
    activation_tamper = copy.deepcopy(package)
    activation_tamper["tables"]["learning_graph_activation_events"][0]["event_sequence"] = 2
    with pytest.raises(AppError, match="Graph activation history"):
        _validate_portable_payload(
            activation_tamper, "graph-activation-tampered", schema_version=10
        )

    compatibility = await client.get(
        f"/api/v2/roadmap-projection/legacy-roadmap-graph/{roadmap['activeVersion']['id']}"
    )
    assert compatibility.status_code == 200, compatibility.text
    assert compatibility.json()["authority"] == "legacy_v1_compatibility_only"
    required = next(
        item for item in compatibility.json()["edges"] if item["edgeType"] == "prerequisite"
    )
    assert required["satisfactionRule"] == "legacy_verified"
    assert required["provenance"]["originalKind"] == "required"
    assert db.scalar(select(func.count()).select_from(LearningGraph)) == 1
    legacy_state = db.get(LegacyRoadmapActiveState, roadmap["id"])
    assert legacy_state is not None and legacy_state.is_current is True
    assert graph["id"] != roadmap["id"]


async def test_portable_v9_verifies_frozen_v2_and_rebuilds_current_v3(
    db: Session,
) -> None:
    fixture_path = Path(__file__).parent / "fixtures/v2/roadmap-projection-v2-portable-v9.json"
    fixture_bytes = fixture_path.read_bytes()
    assert hashlib.sha256(fixture_bytes).hexdigest() == (
        "2f6e6b3615b0e2d12e936e4d0aa9a13ade76f473565801c6ba965cb421ad4bda"
    )
    package = json.loads(fixture_bytes)
    assert package["roadmapProjectionCheckpoint"] == {
        "configured": True,
        "cutoffAt": 1789830912288,
        "cutoffSemantics": "exclusive",
        "inputHash": "84e801e863a14da90840860194b4f19bd0b2080c87faaabeece8b3702f7d60f3",
        "layoutPolicyVersion": "roadmap-layout/v2.0",
        "outputHash": "440b7904e52e0c906b351f92d89bd939dc1adaf3361ca2cf2a99b4b751e7dfd8",
        "projectionPolicyVersion": "roadmap-projection/v2.0",
        "scopeKey": (
            "profile:0c15391d-1fa9-46c8-b180-00853b4f8a3a:"
            "graph:6a2ac48e-e72b-4cd1-87ee-992e222fc501"
        ),
        "sourceHash": "26bd9210ff1b9c42f6ed4dfd67f76de27eb70aad589e3775e15dc413e0f9930a",
    }

    _validate_portable_payload(package, "frozen-v2-checkpoint", schema_version=9)
    _apply_portable_restore(
        db,
        package,
        True,
        package_id="frozen-v2-restore",
        schema_version=9,
    )
    rebuilt = projection_service.cached_projection(db)
    assert rebuilt["projectionPolicyVersion"] == "roadmap-projection/v3.0"
    assert rebuilt["layoutPolicyVersion"] == "roadmap-layout/v3.0"

    tampered = copy.deepcopy(package)
    checkpoint = tampered["roadmapProjectionCheckpoint"]
    checkpoint["projectionPolicyVersion"] = "roadmap-projection/v3.0"
    checkpoint["inputHash"] = content_hash(
        {key: value for key, value in checkpoint.items() if key != "inputHash"}
    )
    with pytest.raises(AppError, match="policy bundle is inconsistent"):
        _validate_portable_payload(tampered, "tampered-policy-pair", schema_version=9)

    unregistered = copy.deepcopy(package)
    unregistered_checkpoint = unregistered["roadmapProjectionCheckpoint"]
    unregistered_checkpoint["layoutPolicyVersion"] = "roadmap-layout/unregistered"
    unregistered_checkpoint["inputHash"] = content_hash(
        {key: value for key, value in unregistered_checkpoint.items() if key != "inputHash"}
    )
    with pytest.raises(AppError, match="unregistered layout policy"):
        _validate_portable_payload(unregistered, "unregistered-layout", schema_version=9)


async def test_current_portable_v9_round_trip_preserves_manual_presentation(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    await _setup_native_graph(client, csrf)
    projection = projection_service.rebuild_projection(db)
    node_key = str(projection["nodes"][1]["nodeKey"])
    override = RoadmapNodePositionOverride(
        scope_key=str(projection["scopeKey"]),
        node_key=node_key,
        position_x=-12345,
        position_y=54321,
        provenance="user_override",
        created_at=1_789_000_000_001,
        updated_at=1_789_000_000_002,
    )
    preference = RoadmapProjectionPreference(
        scope_key=str(projection["scopeKey"]),
        show_prerequisites=True,
        show_recommended_before=False,
        show_supports=False,
        show_related=False,
        updated_at=1_789_000_000_003,
    )
    db.add_all([override, preference])
    db.commit()
    projection_service.rebuild_projection(db)
    db.commit()
    package = _portable_payload(db)
    assert package["roadmapProjectionCheckpoint"]["projectionPolicyVersion"] == (
        "roadmap-projection/v3.0"
    )
    assert package["roadmapProjectionCheckpoint"]["layoutPolicyVersion"] == ("roadmap-layout/v3.0")
    _validate_portable_payload(package, "current-v3-round-trip", schema_version=10)

    _apply_portable_restore(
        db,
        package,
        True,
        package_id="current-v3-round-trip",
        schema_version=10,
    )

    restored_override = db.get(RoadmapNodePositionOverride, override.id)
    assert restored_override is not None
    assert (
        restored_override.scope_key,
        restored_override.node_key,
        restored_override.position_x,
        restored_override.position_y,
        restored_override.provenance,
        restored_override.created_at,
        restored_override.updated_at,
    ) == (
        override.scope_key,
        node_key,
        -12345,
        54321,
        "user_override",
        1_789_000_000_001,
        1_789_000_000_002,
    )
    restored_preference = db.get(RoadmapProjectionPreference, preference.scope_key)
    assert restored_preference is not None
    assert (
        restored_preference.show_prerequisites,
        restored_preference.show_recommended_before,
        restored_preference.show_supports,
        restored_preference.show_related,
        restored_preference.updated_at,
    ) == (True, False, False, False, 1_789_000_000_003)
    restored = projection_service.cached_projection(db)
    restored_node = next(item for item in restored["nodes"] if item["nodeKey"] == node_key)
    assert restored_node["position"] == {"x": -12345, "y": 54321}
    assert restored["relationshipVisibility"] == {
        "prerequisite": True,
        "recommended_before": False,
        "supports": False,
        "specialization": True,
        "related": False,
    }
    cache = db.get(RoadmapProjectionCache, str(projection["scopeKey"]))
    checkpoint = db.get(RoadmapProjectionCheckpoint, str(projection["scopeKey"]))
    assert cache is not None
    assert cache.projection_policy_version == "roadmap-projection/v3.0"
    assert cache.layout_policy_version == "roadmap-layout/v3.0"
    assert checkpoint is not None
    assert checkpoint.policy_bundle_hash == content_hash(
        {"projection": "roadmap-projection/v3.0", "layout": "roadmap-layout/v3.0"}
    )


async def test_startup_drain_persists_v2_cache_policy_transition_without_pending_rows(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    await _setup_native_graph(client, csrf)
    projection = projection_service.rebuild_projection(db)
    scope_key = str(projection["scopeKey"])
    node_key = str(projection["nodes"][0]["nodeKey"])
    override = RoadmapNodePositionOverride(
        scope_key=scope_key,
        node_key=node_key,
        position_x=321,
        position_y=-654,
        provenance="user_override",
        created_at=1,
        updated_at=2,
    )
    preference = RoadmapProjectionPreference(
        scope_key=scope_key,
        show_prerequisites=True,
        show_recommended_before=False,
        show_supports=True,
        show_related=False,
        updated_at=3,
    )
    db.add_all([override, preference])
    db.flush()
    projection_service.rebuild_projection(db)
    cache = db.get(RoadmapProjectionCache, scope_key)
    checkpoint = db.get(RoadmapProjectionCheckpoint, scope_key)
    assert cache is not None and checkpoint is not None
    cache.projection_policy_version = "roadmap-projection/v2.0"
    cache.layout_policy_version = "roadmap-layout/v2.0"
    checkpoint.policy_bundle_hash = content_hash(
        {"projection": "roadmap-projection/v2.0", "layout": "roadmap-layout/v2.0"}
    )
    for row in db.scalars(
        select(ProjectionInvalidation).where(
            ProjectionInvalidation.projection_kind == "roadmap_projection_v2"
        )
    ).all():
        row.status = "completed"
    db.commit()

    assert projection_service.drain_projection_invalidations(db, recover_running=True) == 1

    db.refresh(cache)
    db.refresh(checkpoint)
    db.refresh(override)
    db.refresh(preference)
    assert cache.projection_policy_version == "roadmap-projection/v3.0"
    assert cache.layout_policy_version == "roadmap-layout/v3.0"
    assert checkpoint.policy_bundle_hash == content_hash(
        {"projection": "roadmap-projection/v3.0", "layout": "roadmap-layout/v3.0"}
    )
    assert (override.position_x, override.position_y, override.provenance) == (
        321,
        -654,
        "user_override",
    )
    assert (
        preference.show_prerequisites,
        preference.show_recommended_before,
        preference.show_supports,
        preference.show_related,
    ) == (True, False, True, False)
    rebuilt = projection_service.cached_projection(db)
    rebuilt_node = next(item for item in rebuilt["nodes"] if item["nodeKey"] == node_key)
    assert rebuilt_node["position"] == {"x": 321, "y": -654}


async def test_projection_invalidation_failure_is_durable_and_retryable(
    authenticated_client: tuple[AsyncClient, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, csrf = authenticated_client
    await _setup_native_graph(client, csrf)
    real_rebuild = projection_service.rebuild_projection

    def fail_rebuild(_db: Session, *, cutoff_at: int | None = None) -> dict[str, object]:
        raise RuntimeError("deterministic test failure")

    monkeypatch.setattr(projection_service, "rebuild_projection", fail_rebuild)
    assert projection_service.drain_projection_invalidations(db) == 0
    rows = db.scalars(
        select(ProjectionInvalidation).where(
            ProjectionInvalidation.projection_kind == "roadmap_projection_v2"
        )
    ).all()
    assert rows
    assert all(item.status == "pending" and item.attempt_count == 1 for item in rows)
    assert all("deterministic test failure" in str(item.error_json) for item in rows)
    assert projection_service.drain_projection_invalidations(db) == 0
    assert projection_service.drain_projection_invalidations(db) == 0
    db.expire_all()
    assert all(item.status == "permanent_failure" and item.attempt_count == 3 for item in rows)
    degraded = projection_service.cached_projection(db)
    assert degraded["cacheState"] == "rebuild_failed_bypassed"
    assert degraded["rebuildFailureCount"] == len(rows)

    monkeypatch.setattr(projection_service, "rebuild_projection", real_rebuild)
    rebuilt = projection_service.rebuild_projection(db)
    db.commit()
    assert rebuilt["configured"] is True
    db.expire_all()
    assert all(item.status == "completed" and item.error_json is None for item in rows)


async def test_projection_marks_today_competencies_from_public_overlay(
    authenticated_client: tuple[AsyncClient, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, csrf = authenticated_client
    await _setup_native_graph(client, csrf)
    baseline = projection_service.build_projection(db)
    target_node = next(item for item in baseline["nodes"] if item["profileTarget"] is not None)
    overlay_item = TodayRoadmapOverlayItemDTO(
        suggestion_id="today-overlay-suggestion",
        generation_id="today-overlay-generation",
        status="suggested",
        competency_identity_id=target_node["competencyIdentityId"],
        target_identity_ids=(),
        continuing_started=False,
        expires_at=9_999_999_999_999,
    )
    monkeypatch.setattr(
        projection_service,
        "current_roadmap_overlay",
        lambda _db, *, now_ms: TodayRoadmapOverlayDTO((overlay_item,), "today-overlay-hash"),
    )
    projected = projection_service.build_projection(db)
    marked = next(item for item in projected["nodes"] if item["id"] == target_node["id"])
    assert marked["isToday"] is True
    assert projected["sourceLineage"]["todayOverlay"]["inputHash"] == "today-overlay-hash"
    projection_service.rebuild_projection(db)
    db.execute(
        update(ProjectionInvalidation)
        .where(ProjectionInvalidation.projection_kind == "roadmap_projection_v2")
        .values(status="completed", completed_at=1)
    )
    changed_overlay = TodayRoadmapOverlayDTO((overlay_item,), "changed-overlay-hash")
    monkeypatch.setattr(
        projection_service,
        "current_roadmap_overlay",
        lambda _db, *, now_ms: changed_overlay,
    )
    assert projection_service.cached_projection(db)["cacheState"] == (
        "today_overlay_stale_bypassed"
    )
    historical = projection_service.build_projection(db, cutoff_at=9_999_999_999_999)
    assert all(item["isToday"] is False for item in historical["nodes"])
    assert historical["sourceLineage"]["todayOverlay"] == {"mode": "excluded_historical"}


def test_cp3_migrations_preserve_legacy_pointers_invent_no_graph_and_downgrade_safely(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "cp3-migration.sqlite3"
    config = _migration_config(database_path)
    command.upgrade(config, "0012_project_core")
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO roadmaps "
            "(id, stable_key, title, description, is_current, active_version_id, "
            "current_phase_id, created_at, updated_at) "
            "VALUES ('roadmap-cp3','roadmap.cp3','CP3','',0,NULL,NULL,1000,1000)"
        )
        connection.execute(
            "INSERT INTO roadmap_versions "
            "(id, roadmap_id, version, schema_version, changelog, source, created_at) "
            "VALUES ('version-cp3','roadmap-cp3','v1',1,'','test',1000)"
        )
        connection.execute(
            "INSERT INTO phases "
            "(id, roadmap_version_id, stable_key, title, description, order_index, archived) "
            "VALUES ('phase-cp3','version-cp3','phase.cp3','CP3','',0,0)"
        )
        connection.execute(
            "UPDATE roadmaps SET is_current=1, active_version_id='version-cp3', "
            "current_phase_id='phase-cp3' WHERE id='roadmap-cp3'"
        )
        connection.commit()
    finally:
        connection.close()

    command.upgrade(config, "0013_learning_graph")
    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("SELECT count(*) FROM learning_graphs").fetchone() == (0,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()
    command.upgrade(config, "0014_roadmap_projection_state")
    connection = sqlite3.connect(database_path)
    try:
        state = connection.execute(
            "SELECT roadmap_id, active_version_id, current_phase_id, is_current, state_hash "
            "FROM legacy_roadmap_active_states"
        ).fetchone()
        expected = hashlib.sha256(
            json.dumps(
                {
                    "activeVersionId": "version-cp3",
                    "currentPhaseId": "phase-cp3",
                    "isCurrent": True,
                    "roadmapId": "roadmap-cp3",
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        assert state == ("roadmap-cp3", "version-cp3", "phase-cp3", 1, expected)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()
    command.downgrade(config, "0012_project_core")
    command.upgrade(config, "head")

    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "INSERT INTO learning_graphs (id, stable_key, created_at, creation_source) "
            "VALUES ('graph-data','graph.data',1000,'test')"
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(RuntimeError, match="Populated legacy Roadmap pointer contraction"):
        command.downgrade(config, "0012_project_core")


@pytest.mark.parametrize(
    ("revision", "marker_sql", "message"),
    [
        (
            "0012_project_core",
            "CREATE TABLE learning_graphs (id TEXT PRIMARY KEY)",
            "partial Learning Graph",
        ),
        (
            "0013_learning_graph",
            "CREATE TABLE legacy_roadmap_active_states (roadmap_id TEXT PRIMARY KEY)",
            "partial Roadmap Projection",
        ),
    ],
)
def test_cp3_partial_migrations_are_refused(
    tmp_path: Path, revision: str, marker_sql: str, message: str
) -> None:
    database_path = tmp_path / f"partial-{revision}.sqlite3"
    config = _migration_config(database_path)
    command.upgrade(config, revision)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(marker_sql)
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(RuntimeError, match=message):
        database.run_migrations(f"sqlite:///{database_path}")


def test_cp3_roadmap_projection_manual_input_blocks_downgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "projection-input-downgrade.sqlite3"
    config = _migration_config(database_path)
    command.upgrade(config, "0014_roadmap_projection_state")
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "INSERT INTO roadmap_node_position_overrides "
            "(id, scope_key, node_key, position_x, position_y, provenance, created_at, updated_at) "
            "VALUES ('position-1','profile:p:graph:g','competency-1',0,0,'user_override',1,1)"
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(RuntimeError, match="canonical Roadmap Projection input state"):
        command.downgrade(config, "0013_learning_graph")


def test_roadmap_projection_consumes_graph_public_contract_only() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "roadmap_projection"
    tree = ast.parse((root / "service.py").read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert "app.learning_graph.models" not in imported_modules
    assert "app.compatibility.v1.roadmap_graph" not in imported_modules
    assert "app.roadmap" not in imported_modules
