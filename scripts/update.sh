#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIRECTORY="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/deploy-common.sh
source "$SCRIPT_DIRECTORY/deploy-common.sh"

readonly github_repository="$LCC_GITHUB_REPOSITORY"
readonly github_asset_origin="$LCC_GITHUB_ASSET_ORIGIN"
readonly github_releases_api="https://api.github.com/repos/Learning-Control-Center/Learning-Control-Center/releases?per_page=100"
readonly forgejo_repository="$LCC_FORGEJO_REPOSITORY"
readonly forgejo_asset_origin="$LCC_FORGEJO_ASSET_ORIGIN"
readonly forgejo_releases_api="https://forgejo.waqsea.com/api/v1/repos/Learning-Control-Center/Learning-Control-Center/releases?limit=100"

usage() {
    cat <<'EOF'
Usage: sudo /opt/learning-control-center/update.sh [options]

Update the installed channel without changing it by default:
  stable -> newest final stable release
  main   -> current validated main, pinned to its exact resolved SHA

Options:
  --channel stable|main  Explicitly change update channel
  --yes                  Confirm the displayed update non-interactively
  --dry-run              Resolve and verify the target without applying it
  -h, --help             Show this help

Channel changes are never implicit. Rollbacks and database downgrades use the
separate database-aware rollback workflow.
EOF
}

requested_channel=""
channel_was_set=0
assume_yes=0
dry_run=0
while test "$#" -gt 0; do
    case "$1" in
        --channel)
            requested_channel="${2:?Missing --channel value}"
            channel_was_set=1
            shift 2
            ;;
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

target_channel="${requested_channel:-$current_channel}"
lcc_validate_release_channel "$target_channel"
if test "$target_channel" != "$current_channel" && test "$channel_was_set" -ne 1; then
    lcc_die "Channel changes must be requested explicitly with --channel."
fi

confirm_target() {
    local prompt="$1"
    test "$dry_run" -eq 0 || return 0
    test "$assume_yes" -eq 0 || return 0
    exec 3<>/dev/tty || lcc_die "Interactive confirmation requires a controlling terminal; use --yes for deliberate automation."
    printf '%s [y/N]: ' "$prompt" >&3
    IFS= read -r answer <&3 || lcc_die "Unable to read update confirmation."
    case "${answer,,}" in
        y|yes) ;;
        *) lcc_die "Update cancelled." ;;
    esac
}

record_or_run() {
    local handoff_log="${LCC_UPDATE_TEST_HANDOFF_LOG:-}"
    if test "$test_mode" = 1 && test -n "$handoff_log"; then
        : > "$handoff_log"
        printf '%s\n' "$@" >> "$handoff_log"
        return 0
    fi
    "$@"
}

temporary_directory="$(mktemp -d "${TMPDIR:-/tmp}/lcc-update.XXXXXXXX")"
trap 'rm -rf -- "$temporary_directory"' EXIT

