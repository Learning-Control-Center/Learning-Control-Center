from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import tarfile
import tomllib
from pathlib import Path
from urllib.parse import unquote

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = REPOSITORY_ROOT / "scripts" / "bootstrap-ubuntu.sh"
PACKAGER = REPOSITORY_ROOT / "scripts" / "package-release.sh"
PROMOTER = REPOSITORY_ROOT / "scripts" / "prepare-public-promotion.sh"
GENERATOR = REPOSITORY_ROOT / "scripts" / "generate-production-env.sh"
RELEASE_ID = "v1.0.1"
ARCHIVE_NAME = f"learning-control-center-{RELEASE_ID}.tar.gz"
CHECKSUM_NAME = f"{ARCHIVE_NAME}.sha256"


def _write(path: Path, content: str | bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    path.chmod(mode)


def _copy_current(repository: Path, relative_path: str) -> None:
    source = REPOSITORY_ROOT / relative_path
    destination = repository / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _release_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "release-source"
    repository.mkdir()
    source_archive = subprocess.run(
        ["git", "-C", REPOSITORY_ROOT, "archive", "--format=tar", "HEAD"],
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(source_archive), mode="r:") as bundle:
        bundle.extractall(repository, filter="data")

    for relative_path in (
        ".gitignore",
        ".gitattributes",
        "CHANGELOG.md",
        "README.md",
        "pyproject.toml",
        "backend/app/main.py",
        "backend/app/analysis/v1_compat.py",
        "frontend/package.json",
        "frontend/package-lock.json",
        "scripts/frontend-artifact.py",
        "scripts/bootstrap-ubuntu.sh",
        "scripts/deploy-common.sh",
        "scripts/generate-production-env.sh",
        "scripts/install-ubuntu.sh",
        "scripts/lcc-admin",
        "scripts/package-release.sh",
        "scripts/prepare-public-promotion.sh",
        "scripts/update.sh",
        "scripts/update-ubuntu.sh",
        "deploy/Caddyfile",
        "deploy/Caddyfile.template",
        "deploy/learning-control-center.env.example",
        "deploy/learning-control-center.service",
        "deploy/learning-control-center-update.sh",
        "docs/INSTALLATION.md",
        "docs/PRODUCTION_OPERATIONS.md",
        "docs/RELEASING.md",
        "docs/UPDATES.md",
    ):
        _copy_current(repository, relative_path)
    shutil.copytree(
        REPOSITORY_ROOT / "frontend" / "dist",
        repository / "frontend" / "dist",
        dirs_exist_ok=True,
    )

    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", repository], check=True)
    subprocess.run(["git", "-C", repository, "config", "user.name", "WaqSea"], check=True)
    subprocess.run(
        ["git", "-C", repository, "config", "user.email", "contact@waqsea.com"],
        check=True,
    )

    private_files: dict[str, str | bytes] = {
        "AGENTS.md": "private agent instructions\n",
        "memory-bank/private.md": "private agent context\n",
        "data/lcc.db": b"private database",
        "backups/lcc.sqlite3": b"private backup",
        ".env": "PRIVATE=secret\n",
        ".npmrc": "synthetic npm credential fixture\n",
        ".pypirc": "synthetic package credential fixture\n",
        ".netrc": "synthetic network credential fixture\n",
        ".git-credentials": "synthetic Git credential fixture\n",
        "pip.conf": "synthetic pip credential fixture\n",
        ".aws/credentials": "synthetic AWS credential fixture\n",
        ".ssh/id_ed25519": "synthetic SSH credential fixture\n",
        ".docker/config.json": "synthetic Docker credential fixture\n",
        "deploy/learning-control-center.env": "PRIVATE=secret\n",
        ".abacusai/cache.txt": "private tool state\n",
        "backend/tests/fixtures/private.sqlite3": b"private fixture",
    }
    for relative_path, content in private_files.items():
        _write(repository / relative_path, content)

    _write(
        repository / "scripts" / "install-ubuntu.sh",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$@" > "${LCC_BOOTSTRAP_HANDOFF_LOG:?}"
while test "$#" -gt 0; do
    case "$1" in
        --env-file)
            stat -c '%a' "$(dirname "$2")" > "${LCC_BOOTSTRAP_ENV_MODE_CAPTURE:?}"
            stat -c '%a' "$2" >> "${LCC_BOOTSTRAP_ENV_MODE_CAPTURE:?}"
            cp "$2" "${LCC_BOOTSTRAP_ENV_CAPTURE:?}"
            chmod 0600 "${LCC_BOOTSTRAP_ENV_CAPTURE:?}"
            shift 2
            ;;
        *) shift ;;
    esac
