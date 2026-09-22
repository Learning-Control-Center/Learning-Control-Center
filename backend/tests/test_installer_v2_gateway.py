"""Installer V2 gateway decisions and owned configuration transactions."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMMON = ROOT / "scripts/deploy-common.sh"
GATEWAY = ROOT / "scripts/install/gateway.sh"
INSTALLER = ROOT / "scripts/install.sh"
SHA = "a" * 40


def _shell(
    script: str, *args: str, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script, "gateway-test", str(COMMON), str(GATEWAY), *args],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(environment or {})},
    )


def _install(destination: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(INSTALLER), "--test-root", str(destination), *args],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "LCC_V2_SOURCE_ROOT": str(ROOT),
            "LCC_V2_SOURCE_REPOSITORY": "https://github.com/Learning-Control-Center/Learning-Control-Center.git",
            "LCC_V2_SOURCE_REF": "refs/heads/main",
            "LCC_V2_SOURCE_SHA": SHA,
        },
    )


@pytest.mark.parametrize("mode", ["caddy", "external"])
def test_gateway_state_roundtrip_is_minimal_and_idempotent(tmp_path: Path, mode: str) -> None:
    state = tmp_path / "deployment"
    script = """
set -euo pipefail
source "$1"
lcc_write_gateway_state "$3" "$4" "$(id -u)"
lcc_read_gateway_state "$4" "$(id -u)"
lcc_write_gateway_state "$3" "$4" "$(id -u)"
"""
    result = _shell(script, mode, str(state))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == mode
    assert state.read_text() == f"format_version=1\ngateway={mode}\n"
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert state.stat().st_uid == os.getuid()
    assert not list(tmp_path.glob(".learning-control-center.deployment.*"))


@pytest.mark.parametrize(
    "content",
    [
        "format_version=2\ngateway=external\n",
        "format_version=1\ngateway=nginx\n",
        "format_version=1\ngateway=caddy\nsecret=oops\n",
        "format_version=1\ngateway=caddy\ngateway=external\n",
    ],
)
def test_invalid_gateway_state_is_rejected(tmp_path: Path, content: str) -> None:
    state = tmp_path / "deployment"
    state.write_text(content)
    state.chmod(0o600)
    result = _shell(
        'set -euo pipefail; source "$1"; lcc_read_gateway_state "$3" "$(id -u)"', str(state)
    )
    assert result.returncode != 0


def test_gateway_state_race_refuses_to_overwrite_unrelated_file(tmp_path: Path) -> None:
    state = tmp_path / "deployment"
    script = """
set -euo pipefail
source "$1"
ln() { printf 'unrelated operator file\\n' > "$3"; return 1; }
lcc_write_gateway_state caddy "$3" "$(id -u)"
"""
    result = _shell(script, str(state))
    assert result.returncode != 0
    assert state.read_text() == "unrelated operator file\n"
    assert not list(tmp_path.glob(".learning-control-center.deployment.*"))


def test_external_gateway_layout_and_same_sha_repeat(tmp_path: Path) -> None:
    destination = tmp_path / "host"
    first = _install(
        destination,
        "--gateway",
        "external",
        "--domain",
        "lcc.example.test",
        "--app-port",
        "8123",
        "--non-interactive",
    )
    assert first.returncode == 0, first.stderr
    assert "Public gateway/TLS: pending" in first.stdout
    assert "http://127.0.0.1:8123" in first.stdout
    assert "Proxy /api/*" in first.stdout
    state = destination / "etc/learning-control-center.deployment"
    assert state.read_text() == "format_version=1\ngateway=external\n"
    assert not (destination / "etc/caddy").exists()
    original_state_mtime = state.stat().st_mtime_ns
    repeated = _install(destination, "--non-interactive")
    assert repeated.returncode == 0, repeated.stderr
    assert state.stat().st_mtime_ns == original_state_mtime
    refused = _install(destination, "--gateway", "caddy", "--non-interactive")
    assert refused.returncode != 0 and "switching modes" in refused.stderr
    assert state.read_text() == "format_version=1\ngateway=external\n"


def test_default_managed_requires_live_host_and_invalid_gateway_is_early_failure(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "host"
    default = _install(destination, "--domain", "lcc.example.test", "--non-interactive")
    assert default.returncode != 0 and "Managed Caddy requires" in default.stderr
    assert not destination.exists()
    invalid = _install(destination, "--gateway", "nginx", "--domain", "lcc.example.test")
    assert invalid.returncode != 0 and "Gateway must be caddy or external" in invalid.stderr
    assert not destination.exists()


def _caddy_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    caddy = tmp_path / "caddy"
    caddy.write_text(
        "#!/usr/bin/env bash\n"
        'if test "$1" = validate && test "${FAIL_VALIDATE:-0}" = 1; then exit 1; fi\n'
        "exit 0\n"
    )
    caddy.chmod(0o755)
    directory = tmp_path / "etc/caddy/Caddyfile.d"
    directory.mkdir(parents=True)
    main = directory.parent / "Caddyfile"
    main.write_text("# unrelated Caddy site\nother.example.test { respond hi }\n")
    site = directory / "learning-control-center.caddy"
    return caddy, main, site, tmp_path / "deployment"


@pytest.mark.parametrize(
    "failure", ["none", "package", "render", "validate", "reload", "internal", "public"]
)
def test_managed_caddy_owns_only_site_and_restores_on_failure(tmp_path: Path, failure: str) -> None:
    caddy, main, site, state = _caddy_fixture(tmp_path)
    site.parent.chmod(0o750)
    original_main = main.read_bytes()
    script = """
