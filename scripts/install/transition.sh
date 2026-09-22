#!/usr/bin/env bash
# Shared V2 deployment transition. Sourced after install.sh has validated a
# SHA-addressed, private source tree and parsed the operator's choices.
# shellcheck source=scripts/deploy-common.sh
# The sourced install.sh provides validated paths, CLI values, and source identity.
# shellcheck disable=SC2154

lcc_transition_manifest_value() {
    local release="$1" key="$2" value
    test -f "$release/RELEASE_MANIFEST" || lcc_die "Installed release manifest is missing."
    test "$(grep -c "^${key}=" "$release/RELEASE_MANIFEST" || true)" -eq 1 ||
        lcc_die "Installed release has ambiguous $key metadata."
    value="$(sed -n "s/^${key}=//p" "$release/RELEASE_MANIFEST")"
    test -n "$value" || lcc_die "Installed release has empty $key metadata."
    printf '%s\n' "$value"
}

lcc_transition_release_head() {
    PYTHONPATH="$1/backend" "$1/.venv/bin/python" -c \
        'from app.ops import expected_revision; print(expected_revision())'
}

lcc_transition_database_head() {
    sqlite3 "$LCC_DATABASE_FILE" 'SELECT version_num FROM alembic_version;'
}

lcc_transition_activate() {
    local next="$LCC_APPLICATION_ROOT/.current.$$.next"
    ln -s "$1" "$next"
    mv -Tf -- "$next" "$LCC_CURRENT_RELEASE"
}

lcc_transition_stage() {
    local destination="$release_directory" staging
    if test -d "$destination"; then
        test -f "$destination/INSTALLER_V2_CORE" &&
            test "$(cat "$destination/SOURCE_REVISION")" = "$LCC_V2_SOURCE_SHA" &&
            test -x "$destination/.venv/bin/python" ||
            lcc_die "An incomplete or unrelated target release already exists: $destination"
        lcc_validate_release_manifest "$destination" main "$release_id" \
            "$LCC_GITHUB_REPOSITORY" refs/heads/main "$LCC_V2_SOURCE_SHA" "$LCC_GITHUB_REPOSITORY"
        lcc_verify_frontend_artifact "$destination"
        return
    fi
    test ! -e "$destination" && test ! -L "$destination" ||
        lcc_die "Target release path is occupied: $destination"
    staging="$application_root/releases/.$release_id.staging.$$"
    install -d -m 0700 "$staging"
    lcc_copy_release_source "$source_root" "$staging" sanitized
    lcc_verify_frontend_artifact "$staging"
    printf '1\n' > "$staging/.installing"
    mv -T -- "$staging" "$destination"
    # The venv's editable path must be the final immutable release location.
    if ! (python3 -m venv "$destination/.venv" &&
        "$destination/.venv/bin/python" -m pip install \
            --constraint "$destination/requirements-production.lock" setuptools wheel &&
        "$destination/.venv/bin/python" -m pip install \
            --constraint "$destination/requirements-production.lock" \
            --no-build-isolation --editable "$destination" &&
        "$destination/.venv/bin/python" -m pip check
    ); then
        rm -rf -- "$destination"
        lcc_die "Target release runtime staging failed; active installation was not changed."
    fi
    printf '%s\n' "$release_id" > "$destination/RELEASE_ID"
    printf 'main\n' > "$destination/RELEASE_CHANNEL"
    printf '%s\n' "$LCC_V2_SOURCE_SHA" > "$destination/SOURCE_REVISION"
    cat > "$destination/RELEASE_MANIFEST" <<EOF
metadata_version=1
channel=main
release_id=$release_id
source_repository=$LCC_GITHUB_REPOSITORY
source_ref=refs/heads/main
source_revision=$LCC_V2_SOURCE_SHA
source_origin=$LCC_GITHUB_REPOSITORY
EOF
    printf '1\n' > "$destination/INSTALLER_V2_CORE"
    chown -R "root:$LCC_SERVICE_GROUP" "$destination"
    chmod -R u=rwX,g=rX,o= "$destination"
    chmod o+x "$destination" "$destination/frontend"
    find "$destination/frontend/dist" -type d -exec chmod 0755 {} +
    find "$destination/frontend/dist" -type f -exec chmod 0644 {} +
    rm -f -- "$destination/.installing"
}

