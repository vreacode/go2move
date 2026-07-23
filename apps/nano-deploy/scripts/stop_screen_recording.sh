#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/screen_recording_common.sh"

response="$(call_dev_recording_api "POST" "/api/dev-recording/stop")"
echo "screen recording stop request completed."
print_dev_recording_status "${response}"
