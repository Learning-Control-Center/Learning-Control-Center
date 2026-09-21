from __future__ import annotations

import json
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


def _installer(path: Path, release_id: str) -> Path:
    path.write_text(
        "\n".join(
            (
                "#!/usr/bin/env bash",
                f'readonly embedded_stable_ref="{release_id}"',
                f'readonly embedded_archive_sha256="{"a" * 64}"',
                'readonly stable_only_launcher="1"',
                "exit 98",
                "",
            )
        )
    )
    path.chmod(0o755)
    return path


def _release(
    release_id: str,
    origin: str,
    *,
    prerelease: bool = False,
    draft: bool = False,
    include_installer: bool = True,
) -> dict[str, object]:
    assets: list[dict[str, str]] = []
    if include_installer:
        assets.append(
            {
                "name": "install.sh",
                "browser_download_url": f"{origin}/{release_id}/install.sh",
            }
        )
    return {
        "tag_name": release_id,
        "draft": draft,
        "prerelease": prerelease,
        "assets": assets,
    }


def _environment(
    tmp_path: Path,
    root: Path,
    releases: list[dict[str, object]],
    installer: Path | None = None,
) -> tuple[dict[str, str], Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    release_json = tmp_path / "releases.json"
    release_json.write_text(json.dumps(releases))
    handoff = tmp_path / "handoff.log"
    environment = {
        **os.environ,
        "LCC_UPDATE_TESTING": "1",
        "LCC_UPDATE_INSTALL_ROOT": str(root),
        "LCC_UPDATE_TEST_LATEST_JSON": str(release_json),
        "LCC_UPDATE_TEST_HANDOFF_LOG": str(handoff),
    }
    if installer is not None:
        environment["LCC_UPDATE_TEST_INSTALLER"] = str(installer)
    return environment, handoff


@pytest.mark.parametrize(
    ("repository", "origin"),
    ((GITHUB_REPOSITORY, GITHUB_ORIGIN), (FORGEJO_REPOSITORY, FORGEJO_ORIGIN)),
)
def test_stable_update_selects_newest_final_and_preserves_source(
    tmp_path: Path, repository: str, origin: str
) -> None:
    root, _release_path = _installed_root(
        tmp_path,
        channel="stable",
        release_id="v1.0.0",
        revision="1" * 40,
        repository=repository,
        origin=origin,
    )
    installer = _installer(tmp_path / "install.sh", "v1.0.10")
    releases = [
        _release("v1.0.3-rc.1", origin, prerelease=True),
        _release("v9.0.0", origin, draft=True),
        _release("v1.0.1", origin),
        _release("v1.0.2", origin),
        _release("v1.0.10", origin),
    ]
    environment, handoff = _environment(tmp_path, root, releases, installer)
    result = subprocess.run(
        [UPDATER, "--yes"], check=True, capture_output=True, text=True, env=environment
    )
    assert "Target release: v1.0.10" in result.stdout
    arguments = handoff.read_text().splitlines()
    assert arguments[0].endswith("/install.sh")
    assert arguments[1:] == ["--asset-base-url", origin, "--non-interactive"]


def test_stable_same_release_is_noop_and_newer_install_refuses_downgrade(
    tmp_path: Path,
) -> None:
    root, _release_path = _installed_root(
        tmp_path,
        channel="stable",
        release_id="v1.0.2",
        revision="2" * 40,
    )
    releases = [_release("v1.0.2", GITHUB_ORIGIN, include_installer=False)]
    environment, handoff = _environment(tmp_path, root, releases)
    current = subprocess.run(
        [UPDATER, "--yes"], check=True, capture_output=True, text=True, env=environment
    )
    assert current.stdout.strip() == "Learning Control Center is already up to date."
    assert not handoff.exists()

    releases = [_release("v1.0.1", GITHUB_ORIGIN)]
    environment, _handoff = _environment(tmp_path, root, releases)
    older = subprocess.run(
        [UPDATER, "--yes"], check=False, capture_output=True, text=True, env=environment
    )
    assert older.returncode != 0
    assert "rollback workflow" in older.stderr


def test_main_update_is_exact_sha_pinned_and_same_sha_is_noop(tmp_path: Path) -> None:
    old_revision = "3" * 40
    new_revision = "4" * 40
    root, _release_path = _installed_root(
        tmp_path,
        channel="main",
        release_id=f"main-{old_revision}",
        revision=old_revision,
        origin=GITHUB_REPOSITORY,
    )
    environment, handoff = _environment(tmp_path, root, [])
    environment["LCC_UPDATE_TEST_MAIN_SHA"] = new_revision
    result = subprocess.run(
        [UPDATER, "--yes"], check=True, capture_output=True, text=True, env=environment
    )
    assert f"Current source SHA: {old_revision}" in result.stdout
    assert f"Target source SHA: {new_revision}" in result.stdout
    arguments = handoff.read_text().splitlines()
    assert arguments[1:] == [
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


def test_dry_run_reaches_verified_bootstrap_without_applying(tmp_path: Path) -> None:
    root, _release_path = _installed_root(
        tmp_path,
        channel="stable",
        release_id="v1.0.0",
        revision="1" * 40,
    )
    installer = _installer(tmp_path / "install.sh", "v1.0.1")
    environment, handoff = _environment(
        tmp_path,
        root,
        [_release("v1.0.1", GITHUB_ORIGIN)],
        installer,
    )
    result = subprocess.run(
        [UPDATER, "--dry-run"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert "DRY-RUN: verifying stable target v1.0.1" in result.stdout
    assert handoff.read_text().splitlines()[-1] == "--dry-run"


def test_channel_changes_are_explicit_and_forward_confirmation(tmp_path: Path) -> None:
    revision = "5" * 40
    root, _release_path = _installed_root(
        tmp_path,
        channel="stable",
        release_id="v1.0.1",
        revision=revision,
    )
    environment, handoff = _environment(tmp_path, root, [])
    environment["LCC_UPDATE_TEST_MAIN_SHA"] = "6" * 40
    result = subprocess.run(
        [UPDATER, "--channel", "main", "--yes"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert "Channel change: stable -> main" in result.stdout
    assert handoff.read_text().splitlines()[-1] == "--confirm-channel-change"

    main_root, _main_release = _installed_root(
        tmp_path / "main-to-stable",
        channel="main",
        release_id=f"main-{'7' * 40}",
        revision="7" * 40,
        origin=GITHUB_REPOSITORY,
    )
    stable_installer = _installer(tmp_path / "stable-install.sh", "v1.0.2")
    stable_environment, stable_handoff = _environment(
        tmp_path / "stable-switch",
        main_root,
        [_release("v1.0.2", GITHUB_ORIGIN)],
        stable_installer,
    )
    switched = subprocess.run(
        [UPDATER, "--channel", "stable", "--yes"],
        check=True,
        capture_output=True,
        text=True,
        env=stable_environment,
    )
    assert "Channel change: main -> stable" in switched.stdout
    assert stable_handoff.read_text().splitlines()[-1] == "--confirm-channel-change"


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
    for release_name in ("v1.0.1", "v1.0.2"):
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
    current.symlink_to(releases / "v1.0.1")
    first = subprocess.run([wrapper], check=True, capture_output=True, text=True)
    assert first.stdout.strip() == "v1.0.1"

    replacement = application_root / ".current.next"
    replacement.symlink_to(releases / "v1.0.2")
    replacement.replace(current)
    second = subprocess.run([wrapper], check=True, capture_output=True, text=True)
    assert second.stdout.strip() == "v1.0.2"


def test_public_update_commands_and_main_terminology_stay_synchronized() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text()
    installation = (REPOSITORY_ROOT / "docs" / "INSTALLATION.md").read_text()
    updates = (REPOSITORY_ROOT / "docs" / "UPDATES.md").read_text()
    operations = (REPOSITORY_ROOT / "docs" / "PRODUCTION_OPERATIONS.md").read_text()
    release_docs = (REPOSITORY_ROOT / "docs" / "RELEASING.md").read_text()
    documentation = "\n".join((readme, installation, updates, operations, release_docs))
    stable_command = (
        "curl -fsSL https://github.com/Learning-Control-Center/"
        "Learning-Control-Center/releases/latest/download/install.sh | sudo bash"
    )
    assert stable_command in readme
    assert stable_command in installation
    assert stable_command in updates
    assert "sudo /opt/learning-control-center/update.sh" in readme
    assert "sudo lcc-admin update" in readme
    assert "DEVELOPMENT / UNSTABLE" not in documentation
    assert "unstable-main" not in documentation
    assert "Current `main`" in readme


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
