#!/usr/bin/env bash
set -euo pipefail
umask 077

# Shared V2 entry point. Existing deployments use the transition engine;
# fresh Core installation remains here.
script_directory="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/deploy-common.sh
source "$script_directory/deploy-common.sh"
# shellcheck source=scripts/install/platforms/ubuntu-24.04.sh
source "$script_directory/install/platforms/ubuntu-24.04.sh"
# shellcheck source=scripts/install/gateway.sh
source "$script_directory/install/gateway.sh"

usage() {
    cat <<'EOF'
Usage: install.sh [--domain HOST|--public-origin HTTPS_ORIGIN] [--timezone ZONE] [--app-port PORT]
                  [--gateway caddy|external] [--env-file ABSOLUTE_PATH]
                  [--non-interactive] [--yes] [--dry-run]

Requires a validated source identity from bootstrap.sh or a release-bound
installer. Managed Caddy is the default; external needs operator integration.
Managed Caddy requires a domain; external and Core-only installs may defer it.
--dry-run previews an existing-installation transition only.
EOF
}

domain=""
public_origin=""
timezone=""
requested_port=""
environment_source=""
non_interactive=0
gateway=caddy
gateway_explicit=0
core_only=0
# The sourced transition engine consumes this parsed choice.
# shellcheck disable=SC2034
assume_yes=0
dry_run=0
test_root="/"
# The sourced transition engine consumes assume_yes from this parser.
# shellcheck disable=SC2034
while test "$#" -gt 0; do
    case "$1" in
        --domain|--public-origin|--timezone|--app-port|--env-file|--commit|--channel|--test-root|--gateway)
            test "$#" -ge 2 || lcc_die "$1 requires a value."
            case "$1" in
                --domain) domain="$2" ;;
                --public-origin) public_origin="$2" ;;
                --timezone) timezone="$2" ;;
                --app-port) requested_port="$2" ;;
                --env-file) environment_source="$2" ;;
                --commit) test "$2" = "${LCC_V2_SOURCE_SHA:-}" || lcc_die "--commit differs from the acquired SHA." ;;
                --channel) test "$2" = main || lcc_die "Only public main is supported." ;;
                --test-root) test_root="$2" ;;
                --gateway) gateway="$2"; gateway_explicit=1 ;;
            esac
            shift 2 ;;
        --commit=*) test "${1#*=}" = "${LCC_V2_SOURCE_SHA:-}" || lcc_die "--commit differs from the acquired SHA."; shift ;;
        --channel=*) test "${1#*=}" = main || lcc_die "Only public main is supported."; shift ;;
        --non-interactive) non_interactive=1; shift ;;
        --yes) assume_yes=1; shift ;;
        --dry-run) dry_run=1; shift ;;
        --core-only) core_only=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) lcc_die "Unknown Core installer option: $1" ;;
    esac
done
if test -n "$public_origin"; then
    lcc_validate_public_origin "$public_origin"
    origin_hostname="$(/usr/bin/python3 - "$public_origin" <<'PY'
import sys
from urllib.parse import urlsplit
print(urlsplit(sys.argv[1]).hostname)
PY
)"
    test -z "$domain" || test "$domain" = "$origin_hostname" ||
        lcc_die "--domain and --public-origin disagree."
    domain="$origin_hostname"
fi
lcc_validate_gateway_mode "$gateway"
test "$core_only" -eq 0 || test "$gateway_explicit" -eq 0 ||
    lcc_die "--core-only cannot be combined with --gateway."

