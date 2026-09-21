#!/usr/bin/env bash

# Shared production deployment helpers. This file is sourced by the Ubuntu
# installer, updater, and administrator wrapper; it is not an entry point.

# Constants are consumed by scripts that source this library.
# shellcheck disable=SC2034

LCC_SERVICE_NAME="learning-control-center.service"
LCC_BACKUP_SERVICE_NAME="learning-control-center-backup.service"
LCC_BACKUP_TIMER_NAME="learning-control-center-backup.timer"
LCC_SERVICE_USER="lcc"
LCC_SERVICE_GROUP="lcc"
LCC_APPLICATION_ROOT="/opt/learning-control-center"
LCC_CURRENT_RELEASE="/opt/learning-control-center/current"
LCC_ENVIRONMENT_FILE="/etc/learning-control-center.env"
LCC_DATA_DIRECTORY="/var/lib/learning-control-center"
LCC_DATABASE_FILE="/var/lib/learning-control-center/lcc.sqlite3"
LCC_BACKUP_DIRECTORY_DEFAULT="/var/backups/learning-control-center"
LCC_CADDY_SITE="/etc/caddy/Caddyfile.d/learning-control-center.caddy"
LCC_CADDY_IMPORT="import /etc/caddy/Caddyfile.d/*.caddy"
LCC_CADDY_BINARY="/usr/bin/caddy"

lcc_die() {
    echo "ERROR: $*" >&2
    exit 1
}

lcc_note() {
    echo "==> $*" >&2
}

lcc_acquire_deployment_lock() {
    local lock_file="${1:-/run/lock/learning-control-center-deployment.lock}"
    exec 9>"$lock_file"
    flock -n 9 || lcc_die "Another LCC deployment or administrator operation is running."
}

lcc_validate_release_id() {
    local release_id="$1"
    [[ "$release_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || \
        lcc_die "Release ID must contain only letters, numbers, dots, underscores, and hyphens."
    test "$release_id" != "current" || lcc_die "Release ID 'current' is reserved."
}

lcc_validate_release_channel() {
    case "$1" in
        stable|main) ;;
        *) lcc_die "Release channel must be stable or main." ;;
    esac
}

lcc_validate_source_revision() {
    [[ "$1" =~ ^[0-9a-f]{40}$ ]] || \
        lcc_die "Source revision must be one lowercase 40-character Git commit SHA."
}

lcc_validate_public_hostname() {
    local hostname="$1"
    python3 - "$hostname" <<'PY'
import re
import sys

hostname = sys.argv[1]
labels = hostname.split(".")
if (
    len(hostname) > 253
    or len(labels) < 2
    or any(
        not label
        or len(label) > 63
        or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) is None
        for label in labels
    )
):
    raise SystemExit(
        "Hostname must be a lowercase DNS name without a scheme, port, path, or whitespace"
    )
PY
}

lcc_validate_https_url() {
    local value="$1"
    local label="${2:-URL}"
    python3 - "$value" "$label" <<'PY'
import sys
from urllib.parse import urlparse

value, label = sys.argv[1:]
parsed = urlparse(value)
if (
    value != value.strip()
    or any(ord(character) < 32 or ord(character) == 127 for character in value)
    or parsed.scheme != "https"
    or not parsed.hostname
    or parsed.username is not None
    or parsed.password is not None
    or parsed.query
    or parsed.fragment
):
    raise SystemExit(f"{label} must be a public HTTPS URL without credentials, query, or fragment")
PY
}

