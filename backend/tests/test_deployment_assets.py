from __future__ import annotations

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
    return destination


def _install(test_root: Path, environment_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
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
    assert (current / "scripts" / "lcc-admin").stat().st_mode & stat.S_IXUSR

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
    assert "use update-ubuntu.sh" in different_release.stderr


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
    assert "--confirm-database-replacement" in updater
    assert "lcc-ops" in updater and "restore --from" in updater
    installer = (REPOSITORY_ROOT / "scripts" / "install-ubuntu.sh").read_text()
    assert "--no-build-isolation" in installer
    assert "setuptools wheel" in installer


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


@pytest.mark.skipif(os.name != "posix", reason="deployment scripts target Linux")
def test_deployment_shell_scripts_have_valid_bash_syntax() -> None:
    scripts = [
        "deploy-common.sh",
        "install-ubuntu.sh",
        "lcc-admin",
        "update-ubuntu.sh",
        "uninstall-ubuntu.sh",
        "operational-backup.sh",
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
