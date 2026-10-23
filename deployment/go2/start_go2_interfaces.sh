#!/usr/bin/env bash
set -eo pipefail
source /home/unitree/go2move/deployment/go2/source_upstream_go2_foxy.sh
export CYCLONEDDS_URI=/home/unitree/go2move/ros2/go2_bringup/config/dds/interfaces.xml
exec 9>/tmp/go2-interfaces.lock
flock -n 9 || { echo 'Go2 interface service already running'; exit 1; }
# The launch fixes motion_enabled=false and leaves state/TF ownership with
# the existing state_bridge. It exposes the upstream services but rejects RPCs.
exec ros2 launch go2_driver interfaces.launch.py
