#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="/home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy"
RUNTIME_DIR="${REPO_ROOT}/dashboard_platform/runtime"
PID_DIR="${RUNTIME_DIR}/pids"
LOG_DIR="${RUNTIME_DIR}/logs"

mkdir -p "${PID_DIR}" "${LOG_DIR}"

timestamp() {
  date +"%Y-%m-%d %H:%M:%S"
}

log() {
  printf '[%s] %s\n' "$(timestamp)" "$*"
}

pid_file() {
  printf '%s/%s.pid\n' "${PID_DIR}" "$1"
}

log_file() {
  printf '%s/%s.log\n' "${LOG_DIR}" "$1"
}

env_value() {
  local key="$1"
  python3 - <<PY
from pathlib import Path
value = ""
for line in Path("${REPO_ROOT}/.env").read_text(encoding="utf-8").splitlines():
    if line.startswith("${key}="):
        value = line.split("=", 1)[1].strip()
        break
print(value)
PY
}

is_pid_running() {
  local pid="$1"
  [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1
}

start_component() {
  local name="$1"
  local command="$2"
  local pidfile
  pidfile="$(pid_file "${name}")"
  local logfile
  logfile="$(log_file "${name}")"

  stop_component "${name}" >/dev/null 2>&1 || true

  log "starting ${name}"
  setsid /bin/bash -lc "${command}" >>"${logfile}" 2>&1 &
  local pid=$!
  echo "${pid}" > "${pidfile}"
  sleep 1
  if ! is_pid_running "${pid}"; then
    log "failed: ${name} exited early. check ${logfile}"
    return 1
  fi
  log "started ${name} pid=${pid}"
}

stop_component() {
  local name="$1"
  local pidfile
  pidfile="$(pid_file "${name}")"
  if [[ -f "${pidfile}" ]]; then
    local pid
    pid="$(cat "${pidfile}")"
    if is_pid_running "${pid}"; then
      log "stopping ${name} pid=${pid}"
      kill -TERM "-${pid}" >/dev/null 2>&1 || kill -TERM "${pid}" >/dev/null 2>&1 || true
      for _ in $(seq 1 20); do
        if ! is_pid_running "${pid}"; then
          break
        fi
        sleep 0.5
      done
      if is_pid_running "${pid}"; then
        log "forcing ${name} pid=${pid}"
        kill -KILL "-${pid}" >/dev/null 2>&1 || kill -KILL "${pid}" >/dev/null 2>&1 || true
      fi
    fi
    rm -f "${pidfile}"
  fi
}

wait_for_http() {
  local url="$1"
  local label="$2"
  for _ in $(seq 1 30); do
    if python3 - <<PY >/dev/null 2>&1
import urllib.request
urllib.request.urlopen("${url}", timeout=2).read()
PY
    then
      log "${label} ready"
      return 0
    fi
    sleep 1
  done
  log "failed: ${label} not ready at ${url}"
  return 1
}

wait_for_command() {
  local label="$1"
  local command="$2"
  for _ in $(seq 1 30); do
    if /bin/bash -lc "${command}" >/dev/null 2>&1; then
      log "${label} ready"
      return 0
    fi
    sleep 1
  done
  log "failed: ${label} check did not pass"
  return 1
}
