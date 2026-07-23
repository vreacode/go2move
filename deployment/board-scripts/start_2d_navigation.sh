#!/usr/bin/env bash
set -euo pipefail

LOG_DIR="/home/nvidia/2d_navigation_logs"
PID_FILE="/home/nvidia/2d_navigation_pids.txt"

cleanup() {
  if [[ -f "$PID_FILE" ]]; then
    while read -r line; do
      kill -INT "${line#*:}" 2>/dev/null || true
    done < "$PID_FILE"
  fi
}

if [[ "${1:-}" == "stop" ]]; then
  cleanup
  echo "All navigation processes stopped."
  exit 0
fi

MAP_DIR="/home/nvidia/Desktop/wheeltec_ros2/maps"
MAP_NAME="${1:-my_map}"
MAP_FILE="${MAP_DIR}/${MAP_NAME}.yaml"
PARAMS_FILE="/home/nvidia/Desktop/wheeltec_ros2/src/wheeltec_robot_nav2/param/wheeltec_params/param_top_akm_dl.yaml"
COLLISION_MONITOR_CONFIG="/home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy/config/navigation/collision_monitor.yaml"

if [[ ! -f "$MAP_FILE" ]]; then
  echo "Map not found: ${MAP_FILE}"
  echo "Available maps:"
  ls -1 "${MAP_DIR}"/*.yaml 2>/dev/null | sed 's#.*/##' | sed 's/.yaml$//' || true
  exit 1
fi

mkdir -p "$LOG_DIR"
rm -f "$PID_FILE"

cleanup
trap cleanup EXIT INT TERM

run_cmd() {
  local name="$1"
  local cmd="$2"
  bash -c "$cmd" >"${LOG_DIR}/${name}.log" 2>&1 &
  local pid=$!
  echo "${name}:${pid}" >>"$PID_FILE"
  echo "Started ${name} (pid ${pid})"
}

echo "Starting 2D navigation stack..."
echo "Map: ${MAP_FILE}"

run_cmd "base" \
  "source /opt/ros/humble/setup.bash && \
   source /home/nvidia/Desktop/wheeltec_ros2/install/setup.bash && \
   bash /home/nvidia/Desktop/wheeltec_ros2/start_base.sh"

run_cmd "yesense_imu" \
  "source /opt/ros/humble/setup.bash && \
   source /home/nvidia/serial-ros2/install/setup.bash && \
   source \"/home/nvidia/imu-driver/ROS2 SDK/yesense_ros2/install/setup.bash\" && \
   ros2 launch yesense_std_ros2 yesense_node.launch.py"

run_cmd "ekf" \
  "source /opt/ros/humble/setup.bash && \
   source /home/nvidia/Desktop/wheeltec_ros2/install/setup.bash && \
   ros2 launch turn_on_wheeltec_robot wheeltec_ekf.launch.py"

run_cmd "lidar" \
  "source /opt/ros/humble/setup.bash && \
   source /home/nvidia/Desktop/wheeltec_ros2/install/setup.bash && \
   source /home/nvidia/fastlio2_ws/install/setup.bash && \
   ros2 launch lslidar_driver lslidar_c16_launch.py"

run_cmd "pcl2scan" \
  "source /opt/ros/humble/setup.bash && \
   source /home/nvidia/fastlio2_ws/install/setup.bash && \
   ros2 run pointcloud_to_laserscan pointcloud_to_laserscan_node \
     --ros-args \
     --remap cloud_in:=/c16/lslidar_point_cloud \
     --remap scan:=/scan \
     -p target_frame:=laser \
     -p min_height:=-0.15 -p max_height:=0.20 \
     -p range_min:=0.05 -p range_max:=30.0 \
     -p scan_time:=0.1"

run_cmd "collision_monitor" \
  "source /opt/ros/humble/setup.bash && \
   ros2 launch nav2_collision_monitor collision_monitor_node.launch.py \
     params_file:=${COLLISION_MONITOR_CONFIG} \
     use_sim_time:=False"

run_cmd "nav2" \
  "source /opt/ros/humble/setup.bash && \
   source /home/nvidia/Desktop/wheeltec_ros2/install/setup.bash && \
   ros2 launch nav2_bringup bringup_launch.py \
     map:=${MAP_FILE} \
     params_file:=${PARAMS_FILE} \
     slam:=False \
     use_sim_time:=False \
     autostart:=True"

echo ""
echo "All processes launched."
echo "Logs: ${LOG_DIR}"
echo "PIDs: ${PID_FILE}"
echo ""
echo "To stop all:"
echo "  bash /home/nvidia/start_2d_navigation.sh stop"
echo ""
echo "Press Ctrl+C to stop all processes."
wait
