#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

cd "${REPO_ROOT}"
AUTH_TOKEN="$(env_value AUTH_TOKEN)"
BOARD_HOST="$(env_value BOARD_HOST)"

log "demo stack startup begin"
bash "${SCRIPT_DIR}/stop_demo_stack.sh" >/dev/null 2>&1 || true

start_component "lidar_driver" "source /home/nvidia/fastlio2_ws/install/setup.bash && ros2 run lslidar_driver lslidar_c16driver_node --ros-args -r __ns:=/c16 -r __node:=lslidar_driver_node --params-file /home/nvidia/fastlio2_ws/install/lslidar_driver/share/lslidar_driver/params/lslidar_c16.yaml"
wait_for_command "lidar topic" "source /home/nvidia/fastlio2_ws/install/setup.bash && source /home/nvidia/serial-ros2/install/setup.bash && source \"/home/nvidia/imu-driver/ROS2 SDK/yesense_ros2/install/setup.bash\" && ROS2CLI_NO_DAEMON=1 ros2 topic info /c16/lslidar_point_cloud -v | grep -E 'Publisher count: [1-9]'"

start_component "imu_driver" "source /home/nvidia/serial-ros2/install/setup.bash && source \"/home/nvidia/imu-driver/ROS2 SDK/yesense_ros2/install/setup.bash\" && ros2 launch yesense_std_ros2 yesense_node.launch.py"
wait_for_command "imu raw topic" "source /home/nvidia/serial-ros2/install/setup.bash && source \"/home/nvidia/imu-driver/ROS2 SDK/yesense_ros2/install/setup.bash\" && ROS2CLI_NO_DAEMON=1 ros2 topic info /imu/data_raw -v | grep -E 'Publisher count: [1-9]'"

start_component "imu_republisher" "source /home/nvidia/serial-ros2/install/setup.bash && source \"/home/nvidia/imu-driver/ROS2 SDK/yesense_ros2/install/setup.bash\" && python3 ${REPO_ROOT}/dashboard_platform/agent/imu_republisher.py --source-topic /imu/data_raw --target-topic /imu/data"
wait_for_command "imu standard topic" "source /home/nvidia/serial-ros2/install/setup.bash && source \"/home/nvidia/imu-driver/ROS2 SDK/yesense_ros2/install/setup.bash\" && ROS2CLI_NO_DAEMON=1 ros2 topic info /imu/data -v | grep -E 'Publisher count: [1-9]'"

start_component "agent" "cd ${REPO_ROOT} && PYTHONNOUSERSITE=1 ${REPO_ROOT}/.venv/bin/uvicorn dashboard_platform.agent.app:app --host 0.0.0.0 --port 9001"
wait_for_http "http://127.0.0.1:9001/health" "agent"

start_component "server" "cd ${REPO_ROOT} && PYTHONNOUSERSITE=1 ${REPO_ROOT}/.venv/bin/uvicorn dashboard_platform.server.app:app --host 0.0.0.0 --port 8080"
wait_for_http "http://127.0.0.1:8080/health" "server status api"

log "demo stack startup complete"
log "logs: ${LOG_DIR}"
log "open dashboard: http://${BOARD_HOST}:8080"
