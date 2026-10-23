#!/usr/bin/env bash
source /opt/ros/foxy/setup.bash
source /home/unitree/cyclonedds_ws/install/setup.bash
source /home/unitree/go2_ros2_ws/src/unitree_ros2/cyclonedds_ws/install/setup.bash
source /home/unitree/hesai_ros2_ws/install/setup.bash
source /home/unitree/go2_ros2_ws/platform_install/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp ROS_DOMAIN_ID=0 PYTHONDONTWRITEBYTECODE=1
export CYCLONEDDS_URI=/home/unitree/go2move/ros2/go2_bringup/config/dds/cli.xml
