#!/usr/bin/env bash
set -euo pipefail

rl_root="${GO2_RL_ROOT:-/home/unitree/go2_rl_lab}"
sdk_root="${UNITREE_SDK2_ROOT:-/home/unitree/unitree_sdk2}"
controller="${rl_root}/deploy/robots/go2/build/go2_ctrl"
policy_root="${rl_root}/logs/rsl_rl/unitree_go2_velocity"
runtime_lib="${rl_root}/deploy/thirdparty/onnxruntime-linux-aarch64-1.16.3/lib"
local_lib="/home/unitree/.local/lib"
runtime_path="${local_lib}:${runtime_lib}:${LD_LIBRARY_PATH:-}"

status=0

check_path() {
  local label="$1"
  local path="$2"
  if [[ -e "$path" ]]; then
    echo "[OK] ${label}: ${path}"
  else
    echo "[缺失] ${label}: ${path}"
    status=1
  fi
}

check_path "unitree_rl_lab" "$rl_root"
check_path "unitree_sdk2" "$sdk_root"
check_path "Go2 ARM64 控制器" "$controller"
check_path "Go2 策略目录" "$policy_root"

if [[ -x "$controller" ]]; then
  if env LD_LIBRARY_PATH="$runtime_path" ldd "$controller" | grep -q "not found"; then
    echo "[错误] 控制器存在未解析的动态库"
    env LD_LIBRARY_PATH="$runtime_path" ldd "$controller" | grep "not found"
    status=1
  else
    echo "[OK] 控制器动态库完整"
  fi
fi

if [[ -d "$policy_root" ]]; then
  policy="$(find "$policy_root" -type f -path '*/exported/policy.onnx' -print -quit)"
  params="$(find "$policy_root" -type f -path '*/params/deploy.yaml' -print -quit)"
  check_path "policy.onnx" "$policy"
  check_path "deploy.yaml" "$params"
fi

if [[ "$status" -ne 0 ]]; then
  echo "检查未通过；不要启动真机控制器。"
  exit "$status"
fi

echo "板载控制端检查通过。此结果不代表策略已经通过 Sim2Sim 或真机安全验证。"
