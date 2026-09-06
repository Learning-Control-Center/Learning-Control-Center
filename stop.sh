#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIRECTORY="$PROJECT_ROOT/tmp/learning-control-center"
BACKEND_PID_FILE="$RUN_DIRECTORY/backend.pid"
FRONTEND_PID_FILE="$RUN_DIRECTORY/frontend.pid"

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

stop_service() {
  local name="$1"
  local pid_file="$2"
  local pid

  if ! service_is_running "$pid_file"; then
    rm -f -- "$pid_file"
    printf '%s is not running.\n' "$name"
    return 0
  fi

  read -r pid _ < "$pid_file"
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true

  for _ in {1..20}; do
    if ! service_is_running "$pid_file"; then
      rm -f -- "$pid_file"
      printf '%s stopped.\n' "$name"
      return 0
    fi
    sleep 0.25
  done

  printf '%s did not stop gracefully; forcing it to stop.\n' "$name"
  kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
  rm -f -- "$pid_file"
  printf '%s stopped.\n' "$name"
}

stop_service "Frontend" "$FRONTEND_PID_FILE"
stop_service "Backend" "$BACKEND_PID_FILE"
printf 'Learning-Control-Center is stopped.\n'
