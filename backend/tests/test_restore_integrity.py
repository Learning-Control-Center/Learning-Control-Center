from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from app.errors import AppError
from app.import_export import _apply_portable_restore, _portable_payload
from app.models import (
    ApplicationSetting,
    CompetencyIdentity,
    DailyReflection,
    DisciplineProfile,
    ExportRecord,
    GeneratedReport,
    ImportRecord,
    LearningSession,
    RecommendationSnapshot,
    Roadmap,
)
from app.portability.registry import PORTABLE_SCHEMA_CURRENT
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _package(db: Session, package_id: str) -> dict[str, Any]:
    return {
        "schemaVersion": PORTABLE_SCHEMA_CURRENT,
        "packageType": "portable_logical_backup",
        "packageId": package_id,
        "appVersion": "1.0.0",
        "createdAt": "2026-09-04T18:30:00.000Z",
        "payload": _portable_payload(db),
    }


async def _inspect(client: AsyncClient, csrf: str, package: dict[str, Any]) -> Any:
    return await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "portable.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )


async def test_portable_inspection_rejects_reproduced_domain_corruption(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, _roadmap = configured_client
    identity_id = db.scalar(select(CompetencyIdentity.id))
    assert identity_id is not None
    db.add(
        LearningSession(
            competency_identity_id=identity_id,
            session_mode="manual",
            activity_type="practice",
            assistance_mode="none",
            started_at=1_788_284_400_000,
            ended_at=1_788_284_460_000,
            accumulated_duration_ms=60_000,
            duration_ms=60_000,
            outcome="completed",
        )
    )
    db.commit()
    base = _package(db, "base")

    missing_state = deepcopy(base)
    missing_state["packageId"] = "missing-state"
    missing_state["payload"]["tables"]["competency_states"].pop()

    invalid_timezone = deepcopy(base)
    invalid_timezone["packageId"] = "invalid-timezone"
    invalid_timezone["payload"]["tables"]["discipline_profiles"][0]["timezone"] = "Not/A_Timezone"

    hierarchy_cycle = deepcopy(base)
    hierarchy_cycle["packageId"] = "hierarchy-cycle"
    definitions = hierarchy_cycle["payload"]["tables"]["competency_definitions"]
    definitions[0]["parent_definition_id"] = definitions[1]["id"]
    definitions[1]["parent_definition_id"] = definitions[0]["id"]

    inconsistent_placement = deepcopy(base)
    inconsistent_placement["packageId"] = "inconsistent-placement"
    phases = inconsistent_placement["payload"]["tables"]["phases"]
    definitions = inconsistent_placement["payload"]["tables"]["competency_definitions"]
    definitions[0]["phase_id"] = next(
        phase["id"] for phase in phases if phase["id"] != definitions[0]["phase_id"]
    )

    invalid_verified_event = deepcopy(base)
    invalid_verified_event["packageId"] = "invalid-verified-event"
    event = invalid_verified_event["payload"]["tables"]["competency_status_events"][0]
    event["to_status"] = "verified"
    event["verification_record_id"] = None

    fractional_duration = deepcopy(base)
    fractional_duration["packageId"] = "fractional-duration"
    fractional_duration["payload"]["tables"]["learning_sessions"][0]["duration_ms"] = 60_000.5

    cases = (
        (missing_state, "PORTABLE_COMPETENCY_STATE_INVALID"),
        (invalid_timezone, "PORTABLE_TIMEZONE_INVALID"),
        (hierarchy_cycle, "COMPETENCY_HIERARCHY_CYCLE"),
        (inconsistent_placement, "PORTABLE_ROADMAP_PLACEMENT_INVALID"),
        (invalid_verified_event, "PORTABLE_VERIFICATION_INVALID"),
        (fractional_duration, "PORTABLE_TYPE_INVALID"),
    )
    before = db.scalar(select(func.count()).select_from(CompetencyIdentity))
    for package, error_code in cases:
        response = await _inspect(client, csrf, package)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == error_code
    assert db.scalar(select(func.count()).select_from(CompetencyIdentity)) == before


@pytest.mark.parametrize(
    "category",
    [
        "reflection",
        "discipline",
        "settings",
        "report",
        "recommendation",
        "import_history",
        "export_history",
    ],
)
async def test_every_portable_state_category_requires_replacement_confirmation(
    authenticated_client: tuple[AsyncClient, str], db: Session, category: str
) -> None:
    client, csrf = authenticated_client
    package = _package(db, f"incoming-{category}")
    if category == "reflection":
        db.add(DailyReflection(local_date="2026-09-01", text="Keep this"))
    elif category == "discipline":
        profile = db.get(DisciplineProfile, 1)
        assert profile is not None
        profile.weekly_target_active_days = 4
    elif category == "settings":
        db.add(ApplicationSetting(key="ui.preference", value_json='{"dense":true}'))
    elif category == "report":
        db.add(
            GeneratedReport(
                report_type="daily",
                period_start="2026-09-01",
                period_end="2026-09-01",
                analytics_version=1,
                structured_payload_json="{}",
            )
        )
    elif category == "recommendation":
        db.add(
            RecommendationSnapshot(
                local_date="2026-09-01", engine_version=1, structured_payload_json="{}"
            )
        )
    elif category == "import_history":
        db.add(
            ImportRecord(
                package_id="already-applied",
                import_type="state_update",
                schema_version=1,
                source_filename="state.json",
                dry_run_summary_json="{}",
                applied=True,
            )
        )
    elif category == "export_history":
        db.add(
            ExportRecord(export_type="analysis_snapshot", format="json", scope_summary_json="{}")
        )
    db.commit()

    preview = await _inspect(client, csrf, package)
    assert preview.status_code == 200, preview.text
    assert preview.json()["summary"]["existingStateEmpty"] is False
    assert preview.json()["summary"]["replacementRequired"] is True
    response = await client.post(
        "/api/v1/import-export/import/apply",
        json={
            "filename": "portable.json",
            "package": package,
            "confirmation_token": preview.json()["confirmationToken"],
            "replace_existing": False,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "RESTORE_REPLACEMENT_CONFIRMATION_REQUIRED"


async def test_pristine_default_profile_is_an_empty_learning_state(
    authenticated_client: tuple[AsyncClient, str], db: Session
) -> None:
    client, csrf = authenticated_client
    package = _package(db, "clean-restore")
    preview = await _inspect(client, csrf, package)
    assert preview.status_code == 200, preview.text
    assert preview.json()["summary"]["existingStateEmpty"] is True
    assert preview.json()["summary"]["replacementRequired"] is False
    response = await client.post(
        "/api/v1/import-export/import/apply",
        json={
            "filename": "portable.json",
            "package": package,
            "confirmation_token": preview.json()["confirmationToken"],
            "replace_existing": False,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text


def test_post_restore_domain_failure_rolls_back_replacement(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    package = _package(db, "post-restore-failure")
    package["payload"]["tables"]["competency_states"].pop()
    roadmap_count = db.scalar(select(func.count()).select_from(Roadmap))
    db.rollback()

    with pytest.raises(AppError) as raised:
        with db.begin():
            _apply_portable_restore(db, package["payload"], replace_existing=True, schema_version=4)
    assert raised.value.code == "PORTABLE_COMPETENCY_STATE_INVALID"
    assert db.scalar(select(func.count()).select_from(Roadmap)) == roadmap_count
