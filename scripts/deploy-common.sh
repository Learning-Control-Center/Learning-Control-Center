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
    env -i PATH=/usr/bin:/bin "${LCC_DEPLOY_ENV_ARGS[@]}" \
        python3 - "$expected_release_root" <<'PY'
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
    env -i PATH=/usr/bin:/bin "${LCC_DEPLOY_ENV_ARGS[@]}" python3 - <<'PY'
import os
from urllib.parse import urlparse
print(urlparse(os.environ["LCC_PUBLIC_ORIGIN"]).hostname)
PY
}

lcc_source_revision() {
    local source="$1"
    local git_root=""
    git_root="$(git -C "$source" rev-parse --show-toplevel 2>/dev/null || true)"
    if test -n "$git_root" && test "$(readlink -f "$git_root")" = "$(readlink -f "$source")"; then
        git -C "$source" rev-parse HEAD
    else
        local digest temporary
        temporary="$(mktemp -d)"
        if ! lcc_copy_release_source "$source" "$temporary" sanitized; then
            rm -rf -- "$temporary"
            return 1
        fi
        digest="$(tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner \
            -cf - -C "$temporary" . | sha256sum | cut -d' ' -f1)"
        rm -rf -- "$temporary"
        printf 'artifact-sha256-%s\n' "$digest"
    fi
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
        --exclude=.venv --exclude=venv --exclude=node_modules --exclude=dist \
        --exclude=data --exclude=backups --exclude=tmp --exclude=memory-bank \
        --exclude=__pycache__ --exclude='*.py[cod]' --exclude='*.egg-info' \
        --exclude=.mypy_cache --exclude=.pytest_cache --exclude=.ruff_cache \
        --exclude=.coverage --exclude=htmlcov --exclude=coverage --exclude=test-results \
        --exclude=.vite --exclude=.cache --exclude=.abacusai --exclude=.idea --exclude=.vscode \
        --exclude=.npmrc --exclude=.pypirc --exclude=.netrc --exclude=.git-credentials \
        --exclude=pip.conf --exclude=.aws --exclude=.ssh --exclude=.docker \
        --exclude='*.pem' --exclude='*.key' --exclude='*.db*' --exclude='*.sqlite*' \
        --exclude='*.log' --exclude='*.tmp' --exclude='*.tsbuildinfo' \
        "$source/" "$destination/"
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
