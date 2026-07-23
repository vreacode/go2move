#!/bin/bash

# 设置 serial 库的环境变量
source /home/nvidia/serial-ros2/install/setup.bash

# 设置工作空间的环境变量
source "/home/nvidia/imu-driver/ROS2 SDK/yesense_ros2/install/setup.bash"

# 运行 ROS2 节点
echo "环境已设置完成，现在可以运行 ROS2 节点了。"
echo "例如: ros2 run yesense_std_ros2 yesense_node_publisher"
echo ""

# 如果提供了参数，直接运行命令
if [ $# -gt 0 ]; then
    "$@"
else
    # 否则启动一个配置好环境的 bash shell
    bash
fi

