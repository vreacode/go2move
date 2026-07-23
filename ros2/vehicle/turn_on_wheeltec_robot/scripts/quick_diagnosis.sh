#!/bin/bash

# ROS2 SLAM 系统快速诊断脚本
# 用于快速检查gmapping建图系统的关键组件

echo "=========================================="
echo "ROS2 SLAM 系统快速诊断"
echo "=========================================="

# 检查ROS2环境
echo "1. 检查ROS2环境..."
if [ -z "$ROS_DISTRO" ]; then
    echo "❌ ROS2环境未设置"
    exit 1
else
    echo "✅ ROS2环境: $ROS_DISTRO"
fi

# 检查关键话题
echo -e "\n2. 检查关键话题..."
topics=("/scan" "/map" "/hesai_ros_driver/points" "/map_metadata" "/tf" "/tf_static")

for topic in "${topics[@]}"; do
    if ros2 topic list | grep -q "^$topic$"; then
        echo "✅ $topic: 存在"
        # 检查话题频率
        echo "   频率检查:"
        timeout 3 ros2 topic hz $topic 2>/dev/null | head -1 || echo "   无法获取频率信息"
    else
        echo "❌ $topic: 不存在"
    fi
done

# 检查TF变换
echo -e "\n3. 检查TF变换链..."
tf_chain=("map->odom" "odom->base_link" "base_link->laser_link")

for tf in "${tf_chain[@]}"; do
    parent=$(echo $tf | cut -d'>' -f1)
    child=$(echo $tf | cut -d'>' -f2)
    
    if timeout 2 ros2 run tf2_ros tf2_echo $parent $child >/dev/null 2>&1; then
        echo "✅ $tf: 变换正常"
    else
        echo "❌ $tf: 变换失败"
    fi
done

# 检查节点状态
echo -e "\n4. 检查关键节点..."
nodes=("slam_gmapping" "pointcloud_to_laserscan_node" "hesai_ros_driver_node" "robot_state_publisher")

for node in "${nodes[@]}"; do
    if ros2 node list | grep -q "$node"; then
        echo "✅ $node: 运行中"
    else
        echo "❌ $node: 未运行"
    fi
done

# 检查点云转激光参数
echo -e "\n5. 检查点云转激光参数..."
if ros2 param list | grep -q "pcl2scan"; then
    echo "✅ pointcloud_to_laserscan 节点参数:"
    ros2 param list | grep pcl2scan | head -5
else
    echo "❌ pointcloud_to_laserscan 节点未找到"
fi

# 检查地图状态
echo -e "\n6. 检查地图状态..."
if ros2 topic echo /map_metadata --once >/dev/null 2>&1; then
    echo "✅ 地图元数据: 可获取"
    echo "   地图信息:"
    ros2 topic echo /map_metadata --once | grep -E "(resolution|width|height)" 2>/dev/null || echo "   无法获取详细信息"
else
    echo "❌ 地图元数据: 不可获取"
fi

# 检查激光扫描数据
echo -e "\n7. 检查激光扫描数据..."
if ros2 topic echo /scan --once >/dev/null 2>&1; then
    echo "✅ 激光扫描数据: 可获取"
    echo "   扫描信息:"
    ros2 topic echo /scan --once | grep -E "(range_min|range_max|angle_min|angle_max)" 2>/dev/null || echo "   无法获取详细信息"
else
    echo "❌ 激光扫描数据: 不可获取"
fi

# 生成TF树
echo -e "\n8. 生成TF树..."
echo "正在生成TF树图..."
if ros2 run tf2_tools view_frames >/dev/null 2>&1; then
    echo "✅ TF树已生成: frames.pdf"
    echo "   可以使用以下命令查看: evince frames.pdf"
else
    echo "❌ 无法生成TF树"
fi

echo -e "\n=========================================="
echo "诊断完成！"
echo "=========================================="

# 提供建议
echo -e "\n💡 故障排除建议:"
echo "1. 如果激光扫描数据不可获取，检查Hesai驱动和点云转激光节点"
echo "2. 如果TF变换失败，检查robot_state_publisher和URDF配置"
echo "3. 如果地图不更新，检查gmapping节点参数和激光数据质量"
echo "4. 使用以下命令进行详细调试:"
echo "   ros2 run turn_on_wheeltec_robot slam_debug.py"
echo "   ros2 topic hz /scan"
echo "   ros2 run tf2_ros tf2_echo map base_link"
