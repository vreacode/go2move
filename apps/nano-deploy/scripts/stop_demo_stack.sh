#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

log "demo stack shutdown begin"

stop_component "server"
stop_component "agent"
stop_component "imu_republisher"
stop_component "imu_driver"
stop_component "lidar_driver"

log "demo stack shutdown complete"
