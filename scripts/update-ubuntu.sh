#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIRECTORY="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/deploy-common.sh
source "$SCRIPT_DIRECTORY/deploy-common.sh"

usage() {
    cat <<'EOF'
Usage:
  sudo update-ubuntu.sh apply --source DIR --channel CHANNEL --release-id ID \
    --source-revision SHA --source-repository URL --source-ref REF --source-origin URL \
    [--confirm-channel-change]
  sudo update-ubuntu.sh rollback --to ID [--database-backup FILE --confirm-database-replacement]

Updates always target a deliberate local source/release identity. No command
fetches or selects a remote "latest" release.
EOF
}

test "$(id -u)" -eq 0 || lcc_die "Production updates must run as root."
lcc_verify_runtime_prerequisites
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
old_release_channel="$(lcc_release_channel "$old_release")"
old_source_revision="$(lcc_release_source_revision "$old_release")"

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
    "$LCC_CADDY_BINARY" fmt --overwrite "$LCC_CADDY_SITE"
    "$LCC_CADDY_BINARY" validate --config /etc/caddy/Caddyfile --adapter caddyfile
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
    local backup_path before_inventory after_inventory
    before_inventory="$(mktemp)"
    after_inventory="$(mktemp)"
    if ! find "$LCC_BACKUP_DIRECTORY" -maxdepth 1 -type f \
        -name "lcc-$purpose-*.sqlite3" -printf '%f\n' | LC_ALL=C sort > "$before_inventory"; then
        rm -f -- "$before_inventory" "$after_inventory"
        return 1
    fi
    if ! backup_path="$(
        lcc_run_as_service_user "$release_directory" env LCC_BACKUP_PURPOSE="$purpose" \
            "$release_directory/scripts/operational-backup.sh"
    )"; then
        rm -f -- "$before_inventory" "$after_inventory"
        return 1
    fi
    if test -z "$backup_path"; then
        # v1.0.0 creates a valid backup without printing its path. Its backup
        # service is already stopped, so require exactly one filename that did
        # not exist before this successful invocation. Never select an older file.
        if ! find "$LCC_BACKUP_DIRECTORY" -maxdepth 1 -type f \
            -name "lcc-$purpose-*.sqlite3" -printf '%f\n' | LC_ALL=C sort > "$after_inventory"; then
            rm -f -- "$before_inventory" "$after_inventory"
            return 1
        fi
        if ! backup_path="$(
            lcc_select_single_new_backup \
                "$LCC_BACKUP_DIRECTORY" "$before_inventory" "$after_inventory"
        )"; then
            rm -f -- "$before_inventory" "$after_inventory"
            return 1
        fi
    fi
    rm -f -- "$before_inventory" "$after_inventory"
    case "$backup_path" in
        "$LCC_BACKUP_DIRECTORY/lcc-$purpose-"*.sqlite3) ;;
        *) lcc_note "Backup command returned an unexpected path."; return 1 ;;
    esac
    test -f "$backup_path" && test -f "$backup_path.manifest" || {
        lcc_note "Backup command did not create the reported backup and manifest."
        return 1
    }
    printf '%s\n' "$backup_path"
}

