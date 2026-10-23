# Go2 强化学习部署

本目录记录板载 ARM64 控制端的部署入口。训练在另一台 x86_64 NVIDIA
GPU 机器上完成，板载机只负责加载导出的 ONNX 策略并通过 Unitree SDK2
运行控制器。

## 当前安装

- 框架：`/home/unitree/go2_rl_lab`
- SDK：`/home/unitree/unitree_sdk2`
- 本地 SDK 安装前缀：`/home/unitree/.local`
- 控制器：`/home/unitree/go2_rl_lab/deploy/robots/go2/build/go2_ctrl`
- 推理库：ONNX Runtime 1.16.3 ARM64

上游自带的 ONNX Runtime 1.22.0 是 x86_64 版本，不能直接用于这台
Jetson。板端构建已改为使用 ARM64 版本，并统一使用本地安装的 SDK2 和
CycloneDDS 头文件。

## 策略交付格式

`config/config.yaml` 默认从下面目录寻找最新一次训练：

```text
/home/unitree/go2_rl_lab/logs/rsl_rl/unitree_go2_velocity/
└── <run>/
    ├── exported/
    │   └── policy.onnx
    └── params/
        └── deploy.yaml
```

训练机使用 `unitree_rl_lab` 的 Go2 Velocity 任务训练和 `play.py` 导出后，
把完整的 `<run>` 目录复制到板载机。不要只复制 `policy.onnx`；
`deploy.yaml` 定义观测量顺序、缩放和控制周期，必须和策略严格配套。

## 只读检查

```bash
deployment/go2-rl/check_go2_rl.sh
```

检查不会连接 DDS，也不会发送控制命令。

## 真机启动

必须先确认：

1. 机器人型号及固件支持底层电机控制；
2. 已关闭机身原有运动控制服务，避免两个进程同时发布 `lowcmd`；
3. 机器人使用吊架，四足离地，急停和遥控器可立即使用；
4. 策略先通过 Isaac Lab Play 和 MuJoCo Sim2Sim；
5. `deploy.yaml`、关节顺序、默认姿态和训练策略来自同一次导出。

满足条件后才执行：

```bash
GO2_RL_ARMED=YES deployment/go2-rl/run_go2_rl.sh eth0
```

控制器启动后仍处于 Passive 状态。遥控器 `LT + A` 进入 FixStand，
`Start` 才进入 RL Velocity；`LT + B` 返回 Passive。首次测试不要让足端
接触地面。

