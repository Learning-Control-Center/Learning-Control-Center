"""Installer V2 Ubuntu adapter decisions, without simulating APT metadata internals."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "scripts/install/platforms/ubuntu-24.04.sh"
CORE_PACKAGES = [
    "ca-certificates",
    "curl",
    "python3",
    "python3-venv",
    "sqlite3",
    "rsync",
    "tar",
    "gzip",
    "iproute2",
    "util-linux",
]


@pytest.fixture
def host(tmp_path: Path) -> Path:
    (tmp_path / "bin").mkdir()
    (tmp_path / "run/systemd/system").mkdir(parents=True)
    (tmp_path / "os-release").write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
    (tmp_path / "installed").write_text("\n".join(CORE_PACKAGES) + "\n")
    sources = tmp_path / "sources.list.d"
    sources.mkdir()
    for name in ("docker.list", "cloudflare.list", "cloudsmith.list"):
        (sources / name).write_text(f"deb https://{name}.example.test noble main\n")

    commands = {
        "dpkg": """#!/usr/bin/env bash
printf '%s\\n' "${LCC_TEST_ARCH:-amd64}"
""",
        "systemctl": """#!/usr/bin/env bash
test "${1:-}" = --system || exit 2
test "${LCC_TEST_SYSTEMD_FAILURE:-0}" = 0 || exit 1
printf '%s\\n' running
""",
        "id": """#!/usr/bin/env bash
printf '%s\\n' "${LCC_TEST_UID:-0}"
""",
        "dpkg-query": """#!/usr/bin/env bash
package="${@: -1}"
if grep -Fxq "$package" "$LCC_TEST_HOST/installed"; then
    if test "$package" = "${LCC_TEST_DEINSTALL_SELECTION:-}"; then
        printf '%s' 'deinstall ok installed'
    else
        printf '%s' 'install ok installed'
    fi
else
    exit 1
fi
""",
        "apt-get": """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$LCC_TEST_HOST/apt.log"
case " $* " in
    *" update "*)
        if test "${LCC_TEST_REFRESH_FAILURE:-0}" = 1; then
            printf '%s\\n' 'Temporary vendor repository failure' >&2
            exit 100
        fi ;;
    *" install "*)
        if test "${LCC_TEST_INSTALL_FAILURE:-0}" = 1; then
            printf '%s\\n' 'APT: dependency resolution failed' >&2
            exit 100
        fi
        after_install=0
        for item in "$@"; do
            if test "$after_install" = 1; then
                printf '%s\\n' "$item" >> "$LCC_TEST_HOST/installed"
            fi
            if test "$item" = install; then after_install=1; fi
        done ;;
    *) exit 2 ;;
