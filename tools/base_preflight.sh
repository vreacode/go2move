#!/usr/bin/env bash
set -u

# Read-only preflight for the official Wheeltec base launch.
# This script never starts ROS, opens the serial port, or publishes velocity.

BASE_SCRIPT="${BASE_SCRIPT:-/home/nvidia/Desktop/wheeltec_ros2/start_base.sh}"
BASE_SETUP="${BASE_SETUP:-/home/nvidia/Desktop/wheeltec_ros2/install/local_setup.bash}"
ROS_SETUP="${ROS_SETUP:-/opt/ros/humble/setup.bash}"
SERIAL_PORT="${SERIAL_PORT:-/dev/ttyCH343USB0}"
PACKAGE_NAME="${PACKAGE_NAME:-turn_on_wheeltec_robot}"
STRICT=0

if [[ "${1:-}" == "--strict" ]]; then
  STRICT=1
fi

pass=0
warn=0
fail=0

pass_msg() { printf '[PASS] %s\n' "$*"; pass=$((pass + 1)); }
warn_msg() { printf '[WARN] %s\n' "$*"; warn=$((warn + 1)); }
fail_msg() { printf '[FAIL] %s\n' "$*"; fail=$((fail + 1)); }

if [[ -r "$ROS_SETUP" ]]; then
  pass_msg "ROS setup exists: $ROS_SETUP"
else
  fail_msg "ROS setup missing: $ROS_SETUP"
fi

if [[ -r "$BASE_SETUP" ]]; then
  pass_msg "Wheeltec setup exists: $BASE_SETUP"
else
  fail_msg "Wheeltec setup missing: $BASE_SETUP"
fi

if [[ -x "$BASE_SCRIPT" ]]; then
  pass_msg "base startup script is executable: $BASE_SCRIPT"
elif [[ -r "$BASE_SCRIPT" ]]; then
  warn_msg "base startup script is not executable; bash can still run it: $BASE_SCRIPT"
else
  fail_msg "base startup script missing: $BASE_SCRIPT"
fi

if [[ -e "$SERIAL_PORT" ]]; then
  if [[ -r "$SERIAL_PORT" && -w "$SERIAL_PORT" ]]; then
    pass_msg "serial device is readable and writable: $SERIAL_PORT"
  else
    fail_msg "serial device exists but is not readable/writable: $SERIAL_PORT"
  fi
else
  fail_msg "serial device missing: $SERIAL_PORT"
fi

if id -nG | tr ' ' '\n' | grep -qx dialout; then
  pass_msg "current user belongs to dialout"
else
  warn_msg "current user is not shown in dialout; serial access may fail"
fi

source_if_present() {
  local setup_file="$1"
  if [[ -r "$setup_file" ]]; then
    set +u
    # shellcheck disable=SC1090
    source "$setup_file"
    set -u
  fi
}

source_if_present "$ROS_SETUP"
source_if_present "$BASE_SETUP"

if command -v ros2 >/dev/null 2>&1; then
  pass_msg "ros2 command is available"
  package_prefix="$(ROS2CLI_NO_DAEMON=1 timeout 8 ros2 pkg prefix "$PACKAGE_NAME" 2>/dev/null || true)"
  if [[ -n "$package_prefix" ]]; then
    pass_msg "ROS package is discoverable: $PACKAGE_NAME"
  else
    fail_msg "ROS package is not discoverable: $PACKAGE_NAME"
  fi
else
  fail_msg "ros2 command is not available after sourcing setup files"
fi

if pgrep -af 'wheeltec_robot_node|turn_on_wheeltec_robot.launch.py' >/dev/null 2>&1; then
  warn_msg "a Wheeltec base process appears to be running; use the health check to inspect its ROS graph"
else
  pass_msg "no Wheeltec base process is currently running"
fi

printf 'summary pass=%d warn=%d fail=%d\n' "$pass" "$warn" "$fail"
if (( fail > 0 || (STRICT && warn > 0) )); then
  exit 1
fi