recover_original_installation() {
    local database_backup="$1"
    local restore_database="$2"
    local recovery_failed=0 step_status

    (set -e; atomic_activate "$old_release")
    step_status=$?
    if test "$step_status" -ne 0; then
        lcc_note "CRITICAL: could not reactivate the original release."
        recovery_failed=1
    fi
    (set -e; install_units "$old_release")
    step_status=$?
    if test "$step_status" -ne 0; then
        lcc_note "CRITICAL: could not restore the original systemd units."
        recovery_failed=1
    fi
    (set -e; render_caddy "$old_release")
    step_status=$?
    if test "$step_status" -ne 0; then
        lcc_note "CRITICAL: could not restore the original Caddy configuration."
        recovery_failed=1
    fi
    if test "$restore_database" -eq 1; then
        if test -z "$database_backup"; then
            lcc_note "CRITICAL: no recovery database backup is available."
            recovery_failed=1
        else
            lcc_run_as_service_user "$old_release" "$old_release/.venv/bin/lcc-ops" \
                restore --from "$database_backup"
            step_status=$?
            if test "$step_status" -ne 0; then
                lcc_note "CRITICAL: database recovery from $database_backup failed."
                recovery_failed=1
            fi
        fi
    fi
    if test "$recovery_failed" -ne 0; then
        lcc_note "Recovery is incomplete; application and backup services remain stopped."
        return 1
    fi

    (
        set -e
        systemctl start "$LCC_SERVICE_NAME"
        systemctl start "$LCC_BACKUP_TIMER_NAME"
        systemctl reload caddy.service
        lcc_wait_for_health 60 0.5
    )
    step_status=$?
    if test "$step_status" -ne 0; then
        systemctl stop "$LCC_BACKUP_TIMER_NAME" >/dev/null 2>&1 || true
        systemctl stop "$LCC_SERVICE_NAME" >/dev/null 2>&1 || true
        lcc_note "Recovery did not become healthy; application and backup services remain stopped."
        return 1
    fi
}

