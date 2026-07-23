# ROS 实机基线记录

## 基本信息

- 日期：2026-07-17
- 设备：Jetson，Linux 5.15.148-tegra
- 车辆：低速阿克曼底盘
- 安全状态：车轮悬空，急停可用；未进行运动测试
- 相机：`rtsp://<CAMERA_HOST>:8554/video1`

## 启动顺序

1. `/home/nvidia/Desktop/wheeltec_ros2/start_base.sh`
2. C16 雷达驱动：`lslidar_c16driver_node`
3. 点云转激光：`pointcloud_to_laserscan_node`
4. `/home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy/scripts/start_mapping_integrated.sh`
5. Dashboard Agent `:9001` 和 Server `:8080`

本次没有启动导航，也没有发布 `/cmd_vel_nav` 速度指令。没有启动额外的 Yesense 驱动，避免与官方底盘提供的 `/imu/data_raw` 争用串口。

随后停止 SLAM，使用同一份硬件链路启动 Nav2 节点预检；预检阶段仍未发送导航目标。

## 验收结果

健康检查命令：

```bash
cd /home/nvidia/carmove
CAMERA_RTSP_URL=rtsp://<CAMERA_HOST>:8554/video1 bash tools/ros_healthcheck.sh
```

最终结果：`pass=13 warn=0 fail=0`。

- `/wheeltec_robot`、`/odom`：通过
- `/c16/lslidar_point_cloud`：通过，约 19.9 Hz
- `/imu/data_raw`、`/imu/data`：通过，滤波 IMU 约 20.0 Hz
- `/scan`、`/map`：通过
- `/tf`、`/tf_static`：通过
- `/cmd_vel_nav`：在 SLAM 健康检查阶段无发布者、1 个底盘订阅者；Nav2 预检阶段建立了控制器发布端，但未产生速度消息
- RTSP：通过，1920×1080，HEVC
- 资源：内存约 3.9/7.6 GiB 可用，温度约 50.6°C
- Agent `GET /health`：通过
- Server `GET /health`：通过

## 注意事项

SLAM 启动日志出现过一次队列丢帧提示和激光最小距离配置提示；本次健康检查中点云和 IMU 频率稳定，暂不作为阻塞问题。下一次应在安全封闭场地低速移动，验证地图是否连续、定位是否稳定，再进入 Nav2。

## Nav2 节点预检

在停止 SLAM、改由 Nav2 的 `map_server` 发布 `gmap_map` 后启动：

```bash
bash /home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy/scripts/start_navigation_integrated.sh gmap_map
```

结果：

- `map_server`、`amcl`、`controller_server`、`planner_server`、`bt_navigator`、局部/全局 costmap 均为 `active [3]`；
- `/map` 由 `map_server` 单独发布，避免与 SLAM 同时发布；
- `/amcl_pose` 已有位姿输出，初始位姿约为 `(0.0025, 0.0000)`；
- `/cmd_vel_nav` 已建立控制器发布端，但本次没有发送导航目标，采样期间没有速度消息；
- `/navigate_to_pose` 只有 Action Server，没有执行中的目标。

因此当前状态是“导航栈就绪、车辆未运动”。真正发送目标点前，必须切换到地面安全区域，确认急停和人工接管有效，并先做低速短距离测试。

## 首个低速导航目标

场地确认安全后，按 AMCL 当前朝向发送了一个约 0.25 m 的短距离目标：

```text
frame: map
goal: x=-0.052, y=0.280, yaw≈1.72 rad
```

- Nav2 Action：`/navigate_to_pose`
- 结果：`SUCCEEDED`
- 最终 AMCL 位姿约为 `(-0.093, 0.171)`，落在目标容差内；
- 目标结束后 `/cmd_vel_nav` 无持续输出；
- `/odom` 线速度和角速度均为 `0.0`。

这只证明了“单目标、短距离、低速”链路可工作；还不能代表巡查路线、连续定位和障碍物避让已经验收。

## 相机采集冒烟测试

使用 `tools/collect_rtsp_frames.py` 从相机采集 5 帧，分辨率均为 1920×1080，CSV 清单已生成在设备本地：

```text
datasets/local/roadside_v1/manifests/baseline_20260717.csv
```

该批图像仅用于验证 RTSP 和数据落盘，不作为训练集。画面包含人员和室内环境，原图保持在本地并被 `.gitignore` 忽略；正式采集垃圾/车辆数据前，需要筛选目标场景并处理人员、车牌等隐私内容。
