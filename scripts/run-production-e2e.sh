#!/usr/bin/env bash
set -euo pipefail

command -v caddy >/dev/null || { echo "caddy is required for production E2E" >&2; exit 1; }
command -v google-chrome >/dev/null || { echo "Google Chrome is required for production E2E" >&2; exit 1; }

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
run_directory="$(mktemp -d /tmp/lcc-production-e2e.XXXXXX)"
cleanup() {
    status=$?
    test -n "${backend_pid:-}" && kill "$backend_pid" 2>/dev/null || true
    test -n "${caddy_pid:-}" && kill "$caddy_pid" 2>/dev/null || true
    if test "$status" -ne 0; then
        test -f "$run_directory/backend.log" && tail -n 80 "$run_directory/backend.log" >&2
        test -f "$run_directory/caddy.log" && tail -n 80 "$run_directory/caddy.log" >&2
        test -f "$run_directory/live-restore.log" && tail -n 80 "$run_directory/live-restore.log" >&2
        test -f "$run_directory/rejected-restart.log" && tail -n 80 "$run_directory/rejected-restart.log" >&2
        test -f "$run_directory/restarted.log" && tail -n 80 "$run_directory/restarted.log" >&2
    fi
    rm -rf -- "$run_directory"
    trap - EXIT
    exit "$status"
}
trap cleanup EXIT

cd "$repository_root/frontend"
npm run build

export PYTHONPATH="$repository_root/backend"
export LCC_ENVIRONMENT=production
export LCC_DATABASE_URL="sqlite:///$run_directory/data/lcc.sqlite3"
export LCC_BACKUP_DIRECTORY="$run_directory/backups"
export LCC_RELEASE_ROOT="$run_directory/release"
export LCC_BOOTSTRAP_TOKEN=production-bootstrap-token-for-browser-test
export LCC_SECURITY_SECRET=Q7vN2xK9mR4pT8wY3cF6hJ1sD5gL0bZa
export LCC_PUBLIC_ORIGIN=https://localhost:8443
export LCC_ALLOWED_ORIGINS='["https://localhost:8443"]'
export LCC_ALLOWED_HOSTS='["localhost"]'
export LCC_TRUSTED_PROXY_CIDRS='["127.0.0.0/8","::1/128"]'
export LCC_PUBLIC_HOST=localhost:8443
export LCC_HTTP_PORT=8080
export LCC_FRONTEND_ROOT="$repository_root/frontend/dist"
export LCC_E2E_BASE_URL=https://localhost:8443
export XDG_DATA_HOME="$run_directory/caddy-data"
export XDG_CONFIG_HOME="$run_directory/caddy-config"

cd "$repository_root"
mkdir -p -- "$run_directory/data"
mkdir -p -- "$LCC_RELEASE_ROOT"
ln -s "$repository_root/.venv" "$LCC_RELEASE_ROOT/.venv"
ln -s "$repository_root/backend" "$LCC_RELEASE_ROOT/backend"
printf 'production-e2e\n' > "$LCC_RELEASE_ROOT/RELEASE_ID"
printf '%s\n' "$(git rev-parse HEAD)" > "$LCC_RELEASE_ROOT/SOURCE_REVISION"
caddy validate --config "$repository_root/deploy/Caddyfile" --adapter caddyfile
"$repository_root/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers >"$run_directory/backend.log" 2>&1 &
backend_pid=$!
caddy run --config "$repository_root/deploy/Caddyfile" --adapter caddyfile >"$run_directory/caddy.log" 2>&1 &
caddy_pid=$!
for _attempt in $(seq 1 60); do
    if curl --silent --fail --insecure https://localhost:8443/api/v1/health >/dev/null; then
        break
    fi
    sleep 0.25
done
curl --silent --fail --insecure https://localhost:8443/api/v1/health >/dev/null
"$repository_root/scripts/operational-backup.sh"
live_backup="$(find "$LCC_BACKUP_DIRECTORY" -maxdepth 1 -name 'lcc-scheduled-*.sqlite3' -print -quit)"
if "$repository_root/.venv/bin/python" -m app.ops restore --from "$live_backup" >"$run_directory/live-restore.log" 2>&1; then
    echo "Restore unexpectedly succeeded while the service held the database lock" >&2
    exit 1
fi
grep -q "already open by LCC" "$run_directory/live-restore.log"
cd "$repository_root/frontend"
npm run test:e2e:production

cd "$repository_root"
"$repository_root/scripts/operational-backup.sh"
post_e2e_backup="$(find "$LCC_BACKUP_DIRECTORY" -maxdepth 1 -name 'lcc-scheduled-*.sqlite3' -printf '%T@ %p\n' | sort -nr | sed -n '1s/^[^ ]* //p')"

kill "$backend_pid"
wait "$backend_pid" || true
unset backend_pid
cd "$repository_root"
"$repository_root/.venv/bin/python" -m app.ops restore --from "$post_e2e_backup"
"$repository_root/.venv/bin/python" -c 'import os, sqlite3; path=os.environ["LCC_DATABASE_URL"].removeprefix("sqlite:///"); connection=sqlite3.connect(path); assert connection.execute("SELECT COUNT(*) FROM auth_sessions WHERE revoked_at IS NULL").fetchone()[0] == 0; connection.close()'
"$repository_root/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers >"$run_directory/rejected-restart.log" 2>&1 &
rejected_pid=$!
for _attempt in $(seq 1 100); do
    if ! kill -0 "$rejected_pid" 2>/dev/null; then
        break
    fi
    sleep 0.1
done
if kill -0 "$rejected_pid" 2>/dev/null; then
    kill "$rejected_pid"
    echo "Initialized production unexpectedly restarted with bootstrap token configured" >&2
    exit 1
fi
wait "$rejected_pid" || true

unset LCC_BOOTSTRAP_TOKEN
"$repository_root/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers >"$run_directory/restarted.log" 2>&1 &
backend_pid=$!
for _attempt in $(seq 1 60); do
    if curl --silent --fail --insecure https://localhost:8443/api/v1/health >/dev/null; then
        break
    fi
    sleep 0.25
done
curl --silent --fail --insecure https://localhost:8443/api/v1/health >/dev/null
