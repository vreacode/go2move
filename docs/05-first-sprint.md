# 第一轮推进 Sprint：ROS 基线 + 数据采集

## 目标

先证明“车能稳定巡查、相机能稳定采集、数据能规范沉淀”，再开始训练垃圾/违停模型。

## 任务 A：ROS 健康检查

在车辆安全静止、急停可用的情况下，先启动已有底盘、雷达和 IMU 链路，再运行：

    cd /home/nvidia/carmove
    bash tools/ros_healthcheck.sh

启动底盘前先执行只读启动前检查：

    bash tools/base_preflight.sh

该检查不会启动 ROS、打开串口或发布速度指令。当前确认的官方底盘入口是
`/home/nvidia/Desktop/wheeltec_ros2/start_base.sh`，串口配置为
`/dev/ttyCH343USB0`，速度话题为 `/cmd_vel_nav`。

需要重点观察：

- /wheeltec_robot 是否存在；
- /odom、/c16/lslidar_point_cloud、/imu/data_raw、/imu/data 是否存在；
- /cmd_vel_nav 是否存在但仍保持 dry-run/locked；
- /scan 和 /map 是否在建图或导航阶段出现；
- 雷达和 IMU 的 topic rate 是否稳定；
- CPU 和内存是否有余量。

2026-07-19 已完成一次架空状态下的基础链路短测：串口、`/odom`、IMU、TF、EKF 正常，`/cmd_vel_nav` 无发布者；雷达、`/scan`、`/map` 尚未启动。启动文件中的串口参数名也已修复为驱动实际读取的 `usart_port_name`。

只有确认链路正常，才进入低速封闭场地巡查。

## 任务 B：采集第一批场景数据

示例命令：

    cd /home/nvidia/carmove
    source /home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy/.venv/bin/activate
    python tools/collect_rtsp_frames.py \
      --source rtsp://设备相机地址 \
      --output datasets/local/roadside_v1/images/raw_day_01 \
      --interval 1.0 \
      --scene gate_a_day \
      --note "固定路线，白天，采集垃圾和车辆"

采集结束后，人工筛选图片并分别放入 train/val，再用 CVAT、LabelImg 或 Roboflow 按 datasets/README.md 的格式标注。

## 本轮验收

- ROS 健康检查有一份保存的终端输出；
- 至少完成 3 个不同场景的采集记录；
- 每个场景都包含正常道路、垃圾、车辆和困难样本；
- 选定最终类别定义，不在训练中途随意改名；
- 不把图片、视频、车牌、人脸和模型权重提交到 GitHub。

代码测试可使用仓库根目录的无硬件测试入口：

    bash tools/run_unit_tests.sh

## 下一轮

拿到第一批标注后，再增加：

1. YOLOv5/YOLOv8 训练配置；
2. Jetson 推理 benchmark；
3. 车辆停留/禁停区域规则节点；
4. Dashboard 检测结果卡片和事件列表；
5. 算法、平台、数据集三部分申报材料。
