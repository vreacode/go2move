#!/usr/bin/env bash

# Read-only health report for the vehicle ROS2 stack.
# Use --strict when this report should fail if a required check is missing.

set -u

STRICT=0
EXPECTED_NODE="${EXPECTED_NODE:-/wheeltec_robot}"
ROS_SETUP_BASH="${ROS_SETUP_BASH:-/opt/ros/humble/setup.bash}"
VEHICLE_SETUP_BASH="${VEHICLE_SETUP_BASH:-/home/nvidia/Desktop/wheeltec_ros2/install/setup.bash}"
LIDAR_SETUP_BASH="${LIDAR_SETUP_BASH:-/home/nvidia/fastlio2_ws/install/setup.bash}"
IMU_SETUP_BASH="${IMU_SETUP_BASH:-/home/nvidia/imu-driver/ROS2 SDK/yesense_ros2/install/setup.bash}"
CAMERA_RTSP_URL="${CAMERA_RTSP_URL:-}"
RATE_TIMEOUT_SECONDS="${RATE_TIMEOUT_SECONDS:-6}"

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

usage() {
  cat <<'USAGE'
Usage:
  bash tools/ros_healthcheck.sh [--strict]

The script only reads ROS graph information and system status.
Optional environment variables:
  ROS_SETUP_BASH, VEHICLE_SETUP_BASH, LIDAR_SETUP_BASH, IMU_SETUP_BASH
  EXPECTED_NODE, CAMERA_RTSP_URL, RATE_TIMEOUT_SECONDS
USAGE
}

pass_check() {
  PASS_COUNT=$((PASS_COUNT + 1))
  printf '[PASS] %s\n' "$*"
}

warn_check() {
  WARN_COUNT=$((WARN_COUNT + 1))
  printf '[WARN] %s\n' "$*"
}

fail_check() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  printf '[FAIL] %s\n' "$*"
}

for arg in "$@"; do
  case "$arg" in
    --strict)
      STRICT=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$arg" >&2
      usage >&2
      exit 2
      ;;
  esac
done

source_if_present() {
  local setup_file="$1"
  if [[ -f "$setup_file" ]]; then
    # Some vendor/ROS setup files reference variables that are unset when
    # sourced from a clean shell. Do not let that abort this read-only report.
    set +u
    # shellcheck disable=SC1090
    source "$setup_file"
    local source_status=$?
    set -u
    if [[ "$source_status" -ne 0 ]]; then
      warn_check "source failed: $setup_file"
    fi
  else
    warn_check "setup file not found: $setup_file"
  fi
}

printf '%s\n' '=== ROS2 vehicle health check (read-only) ==='
printf 'time: %s\n' "$(date --iso-8601=seconds)"
printf 'host: %s\n' "$(hostname)"
printf 'kernel: %s\n' "$(uname -sr)"

source_if_present "$ROS_SETUP_BASH"
source_if_present "$VEHICLE_SETUP_BASH"
source_if_present "$LIDAR_SETUP_BASH"
source_if_present "$IMU_SETUP_BASH"

if ! command -v ros2 >/dev/null 2>&1; then
  fail_check 'ros2 command is not available after sourcing setup files'
  printf '\nsummary: pass=%s warn=%s fail=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT"
  exit 1
fi

ROS2CLI_NO_DAEMON=1
export ROS2CLI_NO_DAEMON

NODE_LIST="$(ros2 node list 2>/dev/null || true)"
TOPIC_LIST="$(ros2 topic list 2>/dev/null || true)"

if [[ -n "$EXPECTED_NODE" ]]; then
  if grep -Fxq "$EXPECTED_NODE" <<<"$NODE_LIST"; then
    pass_check "node exists: $EXPECTED_NODE"
  else
    fail_check "node missing: $EXPECTED_NODE"
  fi
fi

check_topic() {
  local topic="$1"
  local info publisher_count subscriber_count
  if ! grep -Fxq "$topic" <<<"$TOPIC_LIST"; then
    fail_check "topic missing: $topic"
    return
  fi

  info="$(ros2 topic info "$topic" -v 2>/dev/null || true)"
  publisher_count="$(awk -F': ' '/Publisher count:/ {print $2; exit}' <<<"$info")"
  subscriber_count="$(awk -F': ' '/Subscription count:/ {print $2; exit}' <<<"$info")"
  publisher_count="${publisher_count:-unknown}"
  subscriber_count="${subscriber_count:-unknown}"
  pass_check "topic exists: $topic (publishers=$publisher_count subscribers=$subscriber_count)"
}

for topic in /odom /c16/lslidar_point_cloud /imu/data_raw /imu/data /cmd_vel_nav /scan /map /tf /tf_static; do
  check_topic "$topic"
done

if command -v timeout >/dev/null 2>&1; then
  check_topic_rate() {
    local topic="$1" info publisher_count hz_output rate
    if ! grep -Fxq "$topic" <<<"$TOPIC_LIST"; then
      return
    fi
    info="$(ros2 topic info "$topic" -v 2>/dev/null || true)"
    publisher_count="$(awk -F': ' '/Publisher count:/ {print $2; exit}' <<<"$info")"
    if [[ ! "$publisher_count" =~ ^[0-9]+$ || "$publisher_count" -eq 0 ]]; then
      warn_check "topic rate skipped; no active publisher: $topic"
      return
    fi
    hz_output="$(timeout "${RATE_TIMEOUT_SECONDS}s" ros2 topic hz "$topic" --window 5 2>&1 || true)"
    if grep -q 'average rate:' <<<"$hz_output"; then
      rate="$(awk -F': ' '/average rate:/ {print $2; exit}' <<<"$hz_output")"
      pass_check "topic rate: $topic = $rate"
    else
      warn_check "topic rate unavailable within timeout: $topic"
    fi
  }

  for topic in /odom /imu/data_raw /imu/data /c16/lslidar_point_cloud /scan; do
    check_topic_rate "$topic"
  done
fi

if [[ -n "$CAMERA_RTSP_URL" ]]; then
  if ! command -v ffprobe >/dev/null 2>&1; then
    warn_check 'CAMERA_RTSP_URL set but ffprobe is not installed'
  else
    stream_info="$(timeout 10s ffprobe -v error -rtsp_transport tcp -show_entries stream=codec_name,width,height -of default=noprint_wrappers=1 "$CAMERA_RTSP_URL" 2>/dev/null || true)"
    if [[ -n "$stream_info" ]]; then
      pass_check "RTSP stream readable: $stream_info"
    else
      fail_check 'RTSP stream could not be read'
    fi
  fi
else
  warn_check 'CAMERA_RTSP_URL is not set; camera check skipped'
fi

printf '\n%s\n' '--- system snapshot ---'
printf 'uptime: '
uptime || true
if command -v free >/dev/null 2>&1; then
  free -h | sed -n '1,3p'
fi
if command -v tegrastats >/dev/null 2>&1; then
  printf '%s\n' 'tegrastats sample:'
  timeout 3s tegrastats --interval 1000 2>/dev/null | sed -n '1,2p' || true
fi

printf '\nsummary: pass=%s warn=%s fail=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT"
if [[ "$STRICT" -eq 1 && "$FAIL_COUNT" -gt 0 ]]; then
  exit 1
fi
exit 0
