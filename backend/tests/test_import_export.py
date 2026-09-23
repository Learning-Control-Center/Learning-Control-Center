from __future__ import annotations

import json
from copy import deepcopy

import pytest
from app.authority import service as authority_service
from app.authority.models import LearningControlAuthorityState
from app.errors import AppError
from app.import_diff import PORTABLE_DOMAIN_TABLES, build_portable_replacement_diff
from app.import_export import (
    PORTABLE_BY_TABLE,
    _apply_portable_restore,
    _portable_payload,
    _validate_portable_payload,
)
from app.models import (
    AuthSession,
    CompetencyIdentity,
    DailyReflection,
    DisciplineProfile,
    ImportRecord,
    LearningSession,
    Track,
    User,
)
from app.portability.registry import PORTABLE_V9_MANIFEST, PORTABLE_V10_MASTER_IMPORT_TABLES
from app.time_utils import utc_now_ms
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _package(package_type: str, package_id: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "packageType": package_type,
        "packageId": package_id,
        "appVersion": "1.0.0",
        "createdAt": "2026-09-04T18:30:00.000Z",
        "payload": payload,
    }


def _activate_v2_for_import_test(db: Session, idempotency_key: str) -> None:
    state = db.get(LearningControlAuthorityState, 1)
    assert state is not None
    authority_service._append_event(
        db,
        state,
        idempotency_key=idempotency_key,
        command_type="activate_v2",
        resulting={
            "canonicalLearningAuthority": "v2",
            "recommendationPresentation": "v2",
            "roadmapPresentation": "v2",
            "todayPresentation": "v2",
        },
        reason="Test-only activation without operational backup",
        now_ms=utc_now_ms(),
    )
    db.commit()


