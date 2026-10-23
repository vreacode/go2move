#!/usr/bin/env bash
set -euo pipefail
# Existing read-only robot state + XT-16 + slam_toolbox; no control bridge.
systemctl --user start go2-health-monitor.service go2-platform.service go2-interfaces.service go2-depth-camera.service
systemctl --user is-active go2-health-monitor.service go2-platform.service go2-interfaces.service go2-depth-camera.service
printf '%s\n' 'XT-16 SLAM and RealSense perception services started. Camera profile follows go2-depth-camera.service; see the latest stream validation. Robot command output remains disabled.'
