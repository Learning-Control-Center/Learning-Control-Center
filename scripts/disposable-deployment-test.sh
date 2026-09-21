#!/usr/bin/env bash
set -euo pipefail
umask 077

repository_root="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
test -x "$repository_root/.venv/bin/uvicorn" || {
    echo "Repository .venv is required for the disposable deployment test" >&2
    exit 1
}
test -d "$repository_root/frontend/node_modules" || {
    echo "Frontend node_modules is required for the disposable deployment test" >&2
    exit 1
}

test_root="$(mktemp -d /tmp/lcc-disposable-deployment.XXXXXX)"
backend_pid=""
cleanup() {
    status=$?
    if test -n "$backend_pid"; then
        kill "$backend_pid" 2>/dev/null || true
        wait "$backend_pid" 2>/dev/null || true
    fi
    if test "$status" -ne 0 && test -f "$test_root/backend.log"; then
        tail -n 100 "$test_root/backend.log" >&2
    fi
    if test "${LCC_KEEP_DISPOSABLE_ROOT:-0}" = "1"; then
        echo "Preserved disposable root for inspection: $test_root" >&2
    else
        rm -rf -- "$test_root"
    fi
    exit "$status"
}
trap cleanup EXIT

application_root="$test_root/opt/learning-control-center"
release_one="$application_root/releases/disposable-v1"
release_two="$application_root/releases/disposable-v2"
data_directory="$test_root/var/lib/learning-control-center"
backup_directory="$test_root/var/backups/learning-control-center"
database_path="$data_directory/lcc.sqlite3"
mkdir -p "$release_one" "$data_directory" "$backup_directory"
chmod 700 "$data_directory" "$backup_directory"
rsync -a --delete \
    --exclude=.git --exclude=.venv --exclude=node_modules --exclude=dist \
    --exclude=data --exclude=backups --exclude=tmp --exclude=memory-bank \
    "$repository_root/" "$release_one/"
ln -s "$repository_root/.venv" "$release_one/.venv"
ln -s "$repository_root/frontend/node_modules" "$release_one/frontend/node_modules"
printf 'disposable-v1\n' > "$release_one/RELEASE_ID"
printf 'main\n' > "$release_one/RELEASE_CHANNEL"
source_revision="$(git -C "$repository_root" rev-parse HEAD)"
printf '%s\n' "$source_revision" > "$release_one/SOURCE_REVISION"
cat > "$release_one/RELEASE_MANIFEST" <<EOF
metadata_version=1
channel=main
release_id=disposable-v1
source_repository=https://github.com/Learning-Control-Center/Learning-Control-Center.git
source_ref=refs/heads/main
source_revision=$source_revision
source_origin=https://github.com/Learning-Control-Center/Learning-Control-Center.git
EOF
npm --prefix "$release_one/frontend" run build
ln -s "$release_one" "$application_root/current"

port="$(python3 - <<'PY'
import socket
with socket.socket() as handle:
    handle.bind(("127.0.0.1", 0))
    print(handle.getsockname()[1])
PY
)"
export LCC_ENVIRONMENT=production
export LCC_DATABASE_URL="sqlite:///$database_path"
export LCC_BACKUP_DIRECTORY="$backup_directory"
export LCC_RELEASE_ROOT="$application_root/current"
export LCC_BOOTSTRAP_TOKEN=B7ootstrap9Token2For4Initial6Setup8Value0X
export LCC_SECURITY_SECRET=S3curity7Value9For2Runtime4Hashing6Only8Q
export LCC_PUBLIC_ORIGIN=https://lcc.example.test
export LCC_ALLOWED_ORIGINS='["https://lcc.example.test"]'
export LCC_ALLOWED_HOSTS='["lcc.example.test"]'
export LCC_TRUSTED_PROXY_CIDRS='["127.0.0.1/32"]'
export LCC_APP_TIMEZONE=UTC
export LCC_BACKUP_RETENTION_COUNT=2

start_backend() {
    active_release="$(readlink -f "$application_root/current")"
    PYTHONPATH="$active_release/backend" "$active_release/.venv/bin/uvicorn" app.main:app \
        --app-dir "$active_release/backend" --host 127.0.0.1 --port "$port" \
        --workers 1 --no-proxy-headers > "$test_root/backend.log" 2>&1 &
    backend_pid=$!
    for _ in $(seq 1 240); do
        if ! kill -0 "$backend_pid" 2>/dev/null; then
            wait "$backend_pid" || true
            backend_pid=""
            echo "Disposable backend exited during startup" >&2
            return 1
        fi
        if curl --fail --silent -H 'Host: lcc.example.test' \
            "http://127.0.0.1:$port/api/v1/health" >/dev/null 2>&1; then
            return
        fi
        sleep 0.25
    done
    echo "Disposable backend failed to become healthy" >&2
    return 1
}

stop_backend() {
    kill "$backend_pid"
    wait "$backend_pid" || true
    backend_pid=""
}

start_backend
test "$(sqlite3 "$database_path" 'SELECT version_num FROM alembic_version;')" = \
    "0019_remove_legacy_roadmap_pointer_cycle"
"$application_root/current/scripts/operational-backup.sh"
test -n "$(find "$backup_directory" -maxdepth 1 -name 'lcc-scheduled-*.sqlite3' -print -quit)"
stop_backend

cp -a "$release_one" "$release_two"
printf 'disposable-v2\n' > "$release_two/RELEASE_ID"
sed -i 's/^release_id=.*/release_id=disposable-v2/' "$release_two/RELEASE_MANIFEST"
next_link="$application_root/.current.next"
ln -s "$release_two" "$next_link"
mv -Tf "$next_link" "$application_root/current"
start_backend
curl --fail --silent -H 'Host: lcc.example.test' \
    "http://127.0.0.1:$port/api/v1/health" >/dev/null
stop_backend

ln -s "$release_one" "$next_link"
mv -Tf "$next_link" "$application_root/current"
start_backend
curl --fail --silent -H 'Host: lcc.example.test' \
    "http://127.0.0.1:$port/api/v1/health" >/dev/null
stop_backend

rm -rf -- "$application_root"
test -f "$database_path"
test -n "$(find "$backup_directory" -maxdepth 1 -name 'lcc-scheduled-*.sqlite3' -print -quit)"
echo "Disposable install/start/backup/update/rollback/uninstall-preserve flow passed."
