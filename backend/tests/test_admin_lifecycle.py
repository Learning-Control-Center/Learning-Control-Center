"""Offline administrator lifecycle behavior; real service-user checks run in Ubuntu VMs."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _offline_service_function() -> str:
    script = (ROOT / "scripts/lcc-admin").read_text()
    body = script.split("run_offline_with_service() (\n", 1)[1].split(
        "\n)\n\nfinalize_bootstrap_offline", 1
    )[0]
    return "run_offline_with_service() (\n" + body + "\n)\n"


@pytest.mark.parametrize(
    (
        "initial_state",
        "operation_result",
        "start_result",
        "health_result",
        "pause_result",
        "expected_status",
        "expected_state",
        "expected_actions",
    ),
    [
        (
            "active",
            0,
            0,
            0,
            0,
            0,
            "active",
            ["timer-paused", "stop", "operation", "start", "internal-health", "timer-resumed"],
        ),
        (
            "active",
            23,
            0,
            0,
            0,
            23,
            "active",
            ["timer-paused", "stop", "operation", "start", "internal-health", "timer-resumed"],
        ),
        (
            "active",
            0,
            1,
            0,
            0,
            1,
            "inactive",
            ["timer-paused", "stop", "operation", "start", "timer-resumed"],
        ),
        (
            "active",
            0,
            0,
            1,
            0,
            1,
            "active",
            ["timer-paused", "stop", "operation", "start", "internal-health", "timer-resumed"],
        ),
        (
            "active",
            0,
            0,
            0,
            1,
            1,
            "active",
            ["timer-paused", "timer-resumed"],
        ),
        (
            "inactive",
            0,
            0,
            0,
            0,
            0,
            "inactive",
            ["timer-paused", "stop", "operation", "timer-resumed"],
        ),
    ],
)
def test_recovery_restores_the_entry_service_state(
    tmp_path: Path,
    initial_state: str,
    operation_result: int,
    start_result: int,
    health_result: int,
    pause_result: int,
    expected_status: int,
    expected_state: str,
    expected_actions: list[str],
) -> None:
    state_file = tmp_path / "state"
    log_file = tmp_path / "actions"
    state_file.write_text(initial_state)
    harness = f"""
set -euo pipefail
{_offline_service_function()}
LCC_SERVICE_NAME=learning-control-center.service
gateway_mode=external
systemctl() {{
    case "$1" in
        is-active) cat "$STATE_FILE" ;;
        stop) echo stop >> "$LOG_FILE"; echo inactive > "$STATE_FILE" ;;
        start)
            echo start >> "$LOG_FILE"
            if test "$START_RESULT" -ne 0; then return "$START_RESULT"; fi
            echo active > "$STATE_FILE" ;;
    esac
}}
pause_backup_timer() {{ echo timer-paused >> "$LOG_FILE"; return "$PAUSE_RESULT"; }}
resume_backup_timer() {{ echo timer-resumed >> "$LOG_FILE"; }}
lcc_require_inactive_service() {{ test "$(cat "$STATE_FILE")" = inactive; }}
lcc_effective_app_port() {{ echo 8123; }}
lcc_wait_for_internal_health() {{ echo internal-health >> "$LOG_FILE"; return "$HEALTH_RESULT"; }}
lcc_wait_for_health() {{ echo public-health >> "$LOG_FILE"; }}
lcc_die() {{ echo "$*" >&2; exit 1; }}
operation() {{ echo operation >> "$LOG_FILE"; return "$OPERATION_RESULT"; }}
run_offline_with_service if-active 0 operation
"""
    result = subprocess.run(
        ["bash", "-c", harness],
        env={
            **os.environ,
            "STATE_FILE": str(state_file),
            "LOG_FILE": str(log_file),
            "OPERATION_RESULT": str(operation_result),
            "START_RESULT": str(start_result),
            "HEALTH_RESULT": str(health_result),
            "PAUSE_RESULT": str(pause_result),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == expected_status, result.stderr
    assert log_file.read_text().splitlines() == expected_actions
    assert state_file.read_text().strip() == expected_state


def test_service_user_entrypoint_uses_release_cwd(tmp_path: Path) -> None:
    release = tmp_path / "release"
    scripts = release / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/deploy-common.sh", scripts / "deploy-common.sh")
    environment = tmp_path / "installed.env"
    environment.write_text("LCC_ENVIRONMENT=production\n")
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    harness = """
set -euo pipefail
cd "$BLOCKED_CWD"
trap 'chmod 700 "$BLOCKED_CWD"' EXIT
chmod 000 "$BLOCKED_CWD"
source "$COMMON"
LCC_ENVIRONMENT_FILE="$INSTALLED_ENV"
runuser() { shift 3; "$@"; }
lcc_run_as_service_user "$RELEASE" /usr/bin/pwd -P
"""
    result = subprocess.run(
        ["bash", "-c", harness],
        env={
            **os.environ,
            "BLOCKED_CWD": str(blocked),
            "COMMON": str(ROOT / "scripts/deploy-common.sh"),
            "INSTALLED_ENV": str(environment),
            "RELEASE": str(release),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(release)