lcc_validate_environment_file_security() {
    local path="$1"
    local allow_installed="${2:-0}"
    local expected_uid="${3:-0}"
    local expected_group="$LCC_SERVICE_GROUP"
    python3 - "$path" "$allow_installed" "$expected_uid" "$expected_group" <<'PY'
import grp
import os
import stat
import sys
from pathlib import Path

path = Path(sys.argv[1])
allow_installed = sys.argv[2] == "1"
expected_uid = int(sys.argv[3])
expected_group_name = sys.argv[4]
if path.is_symlink():
    raise SystemExit("Environment file must not be a symbolic link")
absolute_path = Path(os.path.abspath(path))
resolved_path = path.resolve(strict=True)
if resolved_path != absolute_path:
    raise SystemExit("Environment file path must not contain symbolic links")
details = path.stat()
if not stat.S_ISREG(details.st_mode):
    raise SystemExit("Environment file must be a regular file")
if details.st_uid != expected_uid:
    raise SystemExit("Environment file must be owned by the invoking trusted administrator")
mode = stat.S_IMODE(details.st_mode)
if allow_installed:
    if expected_uid != 0:
        expected_gid = details.st_gid
    else:
        try:
            expected_gid = grp.getgrnam(expected_group_name).gr_gid
        except KeyError:
            raise SystemExit("Installed environment group does not exist")
    if details.st_gid != expected_gid or mode != 0o640:
        raise SystemExit("Installed environment file must be root:lcc 0640")
elif mode & 0o077:
    raise SystemExit("External environment file must not grant group or other permissions")
for parent in resolved_path.parents:
    parent_details = parent.stat()
    parent_mode = stat.S_IMODE(parent_details.st_mode)
    trusted_owner = parent_details.st_uid in {0, expected_uid}
    if expected_uid != 0 and not parent_mode & 0o022:
        # Isolated test roots may run inside a container whose immutable root
        # directories are mapped to a namespace owner other than UID 0.
        trusted_owner = True
    sticky_root_directory = (
        expected_uid != 0 and bool(parent_mode & stat.S_ISVTX)
    )
    if sticky_root_directory:
        trusted_owner = True
    if not trusted_owner or (
        parent_mode & 0o022 and not sticky_root_directory
    ):
        raise SystemExit(
            "Environment file parent directories must not be replaceable by untrusted users"
        )
PY
}

lcc_release_channel() {
    local release_root="$1"
    if test -f "$release_root/RELEASE_CHANNEL"; then
        tr -d '\r\n' < "$release_root/RELEASE_CHANNEL"
    else
        printf 'stable\n'
    fi
}

lcc_release_source_revision() {
    local release_root="$1"
    test -f "$release_root/SOURCE_REVISION" || lcc_die "Release source revision is missing."
    local revision
    revision="$(tr -d '\r\n' < "$release_root/SOURCE_REVISION")"
    if [[ ! "$revision" =~ ^[0-9a-f]{40}$ ]]; then
        if test ! -f "$release_root/RELEASE_CHANNEL" && \
            [[ "$revision" =~ ^artifact-sha256-[0-9a-f]{64}$ ]]; then
            lcc_note "Legacy release uses an artifact-content revision; new releases require Git SHAs."
        else
            lcc_die "Release source revision is not a valid immutable identity."
        fi
    fi
    printf '%s\n' "$revision"
}

lcc_validate_release_manifest() {
    local release_root="$1"
    local release_channel="$2"
    local release_id="$3"
    local source_repository="$4"
    local source_ref="$5"
    local source_revision="$6"
    local source_origin="$7"
    local manifest="$release_root/RELEASE_MANIFEST"
    test -f "$manifest" || lcc_die "Release manifest is missing."
    if ! { grep -Fqx 'metadata_version=1' "$manifest" && \
        grep -Fqx "channel=$release_channel" "$manifest" && \
        grep -Fqx "release_id=$release_id" "$manifest" && \
        grep -Fqx "source_repository=$source_repository" "$manifest" && \
        grep -Fqx "source_ref=$source_ref" "$manifest" && \
        grep -Fqx "source_revision=$source_revision" "$manifest" && \
        grep -Fqx "source_origin=$source_origin" "$manifest"; }; then
        lcc_die "Release manifest does not match the selected immutable source identity."
    fi
}

lcc_validate_update_transition() {
    local old_channel="$1"
    local old_release_id="$2"
    local old_source_revision="$3"
    local new_channel="$4"
    local new_release_id="$5"
    local new_source_revision="$6"
    local confirm_channel_change="${7:-0}"
    if test "$new_release_id" = "$old_release_id" || \
        test "$new_source_revision" = "$old_source_revision"; then
        lcc_die "The requested immutable source identity is already active."
    fi
    if test "$new_channel" != "$old_channel" && test "$confirm_channel_change" -ne 1; then
        lcc_die "Changing from $old_channel to $new_channel requires --confirm-channel-change."
    fi
    if test "$new_channel" = stable && test "$old_channel" = stable; then
        /usr/bin/python3 - "$old_release_id" "$new_release_id" <<'PY' || \
            lcc_die "Stable apply requires a newer vMAJOR.MINOR.PATCH release; use rollback for older releases."
import re
import sys

pattern = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
versions = []
for value in sys.argv[1:]:
    match = pattern.fullmatch(value)
    if match is None:
        raise SystemExit(1)
    versions.append(tuple(int(part) for part in match.groups()))
if versions[1] <= versions[0]:
    raise SystemExit(1)
PY
    fi
}