lcc_transition_classify_gateway() {
    local old="$1" rendered
    if test -e "$LCC_DEPLOYMENT_STATE_FILE" || test -L "$LCC_DEPLOYMENT_STATE_FILE"; then
        test -f "$old/INSTALLER_V2_CORE" ||
            lcc_die "A V1 release has an unexpected V2 deployment-state file."
        lcc_read_gateway_state
        return
    fi
    test ! -f "$old/INSTALLER_V2_CORE" ||
        lcc_die "Existing V2 Core lacks gateway state; select an explicit recovery workflow."
    # A V1 site has no ownership marker. Exact rendered content, the V1
    # glob import, and the installed V1 release are required as proof.
    test -f "$LCC_CADDY_SITE" && test ! -L "$LCC_CADDY_SITE" &&
        test -f "$LCC_CADDY_MAIN" && test ! -L "$LCC_CADDY_MAIN" &&
        grep -Fqx "$LCC_CADDY_IMPORT" "$LCC_CADDY_MAIN" ||
        lcc_die "V1 Caddy ownership is ambiguous; no proxy configuration was changed."
    rendered="$(mktemp /tmp/lcc-v1-site.XXXXXXXX)"
    lcc_render_caddy_site "$old" "$rendered"
    "$LCC_CADDY_BINARY" fmt --overwrite "$rendered" >/dev/null
    if ! cmp -s "$rendered" "$LCC_CADDY_SITE"; then
        rm -f -- "$rendered"
        lcc_die "V1 Caddy site differs from its LCC release; ownership is ambiguous."
    fi
    rm -f -- "$rendered"
    printf 'caddy\n'
}

lcc_transition_render_caddy() {
    local target="$1" rendered
    rendered="$(mktemp /tmp/lcc-v2-site.XXXXXXXX)"
    lcc_render_caddy_site "$target" "$rendered"
    if test -f "$target/INSTALLER_V2_CORE"; then
        sed -i '1i# Managed by Learning Control Center Installer V2' "$rendered"
    fi
    "$LCC_CADDY_BINARY" fmt --overwrite "$rendered" >/dev/null
    install -m 0644 "$rendered" "$LCC_CADDY_SITE"
    rm -f -- "$rendered"
    "$LCC_CADDY_BINARY" validate --config "$LCC_CADDY_MAIN" --adapter caddyfile
}

lcc_transition_validate_managed_caddy() {
    local release="$1"
    test -f "$LCC_CADDY_SITE" && test ! -L "$LCC_CADDY_SITE" &&
        test -f "$LCC_CADDY_MAIN" && test ! -L "$LCC_CADDY_MAIN" ||
        lcc_die "Managed Caddy files are missing or unsafe."
    if test -f "$release/INSTALLER_V2_CORE"; then
        grep -Fqx '# Managed by Learning Control Center Installer V2' "$LCC_CADDY_SITE" ||
            lcc_die "Managed Caddy site ownership cannot be established."
        { grep -Fqx "$LCC_V2_CADDY_IMPORT" "$LCC_CADDY_MAIN" ||
            grep -Fqx "$LCC_CADDY_IMPORT" "$LCC_CADDY_MAIN"; } ||
            lcc_die "Managed Caddy import is missing."
    fi
    # For V1, classify_gateway already compared the site to its own release.
}

