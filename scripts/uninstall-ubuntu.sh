#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIRECTORY="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/deploy-common.sh
source "$SCRIPT_DIRECTORY/deploy-common.sh"

usage() {
    cat <<'EOF'
Usage: sudo ./scripts/uninstall-ubuntu.sh [options]

Default behavior removes services, application releases, Caddy integration,
administrator command, and application configuration while preserving the
SQLite data directory, operational backups, and lcc account.

Options:
  --purge-data              Also remove database and backup directories
  --confirm-purge=DELETE-LCC-DATA
                            Mandatory second opt-in for --purge-data
  --dry-run                 Print actions without changing the machine
  --root DIR                Operate under an isolated test root
EOF
}

purge_data=0
purge_confirmation=""
dry_run=0
install_root="/"
while test "$#" -gt 0; do
    case "$1" in
        --purge-data) purge_data=1; shift ;;
        --confirm-purge=*) purge_confirmation="${1#*=}"; shift ;;
        --dry-run) dry_run=1; shift ;;
        --root) install_root="${2:?Missing --root value}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; lcc_die "Unknown option: $1" ;;
    esac
done

if test "$purge_data" -eq 1 && test "$purge_confirmation" != "DELETE-LCC-DATA"; then
    lcc_die "--purge-data requires --confirm-purge=DELETE-LCC-DATA."
fi
if test "$install_root" != "/"; then
    [[ "$install_root" = /* ]] || lcc_die "--root must be absolute."
    install_root="$(readlink -m -- "$install_root")"
    test "$install_root" != "/" && [[ "$install_root" = /tmp/* ]] || \
        lcc_die "--root must resolve beneath /tmp and must not resolve to /tmp itself or /."
elif test "$dry_run" -eq 0 && test "$(id -u)" -ne 0; then
    lcc_die "Production uninstall must run as root."
fi
if test "$install_root" = "/" && test "$dry_run" -eq 0; then
    lcc_acquire_deployment_lock
fi

application_root="$(lcc_prefixed_path "$install_root" "$LCC_APPLICATION_ROOT")"
environment_file="$(lcc_prefixed_path "$install_root" "$LCC_ENVIRONMENT_FILE")"
data_directory="$(lcc_prefixed_path "$install_root" "$LCC_DATA_DIRECTORY")"
backup_directory="$(lcc_prefixed_path "$install_root" "$LCC_BACKUP_DIRECTORY_DEFAULT")"
caddy_site="$(lcc_prefixed_path "$install_root" "$LCC_CADDY_SITE")"
systemd_directory="$(lcc_prefixed_path "$install_root" "/etc/systemd/system")"
admin_link="$(lcc_prefixed_path "$install_root" "/usr/local/sbin/lcc-admin")"

run() {
    if test "$dry_run" -eq 1; then
        printf 'DRY-RUN:'
        printf ' %q' "$@"
        printf '\n'
    else
        "$@"
    fi
}

if test "$install_root" = "/"; then
    for unit_name in "$LCC_BACKUP_TIMER_NAME" "$LCC_SERVICE_NAME"; do
        if test "$dry_run" -eq 1 || \
            test "$(systemctl show -p LoadState --value "$unit_name" 2>/dev/null || true)" != "not-found"; then
            run systemctl disable --now "$unit_name"
        fi
    done
    if test "$dry_run" -eq 1 || \
        test "$(systemctl show -p LoadState --value "$LCC_BACKUP_SERVICE_NAME" 2>/dev/null || true)" != "not-found"; then
        run systemctl stop "$LCC_BACKUP_SERVICE_NAME"
    fi
fi
run rm -f -- \
    "$systemd_directory/learning-control-center.service" \
    "$systemd_directory/learning-control-center-backup.service" \
    "$systemd_directory/learning-control-center-backup.timer" \
    "$caddy_site" "$admin_link" "$environment_file"
run rm -rf -- "$application_root"

if test "$purge_data" -eq 1; then
    test "$data_directory" != "/" && test "$backup_directory" != "/" || \
        lcc_die "Refusing unsafe purge target."
    run rm -rf -- "$data_directory" "$backup_directory"
    if test "$install_root" = "/" && id "$LCC_SERVICE_USER" >/dev/null 2>&1; then
        run userdel "$LCC_SERVICE_USER"
    fi
    if test "$install_root" = "/" && getent group "$LCC_SERVICE_GROUP" >/dev/null; then
        run groupdel "$LCC_SERVICE_GROUP"
    fi
fi

if test "$install_root" = "/"; then
    run systemctl daemon-reload
    if command -v caddy >/dev/null && test -f /etc/caddy/Caddyfile; then
        run caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
        run systemctl reload caddy.service
    fi
fi

if test "$purge_data" -eq 1; then
    echo "LCC application, configuration, database, backups, and service account were removed."
else
    cat <<EOF
LCC application services and files were removed.
Preserved database directory: $data_directory
Preserved backup directory: $backup_directory
Preserved service account: $LCC_SERVICE_USER

Reinstall with the same paths to reuse the preserved state. To destroy it,
rerun with --purge-data --confirm-purge=DELETE-LCC-DATA.
EOF
fi
