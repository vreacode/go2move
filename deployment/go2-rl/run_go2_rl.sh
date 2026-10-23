#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 1 ]]; then
  echo "用法: GO2_RL_ARMED=YES $0 <network-interface>" >&2
  exit 2
fi

if [[ "${GO2_RL_ARMED:-NO}" != "YES" ]]; then
  echo "拒绝启动：完成吊架、急停、固件和策略核对后设置 GO2_RL_ARMED=YES。" >&2
  exit 2
fi

network="$1"
if [[ ! -d "/sys/class/net/${network}" ]]; then
  echo "网络接口不存在: ${network}" >&2
  exit 2
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
"${script_dir}/check_go2_rl.sh"

rl_root="${GO2_RL_ROOT:-/home/unitree/go2_rl_lab}"
controller="${rl_root}/deploy/robots/go2/build/go2_ctrl"
runtime_lib="${rl_root}/deploy/thirdparty/onnxruntime-linux-aarch64-1.16.3/lib"
export LD_LIBRARY_PATH="/home/unitree/.local/lib:${runtime_lib}:${LD_LIBRARY_PATH:-}"

echo "即将启动低层 Go2 控制器；Ctrl+C 只能结束本进程，现场必须准备物理急停。"
exec "$controller" --network "$network"
