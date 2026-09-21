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
LCC_UPDATE_ENTRYPOINT="/opt/learning-control-center/update.sh"
LCC_ENVIRONMENT_FILE="/etc/learning-control-center.env"
LCC_APP_PORT_DEFAULT="8000"
LCC_DATA_DIRECTORY="/var/lib/learning-control-center"
LCC_DATABASE_FILE="/var/lib/learning-control-center/lcc.sqlite3"
LCC_BACKUP_DIRECTORY_DEFAULT="/var/backups/learning-control-center"
LCC_CADDY_SITE="/etc/caddy/Caddyfile.d/learning-control-center.caddy"
LCC_CADDY_MAIN="/etc/caddy/Caddyfile"
LCC_CADDY_IMPORT="import /etc/caddy/Caddyfile.d/*.caddy"
LCC_CADDY_BINARY="/usr/bin/caddy"
LCC_GITHUB_REPOSITORY="https://github.com/Learning-Control-Center/Learning-Control-Center.git"
LCC_GITHUB_ASSET_ORIGIN="https://github.com/Learning-Control-Center/Learning-Control-Center/releases/download"
LCC_FORGEJO_REPOSITORY="https://forgejo.waqsea.com/Learning-Control-Center/Learning-Control-Center.git"
LCC_FORGEJO_ASSET_ORIGIN="https://forgejo.waqsea.com/Learning-Control-Center/Learning-Control-Center/releases/download"

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

lcc_compare_stable_release_ids() {
    local left="$1"
    local right="$2"
    /usr/bin/python3 - "$left" "$right" <<'PY'
import re
import sys

pattern = re.compile(
    r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$"
)


def parse(value: str) -> tuple[tuple[int, int, int], tuple[tuple[int, object], ...] | None]:
    match = pattern.fullmatch(value)
    if match is None:
        raise SystemExit(f"invalid semantic release identity: {value}")
    core = tuple(int(part) for part in match.groups()[:3])
    prerelease = match.group(4)
    if prerelease is None:
        return core, None
    identifiers: list[tuple[int, object]] = []
    for identifier in prerelease.split("."):
        if identifier.isdigit():
            identifiers.append((0, int(identifier)))
        else:
            identifiers.append((1, identifier))
    return core, tuple(identifiers)


def compare(left: str, right: str) -> int:
    left_core, left_pre = parse(left)
    right_core, right_pre = parse(right)
    if left_core != right_core:
        return -1 if left_core < right_core else 1
    if left_pre is None and right_pre is None:
        return 0
    if left_pre is None:
        return 1
    if right_pre is None:
        return -1
    for left_item, right_item in zip(left_pre, right_pre, strict=False):
        if left_item == right_item:
            continue
        if left_item[0] != right_item[0]:
            return -1 if left_item[0] < right_item[0] else 1
        return -1 if left_item[1] < right_item[1] else 1
    if len(left_pre) == len(right_pre):
        return 0
    return -1 if len(left_pre) < len(right_pre) else 1


print(compare(sys.argv[1], sys.argv[2]))
PY
}

lcc_validate_source_revision() {
    [[ "$1" =~ ^[0-9a-f]{40}$ ]] || \
        lcc_die "Source revision must be one lowercase 40-character Git commit SHA."
}

lcc_validate_source_metadata() {
    local channel="$1"
    local repository="$2"
    local origin="$3"
    case "$channel|$repository|$origin" in
        "stable|$LCC_GITHUB_REPOSITORY|$LCC_GITHUB_ASSET_ORIGIN"|\
        "stable|$LCC_FORGEJO_REPOSITORY|$LCC_FORGEJO_ASSET_ORIGIN"|\
        "main|$LCC_GITHUB_REPOSITORY|$LCC_GITHUB_REPOSITORY"|\
        "main|$LCC_FORGEJO_REPOSITORY|$LCC_FORGEJO_REPOSITORY") ;;
        *) lcc_die "Source repository/origin do not identify a supported GitHub or Forgejo channel source." ;;
    esac
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

