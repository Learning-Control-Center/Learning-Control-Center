#!/usr/bin/env bash
set -euo pipefail
umask 077

: "${LCC_DATABASE_URL:?Set LCC_DATABASE_URL to the application absolute SQLite URL}"
: "${LCC_BACKUP_DIRECTORY:?Set LCC_BACKUP_DIRECTORY to the absolute backup directory}"

case "$LCC_DATABASE_URL" in
    sqlite:////*) database_path="${LCC_DATABASE_URL#sqlite:///}" ;;
    *) echo "LCC_DATABASE_URL must use an absolute SQLite path" >&2; exit 2 ;;
esac

retention="${LCC_BACKUP_RETENTION_COUNT:-14}"
mkdir -p -- "$LCC_BACKUP_DIRECTORY"
chmod 700 -- "$LCC_BACKUP_DIRECTORY"
timestamp="$(date -u +%Y%m%dT%H%M%S%NZ)"
destination="$LCC_BACKUP_DIRECTORY/lcc-scheduled-$timestamp.sqlite3"
cleanup_partial() {
    status=$?
    if test "$status" -ne 0; then
        rm -f -- "$destination" "$destination.manifest"
    fi
    exit "$status"
}
trap cleanup_partial EXIT
sqlite3 "$database_path" ".backup '$destination'"
chmod 600 -- "$destination"
result="$(sqlite3 "$destination" 'PRAGMA integrity_check;')"
test "$result" = "ok"
test -z "$(sqlite3 "$destination" 'PRAGMA foreign_key_check;')"
revision="$(sqlite3 "$destination" 'SELECT version_num FROM alembic_version;')"
test -n "$revision"
checksum="$(sha256sum "$destination" | cut -d' ' -f1)"
manifest="$destination.manifest"
printf 'checksum_sha256=%s\nschema_revision=%s\napp_version=%s\ndatabase_path=%s\n' \
    "$checksum" "$revision" "${LCC_APP_VERSION:-1.0.0}" "$database_path" >"$manifest"
chmod 600 -- "$manifest"

mapfile -d '' expired < <(
    find "$LCC_BACKUP_DIRECTORY" -maxdepth 1 -type f -name 'lcc-scheduled-*.sqlite3' \
        -printf '%T@ %p\0' | sort -zrn | tail -z -n "+$((retention + 1))" | cut -z -d ' ' -f2-
)
for backup in "${expired[@]}"; do
    rm -- "$backup" "$backup.manifest"
done
trap - EXIT
