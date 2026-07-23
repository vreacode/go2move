#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/screen_recording_common.sh"

response="$(call_dev_recording_api "GET" "/api/dev-recording/status")"
print_dev_recording_status "${response}"
