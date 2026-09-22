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
    assert values["LCC_APP_PORT"] == "8000"
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


def test_units_admin_and_update_assets_encode_production_safety() -> None:
    service = (REPOSITORY_ROOT / "deploy" / "learning-control-center.service").read_text()
    backup_service = (
        REPOSITORY_ROOT / "deploy" / "learning-control-center-backup.service"
    ).read_text()
    admin = (REPOSITORY_ROOT / "scripts" / "lcc-admin").read_text()
    updater = (REPOSITORY_ROOT / "scripts" / "update.sh").read_text()
    transition = (REPOSITORY_ROOT / "scripts" / "install" / "transition.sh").read_text()
    update_compat = (REPOSITORY_ROOT / "scripts" / "update-ubuntu.sh").read_text()
    persistent_update = (
        REPOSITORY_ROOT / "deploy" / "learning-control-center-update.sh"
    ).read_text()
    assert "--workers 1 --no-proxy-headers" in service
    assert "EnvironmentFile=/etc/learning-control-center.env" in service
    assert "Environment=LCC_APP_PORT=8000" in service
    assert "--host 127.0.0.1 --port ${LCC_APP_PORT}" in service
    assert "0.0.0.0" not in service
    assert "UMask=0077" in service and "NoNewPrivileges=true" in service
    assert "ReadWritePaths=/var/lib/learning-control-center" in service
    assert "ProtectSystem=strict" in backup_service
    assert "lcc_require_inactive_service" in admin
    assert 'lcc_wait_for_internal_health "$(lcc_effective_app_port)" 120 0.5' in admin
    assert "lcc_wait_for_health 60 0.5" in admin
    assert (
        'LCC_ENVIRONMENT_FILE="/etc/learning-control-center.env"'
        in (REPOSITORY_ROOT / "scripts" / "deploy-common.sh").read_text()
    )
    common = (REPOSITORY_ROOT / "scripts" / "deploy-common.sh").read_text()
    assert "/usr/bin/env -i" in common
    assert 'export "$assignment"' in common
    assert "sqlite:///./data/lcc.db" not in admin
    assert "pre-update" in transition
    assert "lcc_select_single_new_backup" in transition
    assert "did not create exactly one new backup" in common
    assert "CRITICAL: recovery incomplete" in transition
    assert "--confirm-database-replacement" in transition
    assert "lcc_migration_relation" in transition
    assert "show-bootstrap-token" in admin
    assert 'exec "$LCC_UPDATE_ENTRYPOINT" "$@"' in admin
    assert 'exec "$script_directory/bootstrap.sh" --non-interactive "$@"' in updater
    assert 'exec "$script_directory/update.sh" "$@"' in update_compat
    assert "git ls-remote" not in updater
    assert 'exec "$current_updater" "$@"' in persistent_update
    assert "Source revision:" in admin and "Channel:" in admin
    installer = (REPOSITORY_ROOT / "scripts" / "install.sh").read_text()
    assert "--no-build-isolation" in installer
    assert "setuptools wheel" in installer
    assert "npm --prefix" not in installer
    assert "lcc_verify_frontend_artifact" in installer
    assert "npm --prefix" not in transition
    assert "lcc_verify_frontend_artifact" in transition
    assert "the previous configuration was restored" in common
    assert "lcc_apply_app_port_change" in common
    assert "app-port set PORT" in admin


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
        "bootstrap.sh",
        "release-bootstrap.sh",
        "deploy-common.sh",
        "generate-production-env.sh",
        "install-ubuntu.sh",
        "install.sh",
        "lcc-admin",
        "update-ubuntu.sh",
        "uninstall-ubuntu.sh",
        "uninstall.sh",
        "update.sh",
        "operational-backup.sh",
        "package-release.sh",
    ]
    subprocess.run(
        ["bash", "-n", *(str(REPOSITORY_ROOT / "scripts" / item) for item in scripts)],
        check=True,
    )


def test_isolated_root_cannot_resolve_to_real_root(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            str(REPOSITORY_ROOT / "scripts" / "install.sh"),
            "--test-root",
            "/tmp/..",
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
