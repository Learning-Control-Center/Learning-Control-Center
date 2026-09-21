from __future__ import annotations

import json
import os
import stat
import subprocess
import tomllib
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _production_environment(test_root: Path, destination: Path) -> Path:
    content = (REPOSITORY_ROOT / "deploy" / "learning-control-center.env.example").read_text()
    content = content.replace("CHANGE_ME.example.com", "lcc.example.test")
    content = content.replace(
        "CHANGE_ME_INITIAL_BOOTSTRAP_TOKEN",
        "B7ootstrap9Token2For4Initial6Setup8Value0X",
    )
    content = content.replace(
        "CHANGE_ME_INDEPENDENT_SECURITY_SECRET",
        "S3curity7Value9For2Runtime4Hashing6Only8Q",
    )
    content = content.replace(
        "/opt/learning-control-center/current",
        str(test_root / "opt" / "learning-control-center" / "current"),
    )
    content = content.replace(
        "/var/lib/learning-control-center/lcc.sqlite3",
        str(test_root / "var" / "lib" / "learning-control-center" / "lcc.sqlite3"),
    )
    content = content.replace(
        "/var/backups/learning-control-center",
        str(test_root / "var" / "backups" / "learning-control-center"),
    )
    destination.write_text(content)
    destination.chmod(0o600)
    return destination


