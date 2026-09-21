#!/usr/bin/env bash
set -euo pipefail
umask 022

SCRIPT_DIRECTORY="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIRECTORY/.." && pwd)"
# shellcheck source=scripts/deploy-common.sh
source "$SCRIPT_DIRECTORY/deploy-common.sh"

usage() {
    cat <<'EOF'
Usage: sudo ./scripts/install-ubuntu.sh --domain HOST --channel CHANNEL --release-id ID \
  --source-revision SHA --source-repository URL --source-ref REF --source-origin URL \
  --env-file FILE [options]

Options:
  --source DIR          Source checkout/release (default: repository containing this script)
  --replace-env         Replace an existing production environment file
  --no-start            Enable units but do not start them
  --dry-run             Print privileged actions without changing the machine
  --root DIR            Install beneath an isolated test root (implies no systemctl/user changes)
  --skip-build          Copy layout without building the Python environment (isolated tests only)
  --skip-prerequisites  Skip OS/tool checks (isolated tests only)
EOF
}

domain=""
release_channel=""
release_id=""
declared_source_revision=""
source_repository=""
source_ref=""
source_origin=""
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
        --channel) release_channel="${2:?Missing --channel value}"; shift 2 ;;
        --release-id) release_id="${2:?Missing --release-id value}"; shift 2 ;;
        --source-revision) declared_source_revision="${2:?Missing --source-revision value}"; shift 2 ;;
        --source-repository) source_repository="${2:?Missing --source-repository value}"; shift 2 ;;
        --source-ref) source_ref="${2:?Missing --source-ref value}"; shift 2 ;;
        --source-origin) source_origin="${2:?Missing --source-origin value}"; shift 2 ;;
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
if test "$install_root" != "/"; then
    release_channel="${release_channel:-stable}"
    declared_source_revision="${declared_source_revision:-$(git -C "$source_root" rev-parse HEAD)}"
    source_repository="${source_repository:-https://example.invalid/Learning-Control-Center.git}"
    source_ref="${source_ref:-refs/tags/$release_id}"
    source_origin="${source_origin:-https://example.invalid/releases/download}"
fi
test -n "$release_channel" || lcc_die "--channel is required."
test -n "$declared_source_revision" || lcc_die "--source-revision is required."
test -n "$source_repository" || lcc_die "--source-repository is required."
test -n "$source_ref" || lcc_die "--source-ref is required."
test -n "$source_origin" || lcc_die "--source-origin is required."
lcc_validate_release_channel "$release_channel"
lcc_validate_release_id "$release_id"
lcc_validate_source_revision "$declared_source_revision"
lcc_validate_public_hostname "$domain"
case "$release_channel" in
    stable)
        if test "$install_root" = "/"; then
            [[ "$release_id" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([+-][0-9A-Za-z.-]+)?$ ]] || \
                lcc_die "Stable release ID must be a semantic version tag."
        fi
        test "$source_ref" = "refs/tags/$release_id" || \
            lcc_die "Stable source ref must match the release tag."
        ;;
    main)
        test "$release_id" = "main-$declared_source_revision" || \
            lcc_die "Main release ID must be main-<full-source-SHA>."
        test "$source_ref" = "refs/heads/main" || lcc_die "Main source ref must be refs/heads/main."
        ;;
esac
if test "$install_root" = "/"; then
    lcc_validate_https_url "$source_repository" "Source repository"
    lcc_validate_https_url "$source_origin" "Source origin"
    lcc_validate_source_metadata "$release_channel" "$source_repository" "$source_origin"
