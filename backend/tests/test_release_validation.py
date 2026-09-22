from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from app import capability_cli, main
from app.config import Settings, get_settings_dependency
from app.errors import AppError
from app.models import CompetencyIdentity, RecommendationSnapshot
from app.recommendations import record_decision
from app.schemas import RecommendationDecision
from pydantic import ValidationError
from sqlalchemy.orm import Session

REPOSITORY_ROOT = Path(main.__file__).resolve().parents[2]


def test_capability_repair_cli_reports_rebuild_and_permanent_failures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    failure_with_detail = SimpleNamespace(
        id="failure-1",
        projection_kind="capability",
        subject_type="competency",
        subject_id="competency-1",
        error_json=json.dumps({"message": "invalid canonical fact"}),
    )
    failure_without_detail = SimpleNamespace(
        id="failure-2",
        projection_kind="review",
        subject_type="competency",
        subject_id="competency-2",
        error_json=None,
    )

    class ScalarResult:
        def all(self) -> list[Any]:
            return [failure_with_detail, failure_without_detail]

    class FakeSession:
        committed = False

        def commit(self) -> None:
            self.committed = True

        def scalars(self, _statement: Any) -> ScalarResult:
            return ScalarResult()

    session = FakeSession()
    monkeypatch.setattr(capability_cli, "get_settings", lambda: SimpleNamespace(database_url="x"))
    monkeypatch.setattr(capability_cli, "exclusive_operation_lock", lambda _url: nullcontext())
    monkeypatch.setattr(capability_cli, "run_migrations", lambda: None)
    monkeypatch.setattr(capability_cli, "initialize_database", lambda: None)
    monkeypatch.setattr(capability_cli, "SessionLocal", lambda: nullcontext(session))
    monkeypatch.setattr(capability_cli, "enqueue_full_capability_rebuild", lambda *_a, **_k: 2)
    monkeypatch.setattr(capability_cli, "drain_projection_invalidations", lambda *_a, **_k: 3)
    monkeypatch.setattr("sys.argv", ["capability_cli", "--enqueue-full-rebuild"])

    capability_cli.main()

    assert session.committed is True
    assert json.loads(capsys.readouterr().out) == {
        "queued": 2,
        "processed": 3,
        "permanentFailures": [
            {
                "id": "failure-1",
                "projectionKind": "capability",
                "subjectType": "competency",
                "subjectId": "competency-1",
                "error": {"message": "invalid canonical fact"},
            },
            {
                "id": "failure-2",
                "projectionKind": "review",
                "subjectType": "competency",
                "subjectId": "competency-2",
                "error": None,
            },
        ],
    }


@pytest.mark.asyncio
async def test_application_lifespan_runs_startup_and_shutdown_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeSession:
        pass

    async def scheduler(stop_event: Any) -> None:
        calls.append("scheduler_started")
        await stop_event.wait()
        calls.append("scheduler_stopped")

    monkeypatch.setattr(main, "settings", SimpleNamespace(database_url="sqlite:////tmp/test.db"))
    monkeypatch.setattr(main, "exclusive_operation_lock", lambda _url: nullcontext())
    monkeypatch.setattr(main, "run_migrations", lambda: calls.append("migrated"))
    monkeypatch.setattr(main, "initialize_database", lambda: calls.append("initialized"))
    monkeypatch.setattr(main, "SessionLocal", lambda: nullcontext(FakeSession()))
    monkeypatch.setattr(main, "_validate_database_state", lambda _db: calls.append("validated"))
    monkeypatch.setattr(main, "get_or_create_profile", lambda _db: calls.append("profile"))
    monkeypatch.setattr(main, "backfill_reports", lambda _db: calls.append("reports"))
    monkeypatch.setattr(
        main,
        "drain_projection_invalidations",
        lambda _db, *, recover_running: calls.append(f"drain:{recover_running}"),
    )
    monkeypatch.setattr(
        main,
        "drain_roadmap_projection_invalidations",
        lambda _db, *, recover_running: calls.append(f"roadmap_drain:{recover_running}"),
    )
    monkeypatch.setattr(main, "initialize_analysis_v3", lambda _db: calls.append("analysis_init"))
    monkeypatch.setattr(
        main,
        "drain_analysis_invalidations",
        lambda _db, *, recover_running: calls.append(f"analysis_drain:{recover_running}"),
    )
    monkeypatch.setattr(main, "report_scheduler", scheduler)

    async with main.lifespan(main.app):
        calls.append("serving")

    assert calls == [
        "migrated",
        "initialized",
        "validated",
        "profile",
        "reports",
        "drain:True",
        "roadmap_drain:True",
        "analysis_init",
        "analysis_drain:True",
        "serving",
        "scheduler_started",
        "scheduler_stopped",
    ]