done
printf 'fake installer invoked\\n'
""",
        0o755,
    )
    subprocess.run(["git", "-C", repository, "add", "."], check=True)
    subprocess.run(
        ["git", "-C", repository, "add", "--force", "--", *private_files],
        check=True,
    )
    subprocess.run(
        ["git", "-C", repository, "commit", "--quiet", "-m", "release fixture"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", repository, "tag", "-a", RELEASE_ID, "-m", RELEASE_ID],
        check=True,
    )
    return repository


def _package(repository: Path, output_directory: Path) -> tuple[Path, Path, Path]:
    result = subprocess.run(
        [
            PACKAGER,
            "--repository",
            repository,
            "--release-id",
            RELEASE_ID,
            "--source-ref",
            RELEASE_ID,
            "--output-dir",
            output_directory,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert f"Release identity: {RELEASE_ID}" in result.stdout
    return (
        output_directory / ARCHIVE_NAME,
        output_directory / CHECKSUM_NAME,
        output_directory / "install.sh",
    )


def _bootstrap_environment(tmp_path: Path, asset_root: Path | None = None) -> dict[str, str]:
    os_release = tmp_path / "os-release"
    os_release.write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
    secret_temp = tmp_path / "secret-temp"
    secret_temp.mkdir(exist_ok=True)
    environment = {
        **os.environ,
        "LCC_BOOTSTRAP_TESTING": "1",
        "LCC_BOOTSTRAP_OS_RELEASE": str(os_release),
        "LCC_BOOTSTRAP_HANDOFF_LOG": str(tmp_path / "handoff.log"),
        "LCC_BOOTSTRAP_ENV_CAPTURE": str(tmp_path / "generated.env"),
        "LCC_BOOTSTRAP_ENV_MODE_CAPTURE": str(tmp_path / "generated-env.modes"),
        "LCC_BOOTSTRAP_SECRET_TMPDIR": str(secret_temp),
    }
    if asset_root is not None:
        environment["LCC_BOOTSTRAP_ASSET_BASE_URL"] = asset_root.as_uri()
    return environment


def _stable_command(script: Path = BOOTSTRAP, *, dry_run: bool = False) -> list[str]:
    command = [
        str(script),
        "--channel",
        "stable",
        "--ref",
        RELEASE_ID,
        "--domain",
        "lcc.example.test",
        "--timezone",
        "UTC",
        "--non-interactive",
    ]
    if dry_run:
        command.append("--dry-run")
    return command


def test_release_packaging_is_deterministic_bounded_and_mode_preserving(
    tmp_path: Path,
) -> None:
    repository = _release_repository(tmp_path)
    first_archive, first_checksum, first_install = _package(repository, tmp_path / "first")
    second_archive, second_checksum, second_install = _package(repository, tmp_path / "second")

    assert first_archive.read_bytes() == second_archive.read_bytes()
    assert first_checksum.read_text() == second_checksum.read_text()
    assert first_install.read_bytes() == second_install.read_bytes()
    expected_hash, expected_name = first_checksum.read_text().split()
    assert expected_name == ARCHIVE_NAME
    assert hashlib.sha256(first_archive.read_bytes()).hexdigest() == expected_hash
    assert stat.S_IMODE(first_install.stat().st_mode) == 0o755
    launcher = first_install.read_text()
    assert f'readonly embedded_stable_ref="{RELEASE_ID}"' in launcher
    assert f'readonly embedded_archive_sha256="{expected_hash}"' in launcher
    assert 'readonly stable_only_launcher="1"' in launcher

    with tarfile.open(first_archive, "r:gz") as bundle:
        members = {member.name: member for member in bundle.getmembers()}
    prefix = f"Learning-Control-Center-{RELEASE_ID}/"
    expected = {
        f"{prefix}README.md",
        f"{prefix}LICENSE",
        f"{prefix}SECURITY.md",
        f"{prefix}logo.png",
        f"{prefix}frontend/public/logo.png",
        f"{prefix}frontend/dist/index.html",
        f"{prefix}frontend/dist/LCC_FRONTEND_ARTIFACT.json",
        f"{prefix}scripts/bootstrap-ubuntu.sh",
        f"{prefix}scripts/frontend-artifact.py",
        f"{prefix}scripts/generate-production-env.sh",
        f"{prefix}scripts/install-ubuntu.sh",
        f"{prefix}scripts/prepare-public-promotion.sh",
        f"{prefix}scripts/update.sh",
        f"{prefix}scripts/update-ubuntu.sh",
        f"{prefix}deploy/learning-control-center-update.sh",
        f"{prefix}RELEASE_ID",
        f"{prefix}RELEASE_CHANNEL",
        f"{prefix}SOURCE_REVISION",
        f"{prefix}RELEASE_MANIFEST",
    }
    assert expected <= members.keys()
    assert members[f"{prefix}scripts/bootstrap-ubuntu.sh"].mode & stat.S_IXUSR
    assert members[f"{prefix}scripts/generate-production-env.sh"].mode & stat.S_IXUSR
    assert members[f"{prefix}scripts/install-ubuntu.sh"].mode & stat.S_IXUSR
    assert members[f"{prefix}scripts/update.sh"].mode & stat.S_IXUSR
    assert members[f"{prefix}scripts/update-ubuntu.sh"].mode & stat.S_IXUSR
    assert members[f"{prefix}scripts/prepare-public-promotion.sh"].mode & stat.S_IXUSR
    assert members[f"{prefix}deploy/learning-control-center-update.sh"].mode & stat.S_IXUSR
    assert members[f"{prefix}scripts/frontend-artifact.py"].mode & stat.S_IXUSR
    forbidden_fragments = (
        "/memory-bank/",
        "/.git/",
        "/data/",
        "/backups/",
        "/.abacusai/",
        "/.aws/",
        "/.ssh/",
        "/.docker/",
        "/backend/tests/",
    )
    assert not any(fragment in name for name in members for fragment in forbidden_fragments)
    assert f"{prefix}AGENTS.md" not in members
    assert not any("/node_modules/" in name for name in members)
    assert not any(
        name.endswith(
            (
                "/.env",
                "/learning-control-center.env",
                "/.npmrc",
                "/.pypirc",
                "/.netrc",
                "/.git-credentials",
                "/pip.conf",
                ".db",
                ".sqlite3",
            )
        )
        for name in members
    )
    extracted = tmp_path / "extracted-release"
    with tarfile.open(first_archive, "r:gz") as bundle:
        bundle.extractall(extracted, filter="data")
    packaged_root = extracted / f"Learning-Control-Center-{RELEASE_ID}"
    subprocess.run(
        [packaged_root / "scripts" / "frontend-artifact.py", "verify", "--root", packaged_root],
        check=True,
        capture_output=True,
        text=True,
    )


def test_public_promotion_transfers_only_a_sanitized_tree(tmp_path: Path) -> None:
    if shutil.which("gitleaks") is None:
        pytest.skip("gitleaks is required by the public promotion tool")

    repository = tmp_path / "private-repository"
    repository.mkdir()
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", repository], check=True)
    subprocess.run(["git", "-C", repository, "config", "user.name", "WaqSea"], check=True)
    subprocess.run(
        ["git", "-C", repository, "config", "user.email", "contact@waqsea.com"],
        check=True,
    )
    _write(repository / "README.md", "public base\n")
    _write(repository / ".gitignore", "/memory-bank/*\n!/memory-bank/*.md\n")
    shutil.copy2(REPOSITORY_ROOT / ".gitleaks.toml", repository / ".gitleaks.toml")
    subprocess.run(["git", "-C", repository, "add", "."], check=True)
    subprocess.run(
        ["git", "-C", repository, "commit", "--quiet", "-m", "public base"],
        check=True,
    )
    base_commit = subprocess.run(
        ["git", "-C", repository, "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    subprocess.run(["git", "-C", repository, "switch", "--quiet", "-c", "dev"], check=True)
    subprocess.run(
        ["git", "-C", repository, "config", "user.email", "waqsea@waqsea.com"],
        check=True,
    )
    _write(repository / "README.md", "validated public change\n")
    _write(repository / "AGENTS.md", "private agent instructions\n")
    _write(repository / "memory-bank" / "private.md", "private context\n")
    subprocess.run(["git", "-C", repository, "add", "."], check=True)
    subprocess.run(
        ["git", "-C", repository, "commit", "--quiet", "-m", "private development"],
        check=True,
    )
    private_commit = subprocess.run(
        ["git", "-C", repository, "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    candidate = tmp_path / "public-candidate"
    result = subprocess.run(
        [
            PROMOTER,
            "--repository",
            repository,
            "--source-ref",
            "dev",
            "--public-base",
            "main",
            "--output-dir",
            candidate,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Public candidate is ready" in result.stdout
    assert (candidate / "README.md").read_text() == "validated public change\n"
    assert not (candidate / "AGENTS.md").exists()
    assert not (candidate / "memory-bank").exists()
    public_ignore = (candidate / ".gitignore").read_text().splitlines()
    assert "/AGENTS.md" in public_ignore
    assert "/memory-bank/" in public_ignore
    assert (
        subprocess.run(
            ["git", "-C", candidate, "rev-list", "--count", "main"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == "2"
    )
    assert (
        subprocess.run(
            ["git", "-C", candidate, "rev-parse", "main^"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == base_commit
    )
    assert (
        subprocess.run(
            ["git", "-C", candidate, "cat-file", "-e", f"{private_commit}^{{commit}}"],
            check=False,
            capture_output=True,
        ).returncode
        != 0
    )
    metadata = subprocess.run(
        ["git", "-C", candidate, "log", "--format=%ae%n%ce", "main"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "waqsea@waqsea.com" not in metadata


def test_frontend_artifact_is_verified_and_bound_to_source(tmp_path: Path) -> None:
    verifier = REPOSITORY_ROOT / "scripts" / "frontend-artifact.py"
    verified = subprocess.run(
        [verifier, "verify", "--root", REPOSITORY_ROOT],
        check=False,
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0
    manifest = json.loads(
        (REPOSITORY_ROOT / "frontend" / "dist" / "LCC_FRONTEND_ARTIFACT.json").read_text()
    )
    assert manifest["schema_version"] == 1
    assert re.fullmatch(r"[0-9a-f]{64}", manifest["source_sha256"])
    assert re.fullmatch(r"[0-9a-f]{64}", manifest["artifact_sha256"])
    assert manifest["artifact_file_count"] > 1

    copied = tmp_path / "candidate"
    shutil.copytree(REPOSITORY_ROOT, copied, ignore=shutil.ignore_patterns("node_modules", ".git"))
    (copied / "frontend" / "dist" / "index.html").write_text("tampered")
    rejected = subprocess.run(
        [copied / "scripts" / "frontend-artifact.py", "verify", "--root", copied],
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode != 0
    assert "stale or modified" in rejected.stderr

    (copied / "frontend" / "dist" / "LCC_FRONTEND_ARTIFACT.json").unlink()
    missing = subprocess.run(
        [copied / "scripts" / "frontend-artifact.py", "verify", "--root", copied],
        check=False,
        capture_output=True,
        text=True,
    )
    assert missing.returncode != 0
    assert "manifest is missing or unsafe" in missing.stderr


def test_prerequisite_provisioning_uses_only_explicit_ubuntu_packages(tmp_path: Path) -> None:
    repository = _release_repository(tmp_path)
    asset_root = tmp_path / "assets"
    _package(repository, asset_root / RELEASE_ID)
    apt_log = tmp_path / "apt.log"
    environment = {
        **_bootstrap_environment(tmp_path, asset_root),
        "LCC_BOOTSTRAP_TEST_MISSING_PACKAGES": "python3-venv,sqlite3,rsync,caddy,iproute2",
        "LCC_BOOTSTRAP_APT_LOG": str(apt_log),
    }
    result = subprocess.run(
        _stable_command(),
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    commands = apt_log.read_text().splitlines()
    assert commands[0] == "update"
    assert commands[1].split() == [
        "install",
        "python3-venv",
        "sqlite3",
        "rsync",
        "caddy",
        "iproute2",
    ]
    assert "upgrade" not in apt_log.read_text()
    assert "full-upgrade" not in apt_log.read_text()
    assert "Ubuntu 24.04 signed repositories only" in result.stdout
    assert "Node.js/npm are not installed" in result.stdout


def test_prerequisite_dry_run_plans_without_apt_mutation(tmp_path: Path) -> None:
    repository = _release_repository(tmp_path)
    asset_root = tmp_path / "assets"
    _package(repository, asset_root / RELEASE_ID)
    apt_log = tmp_path / "apt.log"
    environment = {
        **_bootstrap_environment(tmp_path, asset_root),
        "LCC_BOOTSTRAP_TEST_MISSING_PACKAGES": "caddy,sqlite3",
        "LCC_BOOTSTRAP_APT_LOG": str(apt_log),
    }
    result = subprocess.run(
        _stable_command(dry_run=True),
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert not apt_log.exists()
    assert "DRY-RUN: would run apt-get update and install: sqlite3 caddy" in result.stdout


def test_prerequisite_provisioning_refuses_unexpected_isolated_package_indexes(
    tmp_path: Path,
) -> None:
    apt_log = tmp_path / "apt.log"
    environment = {
        **_bootstrap_environment(tmp_path),
        "LCC_BOOTSTRAP_TEST_MISSING_PACKAGES": "caddy",
        "LCC_BOOTSTRAP_TEST_APT_INDEX_TARGETS": (
            "Packages|Ubuntu|Ubuntu|noble|http://archive.ubuntu.com/ubuntu|"
            "/usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
            "Packages|Vendor|Vendor|stable|https://packages.example.test/repo|"
            "/usr/share/keyrings/vendor.gpg"
        ),
        "LCC_BOOTSTRAP_APT_LOG": str(apt_log),
    }
    result = subprocess.run(
        _stable_command(),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode != 0
    assert "isolated Ubuntu APT view returned an unexpected package index" in result.stderr
    assert "packages.example.test" in result.stderr
    assert apt_log.read_text().splitlines() == ["update"]


def test_prerequisite_dry_run_preserves_unrelated_package_source(
    tmp_path: Path,
) -> None:
    sources = tmp_path / "apt" / "sources.list.d"
    sources.mkdir(parents=True)
    (sources / "ubuntu.sources").write_text(
        "Types: deb\nURIs: http://archive.ubuntu.com/ubuntu\n"
        "Suites: noble noble-updates\nComponents: main universe\n"
        "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
    )
    vendor = sources / "vendor.sources"
    vendor_content = (
        "Types: deb\nURIs: https://apt.example.test/repo\n"
        "Suites: stable\nComponents: main\n"
        "Signed-By: /usr/share/keyrings/vendor.gpg\n"
    )
    vendor.write_text(vendor_content)
    captured = tmp_path / "isolated.sources"
    environment = {
        **_bootstrap_environment(tmp_path),
        "LCC_BOOTSTRAP_TEST_MISSING_PACKAGES": "caddy",
        "LCC_BOOTSTRAP_TEST_APT_SOURCE_ROOT": str(sources.parent),
        "LCC_BOOTSTRAP_TEST_APT_SOURCE_CAPTURE": str(captured),
        "LCC_BOOTSTRAP_ASSET_BASE_URL": (tmp_path / "missing-assets").as_uri(),
    }
    result = subprocess.run(
        _stable_command(dry_run=True),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode != 0
    assert "stable release acquisition" in result.stderr
    assert "archive.ubuntu.com" in captured.read_text()
    assert "apt.example.test" not in captured.read_text()
    assert vendor.read_text() == vendor_content


def test_prerequisite_provisioning_rejects_spoofed_ubuntu_source(
    tmp_path: Path,
) -> None:
    sources = tmp_path / "apt" / "sources.list.d"
    sources.mkdir(parents=True)
    (sources / "spoofed.sources").write_text(
        "Types: deb\nURIs: https://spoofed.example.test/ubuntu\n"
        "Suites: noble\nComponents: main universe\n"
        "Signed-By: /usr/share/keyrings/vendor.gpg\n"
    )
    environment = {
        **_bootstrap_environment(tmp_path),
        "LCC_BOOTSTRAP_TEST_MISSING_PACKAGES": "caddy",
        "LCC_BOOTSTRAP_TEST_APT_SOURCE_ROOT": str(sources.parent),
    }
    result = subprocess.run(
        _stable_command(dry_run=True),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode != 0
    assert "No trusted Ubuntu 24.04 APT source" in result.stderr


@pytest.mark.parametrize(
    ("variable", "message"),
    (
        ("LCC_BOOTSTRAP_TEST_DPKG_AUDIT_FAILURE", "dpkg reports unfinished"),
        ("LCC_BOOTSTRAP_TEST_APT_CHECK_FAILURE", "APT dependency state is broken"),
        ("LCC_BOOTSTRAP_TEST_UBUNTU_KEYRING_FAILURE", "Ubuntu archive keyring"),
        ("LCC_BOOTSTRAP_TEST_UNMANAGED_CADDY", "unmanaged Caddy"),
        ("LCC_BOOTSTRAP_TEST_CADDY_PATH", "package-owned /usr/bin/caddy"),
        ("LCC_BOOTSTRAP_TEST_CADDY_CONFIG_FAILURE", "existing Caddy configuration is invalid"),
        ("LCC_BOOTSTRAP_TEST_PORT_CONFLICT", "Ports 80 or 443"),
    ),
)
def test_prerequisite_preflight_refuses_unsafe_host_state(
    tmp_path: Path, variable: str, message: str
) -> None:
    environment = _bootstrap_environment(tmp_path)
    environment[variable] = "/usr/local/bin/caddy" if variable.endswith("CADDY_PATH") else "1"
    result = subprocess.run(
        _stable_command(dry_run=True),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode != 0
    assert message in result.stderr


def test_prerequisite_install_failure_is_phased_and_rerunnable(tmp_path: Path) -> None:
    apt_log = tmp_path / "apt.log"
    environment = {
        **_bootstrap_environment(tmp_path),
        "LCC_BOOTSTRAP_TEST_MISSING_PACKAGES": "caddy",
        "LCC_BOOTSTRAP_TEST_APT_INSTALL_FAILURE": "1",
        "LCC_BOOTSTRAP_APT_LOG": str(apt_log),
    }
    failed = subprocess.run(
        _stable_command(),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert failed.returncode != 0
    assert "failed during phase: prerequisite install" in failed.stderr
    assert "correct the error and rerun safely" in failed.stderr
    assert apt_log.read_text().splitlines() == ["update", "install caddy"]


def test_prerequisite_metadata_failure_stops_before_install(tmp_path: Path) -> None:
    apt_log = tmp_path / "apt.log"
    environment = {
        **_bootstrap_environment(tmp_path),
        "LCC_BOOTSTRAP_TEST_MISSING_PACKAGES": "caddy",
        "LCC_BOOTSTRAP_TEST_APT_UPDATE_FAILURE": "1",
        "LCC_BOOTSTRAP_APT_LOG": str(apt_log),
    }
    failed = subprocess.run(
        _stable_command(),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert failed.returncode != 0
    assert "failed during phase: package metadata" in failed.stderr
    assert apt_log.read_text().splitlines() == ["update"]


@pytest.mark.parametrize(
    ("environment_update", "message"),
    (
        ({"LCC_BOOTSTRAP_TEST_ARCHITECTURE": "riscv64"}, "Supported architectures"),
        ({"LCC_BOOTSTRAP_TEST_AVAILABLE_KIB": "1024"}, "At least 1 GiB"),
    ),
)
def test_prerequisite_preflight_rejects_unsupported_host_shape(
    tmp_path: Path, environment_update: dict[str, str], message: str
) -> None:
    environment = {**_bootstrap_environment(tmp_path), **environment_update}
    result = subprocess.run(
        _stable_command(dry_run=True),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode != 0
    assert message in result.stderr


def test_prerequisite_preflight_rejects_unsupported_ubuntu(tmp_path: Path) -> None:
    environment = _bootstrap_environment(tmp_path)
    os_release = tmp_path / "old-os-release"
    os_release.write_text('ID=ubuntu\nVERSION_ID="22.04"\n')
    environment["LCC_BOOTSTRAP_OS_RELEASE"] = str(os_release)
    result = subprocess.run(
        _stable_command(dry_run=True),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode != 0
    assert "Ubuntu Server 24.04 LTS" in result.stderr


def test_existing_newer_install_refuses_downgrade_before_package_changes(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "installed-root"
    active = install_root / "opt" / "learning-control-center" / "releases" / "v1.0.2"
    active.mkdir(parents=True)
    (active / "RELEASE_ID").write_text("v1.0.2\n")
    (active / "RELEASE_CHANNEL").write_text("stable\n")
    (active / "SOURCE_REVISION").write_text("1" * 40 + "\n")
    (active.parent.parent / "current").symlink_to(active)
    apt_log = tmp_path / "apt.log"
    environment = {
        **_bootstrap_environment(tmp_path),
        "LCC_BOOTSTRAP_INSTALL_ROOT": str(install_root),
        "LCC_BOOTSTRAP_TEST_MISSING_PACKAGES": "caddy",
        "LCC_BOOTSTRAP_APT_LOG": str(apt_log),
    }
    result = subprocess.run(
        _stable_command(dry_run=True),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode != 0
    assert "rollback workflow for downgrades" in result.stderr
    assert not apt_log.exists()


def test_main_and_stable_share_prerequisites_without_server_node() -> None:
    source = BOOTSTRAP.read_text()
    provision_start = source.index("provision_prerequisites()")
    provision_end = source.index("verify_provisioned_commands()")
    provision = source[provision_start:provision_end]
    required_packages = re.search(r"required_packages=\((.*?)\n    \)", provision, re.DOTALL)
    assert required_packages is not None
    assert "git" in required_packages.group(1).split()
    assert "required_packages+=(git)" not in provision
    assert "nodejs" not in required_packages.group(1)
    assert "npm" not in required_packages.group(1).split()
    assert "apt-key" not in source
    assert "deb.nodesource" not in source
    assert "dl.cloudsmith" not in source
    assert "full-upgrade" not in source
    assert "npm --prefix" not in (REPOSITORY_ROOT / "scripts" / "install-ubuntu.sh").read_text()
    assert "npm --prefix" not in (REPOSITORY_ROOT / "scripts" / "update-ubuntu.sh").read_text()


def test_main_is_default_and_non_interactive_use_requires_exact_commit(tmp_path: Path) -> None:
    missing = subprocess.run(
        [BOOTSTRAP, "--non-interactive", "--domain", "lcc.example.test", "--timezone", "UTC"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert missing.returncode != 0
    assert "Direct non-interactive main installation requires --commit" in missing.stderr

    moving = subprocess.run(
        [
            BOOTSTRAP,
            "--channel",
            "stable",
            "--ref",
            "main",
            "--non-interactive",
            "--domain",
            "lcc.example.test",
            "--timezone",
            "UTC",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert moving.returncode != 0
    assert "semantic release tag" in moving.stderr


def test_piped_main_bootstrap_reexecutes_immutable_stage_before_host_mutation(
    tmp_path: Path,
) -> None:
    revision = "a" * 40
    stage_log = tmp_path / "stage.log"
    pinned_bootstrap = tmp_path / "pinned-bootstrap.sh"
    pinned_bootstrap.write_text(
        "\n".join(
            (
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'printf "%s\\n" "$LCC_BOOTSTRAP_PINNED_REVISION" > "$LCC_STAGE_TEST_LOG"',
                'printf "%s\\n" "$LCC_BOOTSTRAP_PINNED_REPOSITORY" >> "$LCC_STAGE_TEST_LOG"',
                'printf "%s\\n" "$@" >> "$LCC_STAGE_TEST_LOG"',
                "",
            )
        )
    )
    environment = {
        **os.environ,
        "LCC_BOOTSTRAP_TESTING": "1",
        "LCC_BOOTSTRAP_STAGE_TEST_RESOLVED_SHA": revision,
        "LCC_BOOTSTRAP_STAGE_TEST_SCRIPT": str(pinned_bootstrap),
        "LCC_STAGE_TEST_LOG": str(stage_log),
        "LCC_BOOTSTRAP_TEST_DPKG_AUDIT_FAILURE": "1",
        "TMPDIR": str(tmp_path),
    }
    result = subprocess.run(
        [
            "bash",
            "-s",
            "--",
            "--dry-run",
            "--non-interactive",
            "--domain",
            "lcc.example.test",
            "--app-port",
            "8123",
        ],
        input=BOOTSTRAP.read_text(),
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert f"Stage zero resolved main revision: {revision}" in result.stdout
    assert stage_log.read_text().splitlines() == [
        revision,
        "https://github.com/Learning-Control-Center/Learning-Control-Center.git",
        "--dry-run",
        "--non-interactive",
        "--domain",
        "lcc.example.test",
        "--app-port",
        "8123",
    ]
    assert not list(tmp_path.glob("lcc-bootstrap-stage.*"))


def test_bootstrap_generates_secure_environment_and_hands_off_without_secrets(
    tmp_path: Path,
) -> None:
    repository = _release_repository(tmp_path)
    asset_root = tmp_path / "assets"
    _package(repository, asset_root / RELEASE_ID)
    temporary_root = tmp_path / "bootstrap-temp"
    temporary_root.mkdir()
    environment = {
        **_bootstrap_environment(tmp_path, asset_root),
        "TMPDIR": str(temporary_root),
    }

    installed = subprocess.run(
        _stable_command(),
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    handoff = (tmp_path / "handoff.log").read_text().splitlines()
    generated = (tmp_path / "generated.env").read_text()
    values = dict(
        line.split("=", 1) for line in generated.splitlines() if line and not line.startswith("#")
    )
    security_secret = values["LCC_SECURITY_SECRET"]
    bootstrap_token = values["LCC_BOOTSTRAP_TOKEN"]
    assert values["LCC_APP_PORT"] == "8000"
    assert security_secret != bootstrap_token
    assert len(security_secret) >= 32 and len(bootstrap_token) >= 32
    assert stat.S_IMODE((tmp_path / "generated.env").stat().st_mode) == 0o600
    assert (tmp_path / "generated-env.modes").read_text().splitlines() == ["700", "600"]
    assert handoff[:2] == ["--domain", "lcc.example.test"]
    assert "--channel" in handoff and "stable" in handoff
    assert "--release-id" in handoff and RELEASE_ID in handoff
    assert "--source-revision" in handoff
    assert "--source-repository" in handoff
    combined_output = installed.stdout + installed.stderr + "\n".join(handoff)
    assert security_secret not in combined_output
    assert bootstrap_token not in combined_output
    assert f"Verified stable release: {RELEASE_ID}" in installed.stdout
    assert list(temporary_root.iterdir()) == []
    assert list((tmp_path / "secret-temp").iterdir()) == []


def test_bootstrap_app_port_noninteractive_selection_and_conflicts(tmp_path: Path) -> None:
    repository = _release_repository(tmp_path)
    asset_root = tmp_path / "assets"
    _archive, _checksum, launcher = _package(repository, asset_root / RELEASE_ID)

    custom_root = tmp_path / "custom"
    custom_root.mkdir()
    custom_environment = _bootstrap_environment(custom_root, asset_root)
    custom = subprocess.run(
        [*_stable_command(), "--app-port", "8123"],
        check=True,
        capture_output=True,
        text=True,
        env=custom_environment,
    )
    assert "LCC_APP_PORT=8123" in (custom_root / "generated.env").read_text()
    assert "fake installer invoked" in custom.stdout

    occupied_root = tmp_path / "occupied"
    occupied_root.mkdir()
    occupied_environment = _bootstrap_environment(occupied_root, asset_root)
    occupied_environment["LCC_BOOTSTRAP_TEST_OCCUPIED_APP_PORTS"] = "8000"
    occupied = subprocess.run(
        _stable_command(),
        check=False,
        capture_output=True,
        text=True,
        env=occupied_environment,
    )
    assert occupied.returncode != 0
    assert "rerun with --app-port PORT" in occupied.stderr
    assert "process=test-listener pid=4242" in occupied.stderr

    explicit_root = tmp_path / "explicit-occupied"
    explicit_root.mkdir()
    explicit_environment = _bootstrap_environment(explicit_root, asset_root)
    explicit_environment["LCC_BOOTSTRAP_TEST_OCCUPIED_APP_PORTS"] = "8123"
    explicit = subprocess.run(
        [*_stable_command(), "--app-port", "8123"],
        check=False,
        capture_output=True,
        text=True,
        env=explicit_environment,
    )
    assert explicit.returncode != 0
    assert "Explicitly requested internal application port 8123 is already occupied" in (
        explicit.stderr
    )

    environment_file = tmp_path / "operator.env"
    environment_file.write_text("LCC_APP_PORT=8123\n")
    conflicting_authorities = subprocess.run(
        [launcher, "--env-file", environment_file, "--app-port", "8124"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert conflicting_authorities.returncode != 0
    assert "cannot be combined" in conflicting_authorities.stderr


@pytest.mark.skipif(shutil.which("script") is None, reason="PTY helper is unavailable")
def test_piped_style_interactive_app_port_conflict_reads_from_terminal(tmp_path: Path) -> None:
    repository = _release_repository(tmp_path)
    asset_root = tmp_path / "assets"
    _archive, _checksum, launcher = _package(repository, asset_root / RELEASE_ID)
    environment = _bootstrap_environment(tmp_path, asset_root)
    environment["LCC_BOOTSTRAP_TEST_OCCUPIED_APP_PORTS"] = "8000"
    pipeline = f"cat {shlex.quote(str(launcher))} | bash"
    result = subprocess.run(
        ["script", "-qec", pipeline, "/dev/null"],
        input="\nlcc.example.test\nEurope/Istanbul\ny\n",
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert "Internal application port [8001]" in result.stdout
    assert "Listener: 127.0.0.1:8000" in result.stdout
    assert "Internal endpoint: 127.0.0.1:8001" in result.stdout
    assert "LCC_APP_PORT=8001" in (tmp_path / "generated.env").read_text()


def test_generated_stable_launcher_is_release_bound_and_rejects_identity_override(
    tmp_path: Path,
) -> None:
    repository = _release_repository(tmp_path)
    asset_root = tmp_path / "assets"
    _archive, _checksum, launcher = _package(repository, asset_root / RELEASE_ID)
    environment = _bootstrap_environment(tmp_path, asset_root)
    installed = subprocess.run(
        [
            launcher,
            "--domain",
            "lcc.example.test",
            "--timezone",
            "UTC",
            "--non-interactive",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert f"Verified stable release: {RELEASE_ID}" in installed.stdout

    for arguments in (
        ["--channel", "main"],
        ["--ref", "v9.9.9"],
        ["--commit", "0" * 40],
        ["--repository-url", "https://example.invalid/repository.git"],
    ):
        rejected = subprocess.run(
            [launcher, *arguments],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        assert rejected.returncode != 0
        assert "release-bound install.sh" in rejected.stderr


def _write_active_release(
    root: Path, *, release_id: str, channel: str, revision: str, legacy_v1: bool = False
) -> None:
    release = root / "opt" / "learning-control-center" / "releases" / release_id
    release.mkdir(parents=True)
    (release / "RELEASE_ID").write_text(f"{release_id}\n")
    if legacy_v1:
        revision = "artifact-sha256-" + "a" * 64
    else:
        (release / "RELEASE_CHANNEL").write_text(f"{channel}\n")
    (release / "SOURCE_REVISION").write_text(f"{revision}\n")
    current = root / "opt" / "learning-control-center" / "current"
    current.symlink_to(release)


def test_release_bound_launcher_rerun_delegates_updates_and_handles_version_order(
    tmp_path: Path,
) -> None:
    repository = _release_repository(tmp_path)
    asset_root = tmp_path / "assets"
    _archive, _checksum, launcher = _package(repository, asset_root / RELEASE_ID)
    target_revision = subprocess.run(
        ["git", "-C", repository, "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    older_root = tmp_path / "older-root"
    _write_active_release(
        older_root,
        release_id="v1.0.0",
        channel="stable",
        revision="1" * 40,
        legacy_v1=True,
    )
    older_installed_environment = older_root / "etc" / "learning-control-center.env"
    older_installed_environment.parent.mkdir(parents=True)
    older_installed_environment.write_text("LCC_APP_PORT=8123\n")
    (tmp_path / "older").mkdir(exist_ok=True)
    older_environment = _bootstrap_environment(tmp_path / "older", asset_root)
    older_environment.update(
        {
            "LCC_BOOTSTRAP_INSTALL_ROOT": str(older_root),
            "LCC_BOOTSTRAP_UPDATE_HANDOFF_LOG": str(tmp_path / "update-handoff.log"),
        }
    )
    updated = subprocess.run(
        [launcher, "--non-interactive"],
        check=True,
        capture_output=True,
        text=True,
        env=older_environment,
    )
    update_arguments = (tmp_path / "update-handoff.log").read_text().splitlines()
    assert "apply" in update_arguments
    assert RELEASE_ID in update_arguments
    assert target_revision in update_arguments
    assert "canonical update engine" in updated.stdout
    assert older_installed_environment.read_text() == "LCC_APP_PORT=8123\n"

    same_root = tmp_path / "same-root"
    _write_active_release(
        same_root, release_id=RELEASE_ID, channel="stable", revision=target_revision
    )
    installed_environment = same_root / "etc" / "learning-control-center.env"
    installed_environment.parent.mkdir(parents=True)
    installed_environment.write_text("LCC_APP_PORT=8123\n")
    (tmp_path / "same").mkdir(exist_ok=True)
    same_environment = _bootstrap_environment(tmp_path / "same", asset_root)
    same_environment["LCC_BOOTSTRAP_INSTALL_ROOT"] = str(same_root)
    same = subprocess.run(
        [launcher, "--non-interactive"],
        check=True,
        capture_output=True,
        text=True,
        env=same_environment,
    )
    assert same.stdout.strip().endswith("Learning Control Center is already up to date.")
    assert installed_environment.read_text() == "LCC_APP_PORT=8123\n"
    rejected_port_change = subprocess.run(
        [launcher, "--non-interactive", "--app-port", "8124"],
        check=False,
        capture_output=True,
        text=True,
        env=same_environment,
    )
    assert rejected_port_change.returncode != 0
    assert "sudo lcc-admin app-port set 8124" in rejected_port_change.stderr
    assert installed_environment.read_text() == "LCC_APP_PORT=8123\n"

    newer_root = tmp_path / "newer-root"
    _write_active_release(newer_root, release_id="v1.0.2", channel="stable", revision="2" * 40)
    (tmp_path / "newer").mkdir(exist_ok=True)
    newer_environment = _bootstrap_environment(tmp_path / "newer", asset_root)
    newer_environment["LCC_BOOTSTRAP_INSTALL_ROOT"] = str(newer_root)
    refused = subprocess.run(
        [launcher, "--non-interactive"],
        check=False,
        capture_output=True,
        text=True,
        env=newer_environment,
    )
    assert refused.returncode != 0
    assert "rollback workflow for downgrades" in refused.stderr


def test_release_bound_launcher_requires_explicit_main_to_stable_channel_change(
    tmp_path: Path,
) -> None:
    repository = _release_repository(tmp_path)
    asset_root = tmp_path / "assets"
    _archive, _checksum, launcher = _package(repository, asset_root / RELEASE_ID)
    root = tmp_path / "main-root"
    _write_active_release(
        root,
        release_id=f"main-{'3' * 40}",
        channel="main",
        revision="3" * 40,
    )
    (tmp_path / "main-installed").mkdir(exist_ok=True)
    environment = _bootstrap_environment(tmp_path / "main-installed", asset_root)
    environment.update(
        {
            "LCC_BOOTSTRAP_INSTALL_ROOT": str(root),
            "LCC_BOOTSTRAP_UPDATE_HANDOFF_LOG": str(tmp_path / "channel-handoff.log"),
        }
    )
    refused = subprocess.run(
        [launcher, "--non-interactive"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert refused.returncode != 0
    assert "requires --confirm-channel-change" in refused.stderr

    accepted = subprocess.run(
        [launcher, "--non-interactive", "--confirm-channel-change"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert "canonical update engine" in accepted.stdout
    assert (tmp_path / "channel-handoff.log").read_text().splitlines()[-1] == (
        "--confirm-channel-change"
    )


@pytest.mark.skipif(shutil.which("script") is None, reason="PTY helper is unavailable")
def test_generated_stable_launcher_prompts_only_for_host_timezone_and_confirmation(
    tmp_path: Path,
) -> None:
    repository = _release_repository(tmp_path)
    asset_root = tmp_path / "assets"
    _archive, _checksum, launcher = _package(repository, asset_root / RELEASE_ID)
    environment = _bootstrap_environment(tmp_path, asset_root)
    result = subprocess.run(
        ["script", "-qec", shlex.quote(str(launcher)), "/dev/null"],
        input="lcc.example.test\nEurope/Istanbul\ny\n",
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert "Public hostname" in result.stdout
    assert "Application timezone" in result.stdout
    assert "Continue with this stable installation?" in result.stdout
    assert "LCC_SECURITY_SECRET" not in result.stdout
    assert "LCC_BOOTSTRAP_TOKEN" not in result.stdout
    generated = (tmp_path / "generated.env").read_text()
    secrets = [
        line.split("=", 1)[1]
        for line in generated.splitlines()
        if line.startswith(("LCC_SECURITY_SECRET=", "LCC_BOOTSTRAP_TOKEN="))
    ]
    assert all(secret not in result.stdout + result.stderr for secret in secrets)
    assert (tmp_path / "handoff.log").is_file()


def test_bootstrap_rejects_checksum_mismatch(tmp_path: Path) -> None:
    repository = _release_repository(tmp_path)
    asset_root = tmp_path / "assets"
    archive, _checksum, _install = _package(repository, asset_root / RELEASE_ID)
    archive.write_bytes(archive.read_bytes() + b"tampered")
    result = subprocess.run(
        _stable_command(dry_run=True),
        check=False,
        capture_output=True,
        text=True,
        env=_bootstrap_environment(tmp_path, asset_root),
    )
    assert result.returncode != 0
    assert "SHA-256 verification failed" in result.stderr


def test_bootstrap_rejects_archive_traversal_and_duplicate_members(tmp_path: Path) -> None:
    asset_root = tmp_path / "assets"
    release_assets = asset_root / RELEASE_ID
    release_assets.mkdir(parents=True)
    archive = release_assets / ARCHIVE_NAME
    payload = b"unsafe"
    with tarfile.open(archive, "w:gz") as bundle:
        member = tarfile.TarInfo(f"Learning-Control-Center-{RELEASE_ID}/../../escape")
        member.size = len(payload)
        bundle.addfile(member, io.BytesIO(payload))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (release_assets / CHECKSUM_NAME).write_text(f"{digest}  {ARCHIVE_NAME}\n")
    result = subprocess.run(
        _stable_command(dry_run=True),
        check=False,
        capture_output=True,
        text=True,
        env=_bootstrap_environment(tmp_path, asset_root),
    )
    assert result.returncode != 0
    assert "unsafe release path" in result.stderr
    assert not (tmp_path / "escape").exists()

    with tarfile.open(archive, "w:gz") as bundle:
        for _ in range(2):
            member = tarfile.TarInfo(f"Learning-Control-Center-{RELEASE_ID}/duplicate")
            member.size = len(payload)
            bundle.addfile(member, io.BytesIO(payload))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (release_assets / CHECKSUM_NAME).write_text(f"{digest}  {ARCHIVE_NAME}\n")
    duplicate = subprocess.run(
        _stable_command(dry_run=True),
        check=False,
        capture_output=True,
        text=True,
        env=_bootstrap_environment(tmp_path, asset_root),
    )
    assert duplicate.returncode != 0
    assert "duplicate release path" in duplicate.stderr

    with tarfile.open(archive, "w:gz") as bundle:
        member = tarfile.TarInfo(f"Learning-Control-Center-{RELEASE_ID}/unsafe-link")
        member.type = tarfile.SYMTYPE
        member.linkname = "/etc/passwd"
        bundle.addfile(member)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (release_assets / CHECKSUM_NAME).write_text(f"{digest}  {ARCHIVE_NAME}\n")
    unsafe_type = subprocess.run(
        _stable_command(dry_run=True),
        check=False,
        capture_output=True,
        text=True,
        env=_bootstrap_environment(tmp_path, asset_root),
    )
    assert unsafe_type.returncode != 0
    assert "unsupported release member type" in unsafe_type.stderr

    with tarfile.open(archive, "w:gz") as bundle:
        member = tarfile.TarInfo(f"Learning-Control-Center-{RELEASE_ID}/oversized")
        member.size = len(payload)
        bundle.addfile(member, io.BytesIO(payload))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (release_assets / CHECKSUM_NAME).write_text(f"{digest}  {ARCHIVE_NAME}\n")
    size_environment = _bootstrap_environment(tmp_path, asset_root)
    size_environment["LCC_BOOTSTRAP_TEST_MAXIMUM_UNPACKED_BYTES"] = "5"
    oversized = subprocess.run(
        _stable_command(dry_run=True),
        check=False,
        capture_output=True,
        text=True,
        env=size_environment,
    )
    assert oversized.returncode != 0
    assert "maximum unpacked size" in oversized.stderr


def test_main_non_interactive_resolves_exact_sha_and_rejects_mismatch(tmp_path: Path) -> None:
    repository = _release_repository(tmp_path)
    revision = subprocess.run(
        ["git", "-C", repository, "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    environment = _bootstrap_environment(tmp_path)
    environment["LCC_BOOTSTRAP_REPOSITORY_URL"] = str(repository)
    environment["LCC_BOOTSTRAP_TEST_MISSING_PACKAGES"] = "git"
    environment["LCC_BOOTSTRAP_APT_LOG"] = str(tmp_path / "main-apt.log")
    command = [
        BOOTSTRAP,
        "--commit",
        revision,
        "--domain",
        "lcc.example.test",
        "--timezone",
        "UTC",
        "--non-interactive",
    ]
    installed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    handoff = (tmp_path / "handoff.log").read_text().splitlines()
    assert "latest validated code, resolved to an exact commit" in installed.stdout
    assert f"Resolved main revision: {revision}" in installed.stdout
    channel_index = handoff.index("--channel")
    assert handoff[channel_index : channel_index + 2] == ["--channel", "main"]
    assert f"main-{revision}" in handoff
    assert revision in handoff
    assert "refs/heads/main" in handoff
    assert (tmp_path / "main-apt.log").read_text().splitlines() == ["update", "install git"]

    mismatch_command = command.copy()
    mismatch_command[mismatch_command.index("--commit") + 1] = "0" * 40
    mismatch = subprocess.run(
        mismatch_command,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert mismatch.returncode != 0
    assert "not expected commit" in mismatch.stderr


def test_main_first_bootstrap_rerun_noops_updates_and_requires_release_migration_opt_in(
    tmp_path: Path,
) -> None:
    repository = _release_repository(tmp_path)
    revision = subprocess.run(
        ["git", "-C", repository, "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    same_root = tmp_path / "same-root"
    _write_active_release(
        same_root,
        release_id=f"main-{revision}",
        channel="main",
        revision=revision,
    )
    same_case = tmp_path / "same-case"
    same_case.mkdir()
    same_environment = _bootstrap_environment(same_case)
    same_environment.update(
        {
            "LCC_BOOTSTRAP_REPOSITORY_URL": str(repository),
            "LCC_BOOTSTRAP_INSTALL_ROOT": str(same_root),
        }
    )
    command = [BOOTSTRAP, "--commit", revision, "--non-interactive"]
    same = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=same_environment,
    )
    assert same.stdout.strip().endswith("Learning Control Center is already up to date.")
    assert not (same_case / "handoff.log").exists()

    changed_root = tmp_path / "changed-root"
    previous_revision = "1" * 40
    _write_active_release(
        changed_root,
        release_id=f"main-{previous_revision}",
        channel="main",
        revision=previous_revision,
    )
    changed_case = tmp_path / "changed-case"
    changed_case.mkdir()
    changed_environment = _bootstrap_environment(changed_case)
    update_handoff = changed_case / "update-handoff.log"
    changed_environment.update(
        {
            "LCC_BOOTSTRAP_REPOSITORY_URL": str(repository),
            "LCC_BOOTSTRAP_INSTALL_ROOT": str(changed_root),
            "LCC_BOOTSTRAP_UPDATE_HANDOFF_LOG": str(update_handoff),
        }
    )
    changed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=changed_environment,
    )
    changed_arguments = update_handoff.read_text().splitlines()
    assert "canonical update engine" in changed.stdout
    assert "apply" in changed_arguments
    assert f"main-{revision}" in changed_arguments
    assert revision in changed_arguments
    assert "--confirm-channel-change" not in changed_arguments

    release_root = tmp_path / "release-root"
    _write_active_release(
        release_root,
        release_id="v1.0.0",
        channel="stable",
        revision="2" * 40,
        legacy_v1=True,
    )
    release_case = tmp_path / "release-case"
    release_case.mkdir()
    release_environment = _bootstrap_environment(release_case)
    release_handoff = release_case / "update-handoff.log"
    release_environment.update(
        {
            "LCC_BOOTSTRAP_REPOSITORY_URL": str(repository),
            "LCC_BOOTSTRAP_INSTALL_ROOT": str(release_root),
            "LCC_BOOTSTRAP_UPDATE_HANDOFF_LOG": str(release_handoff),
        }
    )
    refused = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=release_environment,
    )
    assert refused.returncode != 0
    assert "requires --confirm-channel-change" in refused.stderr

    migrated = subprocess.run(
        [*command, "--confirm-channel-change"],
        check=True,
        capture_output=True,
        text=True,
        env=release_environment,
    )
    assert "channel=stable release=v1.0.0" in migrated.stdout
    assert f"Resolved main revision: {revision}" in migrated.stdout
    assert release_handoff.read_text().splitlines()[-1] == "--confirm-channel-change"


def test_main_default_and_pinned_release_have_unambiguous_cli_contract() -> None:
    cases = (
        (["--non-interactive"], "requires --commit"),
        (["--ref", RELEASE_ID], "only with --channel stable"),
        (
            ["--channel", "stable", "--ref", RELEASE_ID, "--commit", "0" * 40],
            "only with --channel main",
        ),
        (
            ["--channel", "main", "--asset-base-url", "https://example.test"],
            "only with --channel stable",
        ),
        (
            [
                "--channel",
                "stable",
                "--ref",
                RELEASE_ID,
                "--repository-url",
                "https://example.test/repo.git",
            ],
            "only with --channel main",
        ),
    )
    for arguments, message in cases:
        result = subprocess.run(
            [BOOTSTRAP, *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert message in result.stderr


@pytest.mark.skipif(shutil.which("script") is None, reason="PTY helper is unavailable")
def test_interactive_main_uses_normal_explicit_confirmation(tmp_path: Path) -> None:
    repository = _release_repository(tmp_path)
    environment = _bootstrap_environment(tmp_path)
    environment["LCC_BOOTSTRAP_REPOSITORY_URL"] = str(repository)
    command = [BOOTSTRAP]
    rejected = subprocess.run(
        ["script", "-qec", shlex.join(str(item) for item in command), "/dev/null"],
        input="NO\n",
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert rejected.returncode != 0
    assert "Continue? [y/N]" in rejected.stdout
    assert "Main installation was not confirmed" in rejected.stdout
    assert not (tmp_path / "handoff.log").exists()
    assert list((tmp_path / "secret-temp").iterdir()) == []
    bootstrap_source = (REPOSITORY_ROOT / "scripts" / "bootstrap-ubuntu.sh").read_text()
    assert bootstrap_source.index("Current main — latest validated code") < bootstrap_source.index(
        'checkout --quiet --detach "$source_revision"'
    )

    accepted = subprocess.run(
        ["script", "-qec", shlex.join(str(item) for item in command), "/dev/null"],
        input="y\nlcc.example.test\nUTC\n",
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert "Learning Control Center installation summary" in accepted.stdout
    assert (tmp_path / "handoff.log").is_file()

    changed_root = tmp_path / "changed-root"
    previous_revision = "9" * 40
    _write_active_release(
        changed_root,
        release_id=f"main-{previous_revision}",
        channel="main",
        revision=previous_revision,
    )
    changed_case = tmp_path / "changed"
    changed_case.mkdir()
    changed_environment = _bootstrap_environment(changed_case)
    changed_environment.update(
        {
            "LCC_BOOTSTRAP_REPOSITORY_URL": str(repository),
            "LCC_BOOTSTRAP_INSTALL_ROOT": str(changed_root),
            "LCC_BOOTSTRAP_UPDATE_HANDOFF_LOG": str(tmp_path / "changed-handoff.log"),
        }
    )
    changed = subprocess.run(
        ["script", "-qec", shlex.join(str(item) for item in command), "/dev/null"],
        input="y\n",
        check=True,
        capture_output=True,
        text=True,
        env=changed_environment,
    )
    current_revision = subprocess.run(
        ["git", "-C", repository, "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert f"Current source SHA: {previous_revision}" in changed.stdout
    assert f"Target source SHA: {current_revision}" in changed.stdout
    assert "Delegating immutable target" in changed.stdout


def test_external_environment_file_must_be_regular_private_and_not_a_symlink(
    tmp_path: Path,
) -> None:
    regular = tmp_path / "operator.env"
    regular.write_text("safe\n")
    regular.chmod(0o644)
    link = tmp_path / "operator-link.env"
    link.symlink_to(regular)
    common = REPOSITORY_ROOT / "scripts" / "deploy-common.sh"
    command = [
        "bash",
        "-c",
        'source "$1"; lcc_validate_environment_file_security "$2" 0 "$(id -u)"',
        "env-security-test",
        str(common),
    ]
    exposed = subprocess.run([*command, str(regular)], check=False, capture_output=True, text=True)
    assert exposed.returncode != 0
    assert "must not grant group or other permissions" in exposed.stderr
    regular.chmod(0o600)
    symlinked = subprocess.run([*command, str(link)], check=False, capture_output=True, text=True)
    assert symlinked.returncode != 0
    assert "must not be a symbolic link" in symlinked.stderr

    unsafe_parent = tmp_path / "replaceable"
    unsafe_parent.mkdir(mode=0o777)
    unsafe_parent.chmod(0o777)
    nested = unsafe_parent / "operator.env"
    nested.write_text("safe\n")
    nested.chmod(0o600)
    replaceable = subprocess.run(
        [*command, str(nested)], check=False, capture_output=True, text=True
    )
    assert replaceable.returncode != 0
    assert "parent directories" in replaceable.stderr


def test_production_source_urls_require_explicit_credential_free_https() -> None:
    common = REPOSITORY_ROOT / "scripts" / "deploy-common.sh"

    def validate(url: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; lcc_validate_https_url "$2" "Source"',
                "source-url-test",
                str(common),
                url,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    github = validate("https://github.com/Learning-Control-Center/Learning-Control-Center.git")
    forgejo = validate(
        "https://forgejo.waqsea.com/Learning-Control-Center/Learning-Control-Center.git"
    )
    assert github.returncode == 0
    assert forgejo.returncode == 0
    for rejected in (
        "http://github.com/Learning-Control-Center/Learning-Control-Center.git",
        "https://user:token@github.com/Learning-Control-Center/Learning-Control-Center.git",
        "https://github.com/Learning-Control-Center/Learning-Control-Center.git?ref=main",
        "https://github.com/Learning-Control-Center/Learning-Control-Center.git\nunsafe",
    ):
        result = validate(rejected)
        assert result.returncode != 0
        assert "public HTTPS URL without credentials" in result.stderr


def test_public_repository_assets_and_metadata_are_consistent() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "logo.png", "frontend/public/logo.png"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert set(tracked.stdout.splitlines()) == {"logo.png", "frontend/public/logo.png"}
    assert (REPOSITORY_ROOT / "logo.png").read_bytes() == (
        REPOSITORY_ROOT / "frontend" / "public" / "logo.png"
    ).read_bytes()
    assert 'href="/logo.png"' in (REPOSITORY_ROOT / "frontend" / "index.html").read_text()
    for script in (BOOTSTRAP, PACKAGER, PROMOTER, GENERATOR):
        assert script.stat().st_mode & stat.S_IXUSR

    project = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text())
    assert project["project"]["version"] == "1.0.1"
    assert project["project"]["license"] == "GPL-3.0-only"
    assert "contact@waqsea.com" in (REPOSITORY_ROOT / "SECURITY.md").read_text()
    frontend_package = json.loads((REPOSITORY_ROOT / "frontend" / "package.json").read_text())
    frontend_lock = json.loads((REPOSITORY_ROOT / "frontend" / "package-lock.json").read_text())
    assert frontend_package["version"] == "1.0.1"
    assert frontend_lock["version"] == "1.0.1"
    assert frontend_lock["packages"][""]["version"] == "1.0.1"
    assert 'version="1.0.1"' in (REPOSITORY_ROOT / "backend" / "app" / "main.py").read_text()
    assert (
        '"appVersion": "1.0.1"'
        in (REPOSITORY_ROOT / "backend" / "app" / "import_export.py").read_text()
    )

    public_frontend_text = "\n".join(
        (REPOSITORY_ROOT / path).read_text()
        for path in (
            "frontend/index.html",
            "frontend/src/index.css",
            "frontend/tailwind.config.js",
            "deploy/Caddyfile.template",
        )
    )
    assert "fonts.googleapis.com" not in public_frontend_text
    assert "fonts.gstatic.com" not in public_frontend_text
    caddy_template = (REPOSITORY_ROOT / "deploy" / "Caddyfile.template").read_text()
    assert "font-src 'self'" in caddy_template

    changelog = (REPOSITORY_ROOT / "CHANGELOG.md").read_text()
    assert "## [1.0.1] - Unreleased" in changelog
    assert "## [1.0.0] - 2026-09-21" in changelog

    required_public_files = [
        "README.md",
        "LICENSE",
        "SECURITY.md",
        "CHANGELOG.md",
        "docs/INSTALLATION.md",
        "docs/PRODUCTION_OPERATIONS.md",
        "docs/UPDATES.md",
        "docs/PRODUCT_QA.md",
        "docs/RELEASING.md",
        "scripts/bootstrap-ubuntu.sh",
        "scripts/generate-production-env.sh",
        "scripts/package-release.sh",
        "scripts/prepare-public-promotion.sh",
    ]
    assert all((REPOSITORY_ROOT / path).is_file() for path in required_public_files)

    gitleaks = tomllib.loads((REPOSITORY_ROOT / ".gitleaks.toml").read_text())
    assert all(item.get("condition") == "AND" for item in gitleaks["allowlists"])
    assert all(
        "tests/.*" not in pattern for item in gitleaks["allowlists"] for pattern in item["paths"]
    )


def test_tracked_markdown_relative_links_resolve() -> None:
    markdown_files = subprocess.run(
        ["git", "ls-files", "*.md"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    missing: list[str] = []
    link_pattern = re.compile(r"\[[^]]*]\(([^)]+)\)")
    for relative_path in markdown_files:
        document = REPOSITORY_ROOT / relative_path
        for raw_target in link_pattern.findall(document.read_text()):
            target = raw_target.strip().strip("<>").split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            resolved = (document.parent / unquote(target)).resolve()
            if not resolved.exists():
                missing.append(f"{relative_path}: {raw_target}")
    assert missing == []
