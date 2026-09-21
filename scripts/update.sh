#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIRECTORY="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/deploy-common.sh
source "$SCRIPT_DIRECTORY/deploy-common.sh"

readonly github_repository="$LCC_GITHUB_REPOSITORY"
readonly forgejo_repository="$LCC_FORGEJO_REPOSITORY"

usage() {
    cat <<'EOF'
Usage: sudo /opt/learning-control-center/update.sh [options]

Resolve the recorded repository's current validated main branch to one exact
commit SHA, then update through the canonical backup/migration engine.

Options:
  --yes      Confirm the displayed update non-interactively
  --dry-run  Resolve and verify current main without applying it
  -h, --help Show this help

Pinned release installation remains available through a release's exact,
version-bound install.sh. Rollbacks and database downgrades use the separate
database-aware rollback workflow.
EOF
}

assume_yes=0
dry_run=0
while test "$#" -gt 0; do
    case "$1" in
        --yes) assume_yes=1; shift ;;
        --dry-run) dry_run=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; lcc_die "Unknown update option: $1" ;;
    esac
done

test_mode="${LCC_UPDATE_TESTING:-0}"
install_root="${LCC_UPDATE_INSTALL_ROOT:-/}"
if test "$test_mode" = 1; then
    install_root="$(readlink -m -- "$install_root")"
    test "$install_root" != / && [[ "$install_root" = /tmp/* ]] || \
        lcc_die "Test update root must resolve beneath /tmp."
elif test "$(id -u)" -ne 0; then
    lcc_die "Production updates must run as root."
elif test "$install_root" != /; then
    lcc_die "Alternate update roots are available only to tests."
fi

current_release="$(lcc_prefixed_path "$install_root" "$LCC_CURRENT_RELEASE")"
test -L "$current_release" || lcc_die "No active Learning Control Center installation was found."
active_release="$(readlink -f "$current_release")"
test -d "$active_release" || lcc_die "The active release link is invalid."
test -f "$active_release/RELEASE_ID" && test -f "$active_release/SOURCE_REVISION" && \
    test -f "$active_release/RELEASE_MANIFEST" || \
    lcc_die "The active release lacks immutable source metadata."

manifest_value() {
    local key="$1"
    local manifest="$active_release/RELEASE_MANIFEST"
    local value count
    count="$(grep -c "^${key}=" "$manifest" || true)"
    test "$count" = 1 || lcc_die "Active release manifest has invalid $key metadata."
    value="$(sed -n "s/^${key}=//p" "$manifest")"
    test -n "$value" || lcc_die "Active release manifest has empty $key metadata."
    printf '%s\n' "$value"
}

current_release_id="$(tr -d '\r\n' < "$active_release/RELEASE_ID")"
current_channel="$(lcc_release_channel "$active_release")"
current_revision="$(lcc_release_source_revision "$active_release")"
current_repository="$(manifest_value source_repository)"
current_ref="$(manifest_value source_ref)"
current_origin="$(manifest_value source_origin)"
lcc_validate_release_channel "$current_channel"
lcc_validate_source_revision "$current_revision"
lcc_validate_https_url "$current_repository" "Installed source repository"
lcc_validate_https_url "$current_origin" "Installed source origin"
lcc_validate_source_metadata "$current_channel" "$current_repository" "$current_origin"
lcc_validate_release_manifest "$active_release" "$current_channel" "$current_release_id" \
    "$current_repository" "$current_ref" "$current_revision" "$current_origin"

case "$current_repository" in
    "$github_repository"|"$forgejo_repository") ;;
    *) lcc_die "Main updates require the recorded GitHub or Forgejo HTTPS repository." ;;
esac

if test "$test_mode" = 1 && test -n "${LCC_UPDATE_TEST_MAIN_SHA:-}"; then
    target_revision="$LCC_UPDATE_TEST_MAIN_SHA"
else
    remote_line="$(git ls-remote --exit-code --refs "$current_repository" refs/heads/main)" || \
        lcc_die "Unable to resolve the recorded public main branch."
    test "$(printf '%s\n' "$remote_line" | wc -l)" -eq 1 || \
        lcc_die "The recorded repository returned an ambiguous main reference."
    read -r target_revision remote_ref <<< "$remote_line"
    test "$remote_ref" = refs/heads/main || \
        lcc_die "The repository returned an unexpected main reference."
fi
lcc_validate_source_revision "$target_revision"

if test "$current_channel" = main && test "$current_revision" = "$target_revision"; then
    echo "Learning Control Center is already up to date."
    exit 0
fi

echo "Current channel: $current_channel"
echo "Current release: $current_release_id"
echo "Current source SHA: $current_revision"
echo "Target channel: main"
echo "Target source SHA: $target_revision"
if test "$current_channel" != main; then
    echo "Channel change: $current_channel -> main"
fi

if test "$dry_run" -eq 0 && test "$assume_yes" -eq 0; then
    exec 3<>/dev/tty || \
        lcc_die "Interactive confirmation requires a controlling terminal; use --yes for deliberate automation."
    printf 'Continue with this update? [y/N]: ' >&3
    IFS= read -r answer <&3 || lcc_die "Unable to read update confirmation."
    case "${answer,,}" in
        y|yes) ;;
        *) lcc_die "Update cancelled." ;;
    esac
fi

bootstrap="$active_release/scripts/bootstrap-ubuntu.sh"
test -x "$bootstrap" || lcc_die "The active release lacks the canonical bootstrap updater."
bootstrap_command=(
    "$bootstrap" --channel main --commit "$target_revision"
    --repository-url "$current_repository" --non-interactive
)
if test "$current_channel" != main; then
    bootstrap_command+=(--confirm-channel-change)
fi
if test "$dry_run" -eq 1; then
    echo "DRY-RUN: verifying current main at $target_revision from $current_repository"
    bootstrap_command+=(--dry-run)
fi

handoff_log="${LCC_UPDATE_TEST_HANDOFF_LOG:-}"
if test "$test_mode" = 1 && test -n "$handoff_log"; then
    : > "$handoff_log"
    printf '%s\n' "${bootstrap_command[@]}" >> "$handoff_log"
else
    "${bootstrap_command[@]}"
fi