set -euo pipefail
source "$1"
source "$2"
LCC_CADDY_BINARY="$3"
LCC_CADDY_MAIN="$4"
LCC_CADDY_SITE="$5"
LCC_CADDY_IMPORT="import $(dirname -- "$5")/*.caddy"
LCC_DEPLOYMENT_STATE_FILE="$6"
LCC_TRANSACTION_DIRECTORY_PARENT="$(dirname -- "$6")"
LCC_CURRENT_RELEASE=/opt/learning-control-center/current
LCC_PUBLIC_ORIGIN=https://lcc.example.test
lcc_ubuntu_provision_managed_caddy() { test "${FAIL_MODE:-none}" != package; }
lcc_verify_managed_caddy() { return 0; }
lcc_gateway_listener_conflict() { return 1; }
lcc_render_caddy_site() {
    test "${FAIL_MODE:-none}" != render || return 1
    printf 'lcc.example.test { reverse_proxy 127.0.0.1:8000 }\\n' > "$3"
}
lcc_effective_app_port() { echo 8000; }
lcc_wait_for_internal_health() { test "${FAIL_MODE:-none}" != internal; }
lcc_wait_for_health() { test "${FAIL_MODE:-none}" != public; }
lcc_write_gateway_state() {
    printf 'format_version=1\\ngateway=caddy\\n' > "$LCC_DEPLOYMENT_STATE_FILE"
}
reload_calls=0
systemctl() {
    if test "$1" = is-active; then return 0; fi
    if test "$1" = reload && test "${FAIL_MODE:-none}" = reload; then
        reload_calls=$((reload_calls + 1))
        test "$reload_calls" -gt 1
        return
    fi
    return 0
}
lcc_install_managed_caddy "$7" ""
"""
    result = _shell(
        script,
        str(caddy),
        str(main),
        str(site),
        str(state),
        str(ROOT),
        environment={"FAIL_MODE": failure, "FAIL_VALIDATE": "1" if failure == "validate" else "0"},
    )
    if failure == "none":
        assert result.returncode == 0, result.stderr
        assert site.exists() and "Managed by Learning Control Center" in site.read_text()
        assert b"# unrelated Caddy site" in main.read_bytes()
        assert state.read_text().splitlines() == ["format_version=1", "gateway=caddy"]
        site_mtime = site.stat().st_mtime_ns
        main_mtime = main.stat().st_mtime_ns
        repeat = _shell(
            script.replace(
                'lcc_install_managed_caddy "$7" ""', 'lcc_install_managed_caddy "$7" "caddy"'
            ),
            str(caddy),
            str(main),
            str(site),
            str(state),
            str(ROOT),
        )
        assert repeat.returncode == 0, repeat.stderr
        assert "no changes made" in repeat.stdout
        assert site.stat().st_mtime_ns == site_mtime
        assert main.stat().st_mtime_ns == main_mtime
    else:
        assert result.returncode != 0, (result.stdout, result.stderr)
        assert main.read_bytes() == original_main
        assert not site.exists()
        assert not state.exists()
    assert stat.S_IMODE(site.parent.stat().st_mode) == 0o750
    assert not list(tmp_path.glob("lcc-gateway.*"))


def test_unmanaged_caddy_site_is_refused_without_overwrite(tmp_path: Path) -> None:
    caddy, main, site, state = _caddy_fixture(tmp_path)
    site.write_text("unrelated site\n")
    result = _shell(
        'set -euo pipefail; source "$1"; source "$2"; '
        'LCC_CADDY_SITE="$3"; LCC_CADDY_MAIN="$4"; '
        'lcc_install_managed_caddy "$5" ""',
        str(site),
        str(main),
        str(ROOT),
    )
    assert result.returncode != 0
    assert "unmanaged or V1" in result.stderr
    assert site.read_text() == "unrelated site\n"
    assert not state.exists()


def test_symlinked_caddy_config_parent_is_refused_before_provisioning(tmp_path: Path) -> None:
    _, main, site, _ = _caddy_fixture(tmp_path)
    actual_parent = main.parent.with_name("caddy-owned-elsewhere")
    main.parent.rename(actual_parent)
    main.parent.symlink_to(actual_parent, target_is_directory=True)
    provision_marker = tmp_path / "package-requested"
    result = _shell(
        """
