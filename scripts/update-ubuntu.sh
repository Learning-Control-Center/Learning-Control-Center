#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIRECTORY="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/deploy-common.sh
source "$SCRIPT_DIRECTORY/deploy-common.sh"

usage() {
    cat <<'EOF'
Usage:
  sudo update-ubuntu.sh apply --source DIR --release-id ID
  sudo update-ubuntu.sh rollback --to ID [--database-backup FILE --confirm-database-replacement]

Updates always target a deliberate local source/release identity. No command
fetches or selects a remote "latest" release.
EOF
}

test "$(id -u)" -eq 0 || lcc_die "Production updates must run as root."
action="${1:-}"
test -n "$action" || { usage; exit 2; }
shift
lcc_acquire_deployment_lock

test -r "$LCC_ENVIRONMENT_FILE" || lcc_die "Missing $LCC_ENVIRONMENT_FILE."
test -L "$LCC_CURRENT_RELEASE" || lcc_die "No active LCC release is installed."
lcc_load_environment "$LCC_ENVIRONMENT_FILE"
lcc_validate_environment "$LCC_CURRENT_RELEASE"
old_release="$(readlink -f "$LCC_CURRENT_RELEASE")"
old_release_id="$(tr -d '\r\n' < "$old_release/RELEASE_ID")"

atomic_activate() {
    local release_directory="$1"
    local next_link="$LCC_APPLICATION_ROOT/.current.$$.next"
    ln -s "$release_directory" "$next_link"
    mv -Tf "$next_link" "$LCC_CURRENT_RELEASE"
}

render_caddy() {
    local release_directory="$1"
    local hostname temporary
    hostname="$(lcc_public_hostname)"
    temporary="$(mktemp)"
    sed -e "s|@@LCC_PUBLIC_HOST@@|$hostname|g" \
        -e "s|@@LCC_FRONTEND_ROOT@@|$LCC_CURRENT_RELEASE/frontend/dist|g" \
        "$release_directory/deploy/Caddyfile.template" > "$temporary"
    install -m 0644 "$temporary" "$LCC_CADDY_SITE"
    rm -f -- "$temporary"
    caddy fmt --overwrite "$LCC_CADDY_SITE"
    caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
}

install_units() {
    local release_directory="$1"
    install -m 0644 "$release_directory/deploy/learning-control-center.service" \
        /etc/systemd/system/learning-control-center.service
    install -m 0644 "$release_directory/deploy/learning-control-center-backup.service" \
        /etc/systemd/system/learning-control-center-backup.service
    install -m 0644 "$release_directory/deploy/learning-control-center-backup.timer" \
        /etc/systemd/system/learning-control-center-backup.timer
    systemctl daemon-reload
}

release_head() {
    local release_directory="$1"
    PYTHONPATH="$release_directory/backend" "$release_directory/.venv/bin/python" -c \
        'from app.ops import expected_revision; print(expected_revision())'
}

database_revision() {
    local path="${LCC_DATABASE_URL#sqlite:///}"
    sqlite3 "$path" 'SELECT version_num FROM alembic_version;'
}

create_offline_backup() {
    local release_directory="$1"
    local purpose="$2"
    lcc_run_as_service_user "$release_directory" env LCC_BACKUP_PURPOSE="$purpose" \
        "$release_directory/scripts/operational-backup.sh"
    find "$LCC_BACKUP_DIRECTORY" -maxdepth 1 -type f -name "lcc-$purpose-*.sqlite3" \
        -printf '%T@ %p\n' | sort -nr | sed -n '1s/^[^ ]* //p'
}

