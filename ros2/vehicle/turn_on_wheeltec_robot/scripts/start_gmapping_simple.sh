#!/bin/bash

echo "=== 启动GMapping SLAM系统 ==="

# 旧脚本不再执行全局进程清理，避免误杀同机其他 ROS 实例。
# 如已有旧进程，请使用其对应 PID 文件或专用 stop 入口处理。

# 启动基础系统
echo "1. 启动机器人基础系统..."
cd ~/Desktop/wheeltec_ros2
source install/setup.bash
ros2 launch turn_on_wheeltec_robot turn_on_wheeltec_robot.launch.py &
sleep 5

# 启动激光雷达
echo "2. 启动Hesai激光雷达..."
cd ~/ros2_ws
source install/setup.bash
ros2 run hesai_ros_driver hesai_ros_driver_node &
sleep 3

# 启动点云转换
echo "3. 启动点云转激光扫描..."
cd ~/Desktop/wheeltec_ros2
source install/setup.bash
ros2 run pointcloud_to_laserscan pointcloud_to_laserscan_node --ros-args --remap cloud_in:=/lidar_points --remap scan:=/scan -p target_frame:=laser_link -p min_height:=-0.15 -p max_height:=0.20 &
sleep 3

# 启动GMapping
echo "4. 启动GMapping..."
ros2 run slam_gmapping slam_gmapping --ros-args -p base_frame:=base_link -p odom_frame:=odom -p map_frame:=map -p maxUrange:=25.0 -p maxRange:=60.0 -p delta:=0.05 -p particles:=30 &
sleep 5

echo "=== 系统启动完成 ==="
echo "请等待几秒钟让系统稳定，然后检查RViz2中的显示"
echo "如果RViz2中仍然显示TF错误，请等待机器人移动一下"