lcc_transition_backup() {
    local old="$1" purpose="$2" path before after
    before="$(mktemp)"
    after="$(mktemp)"
    find "$LCC_BACKUP_DIRECTORY" -maxdepth 1 -type f \
        -name "lcc-$purpose-*.sqlite3" -printf '%f\n' | LC_ALL=C sort > "$before"
    path="$(lcc_run_as_service_user "$old" env LCC_BACKUP_PURPOSE="$purpose" \
        "$old/scripts/operational-backup.sh")" || { rm -f -- "$before" "$after"; return 1; }
    if test -z "$path"; then
        find "$LCC_BACKUP_DIRECTORY" -maxdepth 1 -type f \
            -name "lcc-$purpose-*.sqlite3" -printf '%f\n' | LC_ALL=C sort > "$after"
        path="$(lcc_select_single_new_backup "$LCC_BACKUP_DIRECTORY" "$before" "$after")" || {
            rm -f -- "$before" "$after"
            return 1
        }
    fi
    rm -f -- "$before" "$after"
    test -n "$path" && test -f "$path" && test ! -L "$path" &&
        test -f "$path.manifest" && test ! -L "$path.manifest" || return 1
    case "$path" in "$LCC_BACKUP_DIRECTORY/lcc-$purpose-"*.sqlite3) ;; *) return 1 ;; esac
    test "$(sha256sum "$path" | cut -d' ' -f1)" = \
        "$(sed -n 's/^checksum_sha256=//p' "$path.manifest")" || return 1
    printf '%s\n' "$path"
}

lcc_transition_restore_database() {
    local release="$1" backup="$2"
    lcc_run_as_service_user "$release" "$release/.venv/bin/lcc-ops" restore --from "$backup"
}

lcc_transition_restore_files() {
    local snapshot="$1" mode="$2" path name
    for name in "$LCC_SERVICE_NAME" "$LCC_BACKUP_SERVICE_NAME" "$LCC_BACKUP_TIMER_NAME"; do
        path="/etc/systemd/system/$name"
        if test -f "$snapshot/$name"; then
            cp -a -- "$snapshot/$name" "$path"
        else
            rm -f -- "$path"
        fi
    done
    for name in updater gateway env caddy-site caddy-main; do
        if [[ "$name" = caddy-* ]] && test "$mode" != caddy; then continue; fi
        case "$name" in
            updater) path="$LCC_UPDATE_ENTRYPOINT" ;;
            gateway) path="$LCC_DEPLOYMENT_STATE_FILE" ;;
            env) path="$LCC_ENVIRONMENT_FILE" ;;
            caddy-site) path="$LCC_CADDY_SITE" ;;
            caddy-main) path="$LCC_CADDY_MAIN" ;;
        esac
        if test -f "$snapshot/$name"; then
            cp -a -- "$snapshot/$name" "$path"
        else
            rm -f -- "$path"
        fi
    done
    systemctl daemon-reload
}

lcc_transition_snapshot() {
    local snapshot="$1" mode="$2" name path
    install -d -m 0700 "$snapshot"
    for name in "$LCC_SERVICE_NAME" "$LCC_BACKUP_SERVICE_NAME" "$LCC_BACKUP_TIMER_NAME"; do
        path="/etc/systemd/system/$name"
        test ! -e "$path" || cp -a -- "$path" "$snapshot/$name"
    done
    for name in updater gateway env caddy-site caddy-main; do
        if [[ "$name" = caddy-* ]] && test "$mode" != caddy; then continue; fi
        case "$name" in
            updater) path="$LCC_UPDATE_ENTRYPOINT" ;;
            gateway) path="$LCC_DEPLOYMENT_STATE_FILE" ;;
            env) path="$LCC_ENVIRONMENT_FILE" ;;
            caddy-site) path="$LCC_CADDY_SITE" ;;
            caddy-main) path="$LCC_CADDY_MAIN" ;;
        esac
        test ! -e "$path" || cp -a -- "$path" "$snapshot/$name"
    done
}