lcc_validate_app_port() {
    local value="${1-}"
    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
        lcc_die "Internal application port must contain only ASCII decimal digits."
    fi
    if test "$value" = 0; then
        lcc_die "Internal application port 0 is outside the allowed range 1024-65535."
    fi
    if test "${#value}" -gt 1 && [[ "$value" = 0* ]]; then
        lcc_die "Internal application port must use canonical decimal form without leading zeros."
    fi
    if test "${#value}" -gt 5 || test "$((10#$value))" -gt 65535; then
        lcc_die "Internal application port must be between 1024 and 65535."
    fi
    if test "$((10#$value))" -lt 1024; then
        lcc_die "Internal application port must be 1024 or higher; ports 80/443 and other privileged ports remain outside the unprivileged LCC service."
    fi
    printf '%s\n' "$value"
}

lcc_app_port_is_explicit() {
    [[ -v 'LCC_DEPLOY_ENV_SEEN[LCC_APP_PORT]' ]]
}

lcc_effective_app_port() {
    if lcc_app_port_is_explicit; then
        lcc_validate_app_port "${LCC_APP_PORT-}"
    else
        printf '%s\n' "$LCC_APP_PORT_DEFAULT"
    fi
}

lcc_app_port_is_available() {
    local port
    port="$(lcc_validate_app_port "$1")"
    /usr/bin/python3 - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
    try:
        listener.bind(("127.0.0.1", port))
    except OSError:
        raise SystemExit(1)
PY
}

lcc_find_available_app_port() {
    local start="${1:-8001}"
    start="$(lcc_validate_app_port "$start")"
    /usr/bin/python3 - "$start" <<'PY'
import socket
import sys

for port in range(int(sys.argv[1]), 65536):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            continue
    print(port)
    raise SystemExit(0)
raise SystemExit(1)
PY
}

lcc_describe_app_port_listener() {
    local port listeners
    port="$(lcc_validate_app_port "$1")"
    command -v ss >/dev/null 2>&1 || return 0
    listeners="$(ss -H -ltnp "sport = :$port" 2>/dev/null || true)"
    /usr/bin/python3 - "$port" "$listeners" <<'PY'
import re
import sys

port, listeners = sys.argv[1:]
for raw_line in listeners.splitlines():
    fields = raw_line.split()
    address = fields[3] if len(fields) >= 4 else f"127.0.0.1:{port}"
    processes = re.findall(r'\("([^"\\]+)",pid=([0-9]+)', raw_line)
    if processes:
        rendered = ", ".join(f"process={name} pid={pid}" for name, pid in processes)
        print(f"Listener: {address} ({rendered})", file=sys.stderr)
    else:
        print(f"Listener: {address} (process details unavailable)", file=sys.stderr)
PY
}

lcc_app_port_owned_by_service() {
    local port service_name main_pid listeners
    port="$(lcc_validate_app_port "$1")"
    service_name="${2:-$LCC_SERVICE_NAME}"
    systemctl is-active --quiet "$service_name" || return 1
    main_pid="$(systemctl show --property MainPID --value "$service_name" 2>/dev/null || true)"
    [[ "$main_pid" =~ ^[1-9][0-9]*$ ]] || return 1
    listeners="$(ss -H -ltnp "sport = :$port" 2>/dev/null || true)"
    test -n "$listeners" && printf '%s\n' "$listeners" | grep -Eq "pid=${main_pid},"
}

