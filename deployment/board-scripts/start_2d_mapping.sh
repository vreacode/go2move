#!/usr/bin/env bash
set -euo pipefail

LOG_DIR="/home/nvidia/2d_mapping_logs"
PID_FILE="/home/nvidia/2d_mapping_pids.txt"

cleanup() {
  if [[ -f "$PID_FILE" ]]; then
    while read -r line; do
      kill -INT "${line#*:}" 2>/dev/null || true
    done < "$PID_FILE"
  fi
}

if [[ "${1:-}" == "stop" ]]; then
  cleanup
  echo "All mapping processes stopped."
  exit 0
fi

if [[ "${1:-}" == "savemap" ]]; then
  set +u
  source /opt/ros/humble/setup.bash
  set -u
  MAP_PATH="/home/nvidia/Desktop/wheeltec_ros2/maps"
  MAP_NAME="${2:-my_map}"
  ros2 run nav2_map_server map_saver_cli -f "${MAP_PATH}/${MAP_NAME}" \
    --ros-args -p map_subscribe_transient_local:=true -p save_map_timeout:=10.0 -p map_topic:=/map
  exit $?
fi

MAPPING_MODE="slam_toolbox"
if [[ "${1:-}" == "gmapping" ]]; then
  MAPPING_MODE="gmapping"
  shift
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

echo "Starting 2D mapping stack... (mode: ${MAPPING_MODE})"

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
     -p range_min:=0.1 -p range_max:=30.0 \
     -p scan_time:=0.1"

if [[ "$MAPPING_MODE" == "gmapping" ]]; then
  run_cmd "slam_gmapping" \
    "source /opt/ros/humble/setup.bash && \
     source /home/nvidia/Desktop/wheeltec_ros2/install/setup.bash && \
     ros2 run slam_gmapping slam_gmapping \
       --ros-args \
       -p base_frame:=base_footprint \
       -p odom_frame:=odom_combined \
       -p map_frame:=map \
       -p maxUrange:=15.0 \
       -p maxRange:=30.0 \
       -p delta:=0.05 \
       -p linearUpdate:=0.2 \
       -p angularUpdate:=0.1 \
       -p temporalUpdate:=-1.0 \
       -p particles:=30 \
       -p lstep:=0.05 \
       -p astep:=0.05 \
       -p iterations:=5 \
       -p lsigma:=0.075 \
       -p ogain:=3.0 \
       -p lskip:=0"
else
  run_cmd "slam_toolbox" \
    "source /opt/ros/humble/setup.bash && \
     source /home/nvidia/Desktop/wheeltec_ros2/install/setup.bash && \
     ros2 run slam_toolbox async_slam_toolbox_node \
       --ros-args --params-file /home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy/config/navigation/slam_toolbox_mapping.yaml"
fi

echo ""
echo "All processes launched."
echo "Logs: ${LOG_DIR}"
echo "PIDs: ${PID_FILE}"
echo ""
echo "To stop all:"
echo "  bash /home/nvidia/start_2d_mapping.sh stop"
echo ""
echo "Press Ctrl+C to stop all processes."
wait
