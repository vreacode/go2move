#!/usr/bin/env bash
set -euo pipefail
# Explicit stationary vision session. No audio, scan or movement service starts.
base=/home/unitree/go2move/deployment/go2
case "${1:-}" in
  '') bash "$base/start_person_rgbd.sh" --native ;;
  --rgbd) bash "$base/start_person_rgbd.sh" ;;
  *) echo 'Usage: start_helmet_demo.sh [--rgbd]' >&2; exit 2 ;;
esac
systemctl --user start nano-board-agent.service nano-dashboard-server.service
/home/unitree/go2move/.venv/bin/python -B - <<'PY'
import json,time,urllib.request
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
for attempt in range(30):
    try:
        with opener.open('http://127.0.0.1:8768/helmet/status',timeout=2) as response:
            json.load(response)
        break
    except OSError:
        if attempt==29:raise
        time.sleep(.5)
with opener.open('http://127.0.0.1:8768/helmet/start',data=b'',timeout=45) as response:
    status=json.load(response)
assert status['active'] and status['motion_enabled'] is False
print('安全帽多人识别已启动；打开前端的“安全帽多人识别”，点击“录制 Demo”。机器狗保持静止。')
PY
