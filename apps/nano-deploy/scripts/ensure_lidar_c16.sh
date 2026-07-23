#!/usr/bin/env bash
set -eo pipefail

ROOT_DIR="/home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy"
LOG_DIR="${ROOT_DIR}/dashboard_platform/runtime/logs"
PID_DIR="${ROOT_DIR}/dashboard_platform/runtime/pids"
PID_FILE="${PID_DIR}/lidar_c16.pid"
LOG_FILE="${LOG_DIR}/lidar_c16.log"
PARAMS_FILE="/home/nvidia/fastlio2_ws/install/lslidar_driver/share/lslidar_driver/params/lslidar_c16.yaml"
LIDAR_BIN="/home/nvidia/fastlio2_ws/install/lslidar_driver/lib/lslidar_driver/lslidar_c16driver_node"
TOPIC="/c16/lslidar_point_cloud"

mkdir -p "${LOG_DIR}" "${PID_DIR}"

topic_ready() {
  source /opt/ros/humble/setup.bash
  source /home/nvidia/fastlio2_ws/install/setup.bash
  # Do not pipe ros2 directly into grep -q while pipefail is enabled.  grep
  # may close the pipe as soon as it sees the publisher count, making ros2
  # exit with SIGPIPE and causing a false negative.
  local topic_info
  topic_info="$(ROS2CLI_NO_DAEMON=1 ros2 topic info "${TOPIC}" -v 2>/dev/null || true)"
  grep -q 'Publisher count: [1-9]' <<<"${topic_info}"
}

pid_is_lidar() {
  local pid="${1:-}"
  [[ -n "${pid}" && -r "/proc/${pid}/cmdline" ]] || return 1
  tr '\0' ' ' < "/proc/${pid}/cmdline" | grep -q 'lslidar_c16driver_node'
}

read_pid() {
  [[ -f "${PID_FILE}" ]] || return 1
  local pid
  pid="$(cat "${PID_FILE}")"
  pid_is_lidar "${pid}" || return 1
  printf '%s\n' "${pid}"
}

stop_driver() {
  local pid
  pid="$(read_pid 2>/dev/null || true)"
  if [[ -n "${pid}" ]]; then
    kill -INT "${pid}" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
      kill -0 "${pid}" 2>/dev/null || break
      sleep 0.4
    done
    kill -KILL "${pid}" 2>/dev/null || true
  fi
  rm -f "${PID_FILE}"
}

start_driver() {
  [[ -f "${PARAMS_FILE}" ]] || { echo "C16 lidar params not found: ${PARAMS_FILE}" >&2; return 1; }
  [[ -x "${LIDAR_BIN}" ]] || { echo "C16 lidar executable not found: ${LIDAR_BIN}" >&2; return 1; }
  if read_pid >/dev/null 2>&1; then
    echo "C16 lidar driver already running."
    return 0
  fi
  if topic_ready; then
    echo "C16 lidar topic already has a publisher."
    return 0
  fi

  rm -f "${PID_FILE}"
  nohup bash -lc "source /opt/ros/humble/setup.bash && source /home/nvidia/fastlio2_ws/install/setup.bash && exec '${LIDAR_BIN}' --ros-args -r __node:=lslidar_driver_node -r __ns:=/c16 --params-file '${PARAMS_FILE}'" \
    >"${LOG_FILE}" 2>&1 &
  local pid=$!
  printf '%s\n' "${pid}" > "${PID_FILE}"

  local deadline=$((SECONDS + 15))
  until topic_ready; do
    if ! pid_is_lidar "${pid}"; then
      echo "C16 lidar driver exited; see ${LOG_FILE}" >&2
      rm -f "${PID_FILE}"
      return 1
    fi
    if (( SECONDS >= deadline )); then
      echo "C16 lidar topic not ready after 15 seconds; see ${LOG_FILE}" >&2
      stop_driver
      return 1
    fi
    sleep 1
  done
  echo "C16 lidar ready on ${TOPIC}."
}

case "${1:-start}" in
  start) start_driver ;;
  stop) stop_driver; echo "C16 lidar stopped." ;;
  status)
    if read_pid >/dev/null 2>&1; then echo "C16 lidar running."; else echo "C16 lidar stopped."; fi
    ;;
  *) echo "Usage: $0 {start|stop|status}" >&2; exit 2 ;;
esac