test "$(id -u)" -eq 0 || {
    test "$test_root" != / || lcc_die "Core installation requires root."
}
if test "$test_root" != /; then
    [[ "$test_root" = /* ]] || lcc_die "--test-root must be absolute."
    test_root="$(readlink -m -- "$test_root")"
    [[ "$test_root" = /tmp/* ]] || lcc_die "--test-root must resolve beneath /tmp."
fi
test "${LCC_V2_SOURCE_REPOSITORY:-}" = "$LCC_GITHUB_REPOSITORY" ||
    lcc_die "Core installation requires validated public GitHub source identity."
lcc_validate_source_revision "${LCC_V2_SOURCE_SHA:-}"
source_channel=main
source_ref=refs/heads/main
source_origin="$LCC_GITHUB_REPOSITORY"
release_id="main-$LCC_V2_SOURCE_SHA"
if test -n "${LCC_V2_RELEASE_ID:-}"; then
    source_channel=stable
    release_id="$LCC_V2_RELEASE_ID"
    lcc_validate_release_id "$release_id"
    source_ref="refs/tags/$release_id"
    source_origin=https://github.com/Learning-Control-Center/Learning-Control-Center/releases/download
fi
test "${LCC_V2_SOURCE_REF:-}" = "$source_ref" ||
    lcc_die "Core source ref does not match its selected immutable identity."
test -n "${LCC_V2_SOURCE_ROOT:-}" && [[ "$LCC_V2_SOURCE_ROOT" = /* ]] ||
    lcc_die "Validated source root is required."
test ! -L "$LCC_V2_SOURCE_ROOT" && test -d "$LCC_V2_SOURCE_ROOT" ||
    lcc_die "Validated source root must be a real directory."
source_root="$(readlink -f -- "$LCC_V2_SOURCE_ROOT")"
if test "$source_channel" = stable; then
    lcc_validate_release_manifest "$source_root" stable "$release_id" \
        "$LCC_GITHUB_REPOSITORY" "$source_ref" "$LCC_V2_SOURCE_SHA" "$source_origin"
fi
test "$source_root/scripts/install.sh" = "$(readlink -f -- "${BASH_SOURCE[0]}")" ||
    lcc_die "Installer does not belong to the validated source root."
if test "$test_root" = /; then
    source_parent="$(dirname -- "$source_root")"
    source_mode="$(stat -c %a "$source_root")"
    parent_mode="$(stat -c %a "$source_parent")"
    # shellcheck disable=SC2015
    test "$(stat -c %u "$source_root")" -eq 0 &&
        test "$(stat -c %u "$source_parent")" -eq 0 &&
        test "$((8#$parent_mode & 8#022))" -eq 0 &&
        { test "$((8#$source_mode & 8#077))" -eq 0 ||
            test "$((8#$parent_mode & 8#077))" -eq 0; } ||
        lcc_die "Validated source requires a root-owned, private directory with no writable parent."
fi
for required in pyproject.toml requirements-production.lock frontend/dist/index.html \
    scripts/frontend-artifact.py scripts/generate-production-env.sh \
    scripts/install/gateway.sh \
    deploy/learning-control-center.service deploy/learning-control-center-backup.service \
    deploy/learning-control-center-backup.timer; do
    test -f "$source_root/$required" || lcc_die "Validated source lacks $required."
done
test -x "$source_root/scripts/frontend-artifact.py" || lcc_die "Frontend verifier is not executable."
lcc_verify_frontend_artifact "$source_root"

application_root="$(lcc_prefixed_path "$test_root" "$LCC_APPLICATION_ROOT")"
current_release="$(lcc_prefixed_path "$test_root" "$LCC_CURRENT_RELEASE")"
environment_target="$(lcc_prefixed_path "$test_root" "$LCC_ENVIRONMENT_FILE")"
gateway_state="$(lcc_prefixed_path "$test_root" "$LCC_DEPLOYMENT_STATE_FILE")"
data_directory="$(lcc_prefixed_path "$test_root" "$LCC_DATA_DIRECTORY")"
database_file="$(lcc_prefixed_path "$test_root" "$LCC_DATABASE_FILE")"
backup_directory="$(lcc_prefixed_path "$test_root" "$LCC_BACKUP_DIRECTORY_DEFAULT")"
systemd_directory="$(lcc_prefixed_path "$test_root" /etc/systemd/system)"
admin_link="$(lcc_prefixed_path "$test_root" /usr/local/sbin/lcc-admin)"
release_directory="$application_root/releases/$release_id"
marker="$release_directory/INSTALLER_V2_CORE"
if test -L "$current_release"; then
    if test "$test_root" = /; then
        # A fresh Core may already be healthy when managed Caddy fails DNS/TLS
        # or ownership checks. That exact, marked release has no gateway state
        # yet; allow the same SHA to finish only its missing gateway step.
        if ! { test "$(readlink -- "$current_release")" = "$release_directory" &&
            test -f "$marker" && test "$(cat "$marker")" = 1 &&
            test ! -e "$gateway_state" && test ! -L "$gateway_state"; }; then
            test "$source_channel" = main ||
                lcc_die "Pinned release installers are for fresh installations; update existing LCC from public main."
            # shellcheck source=scripts/install/transition.sh
            source "$script_directory/install/transition.sh"
            lcc_transition_apply
            exit 0
        fi
    fi
fi
test "$dry_run" -eq 0 || lcc_die "Fresh Core dry-run is not supported; no changes made."
if test -e "$gateway_state" || test -L "$gateway_state"; then
    if test "$test_root" = /; then
        saved_gateway="$(lcc_read_gateway_state "$gateway_state")"
    else
        saved_gateway="$(lcc_read_gateway_state "$gateway_state" "$(id -u)")"
    fi
    if test "$gateway_explicit" -eq 1; then
        test "$saved_gateway" = "$gateway" ||
            lcc_die "Existing gateway mode is $saved_gateway; switching modes is not supported in Phase 4."
    else
        gateway="$saved_gateway"
    fi
    test -f "$marker" || lcc_die "Gateway state exists without a V2 Core release."
fi
if test "$test_root" != / && test "$core_only" -eq 0 && test "$gateway" = caddy; then
    lcc_die "Managed Caddy requires a disposable systemd host; use --core-only for layout tests."
fi
if test "$test_root" = / && test "$core_only" -eq 0 && test "$gateway_explicit" -eq 0 &&
    test "$non_interactive" -eq 0 && test ! -e "$environment_target"; then
    exec 3<>/dev/tty || lcc_die "Gateway selection requires a controlling terminal; use --gateway for automation."
    printf 'Gateway mode [caddy/external] (default caddy): ' >&3
    IFS= read -r selected_gateway <&3 || lcc_die "Cannot read gateway selection."
    gateway="${selected_gateway:-caddy}"
    lcc_validate_gateway_mode "$gateway"
fi
for owned_directory in "$application_root" "$application_root/releases" "$release_directory" \
    "$data_directory" "$backup_directory"; do
    test ! -L "$owned_directory" || lcc_die "LCC-owned directory must not be a symlink: $owned_directory"
done
test ! -e "$current_release" || test -L "$current_release" ||
    lcc_die "Active release path is occupied by an unrelated non-symlink file."
test ! -L "$environment_target" || lcc_die "Production environment must not be a symlink."
if test "$test_root" = /; then
    for owned_parent in "$application_root" "$application_root/releases"; do
        if test -e "$owned_parent"; then
            parent_mode="$(stat -c %a "$owned_parent")"
            test -d "$owned_parent" && test "$(stat -c %u "$owned_parent")" -eq 0 &&
                test "$((8#$parent_mode & 8#022))" -eq 0 ||
                lcc_die "Existing application directory is not safely root-owned: $owned_parent"
        fi
    done
fi

lcc_application_skeleton_is_empty() {
    test -d "$application_root" && test ! -L "$application_root" &&
        test -d "$application_root/releases" && test ! -L "$application_root/releases" &&
        test ! -e "$current_release" && test ! -L "$current_release" &&
        test -z "$(find "$application_root" -mindepth 1 -maxdepth 1 ! -name releases -print -quit)" &&
        test -z "$(find "$application_root/releases" -mindepth 1 -print -quit)"
}

# Isolated layout tests have no running service or production migration path.
if test -e "$application_root" || test -L "$current_release"; then
    if test -L "$current_release"; then
        test "$(readlink -- "$current_release")" = "$release_directory" && test -f "$marker" ||
            lcc_die "Existing installation needs the Phase 5 V1-to-V2/update transition; no changes made."
    elif ! test -f "$marker" && ! lcc_application_skeleton_is_empty; then
        lcc_die "Existing LCC application path has no V2 Core identity; no changes made."
    fi
fi
if test -e "$release_directory" && ! test -f "$marker"; then
    lcc_die "Unrecognized or incomplete release path; inspect it before retrying: $release_directory"
fi
if ! test -f "$marker"; then
    for owned_path in "$environment_target" \
        "$systemd_directory/$LCC_SERVICE_NAME" \
        "$systemd_directory/$LCC_BACKUP_SERVICE_NAME" \
        "$systemd_directory/$LCC_BACKUP_TIMER_NAME" "$admin_link"; do
        test ! -e "$owned_path" && test ! -L "$owned_path" ||
            lcc_die "Refusing to overwrite pre-existing LCC path: $owned_path"
    done
    # A default uninstall intentionally retains these two LCC-owned locations.
    # Reuse only dedicated, private directories; the database schema is checked
    # after the immutable runtime has been staged and before activation.
    if test "$test_root" = /; then
        for preserved in "$data_directory" "$backup_directory"; do
            if test -e "$preserved"; then
                test -d "$preserved" && test ! -L "$preserved" &&
                    test "$(stat -c '%U:%G %a' "$preserved")" = 'lcc:lcc 700' ||
                    lcc_die "Preserved LCC data directory has unsafe ownership or mode: $preserved"
            fi
        done
    fi
fi

if test -n "$environment_source" && test -n "$requested_port"; then
    lcc_die "--env-file and --app-port cannot be combined."
fi
if test -n "$environment_source" && test -e "$environment_target"; then
    lcc_die "Existing environment cannot be replaced during a Core rerun."
fi
if test -n "$environment_source"; then
    lcc_validate_environment_file_security "$environment_source" 0 "$(id -u)"
    lcc_load_environment "$environment_source"
    lcc_validate_environment "$current_release"
    test "$LCC_DATABASE_URL" = "sqlite:///$database_file" &&
        test "$LCC_BACKUP_DIRECTORY" = "$backup_directory" ||
        lcc_die "External environment has incompatible Core data paths."
    existing_domain="$(lcc_public_hostname)"
    test -z "$domain" || test "$domain" = "$existing_domain" ||
        lcc_die "External environment domain differs from --domain."
    test -z "$public_origin" || test "$public_origin" = "$LCC_PUBLIC_ORIGIN" ||
        lcc_die "External environment public origin differs from --public-origin."
    test -z "$timezone" || test "$timezone" = "$LCC_APP_TIMEZONE" ||
        lcc_die "External environment timezone differs from --timezone."
    domain="$existing_domain"
    app_port="$(lcc_effective_app_port)"
fi
if test -e "$environment_target"; then
    if test "$test_root" = /; then
        lcc_validate_environment_file_security "$environment_target" 1 0
    else
        lcc_validate_environment_file_security "$environment_target" 0 "$(id -u)"
    fi
    lcc_load_environment "$environment_target"
    lcc_validate_environment "$current_release"
    test "$LCC_DATABASE_URL" = "sqlite:///$database_file" &&
        test "$LCC_BACKUP_DIRECTORY" = "$backup_directory" ||
        lcc_die "Existing environment has incompatible Core data paths."
    existing_domain="$(lcc_public_hostname)"
    test -z "$domain" || test "$domain" = "$existing_domain" || lcc_die "Domain differs from existing configuration."
    test -z "$public_origin" || test "$public_origin" = "$LCC_PUBLIC_ORIGIN" ||
        lcc_die "Public origin differs from existing configuration; use lcc-admin public-origin set."
    test -z "$timezone" || test "$timezone" = "$LCC_APP_TIMEZONE" || lcc_die "Timezone differs from existing configuration."
    test -z "$requested_port" || test "$requested_port" = "$(lcc_effective_app_port)" ||
        lcc_die "Port changes after installation require the gateway-aware Phase 4 workflow."
    domain="$existing_domain"
    app_port="$(lcc_effective_app_port)"
elif test -z "$environment_source"; then
    if test "$gateway" = caddy && test "$core_only" -eq 0 && test -z "$domain"; then
        test "$non_interactive" -eq 0 || lcc_die "Managed Caddy requires --domain or --public-origin in non-interactive mode."
        printf 'Managed Caddy HTTPS domain: ' > /dev/tty
        IFS= read -r domain < /dev/tty || lcc_die "Cannot read domain from the terminal."
    fi
    if test -n "$domain"; then lcc_validate_public_hostname "$domain"; fi
    timezone="${timezone:-UTC}"
    app_port="$(lcc_validate_app_port "${requested_port:-$LCC_APP_PORT_DEFAULT}")"
fi
if test -n "$domain"; then lcc_validate_public_hostname "$domain"; fi
if test "$test_root" = /; then
    lcc_ubuntu_check_platform
    if ! lcc_app_port_is_available "$app_port"; then
        if test -L "$current_release" && lcc_app_port_owned_by_service "$app_port"; then
            lcc_note "Port $app_port is already owned by the active LCC service."
        elif test -n "$requested_port" || test -e "$environment_target" || test "$non_interactive" -eq 1; then
            lcc_describe_app_port_listener "$app_port"
            lcc_die "Port $app_port is occupied; choose an available --app-port."
        else
            lcc_describe_app_port_listener "$app_port"
            suggested="$(lcc_find_available_app_port)"
            printf 'Choose another internal port (suggested %s): ' "$suggested" > /dev/tty
            IFS= read -r app_port < /dev/tty || lcc_die "Cannot read an internal port."
            app_port="$(lcc_validate_app_port "$app_port")"
            lcc_app_port_is_available "$app_port" || lcc_die "Selected port is occupied."
        fi
    fi
fi

temporary_environment=""
staging_directory=""
activated_here=0
cleanup_core_temporary() {
    local status="${1:-$?}"
    if test "$status" -ne 0 && test "$activated_here" -eq 1 && test "$test_root" = /; then
        systemctl stop "$LCC_SERVICE_NAME" >/dev/null 2>&1 || true
        systemctl disable "$LCC_SERVICE_NAME" "$LCC_BACKUP_TIMER_NAME" >/dev/null 2>&1 || true
        if test -L "$current_release" && test "$(readlink "$current_release")" = "$release_directory"; then
            rm -f -- "$current_release"
        fi
        lcc_note "Fresh Core activation was undone; release, data, and configuration remain for a safe retry."
    fi
    if test -n "$temporary_environment"; then rm -f -- "$temporary_environment"; fi
    if test -n "$staging_directory"; then rm -rf -- "$staging_directory"; fi
    exit "$status"
}
trap cleanup_core_temporary EXIT
if test -n "$environment_source"; then
    : # An operator-supplied environment was validated before port selection.
elif ! test -e "$environment_target"; then
    environment_parent=/run
    if test "$test_root" != /; then environment_parent=/tmp; fi
    temporary_environment="$(mktemp "$environment_parent/lcc-core-env.XXXXXXXX")"
    rm -f -- "$temporary_environment"
    env_arguments=(--timezone "$timezone" --app-port "$app_port" --output "$temporary_environment")
    if test -n "$public_origin"; then
        env_arguments+=(--public-origin "$public_origin")
    elif test -n "$domain"; then
        env_arguments+=(--domain "$domain")
    fi
    if test "$test_root" != /; then env_arguments+=(--root "$test_root"); fi
    "$source_root/scripts/generate-production-env.sh" "${env_arguments[@]}"
    environment_source="$temporary_environment"
    if test "$test_root" = / && test -f "$database_file"; then
        # An initialized single-user database cannot coexist with a fresh
        # bootstrap token. Its secrets/sessions are deliberately rotated.
        if test "$(sqlite3 "$database_file" 'SELECT COUNT(*) FROM users;' 2>/dev/null || true)" = 1; then
            sed -i '/^LCC_BOOTSTRAP_TOKEN=/d' "$environment_source"
        fi
    fi
fi
lcc_load_environment "${environment_source:-$environment_target}"
lcc_validate_environment "$current_release"

if test -f "$marker"; then
    test "$(cat "$marker")" = 1 || lcc_die "Unknown V2 Core marker."
    lcc_validate_release_manifest "$release_directory" "$source_channel" "$release_id" \
        "$LCC_GITHUB_REPOSITORY" "$source_ref" "$LCC_V2_SOURCE_SHA" "$source_origin"
    test -x "$release_directory/.venv/bin/python" || {
        test "$test_root" != / || lcc_die "Existing V2 Core runtime is incomplete."
    }
    lcc_verify_frontend_artifact "$release_directory"
    if test "$test_root" = /; then
        for persistent_directory in "$data_directory" "$backup_directory"; do
            test -d "$persistent_directory" &&
                test "$(stat -c '%U:%G %a' "$persistent_directory")" = 'lcc:lcc 700' ||
                lcc_die "Existing Core data directory has unexpected ownership or mode: $persistent_directory"
        done
        test "$(stat -c '%U:%G' "$release_directory")" = 'root:lcc' ||
            lcc_die "Existing Core release ownership changed."
    fi
fi
for unit in "$LCC_SERVICE_NAME" "$LCC_BACKUP_SERVICE_NAME" "$LCC_BACKUP_TIMER_NAME"; do
    target="$systemd_directory/$unit"
    if test -e "$target"; then
        # shellcheck disable=SC2015
        test -f "$marker" && test ! -L "$target" &&
            cmp -s "$source_root/deploy/$unit" "$target" ||
            lcc_die "Refusing to overwrite modified or unrelated unit: $target"
    fi
done
if test -e "$admin_link" || test -L "$admin_link"; then
    test -f "$marker" && test -L "$admin_link" &&
        test "$(readlink "$admin_link")" = "$LCC_CURRENT_RELEASE/scripts/lcc-admin" ||
        lcc_die "Refusing to overwrite unrelated administrator entry point."
fi

if test "$test_root" = /; then
    lcc_acquire_deployment_lock
    python3 - "$LCC_SERVICE_USER" "$LCC_SERVICE_GROUP" "$LCC_DATA_DIRECTORY" <<'PY'
import grp
import pwd
import sys

name, group_name, expected_home = sys.argv[1:]
group = next((group for group in grp.getgrall() if group.gr_name == group_name), None)
user = next((user for user in pwd.getpwall() if user.pw_name == name), None)
if user and (not group or user.pw_gid != group.gr_gid or user.pw_uid >= 1000
             or user.pw_dir != expected_home or user.pw_shell != "/usr/sbin/nologin"):
    raise SystemExit("Existing LCC account is not the dedicated locked system account")
if group and (group.gr_gid >= 1000 or set(group.gr_mem) - {name} or any(
    entry.pw_gid == group.gr_gid and entry.pw_name != name for entry in pwd.getpwall()
)):
    raise SystemExit("Existing LCC group is not dedicated to LCC")
PY
    lcc_ubuntu_provision_core
    # Recheck identity after any package-manager delay, before deployment writes.
    test ! -L "$current_release" ||
        test "$(readlink "$current_release")" = "$release_directory" ||
        lcc_die "Active release changed during provisioning."
    if ! getent group "$LCC_SERVICE_GROUP" >/dev/null; then groupadd --system "$LCC_SERVICE_GROUP"; fi
    if ! id "$LCC_SERVICE_USER" >/dev/null 2>&1; then
        useradd --system --gid "$LCC_SERVICE_GROUP" --home-dir "$LCC_DATA_DIRECTORY" \
            --shell /usr/sbin/nologin "$LCC_SERVICE_USER"
    fi
    password_state="$(getent shadow "$LCC_SERVICE_USER" | cut -d: -f2)"
    [[ "$password_state" = \!* || "$password_state" = \** ]] || lcc_die "LCC account password is not locked."
else
    install -d -m 0700 "$test_root"
    chmod 0700 "$test_root"
fi
if test "$test_root" = /; then
    for host_directory in "$systemd_directory" "$(dirname "$environment_target")" \
        "$(dirname "$admin_link")"; do
        test -d "$host_directory" && test ! -L "$host_directory" ||
            lcc_die "Required host directory is unavailable or unsafe: $host_directory"
    done
else
    install -d -m 0755 "$systemd_directory" "$(dirname "$environment_target")" \
        "$(dirname "$admin_link")"
fi
install -d -m 0755 "$application_root" "$application_root/releases"
install -d -m 0700 "$data_directory" "$backup_directory"
if test "$test_root" = /; then
    chown "$LCC_SERVICE_USER:$LCC_SERVICE_GROUP" "$data_directory" "$backup_directory"
fi

if ! test -f "$marker"; then
    lcc_note "Staging Core release $release_id"
    staging_directory="$application_root/releases/.$release_id.staging.$$"
    test ! -e "$staging_directory" || lcc_die "Core staging path already exists."
    install -d -m 0700 "$staging_directory"
    lcc_copy_release_source "$source_root" "$staging_directory" sanitized
    lcc_verify_frontend_artifact "$staging_directory"
    # Build in its final immutable path: editable installs record this absolute
    # directory in the venv and break if it is moved afterward.
    printf '1\n' > "$staging_directory/.installing"
    mv -T -- "$staging_directory" "$release_directory"
    staging_directory=""
    cleanup_unfinished_release() {
        local status=$?
        if test -f "$release_directory/.installing"; then
            rm -rf -- "$release_directory"
        fi
        rmdir -- "$application_root/releases" "$application_root" 2>/dev/null || true
        trap - EXIT
        cleanup_core_temporary "$status"
    }
    trap cleanup_unfinished_release EXIT
    if test "$test_root" = /; then
        python3 -m venv "$release_directory/.venv"
        "$release_directory/.venv/bin/python" -m pip install \
            --constraint "$release_directory/requirements-production.lock" setuptools wheel
        "$release_directory/.venv/bin/python" -m pip install \
            --constraint "$release_directory/requirements-production.lock" \
            --no-build-isolation --editable "$release_directory"
        "$release_directory/.venv/bin/python" -m pip check
    fi
    printf '%s\n' "$release_id" > "$release_directory/RELEASE_ID"
    printf '%s\n' "$source_channel" > "$release_directory/RELEASE_CHANNEL"
    printf '%s\n' "$LCC_V2_SOURCE_SHA" > "$release_directory/SOURCE_REVISION"
    cat > "$release_directory/RELEASE_MANIFEST" <<EOF
metadata_version=1
channel=$source_channel
release_id=$release_id
source_repository=$LCC_GITHUB_REPOSITORY
source_ref=$source_ref
source_revision=$LCC_V2_SOURCE_SHA
source_origin=$source_origin
EOF
    printf '1\n' > "$release_directory/INSTALLER_V2_CORE"
    if test "$test_root" = /; then
        chown -R "root:$LCC_SERVICE_GROUP" "$release_directory"
        chmod -R u=rwX,g=rX,o= "$release_directory"
        chmod o+x "$release_directory" "$release_directory/frontend"
        find "$release_directory/frontend/dist" -type d -exec chmod 0755 {} +
        find "$release_directory/frontend/dist" -type f -exec chmod 0644 {} +
    fi
    rm -f -- "$release_directory/.installing"
    trap cleanup_core_temporary EXIT
fi
if test "$test_root" = / && ! test -L "$current_release" && test -f "$database_file"; then
    test "$(sqlite3 "$database_file" 'PRAGMA integrity_check;')" = ok &&
        test -z "$(sqlite3 "$database_file" 'PRAGMA foreign_key_check;')" ||
        lcc_die "Preserved database failed integrity verification; no release was activated."
    preserved_revision="$(sqlite3 "$database_file" 'SELECT version_num FROM alembic_version;')"
    candidate_revision="$(PYTHONPATH="$release_directory/backend" \
        "$release_directory/.venv/bin/python" -c \
        'from app.ops import expected_revision; print(expected_revision())')"
    test "$preserved_revision" = "$candidate_revision" ||
        lcc_die "Preserved database schema $preserved_revision differs from this release ($candidate_revision); install a schema-compatible release first."
fi
if ! test -e "$environment_target"; then
    if test "$test_root" = /; then
        lcc_install_environment_file "$environment_source" "$environment_target"
    else
        install -m 0600 "$environment_source" "$environment_target"
    fi
fi
if test "$test_root" = /; then
    lcc_validate_environment_file_security "$environment_target" 1 0
fi
for unit in "$LCC_SERVICE_NAME" "$LCC_BACKUP_SERVICE_NAME" "$LCC_BACKUP_TIMER_NAME"; do
    if ! test -e "$systemd_directory/$unit"; then
        install -m 0644 "$release_directory/deploy/$unit" "$systemd_directory/$unit"
    fi
done
if ! test -L "$admin_link"; then
    ln -s "$LCC_CURRENT_RELEASE/scripts/lcc-admin" "$admin_link"
fi
if test "$test_root" = /; then
    install -m 0755 "$release_directory/deploy/learning-control-center-update.sh" \
        "$LCC_UPDATE_ENTRYPOINT"
fi
if ! test -L "$current_release"; then
    next_link="$application_root/.current.$$.next"
    ln -s "$release_directory" "$next_link"
    mv -T -- "$next_link" "$current_release"
    activated_here=1
fi
if test "$test_root" = /; then
    systemctl daemon-reload
    systemctl enable "$LCC_SERVICE_NAME" "$LCC_BACKUP_TIMER_NAME"
    if ! systemctl is-active --quiet "$LCC_SERVICE_NAME"; then
        systemctl start "$LCC_SERVICE_NAME" || true
    fi
    if ! lcc_wait_for_internal_health "$app_port" 120 0.5; then
        journalctl -u "$LCC_SERVICE_NAME" -n 80 --no-pager >&2 || true
        lcc_die "Core service did not pass loopback health; retained release/config for safe retry."
    fi
    systemctl start "$LCC_BACKUP_TIMER_NAME"
    systemctl is-active --quiet "$LCC_SERVICE_NAME" || lcc_die "Core service is not active."
fi
# A gateway failure must retain the healthy Core for a safe explicit retry.
activated_here=0
if test "$test_root" = /; then
    printf 'LCC Core installed and internally healthy: %s\nLoopback endpoint: 127.0.0.1:%s\n' \
        "$release_id" "$app_port"
else
    printf 'Isolated Core layout prepared (runtime and health not executed): %s\nConfigured loopback port: %s\n' \
        "$release_id" "$app_port"
fi
if test "$core_only" -eq 1; then
    echo 'Public gateway/TLS: pending (Core-only development path).'
elif test "$gateway" = external; then
    lcc_write_gateway_state external "$gateway_state" "$(id -u)"
    lcc_report_external_gateway
else
    if lcc_gateway_listener_conflict && test "$gateway_explicit" -eq 0 &&
        test "$non_interactive" -eq 0; then
        exec 3<>/dev/tty ||
            lcc_die "A public listener conflicts with managed Caddy; choose --gateway external explicitly."
        printf 'Another service owns port 80/443. Use an external gateway for LCC Core? [y/N]: ' >&3
        IFS= read -r answer <&3 || lcc_die "Unable to read gateway choice."
        case "${answer,,}" in
            y|yes)
                lcc_write_gateway_state external "$gateway_state" "$(id -u)"
                lcc_report_external_gateway
                exit 0 ;;
            *) lcc_die "Managed gateway aborted; Core remains healthy." ;;
        esac
    fi
    lcc_install_managed_caddy "$release_directory" "${saved_gateway:-}"
fi