stage_release() {
    local source_root="$1"
    local release_id="$2"
    local destination="$LCC_APPLICATION_ROOT/releases/$release_id"
    local source_revision
    lcc_validate_release_id "$release_id"
    source_root="$(readlink -f "$source_root")"
    test -f "$source_root/requirements-production.lock" || lcc_die "Candidate lacks production constraints."
    test -f "$source_root/frontend/package-lock.json" || lcc_die "Candidate lacks frontend lockfile."
    test -f "$source_root/deploy/Caddyfile.template" || lcc_die "Candidate lacks deployment assets."
    if git -C "$source_root" rev-parse --verify HEAD >/dev/null 2>&1 && \
        test -n "$(git -C "$source_root" status --porcelain --untracked-files=all)"; then
        lcc_die "Candidate source must have a clean Git worktree."
    fi
    source_revision="$(lcc_source_revision "$source_root")"
    if test -e "$destination"; then
        if test -f "$destination/.installing"; then
            rm -rf -- "$destination"
        else
            test -f "$destination/RELEASE_ID" && test -f "$destination/SOURCE_REVISION" || \
                lcc_die "Incomplete candidate release exists: $destination"
            test "$(tr -d '\r\n' < "$destination/RELEASE_ID")" = "$release_id" || \
                lcc_die "Candidate release identity mismatch."
            test "$(tr -d '\r\n' < "$destination/SOURCE_REVISION")" = "$source_revision" || \
                lcc_die "Candidate release ID already refers to another source revision."
            test -x "$destination/.venv/bin/python" && \
                test -f "$destination/frontend/dist/index.html" || \
                lcc_die "Existing candidate release is incomplete."
            printf '%s\n' "$destination"
            return
        fi
    fi
    lcc_note "Staging candidate $release_id ($source_revision)"
    local staging="$LCC_APPLICATION_ROOT/releases/.${release_id}.staging.$$"
    test ! -e "$staging" || lcc_die "Candidate staging directory already exists."
    install -d -m 0755 "$staging"
    trap 'status=$?; rm -rf -- "${staging:-}"; if test -f "${destination:-}/.installing"; then rm -rf -- "$destination"; fi; exit "$status"' ERR
    lcc_copy_release_source "$source_root" "$staging" tracked
    : > "$staging/.installing"
    mv "$staging" "$destination"
    python3 -m venv "$destination/.venv"
    "$destination/.venv/bin/python" -m pip install \
        --constraint "$destination/requirements-production.lock" setuptools wheel >&2
    "$destination/.venv/bin/python" -m pip install \
        --constraint "$destination/requirements-production.lock" --no-build-isolation \
        --editable "$destination" >&2
    "$destination/.venv/bin/python" -m pip check >&2
    npm --prefix "$destination/frontend" ci >&2
    npm --prefix "$destination/frontend" run build >&2
    test -f "$destination/frontend/dist/index.html" || lcc_die "Candidate frontend build is missing."
    printf '%s\n' "$release_id" > "$destination/RELEASE_ID"
    printf '%s\n' "$source_revision" > "$destination/SOURCE_REVISION"
    chown -R "root:$LCC_SERVICE_GROUP" "$destination"
    chmod -R u=rwX,g=rX,o= "$destination"
    chmod o+x "$destination" "$destination/frontend"
    find "$destination/frontend/dist" -type d -exec chmod 0755 {} +
    find "$destination/frontend/dist" -type f -exec chmod 0644 {} +
    rm -f -- "$destination/.installing"
    trap - ERR
    printf '%s\n' "$destination"
}

stop_for_transition() {
    systemctl stop "$LCC_BACKUP_TIMER_NAME"
    if systemctl is-active --quiet "$LCC_BACKUP_SERVICE_NAME"; then
        systemctl start "$LCC_BACKUP_TIMER_NAME"
        lcc_die "A backup is running; retry after it finishes."
    fi
    systemctl stop "$LCC_BACKUP_SERVICE_NAME"
    systemctl stop "$LCC_SERVICE_NAME"
    lcc_require_inactive_service
}

start_and_verify() {
    systemctl start "$LCC_SERVICE_NAME"
    systemctl reload caddy.service
    if ! lcc_wait_for_health 60 0.5; then
        journalctl -u "$LCC_SERVICE_NAME" -n 80 --no-pager >&2 || true
        return 1
    fi
    systemctl start "$LCC_BACKUP_TIMER_NAME"
}

if test "$action" = "apply"; then
    source_root=""
    release_id=""
    while test "$#" -gt 0; do
        case "$1" in
            --source) source_root="${2:?Missing --source value}"; shift 2 ;;
            --release-id) release_id="${2:?Missing --release-id value}"; shift 2 ;;
            *) usage >&2; lcc_die "Unknown apply option: $1" ;;
        esac
    done
    test -n "$source_root" && test -n "$release_id" || \
        lcc_die "apply requires --source and --release-id."
    test "$release_id" != "$old_release_id" || lcc_die "The requested release is already active."
    candidate="$(stage_release "$source_root" "$release_id")"
    candidate_head="$(release_head "$candidate")"
    old_database_head="$(database_revision)"
    transition_started=0
    pre_update_backup=""
    rollback_failed_apply() {
        status=$?
        test "$transition_started" -eq 1 || exit "$status"
        trap - EXIT
        set +e
        lcc_note "Update failed; restoring release $old_release_id"
        systemctl stop "$LCC_BACKUP_TIMER_NAME"
        systemctl stop "$LCC_BACKUP_SERVICE_NAME"
        systemctl stop "$LCC_SERVICE_NAME"
        atomic_activate "$old_release"
        install_units "$old_release"
        render_caddy "$old_release"
        if test -n "$pre_update_backup" && test "$candidate_head" != "$old_database_head"; then
            lcc_run_as_service_user "$old_release" "$old_release/.venv/bin/lcc-ops" \
                restore --from "$pre_update_backup"
        fi
        systemctl start "$LCC_SERVICE_NAME"
        systemctl start "$LCC_BACKUP_TIMER_NAME"
        systemctl reload caddy.service
        lcc_wait_for_health 60 0.5 || true
        exit "$status"
    }
    trap rollback_failed_apply EXIT
    stop_for_transition
    transition_started=1
    pre_update_backup="$(create_offline_backup "$old_release" pre-update)"
    test -n "$pre_update_backup" || lcc_die "Pre-update backup was not created."
    lcc_note "Migrating database from $old_database_head to $candidate_head"
    lcc_run_as_service_user "$candidate" "$candidate/.venv/bin/python" -c \
        'from app.database import run_migrations; run_migrations()'
    atomic_activate "$candidate"
    install_units "$candidate"
    render_caddy "$candidate"
    start_and_verify
    deployment_record="$LCC_DATA_DIRECTORY/deployment-$release_id.env"
    cat > "$deployment_record" <<EOF