esac
""",
    }
    for name, content in commands.items():
        binary = tmp_path / "bin" / name
        binary.write_text(content)
        binary.chmod(0o755)
    return tmp_path


def _run(host: Path, command: str, **changes: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PATH": f"{host / 'bin'}:{os.environ['PATH']}",
        "LCC_TEST_HOST": str(host),
        **changes,
    }
    return subprocess.run(
        ["bash", str(ADAPTER), command, str(host / "os-release"), str(host / "run/systemd/system")],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _apt_calls(host: Path) -> list[str]:
    path = host / "apt.log"
    return path.read_text().splitlines() if path.exists() else []


def test_exact_supported_platform_and_architectures(host: Path) -> None:
    for arch in ("amd64", "arm64"):
        assert _run(host, "check-platform", LCC_TEST_ARCH=arch).returncode == 0
    assert _run(host, "check-platform", LCC_TEST_ARCH="riscv64").returncode != 0
    assert _run(host, "check-platform", LCC_TEST_SYSTEMD_FAILURE="1").returncode != 0
    (host / "os-release").write_text('ID=debian\nID_LIKE=ubuntu\nVERSION_ID="24.04"\n')
    assert _run(host, "check-platform").returncode != 0
    (host / "os-release").write_text('ID=ubuntu\nVERSION_ID="22.04"\n')
    assert _run(host, "check-platform").returncode != 0
    (host / "os-release").write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
    (host / "run/systemd/system").rmdir()
    assert _run(host, "check-platform").returncode != 0


def test_package_mapping_excludes_git_caddy_and_node(host: Path) -> None:
    assert _run(host, "core-packages").stdout.splitlines() == CORE_PACKAGES
    assert _run(host, "managed-caddy-packages").stdout.splitlines() == ["caddy"]
    for excluded in ("git", "caddy", "nodejs", "npm"):
        assert excluded not in CORE_PACKAGES


def test_optional_managed_caddy_is_named_only_when_missing(host: Path) -> None:
    sources = {path.name: path.read_bytes() for path in (host / "sources.list.d").iterdir()}
    missing = _run(host, "provision-managed-caddy")
    assert missing.returncode == 0, missing.stderr
    assert _apt_calls(host) == [
        "-o APT::Update::Error-Mode=any update",
        "-y --no-install-recommends --no-remove install caddy",
    ]
    repeated = _run(host, "provision-managed-caddy")
    assert repeated.returncode == 0, repeated.stderr
    assert len(_apt_calls(host)) == 2
    assert sources == {path.name: path.read_bytes() for path in (host / "sources.list.d").iterdir()}


def test_optional_caddy_refresh_warning_and_install_failure(host: Path) -> None:
    warning = _run(host, "provision-managed-caddy", LCC_TEST_REFRESH_FAILURE="1")
    assert warning.returncode == 0, warning.stderr
    assert "APT metadata refresh did not fully succeed" in warning.stdout
    (host / "installed").write_text("\n".join(CORE_PACKAGES) + "\n")
    failure = _run(host, "provision-managed-caddy", LCC_TEST_INSTALL_FAILURE="1")
    assert failure.returncode != 0
    assert "APT: dependency resolution failed" in failure.stderr
    assert "Managed Caddy package installation failed" in failure.stderr


def test_satisfied_packages_skip_apt_and_verify_capabilities(host: Path) -> None:
    result = _run(host, "provision-core")
    assert result.returncode == 0, result.stderr
    assert "already installed" in result.stdout
    assert _apt_calls(host) == []


def test_dpkg_deinstall_selection_with_installed_files_is_not_missing(host: Path) -> None:
    result = _run(host, "provision-core", LCC_TEST_DEINSTALL_SELECTION="rsync")
    assert result.returncode == 0, result.stderr
    assert _apt_calls(host) == []


def test_missing_named_packages_use_host_apt_and_preserve_sources(host: Path) -> None:
    (host / "installed").write_text("\n".join(CORE_PACKAGES[:-2]) + "\n")
    sources = {path.name: path.read_bytes() for path in (host / "sources.list.d").iterdir()}
    result = _run(host, "provision-core")
    assert result.returncode == 0, result.stderr
    assert _apt_calls(host) == [
        "-o APT::Update::Error-Mode=any update",
        "-y --no-install-recommends --no-remove install iproute2 util-linux",
    ]
    assert sources == {path.name: path.read_bytes() for path in (host / "sources.list.d").iterdir()}
    assert _run(host, "missing-core").stdout == ""


def test_refresh_failure_is_visible_and_install_still_runs(host: Path) -> None:
    (host / "installed").write_text("\n".join(CORE_PACKAGES[:-1]) + "\n")
    result = _run(host, "provision-core", LCC_TEST_REFRESH_FAILURE="1")
    assert result.returncode == 0, result.stderr
    assert "APT metadata refresh did not fully succeed" in result.stdout
    assert "Temporary vendor repository failure" in result.stderr
    assert _apt_calls(host)[-1].endswith("install util-linux")


def test_install_failure_surfaces_apt_error_and_stops(host: Path) -> None:
    (host / "installed").write_text("\n".join(CORE_PACKAGES[:-1]) + "\n")
    result = _run(host, "provision-core", LCC_TEST_INSTALL_FAILURE="1")
    assert result.returncode != 0
    assert "APT: dependency resolution failed" in result.stderr
    assert "Required package installation failed" in result.stderr
    assert "util-linux" not in (host / "installed").read_text()


def test_non_root_fails_without_apt_and_no_upgrade_is_possible(host: Path) -> None:
    (host / "installed").write_text("\n".join(CORE_PACKAGES[:-1]) + "\n")
    result = _run(host, "provision-core", LCC_TEST_UID="1000")
    assert result.returncode != 0
    assert _apt_calls(host) == []
    script = ADAPTER.read_text()
    assert "apt-get -o APT::Update::Error-Mode=any update" in script
    assert "--no-remove" in script
    for banned in (
        "apt upgrade",
        "full-upgrade",
        "dist-upgrade",
        "--allow-unauthenticated",
        "indextargets",
        "SIGNED_BY",
        "apt-key",
    ):
        assert banned not in script


def test_installed_package_with_missing_capability_fails_without_apt(host: Path) -> None:
    binary = host / "bin" / "python3"
    binary.write_text("#!/usr/bin/env bash\nexit 1\n")
    binary.chmod(0o755)
    result = _run(host, "provision-core")
    assert result.returncode != 0
    assert "venv/ensurepip" in result.stderr
    assert _apt_calls(host) == []


def test_post_install_capability_verification_catches_missing_runtime(host: Path) -> None:
    (host / "installed").write_text("\n".join(CORE_PACKAGES[:-1]) + "\n")
    binary = host / "bin" / "python3"
    binary.write_text("#!/usr/bin/env bash\nexit 1\n")
    binary.chmod(0o755)
    result = _run(host, "provision-core")
    assert result.returncode != 0
    assert _apt_calls(host)[-1].endswith("install util-linux")
    assert "venv/ensurepip" in result.stderr


def test_refresh_and_install_failure_reports_both_errors(host: Path) -> None:
    (host / "installed").write_text("\n".join(CORE_PACKAGES[:-1]) + "\n")
    result = _run(
        host, "provision-core", LCC_TEST_REFRESH_FAILURE="1", LCC_TEST_INSTALL_FAILURE="1"
    )
    assert result.returncode != 0
    assert "APT metadata refresh did not fully succeed" in result.stdout
    assert "APT: dependency resolution failed" in result.stderr
