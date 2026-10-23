#!/usr/bin/env bash
set -euo pipefail
# Desktop sessions may retain a separate, stale D-Bus address.
user_bus="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/bus"
if [ -S "$user_bus" ]; then export DBUS_SESSION_BUS_ADDRESS="unix:path=$user_bus"; fi
camera_source=realsense
case "${1:-}" in
  '') ;;
  --native) camera_source=native ;;
  *) echo 'Usage: start_person_rgbd.sh [--native]' >&2; exit 2 ;;
esac
if [ "$camera_source" = realsense ]; then
  systemctl --user start go2-rgbd-camera.service
else
  systemctl --user start go2-native-camera.service
fi
/home/unitree/go2move/.venv/bin/python -B /home/unitree/go2move/deployment/go2/person_camera.py "$camera_source"
systemctl --user restart go2-person-search.service
printf '%s\n' "Person search camera: $camera_source"
