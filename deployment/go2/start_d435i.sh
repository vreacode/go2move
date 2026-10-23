#!/usr/bin/env bash
set -eo pipefail
source /home/unitree/go2move/deployment/go2/source_go2_platform.sh
exec 9>/tmp/go2-d435i.lock
flock -n 9 || { echo 'D435i launcher already running'; exit 1; }
if pgrep -f '^/home/unitree/go2_ros2_ws/(platform_install|realsense_rsusb_install)/realsense2_camera/lib/realsense2_camera/realsense2_camera_node' >/dev/null; then
  echo 'D435i ROS2 driver already running'; exit 1
fi
exec ros2 launch go2_bringup d435i.launch.py "$@"
