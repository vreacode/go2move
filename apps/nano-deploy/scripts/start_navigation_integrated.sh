#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy"
LOG_DIR="${ROOT_DIR}/dashboard_platform/runtime/logs"
PID_DIR="${ROOT_DIR}/dashboard_platform/runtime/pids"
PID_FILE="${PID_DIR}/navigation_integrated.pids"
LAUNCHER_PID_FILE="${PID_DIR}/navigation_integrated.launcher.pid"
MAP_DIR="/home/nvidia/Desktop/wheeltec_ros2/maps"
PARAMS_FILE="/home/nvidia/Desktop/wheeltec_ros2/src/wheeltec_robot_nav2/param/wheeltec_params/param_top_akm_dl.yaml"
COLLISION_MONITOR_CONFIG="${ROOT_DIR}/config/navigation/collision_monitor.yaml"
MAP_VALIDATOR="/home/nvidia/carmove/tools/validate_ros_map.py"
LIDAR_SCRIPT="${ROOT_DIR}/scripts/ensure_lidar_c16.sh"

mkdir -p "${LOG_DIR}" "${PID_DIR}"

wait_for_ready() {
  local label="$1"
  local script="$2"
  local timeout_secs="$3"
  local deadline=$((SECONDS + timeout_secs))
  until bash -lc "${script}" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      echo "${label} not ready" >&2
      return 1
    fi
    sleep 1
  done
}

preflight() {
  local map_name="$1"
  local map_file="${MAP_DIR}/${map_name}.yaml"

  if [[ ! -f "${PARAMS_FILE}" ]]; then
    echo "Navigation params file not found: ${PARAMS_FILE}" >&2
    return 1
  fi
  if [[ ! -f "${COLLISION_MONITOR_CONFIG}" ]]; then
    echo "Collision monitor config not found: ${COLLISION_MONITOR_CONFIG}" >&2
    return 1
  fi
  if [[ ! -f "${map_file}" ]]; then
    echo "Map not found: ${map_file}" >&2
    return 1
  fi
  if [[ ! -f "${MAP_VALIDATOR}" ]]; then
    echo "Map validator not found: ${MAP_VALIDATOR}" >&2
    return 1
  fi
  python3 "${MAP_VALIDATOR}" --map "${map_file}" >/dev/null

  "${LIDAR_SCRIPT}" start
  wait_for_ready "wheeltec_robot" "source /opt/ros/humble/setup.bash && source /home/nvidia/Desktop/wheeltec_ros2/install/setup.bash && ROS2CLI_NO_DAEMON=1 ros2 node list | grep -q '^/wheeltec_robot$'" 12
  wait_for_ready "/odom" "source /opt/ros/humble/setup.bash && source /home/nvidia/Desktop/wheeltec_ros2/install/setup.bash && ROS2CLI_NO_DAEMON=1 ros2 topic info /odom -v | grep -q 'Publisher count: 1'" 12
  wait_for_ready "/c16/lslidar_point_cloud" "source /opt/ros/humble/setup.bash && source /home/nvidia/fastlio2_ws/install/setup.bash && ROS2CLI_NO_DAEMON=1 ros2 topic info /c16/lslidar_point_cloud -v | grep -q 'Publisher count: 1'" 12
}

cleanup() {
  if [[ -f "${PID_FILE}" ]]; then
    while IFS=: read -r _ pid; do
      [[ -n "${pid:-}" ]] || continue
      # Each component is launched in its own session so the ros2 wrapper and
      # the actual executable are stopped together.
      kill -TERM -- "-${pid}" 2>/dev/null || kill -TERM "${pid}" 2>/dev/null || true
      sleep 0.5
      kill -KILL -- "-${pid}" 2>/dev/null || true
    done < "${PID_FILE}"
    rm -f "${PID_FILE}"
  fi
  rm -f "${LAUNCHER_PID_FILE}"
}

stop_launcher() {
  [[ -f "${LAUNCHER_PID_FILE}" ]] || return 0
  local pid cmdline
  pid="$(cat "${LAUNCHER_PID_FILE}" 2>/dev/null || true)"
  if [[ -n "${pid}" && "${pid}" != "$$" && -r "/proc/${pid}/cmdline" ]]; then
    cmdline="$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true)"
    if [[ "${cmdline}" == *"start_navigation_integrated.sh"* ]]; then
      kill -TERM "${pid}" 2>/dev/null || true
    fi
  fi
  rm -f "${LAUNCHER_PID_FILE}"
}

run_cmd() {
  local name="$1"
  local cmd="$2"
  setsid bash -lc "${cmd}" >"${LOG_DIR}/${name}.log" 2>&1 &
  local pid=$!
  echo "${name}:${pid}" >> "${PID_FILE}"
}

ACTION="${1:-start}"
if [[ "${ACTION}" == "stop" ]]; then
  stop_launcher
  cleanup
  echo "Navigation processes stopped."
  exit 0
fi

if [[ "${ACTION}" == "check" ]]; then
  MAP_NAME="${2:-gmap_map}"
else
  MAP_NAME="${1:-gmap_map}"
fi
MAP_FILE="${MAP_DIR}/${MAP_NAME}.yaml"

if [[ "${ACTION}" == "check" ]]; then
  preflight "${MAP_NAME}"
  echo "Navigation preflight ready for ${MAP_NAME}."
  exit 0
fi

cleanup
printf '%s\n' "$$" > "${LAUNCHER_PID_FILE}"
trap cleanup EXIT INT TERM
preflight "${MAP_NAME}"

run_cmd "nav_pcl2scan" \
  "source /opt/ros/humble/setup.bash && \
   source /home/nvidia/Desktop/wheeltec_ros2/install/setup.bash && \
   source /home/nvidia/fastlio2_ws/install/setup.bash && \
   ros2 run pointcloud_to_laserscan pointcloud_to_laserscan_node \
     --ros-args \
     --remap cloud_in:=/c16/lslidar_point_cloud \
     --remap scan:=/scan \
     -p target_frame:=laser \
     -p min_height:=-0.15 \
     -p max_height:=0.20 \
     -p range_min:=0.05 \
     -p range_max:=30.0 \
     -p scan_time:=0.1"

wait_for_ready "/scan" "source /opt/ros/humble/setup.bash && source /home/nvidia/Desktop/wheeltec_ros2/install/setup.bash && ROS2CLI_NO_DAEMON=1 ros2 topic info /scan -v | grep -q 'Publisher count: 1'" 25

run_cmd "collision_monitor" \
  "source /opt/ros/humble/setup.bash && \
   ros2 launch nav2_collision_monitor collision_monitor_node.launch.py \
     params_file:=${COLLISION_MONITOR_CONFIG} \
     use_sim_time:=False"

run_cmd "nav2_integrated" \
  "source /opt/ros/humble/setup.bash && \
   source /home/nvidia/Desktop/wheeltec_ros2/install/setup.bash && \
   ros2 launch nav2_bringup bringup_launch.py \
     map:=${MAP_FILE} \
     params_file:=${PARAMS_FILE} \
     slam:=False \
     use_sim_time:=False \
     autostart:=True"

wait