@pytest.mark.parametrize(
    ("user_count", "bootstrap_token", "message"),
    [
        (2, None, "single-user invariant"),
        (0, "weak", "strong bootstrap token"),
    ],
)
def test_production_database_state_rejects_unsafe_startup(
    monkeypatch: pytest.MonkeyPatch,
    user_count: int,
    bootstrap_token: str | None,
    message: str,
) -> None:
    db = SimpleNamespace(scalar=lambda _statement: user_count)
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(environment="production", bootstrap_token=bootstrap_token),
    )
    with pytest.raises(RuntimeError, match=message):
        main._validate_database_state(db)  # type: ignore[arg-type]


@pytest.mark.parametrize("bootstrap_token", [None, "still-configured"])
def test_production_database_state_accepts_consumed_bootstrap_token(
    monkeypatch: pytest.MonkeyPatch, bootstrap_token: str | None
) -> None:
    db = SimpleNamespace(scalar=lambda _statement: 1)
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(environment="production", bootstrap_token=bootstrap_token),
    )
    main._validate_database_state(db)  # type: ignore[arg-type]


def test_nonproduction_database_state_does_not_query_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = SimpleNamespace(scalar=lambda _statement: pytest.fail("unexpected database query"))
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(environment="test", bootstrap_token=None),
    )
    main._validate_database_state(db)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"allowed_origins": ["https://other.example.test"]}, "allowed_origins"),
        ({"allowed_hosts": ["other.example.test"]}, "allowed_hosts"),
        ({"trusted_proxy_cidrs": []}, "trusted_proxy_cidrs"),
        ({"backup_directory": "relative/backups"}, "backup_directory"),
        ({"backup_directory": "/tmp/lcc-release-data"}, "must not overlap"),
        (
            {"database_url": f"sqlite:///{REPOSITORY_ROOT / 'data/release.db'}"},
            "database must be outside",
        ),
        (
            {"backup_directory": str(REPOSITORY_ROOT / "backups/release")},
            "backups must be outside",
        ),
    ],
)
def test_production_settings_reject_each_unsafe_deployment_boundary(
    changes: dict[str, Any], message: str
) -> None:
    values: dict[str, Any] = {
        "environment": "production",
        "database_url": "sqlite:////tmp/lcc-release-data/database.sqlite3",
        "backup_directory": "/tmp/lcc-release-backups",
        "public_origin": "https://lcc.example.test",
        "allowed_origins": ["https://lcc.example.test"],
        "allowed_hosts": ["lcc.example.test"],
        "security_secret": "A1b2C3d4E5f6G7h8J9k0LmnopQrstUVW",
        "trusted_proxy_cidrs": ["127.0.0.1/32"],
    }
    values.update(changes)
    with pytest.raises(ValidationError, match=message):
        Settings(**values)


@pytest.mark.asyncio
async def test_settings_dependency_and_health_use_current_runtime_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = Settings(environment="test")
    monkeypatch.setattr("app.config.get_settings", lambda: configured)
    assert await get_settings_dependency() is configured
    assert await main.health() == {"status": "ok"}


@pytest.mark.asyncio
async def test_recommendation_decision_success_and_missing_snapshot(db: Session) -> None:
    db.add_all(
        [
            CompetencyIdentity(id="competency-1", stable_key="release.primary"),
            CompetencyIdentity(id="competency-2", stable_key="release.alternate"),
        ]
    )
    snapshot = RecommendationSnapshot(
        id="recommendation-1",
        local_date="2026-09-12",
        engine_version=1,
        primary_competency_identity_id="competency-1",
        primary_activity_type="coding",
        structured_payload_json="{}",
        generated_at=1_789_156_800_000,
    )
    db.add(snapshot)
    db.commit()
    payload = RecommendationDecision(
        accepted_primary=False,
        chosen_competency_identity_id="competency-2",
    )

    result = await record_decision("recommendation-1", payload, db=db)
    assert result == {
        "snapshotId": "recommendation-1",
        "acceptedPrimary": False,
        "chosenCompetencyIdentityId": "competency-2",
    }

    with pytest.raises(AppError) as error:
        await record_decision("missing", payload, db=db)
    assert error.value.status_code == 404
