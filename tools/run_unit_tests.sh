#!/usr/bin/env bash
set -euo pipefail

# Run only the project's dependency-light tests. This intentionally avoids
# recursive discovery from apps/nano-deploy, which also imports optional model
# logger packages that are not required for the core tests.
if [[ -n "${PYTHON_BIN:-}" ]]; then
  UNIT_TEST_PYTHON="${PYTHON_BIN}"
elif [[ -x "/home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy/.venv/bin/python" ]]; then
  UNIT_TEST_PYTHON="/home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy/.venv/bin/python"
else
  UNIT_TEST_PYTHON="python3"
fi

"$UNIT_TEST_PYTHON" -m unittest discover -s apps/nano-deploy/camera -p 'test_*.py'
"$UNIT_TEST_PYTHON" -m unittest discover -s apps/nano-deploy/vision -p 'test_*.py'
"$UNIT_TEST_PYTHON" -m unittest tools.test_dataset_tools
"$UNIT_TEST_PYTHON" -m unittest tools.test_capture_manifest
"$UNIT_TEST_PYTHON" -m unittest tools.test_collect_rtsp_frames
(
  cd apps/nano-deploy
  "$UNIT_TEST_PYTHON" -m unittest dashboard_platform.agent.test_motion
  "$UNIT_TEST_PYTHON" -m unittest dashboard_platform.agent.test_runtime
  "$UNIT_TEST_PYTHON" -m unittest dashboard_platform.agent.test_schemas
  "$UNIT_TEST_PYTHON" -m unittest dashboard_platform.agent.test_capture
  "$UNIT_TEST_PYTHON" -m unittest dashboard_platform.agent.test_battery
  "$UNIT_TEST_PYTHON" -m unittest dashboard_platform.agent.test_serial_voltage_bridge
  "$UNIT_TEST_PYTHON" -m unittest dashboard_platform.server.test_app
)
"$UNIT_TEST_PYTHON" -m unittest tools.test_validate_ros_map
