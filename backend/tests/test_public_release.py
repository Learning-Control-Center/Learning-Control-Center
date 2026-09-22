from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tomllib
from pathlib import Path
from urllib.parse import unquote

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = REPOSITORY_ROOT / "scripts" / "bootstrap.sh"
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


def _release_repository(tmp_path: Path, *, stub_installer: bool = False) -> Path:
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
        "backend/app/frontend.py",
        "backend/app/analysis/v1_compat.py",
        "frontend/package.json",
        "frontend/package-lock.json",
        "scripts/frontend-artifact.py",
        "scripts/bootstrap.sh",
        "scripts/release-bootstrap.sh",
        "scripts/bootstrap-ubuntu.sh",
        "scripts/deploy-common.sh",
        "scripts/install.sh",
        "scripts/install/gateway.sh",
        "scripts/install/transition.sh",
        "scripts/install/platforms/ubuntu-24.04.sh",
        "scripts/generate-production-env.sh",
        "scripts/install-ubuntu.sh",
        "scripts/lcc-admin",
        "scripts/package-release.sh",
        "scripts/prepare-public-promotion.sh",
        "scripts/update.sh",
        "scripts/update-ubuntu.sh",
        "scripts/uninstall.sh",
        "deploy/Caddyfile",
        "deploy/Caddyfile.template",
        "deploy/examples/installer-v2-external-nginx.conf",
        "deploy/learning-control-center.env.example",
        "deploy/learning-control-center.service",
        "deploy/learning-control-center-update.sh",
        "docs/INSTALLATION.md",
        "docs/PRODUCTION_OPERATIONS.md",
        "docs/RELEASING.md",
        "docs/UPDATES.md",
    ):
        _copy_current(repository, relative_path)
    shutil.rmtree(repository / "frontend" / "src")
    shutil.copytree(REPOSITORY_ROOT / "frontend" / "src", repository / "frontend" / "src")
    shutil.rmtree(repository / "frontend" / "dist")
    shutil.copytree(REPOSITORY_ROOT / "frontend" / "dist", repository / "frontend" / "dist")

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

    if stub_installer:
        _write(
            repository / "scripts/install.sh",
            "#!/usr/bin/env bash\n"
            'printf "%s\\n" "$LCC_V2_SOURCE_ROOT" "$LCC_V2_SOURCE_REPOSITORY" '
            '"$LCC_V2_SOURCE_REF" "$LCC_V2_SOURCE_SHA" "$LCC_V2_RELEASE_ID" "$@" '
            '> "$LCC_TEST_RELEASE_HANDOFF"\n',
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
    assert f"readonly release_id='{RELEASE_ID}'" in launcher
    assert f"readonly archive_sha256='{expected_hash}'" in launcher
    assert "readonly source_sha='" in launcher
    assert "lcc-release" in launcher
    assert "embedded_stable_ref" not in launcher

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
        f"{prefix}backend/app/frontend.py",
        f"{prefix}scripts/bootstrap.sh",
        f"{prefix}scripts/release-bootstrap.sh",
        f"{prefix}scripts/bootstrap-ubuntu.sh",
        f"{prefix}scripts/install.sh",
        f"{prefix}scripts/install/gateway.sh",
        f"{prefix}scripts/install/transition.sh",
        f"{prefix}scripts/install/platforms/ubuntu-24.04.sh",
        f"{prefix}deploy/examples/installer-v2-external-nginx.conf",
        f"{prefix}scripts/frontend-artifact.py",
        f"{prefix}scripts/generate-production-env.sh",
        f"{prefix}scripts/install-ubuntu.sh",
        f"{prefix}scripts/prepare-public-promotion.sh",
        f"{prefix}scripts/update.sh",
        f"{prefix}scripts/update-ubuntu.sh",
        f"{prefix}scripts/uninstall.sh",
        f"{prefix}deploy/learning-control-center-update.sh",
        f"{prefix}RELEASE_ID",
        f"{prefix}RELEASE_CHANNEL",
        f"{prefix}SOURCE_REVISION",
        f"{prefix}RELEASE_MANIFEST",
    }
    assert expected <= members.keys()
    assert members[f"{prefix}scripts/bootstrap.sh"].mode & stat.S_IXUSR
    assert members[f"{prefix}scripts/release-bootstrap.sh"].mode & stat.S_IXUSR
    assert members[f"{prefix}scripts/bootstrap-ubuntu.sh"].mode & stat.S_IXUSR
    assert members[f"{prefix}scripts/install.sh"].mode & stat.S_IXUSR
    assert members[f"{prefix}scripts/install/gateway.sh"].mode & stat.S_IXUSR
    assert members[f"{prefix}scripts/install/transition.sh"].mode & stat.S_IRUSR
    assert members[f"{prefix}scripts/generate-production-env.sh"].mode & stat.S_IXUSR
    assert members[f"{prefix}scripts/install-ubuntu.sh"].mode & stat.S_IXUSR
    assert members[f"{prefix}scripts/update.sh"].mode & stat.S_IXUSR
    assert members[f"{prefix}scripts/update-ubuntu.sh"].mode & stat.S_IXUSR
    assert members[f"{prefix}scripts/uninstall.sh"].mode & stat.S_IXUSR
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


