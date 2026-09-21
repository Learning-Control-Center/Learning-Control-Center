#!/usr/bin/env bash
set -euo pipefail

readonly current_updater="/opt/learning-control-center/current/scripts/update.sh"
test -x "$current_updater" || {
    echo "ERROR: Active Learning Control Center updater is unavailable." >&2
    exit 1
}
exec "$current_updater" "$@"
