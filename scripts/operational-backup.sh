#!/usr/bin/env bash
set -euo pipefail
umask 077

: "${LCC_DATABASE_URL:?Set LCC_DATABASE_URL to the application absolute SQLite URL}"
: "${LCC_BACKUP_DIRECTORY:?Set LCC_BACKUP_DIRECTORY to the absolute backup directory}"
: "${LCC_RELEASE_ROOT:=/opt/learning-control-center/current}"

case "$LCC_DATABASE_URL" in
    sqlite:////*) database_path="${LCC_DATABASE_URL#sqlite:///}" ;;
    *) echo "LCC_DATABASE_URL must use an absolute SQLite path" >&2; exit 2 ;;
esac

retention="${LCC_BACKUP_RETENTION_COUNT:-14}"
[[ "$retention" =~ ^[1-9][0-9]*$ ]] || {
    echo "LCC_BACKUP_RETENTION_COUNT must be a positive integer" >&2
    exit 2
}
purpose="${LCC_BACKUP_PURPOSE:-scheduled}"
[[ "$purpose" =~ ^[a-z0-9][a-z0-9-]*$ ]] || {
    echo "LCC_BACKUP_PURPOSE must contain lowercase letters, numbers, and hyphens" >&2
    exit 2
}
test -x "$LCC_RELEASE_ROOT/.venv/bin/python" || {
    echo "The deployed Python runtime is missing under LCC_RELEASE_ROOT" >&2
    exit 2
}
test -f "$LCC_RELEASE_ROOT/RELEASE_ID" || {
    echo "The deployed release identity is missing" >&2
    exit 2
}
mkdir -p -- "$LCC_BACKUP_DIRECTORY"
chmod 700 -- "$LCC_BACKUP_DIRECTORY"
timestamp="$(date -u +%Y%m%dT%H%M%S%NZ)"
destination="$LCC_BACKUP_DIRECTORY/lcc-$purpose-$timestamp.sqlite3"
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
expected_revision="$(
    PYTHONPATH="$LCC_RELEASE_ROOT/backend" "$LCC_RELEASE_ROOT/.venv/bin/python" -c \
        'from app.ops import expected_revision; print(expected_revision())'
)"
test "$revision" = "$expected_revision" || {
    echo "Backup schema revision $revision does not match deployed head $expected_revision" >&2
    exit 1
}
checksum="$(sha256sum "$destination" | cut -d' ' -f1)"
manifest="$destination.manifest"
release_id="$(tr -d '\r\n' < "$LCC_RELEASE_ROOT/RELEASE_ID")"
release_channel="stable"
if test -f "$LCC_RELEASE_ROOT/RELEASE_CHANNEL"; then
    release_channel="$(tr -d '\r\n' < "$LCC_RELEASE_ROOT/RELEASE_CHANNEL")"
fi
test -f "$LCC_RELEASE_ROOT/SOURCE_REVISION" || {
    echo "The deployed source revision is missing" >&2
    exit 2
}
source_revision="$(tr -d '\r\n' < "$LCC_RELEASE_ROOT/SOURCE_REVISION")"
[[ "$source_revision" =~ ^[0-9a-f]{40}$ ]] || {
    echo "The deployed source revision is not a full Git commit SHA" >&2
    exit 2
}
test -f "$LCC_RELEASE_ROOT/RELEASE_MANIFEST" || {
    echo "The deployed release manifest is missing" >&2
    exit 2
}
source_repository="$(sed -n 's/^source_repository=//p' "$LCC_RELEASE_ROOT/RELEASE_MANIFEST" | head -n1)"
source_ref="$(sed -n 's/^source_ref=//p' "$LCC_RELEASE_ROOT/RELEASE_MANIFEST" | head -n1)"
source_origin="$(sed -n 's/^source_origin=//p' "$LCC_RELEASE_ROOT/RELEASE_MANIFEST" | head -n1)"
test -n "$source_repository" && test -n "$source_ref" && test -n "$source_origin" || {
    echo "The deployed release manifest lacks source identity" >&2
    exit 2
}
printf 'checksum_sha256=%s\nschema_revision=%s\nchannel=%s\nrelease_id=%s\nsource_repository=%s\nsource_ref=%s\nsource_revision=%s\nsource_origin=%s\ndatabase_path=%s\n' \
    "$checksum" "$revision" "$release_channel" "$release_id" "$source_repository" \
    "$source_ref" "$source_revision" "$source_origin" "$database_path" >"$manifest"
chmod 600 -- "$manifest"

if test "$purpose" = "scheduled"; then
    mapfile -d '' expired < <(
        find "$LCC_BACKUP_DIRECTORY" -maxdepth 1 -type f -name 'lcc-scheduled-*.sqlite3' \
            -printf '%T@ %p\0' | sort -zrn | tail -z -n "+$((retention + 1))" | cut -z -d ' ' -f2-
    )
    for backup in "${expired[@]}"; do
        rm -f -- "$backup" "$backup.manifest"
    done
fi
trap - EXIT
printf '%s\n' "$destination"
