#!/bin/bash

# 设置 serial 库的环境变量
source /home/nvidia/serial-ros2/install/setup.bash

# 切换到工作空间目录
cd "/home/nvidia/imu-driver/ROS2 SDK/yesense_ros2"

# 编译 yesense_std_ros2 包
colcon build --packages-select yesense_std_ros2

echo ""
echo "编译完成！记得在运行程序前执行以下命令："
echo "source /home/nvidia/serial-ros2/install/setup.bash"
echo "source /home/nvidia/imu-driver/ROS2\ SDK/yesense_ros2/install/setup.bash"