stage_release() {
    local source_root="$1"
    local release_id="$2"
    local release_channel="$3"
    local declared_source_revision="$4"
    local source_repository="$5"
    local source_ref="$6"
    local source_origin="$7"
    local destination="$LCC_APPLICATION_ROOT/releases/$release_id"
    local source_revision git_source_root
    lcc_validate_release_channel "$release_channel"
    lcc_validate_release_id "$release_id"
    lcc_validate_source_revision "$declared_source_revision"
    source_root="$(readlink -f "$source_root")"
    test -f "$source_root/requirements-production.lock" || lcc_die "Candidate lacks production constraints."
    test -f "$source_root/frontend/package-lock.json" || lcc_die "Candidate lacks frontend lockfile."
    test -f "$source_root/frontend/dist/index.html" || lcc_die "Candidate lacks packaged frontend."
    test -f "$source_root/deploy/Caddyfile.template" || lcc_die "Candidate lacks deployment assets."
    lcc_verify_frontend_artifact "$source_root"
    if git -C "$source_root" rev-parse --verify HEAD >/dev/null 2>&1 && \
        test -n "$(git -C "$source_root" status --porcelain --untracked-files=all)"; then
        lcc_die "Candidate source must have a clean Git worktree."
    fi
    git_source_root="$(git -C "$source_root" rev-parse --show-toplevel 2>/dev/null || true)"
    if test -n "$git_source_root" && test "$(readlink -f "$git_source_root")" = "$source_root"; then
        source_revision="$(git -C "$source_root" rev-parse HEAD)"
    else
        test -f "$source_root/SOURCE_REVISION" || lcc_die "Artifact candidate lacks SOURCE_REVISION."
        source_revision="$(tr -d '\r\n' < "$source_root/SOURCE_REVISION")"
        test -f "$source_root/RELEASE_ID" && test -f "$source_root/RELEASE_CHANNEL" && \
            test -f "$source_root/RELEASE_MANIFEST" || \
            lcc_die "Artifact candidate lacks immutable release metadata."
        test "$(tr -d '\r\n' < "$source_root/RELEASE_ID")" = "$release_id" || \
            lcc_die "Artifact release ID does not match the requested release."
        test "$(tr -d '\r\n' < "$source_root/RELEASE_CHANNEL")" = "$release_channel" || \
            lcc_die "Artifact channel does not match the requested channel."
        artifact_manifest="$source_root/RELEASE_MANIFEST"
        if ! { grep -Fqx 'metadata_version=1' "$artifact_manifest" && \
            grep -Fqx "channel=$release_channel" "$artifact_manifest" && \
            grep -Fqx "release_id=$release_id" "$artifact_manifest" && \
            grep -Fqx "source_repository=$source_repository" "$artifact_manifest" && \
            grep -Fqx "source_ref=$source_ref" "$artifact_manifest" && \
            grep -Fqx "source_revision=$source_revision" "$artifact_manifest"; }; then
            lcc_die "Artifact release manifest does not match the requested immutable identity."
        fi
    fi
    test "$source_revision" = "$declared_source_revision" || \
        lcc_die "Candidate source does not match --source-revision."
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
            test "$(lcc_release_channel "$destination")" = "$release_channel" || \
                lcc_die "Candidate release ID already refers to another channel."
            lcc_validate_release_manifest "$destination" "$release_channel" "$release_id" \
                "$source_repository" "$source_ref" "$source_revision" "$source_origin"
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
    lcc_verify_frontend_artifact "$destination"
    printf '%s\n' "$release_id" > "$destination/RELEASE_ID"
    printf '%s\n' "$release_channel" > "$destination/RELEASE_CHANNEL"
    printf '%s\n' "$source_revision" > "$destination/SOURCE_REVISION"
    cat > "$destination/RELEASE_MANIFEST" <<EOF
metadata_version=1
channel=$release_channel
release_id=$release_id
source_repository=$source_repository
source_ref=$source_ref
source_revision=$source_revision
source_origin=$source_origin
EOF
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
    release_channel=""
    release_id=""
    source_revision=""
    source_repository=""
    source_ref=""
    source_origin=""
    confirm_channel_change=0
    while test "$#" -gt 0; do
        case "$1" in
            --source) source_root="${2:?Missing --source value}"; shift 2 ;;
            --channel) release_channel="${2:?Missing --channel value}"; shift 2 ;;
            --release-id) release_id="${2:?Missing --release-id value}"; shift 2 ;;
            --source-revision) source_revision="${2:?Missing --source-revision value}"; shift 2 ;;
            --source-repository) source_repository="${2:?Missing --source-repository value}"; shift 2 ;;
            --source-ref) source_ref="${2:?Missing --source-ref value}"; shift 2 ;;
            --source-origin) source_origin="${2:?Missing --source-origin value}"; shift 2 ;;
            --confirm-channel-change) confirm_channel_change=1; shift ;;
            *) usage >&2; lcc_die "Unknown apply option: $1" ;;
        esac
    done
    test -n "$source_root" && test -n "$release_channel" && test -n "$release_id" && \
        test -n "$source_revision" && test -n "$source_repository" && \
        test -n "$source_ref" && test -n "$source_origin" || \
        lcc_die "apply requires complete channel, release, source revision, repository, ref, and origin metadata."
    lcc_validate_release_channel "$release_channel"
    lcc_validate_source_revision "$source_revision"
    lcc_validate_https_url "$source_repository" "Source repository"
    lcc_validate_https_url "$source_origin" "Source origin"
    case "$release_channel" in
        stable)
            [[ "$release_id" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([+-][0-9A-Za-z.-]+)?$ ]] || \
                lcc_die "Stable release ID must be a semantic version tag."
            test "$source_ref" = "refs/tags/$release_id" || \
                lcc_die "Stable source ref must match the release tag."
            ;;
        main)
            test "$release_id" = "main-$source_revision" || \
                lcc_die "Main release ID must contain the full source SHA."
            test "$source_ref" = refs/heads/main || lcc_die "Main source ref must be refs/heads/main."
            ;;
    esac
    lcc_validate_update_transition "$old_release_channel" "$old_release_id" \
        "$old_source_revision" "$release_channel" "$release_id" "$source_revision" \
        "$confirm_channel_change"
    candidate="$(stage_release "$source_root" "$release_id" "$release_channel" \
        "$source_revision" "$source_repository" "$source_ref" "$source_origin")"
    candidate_head="$(release_head "$candidate")"
    old_database_head="$(database_revision)"
    revision_relation="$(lcc_migration_relation "$candidate" "$old_release" \
        "$old_database_head" "$candidate_head")"
    case "$revision_relation" in
        same|forward) ;;
        backward)
            lcc_die "Candidate requires a database downgrade; use database-aware rollback instead."
            ;;
        divergent)
            lcc_die "Candidate Alembic history is divergent or incompatible with the deployed database."
            ;;
        *) lcc_die "Unable to classify candidate migration compatibility." ;;
    esac
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
        restore_required=0
        test "$candidate_head" = "$old_database_head" || restore_required=1
        recover_original_installation "$pre_update_backup" "$restore_required"
        recovery_status=$?
        if test "$recovery_status" -ne 0; then
            lcc_note "Automatic update recovery failed; manual database-aware recovery is required."
            exit 1
        fi
        exit "$status"
    }
    trap rollback_failed_apply EXIT
    stop_for_transition
    transition_started=1
    if ! pre_update_backup="$(create_offline_backup "$old_release" pre-update)"; then
        lcc_die "Pre-update backup was not created."
    fi
    lcc_note "Migrating database from $old_database_head to $candidate_head"
    lcc_run_as_service_user "$candidate" "$candidate/.venv/bin/python" -c \
        'from app.database import run_migrations; run_migrations()'
    atomic_activate "$candidate"
    install_units "$candidate"
    render_caddy "$candidate"
    start_and_verify
    deployment_record="$LCC_DATA_DIRECTORY/deployment-$release_id.env"
    cat > "$deployment_record" <<EOF