if test "$target_channel" = stable; then
    case "$current_repository" in
        "$github_repository")
            stable_origin="$github_asset_origin"
            releases_api="$github_releases_api"
            ;;
        "$forgejo_repository")
            stable_origin="$forgejo_asset_origin"
            releases_api="$forgejo_releases_api"
            ;;
        *)
            lcc_die "Automatic stable discovery is supported only for the recorded GitHub or Forgejo repository; use an exact pinned install.sh for this source."
            ;;
    esac

    release_json="$temporary_directory/latest.json"
    if test "$test_mode" = 1 && test -n "${LCC_UPDATE_TEST_LATEST_JSON:-}"; then
        cp -- "$LCC_UPDATE_TEST_LATEST_JSON" "$release_json"
    else
        curl --fail --location --silent --show-error --retry 3 --max-filesize 1048576 \
            --proto '=https' --proto-redir '=https' --tlsv1.2 \
            --header 'Accept: application/json' \
            --header 'User-Agent: Learning-Control-Center-Updater/1.0.1' \
            --output "$release_json" "$releases_api"
    fi

    mapfile -t release_details < <(/usr/bin/python3 - "$release_json" <<'PY'
import json
import re
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not isinstance(payload, list):
    raise SystemExit("release API returned invalid data")
pattern = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
candidates = []
for item in payload:
    if not isinstance(item, dict) or item.get("draft") is not False or item.get("prerelease") is not False:
        continue
    tag = item.get("tag_name")
    match = pattern.fullmatch(tag) if isinstance(tag, str) else None
    if match is not None:
        candidates.append((tuple(int(part) for part in match.groups()), tag, item))
if not candidates:
    raise SystemExit("release API contains no final semantic release")
_version, tag, selected = max(candidates, key=lambda candidate: candidate[0])
assets = selected.get("assets")
if not isinstance(assets, list):
    raise SystemExit("selected final release returned invalid assets")
urls = [item.get("browser_download_url") for item in assets if item.get("name") == "install.sh"]
if len(urls) > 1 or (urls and not isinstance(urls[0], str)):
    raise SystemExit("selected final release returned ambiguous install.sh assets")
print(tag)
print(urls[0] if urls else "")
PY
    )
    test "${#release_details[@]}" -eq 2 || lcc_die "Latest stable release discovery returned invalid data."
    target_release_id="${release_details[0]}"
    installer_url="${release_details[1]}"
    lcc_is_final_stable_release_id "$target_release_id" || \
        lcc_die "Automatic stable updates never select prereleases."

    if test "$current_channel" = stable; then
        comparison="$(lcc_compare_stable_release_ids "$current_release_id" "$target_release_id")"
        if test "$comparison" -eq 0; then
            echo "Learning Control Center is already up to date."
            exit 0
        fi
        if test "$comparison" -gt 0; then
            lcc_die "Installed stable release $current_release_id is newer than discovered $target_release_id; use the rollback workflow for downgrades."
        fi
    fi

    expected_installer_url="${stable_origin%/}/$target_release_id/install.sh"
    test "$installer_url" = "$expected_installer_url" || \
        lcc_die "Latest stable release does not publish install.sh at the expected recorded source."
    installer="$temporary_directory/install.sh"
    if test "$test_mode" = 1 && test -n "${LCC_UPDATE_TEST_INSTALLER:-}"; then
        cp -- "$LCC_UPDATE_TEST_INSTALLER" "$installer"
    else
        curl --fail --location --silent --show-error --retry 3 --max-filesize 1048576 \
            --proto '=https' --proto-redir '=https' --tlsv1.2 \
            --output "$installer" "$installer_url"
    fi
    chmod 0700 "$installer"
    grep -Fqx "readonly embedded_stable_ref=\"$target_release_id\"" "$installer" || \
        lcc_die "Downloaded install.sh is not bound to the discovered stable release."
    grep -Eq '^readonly embedded_archive_sha256="[0-9a-f]{64}"$' "$installer" || \
        lcc_die "Downloaded install.sh lacks a valid embedded archive digest."
    grep -Fqx 'readonly stable_only_launcher="1"' "$installer" || \
        lcc_die "Downloaded install.sh is not a stable-only release launcher."

    echo "Current channel: $current_channel"
    echo "Current release: $current_release_id"
    echo "Target channel: stable"
    echo "Target release: $target_release_id"
    if test "$current_channel" != stable; then
        echo "Channel change: $current_channel -> stable"
    fi
    confirm_target "Continue with this update?"
    launcher_command=("$installer" --asset-base-url "$stable_origin" --non-interactive)
    if test "$current_channel" != stable; then
        launcher_command+=(--confirm-channel-change)
    fi
    if test "$dry_run" -eq 1; then
        echo "DRY-RUN: verifying stable target $target_release_id from $stable_origin"
        launcher_command+=(--dry-run)
    fi
    record_or_run "${launcher_command[@]}"
else
    main_repository="$current_repository"
    case "$main_repository" in
        "$github_repository"|"$forgejo_repository") ;;
        *) lcc_die "Automatic main updates require the recorded GitHub or Forgejo HTTPS repository." ;;
    esac
    if test "$test_mode" = 1 && test -n "${LCC_UPDATE_TEST_MAIN_SHA:-}"; then
        target_revision="$LCC_UPDATE_TEST_MAIN_SHA"
    else
        remote_line="$(git ls-remote --exit-code --refs "$main_repository" refs/heads/main)" || \
            lcc_die "Unable to resolve the recorded public main branch."
        test "$(printf '%s\n' "$remote_line" | wc -l)" -eq 1 || \
            lcc_die "The recorded repository returned an ambiguous main reference."
        read -r target_revision remote_ref <<< "$remote_line"
        test "$remote_ref" = refs/heads/main || lcc_die "The repository returned an unexpected main reference."
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
    confirm_target "Continue with this update?"
    bootstrap="$active_release/scripts/bootstrap-ubuntu.sh"
    test -x "$bootstrap" || lcc_die "The active release lacks the canonical bootstrap updater."
    bootstrap_command=(
        "$bootstrap" --channel main --commit "$target_revision"
        --repository-url "$main_repository" --non-interactive
    )
    if test "$current_channel" != main; then
        bootstrap_command+=(--confirm-channel-change)
    fi
    if test "$dry_run" -eq 1; then
        echo "DRY-RUN: verifying current main at $target_revision from $main_repository"
        bootstrap_command+=(--dry-run)
    fi
    record_or_run "${bootstrap_command[@]}"
fi
