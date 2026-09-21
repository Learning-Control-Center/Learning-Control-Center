from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
UPDATER = REPOSITORY_ROOT / "scripts" / "update.sh"
GITHUB_REPOSITORY = "https://github.com/Learning-Control-Center/Learning-Control-Center.git"
GITHUB_ORIGIN = (
    "https://github.com/Learning-Control-Center/Learning-Control-Center/releases/download"
)
FORGEJO_REPOSITORY = (
    "https://forgejo.waqsea.com/Learning-Control-Center/Learning-Control-Center.git"
)
FORGEJO_ORIGIN = (
    "https://forgejo.waqsea.com/Learning-Control-Center/Learning-Control-Center/releases/download"
)


def _installed_root(
    tmp_path: Path,
    *,
    channel: str,
    release_id: str,
    revision: str,
    repository: str = GITHUB_REPOSITORY,
    origin: str | None = None,
) -> tuple[Path, Path]:
    root = tmp_path / "installed-root"
    release = root / "opt" / "learning-control-center" / "releases" / release_id
    release.mkdir(parents=True)
    if origin is None:
        origin = GITHUB_ORIGIN if channel == "stable" else repository
    source_ref = f"refs/tags/{release_id}" if channel == "stable" else "refs/heads/main"
    (release / "RELEASE_ID").write_text(f"{release_id}\n")
    (release / "RELEASE_CHANNEL").write_text(f"{channel}\n")
    (release / "SOURCE_REVISION").write_text(f"{revision}\n")
    (release / "RELEASE_MANIFEST").write_text(
        "\n".join(
            (
                "metadata_version=1",
                f"channel={channel}",
                f"release_id={release_id}",
                f"source_repository={repository}",
                f"source_ref={source_ref}",
                f"source_revision={revision}",
                f"source_origin={origin}",
                "",
            )
        )
    )
    scripts = release / "scripts"
    scripts.mkdir()
    bootstrap = scripts / "bootstrap-ubuntu.sh"
    bootstrap.write_text("#!/usr/bin/env bash\nexit 99\n")
    bootstrap.chmod(0o755)
    current = root / "opt" / "learning-control-center" / "current"
    current.symlink_to(release)
    return root, release


