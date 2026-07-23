#!/bin/bash

# ROS2 GMapping SLAM 分步启动脚本
# 用于逐步启动各个组件，便于调试

echo "=========================================="
echo "ROS2 GMapping SLAM 分步启动"
echo "=========================================="

# 设置工作空间环境
cd ~/Desktop/wheeltec_ros2
source install/setup.bash

echo "步骤1: 启动基础机器人系统..."
ros2 launch turn_on_wheeltec_robot turn_on_wheeltec_robot.launch.py &
sleep 5

echo "步骤2: 启动点云转激光节点..."
ros2 run pointcloud_to_laserscan pointcloud_to_laserscan_node \
    --ros-args \
    --remap cloud_in:=/lidar_points \
    --remap scan:=/scan \
    -p target_frame:=laser_link \
    -p min_height:=-0.15 \
    -p max_height:=0.20 \
    -p angle_min:=-3.14159 \
    -p angle_max:=3.14159 \
    -p range_min:=0.1 \
    -p range_max:=60.0 &
sleep 3

echo "步骤3: 启动GMapping SLAM..."
ros2 run slam_gmapping slam_gmapping \
    --ros-args \
    -p base_frame:=base_link \
    -p odom_frame:=odom \
    -p map_frame:=map \
    -p maxUrange:=25.0 \
    -p maxRange:=60.0 \
    -p delta:=0.05 \
    -p particles:=30 &
sleep 3

echo "步骤4: 启动RViz2..."
ros2 run rviz2 rviz2 -d src/turn_on_wheeltec_robot/rviz/gmapping.rviz &

echo "=========================================="
echo "所有组件已启动！"
echo "=========================================="

echo "检查系统状态..."
sleep 2

echo "话题列表:"
ros2 topic list | grep -E "(scan|map|points)"

echo "节点列表:"
ros2 node list | grep -E "(gmapping|pcl2scan|rviz)"

echo "TF变换检查:"
ros2 run tf2_ros tf2_echo base_link laser_link | head -5

echo "=========================================="
echo "启动完成！"
echo "=========================================="

# 保持脚本运行
echo "按 Ctrl+C 停止所有进程"
wait