lcc_revision_is_ancestor() {
    local release_directory="$1"
    local ancestor="$2"
    local descendant="$3"
    "$release_directory/.venv/bin/python" - "$release_directory" "$ancestor" "$descendant" <<'PY'
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

root = Path(sys.argv[1])
ancestor, descendant = sys.argv[2:]
config = Config(str(root / "alembic.ini"))
config.set_main_option("script_location", str(root / "backend" / "alembic"))
scripts = ScriptDirectory.from_config(config)
try:
    tuple(scripts.iterate_revisions(descendant, ancestor))
except Exception:
    raise SystemExit(1)
raise SystemExit(0)
PY
}

lcc_migration_relation() {
    local candidate_release="$1"
    local current_release="$2"
    local current_revision="$3"
    local candidate_revision="$4"
    if test "$current_revision" = "$candidate_revision"; then
        printf 'same\n'
    elif lcc_revision_is_ancestor "$candidate_release" "$current_revision" "$candidate_revision"; then
        printf 'forward\n'
    elif lcc_revision_is_ancestor "$current_release" "$candidate_revision" "$current_revision"; then
        printf 'backward\n'
    else
        printf 'divergent\n'
    fi
}

lcc_prefixed_path() {
    local root="$1"
    local path="$2"
    if test "$root" = "/"; then
        printf '%s\n' "$path"
    else
        printf '%s%s\n' "${root%/}" "$path"
    fi
}

