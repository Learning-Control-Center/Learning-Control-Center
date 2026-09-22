from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
COMMON = REPOSITORY_ROOT / "scripts" / "deploy-common.sh"
GENERATOR = REPOSITORY_ROOT / "scripts" / "generate-production-env.sh"


def _run_common(command: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f'source "$1"; shift; {command}', "app-port-test", str(COMMON), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("value", ["1024", "8000", "65535"])
def test_app_port_validation_accepts_canonical_unprivileged_range(value: str) -> None:
    result = _run_common('lcc_validate_app_port "$1"', value)
    assert result.returncode == 0
    assert result.stdout.strip() == value


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("0", "outside the allowed range"),
        ("1", "1024 or higher"),
        ("80", "1024 or higher"),
        ("443", "1024 or higher"),
        ("1023", "1024 or higher"),
        ("65536", "between 1024 and 65535"),
        (" 8000", "ASCII decimal digits"),
        ("8000 ", "ASCII decimal digits"),
        ("+8000", "ASCII decimal digits"),
        ("-8000", "ASCII decimal digits"),
        ("8000.0", "ASCII decimal digits"),
        ("port", "ASCII decimal digits"),
        ("08000", "without leading zeros"),
    ],
)
def test_app_port_validation_rejects_invalid_values(value: str, message: str) -> None:
    result = _run_common('lcc_validate_app_port "$1"', value)
    assert result.returncode != 0
    assert message in result.stderr


