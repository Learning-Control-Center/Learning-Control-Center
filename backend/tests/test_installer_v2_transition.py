"""Installer V2 transition decisions, compatibility seams, and safe uninstall."""

from __future__ import annotations

import fcntl
import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMMON = ROOT / "scripts/deploy-common.sh"
TRANSITION = ROOT / "scripts/install/transition.sh"
UNINSTALL = ROOT / "scripts/uninstall.sh"
SHA = "a" * 40


def _shell(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script, "transition-test", str(COMMON), str(TRANSITION), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_same_sha_v2_transition_is_read_only(tmp_path: Path) -> None:
    root = tmp_path / "opt/learning-control-center"
    release = root / "releases" / f"main-{SHA}"
    release.mkdir(parents=True)
    (release / "RELEASE_ID").write_text(f"main-{SHA}\n")
    (release / "RELEASE_CHANNEL").write_text("main\n")
    (release / "SOURCE_REVISION").write_text(f"{SHA}\n")
    (release / "INSTALLER_V2_CORE").write_text("1\n")
    (root / "current").symlink_to(release)
    environment = tmp_path / "lcc.env"
    environment.write_text("preserve\n")
    environment.chmod(0o600)
    previous = environment.stat().st_mtime_ns
    script = r"""
set -euo pipefail
source "$1"
source "$2"
LCC_APPLICATION_ROOT="$3"
LCC_CURRENT_RELEASE="$3/current"
LCC_ENVIRONMENT_FILE="$4"
LCC_DATABASE_FILE=/var/lib/learning-control-center/lcc.sqlite3
LCC_BACKUP_DIRECTORY_DEFAULT=/var/backups/learning-control-center
LCC_V2_SOURCE_SHA="$5"
requested_port="" domain="" timezone="" environment_source=""
gateway_explicit=0 gateway=caddy core_only=0 dry_run=0 assume_yes=0
lcc_validate_release_manifest() { :; }
lcc_validate_environment_file_security() { :; }
lcc_load_environment() {
  LCC_DATABASE_URL=sqlite:////var/lib/learning-control-center/lcc.sqlite3
  LCC_BACKUP_DIRECTORY=/var/backups/learning-control-center
  LCC_APP_PORT=8000
}
lcc_validate_environment() { :; }
lcc_transition_manifest_value() {
  case "$2" in
    source_ref) printf 'refs/heads/main\n' ;;
    *) printf 'https://github.com/Learning-Control-Center/Learning-Control-Center.git\n' ;;
  esac
}
lcc_transition_classify_gateway() { printf 'external\n'; }
lcc_transition_stage() { echo unexpected-mutation >&2; exit 79; }
lcc_acquire_deployment_lock() { :; }
lcc_transition_apply
"""
    result = _shell(script, str(root), str(environment), SHA)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Learning Control Center is already up to date."
    assert environment.read_text() == "preserve\n"
    assert environment.stat().st_mtime_ns == previous


def test_ambiguous_v1_gateway_is_refused(tmp_path: Path) -> None:
    site = tmp_path / "site.caddy"
    main = tmp_path / "Caddyfile"
    site.write_text("unrelated site\n")
    main.write_text("unrelated config\n")
    script = r"""
set -euo pipefail
source "$1"
source "$2"
LCC_DEPLOYMENT_STATE_FILE="$3/missing-state"
LCC_CADDY_SITE="$3/site.caddy"
LCC_CADDY_MAIN="$3/Caddyfile"
lcc_transition_classify_gateway "$3/legacy-release"
"""
    result = _shell(script, str(tmp_path))
    assert result.returncode != 0
    assert "ownership is ambiguous" in result.stderr
    assert site.read_text() == "unrelated site\n"
    assert main.read_text() == "unrelated config\n"


def test_v1_gateway_ownership_uses_the_installed_legacy_template(tmp_path: Path) -> None:
    release = tmp_path / "legacy-release"
    (release / "deploy").mkdir(parents=True)
    (release / "deploy/Caddyfile.template").write_text(
        "@@LCC_PUBLIC_HOST@@ {\n"
        "  root * @@LCC_FRONTEND_ROOT@@\n"
        "  reverse_proxy 127.0.0.1:@@LCC_APP_PORT@@\n"
        "}\n"
    )
    current = tmp_path / "current"
    current.symlink_to(release)
    site = tmp_path / "site.caddy"
    site.write_text(
        "lcc.example.test {\n"
        f"  root * {current}/frontend/dist\n"
        "  reverse_proxy 127.0.0.1:8000\n"
        "}\n"
    )
    main = tmp_path / "Caddyfile"
    main.write_text("import legacy-site\n")
    caddy = tmp_path / "caddy"
    caddy.write_text("#!/bin/sh\nexit 0\n")
    caddy.chmod(0o755)
    environment = tmp_path / "lcc.env"
    generated = subprocess.run(
        [
            str(ROOT / "scripts/generate-production-env.sh"),
            "--root",
            str(tmp_path / "host"),
            "--domain",
            "lcc.example.test",
            "--timezone",
            "UTC",
            "--output",
            str(environment),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert generated.returncode == 0, generated.stderr
    script = r"""
set -euo pipefail
source "$1"
source "$2"
LCC_DEPLOYMENT_STATE_FILE="$3/missing-state"
LCC_CADDY_SITE="$3/site.caddy"
LCC_CADDY_MAIN="$3/Caddyfile"
LCC_CADDY_IMPORT='import legacy-site'
LCC_CADDY_BINARY="$3/caddy"
LCC_CURRENT_RELEASE="$3/current"
lcc_load_environment "$3/lcc.env"
lcc_transition_classify_gateway "$3/legacy-release"
"""
    result = _shell(script, str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "caddy"


def test_v2_managed_caddy_requires_owned_site_and_import(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    (release / "INSTALLER_V2_CORE").write_text("1\n")
    site = tmp_path / "site.caddy"
    main = tmp_path / "Caddyfile"
    script = r"""
set -euo pipefail
source "$1"
source "$2"
LCC_CADDY_SITE="$3/site.caddy"
LCC_CADDY_MAIN="$3/Caddyfile"
lcc_transition_validate_managed_caddy "$3/release"
"""
    site.write_text("unrelated site\n")
    main.write_text("import /etc/caddy/Caddyfile.d/learning-control-center.caddy\n")
    result = _shell(script, str(tmp_path))
    assert result.returncode != 0
    assert "ownership cannot be established" in result.stderr
    site.write_text("# Managed by Learning Control Center Installer V2\n")
    main.write_text("unrelated config\n")
    result = _shell(script, str(tmp_path))
    assert result.returncode != 0
    assert "import is missing" in result.stderr
    main.write_text("import /etc/caddy/Caddyfile.d/learning-control-center.caddy\n")
    result = _shell(script, str(tmp_path))
    assert result.returncode == 0, result.stderr


def test_transition_uses_shared_deployment_lock(tmp_path: Path) -> None:
    lock = tmp_path / "deployment.lock"
    with lock.open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = _shell(
            'set -euo pipefail; source "$1"; lcc_acquire_deployment_lock "$3"', str(lock)
        )
    assert result.returncode != 0
    assert "Another LCC deployment or administrator operation" in result.stderr


def test_external_transition_snapshot_excludes_unowned_proxy(tmp_path: Path) -> None:
    (tmp_path / "Caddyfile").write_text("operator config\n")
    (tmp_path / "site.caddy").write_text("operator site\n")
    script = r"""
set -euo pipefail
source "$1"
source "$2"
LCC_SERVICE_NAME=fixture-app.service
LCC_BACKUP_SERVICE_NAME=fixture-backup.service
LCC_BACKUP_TIMER_NAME=fixture-backup.timer
LCC_CADDY_MAIN="$3/Caddyfile"
LCC_CADDY_SITE="$3/site.caddy"
LCC_UPDATE_ENTRYPOINT="$3/updater"
LCC_DEPLOYMENT_STATE_FILE="$3/state"
LCC_ENVIRONMENT_FILE="$3/env"
lcc_transition_snapshot "$3/external" external
test ! -e "$3/external/caddy-main" && test ! -e "$3/external/caddy-site"
lcc_transition_snapshot "$3/managed" caddy
test -f "$3/managed/caddy-main" && test -f "$3/managed/caddy-site"
"""
    result = _shell(script, str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "Caddyfile").read_text() == "operator config\n"


def test_external_legacy_rollback_refused_before_service_stop(tmp_path: Path) -> None:
    root = tmp_path / "opt/learning-control-center"
    old = root / "releases" / "main-current"
    target = root / "releases" / "v1.0.0"
    old.mkdir(parents=True)
    (old / "RELEASE_ID").write_text("main-current\n")
    (root / "current").symlink_to(old)
    python = target / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    (target / "RELEASE_ID").write_text("v1.0.0\n")
    script = r"""
set -euo pipefail
source "$1"
source "$2"
LCC_APPLICATION_ROOT="$3"
LCC_CURRENT_RELEASE="$3/current"
LCC_ENVIRONMENT_FILE="$4"
lcc_load_environment() { LCC_APP_PORT=8000; }
lcc_validate_environment() { :; }
lcc_transition_classify_gateway() { printf 'external\n'; }
lcc_validate_release_manifest() { :; }
lcc_transition_manifest_value() { printf 'identity\n'; }
lcc_release_source_revision() { printf '%040d\n' 0; }
lcc_acquire_deployment_lock() { :; }
id() { if test "${1:-}" = -u; then printf '0\n'; else command id "$@"; fi; }
lcc_transition_rollback --to v1.0.0
"""
    result = _shell(script, str(root), str(tmp_path / "env"))
    assert result.returncode != 0
    assert "cannot honor an external gateway" in result.stderr
    assert (root / "current").resolve() == old


def test_schema_crossing_rollback_requires_matching_backup_before_lock(tmp_path: Path) -> None:
    root = tmp_path / "opt/learning-control-center"
    old = root / "releases" / "main-current"
    target = root / "releases" / "main-previous"
    old.mkdir(parents=True)
    (old / "RELEASE_ID").write_text("main-current\n")
    (root / "current").symlink_to(old)
    python = target / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    (target / "RELEASE_ID").write_text("main-previous\n")
    (target / "INSTALLER_V2_CORE").write_text("1\n")
    script = r"""
set -euo pipefail
source "$1"
source "$2"
LCC_APPLICATION_ROOT="$3"
LCC_CURRENT_RELEASE="$3/current"
LCC_ENVIRONMENT_FILE="$4"
lcc_load_environment() { LCC_APP_PORT=8000; }
lcc_validate_environment() { :; }
lcc_transition_classify_gateway() { printf 'external\n'; }
lcc_validate_release_manifest() { :; }
lcc_transition_manifest_value() { printf 'identity\n'; }
lcc_release_source_revision() { printf '%040d\n' 0; }
lcc_transition_database_head() { printf '0020\n'; }
lcc_transition_release_head() { printf '0019\n'; }
lcc_acquire_deployment_lock() { :; }
id() { if test "${1:-}" = -u; then printf '0\n'; else command id "$@"; fi; }
lcc_transition_rollback --to main-previous
"""
    result = _shell(script, str(root), str(tmp_path / "env"))
    assert result.returncode != 0
    assert "--database-backup and --confirm-database-replacement" in result.stderr
    assert (root / "current").resolve() == old


def test_legacy_custom_port_rollback_refuses_before_lock(tmp_path: Path) -> None:
    root = tmp_path / "opt/learning-control-center"
    old = root / "releases/main-current"
    target = root / "releases/v1.0.0"
    old.mkdir(parents=True)
    (old / "RELEASE_ID").write_text("main-current\n")
    (root / "current").symlink_to(old)
    python = target / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    (target / "RELEASE_ID").write_text("v1.0.0\n")
    script = r"""
set -euo pipefail
source "$1"
source "$2"
LCC_APPLICATION_ROOT="$3"
LCC_CURRENT_RELEASE="$3/current"
lcc_load_environment() { LCC_APP_PORT=8123; }
lcc_validate_environment() { :; }
lcc_transition_classify_gateway() { printf 'caddy\n'; }
declare -A LCC_DEPLOY_ENV_SEEN=([LCC_APP_PORT]=1)
lcc_validate_release_manifest() { :; }
lcc_transition_manifest_value() { printf 'identity\n'; }
lcc_release_source_revision() { printf '%040d\n' 0; }
lcc_acquire_deployment_lock() { :; }
id() { if test "${1:-}" = -u; then printf '0\n'; else command id "$@"; fi; }
lcc_transition_rollback --to v1.0.0
"""
    result = _shell(script, str(root))
    assert result.returncode != 0
    assert "cannot honor custom internal port 8123" in result.stderr
    assert (root / "current").resolve() == old


@pytest.mark.parametrize("mode", ["external", "caddy"])
def test_generic_uninstall_preserves_data_and_owned_boundary(tmp_path: Path, mode: str) -> None:
    host = tmp_path / "host"
    application = host / "opt/learning-control-center"
    release = application / "releases/main-test"
    release.mkdir(parents=True)
    (release / "INSTALLER_V2_CORE").write_text("1\n")
    (application / "current").symlink_to(release)
    data = host / "var/lib/learning-control-center"
    backups = host / "var/backups/learning-control-center"
    data.mkdir(parents=True)
    backups.mkdir(parents=True)
    (data / "lcc.sqlite3").write_text("disposable data\n")
    (backups / "backup.sqlite3").write_text("disposable backup\n")
    state = host / "etc/learning-control-center.deployment"
    state.parent.mkdir(parents=True)
    state.write_text(f"format_version=1\ngateway={mode}\n")
    state.chmod(0o600)
    site = host / "etc/caddy/Caddyfile.d/learning-control-center.caddy"
    site.parent.mkdir(parents=True)
    site.write_text(
        "# Managed by Learning Control Center Installer V2\n"
        if mode == "caddy"
        else "unrelated operator site\n"
    )
    if mode == "caddy":
        (host / "etc/caddy/Caddyfile").write_text(
            "import /etc/caddy/Caddyfile.d/learning-control-center.caddy\n"
        )
    command = subprocess.run(
        [str(UNINSTALL), "--root", str(host)], check=False, capture_output=True, text=True
    )
    assert command.returncode == 0, command.stderr
    assert not application.exists()
    assert data.joinpath("lcc.sqlite3").read_text() == "disposable data\n"
    assert backups.joinpath("backup.sqlite3").read_text() == "disposable backup\n"
    assert site.exists() is (mode == "external")
    if mode == "external":
        assert "Remove its obsolete LCC route manually" in command.stdout


def test_generic_uninstall_requires_two_purge_opt_ins(tmp_path: Path) -> None:
    host = tmp_path / "host"
    result = subprocess.run(
        [str(UNINSTALL), "--root", str(host), "--purge-data"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "--confirm-purge=DELETE-LCC-DATA" in result.stderr
    assert not host.exists()


def test_managed_uninstall_refuses_unowned_caddy_import(tmp_path: Path) -> None:
    host = tmp_path / "host"
    release = host / "opt/learning-control-center/releases/main-test"
    release.mkdir(parents=True)
    (release / "INSTALLER_V2_CORE").write_text("1\n")
    (release.parent.parent / "current").symlink_to(release)
    state = host / "etc/learning-control-center.deployment"
    state.parent.mkdir(parents=True)
    state.write_text("format_version=1\ngateway=caddy\n")
    state.chmod(0o600)
    site = host / "etc/caddy/Caddyfile.d/learning-control-center.caddy"
    site.parent.mkdir(parents=True)
    site.write_text("# Managed by Learning Control Center Installer V2\n")
    main = host / "etc/caddy/Caddyfile"
    main.write_text("unrelated config\n")
    result = subprocess.run(
        [str(UNINSTALL), "--root", str(host)], check=False, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "import is missing" in result.stderr
    assert release.is_dir() and site.is_file()


def test_generic_uninstall_purge_removes_only_disposable_lcc_data(tmp_path: Path) -> None:
    host = tmp_path / "host"
    data = host / "var/lib/learning-control-center"
    backups = host / "var/backups/learning-control-center"
    data.mkdir(parents=True)
    backups.mkdir(parents=True)
    (data / "lcc.sqlite3").write_text("disposable\n")
    unrelated = host / "var/lib/unrelated"
    unrelated.mkdir()
    result = subprocess.run(
        [str(UNINSTALL), "--root", str(host), "--purge-data", "--confirm-purge=DELETE-LCC-DATA"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not data.exists() and not backups.exists()
    assert unrelated.is_dir()


def test_preserved_data_allows_schema_neutral_layout_reinstall(tmp_path: Path) -> None:
    host = tmp_path / "host"
    env = {
        **os.environ,
        "LCC_V2_SOURCE_ROOT": str(ROOT),
        "LCC_V2_SOURCE_REPOSITORY": "https://github.com/Learning-Control-Center/Learning-Control-Center.git",
        "LCC_V2_SOURCE_REF": "refs/heads/main",
        "LCC_V2_SOURCE_SHA": SHA,
    }
    first = subprocess.run(
        [
            str(ROOT / "scripts/install.sh"),
            "--test-root",
            str(host),
            "--gateway",
            "external",
            "--domain",
            "lcc.example.test",
            "--non-interactive",
        ],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert first.returncode == 0, first.stderr
    data = host / "var/lib/learning-control-center"
    backups = host / "var/backups/learning-control-center"
    (data / "preserved.txt").write_text("keep\n")
    (backups / "preserved.txt").write_text("keep\n")
    uninstalled = subprocess.run(
        [str(UNINSTALL), "--root", str(host)], check=False, capture_output=True, text=True
    )
    assert uninstalled.returncode == 0, uninstalled.stderr
    repeated = subprocess.run(
        [
            str(ROOT / "scripts/install.sh"),
            "--test-root",
            str(host),
            "--gateway",
            "external",
            "--domain",
            "lcc.example.test",
            "--non-interactive",
        ],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert repeated.returncode == 0, repeated.stderr
    assert (data / "preserved.txt").read_text() == "keep\n"
    assert (backups / "preserved.txt").read_text() == "keep\n"


def test_generic_uninstall_executable_and_ubuntu_bootstrap_compatibility() -> None:
    assert os.stat(UNINSTALL).st_mode & stat.S_IXUSR
    bootstrap = (ROOT / "scripts/bootstrap-ubuntu.sh").read_text()
    assert 'exec "$script_directory/bootstrap.sh" "$@"' in bootstrap
    assert "apt-get" not in bootstrap
    assert "https://raw.githubusercontent.com/" in bootstrap


def test_staged_ubuntu_compatibility_entrypoints_preserve_help_and_exit() -> None:
    bootstrap = subprocess.run(
        [str(ROOT / "scripts/bootstrap-ubuntu.sh"), "--help"],
        env={**os.environ, "LCC_V2_COMPAT_FORWARD": "1"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert bootstrap.returncode == 0
    assert "Usage: bootstrap.sh" in bootstrap.stdout
    installer = subprocess.run(
        [str(ROOT / "scripts/install-ubuntu.sh"), "--help"],
        env={**os.environ, "LCC_V2_SOURCE_ROOT": str(ROOT)},
        check=False,
        capture_output=True,
        text=True,
    )
    assert installer.returncode == 0
    assert "Usage: install.sh" in installer.stdout


def test_v2_installed_updater_delegates_without_git(tmp_path: Path) -> None:
    release = tmp_path / "release"
    scripts = release / "scripts"
    scripts.mkdir(parents=True)
    (release / "INSTALLER_V2_CORE").write_text("1\n")
    for name in ("update.sh", "deploy-common.sh"):
        (scripts / name).write_bytes((ROOT / "scripts" / name).read_bytes())
    (scripts / "update.sh").chmod(0o755)
    bootstrap = scripts / "bootstrap.sh"
    bootstrap.write_text('#!/usr/bin/env bash\nprintf \'%s\\n\' "$@" > "$LCC_TEST_ARG_LOG"\n')
    bootstrap.chmod(0o755)
    argument_log = tmp_path / "arguments.txt"
    result = subprocess.run(
        [str(scripts / "update.sh"), "--yes"],
        env={**os.environ, "LCC_TEST_ARG_LOG": str(argument_log)},
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert argument_log.read_text().splitlines() == ["--non-interactive", "--yes"]
    refused = subprocess.run(
        [str(scripts / "update.sh"), "--repository-url", "https://forgejo.invalid"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert refused.returncode != 0
    assert "Unknown update option" in refused.stderr