fi
source_root="$(readlink -f "$source_root")"
test ! -L "$environment_source" || lcc_die "Environment source must not be a symbolic link."
test -e "$environment_source" || lcc_die "Environment source does not exist."
environment_source="$(readlink -f "$environment_source")"
test -f "$source_root/pyproject.toml" || lcc_die "Source does not contain pyproject.toml."
test -f "$source_root/frontend/package-lock.json" || lcc_die "Source lacks frontend lockfile."
test -f "$source_root/requirements-production.lock" || lcc_die "Source lacks production constraints."
test -f "$source_root/frontend/dist/index.html" || lcc_die "Source lacks the packaged frontend."
expected_environment_owner=0
if test "$install_root" != "/"; then
    expected_environment_owner="$(id -u)"
fi
allow_installed_environment=0
expected_environment_target="${install_root%/}$LCC_ENVIRONMENT_FILE"
if test "$environment_source" = "$(readlink -m -- "$expected_environment_target")"; then
    allow_installed_environment=1
fi
lcc_validate_environment_file_security "$environment_source" "$allow_installed_environment" \
    "$expected_environment_owner"

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
update_entrypoint="$(lcc_prefixed_path "$install_root" "$LCC_UPDATE_ENTRYPOINT")"
release_directory="$application_root/releases/$release_id"

if test -L "$current_release"; then
    active_release="$(readlink -f "$current_release")"
    test "$active_release" = "$release_directory" || \
        lcc_die "Another release is already active; use update-ubuntu.sh instead of the installer."
fi

lcc_load_environment "$environment_source"
lcc_validate_environment "$current_release"
app_port="$(lcc_effective_app_port)"
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
    # shellcheck disable=SC1091
    source /etc/os-release
    test "${ID:-}" = "ubuntu" && test "${VERSION_ID:-}" = "24.04" || \
        lcc_die "The supported production baseline is Ubuntu 24.04 LTS."
    if test "$dry_run" -eq 1; then
        lcc_note "DRY-RUN: runtime prerequisites were planned by bootstrap and would be verified before installation."
    else
        lcc_verify_runtime_prerequisites
    fi
fi
lcc_verify_frontend_artifact "$source_root"
if test "$install_root" = "/" && test "$dry_run" -eq 0 && \
    ! lcc_app_port_is_available "$app_port"; then
    if test -L "$current_release" && lcc_app_port_owned_by_service "$app_port"; then
        lcc_note "Internal application port $app_port is already owned by the active LCC service."
    else
        lcc_describe_app_port_listener "$app_port"
        lcc_die "Internal application port $app_port is already occupied."
    fi
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

git_source_root="$(git -C "$source_root" rev-parse --show-toplevel 2>/dev/null || true)"
if test -n "$git_source_root" && test "$(readlink -f "$git_source_root")" = "$source_root"; then
    source_revision="$(git -C "$source_root" rev-parse HEAD)"
    test "$source_revision" = "$declared_source_revision" || \
        lcc_die "Git source HEAD does not match --source-revision."
else
    test -f "$source_root/SOURCE_REVISION" || \
        lcc_die "Artifact source lacks its verified SOURCE_REVISION."
    source_revision="$(tr -d '\r\n' < "$source_root/SOURCE_REVISION")"
    test "$source_revision" = "$declared_source_revision" || \
        lcc_die "Artifact source revision does not match --source-revision."
fi
release_is_complete=0
if test -f "$release_directory/RELEASE_ID" && test -f "$release_directory/SOURCE_REVISION"; then
    if test "$(tr -d '\r\n' < "$release_directory/RELEASE_ID")" = "$release_id" && \
        test "$(tr -d '\r\n' < "$release_directory/SOURCE_REVISION")" = "$source_revision" && \
        test "$(lcc_release_channel "$release_directory")" = "$release_channel"; then
        lcc_validate_release_manifest "$release_directory" "$release_channel" "$release_id" \
            "$source_repository" "$source_ref" "$source_revision" "$source_origin"
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
    fi
    if test "$dry_run" -eq 0; then
        lcc_verify_frontend_artifact "$release_directory"
    fi
    if test "$dry_run" -eq 0; then
        printf '%s\n' "$release_id" > "$release_directory/RELEASE_ID"
        printf '%s\n' "$release_channel" > "$release_directory/RELEASE_CHANNEL"
        printf '%s\n' "$source_revision" > "$release_directory/SOURCE_REVISION"
        cat > "$release_directory/RELEASE_MANIFEST" <<EOF
