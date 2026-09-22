#!/usr/bin/env bash
set -euo pipefail
umask 077

script_directory="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/deploy-common.sh
source "$script_directory/deploy-common.sh"

usage() {
    cat <<'EOF'
Usage: sudo /opt/learning-control-center/update.sh [--yes|--dry-run]
       sudo /opt/learning-control-center/update.sh rollback --to RELEASE_ID

The normal update resolves public GitHub main once to a full SHA, stages its
immutable release, backs up before activation, and verifies health. Rollback
uses an already installed release; a schema crossing also requires its exact
matching --database-backup and --confirm-database-replacement.
EOF
}

if test "${1:-}" = rollback; then
    shift
    # shellcheck source=scripts/install/transition.sh
    source "$script_directory/install/transition.sh"
    lcc_transition_rollback "$@"
    exit $?
fi
for argument in "$@"; do
    case "$argument" in
        --yes|--dry-run) ;;
        -h|--help) usage; exit 0 ;;
        *) lcc_die "Unknown update option: $argument" ;;
    esac
done
exec "$script_directory/bootstrap.sh" --non-interactive "$@"
