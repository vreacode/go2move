#!/usr/bin/env bash
set -eo pipefail

MAP_NAME="${1:-gmap_map}"
MAPS_DIRECTORY="${MAPS_DIRECTORY:-/home/nvidia/Desktop/wheeltec_ros2/maps}"
MAP_TOPIC="${MAP_TOPIC:-/map}"
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-20}"
SAVE_TIMEOUT_SECONDS="${SAVE_TIMEOUT_SECONDS:-15.0}"

source /opt/ros/humble/setup.bash

mkdir -p "${MAPS_DIRECTORY}"

topic_has_publisher() {
  local info
  info="$(ROS2CLI_NO_DAEMON=1 ros2 topic info "${MAP_TOPIC}" -v 2>/dev/null || true)"
  [[ "${info}" =~ Publisher\ count:\ ([1-9][0-9]*) ]]
}

deadline=$((SECONDS + WAIT_TIMEOUT_SECONDS))
while (( SECONDS < deadline )); do
  if topic_has_publisher; then
    break
  fi
  sleep 1
done

if ! topic_has_publisher; then
  echo "save_map failed: ${MAP_TOPIC} is not ready" >&2
  ROS2CLI_NO_DAEMON=1 ros2 topic info "${MAP_TOPIC}" -v || true
  exit 1
fi

PYTHONNOUSERSITE=1 ROS2CLI_NO_DAEMON=1 ros2 run nav2_map_server map_saver_cli \
  -f "${MAPS_DIRECTORY}/${MAP_NAME}" \
  --ros-args \
  -p map_topic:="${MAP_TOPIC}" \
  -p map_subscribe_transient_local:=true \
  -p save_map_timeout:="${SAVE_TIMEOUT_SECONDS}"
