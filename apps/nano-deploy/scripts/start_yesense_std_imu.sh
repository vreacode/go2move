#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="/home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy"

source /home/nvidia/serial-ros2/install/setup.bash
source "/home/nvidia/imu-driver/ROS2 SDK/yesense_ros2/install/setup.bash"

python3 "${REPO_ROOT}/dashboard_platform/agent/imu_republisher.py" --source-topic /imu/data_raw --target-topic /imu/data &
relay_pid=$!

cleanup() {
  kill "${relay_pid}" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

ros2 launch yesense_std_ros2 yesense_node.launch.py
