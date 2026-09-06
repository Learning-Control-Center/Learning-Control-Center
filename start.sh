#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIRECTORY="$PROJECT_ROOT/tmp/learning-control-center"
BACKEND_PID_FILE="$RUN_DIRECTORY/backend.pid"
FRONTEND_PID_FILE="$RUN_DIRECTORY/frontend.pid"
BACKEND_LOG_FILE="$RUN_DIRECTORY/backend.log"
FRONTEND_LOG_FILE="$RUN_DIRECTORY/frontend.log"

process_start_time() {
  local pid="$1"
  local process_stat
  local -a stat_fields

  [[ -r "/proc/$pid/stat" ]] || return 1
  IFS= read -r process_stat < "/proc/$pid/stat"
  read -r -a stat_fields <<< "${process_stat##*) }"
  [[ "${#stat_fields[@]}" -ge 20 ]] || return 1
  printf '%s\n' "${stat_fields[19]}"
}

service_is_running() {
  local pid_file="$1"
  local pid
  local recorded_start_time
  local current_start_time

  [[ -f "$pid_file" ]] || return 1
  read -r pid recorded_start_time < "$pid_file" || return 1
  [[ "$pid" =~ ^[0-9]+$ && "$recorded_start_time" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  current_start_time="$(process_start_time "$pid")" || return 1
  [[ "$current_start_time" == "$recorded_start_time" ]]
}

start_service() {
  local name="$1"
  local pid_file="$2"
  local log_file="$3"
  local pid
  local start_time
  shift 3

  if service_is_running "$pid_file"; then
    read -r pid _ < "$pid_file"
    printf '%s is already running (PID %s).\n' "$name" "$pid"
    return 2
  fi

  rm -f -- "$pid_file"
  nohup setsid "$@" > "$log_file" 2>&1 < /dev/null &
  pid=$!
  start_time="$(process_start_time "$pid")"
  printf '%s %s\n' "$pid" "$start_time" > "$pid_file"

  sleep 1
  if ! service_is_running "$pid_file"; then
    rm -f -- "$pid_file"
    printf 'Failed to start %s. See %s\n' "$name" "$log_file" >&2
    return 1
  fi

  printf '%s started (PID %s).\n' "$name" "$pid"
  return 0
}

stop_started_service() {
  local pid_file="$1"
  local pid

  service_is_running "$pid_file" || return 0
  read -r pid _ < "$pid_file"
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  rm -f -- "$pid_file"
}

if [[ ! -x "$PROJECT_ROOT/.venv/bin/alembic" || ! -x "$PROJECT_ROOT/.venv/bin/uvicorn" ]]; then
  printf 'Backend dependencies are missing. Create .venv and install the project first.\n' >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  printf 'npm is required to start the frontend.\n' >&2
  exit 1
fi

if [[ ! -d "$PROJECT_ROOT/frontend/node_modules" ]]; then
  printf 'Frontend dependencies are missing. Run npm install in frontend/ first.\n' >&2
  exit 1
fi

mkdir -p -- "$RUN_DIRECTORY" "$PROJECT_ROOT/data" "$PROJECT_ROOT/backups"
cd "$PROJECT_ROOT"

printf 'Applying database migrations...\n'
"$PROJECT_ROOT/.venv/bin/alembic" upgrade head

backend_started=0
frontend_started=0

if start_service \
  "Backend" \
  "$BACKEND_PID_FILE" \
  "$BACKEND_LOG_FILE" \
  "$PROJECT_ROOT/.venv/bin/uvicorn" app.main:app --app-dir backend --reload; then
  backend_started=1
else
  status=$?
  [[ "$status" -eq 2 ]] || exit "$status"
fi

if start_service \
  "Frontend" \
  "$FRONTEND_PID_FILE" \
  "$FRONTEND_LOG_FILE" \
  npm --prefix "$PROJECT_ROOT/frontend" run dev -- --strictPort; then
  frontend_started=1
else
  status=$?
  if [[ "$status" -ne 2 ]]; then
    [[ "$backend_started" -eq 0 ]] || stop_started_service "$BACKEND_PID_FILE"
    exit "$status"
  fi
fi

if [[ "$backend_started" -eq 0 && "$frontend_started" -eq 0 ]]; then
  printf 'Learning-Control-Center is already running.\n'
else
  printf 'Learning-Control-Center is running in the background.\n'
fi

printf 'Open http://127.0.0.1:5173\n'
printf 'Logs: %s and %s\n' "$BACKEND_LOG_FILE" "$FRONTEND_LOG_FILE"