channel=$release_channel
release_id=$release_id
source_repository=$source_repository
source_ref=$source_ref
source_revision=$source_revision
source_origin=$source_origin
previous_channel=$old_release_channel
previous_release_id=$old_release_id
previous_source_revision=$old_source_revision
previous_database_revision=$old_database_head
new_database_revision=$candidate_head
migration_relation=$revision_relation
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
        restore_required=0
        test "$target_head" = "$current_head" || restore_required=1
        recover_original_installation "$pre_rollback_backup" "$restore_required"
        recovery_status=$?
        if test "$recovery_status" -ne 0; then
            lcc_note "Automatic rollback recovery failed; manual database-aware recovery is required."
            exit 1
        fi
        exit "$status"
    }
    trap rollback_failed_rollback EXIT
    stop_for_transition
    transition_started=1
    if ! pre_rollback_backup="$(create_offline_backup "$old_release" pre-rollback)"; then
        lcc_die "Pre-rollback backup was not created."
    fi
    atomic_activate "$target_release"
    install_units "$target_release"
    render_caddy "$target_release"
    if test "$target_head" != "$current_head"; then
        lcc_run_as_service_user "$target_release" "$target_release/.venv/bin/lcc-ops" \
            restore --from "$database_backup"
    fi
    start_and_verify
    target_channel="$(lcc_release_channel "$target_release")"
    target_source_revision="$(lcc_release_source_revision "$target_release")"
    rollback_record="$LCC_DATA_DIRECTORY/deployment-rollback-$target_id.env"
    cat > "$rollback_record" <<EOF
channel=$target_channel
release_id=$target_id
source_revision=$target_source_revision
previous_channel=$old_release_channel
previous_release_id=$old_release_id
previous_source_revision=$old_source_revision
previous_database_revision=$current_head
new_database_revision=$target_head
pre_rollback_backup=$pre_rollback_backup
restored_database_backup=$database_backup
activated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
    chmod 0600 "$rollback_record"
    chown "$LCC_SERVICE_USER:$LCC_SERVICE_GROUP" "$rollback_record"
    trap - EXIT
    echo "Rolled back to $target_id; pre-rollback backup: $pre_rollback_backup"
else
    usage >&2
    lcc_die "Unknown action: $action"
fi