release_id=$release_id
source_revision=$(tr -d '\r\n' < "$candidate/SOURCE_REVISION")
previous_release_id=$old_release_id
previous_database_revision=$old_database_head
new_database_revision=$candidate_head
pre_update_backup=$pre_update_backup
activated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
    chmod 0600 "$deployment_record"
    chown "$LCC_SERVICE_USER:$LCC_SERVICE_GROUP" "$deployment_record"
    trap - EXIT
    echo "Activated release $release_id; pre-update backup: $pre_update_backup"
elif test "$action" = "rollback"; then
    target_id=""
    database_backup=""
    confirm_database_replacement=0
    while test "$#" -gt 0; do
        case "$1" in
            --to) target_id="${2:?Missing --to value}"; shift 2 ;;
            --database-backup) database_backup="${2:?Missing --database-backup value}"; shift 2 ;;
            --confirm-database-replacement) confirm_database_replacement=1; shift ;;
            *) usage >&2; lcc_die "Unknown rollback option: $1" ;;
        esac
    done
    test -n "$target_id" || lcc_die "rollback requires --to."
    lcc_validate_release_id "$target_id"
    target_release="$LCC_APPLICATION_ROOT/releases/$target_id"
    test -x "$target_release/.venv/bin/python" || lcc_die "Target release is not installed."
    current_head="$(database_revision)"
    target_head="$(release_head "$target_release")"
    if test "$target_head" != "$current_head"; then
        test -n "$database_backup" && test "$confirm_database_replacement" -eq 1 || \
            lcc_die "Rollback crosses schema revisions; provide --database-backup and --confirm-database-replacement."
        database_backup="$(readlink -f "$database_backup")"
        test -f "$database_backup" || lcc_die "Rollback database backup does not exist."
        runuser -u "$LCC_SERVICE_USER" -- test -r "$database_backup" || \
            lcc_die "The lcc service user cannot read the rollback backup."
    fi
    transition_started=0
    pre_rollback_backup=""
    rollback_failed_rollback() {
        status=$?
        test "$transition_started" -eq 1 || exit "$status"
        trap - EXIT
        set +e
        lcc_note "Rollback failed; restoring original release $old_release_id"
        systemctl stop "$LCC_BACKUP_TIMER_NAME"
        systemctl stop "$LCC_BACKUP_SERVICE_NAME"
        systemctl stop "$LCC_SERVICE_NAME"
        atomic_activate "$old_release"
        install_units "$old_release"
        render_caddy "$old_release"
        if test -n "$pre_rollback_backup" && test "$target_head" != "$current_head"; then
            lcc_run_as_service_user "$old_release" "$old_release/.venv/bin/lcc-ops" \
                restore --from "$pre_rollback_backup"
        fi
        systemctl start "$LCC_SERVICE_NAME"
        systemctl start "$LCC_BACKUP_TIMER_NAME"
        systemctl reload caddy.service
        lcc_wait_for_health 60 0.5 || true
        exit "$status"
    }
    trap rollback_failed_rollback EXIT
    stop_for_transition
    transition_started=1
    pre_rollback_backup="$(create_offline_backup "$old_release" pre-rollback)"
    test -n "$pre_rollback_backup" || lcc_die "Pre-rollback backup was not created."
    atomic_activate "$target_release"
    install_units "$target_release"
    render_caddy "$target_release"
    if test "$target_head" != "$current_head"; then
        lcc_run_as_service_user "$target_release" "$target_release/.venv/bin/lcc-ops" \
            restore --from "$database_backup"
    fi
    start_and_verify
    trap - EXIT
    echo "Rolled back to $target_id; pre-rollback backup: $pre_rollback_backup"
else
    usage >&2
    lcc_die "Unknown action: $action"
fi
