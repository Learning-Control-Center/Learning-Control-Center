"""Installer V2 Core filesystem and handoff contracts; systemd is tested in a VM."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts/install.sh"
SHA = "a" * 40


def _run(
    destination: Path,
    *args: str,
    sha: str = SHA,
    repository: str = "https://github.com/Learning-Control-Center/Learning-Control-Center.git",
    source: Path = ROOT,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(INSTALLER), "--test-root", str(destination), "--core-only", *args],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "LCC_V2_SOURCE_ROOT": str(source),
            "LCC_V2_SOURCE_REPOSITORY": repository,
            "LCC_V2_SOURCE_REF": "refs/heads/main",
            "LCC_V2_SOURCE_SHA": sha,
        },
    )


@pytest.mark.parametrize(
    ("identity", "message"),
    [
        ({"sha": "abc"}, "40-character"),
        ({"sha": "A" * 40}, "40-character"),
        ({"repository": "https://forgejo.example.invalid/repo.git"}, "public GitHub"),
        ({"source": Path("/tmp/nonexistent-lcc-source")}, "source root"),
    ],
)
def test_invalid_acquisition_identity_never_mutates_host(
    tmp_path: Path, identity: dict[str, str | Path], message: str
) -> None:
    destination = tmp_path / "host"
    result = _run(
        destination,
        "--domain",
        "lcc.example.test",
        sha=str(identity.get("sha", SHA)),
        repository=str(
            identity.get(
                "repository",
                "https://github.com/Learning-Control-Center/Learning-Control-Center.git",
            )
        ),
        source=Path(identity.get("source", ROOT)),
    )
    assert result.returncode != 0
    assert message in result.stderr
    assert not destination.exists()


def test_core_layout_repeat_and_secret_preservation(tmp_path: Path) -> None:
    destination = tmp_path / "host"
    first = _run(
        destination,
        "--domain",
        "lcc.example.test",
        "--timezone",
        "Europe/Istanbul",
        "--app-port",
        "8123",
        "--non-interactive",
    )
    assert first.returncode == 0, first.stderr
    assert "Public gateway/TLS: pending" in first.stdout
    release = destination / "opt/learning-control-center/releases" / f"main-{SHA}"
    current = destination / "opt/learning-control-center/current"
    env = destination / "etc/learning-control-center.env"
    assert current.is_symlink() and current.resolve() == release
    assert (release / "SOURCE_REVISION").read_text().strip() == SHA
    assert (release / "INSTALLER_V2_CORE").read_text().strip() == "1"
    assert (release / "RELEASE_MANIFEST").read_text().count(f"source_revision={SHA}") == 1
    assert "LCC_APP_PORT=8123" in env.read_text()
    assert "LCC_APP_TIMEZONE=Europe/Istanbul" in env.read_text()
    assert "LCC_PUBLIC_ORIGIN=https://lcc.example.test" in env.read_text()
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert (destination / "usr/local/sbin/lcc-admin").is_symlink()
    assert not (destination / "etc/caddy").exists()
    assert not (destination / "opt/learning-control-center/update.sh").exists()
    assert not (release / "AGENTS.md").exists()
    assert not (release / "memory-bank").exists()
    assert not (release / "data/lcc.db").exists()
    original_env = env.read_bytes()
    original_release = (release / "RELEASE_MANIFEST").stat().st_mtime_ns
    second = _run(destination, "--non-interactive")
    assert second.returncode == 0, second.stderr
    assert env.read_bytes() == original_env
    assert (release / "RELEASE_MANIFEST").stat().st_mtime_ns == original_release
    assert list(release.parent.iterdir()) == [release]


def test_release_bound_fresh_core_keeps_stable_identity(tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(
        ROOT,
        source,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "node_modules",
            "data",
            "backups",
            "memory-bank",
            "AGENTS.md",
            ".agents",
            ".codex",
            "tmp",
            "__pycache__",
            ".pytest_cache",
        ),
    )
    release_id = "v1.0.2"
    repository = "https://github.com/Learning-Control-Center/Learning-Control-Center.git"
    origin = "https://github.com/Learning-Control-Center/Learning-Control-Center/releases/download"
    (source / "RELEASE_MANIFEST").write_text(
        "metadata_version=1\nchannel=stable\n"
        f"release_id={release_id}\nsource_repository={repository}\n"
        f"source_ref=refs/tags/{release_id}\nsource_revision={SHA}\n"
        f"source_origin={origin}\n"
    )
    destination = tmp_path / "host"
    environment = {
        **os.environ,
        "LCC_V2_SOURCE_ROOT": str(source),
        "LCC_V2_SOURCE_REPOSITORY": repository,
        "LCC_V2_SOURCE_REF": f"refs/tags/{release_id}",
        "LCC_V2_SOURCE_SHA": SHA,
        "LCC_V2_RELEASE_ID": release_id,
    }
    result = subprocess.run(
        [
            source / "scripts/install.sh",
            "--test-root",
            str(destination),
            "--core-only",
            "--domain",
            "lcc.example.test",
            "--non-interactive",
        ],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    release = destination / "opt/learning-control-center/releases" / release_id
    assert (release / "RELEASE_CHANNEL").read_text() == "stable\n"
    assert (release / "RELEASE_MANIFEST").read_text().count(
        f"source_ref=refs/tags/{release_id}"
    ) == 1
    repeated = subprocess.run(
        [
            source / "scripts/install.sh",
            "--test-root",
            str(destination),
            "--core-only",
            "--non-interactive",
        ],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert repeated.returncode == 0, repeated.stderr


def test_v1_installation_refused_without_mutation(tmp_path: Path) -> None:
    destination = tmp_path / "host"
    current = destination / "opt/learning-control-center/current"
    current.parent.mkdir(parents=True)
    current.symlink_to("/opt/learning-control-center/releases/v1.0.0")
    result = _run(destination, "--domain", "lcc.example.test")
    assert result.returncode != 0
    assert "Phase 5" in result.stderr
    assert current.is_symlink()
    assert not (destination / "etc").exists()


def test_unrelated_current_file_is_not_replaced(tmp_path: Path) -> None:
    destination = tmp_path / "host"
    current = destination / "opt/learning-control-center/current"
    current.parent.mkdir(parents=True)
    current.write_text("unrelated occupant\n")
    result = _run(destination, "--domain", "lcc.example.test")
    assert result.returncode != 0
    assert "non-symlink file" in result.stderr
    assert current.read_text() == "unrelated occupant\n"
    assert not (destination / "etc").exists()


def test_exact_empty_failed_install_skeleton_is_reusable(tmp_path: Path) -> None:
    destination = tmp_path / "host"
    releases = destination / "opt/learning-control-center/releases"
    releases.mkdir(parents=True)

    result = _run(destination, "--domain", "lcc.example.test", "--non-interactive")

    assert result.returncode == 0, result.stderr
    assert (releases / f"main-{SHA}" / "INSTALLER_V2_CORE").read_text() == "1\n"


def test_nonempty_failed_install_skeleton_is_refused(tmp_path: Path) -> None:
    destination = tmp_path / "host"
    releases = destination / "opt/learning-control-center/releases"
    releases.mkdir(parents=True)
    (releases / "unrelated").write_text("do not replace\n")

    result = _run(destination, "--domain", "lcc.example.test", "--non-interactive")

    assert result.returncode != 0
    assert "no V2 Core identity" in result.stderr
    assert (releases / "unrelated").read_text() == "do not replace\n"
    assert not (destination / "etc").exists()


def test_unrelated_owned_path_and_modified_unit_are_refused(tmp_path: Path) -> None:
    destination = tmp_path / "host"
    env = destination / "etc/learning-control-center.env"
    env.parent.mkdir(parents=True)
    env.write_text("UNRELATED=1\n")
    result = _run(destination, "--domain", "lcc.example.test")
    assert result.returncode != 0
    assert "pre-existing LCC path" in result.stderr
    assert env.read_text() == "UNRELATED=1\n"
    env.unlink()
    assert _run(destination, "--domain", "lcc.example.test").returncode == 0
    unit = destination / "etc/systemd/system/learning-control-center.service"
    unit.write_text("unrelated unit\n")
    refused = _run(destination)
    assert refused.returncode != 0
    assert "modified or unrelated unit" in refused.stderr
    assert unit.read_text() == "unrelated unit\n"


def test_changed_sha_and_configuration_refused(tmp_path: Path) -> None:
    destination = tmp_path / "host"
    assert _run(destination, "--domain", "lcc.example.test").returncode == 0
    changed = _run(destination, sha="b" * 40)
    assert changed.returncode != 0 and "Phase 5" in changed.stderr
    changed_port = _run(destination, "--app-port", "8123")
    assert changed_port.returncode != 0 and "Phase 4" in changed_port.stderr
    changed_origin = _run(destination, "--domain", "different.example.test")
    assert changed_origin.returncode != 0 and "Domain differs" in changed_origin.stderr


def test_default_port_and_symlinked_owned_path(tmp_path: Path) -> None:
    destination = tmp_path / "host"
    data_path = destination / "var/lib/learning-control-center"
    data_path.parent.mkdir(parents=True)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    data_path.symlink_to(unrelated)
    refused = _run(destination, "--domain", "lcc.example.test")
    assert refused.returncode != 0
    assert "must not be a symlink" in refused.stderr
    assert not list(unrelated.iterdir())
    data_path.unlink()
    installed = _run(destination, "--domain", "lcc.example.test", "--non-interactive")
    assert installed.returncode == 0, installed.stderr
    assert "LCC_APP_PORT=8000" in (destination / "etc/learning-control-center.env").read_text()


def test_noninteractive_core_has_no_domain_caddy_or_git_requirement() -> None:
    script = INSTALLER.read_text()
    assert "lcc_ubuntu_provision_core" in script
    assert "lcc_wait_for_internal_health" in script
    assert "lcc_wait_for_health " not in script
    assert "apt-get" not in script
    assert "caddy.service" not in script
    assert "git clone" not in script


def test_noninteractive_core_without_domain_is_pending(tmp_path: Path) -> None:
    destination = tmp_path / "host"
    result = _run(destination, "--non-interactive")
    assert result.returncode == 0, result.stderr
    environment = (destination / "etc/learning-control-center.env").read_text()
    assert "LCC_PUBLIC_ORIGIN=\n" in environment
    assert "LCC_ALLOWED_ORIGINS='[]'" in environment
    assert "LCC_ALLOWED_HOSTS='[\"127.0.0.1\"]'" in environment