lcc_transition_install_units() {
    local target="$1" unit
    for unit in "$LCC_SERVICE_NAME" "$LCC_BACKUP_SERVICE_NAME" "$LCC_BACKUP_TIMER_NAME"; do
        install -m 0644 "$target/deploy/$unit" "/etc/systemd/system/$unit"
    done
    if test -x "$target/deploy/learning-control-center-update.sh"; then
        install -m 0755 "$target/deploy/learning-control-center-update.sh" "$LCC_UPDATE_ENTRYPOINT"
    else
        # The historical v1.0.0 snapshot predates the installed updater.
        rm -f -- "$LCC_UPDATE_ENTRYPOINT"
    fi
    ln -sfn "$LCC_CURRENT_RELEASE/scripts/lcc-admin" /usr/local/sbin/lcc-admin
    systemctl daemon-reload
}

lcc_transition_verify() {
    local mode="$1" port="$2"
    systemctl start "$LCC_SERVICE_NAME"
    lcc_wait_for_internal_health "$port" 120 0.5 || return 1
    systemctl start "$LCC_BACKUP_TIMER_NAME"
    if test "$mode" = caddy; then
        "$LCC_CADDY_BINARY" validate --config "$LCC_CADDY_MAIN" --adapter caddyfile || return 1
        systemctl reload caddy.service
        lcc_wait_for_health 120 0.5 || return 1
    fi
}

