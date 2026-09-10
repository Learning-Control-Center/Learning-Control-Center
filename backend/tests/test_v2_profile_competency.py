from __future__ import annotations

import copy

import pytest
from app.errors import AppError
from app.import_export import _portable_payload, _validate_portable_payload
from app.models import (
    ActiveCompetencyDefinitionState,
    ActiveTargetProfileState,
    CompetencyDefinitionActivationEvent,
    CriterionDefinition,
    LegacyCriterionAssertion,
    ProjectionInvalidation,
    SemanticCompetencyDefinition,
    TargetProfile,
    TargetProfileActivationEvent,
    TargetProfileVersion,
)
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _first_competency(roadmap: dict[str, object]) -> dict[str, object]:
    return roadmap["phases"][0]["tracks"][0]["competencies"][0]


def _semantic_payload() -> dict[str, object]:
    return {
        "title": "Python fundamentals",
        "description": "A semantic definition independent of roadmap placement.",
        "scope": "Small Python programs and ordinary debugging.",
        "scale_stable_key": "technical",
        "scale_version": "v1",
        "dimension_keys": [],
        "effective_at": "2026-09-09T00:00:00Z",
        "creation_source": "user",
        "criteria": [
            {
                "stable_key": "python.program.independent",
                "level_stable_key": "independent",
                "dimension_key": None,
                "requirement_type": "required",
                "demonstration_rule": "independent_performance",
                "description": "Build and debug a bounded program independently.",
                "importance_weight": 5,
            }
        ],
    }


def _profile_payload(competency_id: str) -> dict[str, object]:
    return {
        "stable_key": "backend-engineer",
        "creation_source": "user",
        "version": {
            "title": "Backend engineer",
            "description": "A durable target profile.",
            "creation_source": "user",
            "effective_at": "2026-09-09T00:00:00Z",
            "domains": [
                {
                    "stable_key": "engineering",
                    "title": "Engineering",
                    "minimum_percent": 40,
                    "maximum_percent": 100,
                    "order_index": 0,
                }
            ],
            "targets": [
                {
                    "stable_key": "python-independent",
                    "competency_identity_id": competency_id,
                    "dimension_key": None,
                    "domain_stable_key": "engineering",
                    "scale_stable_key": "technical",
                    "scale_version": "v1",
                    "target_level_stable_key": "independent",
                    "priority": "core",
                    "target_month": "2027-03",
                    "date_interpretation": "Reach during March 2027 local time.",
                }
            ],
            "milestones": [
                {
                    "stable_key": "service-ready",
                    "title": "Service ready",
                    "target_date": "2027-03-31",
                    "order_index": 0,
                    "target_stable_keys": ["python-independent"],
                }
            ],
            "readiness_gates": [
                {
                    "stable_key": "service-gate",
                    "title": "Service gate",
                    "effect": "urgency",
                    "order_index": 0,
                    "milestone_stable_key": "service-ready",
                    "target_stable_keys": ["python-independent"],
                    "predicates": [],
                }
            ],
        },
    }


