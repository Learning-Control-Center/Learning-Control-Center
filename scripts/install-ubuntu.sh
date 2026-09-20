#!/usr/bin/env bash
set -euo pipefail
umask 022

SCRIPT_DIRECTORY="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIRECTORY/.." && pwd)"
# shellcheck source=scripts/deploy-common.sh
source "$SCRIPT_DIRECTORY/deploy-common.sh"

usage() {
    cat <<'EOF'
Usage: sudo ./scripts/install-ubuntu.sh --domain HOST --release-id ID --env-file FILE [options]

Options:
  --source DIR          Source checkout/release (default: repository containing this script)
  --replace-env         Replace an existing production environment file
  --no-start            Enable units but do not start them
  --dry-run             Print privileged actions without changing the machine
  --root DIR            Install beneath an isolated test root (implies no systemctl/user changes)
  --skip-build          Copy layout without Python/npm builds (isolated tests only)
  --skip-prerequisites  Skip OS/tool checks (isolated tests only)
EOF
}

domain=""
release_id=""
environment_source=""
source_root="$REPOSITORY_ROOT"
install_root="/"
replace_environment=0
start_services=1
dry_run=0
skip_build=0
skip_prerequisites=0

while test "$#" -gt 0; do
    case "$1" in
        --domain) domain="${2:?Missing --domain value}"; shift 2 ;;
        --release-id) release_id="${2:?Missing --release-id value}"; shift 2 ;;
        --env-file) environment_source="${2:?Missing --env-file value}"; shift 2 ;;
        --source) source_root="${2:?Missing --source value}"; shift 2 ;;
        --replace-env) replace_environment=1; shift ;;
        --no-start) start_services=0; shift ;;
        --dry-run) dry_run=1; shift ;;
        --root) install_root="${2:?Missing --root value}"; shift 2 ;;
        --skip-build) skip_build=1; shift ;;
        --skip-prerequisites) skip_prerequisites=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; lcc_die "Unknown option: $1" ;;
    esac
done

test -n "$domain" || lcc_die "--domain is required."
test -n "$release_id" || lcc_die "--release-id is required."
test -n "$environment_source" || lcc_die "--env-file is required."
lcc_validate_release_id "$release_id"
[[ "$domain" =~ ^[A-Za-z0-9.-]+$ && "$domain" == *.* ]] || \
    lcc_die "--domain must be a DNS hostname, not a URL."
source_root="$(readlink -f "$source_root")"
environment_source="$(readlink -f "$environment_source")"
test -f "$source_root/pyproject.toml" || lcc_die "Source does not contain pyproject.toml."
test -f "$source_root/frontend/package-lock.json" || lcc_die "Source lacks frontend lockfile."
test -f "$source_root/requirements-production.lock" || lcc_die "Source lacks production constraints."
test -f "$environment_source" || lcc_die "Environment source does not exist."

