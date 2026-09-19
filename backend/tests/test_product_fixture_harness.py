from __future__ import annotations

from pathlib import Path

import pytest

from scripts.product_fixture_harness import (
    FixtureRun,
    _fixture_hash,
    _parse_clock,
    _sanitize_fixture_value,
)


def test_fixture_run_owns_an_isolated_database_and_declared_time(tmp_path: Path) -> None:
    root = tmp_path / "fixture"
    run = FixtureRun(
        scenario="legacy-shell",
        timezone="Europe/Istanbul",
        clock_at="2026-09-19T13:00:00+03:00",
        root=root,
        backend_port=18101,
        frontend_port=18102,
    )
    environment = run.backend_environment()

    assert environment["LCC_DATABASE_URL"] == f"sqlite:///{root / 'data/fixture.sqlite3'}"
    assert environment["LCC_APP_TIMEZONE"] == "Europe/Istanbul"
    assert environment["LCC_FIXTURE_CLOCK_AT"] == "2026-09-19T10:00:00Z"
    assert environment["LCC_FIXTURE_CLOCK_STEP_MS"] == "10"
    assert environment["LCC_ENVIRONMENT"] == "test"
    assert str(run.database_path).startswith(str(root))

    run.finish(success=True)
    assert not root.exists()


def test_real_clock_mode_omits_fixture_override(tmp_path: Path) -> None:
    root = tmp_path / "real-clock-fixture"
    run = FixtureRun(
        scenario="legacy-shell",
        clock_at="real",
        root=root,
        backend_port=18103,
        frontend_port=18104,
    )

    assert "LCC_FIXTURE_CLOCK_AT" not in run.backend_environment()
    assert "LCC_FIXTURE_CLOCK_STEP_MS" not in run.backend_environment()

    run.finish(success=True)


def test_failed_fixture_retains_its_artifact_root(tmp_path: Path) -> None:
    root = tmp_path / "retained-fixture"
    run = FixtureRun(
        scenario="legacy-shell",
        root=root,
        backend_port=18105,
        frontend_port=18106,
    )

    run.finish(success=False)

    assert root.is_dir()


def test_fixture_clock_must_be_an_exact_instant() -> None:
    with pytest.raises(ValueError, match="timezone offset"):
        _parse_clock("2026-09-19T10:00:00")


def test_fixture_hash_normalizes_generated_resource_ids() -> None:
    first = _fixture_hash(
        "v2-shell",
        "UTC",
        "2026-09-19T10:01:00Z",
        [
            {
                "method": "POST",
                "path": "/api/v2/competencies/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/definitions",
                "status": 201,
            }
        ],
    )
    second = _fixture_hash(
        "v2-shell",
        "UTC",
        "2026-09-19T10:01:00Z",
        [
            {
                "method": "POST",
                "path": "/api/v2/competencies/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/definitions",
                "status": 201,
            }
        ],
    )

    assert first == second


def test_fixture_hash_tracks_semantic_payload_changes_without_run_specific_values() -> None:
    base_call = {
        "method": "POST",
        "path": "/api/v2/evidence/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "status": 201,
        "payload": {
            "title": "Evidence A",
            "occurred_at": "2026-09-19T10:00:00Z",
            "competency_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        },
    }
    equivalent_call = {
        **base_call,
        "path": "/api/v2/evidence/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "payload": {
            **base_call["payload"],
            "occurred_at": "2026-09-20T12:15:00+03:00",
            "competency_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        },
    }
    changed_call = {
        **equivalent_call,
        "payload": {**equivalent_call["payload"], "title": "Evidence B"},
    }

    first = _fixture_hash("v2-shell", "UTC", None, [base_call])
    equivalent = _fixture_hash("v2-shell", "UTC", None, [equivalent_call])
    changed = _fixture_hash("v2-shell", "UTC", None, [changed_call])

    assert first == equivalent
    assert changed != first


def test_fixture_metadata_sanitizer_redacts_credentials() -> None:
    assert _sanitize_fixture_value(
        {
            "username": "fixture-learner",
            "password": "not-for-artifacts",
            "bootstrap_token": "not-for-artifacts-either",
        }
    ) == {
        "username": "fixture-learner",
        "password": "<redacted>",
        "bootstrap_token": "<redacted>",
    }