async def test_seeded_scales_and_strict_semantic_definition_activation(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, roadmap = configured_client
    competency_id = str(_first_competency(roadmap)["identityId"])
    scales = await client.get("/api/v2/capability-scales")
    assert scales.status_code == 200
    by_key = {item["stableKey"]: item for item in scales.json()}
    assert [item["stableKey"] for item in by_key["technical"]["levels"]] == [
        "unexposed",
        "familiar",
        "guided",
        "independent",
        "strong",
        "advanced",
    ]
    assert [item["stableKey"] for item in by_key["cefr"]["dimensions"]] == [
        "speaking",
        "listening",
        "reading",
        "writing",
        "grammar",
        "vocabulary",
    ]

    created = await client.post(
        f"/api/v2/competencies/{competency_id}/definitions",
        json=_semantic_payload(),
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["criteria"][0]["demonstrationRule"] == "independent_performance"
    activated = await client.post(
        f"/api/v2/competencies/{competency_id}/definitions/{body['id']}/activate",
        json={
            "reason": "Initial semantic definition",
            "source": "user",
            "idempotency_key": "semantic-python-v1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert activated.status_code == 200
    replay = await client.post(
        f"/api/v2/competencies/{competency_id}/definitions/{body['id']}/activate",
        json={
            "reason": "Initial semantic definition",
            "source": "user",
            "idempotency_key": "semantic-python-v1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert replay.status_code == 200
    assert db.get(ActiveCompetencyDefinitionState, competency_id) is not None
    assert db.scalar(select(func.count()).select_from(CompetencyDefinitionActivationEvent)) == 1
    _validate_portable_payload(_portable_payload(db), "semantic-round-trip", schema_version=2)
    disconnected = copy.deepcopy(_portable_payload(db))
    disconnected["tables"]["competency_definition_activation_events"][0]["from_definition_id"] = (
        body["id"]
    )
    with pytest.raises(AppError, match="activation history"):
        _validate_portable_payload(disconnected, "semantic-disconnected", schema_version=2)


async def test_complete_profile_versions_validate_and_preserve_stable_target_identity(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, roadmap = configured_client
    competency_id = str(_first_competency(roadmap)["identityId"])
    created = await client.post(
        "/api/v2/target-profiles",
        json=_profile_payload(competency_id),
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    version_one = created.json()
    assert version_one["milestones"][0]["targetStableKeys"] == ["python-independent"]
    assert version_one["readinessGates"][0]["predicates"] == []
    activated = await client.post(
        f"/api/v2/target-profiles/{version_one['profileId']}/versions/{version_one['versionId']}/activate",
        json={
            "reason": "Primary target",
            "source": "user",
            "idempotency_key": "profile-backend-v1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert activated.status_code == 200
    replay = await client.post(
        f"/api/v2/target-profiles/{version_one['profileId']}/versions/{version_one['versionId']}/activate",
        json={
            "reason": "Primary target",
            "source": "user",
            "idempotency_key": "profile-backend-v1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert replay.status_code == 200

    version_two_payload = _profile_payload(competency_id)["version"]
    version_two_payload["title"] = "Backend engineer revision"
    version_two = await client.post(
        f"/api/v2/target-profiles/{version_one['profileId']}/versions",
        json=version_two_payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert version_two.status_code == 201, version_two.text
    assert version_two.json()["targets"][0]["identityId"] == version_one["targets"][0]["identityId"]
    assert (
        version_two.json()["milestones"][0]["identityId"]
        == version_one["milestones"][0]["identityId"]
    )
    assert (
        version_two.json()["readinessGates"][0]["identityId"]
        == version_one["readinessGates"][0]["identityId"]
    )
    assert db.scalar(select(func.count()).select_from(TargetProfile)) == 1
    assert db.scalar(select(func.count()).select_from(TargetProfileVersion)) == 2
    assert db.get(ActiveTargetProfileState, 1).target_profile_version_id == version_one["versionId"]
    assert db.scalar(select(func.count()).select_from(TargetProfileActivationEvent)) == 1
    _validate_portable_payload(_portable_payload(db), "profile-round-trip", schema_version=2)
    disconnected = copy.deepcopy(_portable_payload(db))
    disconnected["tables"]["target_profile_activation_events"][0]["from_profile_version_id"] = (
        version_one["versionId"]
    )
    with pytest.raises(AppError, match="activation history"):
        _validate_portable_payload(disconnected, "profile-disconnected", schema_version=2)


async def test_cefr_overall_and_dimensions_are_explicitly_separate(
    authenticated_client: tuple[AsyncClient, str],
) -> None:
    client, csrf = authenticated_client
    identity_response = await client.post(
        "/api/v2/competencies",
        json={"stable_key": "language.english", "creation_source": "user"},
        headers={"X-CSRF-Token": csrf},
    )
    assert identity_response.status_code == 201, identity_response.text
    competency_id = identity_response.json()["id"]
    payload = _semantic_payload()
    payload.update(
        {
            "title": "English communication",
            "scope": "Integrated English and separately evaluated language dimensions.",
            "scale_stable_key": "cefr",
            "dimension_keys": [
                "speaking",
                "listening",
                "reading",
                "writing",
                "grammar",
                "vocabulary",
            ],
            "criteria": [
                {
                    "stable_key": "english.overall.b1",
                    "level_stable_key": "b1",
                    "dimension_key": None,
                    "requirement_type": "required",
                    "demonstration_rule": "authoritative_assessment",
                    "description": "Demonstrate integrated B1 proficiency.",
                },
                {
                    "stable_key": "english.speaking.b1",
                    "level_stable_key": "b1",
                    "dimension_key": "speaking",
                    "requirement_type": "required",
                    "demonstration_rule": "independent_performance",
                    "description": "Demonstrate B1 speaking independently.",
                },
            ],
        }
    )
    response = await client.post(
        f"/api/v2/competencies/{competency_id}/definitions",
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    dimensions_by_key = {
        item["stableKey"]: item["dimensionKey"] for item in response.json()["criteria"]
    }
    assert dimensions_by_key == {
        "english.overall.b1": None,
        "english.speaking.b1": "speaking",
    }


async def test_profile_validation_rejects_infeasible_ranges_and_deferred_projects(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
) -> None:
    client, csrf, roadmap = configured_client
    competency_id = str(_first_competency(roadmap)["identityId"])
    infeasible = _profile_payload(competency_id)
    infeasible["version"]["domains"][0]["minimum_percent"] = 60
    infeasible["version"]["domains"].append(
        {
            "stable_key": "operations",
            "title": "Operations",
            "minimum_percent": 50,
            "maximum_percent": 100,
            "order_index": 1,
        }
    )
    response = await client.post(
        "/api/v2/target-profiles",
        json=infeasible,
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PROFILE_ALLOCATION_INFEASIBLE"

    deferred = _profile_payload(competency_id)
    deferred["stable_key"] = "project-profile"
    deferred["version"]["readiness_gates"][0]["predicates"] = [
        {
            "predicate_type": "project_criterion_demonstrated",
            "requirement_type": "required",
            "order_index": 0,
            "subject": {"projectCriterionId": "future"},
        }
    ]
    response = await client.post(
        "/api/v2/target-profiles",
        json=deferred,
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "FEATURE_NOT_AVAILABLE"


async def test_v1_checkbox_appends_unknown_legacy_assertion_and_invalidation(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, roadmap = configured_client
    criterion_id = str(_first_competency(roadmap)["exitCriteria"][0]["id"])
    assert db.scalar(select(func.count()).select_from(SemanticCompetencyDefinition)) == 0
    before = db.scalar(select(func.count()).select_from(LegacyCriterionAssertion))
    response = await client.put(
        f"/api/v1/roadmap/exit-criteria/{criterion_id}",
        json={"state": "met"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    assert db.scalar(select(func.count()).select_from(LegacyCriterionAssertion)) == before + 1
    assertion = db.scalars(
        select(LegacyCriterionAssertion).order_by(LegacyCriterionAssertion.asserted_at.desc())
    ).first()
    assert assertion.legacy_state == "met"
    assert assertion.demonstration_rule is None
    assert assertion.evidence_strength is None
    assert assertion.independence is None
    assert assertion.source_confidence is None
    assert db.scalar(select(func.count()).select_from(CriterionDefinition)) == 0
    assert db.scalar(select(func.count()).select_from(ProjectionInvalidation)) == 1

    portable = _portable_payload(db)
    assert len(portable["tables"]["legacy_criterion_assertions"]) == before + 1
    assert portable["tables"]["target_profiles"] == []
    assert "projection_invalidations" not in portable["tables"]
    validated, _summary = _validate_portable_payload(
        portable, "v2-profile-round-trip", schema_version=2
    )
    assert (
        validated["legacy_criterion_assertions"]
        == portable["tables"]["legacy_criterion_assertions"]
    )
    tampered = copy.deepcopy(portable)
    tampered["tables"]["legacy_criterion_assertions"][0]["provenance"] = "invented"
    with pytest.raises(AppError, match="legacy assertion"):
        _validate_portable_payload(tampered, "tampered-legacy-provenance", schema_version=2)
    tampered_hash = copy.deepcopy(portable)
    tampered_hash["tables"]["migration_backfill_runs"][0]["result_hash"] = "0" * 64
    with pytest.raises(AppError, match="Backfill counts or hashes"):
        _validate_portable_payload(tampered_hash, "tampered-backfill-hash", schema_version=2)


def test_profile_version_rows_are_immutable(db: Session) -> None:
    profile = TargetProfile(stable_key="immutable", creation_source="test")
    db.add(profile)
    db.flush()
    version = TargetProfileVersion(
        target_profile_id=profile.id,
        version=1,
        title="Original",
        description="",
        creation_source="test",
        effective_at=1_788_912_000_000,
    )
    db.add(version)
    db.commit()
    version.title = "Mutated"
    with pytest.raises(ValueError, match="immutable"):
        db.commit()
    db.rollback()