lcc_require_configured_app_port_safe() {
    local port
    port="$(lcc_effective_app_port)"
    if lcc_app_port_is_available "$port"; then
        return 0
    fi
    if lcc_app_port_owned_by_service "$port"; then
        return 0
    fi
    lcc_describe_app_port_listener "$port"
    lcc_die "Configured internal application port $port is occupied by a listener that cannot be associated with the active LCC service."
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
    if test "$new_channel" = "$old_channel" && \
        { test "$new_release_id" = "$old_release_id" || \
            test "$new_source_revision" = "$old_source_revision"; }; then
        lcc_die "The requested immutable source identity is already active."
    fi
    if test "$new_channel" != "$old_channel" && test "$confirm_channel_change" -ne 1; then
        lcc_die "Changing from $old_channel to $new_channel requires --confirm-channel-change."
    fi
    if test "$new_channel" = stable && test "$old_channel" = stable; then
        local stable_comparison
        stable_comparison="$(lcc_compare_stable_release_ids "$old_release_id" "$new_release_id")" || \
            lcc_die "Stable update identities are not valid semantic releases."
        if test "$stable_comparison" -ge 0; then
            lcc_die "Stable apply requires a newer semantic release; use rollback for an older release."
        fi
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
    unset LCC_ENVIRONMENT LCC_DATABASE_URL LCC_BACKUP_DIRECTORY LCC_RELEASE_ROOT LCC_APP_PORT \
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
            LCC_ENVIRONMENT|LCC_DATABASE_URL|LCC_BACKUP_DIRECTORY|LCC_RELEASE_ROOT|LCC_APP_PORT|\
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
    lcc_effective_app_port >/dev/null
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

lcc_render_caddy_site() {
    local release_root="$1"
    local frontend_root="$2"
    local output="$3"
    local hostname app_port
    hostname="$(lcc_public_hostname)"
    app_port="$(lcc_effective_app_port)"
    sed -e "s|@@LCC_PUBLIC_HOST@@|$hostname|g" \
        -e "s|@@LCC_FRONTEND_ROOT@@|$frontend_root|g" \
        -e "s|@@LCC_APP_PORT@@|$app_port|g" \
        "$release_root/deploy/Caddyfile.template" > "$output"
    if grep -Eq '@@LCC_[A-Z0-9_]+@@' "$output"; then
        lcc_die "Rendered Caddy site contains an unresolved deployment token."
    fi
}

lcc_release_supports_app_port() {
    local release_root="$1"
    # The literal systemd placeholder is the capability marker.
    # shellcheck disable=SC2016
    grep -Fq 'Environment=LCC_APP_PORT=8000' \
        "$release_root/deploy/learning-control-center.service" 2>/dev/null && \
        grep -Fq '${LCC_APP_PORT}' \
            "$release_root/deploy/learning-control-center.service" 2>/dev/null && \
        grep -Fq '@@LCC_APP_PORT@@' \
            "$release_root/deploy/Caddyfile.template" 2>/dev/null && \
        grep -Fq 'LCC_APP_PORT' "$release_root/scripts/deploy-common.sh" 2>/dev/null
}

lcc_app_port_rollback_mode() {
    local release_root="$1"
    local app_port
    app_port="$(lcc_effective_app_port)"
    if lcc_release_supports_app_port "$release_root"; then
        printf 'port-aware\n'
    elif test "$app_port" != "$LCC_APP_PORT_DEFAULT"; then
        lcc_die "Rollback target predates configurable app ports and cannot honor custom internal port $app_port. Set the app port to 8000 first or choose a port-aware release."
    elif lcc_app_port_is_explicit; then
        printf 'legacy-remove-key\n'
    else
        printf 'legacy-default\n'
    fi
}

lcc_render_environment_with_app_port() {
    local source_file="$1"
    local destination_file="$2"
    local app_port
    app_port="$(lcc_validate_app_port "$3")"
    awk -v app_port="$app_port" '
        BEGIN { found = 0 }
        /^LCC_APP_PORT=/ {
            if (!found) {
                print "LCC_APP_PORT=" app_port
                found = 1
            }
            next
        }
        { print }
        END {
            if (!found) {
                print "LCC_APP_PORT=" app_port
            }
        }
    ' "$source_file" > "$destination_file"
}

lcc_render_environment_without_app_port() {
    local source_file="$1"
    local destination_file="$2"
    awk '!/^LCC_APP_PORT=/' "$source_file" > "$destination_file"
}

lcc_install_environment_file() {
    local source_file="$1"
    local target_file="$2"
    local temporary_file="${target_file}.next.$$"
    install -m 0640 "$source_file" "$temporary_file"
    chown "root:$LCC_SERVICE_GROUP" "$temporary_file"
    mv -f "$temporary_file" "$target_file"
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
    for command_name in python3 sqlite3 rsync tar curl systemctl runuser flock dpkg-query ss; do
        command -v "$command_name" >/dev/null || lcc_die "Missing production prerequisite: $command_name"
    done
    python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' || \
        lcc_die "Python 3.12 or newer is required."
    test -x "$LCC_CADDY_BINARY" || lcc_die "The package-owned $LCC_CADDY_BINARY is required."
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

lcc_validate_staged_caddy_site() (
    set -euo pipefail
    local candidate_site="$1"
    local main_path="${2:-$LCC_CADDY_MAIN}"
    local caddy_binary="${3:-$LCC_CADDY_BINARY}"
    local site_directory expected_import staging_directory="" staged_site_directory staged_main
    # Invoked indirectly by the EXIT trap below.
    # shellcheck disable=SC2329
    cleanup_staged_caddy_validation() {
        local original_status=$?
        if test -n "$staging_directory"; then
            rm -rf -- "$staging_directory"
        fi
        exit "$original_status"
    }
    trap cleanup_staged_caddy_validation EXIT
    site_directory="$(dirname -- "$LCC_CADDY_SITE")"
    expected_import="import $site_directory/*.caddy"
    test -f "$main_path" || lcc_die "Caddy main configuration is missing: $main_path"
    grep -Fqx "$expected_import" "$main_path" || \
        lcc_die "Caddy main configuration no longer contains the managed LCC site import."
    staging_directory="$(mktemp -d "${LCC_TRANSACTION_DIRECTORY_PARENT:-/run}/lcc-caddy-validation.XXXXXXXX")"
    chmod 0700 "$staging_directory"
    staged_site_directory="$staging_directory/Caddyfile.d"
    staged_main="$staging_directory/Caddyfile"
    mkdir -m 0700 "$staged_site_directory"
    if test -d "$site_directory"; then
        cp -a -- "$site_directory/." "$staged_site_directory/"
    fi
    install -m 0644 "$candidate_site" \
        "$staged_site_directory/$(basename -- "$LCC_CADDY_SITE")"
    awk -v managed_import="$expected_import" \
        -v staged_import="import $staged_site_directory/*.caddy" '
        $0 == managed_import { print staged_import; next }
        { print }
    ' "$main_path" > "$staged_main"
    if ! "$caddy_binary" fmt --overwrite \
        "$staged_site_directory/$(basename -- "$LCC_CADDY_SITE")" || \
        ! "$caddy_binary" validate --config "$staged_main" --adapter caddyfile; then
        lcc_die "The proposed LCC site does not pass complete Caddy configuration validation."
    fi
)

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

lcc_wait_for_internal_health() {
    local port attempts delay hostname attempt
    port="$(lcc_validate_app_port "$1")"
    attempts="${2:-40}"
    delay="${3:-0.5}"
    hostname="$(lcc_public_hostname)"
    for ((attempt = 1; attempt <= attempts; attempt++)); do
        if curl --fail --silent --show-error --max-time 5 \
            --header "Host: $hostname" \
            "http://127.0.0.1:$port/api/v1/health" >/dev/null 2>&1; then
            return 0
        fi
        sleep "$delay"
    done
    return 1
}

lcc_apply_app_port_change() (
    set -euo pipefail
    local requested_port="$1"
    local dry_run="${2:-0}"
    local old_port release_root transaction_parent transaction_directory
    local staged_environment rendered_caddy caddy_backup_directory temporary_site
    local transaction_active=0
    old_port="$(lcc_effective_app_port)"
    requested_port="$(lcc_validate_app_port "$requested_port")"
    test "$requested_port" != "$old_port" || {
        echo "Internal application port is already $old_port; no changes were made."
        exit 0
    }
    if ! lcc_app_port_is_available "$requested_port"; then
        lcc_describe_app_port_listener "$requested_port"
        lcc_die "Internal application port $requested_port is already occupied."
    fi
    release_root="$(readlink -f "$LCC_CURRENT_RELEASE")"
    test -d "$release_root" || lcc_die "Active release link is invalid."
    transaction_parent="${LCC_TRANSACTION_DIRECTORY_PARENT:-/run}"
    transaction_directory="$(mktemp -d "$transaction_parent/lcc-app-port.XXXXXXXX")"
    chmod 0700 "$transaction_directory"
    cleanup_unactivated_app_port_change() {
        local original_status=$?
        rm -rf -- "$transaction_directory"
        exit "$original_status"
    }
    trap cleanup_unactivated_app_port_change EXIT
    staged_environment="$transaction_directory/environment.next"
    rendered_caddy="$transaction_directory/site.next"
    caddy_backup_directory="$transaction_directory/caddy-backup"
    mkdir -m 0700 "$caddy_backup_directory"
    cp -a -- "$LCC_ENVIRONMENT_FILE" "$transaction_directory/environment.previous"
    if test -f "$LCC_CADDY_SITE"; then
        cp -a -- "$LCC_CADDY_SITE" "$caddy_backup_directory/site"
    fi
    if test -f "$LCC_CADDY_MAIN"; then
        cp -a -- "$LCC_CADDY_MAIN" "$caddy_backup_directory/main"
    fi
    lcc_render_environment_with_app_port \
        "$LCC_ENVIRONMENT_FILE" "$staged_environment" "$requested_port"
    chmod 0600 "$staged_environment"
    lcc_load_environment "$staged_environment"
    lcc_validate_environment "$LCC_CURRENT_RELEASE"
    lcc_render_caddy_site \
        "$release_root" "$LCC_CURRENT_RELEASE/frontend/dist" "$rendered_caddy"
    lcc_validate_staged_caddy_site \
        "$rendered_caddy" "$LCC_CADDY_MAIN" "$LCC_CADDY_BINARY"

    if test "$dry_run" -eq 1; then
        echo "Dry run passed: internal application port can change from $old_port to $requested_port."
        exit 0
    fi

    if ! lcc_app_port_is_available "$requested_port"; then
        lcc_describe_app_port_listener "$requested_port"
        lcc_die "Internal application port $requested_port became occupied before activation."
    fi

    # Invoked indirectly by the EXIT trap below.
    # shellcheck disable=SC2329
    rollback_app_port_change() {
        local original_status=$? recovery_failed=0
        test "$transaction_active" -eq 1 || exit "$original_status"
        trap - EXIT
        set +e
        lcc_note "App-port change failed; restoring internal port $old_port."
        systemctl stop "$LCC_SERVICE_NAME" >/dev/null 2>&1
        lcc_install_environment_file \
            "$transaction_directory/environment.previous" "$LCC_ENVIRONMENT_FILE" || recovery_failed=1
        lcc_restore_caddy_configuration \
            "$caddy_backup_directory" "$LCC_CADDY_SITE" "$LCC_CADDY_MAIN" || recovery_failed=1
        "$LCC_CADDY_BINARY" validate --config "$LCC_CADDY_MAIN" --adapter caddyfile \
            >/dev/null || recovery_failed=1
        systemctl restart "$LCC_SERVICE_NAME" || recovery_failed=1
        lcc_load_environment "$LCC_ENVIRONMENT_FILE"
        lcc_wait_for_internal_health "$old_port" 60 0.5 || recovery_failed=1
        systemctl reload caddy.service || recovery_failed=1
        lcc_wait_for_health 60 0.5 || recovery_failed=1
        if test "$recovery_failed" -ne 0; then
            lcc_note "CRITICAL: app-port rollback is incomplete. Protected recovery artifacts remain at $transaction_directory"
            exit 1
        fi
        rm -rf -- "$transaction_directory"
        lcc_note "Restored internal application port $old_port after the failed change."
        exit "$original_status"
    }
    transaction_active=1
    trap rollback_app_port_change EXIT

    temporary_site="${LCC_CADDY_SITE}.next.$$"
    install -m 0644 "$rendered_caddy" "$temporary_site"
    mv -f "$temporary_site" "$LCC_CADDY_SITE"
    lcc_format_validate_or_restore_caddy \
        "$LCC_CADDY_SITE" "$LCC_CADDY_MAIN" "$caddy_backup_directory"
    lcc_install_environment_file "$staged_environment" "$LCC_ENVIRONMENT_FILE"
    systemctl restart "$LCC_SERVICE_NAME"
    lcc_wait_for_internal_health "$requested_port" 60 0.5 || {
        journalctl -u "$LCC_SERVICE_NAME" -n 80 --no-pager >&2 || true
        lcc_die "LCC did not become healthy on internal application port $requested_port."
    }
    systemctl reload caddy.service
    lcc_wait_for_health 60 0.5 || \
        lcc_die "Public HTTPS health verification failed after the app-port change."

    transaction_active=0
    trap - EXIT
    rm -rf -- "$transaction_directory"
    echo "Internal application port changed from $old_port to $requested_port."
    echo "Uvicorn remains bound to 127.0.0.1; public HTTPS remains at $LCC_PUBLIC_ORIGIN."
)