if test "$install_root" != "/"; then
    [[ "$install_root" = /* ]] || lcc_die "--root must be absolute."
    install_root="$(readlink -m -- "$install_root")"
    test "$install_root" != "/" && [[ "$install_root" = /tmp/* ]] || \
        lcc_die "--root must resolve beneath /tmp and must not resolve to /tmp itself or /."
    start_services=0
    skip_prerequisites=1
elif test "$dry_run" -eq 0 && test "$(id -u)" -ne 0; then
    lcc_die "Production installation must run as root."
fi
if test "$install_root" = "/" && { test "$skip_build" -eq 1 || test "$skip_prerequisites" -eq 1; }; then
    lcc_die "--skip-build and --skip-prerequisites are allowed only with an isolated --root."
fi
if test "$install_root" = "/" && test "$dry_run" -eq 0; then
    lcc_acquire_deployment_lock
fi
if test "$install_root" = "/" && git -C "$source_root" rev-parse --verify HEAD >/dev/null 2>&1 && \
    test -n "$(git -C "$source_root" status --porcelain --untracked-files=all)"; then
    lcc_die "Production installation requires a clean Git source tree."
fi

application_root="$(lcc_prefixed_path "$install_root" "$LCC_APPLICATION_ROOT")"
current_release="$(lcc_prefixed_path "$install_root" "$LCC_CURRENT_RELEASE")"
environment_target="$(lcc_prefixed_path "$install_root" "$LCC_ENVIRONMENT_FILE")"
data_directory="$(lcc_prefixed_path "$install_root" "$LCC_DATA_DIRECTORY")"
database_file="$(lcc_prefixed_path "$install_root" "$LCC_DATABASE_FILE")"
backup_directory="$(lcc_prefixed_path "$install_root" "$LCC_BACKUP_DIRECTORY_DEFAULT")"
caddy_site="$(lcc_prefixed_path "$install_root" "$LCC_CADDY_SITE")"
caddy_main="$(lcc_prefixed_path "$install_root" "/etc/caddy/Caddyfile")"
systemd_directory="$(lcc_prefixed_path "$install_root" "/etc/systemd/system")"
admin_link="$(lcc_prefixed_path "$install_root" "/usr/local/sbin/lcc-admin")"
release_directory="$application_root/releases/$release_id"

if test -L "$current_release"; then
    active_release="$(readlink -f "$current_release")"
    test "$active_release" = "$release_directory" || \
        lcc_die "Another release is already active; use update-ubuntu.sh instead of the installer."
fi

lcc_load_environment "$environment_source"
lcc_validate_environment "$current_release"
test "$(lcc_public_hostname)" = "$domain" || \
    lcc_die "--domain must match LCC_PUBLIC_ORIGIN and LCC_ALLOWED_HOSTS."
test "$LCC_DATABASE_URL" = "sqlite:///$database_file" || \
    lcc_die "LCC_DATABASE_URL must be sqlite:///$database_file for this installation layout."
test "$LCC_BACKUP_DIRECTORY" = "$backup_directory" || \
    lcc_die "LCC_BACKUP_DIRECTORY must be $backup_directory for this installation layout."

run() {
    if test "$dry_run" -eq 1; then
        printf 'DRY-RUN:'
        printf ' %q' "$@"
        printf '\n'
    else
        "$@"
    fi
}

if test "$skip_prerequisites" -eq 0; then
    # Ubuntu 24.04 LTS provides Python 3.12 directly. Node.js 22+ and Caddy may
    # come from their official repositories, but must already be installed.
    # shellcheck disable=SC1091
    source /etc/os-release
    test "${ID:-}" = "ubuntu" && test "${VERSION_ID:-}" = "24.04" || \
        lcc_die "The supported production baseline is Ubuntu 24.04 LTS."
    for command_name in python3 npm node caddy sqlite3 rsync tar curl systemctl runuser flock; do
        command -v "$command_name" >/dev/null || lcc_die "Missing prerequisite: $command_name"
    done
    python3 -c 'import sys; assert sys.version_info >= (3, 12)' || lcc_die "Python 3.12+ is required."
    node -e 'const major=Number(process.versions.node.split(".")[0]); process.exit([22, 24].includes(major) ? 0 : 1)' || \
        lcc_die "Node.js 22 LTS or 24 LTS is required to build the frontend."
fi

if test "$install_root" = "/"; then
    python3 - "$LCC_SERVICE_USER" "$LCC_SERVICE_GROUP" "$LCC_DATA_DIRECTORY" <<'PY'
import grp
import pwd
import sys

user_name, group_name, expected_home = sys.argv[1:]
try:
    group = grp.getgrnam(group_name)
except KeyError:
    group = None
try:
    user = pwd.getpwnam(user_name)
except KeyError:
    user = None
if user is not None and group is None:
    raise SystemExit("Existing lcc user has no dedicated lcc group")
if group is not None:
    if group.gr_gid >= 1000:
        raise SystemExit("Existing lcc group is not a system group")
    unexpected_members = set(group.gr_mem) - {user_name}
    unexpected_primary = {
        entry.pw_name for entry in pwd.getpwall()
        if entry.pw_gid == group.gr_gid and entry.pw_name != user_name
    }
    if unexpected_members or unexpected_primary:
        raise SystemExit("Existing lcc group is used by another account")
if user is not None:
    if user.pw_uid >= 1000:
        raise SystemExit("Existing lcc user is not a system account")
    if user.pw_gid != group.gr_gid:
        raise SystemExit("Existing lcc user does not use the dedicated lcc primary group")
    if user.pw_dir != expected_home or user.pw_shell != "/usr/sbin/nologin":
        raise SystemExit("Existing lcc account has unexpected home or login shell")
PY
    if ! getent group "$LCC_SERVICE_GROUP" >/dev/null; then
        run groupadd --system "$LCC_SERVICE_GROUP"
    fi
    if ! id "$LCC_SERVICE_USER" >/dev/null 2>&1; then
        run useradd --system --gid "$LCC_SERVICE_GROUP" --home-dir "$LCC_DATA_DIRECTORY" \
            --shell /usr/sbin/nologin "$LCC_SERVICE_USER"
    fi
    if id "$LCC_SERVICE_USER" >/dev/null 2>&1; then
        password_state="$(getent shadow "$LCC_SERVICE_USER" | cut -d: -f2)"
        [[ "$password_state" = \!* || "$password_state" = \** ]] || \
            lcc_die "Existing lcc service account must have a locked password."
    fi
fi

run install -d -m 0755 "$application_root" "$application_root/releases"
run install -d -m 0700 "$data_directory" "$backup_directory"
run install -d -m 0755 "$(dirname "$environment_target")" "$systemd_directory" \
    "$(dirname "$caddy_site")" "$(dirname "$admin_link")"
if test "$install_root" = "/"; then
    run chown "$LCC_SERVICE_USER:$LCC_SERVICE_GROUP" "$data_directory" "$backup_directory"
fi

source_revision="$(lcc_source_revision "$source_root")"
release_is_complete=0
if test -f "$release_directory/RELEASE_ID" && test -f "$release_directory/SOURCE_REVISION"; then
    if test "$(tr -d '\r\n' < "$release_directory/RELEASE_ID")" = "$release_id" && \
        test "$(tr -d '\r\n' < "$release_directory/SOURCE_REVISION")" = "$source_revision"; then
        release_is_complete=1
    else
        lcc_die "Release directory already exists with different identity: $release_directory"
    fi
elif test -e "$release_directory"; then
    if test -f "$release_directory/.installing"; then
        run rm -rf -- "$release_directory"
    else
        lcc_die "Incomplete release directory exists without an installer marker: $release_directory"
    fi
fi

if test "$release_is_complete" -eq 0; then
    lcc_note "Staging immutable release $release_id"
    staging_directory="$application_root/releases/.${release_id}.staging.$$"
    if test "$dry_run" -eq 0; then
        test ! -e "$staging_directory" || lcc_die "Staging directory already exists."
    fi
    cleanup_failed_install() {
        local status=$?
        if test "$dry_run" -eq 0; then
            rm -rf -- "${staging_directory:-}"
            if test -f "${release_directory:-}/.installing"; then
                rm -rf -- "$release_directory"
            fi
        fi
        exit "$status"
    }
    trap cleanup_failed_install ERR
    run install -d -m 0755 "$staging_directory"
    if test "$dry_run" -eq 1; then
        echo "DRY-RUN: copy tracked/sanitized release source to $staging_directory"
    elif test "$install_root" = "/"; then
        lcc_copy_release_source "$source_root" "$staging_directory" tracked
    else
        lcc_copy_release_source "$source_root" "$staging_directory" sanitized
    fi
    if test "$dry_run" -eq 0; then
        : > "$staging_directory/.installing"
    fi
    run mv "$staging_directory" "$release_directory"
    if test "$skip_build" -eq 0; then
        run python3 -m venv "$release_directory/.venv"
        run "$release_directory/.venv/bin/python" -m pip install \
            --constraint "$release_directory/requirements-production.lock" \
            setuptools wheel
        run "$release_directory/.venv/bin/python" -m pip install \
            --constraint "$release_directory/requirements-production.lock" \
            --no-build-isolation \
            --editable "$release_directory"
        run "$release_directory/.venv/bin/python" -m pip check
        run npm --prefix "$release_directory/frontend" ci
        run npm --prefix "$release_directory/frontend" run build
        if test "$dry_run" -eq 0; then
            test -f "$release_directory/frontend/dist/index.html" || \
                lcc_die "Frontend build did not produce index.html."
        fi
    fi
    if test "$dry_run" -eq 0; then
        printf '%s\n' "$release_id" > "$release_directory/RELEASE_ID"
        printf '%s\n' "$source_revision" > "$release_directory/SOURCE_REVISION"
    fi
    if test "$install_root" = "/"; then
        run chown -R "root:$LCC_SERVICE_GROUP" "$release_directory"
        run chmod -R u=rwX,g=rX,o= "$release_directory"
        run chmod o+x "$release_directory" "$release_directory/frontend"
        if test "$skip_build" -eq 0; then
            run find "$release_directory/frontend/dist" -type d -exec chmod 0755 {} +
            run find "$release_directory/frontend/dist" -type f -exec chmod 0644 {} +
        fi
    else
        run chmod -R go+rX,go-w "$release_directory"
    fi
    if test "$dry_run" -eq 0; then
        rm -f -- "$release_directory/.installing"
    fi
    trap - ERR
fi
asset_release="$release_directory"
if test "$dry_run" -eq 1; then
    asset_release="$source_root"
elif test "$skip_build" -eq 0; then
    test -x "$release_directory/.venv/bin/python" || lcc_die "Release Python runtime is missing."
    test -f "$release_directory/frontend/dist/index.html" || lcc_die "Release frontend build is missing."
fi

if test -e "$environment_target" && test "$replace_environment" -eq 0; then
    cmp -s "$environment_source" "$environment_target" || \
        lcc_die "$environment_target already exists; use --replace-env to replace it deliberately."
else
    run install -m 0640 "$environment_source" "$environment_target"
fi
if test "$install_root" = "/"; then
    run chown "root:$LCC_SERVICE_GROUP" "$environment_target"
fi

lcc_note "Installing systemd and Caddy assets"
run install -m 0644 "$asset_release/deploy/learning-control-center.service" \
    "$systemd_directory/learning-control-center.service"
run install -m 0644 "$asset_release/deploy/learning-control-center-backup.service" \
    "$systemd_directory/learning-control-center-backup.service"
run install -m 0644 "$asset_release/deploy/learning-control-center-backup.timer" \
    "$systemd_directory/learning-control-center-backup.timer"

rendered_caddy="$(mktemp)"
trap 'rm -f -- "$rendered_caddy"' EXIT
sed -e "s|@@LCC_PUBLIC_HOST@@|$domain|g" \
    -e "s|@@LCC_FRONTEND_ROOT@@|$current_release/frontend/dist|g" \
    "$asset_release/deploy/Caddyfile.template" > "$rendered_caddy"
run install -m 0644 "$rendered_caddy" "$caddy_site"

if test ! -e "$caddy_main"; then
    if test "$dry_run" -eq 0; then
        printf '%s\n' "$LCC_CADDY_IMPORT" > "$caddy_main"
        chmod 0644 "$caddy_main"
    else
        echo "DRY-RUN: create $caddy_main with $LCC_CADDY_IMPORT"
    fi
elif ! grep -Fqx "$LCC_CADDY_IMPORT" "$caddy_main"; then
    if test "$dry_run" -eq 0; then
        printf '\n# Learning Control Center sites\n%s\n' "$LCC_CADDY_IMPORT" >> "$caddy_main"
    else
        echo "DRY-RUN: append Caddy import to $caddy_main"
    fi
fi

next_link="$application_root/.current.$$.next"
run ln -s "$release_directory" "$next_link"
run mv -Tf "$next_link" "$current_release"
run ln -sfn "$current_release/scripts/lcc-admin" "$admin_link"

if test "$install_root" = "/" && test "$dry_run" -eq 0; then
    caddy fmt --overwrite "$caddy_site"
    caddy validate --config "$caddy_main" --adapter caddyfile
    systemctl daemon-reload
    systemctl enable caddy.service "$LCC_SERVICE_NAME" "$LCC_BACKUP_TIMER_NAME"
    if test "$start_services" -eq 1; then
        systemctl reload-or-restart caddy.service
        systemctl restart "$LCC_SERVICE_NAME"
        if ! lcc_wait_for_health 120 0.5; then
            journalctl -u "$LCC_SERVICE_NAME" -n 80 --no-pager >&2 || true
            lcc_die "Installation completed, but the public HTTPS health check failed."
        fi
        systemctl start "$LCC_BACKUP_TIMER_NAME"
    fi
elif test "$install_root" = "/"; then
    echo "DRY-RUN: caddy fmt/validate, systemctl daemon-reload, enable services"
fi

cat <<EOF

Learning Control Center release $release_id is installed.
Public URL: https://$domain
Application service: $LCC_SERVICE_NAME
Backup timer: $LCC_BACKUP_TIMER_NAME
Database: $database_file
Backups: $backup_directory

Create the first user in the browser with the configured bootstrap token, then run:
  sudo lcc-admin finalize-bootstrap

After installation succeeds, delete the temporary source copy of the environment file.

Inspect operation with:
  sudo lcc-admin status
  sudo lcc-admin backup-status
EOF
