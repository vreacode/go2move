#!/usr/bin/env bash
set -eo pipefail
source /home/unitree/go2move/deployment/go2/source_go2_platform.sh
export CYCLONEDDS_URI=/home/unitree/go2move/ros2/go2_bringup/config/dds/rviz.xml
exec rviz2 -d "${1:-/home/unitree/go2move/ros2/go2_bringup/rviz/depth_camera.rviz}"