def test_release_bound_launcher_verifies_archive_and_passes_immutable_identity(
    tmp_path: Path,
) -> None:
    repository = _release_repository(tmp_path, stub_installer=True)
    archive, _, launcher = _package(repository, tmp_path / "assets")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write(
        fake_bin / "curl",
        "#!/usr/bin/env bash\n"
        'test "${*: -1}" = '
        '"https://github.com/Learning-Control-Center/Learning-Control-Center/releases/'
        'download/v1.0.1/learning-control-center-v1.0.1.tar.gz" || exit 3\n'
        'while test "$#" -gt 0; do\n'
        '  if test "$1" = --output; then cp "$LCC_TEST_ARCHIVE" "$2"; exit; fi\n'
        "  shift\n"
        "done\nexit 4\n",
        0o755,
    )
    handoff = tmp_path / "handoff"
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "LCC_TEST_ARCHIVE": str(archive),
        "LCC_TEST_RELEASE_HANDOFF": str(handoff),
    }
    installed = subprocess.run(
        [launcher, "--non-interactive", "--domain", "lcc.example.test"],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert installed.returncode == 0, installed.stderr
    identity = handoff.read_text().splitlines()
    assert identity[0].endswith(f"Learning-Control-Center-{RELEASE_ID}")
    assert identity[1:3] == [
        "https://github.com/Learning-Control-Center/Learning-Control-Center.git",
        f"refs/tags/{RELEASE_ID}",
    ]
    assert re.fullmatch("[0-9a-f]{40}", identity[3])
    assert identity[4:] == [RELEASE_ID, "--non-interactive", "--domain", "lcc.example.test"]

    archive.write_bytes(archive.read_bytes()[:-1])
    handoff.unlink()
    rejected = subprocess.run([launcher], env=environment, capture_output=True, text=True)
    assert rejected.returncode != 0
    assert "SHA-256 does not match" in rejected.stderr
    assert not handoff.exists()

    override = subprocess.run(
        [launcher, "--repository-url", "https://elsewhere.invalid"],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert override.returncode != 0
    assert "does not accept source or channel overrides" in override.stderr


@pytest.mark.parametrize(
    "member_name,member_type",
    [
        ("../outside", tarfile.REGTYPE),
        ("/absolute", tarfile.REGTYPE),
        (f"Learning-Control-Center-{RELEASE_ID}/README.md", tarfile.REGTYPE),
        (f"Learning-Control-Center-{RELEASE_ID}/escape", tarfile.SYMTYPE),
        (f"Learning-Control-Center-{RELEASE_ID}/device", tarfile.CHRTYPE),
    ],
)
def test_release_bound_launcher_rejects_unsafe_members_even_with_matching_digest(
    tmp_path: Path, member_name: str, member_type: bytes
) -> None:
    root = f"Learning-Control-Center-{RELEASE_ID}"
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for name, data, mode, kind in (
            (f"{root}/README.md", b"LCC", 0o644, tarfile.REGTYPE),
            (f"{root}/pyproject.toml", b"[project]", 0o644, tarfile.REGTYPE),
            (
                f"{root}/scripts/install.sh",
                b"#!/usr/bin/env bash\nexit 0\n",
                0o755,
                tarfile.REGTYPE,
            ),
            (f"{root}/scripts/install/transition.sh", b"# fixture", 0o644, tarfile.REGTYPE),
            (f"{root}/SOURCE_REVISION", ("a" * 40).encode(), 0o644, tarfile.REGTYPE),
            (f"{root}/RELEASE_MANIFEST", b"fixture", 0o644, tarfile.REGTYPE),
            (member_name, b"unsafe", 0o644, member_type),
        ):
            member = tarfile.TarInfo(name)
            member.mode = mode
            member.type = kind
            member.size = len(data) if kind == tarfile.REGTYPE else 0
            bundle.addfile(member, io.BytesIO(data) if member.isfile() else None)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    template = (REPOSITORY_ROOT / "scripts/release-bootstrap.sh").read_text()
    launcher = tmp_path / "install.sh"
    launcher.write_text(
        template.replace("@LCC_RELEASE_ID@", RELEASE_ID)
        .replace("@LCC_SOURCE_SHA@", "a" * 40)
        .replace("@LCC_ARCHIVE_SHA256@", digest)
    )
    launcher.chmod(0o755)
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    _write(
        bin_directory / "curl",
        "#!/usr/bin/env bash\n"
        'while test "$#" -gt 0; do\n'
        '  if test "$1" = --output; then cp "$LCC_TEST_ARCHIVE" "$2"; exit; fi\n'
        "  shift\n"
        "done\nexit 2\n",
        0o755,
    )
    result = subprocess.run(
        [launcher],
        env={
            **os.environ,
            "PATH": f"{bin_directory}:{os.environ['PATH']}",
            "LCC_TEST_ARCHIVE": str(archive),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Invalid release archive" in result.stderr


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
    frontend_policy = (REPOSITORY_ROOT / "backend/app/frontend.py").read_text()
    assert "font-src 'self'" in frontend_policy
    assert "reverse_proxy 127.0.0.1:@@LCC_APP_PORT@@" in caddy_template
    assert "file_server" not in caddy_template

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
        "scripts/bootstrap.sh",
        "scripts/release-bootstrap.sh",
        "scripts/install.sh",
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