def _environment(tmp_path: Path, root: Path, target_revision: str) -> tuple[dict[str, str], Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    handoff = tmp_path / "handoff.log"
    return (
        {
            **os.environ,
            "LCC_UPDATE_TESTING": "1",
            "LCC_UPDATE_INSTALL_ROOT": str(root),
            "LCC_UPDATE_TEST_MAIN_SHA": target_revision,
            "LCC_UPDATE_TEST_HANDOFF_LOG": str(handoff),
        },
        handoff,
    )


@pytest.mark.parametrize(
    ("repository", "origin"),
    ((GITHUB_REPOSITORY, GITHUB_ORIGIN), (FORGEJO_REPOSITORY, FORGEJO_ORIGIN)),
)
def test_release_installation_updates_to_exact_main_and_preserves_repository(
    tmp_path: Path, repository: str, origin: str
) -> None:
    old_revision = "1" * 40
    new_revision = "2" * 40
    root, _release = _installed_root(
        tmp_path,
        channel="stable",
        release_id="v1.0.1",
        revision=old_revision,
        repository=repository,
        origin=origin,
    )
    environment, handoff = _environment(tmp_path, root, new_revision)
    result = subprocess.run(
        [UPDATER, "--yes"], check=True, capture_output=True, text=True, env=environment
    )
    assert "Current channel: stable" in result.stdout
    assert "Current release: v1.0.1" in result.stdout
    assert f"Current source SHA: {old_revision}" in result.stdout
    assert f"Target source SHA: {new_revision}" in result.stdout
    assert "Channel change: stable -> main" in result.stdout
    assert handoff.read_text().splitlines()[1:] == [
        "--channel",
        "main",
        "--commit",
        new_revision,
        "--repository-url",
        repository,
        "--non-interactive",
        "--confirm-channel-change",
    ]


def test_main_update_is_exact_sha_pinned_and_same_sha_is_noop(tmp_path: Path) -> None:
    old_revision = "3" * 40
    new_revision = "4" * 40
    root, _release = _installed_root(
        tmp_path,
        channel="main",
        release_id=f"main-{old_revision}",
        revision=old_revision,
        origin=GITHUB_REPOSITORY,
    )
    environment, handoff = _environment(tmp_path, root, new_revision)
    result = subprocess.run(
        [UPDATER, "--yes"], check=True, capture_output=True, text=True, env=environment
    )
    assert f"Current source SHA: {old_revision}" in result.stdout
    assert f"Target source SHA: {new_revision}" in result.stdout
    assert handoff.read_text().splitlines()[1:] == [
        "--channel",
        "main",
        "--commit",
        new_revision,
        "--repository-url",
        GITHUB_REPOSITORY,
        "--non-interactive",
    ]

    handoff.unlink()
    environment["LCC_UPDATE_TEST_MAIN_SHA"] = old_revision
    same = subprocess.run(
        [UPDATER, "--yes"], check=True, capture_output=True, text=True, env=environment
    )
    assert same.stdout.strip() == "Learning Control Center is already up to date."
    assert not handoff.exists()


def test_dry_run_reaches_verified_main_bootstrap_without_applying(tmp_path: Path) -> None:
    root, _release = _installed_root(
        tmp_path,
        channel="stable",
        release_id="v1.0.1",
        revision="5" * 40,
    )
    target_revision = "6" * 40
    environment, handoff = _environment(tmp_path, root, target_revision)
    result = subprocess.run(
        [UPDATER, "--dry-run"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert f"DRY-RUN: verifying current main at {target_revision}" in result.stdout
    assert handoff.read_text().splitlines()[-2:] == ["--confirm-channel-change", "--dry-run"]


def test_normal_updater_has_no_release_discovery_or_channel_override(tmp_path: Path) -> None:
    source = UPDATER.read_text()
    assert "api.github.com/repos" not in source
    assert "/api/v1/repos" not in source
    assert "releases/latest" not in source
    assert "prerelease" not in source
    assert "refs/heads/main" in source
    assert "git ls-remote" in source

    root, _release = _installed_root(
        tmp_path,
        channel="main",
        release_id=f"main-{'7' * 40}",
        revision="7" * 40,
        origin=GITHUB_REPOSITORY,
    )
    environment, _handoff = _environment(tmp_path, root, "8" * 40)
    rejected = subprocess.run(
        [UPDATER, "--channel", "stable"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert rejected.returncode != 0
    assert "Unknown update option" in rejected.stderr


def test_persistent_wrapper_and_admin_are_thin_delegates() -> None:
    wrapper = (REPOSITORY_ROOT / "deploy" / "learning-control-center-update.sh").read_text()
    admin = (REPOSITORY_ROOT / "scripts" / "lcc-admin").read_text()
    engine = (REPOSITORY_ROOT / "scripts" / "update-ubuntu.sh").read_text()
    assert 'exec "$current_updater" "$@"' in wrapper
    assert 'exec "$LCC_UPDATE_ENTRYPOINT" "$@"' in admin
    assert "create_offline_backup" in engine
    assert "lcc_migration_relation" in engine
    assert "create_offline_backup" not in wrapper
    assert "lcc_migration_relation" not in wrapper


def test_persistent_wrapper_follows_atomic_current_release_switch(tmp_path: Path) -> None:
    application_root = tmp_path / "opt" / "learning-control-center"
    releases = application_root / "releases"
    releases.mkdir(parents=True)
    for release_name in ("main-old", "main-new"):
        scripts = releases / release_name / "scripts"
        scripts.mkdir(parents=True)
        updater = scripts / "update.sh"
        updater.write_text(f"#!/usr/bin/env bash\nprintf '%s\\n' '{release_name}'\n")
        updater.chmod(0o755)

    wrapper = tmp_path / "update.sh"
    wrapper.write_text(
        (REPOSITORY_ROOT / "deploy" / "learning-control-center-update.sh")
        .read_text()
        .replace("/opt/learning-control-center", str(application_root))
    )
    wrapper.chmod(0o755)
    current = application_root / "current"
    current.symlink_to(releases / "main-old")
    first = subprocess.run([wrapper], check=True, capture_output=True, text=True)
    assert first.stdout.strip() == "main-old"

    replacement = application_root / ".current.next"
    replacement.symlink_to(releases / "main-new")
    replacement.replace(current)
    second = subprocess.run([wrapper], check=True, capture_output=True, text=True)
    assert second.stdout.strip() == "main-new"


def test_public_update_commands_and_main_terminology_stay_synchronized() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text()
    installation = (REPOSITORY_ROOT / "docs" / "INSTALLATION.md").read_text()
    updates = (REPOSITORY_ROOT / "docs" / "UPDATES.md").read_text()
    operations = (REPOSITORY_ROOT / "docs" / "PRODUCTION_OPERATIONS.md").read_text()
    release_docs = (REPOSITORY_ROOT / "docs" / "RELEASING.md").read_text()
    documentation = "\n".join((readme, installation, updates, operations, release_docs))
    main_command = (
        "curl -fsSL https://raw.githubusercontent.com/Learning-Control-Center/"
        "Learning-Control-Center/main/scripts/bootstrap-ubuntu.sh | sudo bash"
    )
    assert main_command in readme
    assert main_command in installation
    assert main_command in updates
    assert "releases/latest/download/install.sh" not in documentation
    assert "sudo /opt/learning-control-center/update.sh" in readme
    assert "sudo lcc-admin update" in readme
    assert "DEVELOPMENT / UNSTABLE" not in documentation
    assert "unstable-main" not in documentation
    assert "immutable version snapshots" in readme


@pytest.mark.parametrize(
    ("channel", "repository", "origin"),
    (
        ("stable", GITHUB_REPOSITORY, GITHUB_ORIGIN),
        ("stable", FORGEJO_REPOSITORY, FORGEJO_ORIGIN),
        ("main", GITHUB_REPOSITORY, GITHUB_REPOSITORY),
        ("main", FORGEJO_REPOSITORY, FORGEJO_REPOSITORY),
    ),
)
def test_supported_source_metadata_pairs_are_explicit(
    channel: str, repository: str, origin: str
) -> None:
    common = REPOSITORY_ROOT / "scripts" / "deploy-common.sh"
    valid = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; lcc_validate_source_metadata "$2" "$3" "$4"',
            "source-pair-test",
            common,
            channel,
            repository,
            origin,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert valid.returncode == 0


def test_mismatched_or_unapproved_source_metadata_is_rejected() -> None:
    common = REPOSITORY_ROOT / "scripts" / "deploy-common.sh"
    invalid = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; lcc_validate_source_metadata stable "$2" "$3"',
            "source-pair-test",
            common,
            GITHUB_REPOSITORY,
            FORGEJO_ORIGIN,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert invalid.returncode != 0
    assert "supported GitHub or Forgejo" in invalid.stderr