lcc_load_environment() {
    local environment_file="$1"
    local line key value line_number=0
    unset LCC_ENVIRONMENT LCC_DATABASE_URL LCC_BACKUP_DIRECTORY LCC_RELEASE_ROOT \
        LCC_PUBLIC_ORIGIN LCC_ALLOWED_ORIGINS LCC_ALLOWED_HOSTS LCC_TRUSTED_PROXY_CIDRS \
        LCC_BOOTSTRAP_TOKEN LCC_SECURITY_SECRET LCC_APP_TIMEZONE LCC_SESSION_COOKIE_NAME \
        LCC_FIXTURE_CLOCK_AT LCC_FIXTURE_CLOCK_STEP_MS \
        LCC_SESSION_IDLE_TIMEOUT_MS LCC_SESSION_ABSOLUTE_TIMEOUT_MS LCC_MAX_IMPORT_BYTES \
        LCC_LOGIN_RATE_LIMIT_ATTEMPTS LCC_LOGIN_RATE_LIMIT_WINDOW_MS \
        LCC_IMPORT_RATE_LIMIT_ATTEMPTS LCC_IMPORT_RATE_LIMIT_WINDOW_MS \
        LCC_BACKUP_RETENTION_COUNT LCC_BACKUP_PURPOSE
    declare -gA LCC_DEPLOY_ENV_SEEN=()
    declare -ga LCC_DEPLOY_ENV_ARGS=()
    test -r "$environment_file" || lcc_die "Cannot read environment file: $environment_file"
    while IFS= read -r line || test -n "$line"; do
        line_number=$((line_number + 1))
        line="${line%$'\r'}"
        [[ "$line" =~ ^[[:space:]]*$ ]] && continue
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || \
            lcc_die "Invalid environment-file syntax at line $line_number."
        key="${BASH_REMATCH[1]}"
        value="${BASH_REMATCH[2]}"
        case "$key" in
            LCC_ENVIRONMENT|LCC_DATABASE_URL|LCC_BACKUP_DIRECTORY|LCC_RELEASE_ROOT|\
            LCC_PUBLIC_ORIGIN|LCC_ALLOWED_ORIGINS|LCC_ALLOWED_HOSTS|\
            LCC_TRUSTED_PROXY_CIDRS|LCC_BOOTSTRAP_TOKEN|LCC_SECURITY_SECRET|\
            LCC_APP_TIMEZONE|LCC_SESSION_COOKIE_NAME|LCC_SESSION_IDLE_TIMEOUT_MS|\
            LCC_SESSION_ABSOLUTE_TIMEOUT_MS|LCC_MAX_IMPORT_BYTES|\
            LCC_LOGIN_RATE_LIMIT_ATTEMPTS|LCC_LOGIN_RATE_LIMIT_WINDOW_MS|\
            LCC_IMPORT_RATE_LIMIT_ATTEMPTS|LCC_IMPORT_RATE_LIMIT_WINDOW_MS|\
            LCC_BACKUP_RETENTION_COUNT) ;;
            *) lcc_die "Unsupported production environment key at line $line_number: $key" ;;
        esac
        test -z "${LCC_DEPLOY_ENV_SEEN[$key]:-}" || lcc_die "Duplicate environment key: $key"
        LCC_DEPLOY_ENV_SEEN[$key]=1
        if [[ "$value" == \'*\' ]]; then
            [[ "$value" =~ ^\'[^\']*\'$ ]] || lcc_die "Invalid single-quoted value for $key"
            value="${value:1:${#value}-2}"
        elif [[ "$value" == \"*\" ]]; then
            [[ "$value" =~ ^\"[^\"\\]*\"$ ]] || lcc_die "Invalid double-quoted value for $key"
            value="${value:1:${#value}-2}"
        elif [[ "$value" =~ [[:space:]\`\$] ]]; then
            lcc_die "Unquoted whitespace or shell metacharacter in $key"
        fi
        printf -v "$key" '%s' "$value"
        LCC_DEPLOY_ENV_ARGS+=("$key=$value")
    done < "$environment_file"
}

lcc_run_with_deploy_environment() {
    # Export parsed values inside a subshell so secret values are inherited through the
    # process environment rather than exposed in a child process's argument vector.
    (
        local assignment key
        for assignment in "${LCC_DEPLOY_ENV_ARGS[@]}"; do
            key="${assignment%%=*}"
            export "${key?}"
        done
        "$@"
    )
}

lcc_select_single_new_backup() {
    local backup_directory="$1"
    local before_inventory="$2"
    local after_inventory="$3"
    local -a created_backups=()
    mapfile -t created_backups < <(comm -13 "$before_inventory" "$after_inventory")
    if test "${#created_backups[@]}" -ne 1; then
        lcc_note "Legacy backup command did not create exactly one new backup."
        return 1
    fi
    printf '%s/%s\n' "$backup_directory" "${created_backups[0]}"
}

lcc_validate_environment() {
    local expected_release_root="${1:-$LCC_CURRENT_RELEASE}"
    test "${LCC_ENVIRONMENT:-}" = "production" || lcc_die "LCC_ENVIRONMENT must be production."
    case "${LCC_DATABASE_URL:-}" in
        sqlite:////*) ;;
        *) lcc_die "LCC_DATABASE_URL must be an absolute SQLite URL." ;;
    esac
    [[ "${LCC_BACKUP_DIRECTORY:-}" = /* ]] || lcc_die "LCC_BACKUP_DIRECTORY must be absolute."
    test "${LCC_RELEASE_ROOT:-}" = "$expected_release_root" || \
        lcc_die "LCC_RELEASE_ROOT must be $expected_release_root."
    test -n "${LCC_SECURITY_SECRET:-}" || lcc_die "LCC_SECURITY_SECRET is required."
    test -n "${LCC_PUBLIC_ORIGIN:-}" || lcc_die "LCC_PUBLIC_ORIGIN is required."
    test -n "${LCC_ALLOWED_ORIGINS:-}" || lcc_die "LCC_ALLOWED_ORIGINS is required."
    test -n "${LCC_ALLOWED_HOSTS:-}" || lcc_die "LCC_ALLOWED_HOSTS is required."
    test -n "${LCC_TRUSTED_PROXY_CIDRS:-}" || lcc_die "LCC_TRUSTED_PROXY_CIDRS is required."
    test -n "${LCC_APP_TIMEZONE:-}" || lcc_die "LCC_APP_TIMEZONE is required."
    local assignment
    for assignment in "${LCC_DEPLOY_ENV_ARGS[@]}"; do
        [[ "$assignment" != *CHANGE_ME* ]] || \
            lcc_die "Replace every CHANGE_ME placeholder before installation."
    done
    lcc_run_with_deploy_environment /usr/bin/python3 - "$expected_release_root" <<'PY'
import json
import os
import sys
from ipaddress import ip_network
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

release_root = Path(sys.argv[1]).resolve()
origin = urlparse(os.environ["LCC_PUBLIC_ORIGIN"])
if origin.scheme != "https" or not origin.hostname or origin.path not in ("", "/"):
    raise SystemExit("LCC_PUBLIC_ORIGIN must be one HTTPS origin without a path")
origins = json.loads(os.environ["LCC_ALLOWED_ORIGINS"])
hosts = json.loads(os.environ["LCC_ALLOWED_HOSTS"])
proxies = json.loads(os.environ["LCC_TRUSTED_PROXY_CIDRS"])
if origins != [os.environ["LCC_PUBLIC_ORIGIN"]]:
    raise SystemExit("LCC_ALLOWED_ORIGINS must contain only LCC_PUBLIC_ORIGIN")
if hosts != [origin.hostname]:
    raise SystemExit("LCC_ALLOWED_HOSTS must contain only the public hostname")
if not proxies:
    raise SystemExit("At least one trusted loopback proxy CIDR is required")
for value in proxies:
    network = ip_network(value, strict=False)
    if not network.is_loopback:
        raise SystemExit("Production trusted proxies must be loopback networks")
ZoneInfo(os.environ["LCC_APP_TIMEZONE"])
secret = os.environ["LCC_SECURITY_SECRET"]
markers = ("change", "replace", "example", "password", "secret")
if len(secret) < 32 or len(set(secret)) < 12 or any(item in secret.lower() for item in markers):
    raise SystemExit("LCC_SECURITY_SECRET must be a strong non-placeholder value")
bootstrap = os.environ.get("LCC_BOOTSTRAP_TOKEN")
if bootstrap and (
    len(bootstrap) < 32
    or len(set(bootstrap)) < 12
    or any(item in bootstrap.lower() for item in markers)
):
    raise SystemExit("LCC_BOOTSTRAP_TOKEN must be a strong non-placeholder value")
database = Path(os.environ["LCC_DATABASE_URL"].removeprefix("sqlite:///")).resolve()
backup = Path(os.environ["LCC_BACKUP_DIRECTORY"]).resolve()
if database.parent == backup or database.parent in backup.parents or backup in database.parent.parents:
    raise SystemExit("Database and backup paths must not overlap")
if release_root == database or release_root in database.parents:
    raise SystemExit("Database must be outside the release tree")
if release_root == backup or release_root in backup.parents:
    raise SystemExit("Backups must be outside the release tree")
PY
}

lcc_public_hostname() {
    lcc_run_with_deploy_environment /usr/bin/python3 - <<'PY'
import os
from urllib.parse import urlparse
print(urlparse(os.environ["LCC_PUBLIC_ORIGIN"]).hostname)
PY
}

lcc_copy_release_source() {
    local source="$1"
    local destination="$2"
    local mode="${3:-tracked}"
    local git_root=""
    if test "$mode" = "tracked"; then
        git_root="$(git -C "$source" rev-parse --show-toplevel 2>/dev/null || true)"
    fi
    if test -n "$git_root" && test "$(readlink -f "$git_root")" = "$(readlink -f "$source")"; then
        git -C "$source" archive --format=tar HEAD | tar -xf - -C "$destination"
        return
    fi
    rsync -a --delete \
        --include=/.env.example --exclude='.env*' --exclude=.git \
        --exclude=.venv --exclude=venv --exclude=node_modules \
        --exclude=data --exclude=backups --exclude=tmp --exclude=memory-bank \
        --exclude=__pycache__ --exclude='*.py[cod]' --exclude='*.egg-info' \
        --exclude=.mypy_cache --exclude=.pytest_cache --exclude=.ruff_cache \
        --exclude=.coverage --exclude=htmlcov --exclude=coverage --exclude=test-results \
        --exclude=.cache --exclude=.abacusai --exclude=.idea --exclude=.vscode \
        --exclude=.npmrc --exclude=.pypirc --exclude=.netrc --exclude=.git-credentials \
        --exclude=pip.conf --exclude=.aws --exclude=.ssh --exclude=.docker \
        --exclude='*.pem' --exclude='*.key' --exclude='*.db*' --exclude='*.sqlite*' \
        --exclude='*.log' --exclude='*.tmp' --exclude='*.tsbuildinfo' \
        "$source/" "$destination/"
}

lcc_verify_runtime_prerequisites() {
    local command_name
    for command_name in python3 sqlite3 rsync tar curl systemctl runuser flock dpkg-query; do
        command -v "$command_name" >/dev/null || lcc_die "Missing production prerequisite: $command_name"
    done
    python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' || \
        lcc_die "Python 3.12 or newer is required."
    test -x "$LCC_CADDY_BINARY" || lcc_die "The Ubuntu package-owned $LCC_CADDY_BINARY is required."
    dpkg-query -S "$LCC_CADDY_BINARY" 2>/dev/null | \
        grep -Eq '^caddy(:[^:]+)?: /usr/bin/caddy$' || \
        lcc_die "$LCC_CADDY_BINARY must be owned by the Ubuntu caddy package."
    "$LCC_CADDY_BINARY" version 2>/dev/null | grep -Eq '^v?2\.' || lcc_die "Caddy 2 is required."
    test -f /lib/systemd/system/caddy.service || test -f /usr/lib/systemd/system/caddy.service || \
        lcc_die "The packaged Caddy systemd service is required."
}

lcc_restore_caddy_configuration() {
    local backup_directory="$1"
    local site_path="$2"
    local main_path="$3"
    if test -f "$backup_directory/site"; then
        cp -a -- "$backup_directory/site" "$site_path"
    else
        rm -f -- "$site_path"
    fi
    if test -f "$backup_directory/main"; then
        cp -a -- "$backup_directory/main" "$main_path"
    else
        rm -f -- "$main_path"
    fi
}

lcc_format_validate_or_restore_caddy() {
    local site_path="$1"
    local main_path="$2"
    local backup_directory="$3"
    local caddy_binary="${4:-$LCC_CADDY_BINARY}"
    if "$caddy_binary" fmt --overwrite "$site_path" && \
        "$caddy_binary" validate --config "$main_path" --adapter caddyfile; then
        return 0
    fi
    lcc_restore_caddy_configuration "$backup_directory" "$site_path" "$main_path"
    lcc_die "Caddy configuration conflicts with the LCC site; the previous configuration was restored."
}

lcc_verify_frontend_artifact() {
    local release_root="$1"
    test -x "$release_root/scripts/frontend-artifact.py" || \
        lcc_die "Release lacks the frontend artifact verifier."
    "$release_root/scripts/frontend-artifact.py" verify --root "$release_root" >&2 || \
        lcc_die "Release frontend artifact is missing, stale, or modified."
}

lcc_require_inactive_service() {
    local state
    state="$(systemctl is-active "$LCC_SERVICE_NAME" 2>/dev/null || true)"
    case "$state" in
        inactive|failed|unknown) ;;
        *) lcc_die "$LCC_SERVICE_NAME must be stopped for this operation (current state: $state)." ;;
    esac
}

lcc_run_as_service_user() {
    local release_root="$1"
    shift
    # The service-user shell expands the quoted script body.
    # shellcheck disable=SC2016
    runuser -u "$LCC_SERVICE_USER" -- /usr/bin/env -i \
        HOME="$LCC_DATA_DIRECTORY" \
        PATH="$release_root/.venv/bin:/usr/bin:/bin" \
        PYTHONPATH="$release_root/backend" \
        /bin/bash --noprofile --norc -c '
            set -euo pipefail
            common_path="$1"
            environment_path="$2"
            shift 2
            # shellcheck source=scripts/deploy-common.sh
            source "$common_path"
            lcc_load_environment "$environment_path"
            for assignment in "${LCC_DEPLOY_ENV_ARGS[@]}"; do
                export "$assignment"
            done
            exec "$@"
        ' lcc-environment-exec \
        "$release_root/scripts/deploy-common.sh" "$LCC_ENVIRONMENT_FILE" "$@"
}

lcc_wait_for_health() {
    local attempts="${1:-40}"
    local delay="${2:-0.5}"
    local attempt
    for ((attempt = 1; attempt <= attempts; attempt++)); do
        if curl --fail --silent --show-error --max-time 5 \
            "$LCC_PUBLIC_ORIGIN/api/v1/health" >/dev/null 2>&1; then
            return 0
        fi
        sleep "$delay"
    done
    return 1
}