def _install(test_root: Path, environment_file: Path) -> subprocess.CompletedProcess[str]:
    source_revision = subprocess.run(
        ["git", "-C", REPOSITORY_ROOT, "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return subprocess.run(
        [
            str(REPOSITORY_ROOT / "scripts" / "install-ubuntu.sh"),
            "--domain",
            "lcc.example.test",
            "--release-id",
            "test-release",
            "--channel",
            "stable",
            "--source-revision",
            source_revision,
            "--source-repository",
            "https://example.invalid/Learning-Control-Center.git",
            "--source-ref",
            "refs/tags/test-release",
            "--source-origin",
            "https://example.invalid/releases/download",
            "--env-file",
            str(environment_file),
            "--source",
            str(REPOSITORY_ROOT),
            "--root",
            str(test_root),
            "--skip-build",
            "--skip-prerequisites",
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_isolated_installer_is_idempotent_and_renders_non_secret_caddy(tmp_path: Path) -> None:
    test_root = tmp_path / "installed-root"
    environment_file = _production_environment(test_root, tmp_path / "production.env")
    first = _install(test_root, environment_file)
    second = _install(test_root, environment_file)
    assert "test-release is installed" in first.stdout
    assert "test-release is installed" in second.stdout

    application_root = test_root / "opt" / "learning-control-center"
    current = application_root / "current"
    assert current.is_symlink()
    assert current.resolve() == application_root / "releases" / "test-release"
    assert (current / "RELEASE_ID").read_text().strip() == "test-release"
    assert (current / "RELEASE_CHANNEL").read_text().strip() == "stable"
    source_revision = subprocess.run(
        ["git", "-C", REPOSITORY_ROOT, "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert (current / "SOURCE_REVISION").read_text().strip() == source_revision
    release_manifest = (current / "RELEASE_MANIFEST").read_text()
    assert "channel=stable" in release_manifest
    assert f"source_revision={source_revision}" in release_manifest
    deployment_record = (
        test_root / "var" / "lib" / "learning-control-center" / "deployment-test-release.env"
    ).read_text()
    assert "channel=stable" in deployment_record
    assert f"source_revision={source_revision}" in deployment_record
    assert (current / "scripts" / "lcc-admin").stat().st_mode & stat.S_IXUSR
    assert (
        test_root / "opt" / "learning-control-center" / "update.sh"
    ).stat().st_mode & stat.S_IXUSR

    installed_environment = test_root / "etc" / "learning-control-center.env"
    assert stat.S_IMODE(installed_environment.stat().st_mode) == 0o640
    caddy_site = (
        test_root / "etc" / "caddy" / "Caddyfile.d" / "learning-control-center.caddy"
    ).read_text()
    assert "lcc.example.test {" in caddy_site
    assert str(current / "frontend" / "dist") in caddy_site
    assert "BOOTSTRAP" not in caddy_site
    assert "SECURITY_SECRET" not in caddy_site
    assert "reverse_proxy 127.0.0.1:8000" in caddy_site
    assert "try_files {path} /index.html" in caddy_site

    assert (
        stat.S_IMODE((test_root / "var" / "lib" / "learning-control-center").stat().st_mode)
        == 0o700
    )
    assert (
        stat.S_IMODE((test_root / "var" / "backups" / "learning-control-center").stat().st_mode)
        == 0o700
    )

    different_release = subprocess.run(
        [
            str(REPOSITORY_ROOT / "scripts" / "install-ubuntu.sh"),
            "--domain",
            "lcc.example.test",
            "--release-id",
            "different-release",
            "--env-file",
            str(environment_file),
            "--source",
            str(REPOSITORY_ROOT),
            "--root",
            str(test_root),
            "--skip-build",
            "--skip-prerequisites",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert different_release.returncode != 0
    assert "Another release is already active" in different_release.stderr

    changed_environment = tmp_path / "changed-production.env"
    changed_environment.write_text(
        environment_file.read_text().replace(
            "LCC_APP_TIMEZONE=UTC", "LCC_APP_TIMEZONE=Europe/Istanbul"
        )
    )
    changed_environment.chmod(0o600)
    refused_environment_replacement = _run_install_without_check(test_root, changed_environment)
    assert refused_environment_replacement.returncode != 0
    assert "--replace-env" in refused_environment_replacement.stderr


def test_installer_recovers_only_marked_partial_matching_release(tmp_path: Path) -> None:
    test_root = tmp_path / "installed-root"
    environment_file = _production_environment(test_root, tmp_path / "production.env")
    partial = test_root / "opt" / "learning-control-center" / "releases" / "test-release"
    partial.mkdir(parents=True)
    (partial / ".installing").write_text("")
    (partial / "untrusted-partial-file").write_text("remove me")
    result = _install(test_root, environment_file)
    assert result.returncode == 0
    assert not (partial / ".installing").exists()
    assert not (partial / "untrusted-partial-file").exists()
    assert (partial / "RELEASE_ID").read_text().strip() == "test-release"


def test_uninstall_preserves_data_and_purge_requires_two_opt_ins(tmp_path: Path) -> None:
    test_root = tmp_path / "installed-root"
    environment_file = _production_environment(test_root, tmp_path / "production.env")
    _install(test_root, environment_file)
    data_directory = test_root / "var" / "lib" / "learning-control-center"
    backup_directory = test_root / "var" / "backups" / "learning-control-center"
    database = data_directory / "lcc.sqlite3"
    backup = backup_directory / "lcc-scheduled-test.sqlite3"
    database.write_bytes(b"preserve-database")
    backup.write_bytes(b"preserve-backup")

    subprocess.run(
        [
            str(REPOSITORY_ROOT / "scripts" / "uninstall-ubuntu.sh"),
            "--root",
            str(test_root),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert database.read_bytes() == b"preserve-database"
    assert backup.read_bytes() == b"preserve-backup"
    assert not (test_root / "opt" / "learning-control-center").exists()

    rejected = subprocess.run(
        [
            str(REPOSITORY_ROOT / "scripts" / "uninstall-ubuntu.sh"),
            "--root",
            str(test_root),
            "--purge-data",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode != 0
    assert data_directory.exists()
    assert backup_directory.exists()

    subprocess.run(
        [
            str(REPOSITORY_ROOT / "scripts" / "uninstall-ubuntu.sh"),
            "--root",
            str(test_root),
            "--purge-data",
            "--confirm-purge=DELETE-LCC-DATA",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert not data_directory.exists()
    assert not backup_directory.exists()


def test_production_environment_rejects_placeholder_and_development_database(
    tmp_path: Path,
) -> None:
    test_root = tmp_path / "root"
    placeholder = tmp_path / "placeholder.env"
    placeholder.write_text(
        (REPOSITORY_ROOT / "deploy" / "learning-control-center.env.example")
        .read_text()
        .replace(
            "/opt/learning-control-center/current",
            str(test_root / "opt" / "learning-control-center" / "current"),
        )
        .replace(
            "/var/lib/learning-control-center/lcc.sqlite3",
            str(test_root / "var" / "lib" / "learning-control-center" / "lcc.sqlite3"),
        )
        .replace(
            "/var/backups/learning-control-center",
            str(test_root / "var" / "backups" / "learning-control-center"),
        )
    )
    placeholder.chmod(0o600)
    result = subprocess.run(
        [
            str(REPOSITORY_ROOT / "scripts" / "install-ubuntu.sh"),
            "--domain",
            "lcc.example.test",
            "--release-id",
            "test-release",
            "--env-file",
            str(placeholder),
            "--root",
            str(test_root),
            "--skip-build",
            "--skip-prerequisites",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "CHANGE_ME" in result.stderr

    environment_file = _production_environment(test_root, tmp_path / "production.env")
    environment_file.write_text(
        environment_file.read_text().replace(
            f"sqlite:///{test_root}/var/lib/learning-control-center/lcc.sqlite3",
            "sqlite:///./data/lcc.db",
        )
    )
    result = _run_install_without_check(test_root, environment_file)
    assert result.returncode != 0
    assert "absolute SQLite" in result.stderr


def test_installer_ignores_inherited_development_lcc_environment(tmp_path: Path) -> None:
    test_root = tmp_path / "root"
    environment_file = _production_environment(test_root, tmp_path / "production.env")
    inherited_environment = {
        **os.environ,
        "LCC_ENVIRONMENT": "development",
        "LCC_DATABASE_URL": "sqlite:///./data/lcc.db",
        "LCC_PUBLIC_ORIGIN": "http://localhost:5173",
        "LCC_BOOTSTRAP_TOKEN": "inherited-unsafe-token",
    }
    result = subprocess.run(
        [
            str(REPOSITORY_ROOT / "scripts" / "install-ubuntu.sh"),
            "--domain",
            "lcc.example.test",
            "--release-id",
            "test-release",
            "--env-file",
            str(environment_file),
            "--source",
            str(REPOSITORY_ROOT),
            "--root",
            str(test_root),
            "--skip-build",
            "--skip-prerequisites",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=inherited_environment,
    )
    assert result.returncode == 0, result.stderr
    assert (test_root / "var" / "lib" / "learning-control-center").is_dir()


def test_environment_parser_does_not_export_or_echo_secrets(tmp_path: Path) -> None:
    secret = "S3curity7Value9For2Runtime4Hashing6Only8Q"
    environment_file = tmp_path / "production.env"
    environment_file.write_text(f"LCC_SECURITY_SECRET={secret}\n")
    result = subprocess.run(
        [
            "bash",
            "-c",
            'set -euo pipefail; source "$1"; lcc_load_environment "$2"; env',
            "deployment-env-test",
            str(REPOSITORY_ROOT / "scripts" / "deploy-common.sh"),
            str(environment_file),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "LCC_SECURITY_SECRET=" not in result.stdout
    assert secret not in result.stdout

    environment_file.write_text(f"export LCC_SECURITY_SECRET={secret}\n")
    malformed = subprocess.run(
        [
            "bash",
            "-c",
            'set -euo pipefail; source "$1"; lcc_load_environment "$2"',
            "deployment-env-test",
            str(REPOSITORY_ROOT / "scripts" / "deploy-common.sh"),
            str(environment_file),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert malformed.returncode != 0
    assert secret not in malformed.stderr
    assert "line 1" in malformed.stderr


def test_parsed_secrets_are_inherited_without_child_argv_exposure(tmp_path: Path) -> None:
    environment_file = _production_environment(tmp_path, tmp_path / "production.env")
    result = subprocess.run(
        [
            "bash",
            "-c",
            """
source "$1"
lcc_load_environment "$2"
lcc_run_with_deploy_environment /usr/bin/python3 -c '
import json
import os
from pathlib import Path
secret = os.environ["LCC_SECURITY_SECRET"]
bootstrap = os.environ["LCC_BOOTSTRAP_TOKEN"]
cmdline = Path("/proc/self/cmdline").read_bytes()
print(json.dumps({
    "argv_has_security_secret": secret.encode() in cmdline,
    "argv_has_bootstrap_token": bootstrap.encode() in cmdline,
    "security_secret_available": len(secret) >= 32,
    "bootstrap_token_available": len(bootstrap) >= 32,
}))'
""",
            "environment-argv-test",
            str(REPOSITORY_ROOT / "scripts" / "deploy-common.sh"),
            str(environment_file),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == {
        "argv_has_security_secret": False,
        "argv_has_bootstrap_token": False,
        "security_secret_available": True,
        "bootstrap_token_available": True,
    }


def test_environment_generator_creates_complete_private_independent_secrets(
    tmp_path: Path,
) -> None:
    test_root = tmp_path / "installed-root"
    destination = tmp_path / "generated.env"
    subprocess.run(
        [
            str(REPOSITORY_ROOT / "scripts" / "generate-production-env.sh"),
            "--domain",
            "lcc.example.test",
            "--timezone",
            "Europe/Istanbul",
            "--output",
            str(destination),
            "--root",
            str(test_root),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    values = dict(
        line.split("=", 1)
        for line in destination.read_text().splitlines()
        if line and not line.startswith("#")
    )
    assert values["LCC_ENVIRONMENT"] == "production"
    assert values["LCC_PUBLIC_ORIGIN"] == "https://lcc.example.test"
    assert values["LCC_APP_TIMEZONE"] == "Europe/Istanbul"
    assert values["LCC_SECURITY_SECRET"] != values["LCC_BOOTSTRAP_TOKEN"]
    assert len(values["LCC_SECURITY_SECRET"]) >= 32
    assert len(values["LCC_BOOTSTRAP_TOKEN"]) >= 32
    assert str(test_root / "var" / "lib" / "learning-control-center") in values["LCC_DATABASE_URL"]
    assert (
        str(test_root / "var" / "backups" / "learning-control-center")
        == values["LCC_BACKUP_DIRECTORY"]
    )

    replacement = subprocess.run(
        [
            str(REPOSITORY_ROOT / "scripts" / "generate-production-env.sh"),
            "--domain",
            "lcc.example.test",
            "--timezone",
            "UTC",
            "--output",
            str(destination),
            "--root",
            str(test_root),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert replacement.returncode != 0
    assert "Refusing to replace" in replacement.stderr


def _run_install_without_check(
    test_root: Path, environment_file: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(REPOSITORY_ROOT / "scripts" / "install-ubuntu.sh"),
            "--domain",
            "lcc.example.test",
            "--release-id",
            "test-release",
            "--env-file",
            str(environment_file),
            "--root",
            str(test_root),
            "--skip-build",
            "--skip-prerequisites",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_units_admin_and_update_assets_encode_production_safety() -> None:
    service = (REPOSITORY_ROOT / "deploy" / "learning-control-center.service").read_text()
    backup_service = (
        REPOSITORY_ROOT / "deploy" / "learning-control-center-backup.service"
    ).read_text()
    admin = (REPOSITORY_ROOT / "scripts" / "lcc-admin").read_text()
    updater = (REPOSITORY_ROOT / "scripts" / "update-ubuntu.sh").read_text()
    update_frontend = (REPOSITORY_ROOT / "scripts" / "update.sh").read_text()
    persistent_update = (
        REPOSITORY_ROOT / "deploy" / "learning-control-center-update.sh"
    ).read_text()
    assert "--workers 1 --no-proxy-headers" in service
    assert "EnvironmentFile=/etc/learning-control-center.env" in service
    assert "UMask=0077" in service and "NoNewPrivileges=true" in service
    assert "ReadWritePaths=/var/lib/learning-control-center" in service
    assert "ProtectSystem=strict" in backup_service
    assert "lcc_require_inactive_service" in admin
    assert (
        'LCC_ENVIRONMENT_FILE="/etc/learning-control-center.env"'
        in (REPOSITORY_ROOT / "scripts" / "deploy-common.sh").read_text()
    )
    common = (REPOSITORY_ROOT / "scripts" / "deploy-common.sh").read_text()
    assert "/usr/bin/env -i" in common
    assert 'export "$assignment"' in common
    assert "sqlite:///./data/lcc.db" not in admin
    assert "pre-update" in updater
    assert "Backup command returned an unexpected path" in updater
    assert 'test -f "$backup_path.manifest"' in updater
    assert "lcc_select_single_new_backup" in updater
    assert "did not create exactly one new backup" in common
    assert "Never select an older file" in updater
    assert "Recovery is incomplete; application and backup services remain stopped" in updater
    assert "Automatic update recovery failed" in updater
    assert "Automatic rollback recovery failed" in updater
    assert "--confirm-database-replacement" in updater
    assert "--confirm-channel-change" in updater
    assert "lcc_migration_relation" in updater
    assert "main-$source_revision" in updater
    assert "lcc-ops" in updater and "restore --from" in updater
    assert "show-bootstrap-token" in admin
    assert 'exec "$LCC_UPDATE_ENTRYPOINT" "$@"' in admin
    assert "releases?per_page=100" in update_frontend
    assert "releases?limit=100" in update_frontend
    assert "prerelease" in update_frontend and "draft" in update_frontend
    assert 'exec "$current_updater" "$@"' in persistent_update
    assert "install_update_entrypoint" in updater
    assert "Source revision:" in admin and "Channel:" in admin
    installer = (REPOSITORY_ROOT / "scripts" / "install-ubuntu.sh").read_text()
    assert "--no-build-isolation" in installer
    assert "setuptools wheel" in installer
    assert "npm --prefix" not in installer
    assert "lcc_verify_frontend_artifact" in installer
    assert "npm --prefix" not in updater
    assert "lcc_verify_runtime_prerequisites" in updater
    assert "lcc_verify_frontend_artifact" in updater
    assert installer.index("lcc_format_validate_or_restore_caddy") < installer.index(
        'run mv -Tf "$next_link"'
    )
    assert "the previous configuration was restored" in common


@pytest.mark.parametrize("failing_operation", ["fmt", "validate"])
def test_caddy_format_or_validation_failure_restores_previous_files(
    tmp_path: Path, failing_operation: str
) -> None:
    site = tmp_path / "site.caddy"
    main = tmp_path / "Caddyfile"
    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "site").write_text("old site\n")
    (backup / "main").write_text("old main\n")
    site.write_text("new site\n")
    main.write_text("new main\n")
    fake_caddy = tmp_path / "caddy"
    fake_caddy.write_text(
        "#!/usr/bin/env bash\n"
        'if test "$1" = "fmt"; then printf "formatted\\n" > "$3"; fi\n'
        f'test "$1" != "{failing_operation}"\n'
    )
    fake_caddy.chmod(0o755)
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; lcc_format_validate_or_restore_caddy "$2" "$3" "$4" "$5"',
            "caddy-rollback-test",
            str(REPOSITORY_ROOT / "scripts" / "deploy-common.sh"),
            str(site),
            str(main),
            str(backup),
            str(fake_caddy),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert site.read_text() == "old site\n"
    assert main.read_text() == "old main\n"
    assert "previous configuration was restored" in result.stderr


def test_update_channel_and_migration_preflight_contracts(tmp_path: Path) -> None:
    common = str(REPOSITORY_ROOT / "scripts" / "deploy-common.sh")
    old_sha = "1" * 40
    new_sha = "2" * 40

    def transition(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; shift; lcc_validate_update_transition "$@"',
                "transition-test",
                common,
                *arguments,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    assert transition("stable", "v1.0.0", old_sha, "stable", "v1.0.1", new_sha, "0").returncode == 0
    assert (
        transition(
            "main", f"main-{old_sha}", old_sha, "main", f"main-{new_sha}", new_sha, "0"
        ).returncode
        == 0
    )

    channel_change = transition(
        "stable", "v1.0.0", old_sha, "main", f"main-{new_sha}", new_sha, "0"
    )
    assert channel_change.returncode != 0
    assert "--confirm-channel-change" in channel_change.stderr
    assert (
        transition("stable", "v1.0.0", old_sha, "main", f"main-{new_sha}", new_sha, "1").returncode
        == 0
    )
    assert (
        transition("stable", "v1.0.0", old_sha, "main", f"main-{old_sha}", old_sha, "1").returncode
        == 0
    )
    assert (
        transition("main", f"main-{old_sha}", old_sha, "stable", "v1.0.1", new_sha, "1").returncode
        == 0
    )

    same = transition("stable", "v1.0.0", old_sha, "stable", "v1.0.0", old_sha, "0")
    assert same.returncode != 0
    assert "already active" in same.stderr
    backward = transition("stable", "v1.0.1", old_sha, "stable", "v1.0.0", new_sha, "0")
    assert backward.returncode != 0
    assert "requires a newer semantic release" in backward.stderr
    prerelease = transition("stable", "v1.0.0", old_sha, "stable", "v1.0.1-rc.1", new_sha, "0")
    assert prerelease.returncode == 0
    build_metadata = transition(
        "stable", "v1.0.0", old_sha, "stable", "v1.0.1+build.1", new_sha, "0"
    )
    assert build_metadata.returncode == 0

    revisions = sorted(
        path.stem
        for path in (REPOSITORY_ROOT / "backend" / "alembic" / "versions").glob("*.py")
        if path.name[0].isdigit()
    )
    first_revision = revisions[0]
    head_revision = revisions[-1]

    def migration_relation(current: str, candidate: str) -> str:
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; lcc_migration_relation "$2" "$2" "$3" "$4"',
                "migration-test",
                common,
                str(REPOSITORY_ROOT),
                current,
                candidate,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    assert migration_relation(head_revision, head_revision) == "same"
    assert migration_relation(first_revision, head_revision) == "forward"
    assert migration_relation(head_revision, first_revision) == "backward"
    assert migration_relation("not-a-revision", head_revision) == "divergent"

    legacy_release = tmp_path / "legacy-release"
    legacy_release.mkdir()
    legacy_revision = "artifact-sha256-" + "a" * 64
    (legacy_release / "SOURCE_REVISION").write_text(legacy_revision + "\n")
    legacy_result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; lcc_release_source_revision "$2"',
            "legacy-revision-test",
            common,
            str(legacy_release),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert legacy_result.stdout.strip() == legacy_revision
    assert "Legacy release" in legacy_result.stderr
    (legacy_release / "RELEASE_CHANNEL").write_text("stable\n")
    rejected_legacy = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; lcc_release_source_revision "$2"',
            "legacy-revision-test",
            common,
            str(legacy_release),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected_legacy.returncode != 0


def test_legacy_backup_selection_accepts_only_one_new_file(tmp_path: Path) -> None:
    common = str(REPOSITORY_ROOT / "scripts" / "deploy-common.sh")
    backup_directory = tmp_path / "backups"
    backup_directory.mkdir()
    old_backup = "lcc-pre-update-old.sqlite3"
    new_backup = "lcc-pre-update-new.sqlite3"
    extra_backup = "lcc-pre-update-extra.sqlite3"
    (backup_directory / old_backup).write_text("old")
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.write_text(f"{old_backup}\n")
    (backup_directory / new_backup).write_text("new")
    after.write_text(f"{new_backup}\n{old_backup}\n")

    selected = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; lcc_select_single_new_backup "$2" "$3" "$4"',
            "backup-selection-test",
            common,
            str(backup_directory),
            str(before),
            str(after),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert selected.stdout.strip() == str(backup_directory / new_backup)

    after.write_text(f"{extra_backup}\n{new_backup}\n{old_backup}\n")
    ambiguous = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; lcc_select_single_new_backup "$2" "$3" "$4"',
            "backup-selection-test",
            common,
            str(backup_directory),
            str(before),
            str(after),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert ambiguous.returncode != 0
    assert "exactly one new backup" in ambiguous.stderr


def test_python_constraints_cover_direct_production_dependencies() -> None:
    project = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text())
    constraints = {
        line.split("==", 1)[0].lower()
        for line in (REPOSITORY_ROOT / "requirements-production.lock").read_text().splitlines()
        if line and not line.startswith("#")
    }
    direct = {
        dependency.split("[", 1)[0].split(">", 1)[0].lower()
        for dependency in project["project"]["dependencies"]
    }
    assert direct <= constraints


def test_sanitized_release_copy_excludes_local_secrets_and_state(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "app.py").write_text("print('safe')\n")
    (source / ".env.example").write_text("SAFE=example\n")
    (source / ".env").write_text("SECRET=must-not-copy\n")
    (source / "nested").mkdir()
    (source / "nested" / ".env.production").write_text("SECRET=nested\n")
    (source / "nested" / ".netrc").write_text("password private\n")
    (source / "operator.key").write_text("private\n")
    (source / ".npmrc").write_text("//registry.example/:_authToken=private\n")
    (source / "data").mkdir()
    (source / "data" / "lcc.sqlite3").write_bytes(b"private database")
    (source / ".pytest_cache").mkdir()
    (source / ".pytest_cache" / "state").write_text("private\n")
    (source / "frontend" / "dist" / "assets").mkdir(parents=True)
    (source / "frontend" / "dist" / "index.html").write_text("packaged frontend\n")
    (source / "frontend" / "dist" / "assets" / "app.js").write_text("safe artifact\n")
    subprocess.run(
        [
            "bash",
            "-c",
            'set -euo pipefail; source "$1"; lcc_copy_release_source "$2" "$3" sanitized',
            "deployment-copy-test",
            str(REPOSITORY_ROOT / "scripts" / "deploy-common.sh"),
            str(source),
            str(destination),
        ],
        check=True,
    )
    assert (destination / "app.py").is_file()
    assert (destination / ".env.example").is_file()
    assert not (destination / ".env").exists()
    assert not (destination / "nested" / ".env.production").exists()
    assert not (destination / "nested" / ".netrc").exists()
    assert not (destination / "operator.key").exists()
    assert not (destination / ".npmrc").exists()
    assert not (destination / "data").exists()
    assert not (destination / ".pytest_cache").exists()
    assert (destination / "frontend" / "dist" / "index.html").is_file()
    assert (destination / "frontend" / "dist" / "assets" / "app.js").is_file()


@pytest.mark.skipif(os.name != "posix", reason="deployment scripts target Linux")
def test_deployment_shell_scripts_have_valid_bash_syntax() -> None:
    scripts = [
        "bootstrap-ubuntu.sh",
        "deploy-common.sh",
        "generate-production-env.sh",
        "install-ubuntu.sh",
        "lcc-admin",
        "update-ubuntu.sh",
        "uninstall-ubuntu.sh",
        "operational-backup.sh",
        "package-release.sh",
    ]
    subprocess.run(
        ["bash", "-n", *(str(REPOSITORY_ROOT / "scripts" / item) for item in scripts)],
        check=True,
    )


def test_isolated_root_cannot_resolve_to_real_root(tmp_path: Path) -> None:
    environment_file = _production_environment(tmp_path, tmp_path / "production.env")
    result = subprocess.run(
        [
            str(REPOSITORY_ROOT / "scripts" / "install-ubuntu.sh"),
            "--domain",
            "lcc.example.test",
            "--release-id",
            "test-release",
            "--env-file",
            str(environment_file),
            "--root",
            "/tmp/..",
            "--skip-build",
            "--skip-prerequisites",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "must resolve beneath /tmp" in result.stderr

    uninstall = subprocess.run(
        [
            str(REPOSITORY_ROOT / "scripts" / "uninstall-ubuntu.sh"),
            "--root",
            "/tmp/..",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert uninstall.returncode != 0
    assert "must resolve beneath /tmp" in uninstall.stderr
