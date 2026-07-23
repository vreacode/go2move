#!/usr/bin/env bash
set -euo pipefail

source /home/nvidia/fastlio2_ws/install/setup.bash
ros2 launch lslidar_driver lslidar_c16_launch.py
