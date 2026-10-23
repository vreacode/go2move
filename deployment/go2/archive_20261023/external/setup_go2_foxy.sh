#!/usr/bin/env bash
set -eo pipefail

# Isolated Unitree ROS 2 message workspace for the Go2 upper computer.
# This script only configures ROS 2 discovery and message types. It does not
# launch a node or invoke Sport, low-level, stand, lie-down, or velocity APIs.
source /opt/ros/foxy/setup.bash
source /home/unitree/go2_ros2_ws/src/unitree_ros2/cyclonedds_ws/install/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=file:///home/unitree/cyclonedds_ws/cyclonedds.xml