set -euo pipefail
source "$1"
source "$2"
LCC_CADDY_MAIN="$3"
LCC_CADDY_SITE="$4"
lcc_ubuntu_provision_managed_caddy() { touch "$5"; }
lcc_install_managed_caddy "$6" ""
""",
        str(main),
        str(site),
        str(provision_marker),
        str(ROOT),
    )
    assert result.returncode != 0
    assert "Refusing symlinked Caddy configuration paths" in result.stderr
    assert not provision_marker.exists()
    assert main.read_text().startswith("# unrelated Caddy site")


def test_non_caddy_public_listener_blocks_managed_package_provisioning(tmp_path: Path) -> None:
    marker = tmp_path / "package-requested"
    script = """
set -euo pipefail
source "$1"
source "$2"
ss() {
    if [[ "$*" = *:80* ]]; then
        printf 'LISTEN 0 128 0.0.0.0:80 0.0.0.0:* users:(("nginx",pid=4444,fd=3))\\n'
    fi
}
systemctl() { return 1; }
lcc_ubuntu_provision_managed_caddy() { touch "$3"; }
lcc_install_managed_caddy "$4" ""
"""
    result = _shell(script, str(marker), str(ROOT))
    assert result.returncode != 0
    assert "process=nginx pid=4444" in result.stderr
    assert "Choose --gateway external explicitly" in result.stderr
    assert not marker.exists()


def test_external_nginx_example_preserves_api_prefix_and_spa_fallback() -> None:
    example = (ROOT / "deploy/examples/installer-v2-external-nginx.conf").read_text()
    assert "proxy_pass http://127.0.0.1:8000;" in example
    assert "proxy_set_header Host $host;" in example
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in example
    assert "try_files $uri $uri/ /index.html;" in example
    assert "/etc/learning-control-center.env" not in example


@pytest.mark.parametrize("mode", ["dry-run", "success", "internal-failure"])
def test_external_port_transaction_changes_only_core_and_rolls_back(
    tmp_path: Path, mode: str
) -> None:
    root = tmp_path / "host"
    current = root / "opt/learning-control-center/current"
    release = current.parent / "releases/test"
    release.mkdir(parents=True)
    current.symlink_to(release)
    environment = root / "etc/learning-control-center.env"
    environment.parent.mkdir(parents=True)
    generator = ROOT / "scripts/generate-production-env.sh"
    generated = subprocess.run(
        [
            str(generator),
            "--root",
            str(root),
            "--domain",
            "lcc.example.test",
            "--timezone",
            "UTC",
            "--app-port",
            "8000",
            "--output",
            str(environment),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr
    old_environment = environment.read_bytes()
    unrelated_proxy = tmp_path / "nginx.conf"
    unrelated_proxy.write_text("operator-owned proxy\n")
    script = """
set -euo pipefail
source "$1"
LCC_ENVIRONMENT_FILE="$3"
LCC_CURRENT_RELEASE="$4"
LCC_TRANSACTION_DIRECTORY_PARENT="$5"
lcc_app_port_is_available() { return 0; }
lcc_install_environment_file() { install -m 0640 "$1" "$2.next"; mv -f "$2.next" "$2"; }
systemctl() { return 0; }
lcc_wait_for_internal_health() {
    test "${PORT_MODE:-success}" != internal-failure || test "$1" = 8000
}
lcc_load_environment "$LCC_ENVIRONMENT_FILE"
lcc_validate_environment "$LCC_CURRENT_RELEASE"
lcc_apply_external_app_port_change 8123 "$6"
"""
    result = _shell(
        script,
        str(environment),
        str(current),
        str(tmp_path),
        "1" if mode == "dry-run" else "0",
        environment={"PORT_MODE": mode},
    )
    assert unrelated_proxy.read_text() == "operator-owned proxy\n"
    if mode == "success":
        assert result.returncode == 0, result.stderr
        assert "LCC_APP_PORT=8123" in environment.read_text()
        assert "public gateway/TLS is pending" in result.stdout
    else:
        assert environment.read_bytes() == old_environment
        if mode == "dry-run":
            assert result.returncode == 0, result.stderr
        else:
            assert result.returncode != 0
    assert not list(tmp_path.glob("lcc-external-port.*"))