lcc_transition_apply() {
    local old old_id old_sha old_channel old_repository old_ref mode effective_port candidate_head database_head relation
    local backup="" snapshot="" transition_started=0 database_touched=0 recovered=0 status
    # Hold one lock from identity comparison through activation so a queued
    # invocation cannot act on an old same-SHA or rollback decision.
    lcc_acquire_deployment_lock
    old="$(readlink -f -- "$LCC_CURRENT_RELEASE")"
    [[ "$old" = "$LCC_APPLICATION_ROOT/releases/"* ]] && test -d "$old" ||
        lcc_die "Active release is outside LCC-owned immutable releases."
    old_id="$(cat "$old/RELEASE_ID")"
    old_sha="$(lcc_release_source_revision "$old")"
    old_channel="$(lcc_release_channel "$old")"
    old_repository="$(lcc_transition_manifest_value "$old" source_repository)"
    old_ref="$(lcc_transition_manifest_value "$old" source_ref)"
    test "$old_repository" = "$LCC_GITHUB_REPOSITORY" ||
        lcc_die "This installation records a non-GitHub source; it cannot be silently converted to the V2 public source."
    if test -f "$old/INSTALLER_V2_CORE"; then
        case "$old_channel:$old_ref" in
            main:refs/heads/main) ;;
            stable:refs/tags/*)
                test "$old_ref" = "refs/tags/$old_id" ||
                    lcc_die "V2 pinned release ref does not match its release identity." ;;
            *) lcc_die "V2 release source channel/ref is invalid." ;;
        esac
    fi
    lcc_validate_release_manifest "$old" "$old_channel" "$old_id" \
        "$old_repository" "$old_ref" "$old_sha" \
        "$(lcc_transition_manifest_value "$old" source_origin)"
    test -r "$LCC_ENVIRONMENT_FILE" || lcc_die "Existing production environment is missing."
    lcc_validate_environment_file_security "$LCC_ENVIRONMENT_FILE" 1 0
    lcc_load_environment "$LCC_ENVIRONMENT_FILE"
    lcc_validate_environment "$LCC_CURRENT_RELEASE"
    test "$LCC_DATABASE_URL" = "sqlite:///$LCC_DATABASE_FILE" &&
        test "$LCC_BACKUP_DIRECTORY" = "$LCC_BACKUP_DIRECTORY_DEFAULT" ||
        lcc_die "Existing data paths differ from the managed layout."
    effective_port="$(lcc_effective_app_port)"
    test -z "$requested_port" || test "$requested_port" = "$effective_port" ||
        lcc_die "Update cannot change the existing internal port."
    test -z "$domain" || test "$domain" = "$(lcc_public_hostname)" ||
        lcc_die "Update cannot change the existing domain."
    test -z "$timezone" || test "$timezone" = "$LCC_APP_TIMEZONE" ||
        lcc_die "Update cannot change the existing timezone."
    test -z "$environment_source" || lcc_die "Update cannot replace the production environment."
    mode="$(lcc_transition_classify_gateway "$old")"
    test "$gateway_explicit" -eq 0 || test "$gateway" = "$mode" ||
        lcc_die "Update cannot change gateway mode ($mode)."
    if test -f "$old/INSTALLER_V2_CORE" && test "$old_channel" = main &&
        test "$old_sha" = "$LCC_V2_SOURCE_SHA"; then
        echo 'Learning Control Center is already up to date.'
        return 0
    fi
    test "$core_only" -eq 0 || lcc_die "Core-only transition is unavailable."
    echo "Current channel: $old_channel"
    echo "Current release: $old_id"
    echo "Current source SHA: $old_sha"
    echo 'Target channel: main'
    echo "Target source SHA: $LCC_V2_SOURCE_SHA"
    if test "$old_channel" != main; then echo "Channel change: $old_channel -> main"; fi
    if test "$dry_run" -eq 1; then echo 'DRY-RUN: no deployment changes made.'; return 0; fi
    test -d "$LCC_DATA_DIRECTORY" && test ! -L "$LCC_DATA_DIRECTORY" &&
        test -d "$LCC_BACKUP_DIRECTORY" && test ! -L "$LCC_BACKUP_DIRECTORY" &&
        test "$(stat -c '%U:%G %a' "$LCC_DATA_DIRECTORY")" = 'lcc:lcc 700' &&
        test "$(stat -c '%U:%G %a' "$LCC_BACKUP_DIRECTORY")" = 'lcc:lcc 700' ||
        lcc_die "Persistent LCC data/backup paths have unsafe ownership or mode."
    local unit owned_unit
    for unit in "$LCC_SERVICE_NAME" "$LCC_BACKUP_SERVICE_NAME" "$LCC_BACKUP_TIMER_NAME"; do
        owned_unit="/etc/systemd/system/$unit"
        test -f "$owned_unit" && test ! -L "$owned_unit" &&
            cmp -s "$old/deploy/$unit" "$owned_unit" ||
            lcc_die "Installed LCC unit differs from the active release: $owned_unit"
    done
    test -L /usr/local/sbin/lcc-admin &&
        test "$(readlink /usr/local/sbin/lcc-admin)" = "$LCC_CURRENT_RELEASE/scripts/lcc-admin" ||
        lcc_die "Administrator entry point is not the LCC-owned symlink."
    if test -e "$LCC_UPDATE_ENTRYPOINT" || test -L "$LCC_UPDATE_ENTRYPOINT"; then
        test -f "$LCC_UPDATE_ENTRYPOINT" && test ! -L "$LCC_UPDATE_ENTRYPOINT" &&
            cmp -s "$old/deploy/learning-control-center-update.sh" "$LCC_UPDATE_ENTRYPOINT" ||
            lcc_die "Installed updater differs from the active release."
    fi
    if test "$assume_yes" -eq 0; then
        exec 3<>/dev/tty || lcc_die "Update confirmation needs a terminal; use --yes."
        printf 'Continue with this update? [y/N]: ' >&3
        IFS= read -r answer <&3 || lcc_die "Unable to read update confirmation."
        case "${answer,,}" in y|yes) ;; *) lcc_die "Update cancelled." ;; esac
    fi
    lcc_ubuntu_check_platform
    # Preserve the operator's APT policy. Staging and all schema checks finish
    # before any LCC service or database is stopped.
    lcc_ubuntu_provision_core
    lcc_transition_stage
    candidate_head="$(lcc_transition_release_head "$release_directory")"
    database_head="$(lcc_transition_database_head)"
    relation="$(lcc_migration_relation "$release_directory" "$old" "$database_head" "$candidate_head")"
    case "$relation" in
        same|forward) ;;
        backward|divergent) lcc_die "Candidate database migration is $relation; no service was stopped." ;;
        *) lcc_die "Database migration relation is unknown; no service was stopped." ;;
    esac
    test -x "$old/scripts/operational-backup.sh" && test -x "$old/.venv/bin/lcc-ops" ||
        lcc_die "Previous release lacks verified backup/restore tooling."
    if test "$mode" = caddy; then
        test -x "$LCC_CADDY_BINARY" || lcc_die "Managed Caddy is unavailable."
        lcc_transition_validate_managed_caddy "$old"
    fi
    snapshot="$(mktemp -d /run/lcc-transition.XXXXXXXX)"
    lcc_transition_snapshot "$snapshot" "$mode"
    # shellcheck disable=SC2329
    recover_transition() {
        status=$?
        trap - EXIT
        if test "$transition_started" -eq 0; then
            rm -rf -- "$snapshot"
            exit "$status"
        fi
        set +e
        lcc_note "Transition failed; restoring release $old_id."
        systemctl stop "$LCC_BACKUP_TIMER_NAME" "$LCC_BACKUP_SERVICE_NAME" "$LCC_SERVICE_NAME"
        lcc_transition_restore_files "$snapshot" "$mode"
        files_status=$?
        lcc_transition_activate "$old"
        activate_status=$?
        if test "$database_touched" -eq 1; then
            lcc_transition_restore_database "$old" "$backup"
            database_status=$?
        else
            database_status=0
        fi
        if test "$files_status" -eq 0 && test "$activate_status" -eq 0 && test "$database_status" -eq 0; then
            lcc_load_environment "$LCC_ENVIRONMENT_FILE"
            lcc_transition_verify "$mode" "$effective_port"
            recovered=$?
        else
            recovered=1
        fi
        if test "$recovered" -eq 0; then
            rm -rf -- "$snapshot"
            lcc_note "Previous release recovered and health verified."
        else
            lcc_note "CRITICAL: recovery incomplete; protected snapshot remains at $snapshot; inspect services and backup $backup."
            systemctl stop "$LCC_BACKUP_TIMER_NAME" "$LCC_SERVICE_NAME"
        fi
        exit "$status"
    }
    trap recover_transition EXIT
    transition_started=1
    systemctl stop "$LCC_BACKUP_TIMER_NAME" "$LCC_BACKUP_SERVICE_NAME" "$LCC_SERVICE_NAME"
    lcc_require_inactive_service
    backup="$(lcc_transition_backup "$old" pre-update)" || lcc_die "Pre-update backup failed."
    if test "$relation" = forward; then
        database_touched=1
        lcc_run_as_service_user "$release_directory" "$release_directory/.venv/bin/python" -c \
            'from app.database import run_migrations; run_migrations()'
    fi
    lcc_transition_activate "$release_directory"
    lcc_transition_install_units "$release_directory"
    if test "$mode" = caddy; then lcc_transition_render_caddy "$release_directory"; fi
    lcc_transition_verify "$mode" "$effective_port" || lcc_die "Target release health failed."
    lcc_write_gateway_state "$mode"
    record="$LCC_DATA_DIRECTORY/deployment-$release_id.env"
    cat > "$record" <<EOF
channel=main
release_id=$release_id
source_repository=$LCC_GITHUB_REPOSITORY
source_ref=refs/heads/main
source_revision=$LCC_V2_SOURCE_SHA
source_origin=$LCC_GITHUB_REPOSITORY
previous_channel=$old_channel
previous_release_id=$old_id
previous_source_revision=$old_sha
previous_database_revision=$database_head
new_database_revision=$candidate_head
migration_relation=$relation
pre_update_backup=$backup
activated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
    chmod 0600 "$record"
    chown "$LCC_SERVICE_USER:$LCC_SERVICE_GROUP" "$record"
    trap - EXIT
    rm -rf -- "$snapshot"
    echo "Activated release $release_id; pre-update backup: $backup"
    if test "$mode" = external; then
        if lcc_wait_for_health 1 0; then
            echo 'Public HTTPS health: healthy (external proxy was not modified).'
        else
            echo 'Public gateway/TLS: pending operator verification; external proxy was not modified.'
        fi
    fi
}

lcc_transition_rollback() {
    local target_id="" supplied_backup="" confirmed=0 old old_id target mode port
    local current_head target_head snapshot backup="" database_touched=0 transition_started=0
    local legacy_port_mode="" status files_status activate_status database_status recovered
    test "$(id -u)" -eq 0 || lcc_die "Rollback requires root."
    while test "$#" -gt 0; do
        case "$1" in
            --to) test "$#" -ge 2 || lcc_die "--to requires a release ID."; target_id="$2"; shift 2 ;;
            --database-backup) test "$#" -ge 2 || lcc_die "--database-backup needs a path."; supplied_backup="$2"; shift 2 ;;
            --confirm-database-replacement) confirmed=1; shift ;;
            *) lcc_die "Unknown rollback option: $1" ;;
        esac
    done
    test -n "$target_id" || lcc_die "Rollback requires --to RELEASE_ID."
    lcc_validate_release_id "$target_id"
    lcc_acquire_deployment_lock
    old="$(readlink -f -- "$LCC_CURRENT_RELEASE")"
    [[ "$old" = "$LCC_APPLICATION_ROOT/releases/"* ]] || lcc_die "Invalid active release."
    old_id="$(cat "$old/RELEASE_ID")"
    test "$target_id" != "$old_id" || lcc_die "Target release is already active."
    target="$LCC_APPLICATION_ROOT/releases/$target_id"
    test -d "$target" && test ! -L "$target" && test -x "$target/.venv/bin/python" ||
        lcc_die "Target immutable release is unavailable."
    test "$(cat "$target/RELEASE_ID")" = "$target_id" ||
        lcc_die "Target release identity differs from its directory."
    lcc_validate_release_manifest "$target" "$(lcc_release_channel "$target")" "$target_id" \
        "$(lcc_transition_manifest_value "$target" source_repository)" \
        "$(lcc_transition_manifest_value "$target" source_ref)" \
        "$(lcc_release_source_revision "$target")" \
        "$(lcc_transition_manifest_value "$target" source_origin)"
    lcc_load_environment "$LCC_ENVIRONMENT_FILE"
    lcc_validate_environment "$LCC_CURRENT_RELEASE"
    port="$(lcc_effective_app_port)"
    mode="$(lcc_transition_classify_gateway "$old")"
    if test "$mode" = external && ! test -f "$target/INSTALLER_V2_CORE"; then
        lcc_die "Legacy release cannot honor an external gateway; no service was stopped."
    fi
    legacy_port_mode="$(lcc_app_port_rollback_mode "$target")"
    current_head="$(lcc_transition_database_head)"
    target_head="$(lcc_transition_release_head "$target")"
    if test "$target_head" != "$current_head"; then
        test "$confirmed" -eq 1 && test -n "$supplied_backup" ||
            lcc_die "Schema-crossing rollback requires --database-backup and --confirm-database-replacement."
        case "$supplied_backup" in
            "$LCC_BACKUP_DIRECTORY_DEFAULT/"*.sqlite3) ;;
            *) lcc_die "Rollback backup must be an operational LCC backup." ;;
        esac
        test -f "$supplied_backup" && test ! -L "$supplied_backup" &&
            test -f "$supplied_backup.manifest" && test ! -L "$supplied_backup.manifest" ||
            lcc_die "Rollback backup or manifest is missing or unsafe."
        test "$(grep -c "^release_id=$target_id$" "$supplied_backup.manifest" || true)" -eq 1 &&
            test "$(grep -c "^schema_revision=$target_head$" "$supplied_backup.manifest" || true)" -eq 1 &&
            test "$(grep -c "^database_path=$LCC_DATABASE_FILE$" "$supplied_backup.manifest" || true)" -eq 1 &&
            test "$(sha256sum "$supplied_backup" | cut -d' ' -f1)" = \
                "$(sed -n 's/^checksum_sha256=//p' "$supplied_backup.manifest")" &&
            test "$(sqlite3 "$supplied_backup" 'PRAGMA integrity_check;')" = ok ||
            lcc_die "Rollback backup does not match the target release/schema or failed integrity verification."
        lcc_run_as_service_user "$old" test -r "$supplied_backup" ||
            lcc_die "LCC service account cannot read rollback backup."
    else
        test -z "$supplied_backup" || lcc_die "A database replacement is unnecessary for this rollback."
    fi
    if test "$mode" = caddy; then
        lcc_transition_validate_managed_caddy "$old"
    fi
    snapshot="$(mktemp -d /run/lcc-rollback.XXXXXXXX)"
    lcc_transition_snapshot "$snapshot" "$mode"
    # shellcheck disable=SC2329
    recover_rollback() {
        status=$?
        trap - EXIT
        if test "$transition_started" -eq 0; then rm -rf -- "$snapshot"; exit "$status"; fi
        set +e
        lcc_note "Rollback failed; restoring release $old_id."
        systemctl stop "$LCC_BACKUP_TIMER_NAME" "$LCC_BACKUP_SERVICE_NAME" "$LCC_SERVICE_NAME"
        lcc_transition_restore_files "$snapshot" "$mode"; files_status=$?
        lcc_transition_activate "$old"; activate_status=$?
        if test "$database_touched" -eq 1; then
            lcc_transition_restore_database "$old" "$backup"; database_status=$?
        else
            database_status=0
        fi
        if test "$files_status" -eq 0 && test "$activate_status" -eq 0 && test "$database_status" -eq 0; then
            lcc_load_environment "$LCC_ENVIRONMENT_FILE"
            lcc_transition_verify "$mode" "$port"; recovered=$?
        else
            recovered=1
        fi
        if test "$recovered" -eq 0; then
            rm -rf -- "$snapshot"
            lcc_note "Original release recovered and health verified."
        else
            lcc_note "CRITICAL: rollback recovery failed; snapshot remains at $snapshot; backup: $backup"
            systemctl stop "$LCC_BACKUP_TIMER_NAME" "$LCC_SERVICE_NAME"
        fi
        exit "$status"
    }
    trap recover_rollback EXIT
    transition_started=1
    systemctl stop "$LCC_BACKUP_TIMER_NAME" "$LCC_BACKUP_SERVICE_NAME" "$LCC_SERVICE_NAME"
    lcc_require_inactive_service
    backup="$(lcc_transition_backup "$old" pre-rollback)" || lcc_die "Pre-rollback backup failed."
    if test "$legacy_port_mode" = legacy-remove-key; then
        temporary_env="$(mktemp /run/lcc-rollback-env.XXXXXXXX)"
        lcc_render_environment_without_app_port "$LCC_ENVIRONMENT_FILE" "$temporary_env"
        lcc_install_environment_file "$temporary_env" "$LCC_ENVIRONMENT_FILE"
        rm -f -- "$temporary_env"
        lcc_load_environment "$LCC_ENVIRONMENT_FILE"
    fi
    if test "$target_head" != "$current_head"; then
        database_touched=1
        lcc_transition_restore_database "$target" "$supplied_backup"
    fi
    lcc_transition_activate "$target"
    lcc_transition_install_units "$target"
    if test "$mode" = caddy; then lcc_transition_render_caddy "$target"; fi
    lcc_transition_verify "$mode" "$port" || lcc_die "Rollback target health failed."
    if ! test -f "$target/INSTALLER_V2_CORE"; then rm -f -- "$LCC_DEPLOYMENT_STATE_FILE"; fi
    record="$LCC_DATA_DIRECTORY/deployment-rollback-$target_id.env"
    cat > "$record" <<EOF
channel=$(lcc_release_channel "$target")
release_id=$target_id
source_revision=$(lcc_release_source_revision "$target")
previous_release_id=$old_id
previous_database_revision=$current_head
new_database_revision=$target_head
pre_rollback_backup=$backup
restored_database_backup=$supplied_backup
activated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
    chown "$LCC_SERVICE_USER:$LCC_SERVICE_GROUP" "$record"
    chmod 0600 "$record"
    trap - EXIT
    rm -rf -- "$snapshot"
    echo "Rolled back to $target_id; pre-rollback backup: $backup"
}
