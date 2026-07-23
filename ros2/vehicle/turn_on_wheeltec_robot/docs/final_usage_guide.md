# ROS2 GMapping SLAM 最终使用指南

## 问题诊断结果

经过测试，我们发现以下关键信息：

### ✅ 正常工作的组件
1. **基础机器人系统**: `turn_on_wheeltec_robot.launch.py` 正常启动
2. **Hesai激光雷达**: 发布 `/lidar_points` 话题，频率正常
3. **TF变换**: `base_link -> laser_link` 变换正常，位置 `[-0.175, 0.000, 0.345]`
4. **点云转激光**: `pointcloud_to_laserscan` 节点可以正常工作，生成 `/scan` 话题

### ❌ 遇到的问题
1. **Launch文件集成**: 完整的launch文件启动时出现节点冲突
2. **系统稳定性**: 多个组件同时启动时系统不稳定

## 推荐的分步启动方案

### 方案1: 手动分步启动（推荐）

```bash
# 1. 启动基础机器人系统
cd ~/Desktop/wheeltec_ros2
source install/setup.bash
ros2 launch turn_on_wheeltec_robot turn_on_wheeltec_robot.launch.py

# 2. 在新终端启动点云转激光
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
    -p range_max:=60.0

# 3. 在新终端启动GMapping
ros2 run slam_gmapping slam_gmapping \
    --ros-args \
    -p base_frame:=base_link \
    -p odom_frame:=odom \
    -p map_frame:=map \
    -p maxUrange:=25.0 \
    -p maxRange:=60.0 \
    -p delta:=0.05 \
    -p particles:=30

# 4. 在新终端启动RViz2
ros2 run rviz2 rviz2 -d src/turn_on_wheeltec_robot/rviz/gmapping.rviz
```

### 方案2: 使用分步启动脚本

```bash
cd ~/Desktop/wheeltec_ros2
./src/turn_on_wheeltec_robot/scripts/start_gmapping.sh
```

## 系统检查命令

### 检查话题状态
```bash
# 检查关键话题
ros2 topic list | grep -E "(scan|map|points)"

# 检查话题频率
ros2 topic hz /scan
ros2 topic hz /lidar_points
```

### 检查TF变换
```bash
# 检查TF变换链
ros2 run tf2_ros tf2_echo base_link laser_link
ros2 run tf2_ros tf2_echo odom base_link

# 生成TF树
ros2 run tf2_tools view_frames
```

### 检查节点状态
```bash
# 检查关键节点
ros2 node list | grep -E "(gmapping|pcl2scan|robot_state_publisher)"
```

## 参数调优建议

### 点云转激光参数
根据实际环境调整以下参数：
- `min_height`: -0.15 (高度窗口下限)
- `max_height`: 0.20 (高度窗口上限)
- `range_min`: 0.1 (最小距离)
- `range_max`: 60.0 (最大距离)

### GMapping参数
根据环境大小调整：
- `delta`: 0.05 (地图分辨率，室内可调至0.03)
- `particles`: 30 (粒子数量，复杂环境可增至50)
- `maxUrange`: 25.0 (最大有效距离)

## 故障排除

### 1. 激光扫描数据不可获取
```bash
# 检查点云数据
ros2 topic hz /lidar_points

# 检查点云转激光节点
ros2 node list | grep pcl2scan
```

### 2. 地图不更新
```bash
# 检查gmapping节点
ros2 node list | grep slam_gmapping

# 检查地图话题
ros2 topic echo /map_metadata --once
```

### 3. TF变换失败
```bash
# 检查robot_state_publisher
ros2 node list | grep robot_state_publisher

# 检查TF树
ros2 run tf2_tools view_frames
```

## 保存地图

```bash
# 保存地图
ros2 run nav2_map_server map_saver_cli -f my_map

# 或使用map_server
ros2 run map_server map_saver -f my_map
```

## 注意事项

1. **启动顺序**: 必须先启动基础机器人系统，再启动其他组件
2. **环境要求**: 确保建图环境有足够的特征点
3. **移动策略**: 建图时建议缓慢移动，避免快速转向
4. **资源监控**: 注意系统CPU和内存使用情况
5. **参数调整**: 根据实际环境调整点云转激光的高度窗口参数

## 成功标志

系统正常工作时应该看到：
- ✅ `/scan` 话题存在且频率正常（约3Hz）
- ✅ `/map` 话题存在且地图在更新
- ✅ TF变换链完整：`map -> odom -> base_link -> laser_link`
- ✅ RViz2中显示地图、机器人模型和激光扫描
- ✅ 机器人移动时地图实时更新

---

**版本**: 1.0  
**更新日期**: 2024年  
**适用系统**: ROS2 Humble + Ubuntu 22.04