metadata_version=1
channel=$release_channel
release_id=$release_id
source_repository=$source_repository
source_ref=$source_ref
source_revision=$source_revision
source_origin=$source_origin
EOF
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
    lcc_verify_frontend_artifact "$release_directory"
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
run install -m 0755 "$asset_release/deploy/learning-control-center-update.sh" \
    "$update_entrypoint"

rendered_caddy="$(mktemp)"
caddy_backup_directory=""
if test "$install_root" = "/" && test "$dry_run" -eq 0; then
    caddy_backup_directory="$(mktemp -d)"
    if test -f "$caddy_site"; then
        cp -a -- "$caddy_site" "$caddy_backup_directory/site"
    fi
    if test -f "$caddy_main"; then
        cp -a -- "$caddy_main" "$caddy_backup_directory/main"
    fi
fi
cleanup_caddy_temporary_files() {
    rm -f -- "$rendered_caddy"
    if test -n "$caddy_backup_directory"; then
        rm -rf -- "$caddy_backup_directory"
    fi
}
trap cleanup_caddy_temporary_files EXIT
lcc_render_caddy_site "$asset_release" "$current_release/frontend/dist" "$rendered_caddy"
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

if test "$install_root" = "/" && test "$dry_run" -eq 0; then
    lcc_format_validate_or_restore_caddy "$caddy_site" "$caddy_main" \
        "$caddy_backup_directory"
elif test "$install_root" = "/"; then
    echo "DRY-RUN: caddy fmt/validate before release activation"
fi

next_link="$application_root/.current.$$.next"
run ln -s "$release_directory" "$next_link"
run mv -Tf "$next_link" "$current_release"
run ln -sfn "$current_release/scripts/lcc-admin" "$admin_link"

if test "$install_root" = "/" && test "$dry_run" -eq 0; then
    systemctl daemon-reload
    systemctl enable caddy.service "$LCC_SERVICE_NAME" "$LCC_BACKUP_TIMER_NAME"
    if test "$start_services" -eq 1; then
        systemctl reload-or-restart caddy.service
        systemctl restart "$LCC_SERVICE_NAME"
        if ! lcc_wait_for_internal_health "$app_port" 120 0.5; then
            journalctl -u "$LCC_SERVICE_NAME" -n 80 --no-pager >&2 || true
            lcc_die "Installation completed, but LCC did not become healthy on internal port $app_port."
        fi
        if ! lcc_wait_for_health 120 0.5; then
            journalctl -u "$LCC_SERVICE_NAME" -n 80 --no-pager >&2 || true
            lcc_die "Installation completed, but the public HTTPS health check failed."
        fi
        systemctl start "$LCC_BACKUP_TIMER_NAME"
    fi
elif test "$install_root" = "/"; then
    echo "DRY-RUN: systemctl daemon-reload and enable services"
fi

if test "$dry_run" -eq 0; then
    deployment_record="$data_directory/deployment-$release_id.env"
    cat > "$deployment_record" <<EOF
channel=$release_channel
release_id=$release_id
source_repository=$source_repository
source_ref=$source_ref
source_revision=$source_revision
source_origin=$source_origin
activated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
    chmod 0600 "$deployment_record"
    if test "$install_root" = "/"; then
        chown "$LCC_SERVICE_USER:$LCC_SERVICE_GROUP" "$deployment_record"
    fi
fi

cat <<EOF

Learning Control Center release $release_id is installed.
Internal application endpoint: 127.0.0.1:$app_port
Channel: $release_channel
Source revision: $source_revision
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
  sudo $LCC_UPDATE_ENTRYPOINT
EOF
