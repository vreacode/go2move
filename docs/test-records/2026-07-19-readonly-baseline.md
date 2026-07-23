# 2026-07-19 只读基线检查

## 执行范围

- `bash tools/base_preflight.sh`
- `bash tools/ros_healthcheck.sh`
- 没有启动底盘、雷达、IMU、建图或导航；没有发布速度指令。

## 底盘启动前检查

结果：`pass=8 warn=0 fail=0`

- ROS Humble setup 存在；
- Wheeltec workspace setup 存在；
- `/home/nvidia/Desktop/wheeltec_ros2/start_base.sh` 可执行；
- `/dev/ttyCH343USB0` 可读写；
- 当前用户具备 `dialout`；
- `ros2` 命令可用；
- `turn_on_wheeltec_robot` 包可发现；
- 当前没有 Wheeltec 底盘进程。

## ROS 运行时健康检查

由于底盘和传感器未启动，以下项目当前缺失：

- `/wheeltec_robot`
- `/odom`
- `/c16/lslidar_point_cloud`
- `/imu/data_raw`
- `/imu/data`
- `/cmd_vel_nav`
- `/scan`
- `/map`

`/tf` 和 `/tf_static` 仅有订阅端，没有运行时发布者。雷达和 IMU 频率也无法在只读检查窗口内取得。

## 系统快照

- 内核：Linux 5.15.148-tegra；
- 内存：约 7.4 GiB 总量，约 4.9 GiB 可用；
- 温度：约 52–55°C；
- GPU 使用率：约 18–29%；
- 当前没有底盘进程。

## 结论

板卡环境和底盘启动前条件已具备，下一次现场联调应按“启动底盘 → 重新运行只读 healthcheck → 单独确认雷达/IMU → 再做建图”的顺序进行。当前数据集仍只有 5 张未标注基线图，不能开始训练。
