# 设备调试摘要（已脱敏）

以下命令是从板上已有材料整理出的操作骨架。具体 IP、用户名、密码、代理和模型路径应放在本机 .env 或私有部署配置中，不要写入公开文档。

## Dashboard

    cd /home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy
    source .venv/bin/activate
    PYTHONNOUSERSITE=1 uvicorn dashboard_platform.agent.app:app --host 0.0.0.0 --port 9001
    PYTHONNOUSERSITE=1 uvicorn dashboard_platform.server.app:app --host 0.0.0.0 --port 8080

推荐使用统一启动/停止脚本：

    bash scripts/start_demo_stack.sh
    bash scripts/stop_demo_stack.sh

## 视觉检测

    cd /home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy
    source .venv/bin/activate
    python nano_rtsp_detect.py --weights helmet.pt --source "$CAMERA_RTSP_URL" --vid-stride 5 --view

模型权重不入库，运行前确认本机已有对应文件。

## 雷达和 IMU

    source /home/nvidia/fastlio2_ws/install/setup.bash
    ros2 launch lslidar_driver lslidar_c16_launch.py

    source "/home/nvidia/imu-driver/ROS2 SDK/yesense_ros2/install/setup.bash"
    ros2 launch yesense_std_ros2 yesense_node.launch.py

    ros2 topic hz /c16/lslidar_point_cloud
    ros2 topic echo /imu/data_raw --no-arr

## 建图和导航

    bash /home/nvidia/start_2d_mapping.sh
    bash /home/nvidia/start_2d_navigation.sh gmap_map
    rviz2 -d /opt/ros/humble/share/nav2_bringup/rviz/nav2_default_view.rviz

实机控制前必须先确认车辆架空/封闭场地、急停可用、速度限制生效，并按 docs/01-current-status.md 的顺序逐层启动。
