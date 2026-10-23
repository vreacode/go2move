#!/usr/bin/env bash
set -euo pipefail
camera_model=d435i
camera_profile=full
while [ "$#" -gt 0 ]; do
  case "$1" in
    --model) camera_model="${2:?Missing camera model}"; shift 2 ;;
    --depth-only)
      [ "$camera_profile" = full ] || { echo 'Choose one camera profile' >&2; exit 2; }
      camera_profile=depth_only; shift ;;
    --raw)
      [ "$camera_profile" = full ] || { echo 'Choose one camera profile' >&2; exit 2; }
      camera_profile=raw; shift ;;
    --rgbd)
      [ "$camera_profile" = full ] || { echo 'Choose one camera profile' >&2; exit 2; }
      camera_profile=rgbd; shift ;;
    *) echo "Usage: $0 [--model d435i|d455] [--depth-only | --raw | --rgbd]" >&2; exit 2 ;;
  esac
done
case "$camera_model" in d435i|d455) ;; *) echo 'Unsupported camera model' >&2; exit 2 ;; esac
config_file="/home/unitree/go2_ros2_ws/platform_install/go2_bringup/share/go2_bringup/config/${camera_model}.yaml"
if [ "$camera_profile" != full ]; then
  config_file="${config_file%.yaml}_${camera_profile}.yaml"
fi
if [ "$camera_profile" = rgbd ]; then
  config_file="/home/unitree/go2move/ros2/go2_bringup/config/${camera_model}_rgbd.yaml"
fi
if [ ! -f "$config_file" ]; then echo "Missing camera profile: $config_file" >&2; exit 1; fi
# Reuse the existing lock and private SDK launcher to prevent duplicate drivers.
exec /home/unitree/go2move/deployment/go2/start_d435i.sh "config_file:=$config_file"