async def _portable_export(client: AsyncClient, csrf: str) -> dict[str, object]:
    response = await client.post(
        "/api/v1/import-export/export",
        json={"purpose": "portable_logical_backup", "format": "json"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    return response.json()["content"]


async def test_export_taxonomy_and_secret_exclusion(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, _roadmap = configured_client
    local_backup_path = "/srv/lcc/backups/pre-import-sensitive.sqlite3"
    db.add(
        ImportRecord(
            package_id="historical-import-with-local-path",
            import_type="roadmap_update",
            schema_version=1,
            source_filename="roadmap.json",
            dry_run_summary_json="{}",
            applied=True,
            applied_at=utc_now_ms(),
            pre_import_backup_reference=local_backup_path,
        )
    )
    db.commit()
    invalid = await client.post(
        "/api/v1/import-export/export",
        json={"purpose": "portable_logical_backup", "format": "markdown"},
        headers={"X-CSRF-Token": csrf},
    )
    assert invalid.status_code == 422

    package = await _portable_export(client, csrf)
    tables = package["payload"]["tables"]
    assert "users" not in tables
    assert "auth_sessions" not in tables
    serialized = str(package)
    assert "password_hash" not in serialized
    assert "token_lookup_hash" not in serialized
    assert "csrf_secret_hash" not in serialized
    assert local_backup_path not in json.dumps(package)
    exported_import_record = next(
        row
        for row in tables["import_records"]
        if row["package_id"] == "historical-import-with-local-path"
    )
    assert exported_import_record["pre_import_backup_reference"] is None

    analysis = await client.post(
        "/api/v1/import-export/export",
        json={
            "purpose": "analysis_snapshot",
            "format": "json",
            "range": "30d",
            "categories": ["analytics"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert analysis.status_code == 200
    assert set(analysis.json()["content"]["payload"]) == {"scope", "analytics"}

    basics = db.scalar(
        select(CompetencyIdentity).where(CompetencyIdentity.stable_key == "python.basics")
    )
    functions = db.scalar(
        select(CompetencyIdentity).where(CompetencyIdentity.stable_key == "python.functions")
    )
    python_track = db.scalar(select(Track).where(Track.stable_key == "python"))
    started_at = utc_now_ms() - 60_000
    db.add(
        LearningSession(
            competency_identity_id=basics.id,
            track_id=python_track.id,
            session_mode="manual",
            activity_type="learning",
            assistance_mode="none",
            started_at=started_at,
            ended_at=started_at + 60_000,
            accumulated_duration_ms=60_000,
            duration_ms=60_000,
            outcome="completed",
        )
    )
    db.commit()
    selective = await client.post(
        "/api/v1/import-export/export",
        json={
            "purpose": "analysis_snapshot",
            "format": "json",
            "range": "7d",
            "categories": ["analytics", "sessions"],
            "competency_identity_ids": [basics.id],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert selective.status_code == 200, selective.text
    payload = selective.json()["content"]["payload"]
    assert set(payload["analytics"]["competencies"]) == {basics.id}
    assert functions.id not in payload["analytics"]["competencies"]
    assert payload["analytics"]["totalDurationMs"] == 60_000
    assert len(payload["sessions"]) == 1
    assert payload["sessions"][0]["competency_identity_id"] == basics.id

    human = await client.post(
        "/api/v1/import-export/export",
        json={"purpose": "human_report", "format": "markdown", "range": "7d"},
        headers={"X-CSRF-Token": csrf},
    )
    assert human.status_code == 200
    assert human.json()["content"].startswith("# Learning-Control-Center export")


def test_historical_v9_backup_paths_are_readable_but_not_restored(db: Session) -> None:
    historical_path = "/var/lib/lcc/backups/old-host-pre-import.sqlite3"
    db.add(
        ImportRecord(
            package_id="historical-v9-audit-record",
            import_type="state_update",
            schema_version=1,
            source_filename="state.json",
            dry_run_summary_json="{}",
            applied=True,
            applied_at=utc_now_ms(),
            pre_import_backup_reference=historical_path,
        )
    )
    db.commit()
    payload = _portable_payload(db)
    payload["manifest"] = PORTABLE_V9_MANIFEST
    for table_name in PORTABLE_V10_MASTER_IMPORT_TABLES:
        payload["tables"].pop(table_name)
    historical_row = next(
        row
        for row in payload["tables"]["import_records"]
        if row["package_id"] == "historical-v9-audit-record"
    )
    historical_row["pre_import_backup_reference"] = historical_path

    validated_tables, _summary = _validate_portable_payload(
        payload, "historical-v9-path-package", 9
    )
    assert validated_tables["master_import_revisions"] == []
    assert validated_tables["master_import_owned_keys"] == []
    validated_row = next(
        row
        for row in validated_tables["import_records"]
        if row["package_id"] == "historical-v9-audit-record"
    )
    assert validated_row["pre_import_backup_reference"] is None

    invalid_type = deepcopy(payload)
    invalid_type["tables"]["import_records"][0]["pre_import_backup_reference"] = {
        "path": historical_path
    }
    with pytest.raises(AppError) as raised:
        _validate_portable_payload(invalid_type, "historical-v9-invalid-path-type", 9)
    assert raised.value.code == "PORTABLE_TYPE_INVALID"
    assert raised.value.details == {
        "table": "import_records",
        "row": 0,
        "column": "pre_import_backup_reference",
    }

    missing_column = deepcopy(payload)
    del missing_column["tables"]["import_records"][0]["pre_import_backup_reference"]
    with pytest.raises(AppError) as raised:
        _validate_portable_payload(missing_column, "historical-v9-missing-column", 9)
    assert raised.value.code == "PORTABLE_SCHEMA_INVALID"

    _apply_portable_restore(
        db,
        payload,
        True,
        package_id="historical-v9-path-restore",
        schema_version=9,
    )
    db.commit()
    db.expire_all()
    restored = db.scalar(
        select(ImportRecord).where(ImportRecord.package_id == "historical-v9-audit-record")
    )
    assert restored is not None
    assert restored.pre_import_backup_reference is None


async def test_dry_run_does_not_mutate_and_full_restore_preserves_authentication(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, _roadmap = configured_client
    package = await _portable_export(client, csrf)
    identity_count = db.scalar(select(func.count(CompetencyIdentity.id)))
    user_id = db.scalar(select(User.id))
    session_count = db.scalar(select(func.count(AuthSession.id)))
    inspect = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "backup.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )
    assert inspect.status_code == 200, inspect.text
    assert inspect.json()["dryRun"] is True
    assert db.scalar(select(func.count(CompetencyIdentity.id))) == identity_count
    assert db.scalar(select(func.count(ImportRecord.id))) == 0

    no_replace = await client.post(
        "/api/v1/import-export/import/apply",
        json={
            "filename": "backup.json",
            "package": package,
            "confirmation_token": inspect.json()["confirmationToken"],
            "replace_existing": False,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert no_replace.status_code == 409

    inspect_again = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "backup.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )
    applied = await client.post(
        "/api/v1/import-export/import/apply",
        json={
            "filename": "backup.json",
            "package": package,
            "confirmation_token": inspect_again.json()["confirmationToken"],
            "replace_existing": True,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert applied.status_code == 200, applied.text
    assert db.scalar(select(User.id)) == user_id
    assert db.scalar(select(func.count(AuthSession.id))) == session_count
    assert db.scalar(select(func.count(CompetencyIdentity.id))) == identity_count
    assert db.scalar(select(func.count(ImportRecord.id))) == 1


async def test_invalid_portable_package_is_rejected_without_mutation(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, _roadmap = configured_client
    package = await _portable_export(client, csrf)
    corrupt = deepcopy(package)
    corrupt["packageId"] = "corrupt-package"
    del corrupt["payload"]["tables"]["learning_sessions"]
    before = db.scalar(select(func.count(CompetencyIdentity.id)))
    response = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "corrupt.json", "package": corrupt},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422
    assert db.scalar(select(func.count(CompetencyIdentity.id))) == before


async def test_portable_restore_preview_compares_existing_replacement_scope(
    configured_client: tuple[AsyncClient, str, dict[str, object]], db: Session
) -> None:
    client, csrf, _roadmap = configured_client
    package = await _portable_export(client, csrf)
    package["payload"]["tables"]["daily_reflections"].append(
        {
            "id": "incoming-reflection",
            "local_date": "2026-09-02",
            "text": "Incoming reflection",
            "created_at": 1_788_307_200_000,
            "updated_at": 1_788_307_200_000,
        }
    )
    db.add(DailyReflection(local_date="2026-09-03", text="Existing reflection"))
    profile = db.get(DisciplineProfile, 1)
    assert profile is not None
    profile.weekly_target_active_days = 4
    db.commit()

    preview = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "backup.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )
    assert preview.status_code == 200, preview.text
    replacement = preview.json()["diff"]["replacementDiff"]
    assert replacement["operation"] == "fullReplacement"
    assert replacement["mergeSupported"] is False
    assert replacement["authenticationPreserved"] is True
    assert replacement["willAddRows"] is True
    assert replacement["willDeleteRows"] is True
    assert replacement["willModifyRows"] is True
    assert replacement["tables"]["daily_reflections"] == {
        "existing": 1,
        "incoming": 1,
        "added": 1,
        "modified": 0,
        "removed": 1,
        "changed": True,
    }
    assert replacement["tables"]["discipline_profiles"]["modified"] == 1
    assert replacement["categories"]["reflections"] == {
        "existing": 1,
        "incoming": 1,
        "added": 1,
        "modified": 0,
        "removed": 1,
        "changed": True,
    }
    assert replacement["categories"]["discipline"]["modified"] == 1
    assert {"discipline", "reflections"} <= set(replacement["categoriesTouched"])


@pytest.mark.parametrize(
    ("table_name", "category"),
    [
        ("target_profiles", "targetProfiles"),
        ("curricula", "curriculum"),
        ("projects", "projects"),
        ("learning_graphs", "learningGraph"),
        ("evidence", "evidence"),
        ("capability_evaluation_runs", "capabilityReview"),
        ("analysis_v3_signals", "analysisV3"),
        ("recommendation_v2_runs", "recommendationV2"),
        ("today_suggestions", "todayV2"),
        ("learning_control_authority_events", "learningControlAuthority"),
    ],
)
def test_portable_replacement_diff_categorizes_v2_only_changes(
    table_name: str, category: str
) -> None:
    existing = {name: [] for name in PORTABLE_BY_TABLE}
    incoming = deepcopy(existing)
    table = PORTABLE_BY_TABLE[table_name].__table__
    incoming[table_name] = [
        {column.name: f"{table_name}:{column.name}" for column in table.primary_key.columns}
    ]

    replacement = build_portable_replacement_diff(existing, incoming, PORTABLE_BY_TABLE)

    assert replacement["categoriesTouched"] == [category]
    assert replacement["categories"][category]["added"] == 1
    assert replacement["tables"][table_name]["added"] == 1
    assert replacement["tables"][table_name]["changed"] is True


def test_portable_replacement_diff_taxonomy_is_complete_disjoint_and_deterministic() -> None:
    assignments = [
        table_name for table_names in PORTABLE_DOMAIN_TABLES.values() for table_name in table_names
    ]
    assert set(assignments) == set(PORTABLE_BY_TABLE)
    assert len(assignments) == len(set(assignments))

    existing = {name: [] for name in PORTABLE_BY_TABLE}
    incoming = deepcopy(existing)
    changed_tables = (
        "target_profiles",
        "curricula",
        "recommendation_v2_runs",
        "application_settings",
    )
    for table_name in changed_tables:
        table = PORTABLE_BY_TABLE[table_name].__table__
        incoming[table_name] = [
            {column.name: f"{table_name}:{column.name}" for column in table.primary_key.columns}
        ]

    first = build_portable_replacement_diff(existing, incoming, PORTABLE_BY_TABLE)
    second = build_portable_replacement_diff(existing, incoming, PORTABLE_BY_TABLE)

    assert first == second
    assert first["categoriesTouched"] == [
        "targetProfiles",
        "curriculum",
        "recommendationV2",
        "settings",
    ]
    assert sum(category["added"] for category in first["categories"].values()) == len(
        changed_tables
    )
    assert sum(table["added"] for table in first["tables"].values()) == len(changed_tables)


async def test_analysis_snapshot_includes_filtered_roadmap_and_resolved_scope(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
) -> None:
    client, csrf, roadmap = configured_client
    current_phase = roadmap["phases"][0]
    selected_track = current_phase["tracks"][0]
    selected_competency = selected_track["competencies"][0]
    response = await client.post(
        "/api/v1/import-export/export",
        json={
            "purpose": "analysis_snapshot",
            "format": "json",
            "range": "custom",
            "start_date": "2026-09-01",
            "end_date": "2026-09-04",
            "current_phase_only": True,
            "track_ids": [selected_track["id"]],
            "competency_identity_ids": [selected_competency["identityId"]],
            "categories": ["roadmap"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    payload = response.json()["content"]["payload"]
    assert set(payload) == {"scope", "roadmap"}
    assert payload["scope"]["resolved_start_date"] == "2026-09-01"
    assert payload["scope"]["resolved_end_date"] == "2026-09-04"
    assert payload["scope"]["timezone"] == "UTC"
    assert payload["scope"]["resolved_categories"] == ["roadmap"]
    assert payload["scope"]["selected_tracks"][0]["stable_key"] == "python"
    assert payload["scope"]["selected_competencies"][0]["stable_key"] == "python.basics"
    exported = payload["roadmap"]
    assert [phase["stableKey"] for phase in exported["phases"]] == ["phase-1"]
    assert [item["stableKey"] for item in exported["phases"][0]["tracks"][0]["competencies"]] == [
        "python.basics"
    ]


async def test_human_report_identifies_resolved_scope(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
) -> None:
    client, csrf, roadmap = configured_client
    track = roadmap["phases"][0]["tracks"][0]
    competency = track["competencies"][0]
    response = await client.post(
        "/api/v1/import-export/export",
        json={
            "purpose": "human_report",
            "format": "markdown",
            "range": "custom",
            "start_date": "2026-09-01",
            "end_date": "2026-09-04",
            "current_phase_only": True,
            "track_ids": [track["id"]],
            "competency_identity_ids": [competency["identityId"]],
            "categories": ["roadmap", "analytics"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    report = response.json()["content"]
    assert "Range preset: custom" in report
    assert "Resolved period: 2026-09-01 to 2026-09-04 (UTC)" in report
    assert "Included categories: roadmap, analytics" in report
    assert "Current phase only: yes" in report
    assert "Tracks: Python (python)" in report
    assert "Competencies: Python basics (python.basics)" in report


async def test_package_specific_payloads_reject_unknown_fields(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
    roadmap_payload: dict[str, object],
) -> None:
    client, csrf, roadmap = configured_client
    identity_id = roadmap["phases"][0]["tracks"][0]["competencies"][0]["identityId"]
    packages = [
        _package(
            "roadmap_update",
            "strict-roadmap",
            {"roadmap": {**roadmap_payload, "version": "2.0.0"}, "unexpected": True},
        ),
        _package(
            "verification_update",
            "strict-verification",
            {"verifications": [], "unexpected": True},
        ),
        _package(
            "state_update",
            "strict-state",
            {
                "states": [
                    {
                        "competency_identity_id": identity_id,
                        "status": "learning",
                        "unexpected": True,
                    }
                ]
            },
        ),
    ]
    for index, package in enumerate(packages):
        response = await client.post(
            "/api/v1/import-export/import/inspect",
            json={"filename": f"strict-{index}.json", "package": package},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 422, response.text

    portable = await _portable_export(client, csrf)
    portable["packageId"] = "strict-portable"
    portable["payload"]["unexpected"] = True
    response = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "strict-portable.json", "package": portable},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PORTABLE_SCHEMA_INVALID"


async def test_roadmap_conflicts_are_rejected_during_non_mutating_inspection(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
    roadmap_payload: dict[str, object],
    db: Session,
) -> None:
    client, csrf, _roadmap = configured_client
    version_count = db.scalar(select(func.count()).select_from(CompetencyIdentity))
    duplicate_version = await client.post(
        "/api/v1/import-export/import/inspect",
        json={
            "filename": "duplicate-version.json",
            "package": _package(
                "roadmap_update", "duplicate-version", {"roadmap": roadmap_payload}
            ),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert duplicate_version.status_code == 409
    assert duplicate_version.json()["error"]["code"] == "ROADMAP_VERSION_DUPLICATE"
    assert db.scalar(select(func.count()).select_from(CompetencyIdentity)) == version_count

    invalid_order = deepcopy(roadmap_payload)
    invalid_order["version"] = "2.0.0"
    invalid_order["phases"][1]["order_index"] = 0
    duplicate_order = await client.post(
        "/api/v1/import-export/import/inspect",
        json={
            "filename": "duplicate-order.json",
            "package": _package("roadmap_replace", "duplicate-order", {"roadmap": invalid_order}),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert duplicate_order.status_code == 422
    assert duplicate_order.json()["error"]["code"] == "ROADMAP_PACKAGE_INVALID"
    assert db.scalar(select(func.count()).select_from(CompetencyIdentity)) == version_count


async def test_roadmap_update_and_replace_are_complete_version_aliases(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
    roadmap_payload: dict[str, object],
) -> None:
    client, csrf, _roadmap = configured_client
    for package_type, version, title in (
        ("roadmap_update", "2.0.0", "Updated foundations"),
        ("roadmap_replace", "3.0.0", "Replacement-labelled foundations"),
    ):
        incoming = deepcopy(roadmap_payload)
        incoming["version"] = version
        incoming["title"] = title
        package = _package(package_type, f"alias-{version}", {"roadmap": incoming})
        preview = await client.post(
            "/api/v1/import-export/import/inspect",
            json={"filename": f"{package_type}.json", "package": package},
            headers={"X-CSRF-Token": csrf},
        )
        assert preview.status_code == 200, preview.text
        applied = await client.post(
            "/api/v1/import-export/import/apply",
            json={
                "filename": f"{package_type}.json",
                "package": package,
                "confirmation_token": preview.json()["confirmationToken"],
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert applied.status_code == 200, applied.text
        current = await client.get("/api/v1/roadmap/current")
        assert current.json()["roadmap"]["activeVersion"]["version"] == version
        assert current.json()["roadmap"]["title"] == title


@pytest.mark.parametrize("package_type", ["roadmap_update", "roadmap_replace"])
async def test_legacy_roadmap_import_inspection_is_blocked_after_v2_activation(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
    roadmap_payload: dict[str, object],
    db: Session,
    package_type: str,
) -> None:
    client, csrf, _roadmap = configured_client
    _activate_v2_for_import_test(db, f"activate-before-{package_type}-inspect")
    incoming = deepcopy(roadmap_payload)
    incoming["version"] = "2.0.0"
    package = _package(package_type, f"blocked-{package_type}", {"roadmap": incoming})

    preview = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": f"{package_type}.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )

    assert preview.status_code == 409
    assert preview.json()["error"]["code"] == "LEGACY_AUTHORITY_READ_ONLY"
    assert "confirmationToken" not in preview.json()


async def test_legacy_roadmap_import_apply_rechecks_authority_after_inspection(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
    roadmap_payload: dict[str, object],
    db: Session,
) -> None:
    client, csrf, _roadmap = configured_client
    incoming = deepcopy(roadmap_payload)
    incoming["version"] = "2.0.0"
    package = _package("roadmap_update", "authority-changed-after-inspect", {"roadmap": incoming})
    preview = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "roadmap-update.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )
    assert preview.status_code == 200, preview.text

    _activate_v2_for_import_test(db, "activate-between-import-inspect-and-apply")
    applied = await client.post(
        "/api/v1/import-export/import/apply",
        json={
            "filename": "roadmap-update.json",
            "package": package,
            "confirmation_token": preview.json()["confirmationToken"],
        },
        headers={"X-CSRF-Token": csrf},
    )

    assert applied.status_code == 409
    assert applied.json()["error"]["code"] == "LEGACY_AUTHORITY_READ_ONLY"
    assert (
        db.scalar(
            select(func.count(ImportRecord.id)).where(
                ImportRecord.package_id == "authority-changed-after-inspect"
            )
        )
        == 0
    )


async def test_dependency_cycles_are_validated_within_each_retained_version(
    configured_client: tuple[AsyncClient, str, dict[str, object]],
    roadmap_payload: dict[str, object],
) -> None:
    client, csrf, _roadmap = configured_client
    incoming = deepcopy(roadmap_payload)
    incoming["version"] = "2.0.0"
    competencies = incoming["phases"][0]["tracks"][0]["competencies"]
    basics = next(item for item in competencies if item["stable_key"] == "python.basics")
    functions = next(item for item in competencies if item["stable_key"] == "python.functions")
    basics["prerequisite_stable_keys"] = ["python.functions"]
    functions["prerequisite_stable_keys"] = []
    package = _package("roadmap_update", "opposite-valid-edges", {"roadmap": incoming})

    preview = await client.post(
        "/api/v1/import-export/import/inspect",
        json={"filename": "roadmap-v2.json", "package": package},
        headers={"X-CSRF-Token": csrf},
    )
    assert preview.status_code == 200, preview.text
    applied = await client.post(
        "/api/v1/import-export/import/apply",
        json={
            "filename": "roadmap-v2.json",
            "package": package,
            "confirmation_token": preview.json()["confirmationToken"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert applied.status_code == 200, applied.text
    current = await client.get("/api/v1/roadmap/current")
    assert current.json()["roadmap"]["activeVersion"]["version"] == "2.0.0"
