#!/usr/bin/env bash
set -eo pipefail
source /opt/ros/foxy/setup.bash
source /home/unitree/cyclonedds_ws/install/setup.bash
source /home/unitree/go2_ros2_ws/src/unitree_ros2/cyclonedds_ws/install/setup.bash
source /home/unitree/hesai_ros2_ws/install/setup.bash
source /home/unitree/go2_ros2_ws/platform_install/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export ROS_DOMAIN_ID=0
export PYTHONDONTWRITEBYTECODE=1
exec 9>/tmp/go2-platform.lock
flock -n 9 || { echo 'Go2 platform already running'; exit 1; }
if pgrep -x hesai_ros_drive >/dev/null; then
  echo 'An XT-16 driver is already running. Stop that known driver before launching this platform.' >&2
  exit 1
fi
cd /tmp
exec ros2 launch go2_bringup platform.launch.py "$@"
