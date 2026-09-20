from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tomllib
from pathlib import Path
from urllib.parse import unquote

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = REPOSITORY_ROOT / "scripts" / "bootstrap-ubuntu.sh"
PACKAGER = REPOSITORY_ROOT / "scripts" / "package-release.sh"
RELEASE_ID = "v1.0.0"
ARCHIVE_NAME = f"learning-control-center-{RELEASE_ID}.tar.gz"
CHECKSUM_NAME = f"{ARCHIVE_NAME}.sha256"


def _write(path: Path, content: str | bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    path.chmod(mode)


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

    shutil.copy2(BOOTSTRAP, repository / "scripts" / "bootstrap-ubuntu.sh")
    shutil.copy2(PACKAGER, repository / "scripts" / "package-release.sh")
    shutil.copy2(REPOSITORY_ROOT / "CHANGELOG.md", repository / "CHANGELOG.md")
    shutil.copy2(REPOSITORY_ROOT / "docs" / "RELEASING.md", repository / "docs" / "RELEASING.md")
    shutil.copy2(
        REPOSITORY_ROOT / "docs" / "INSTALLATION.md",
        repository / "docs" / "INSTALLATION.md",
    )
    shutil.copy2(REPOSITORY_ROOT / "docs" / "UPDATES.md", repository / "docs" / "UPDATES.md")

    subprocess.run(["git", "init", "--quiet", repository], check=True)
    subprocess.run(["git", "-C", repository, "config", "user.name", "WaqSea"], check=True)
    subprocess.run(
        ["git", "-C", repository, "config", "user.email", "contact@waqsea.com"],
        check=True,
    )

    files: dict[str, str | bytes] = {
        "memory-bank/private.md": "private agent context\n",
        "data/lcc.db": b"private database",
        "backups/lcc.sqlite3": b"private backup",
        ".env": "PRIVATE=secret\n",
        "deploy/learning-control-center.env": "PRIVATE=secret\n",
        ".abacusai/cache.txt": "private tool state\n",
        "backend/tests/fixtures/private.sqlite3": b"private fixture",
    }
    for relative_path, content in files.items():
        _write(repository / relative_path, content)

    _write(
        repository / "scripts" / "install-ubuntu.sh",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$@" > "${LCC_BOOTSTRAP_HANDOFF_LOG:?}"
printf 'fake installer invoked\\n'
""",
        0o755,
    )
    subprocess.run(["git", "-C", repository, "add", "."], check=True)
    subprocess.run(
        ["git", "-C", repository, "commit", "--quiet", "-m", "release fixture"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", repository, "tag", "-a", RELEASE_ID, "-m", RELEASE_ID],
        check=True,
    )
    return repository


def _package(repository: Path, output_directory: Path) -> tuple[Path, Path]:
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
    assert "Release identity: v1.0.0" in result.stdout
    return output_directory / ARCHIVE_NAME, output_directory / CHECKSUM_NAME


def _bootstrap_environment(tmp_path: Path, asset_root: Path) -> dict[str, str]:
    os_release = tmp_path / "os-release"
    os_release.write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
    return {
        **os.environ,
        "LCC_BOOTSTRAP_TESTING": "1",
        "LCC_BOOTSTRAP_OS_RELEASE": str(os_release),
        "LCC_BOOTSTRAP_ASSET_BASE_URL": asset_root.as_uri(),
        "LCC_BOOTSTRAP_HANDOFF_LOG": str(tmp_path / "handoff.log"),
    }


def _bootstrap_command(environment_file: Path, *, dry_run: bool = False) -> list[str]:
    command = [
        str(BOOTSTRAP),
        "--ref",
        RELEASE_ID,
        "--domain",
        "lcc.example.test",
        "--env-file",
        str(environment_file),
    ]
    if dry_run:
        command.append("--dry-run")
    return command


def test_release_packaging_is_deterministic_bounded_and_mode_preserving(
    tmp_path: Path,
) -> None:
    repository = _release_repository(tmp_path)
    first_archive, first_checksum = _package(repository, tmp_path / "first")
    second_archive, second_checksum = _package(repository, tmp_path / "second")

    assert first_archive.read_bytes() == second_archive.read_bytes()
    assert first_checksum.read_text() == second_checksum.read_text()
    expected_hash, expected_name = first_checksum.read_text().split()
    assert expected_name == ARCHIVE_NAME
    assert hashlib.sha256(first_archive.read_bytes()).hexdigest() == expected_hash

    with tarfile.open(first_archive, "r:gz") as bundle:
        members = {member.name: member for member in bundle.getmembers()}
    prefix = f"Learning-Control-Center-{RELEASE_ID}/"
    expected = {
        f"{prefix}README.md",
        f"{prefix}LICENSE",
        f"{prefix}SECURITY.md",
        f"{prefix}logo.png",
        f"{prefix}frontend/public/logo.png",
        f"{prefix}scripts/bootstrap-ubuntu.sh",
        f"{prefix}scripts/install-ubuntu.sh",
        f"{prefix}RELEASE_ID",
        f"{prefix}SOURCE_REVISION",
        f"{prefix}RELEASE_MANIFEST",
    }
    assert expected <= members.keys()
    assert members[f"{prefix}scripts/bootstrap-ubuntu.sh"].mode & stat.S_IXUSR
    assert members[f"{prefix}scripts/install-ubuntu.sh"].mode & stat.S_IXUSR
    forbidden_fragments = (
        "/memory-bank/",
        "/.git/",
        "/data/",
        "/backups/",
        "/.abacusai/",
        "/backend/tests/",
    )
    assert not any(fragment in name for name in members for fragment in forbidden_fragments)
    assert not any(
        name.endswith(("/.env", "/learning-control-center.env", ".db", ".sqlite3"))
        for name in members
    )


def test_bootstrap_requires_explicit_immutable_ref(tmp_path: Path) -> None:
    environment_file = tmp_path / "production.env"
    environment_file.write_text("placeholder\n")
    missing = subprocess.run(
        [BOOTSTRAP, "--domain", "lcc.example.test", "--env-file", environment_file],
        check=False,
        capture_output=True,
        text=True,
    )
    assert missing.returncode != 0
    assert "--ref is required" in missing.stderr

    moving = subprocess.run(
        [
            BOOTSTRAP,
            "--ref",
            "main",
            "--domain",
            "lcc.example.test",
            "--env-file",
            environment_file,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert moving.returncode != 0
    assert "semantic release tag or full 40-character commit" in moving.stderr


def test_bootstrap_validates_release_cleans_temp_and_hands_off_without_secrets(
    tmp_path: Path,
) -> None:
    repository = _release_repository(tmp_path)
    asset_root = tmp_path / "assets"
    release_assets = asset_root / RELEASE_ID
    _package(repository, release_assets)
    environment_file = tmp_path / "production.env"
    secret = "never-print-this-production-secret"
    environment_file.write_text(f"LCC_SECURITY_SECRET={secret}\n")
    temporary_root = tmp_path / "bootstrap-temp"
    temporary_root.mkdir()
    environment = {
        **_bootstrap_environment(tmp_path, asset_root),
        "TMPDIR": str(temporary_root),
    }

    dry_run = subprocess.run(
        _bootstrap_command(environment_file, dry_run=True),
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert "Dry run complete" in dry_run.stdout
    assert secret not in dry_run.stdout + dry_run.stderr
    assert not (tmp_path / "handoff.log").exists()
    assert list(temporary_root.iterdir()) == []

    installed = subprocess.run(
        _bootstrap_command(environment_file),
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    handoff = (tmp_path / "handoff.log").read_text().splitlines()
    assert handoff[:7] == [
        "--domain",
        "lcc.example.test",
        "--release-id",
        RELEASE_ID,
        "--env-file",
        str(environment_file),
        "--source",
    ]
    assert len(handoff) == 8
    assert handoff[-1].endswith(f"Learning-Control-Center-{RELEASE_ID}")
    assert secret not in installed.stdout + installed.stderr + "\n".join(handoff)
    assert "Verified release: v1.0.0" in installed.stdout
    assert list(temporary_root.iterdir()) == []


def test_bootstrap_rejects_checksum_mismatch(tmp_path: Path) -> None:
    repository = _release_repository(tmp_path)
    asset_root = tmp_path / "assets"
    archive, _checksum = _package(repository, asset_root / RELEASE_ID)
    archive.write_bytes(archive.read_bytes() + b"tampered")
    environment_file = tmp_path / "production.env"
    environment_file.write_text("placeholder\n")
    result = subprocess.run(
        _bootstrap_command(environment_file, dry_run=True),
        check=False,
        capture_output=True,
        text=True,
        env=_bootstrap_environment(tmp_path, asset_root),
    )
    assert result.returncode != 0
    assert "SHA-256 verification failed" in result.stderr


def test_bootstrap_rejects_archive_traversal(tmp_path: Path) -> None:
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
    environment_file = tmp_path / "production.env"
    environment_file.write_text("placeholder\n")
    result = subprocess.run(
        _bootstrap_command(environment_file, dry_run=True),
        check=False,
        capture_output=True,
        text=True,
        env=_bootstrap_environment(tmp_path, asset_root),
    )
    assert result.returncode != 0
    assert "unsafe release path" in result.stderr
    assert not (tmp_path / "escape").exists()


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
    assert BOOTSTRAP.stat().st_mode & stat.S_IXUSR
    assert PACKAGER.stat().st_mode & stat.S_IXUSR

    agents = (REPOSITORY_ROOT / "AGENTS.md").read_text()
    assert "memory-bank" not in agents
    project = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text())
    assert project["project"]["license"] == "GPL-3.0-only"
    assert "contact@waqsea.com" in (REPOSITORY_ROOT / "SECURITY.md").read_text()

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
        "scripts/package-release.sh",
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
