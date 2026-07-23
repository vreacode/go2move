#!/bin/bash

# ROS2 GMapping SLAM 修正启动脚本
# 解决包依赖和显示问题

echo "=========================================="
echo "ROS2 GMapping SLAM 修正启动"
echo "=========================================="

# 设置工作空间环境
cd ~/Desktop/wheeltec_ros2
source install/setup.bash

# 设置显示环境变量（解决RViz显示问题）
export DISPLAY=:0
export QT_QPA_PLATFORM=xcb

echo "步骤1: 启动基础机器人系统..."
ros2 launch turn_on_wheeltec_robot turn_on_wheeltec_robot.launch.py &
sleep 8

echo "步骤2: 启动Hesai激光雷达驱动..."
cd ~/ros2_ws
source install/setup.bash
ros2 run hesai_ros_driver hesai_ros_driver_node &
sleep 3

echo "步骤3: 启动点云转激光节点..."
cd ~/Desktop/wheeltec_ros2
source install/setup.bash
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

echo "步骤4: 启动GMapping SLAM..."
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

echo "步骤5: 启动RViz2..."
ros2 run rviz2 rviz2 -d src/turn_on_wheeltec_robot/rviz/gmapping.rviz &

echo "=========================================="
echo "所有组件已启动！"
echo "=========================================="

echo "等待系统稳定..."
sleep 5

echo "检查系统状态..."
echo "话题列表:"
ros2 topic list | grep -E "(scan|map|points)" || echo "话题检查失败"

echo "节点列表:"
ros2 node list | grep -E "(gmapping|pcl2scan|hesai)" || echo "节点检查失败"

echo "TF变换检查:"
timeout 3 ros2 run tf2_ros tf2_echo base_link laser_link | head -5 || echo "TF检查失败"

echo "=========================================="
echo "启动完成！"
echo "=========================================="

# 保持脚本运行
echo "按 Ctrl+C 停止所有进程"
wait
