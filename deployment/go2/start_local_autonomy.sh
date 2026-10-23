#!/usr/bin/env bash
set -eo pipefail
source /home/unitree/go2move/deployment/go2/source_go2_platform.sh
exec python3 -B -u /home/unitree/go2move/deployment/go2/run_local_autonomy.py "$@"
