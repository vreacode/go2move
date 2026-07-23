# ROS 健康检查说明

tools/ros_healthcheck.sh 是只读诊断脚本，检查节点、topic、雷达/IMU 频率、可选 RTSP 读取和系统负载。它不会启动、停止或控制车辆。

默认使用板上的这些 setup 文件：

- /opt/ros/humble/setup.bash
- /home/nvidia/Desktop/wheeltec_ros2/install/setup.bash
- /home/nvidia/fastlio2_ws/install/setup.bash
- /home/nvidia/imu-driver/ROS2 SDK/yesense_ros2/install/setup.bash

如果设备路径不同，可以临时覆盖环境变量：

    ROS_SETUP_BASH=/path/to/ros/setup.bash +    VEHICLE_SETUP_BASH=/path/to/vehicle/install/setup.bash +    LIDAR_SETUP_BASH=/path/to/lidar/install/setup.bash +    IMU_SETUP_BASH=/path/to/imu/install/setup.bash +    bash tools/ros_healthcheck.sh

严格验收时使用：

    EXPECTED_NODE=/wheeltec_robot +    CAMERA_RTSP_URL=rtsp://设备相机地址 +    bash tools/ros_healthcheck.sh --strict

当前 /map、/scan 只在相应建图/导航链路运行时要求有效，脚本默认会报告缺失，不会自行拉起服务。
