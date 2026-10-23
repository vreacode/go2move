#!/usr/bin/env bash
set -euo pipefail
# Use the current user bus even from a desktop terminal with a stale D-Bus address.
user_bus="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/bus"
if [ -S "$user_bus" ]; then export DBUS_SESSION_BUS_ADDRESS="unix:path=$user_bus"; fi
camera_option="${1:-}"
case "$camera_option" in
  ''|--native) ;;
  *) echo 'Usage: start_go2_media.sh [--native]' >&2; exit 2 ;;
esac
systemctl --user daemon-reload
# Reuse the validated RGB-D startup and explicit camera-selection checks.
# Failure leaves the previous source selected and does not silently hide the error.
if [ "$camera_option" = --native ]; then
  /bin/bash /home/unitree/go2move/deployment/go2/start_person_rgbd.sh --native
else
  /bin/bash /home/unitree/go2move/deployment/go2/start_person_rgbd.sh
fi
systemctl --user start go2-health-monitor.service go2-native-camera.service go2-native-camera-ros.service go2-person-search.service nano-board-agent.service nano-dashboard-server.service
systemctl --user is-active go2-health-monitor.service go2-native-camera.service go2-native-camera-ros.service go2-person-search.service nano-board-agent.service nano-dashboard-server.service
if /home/unitree/go2move/.venv/bin/python /home/unitree/go2move/deployment/go2/media_policy.py; then
  systemctl --user start go2-voice.service
else
  printf '%s\n' '语音测试暂停；相机与静音找人已启动。'
fi
printf '%s\n' '相机与静止找人已启动：打开现有 Dashboard（8080），进入找人页面查看画面。'