def test_effective_app_port_distinguishes_explicit_and_legacy_default(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy.env"
    legacy.write_text("LCC_ENVIRONMENT=production\n")
    legacy_result = _run_common(
        'lcc_load_environment "$1"; lcc_effective_app_port; '
        "if lcc_app_port_is_explicit; then echo explicit; else echo legacy; fi",
        str(legacy),
    )
    assert legacy_result.returncode == 0
    assert legacy_result.stdout.splitlines() == ["8000", "legacy"]

    explicit = tmp_path / "explicit.env"
    explicit.write_text("LCC_ENVIRONMENT=production\nLCC_APP_PORT=8123\n")
    explicit_result = _run_common(
        'lcc_load_environment "$1"; lcc_effective_app_port; '
        "if lcc_app_port_is_explicit; then echo explicit; else echo legacy; fi",
        str(explicit),
    )
    assert explicit_result.returncode == 0
    assert explicit_result.stdout.splitlines() == ["8123", "explicit"]


def test_loopback_bind_probe_detects_an_occupied_port() -> None:
    try:
        listener_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except PermissionError:
        pytest.skip("execution sandbox forbids IPv4 sockets")
    with listener_socket as listener:
        listener.bind(("127.0.0.1", 0))
        port = str(listener.getsockname()[1])
        occupied = _run_common('lcc_app_port_is_available "$1"', port)
    available = _run_common('lcc_app_port_is_available "$1"', port)
    assert occupied.returncode != 0
    assert available.returncode == 0


def test_listener_is_accepted_only_when_owned_by_active_lcc_main_pid() -> None:
    owned = _run_common(
        """
systemctl() {
    if test "$1" = is-active; then return 0; fi
    if test "$1" = show; then printf '4242\\n'; return 0; fi
    return 1
}
ss() { printf 'LISTEN 0 128 127.0.0.1:8000 0.0.0.0:* users:(("uvicorn",pid=4242,fd=3))\\n'; }
lcc_app_port_owned_by_service 8000
"""
    )
    assert owned.returncode == 0, owned.stderr

    ambiguous = _run_common(
        """
systemctl() {
    if test "$1" = is-active; then return 0; fi
    if test "$1" = show; then printf '4242\\n'; return 0; fi
    return 1
}
ss() { printf 'LISTEN 0 128 127.0.0.1:8000 0.0.0.0:* users:(("other",pid=9000,fd=3))\\n'; }
lcc_app_port_owned_by_service 8000
"""
    )
    assert ambiguous.returncode != 0

    shared = _run_common(
        """
systemctl() {
    if test "$1" = is-active; then return 0; fi
    if test "$1" = show; then printf '4242\\n'; return 0; fi
    return 1
}
ss() {
    printf 'LISTEN 0 128 0.0.0.0:80 0.0.0.0:* '
    printf 'users:(("caddy",pid=4242,fd=3),("other",pid=9000,fd=4))\\n'
}
lcc_app_port_owned_by_service 80 caddy.service
"""
    )
    assert shared.returncode != 0


def test_environment_generator_and_caddy_renderer_use_custom_app_port(tmp_path: Path) -> None:
    root = tmp_path / "root"
    environment = tmp_path / "production.env"
    subprocess.run(
        [
            GENERATOR,
            "--domain",
            "lcc.example.test",
            "--timezone",
            "UTC",
            "--app-port",
            "8123",
            "--output",
            environment,
            "--root",
            root,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "LCC_APP_PORT=8123" in environment.read_text()
    rendered = tmp_path / "site.caddy"
    result = _run_common(
        'lcc_load_environment "$1"; lcc_render_caddy_site "$2" "$3"',
        str(environment),
        str(REPOSITORY_ROOT),
        str(rendered),
    )
    assert result.returncode == 0, result.stderr
    assert "reverse_proxy 127.0.0.1:8123" in rendered.read_text()
    assert "LCC_SECURITY_SECRET" not in rendered.read_text()


def test_static_systemd_unit_uses_environment_override_without_shell() -> None:
    unit = (REPOSITORY_ROOT / "deploy" / "learning-control-center.service").read_text()
    assert "Environment=LCC_APP_PORT=8000" in unit
    assert "EnvironmentFile=/etc/learning-control-center.env" in unit
    assert "--host 127.0.0.1 --port ${LCC_APP_PORT}" in unit
    assert "0.0.0.0" not in unit
    assert "/bin/sh" not in unit and "/bin/bash" not in unit


def _admin_fixture(
    tmp_path: Path, app_port: str | None, gateway: str | None = None
) -> tuple[Path, Path, dict[str, str]]:
    install_root = tmp_path / "installed-root"
    application_root = install_root / "opt" / "learning-control-center"
    release = application_root / "releases" / "test-release"
    scripts = release / "scripts"
    runtime = release / ".venv" / "bin"
    scripts.mkdir(parents=True)
    runtime.mkdir(parents=True)
    (runtime / "python").symlink_to(sys.executable)
    current = application_root / "current"
    current.symlink_to(release)
    if gateway is not None:
        (release / "INSTALLER_V2_CORE").write_text("1\n")
        (release / "RELEASE_ID").write_text("main-" + "a" * 40 + "\n")
        (release / "RELEASE_CHANNEL").write_text("main\n")
        (release / "SOURCE_REVISION").write_text("a" * 40 + "\n")
        state = install_root / "etc/learning-control-center.deployment"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(f"format_version=1\ngateway={gateway}\n")
        state.chmod(0o600)

    environment = install_root / "etc" / "learning-control-center.env"
    environment.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            GENERATOR,
            "--domain",
            "lcc.example.test",
            "--timezone",
            "UTC",
            "--output",
            environment,
            "--root",
            install_root,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if app_port is None:
        environment.write_text(
            "\n".join(
                line
                for line in environment.read_text().splitlines()
                if not line.startswith("LCC_APP_PORT=")
            )
            + "\n"
        )
    elif app_port != "8000":
        environment.write_text(
            environment.read_text().replace("LCC_APP_PORT=8000", f"LCC_APP_PORT={app_port}")
        )

    common = COMMON.read_text()
    replacements = {
        'LCC_APPLICATION_ROOT="/opt/learning-control-center"': (
            f'LCC_APPLICATION_ROOT="{application_root}"'
        ),
        'LCC_CURRENT_RELEASE="/opt/learning-control-center/current"': (
            f'LCC_CURRENT_RELEASE="{current}"'
        ),
        'LCC_ENVIRONMENT_FILE="/etc/learning-control-center.env"': (
            f'LCC_ENVIRONMENT_FILE="{environment}"'
        ),
        'LCC_DEPLOYMENT_STATE_FILE="/etc/learning-control-center.deployment"': (
            f'LCC_DEPLOYMENT_STATE_FILE="{install_root / "etc/learning-control-center.deployment"}"'
        ),
        'local lock_file="${1:-/run/lock/learning-control-center-deployment.lock}"': (
            f'local lock_file="${{1:-{tmp_path / "deployment.lock"}}}"'
        ),
    }
    for old, new in replacements.items():
        assert old in common
        common = common.replace(old, new)
    (scripts / "deploy-common.sh").write_text(common)
    shutil.copy2(REPOSITORY_ROOT / "scripts" / "lcc-admin", scripts / "lcc-admin")
    if gateway is not None:
        administrator = scripts / "lcc-admin"
        administrator.write_text(
            administrator.read_text().replace(
                'gateway_mode="$(lcc_read_gateway_state)"',
                'gateway_mode="$(lcc_read_gateway_state '
                f'"$LCC_DEPLOYMENT_STATE_FILE" "{os.getuid()}")"',
            )
        )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_id = fake_bin / "id"
    fake_id.write_text(
        '#!/usr/bin/env bash\nif test "${1:-}" = -u; then echo 0; else exec /usr/bin/id "$@"; fi\n'
    )
    fake_id.chmod(0o755)
    command_environment = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}
    return scripts / "lcc-admin", environment, command_environment


def test_lcc_admin_reports_explicit_and_legacy_ports_and_same_port_is_a_noop(
    tmp_path: Path,
) -> None:
    explicit_admin, _explicit_environment, explicit_process_environment = _admin_fixture(
        tmp_path / "explicit", "8123"
    )
    explicit = subprocess.run(
        [explicit_admin, "app-port"],
        check=True,
        capture_output=True,
        text=True,
        env=explicit_process_environment,
    )
    assert "Internal application port: 8123" in explicit.stdout
    assert "Bind address: 127.0.0.1" in explicit.stdout
    assert "Configuration: explicit (LCC_APP_PORT)" in explicit.stdout

    legacy_admin, legacy_environment, legacy_process_environment = _admin_fixture(
        tmp_path / "legacy", None
    )
    legacy = subprocess.run(
        [legacy_admin, "app-port"],
        check=True,
        capture_output=True,
        text=True,
        env=legacy_process_environment,
    )
    assert "Internal application port: 8000" in legacy.stdout
    assert "Configuration: legacy default 8000" in legacy.stdout
    before = legacy_environment.read_text()
    unchanged = subprocess.run(
        [legacy_admin, "app-port", "set", "8000", "--yes"],
        check=True,
        capture_output=True,
        text=True,
        env=legacy_process_environment,
    )
    assert "already 8000; no changes were made" in unchanged.stdout
    assert legacy_environment.read_text() == before


def test_v2_external_admin_requires_acknowledgement_and_reports_separate_health(
    tmp_path: Path,
) -> None:
    admin, environment, process_environment = _admin_fixture(tmp_path, "8123", "external")
    fake_bin = Path(process_environment["PATH"].split(":", 1)[0])
    fake_systemctl = fake_bin / "systemctl"
    fake_systemctl.write_text(
        '#!/usr/bin/env bash\nif test "$1" = status; then echo "service active"; fi\nexit 0\n'
    )
    fake_systemctl.chmod(0o755)
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$*" = *https://* ]] && test "${FAIL_PUBLIC:-0}" = 1; then exit 7; fi\n'
        'echo \'{"status":"ok"}\'\n'
    )
    fake_curl.chmod(0o755)
    before = environment.read_bytes()
    refused = subprocess.run(
        [admin, "app-port", "set", "8124", "--yes"],
        capture_output=True,
        text=True,
        check=False,
        env=process_environment,
    )
    assert refused.returncode != 0
    assert "--ack-external-proxy" in refused.stderr
    assert environment.read_bytes() == before
    status = subprocess.run(
        [admin, "status"],
        capture_output=True,
        text=True,
        check=False,
        env={**process_environment, "FAIL_PUBLIC": "1"},
    )
    assert status.returncode == 0, status.stderr
    assert "Gateway mode: external" in status.stdout
    assert "Internal Core health: healthy" in status.stdout
    assert "Public gateway/TLS: pending/unhealthy" in status.stdout
    internal = subprocess.run(
        [admin, "health", "--internal"],
        capture_output=True,
        text=True,
        check=False,
        env={**process_environment, "FAIL_PUBLIC": "1"},
    )
    assert internal.returncode == 0
    public_pending = subprocess.run(
        [admin, "health", "--public"],
        capture_output=True,
        text=True,
        check=False,
        env={**process_environment, "FAIL_PUBLIC": "1"},
    )
    assert public_pending.returncode != 0
    public_ready = subprocess.run(
        [admin, "health", "--public"],
        capture_output=True,
        text=True,
        check=False,
        env=process_environment,
    )
    assert public_ready.returncode == 0


def _transaction_fixture(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "root"
    release = root / "release"
    (release / "deploy").mkdir(parents=True)
    (release / "frontend" / "dist").mkdir(parents=True)
    (release / "deploy" / "Caddyfile.template").write_text(
        "@@LCC_PUBLIC_HOST@@ {\n\t\treverse_proxy 127.0.0.1:@@LCC_APP_PORT@@\n}\n"
    )
    current = root / "current"
    current.symlink_to(release)
    environment = root / "learning-control-center.env"
    subprocess.run(
        [
            GENERATOR,
            "--domain",
            "lcc.example.test",
            "--timezone",
            "UTC",
            "--output",
            environment,
            "--root",
            root,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    environment.write_text(
        environment.read_text().replace(
            f"LCC_RELEASE_ROOT={root}/opt/learning-control-center/current",
            f"LCC_RELEASE_ROOT={current}",
        )
    )
    caddy_site = root / "etc" / "caddy" / "Caddyfile.d" / "learning-control-center.caddy"
    caddy_site.parent.mkdir(parents=True)
    caddy_site.write_text("old site on 127.0.0.1:8000\n")
    caddy_main = root / "etc" / "caddy" / "Caddyfile"
    caddy_main.write_text(f"import {caddy_site.parent}/*.caddy\n")
    fake_caddy = root / "caddy"
    fake_caddy.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if test "$1" = validate && test "${FAKE_CADDY_FAIL_NEW:-0}" = 1 && '
        'grep -R -q \'127.0.0.1:8123\' "$(dirname -- "$3")"; then exit 1; fi\n'
    )
    fake_caddy.chmod(0o755)
    return {
        "root": root,
        "release": release,
        "current": current,
        "environment": environment,
        "caddy_site": caddy_site,
        "caddy_main": caddy_main,
        "fake_caddy": fake_caddy,
    }


def _run_port_transaction(
    fixture: dict[str, Path], mode: str, *, dry_run: bool = False
) -> subprocess.CompletedProcess[str]:
    script = r"""
set -euo pipefail
source "$1"
LCC_ENVIRONMENT_FILE="$2"
LCC_CURRENT_RELEASE="$3"
LCC_CADDY_SITE="$4"
LCC_CADDY_MAIN="$5"
LCC_CADDY_BINARY="$6"
LCC_TRANSACTION_DIRECTORY_PARENT="$7"
LCC_SERVICE_GROUP="$(id -gn)"
export FAKE_CADDY_SITE="$LCC_CADDY_SITE"
systemctl() { return 0; }
journalctl() { return 0; }
lcc_install_environment_file() {
    install -m 0640 "$1" "$2.next"
    mv -f "$2.next" "$2"
}
availability_calls=0
lcc_app_port_is_available() {
    availability_calls=$((availability_calls + 1))
    test "${TRANSACTION_MODE:-success}" != conflict && \
        { test "${TRANSACTION_MODE:-success}" != late-conflict || \
            test "$availability_calls" -eq 1; }
}
lcc_wait_for_internal_health() {
    if test "${TRANSACTION_MODE:-success}" = internal-failure; then
        test "$1" = 8000
    else
        return 0
    fi
}
public_health_calls=0
lcc_wait_for_health() {
    public_health_calls=$((public_health_calls + 1))
    if test "${TRANSACTION_MODE:-success}" = public-failure; then
        test "$public_health_calls" -gt 1
    else
        return 0
    fi
}
lcc_load_environment "$LCC_ENVIRONMENT_FILE"
lcc_validate_environment "$LCC_CURRENT_RELEASE"
lcc_apply_app_port_change 8123 "$8"
"""
    environment = {**os.environ, "TRANSACTION_MODE": mode}
    if mode == "caddy-failure":
        environment["FAKE_CADDY_FAIL_NEW"] = "1"
    return subprocess.run(
        [
            "bash",
            "-c",
            script,
            "app-port-transaction",
            str(COMMON),
            str(fixture["environment"]),
            str(fixture["current"]),
            str(fixture["caddy_site"]),
            str(fixture["caddy_main"]),
            str(fixture["fake_caddy"]),
            str(fixture["root"]),
            "1" if dry_run else "0",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_app_port_change_and_dry_run_are_transactional(tmp_path: Path) -> None:
    dry_fixture = _transaction_fixture(tmp_path / "dry")
    original_environment = dry_fixture["environment"].read_text()
    original_site = dry_fixture["caddy_site"].read_text()
    dry_run = _run_port_transaction(dry_fixture, "success", dry_run=True)
    assert dry_run.returncode == 0, dry_run.stderr
    assert dry_fixture["environment"].read_text() == original_environment
    assert dry_fixture["caddy_site"].read_text() == original_site

    success_fixture = _transaction_fixture(tmp_path / "success")
    changed = _run_port_transaction(success_fixture, "success")
    assert changed.returncode == 0, changed.stderr
    assert "LCC_APP_PORT=8123" in success_fixture["environment"].read_text()
    assert "reverse_proxy 127.0.0.1:8123" in success_fixture["caddy_site"].read_text()


@pytest.mark.parametrize(
    "mode",
    ["conflict", "late-conflict", "caddy-failure", "internal-failure", "public-failure"],
)
def test_failed_app_port_change_restores_environment_and_caddy(tmp_path: Path, mode: str) -> None:
    fixture = _transaction_fixture(tmp_path)
    original_environment = fixture["environment"].read_text()
    original_site = fixture["caddy_site"].read_text()
    result = _run_port_transaction(fixture, mode)
    assert result.returncode != 0
    assert fixture["environment"].read_text() == original_environment
    assert fixture["caddy_site"].read_text() == original_site
    assert not list(fixture["root"].glob("lcc-app-port.*"))


def test_legacy_rollback_modes_are_explicit(tmp_path: Path) -> None:
    legacy_release = tmp_path / "legacy"
    legacy_release.mkdir()
    legacy_environment = tmp_path / "legacy.env"
    legacy_environment.write_text("LCC_ENVIRONMENT=production\n")
    legacy = _run_common(
        'lcc_load_environment "$1"; lcc_app_port_rollback_mode "$2"',
        str(legacy_environment),
        str(legacy_release),
    )
    assert legacy.returncode == 0
    assert legacy.stdout.strip() == "legacy-default"

    explicit_default = tmp_path / "explicit-default.env"
    explicit_default.write_text("LCC_ENVIRONMENT=production\nLCC_APP_PORT=8000\n")
    remove_key = _run_common(
        'lcc_load_environment "$1"; lcc_app_port_rollback_mode "$2"',
        str(explicit_default),
        str(legacy_release),
    )
    assert remove_key.returncode == 0
    assert remove_key.stdout.strip() == "legacy-remove-key"

    custom = tmp_path / "custom.env"
    custom.write_text("LCC_ENVIRONMENT=production\nLCC_APP_PORT=8123\n")
    refused = _run_common(
        'lcc_load_environment "$1"; lcc_app_port_rollback_mode "$2"',
        str(custom),
        str(legacy_release),
    )
    assert refused.returncode != 0
    assert "cannot honor custom internal port 8123" in refused.stderr
