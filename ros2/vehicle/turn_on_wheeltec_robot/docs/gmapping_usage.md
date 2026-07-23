# ROS2 GMapping SLAM 建图系统使用指南

## 概述

本系统集成了Hesai XT-16激光雷达、点云转激光扫描、GMapping SLAM算法和RViz2可视化，实现完整的建图功能。

## 系统架构

```
TF链: map ←(gmapping) odom ←(EKF) base_link ←(RSP) laser_link
话题: /hesai_ros_driver/points → /scan → /map
```

## 快速启动

### 1. 启动完整建图系统

```bash
# 进入工作空间
cd ~/Desktop/wheeltec_ros2

# 编译工作空间
colcon build --packages-select turn_on_wheeltec_robot

# 启动建图系统
ros2 launch turn_on_wheeltec_robot gmapping_bringup.launch.py
```

### 2. 启动参数说明

```bash
# 启动时不显示RViz2
ros2 launch turn_on_wheeltec_robot gmapping_bringup.launch.py with_rviz:=false

# 使用仿真时间
ros2 launch turn_on_wheeltec_robot gmapping_bringup.launch.py use_sim_time:=true
```

## 系统组件

### 1. 机器人基础组件
- **底盘驱动**: `base_serial.launch.py`
- **EKF融合**: `wheeltec_ekf.launch.py`
- **机器人模型**: `robot_mode_description.launch.py` (top_akm_dl配置)
- **IMU滤波**: `imu_filter_madgwick_node`

### 2. 传感器组件
- **Hesai激光雷达**: `hesai_ros_driver_node`
- **点云转激光**: `pointcloud_to_laserscan_node`

### 3. SLAM组件
- **GMapping**: `slam_gmapping`

### 4. 可视化组件
- **RViz2**: 使用专用配置文件 `gmapping.rviz`

## 关键参数配置

### 点云转激光参数

```yaml
target_frame: 'laser_link'        # 输出坐标系
min_height: -0.15                 # 高度窗口下限
max_height: 0.20                  # 高度窗口上限
angle_min: -3.14159               # 扫描角度范围
angle_max: 3.14159
range_min: 0.1                    # 最小距离
range_max: 60.0                   # 最大距离
```

### GMapping参数

```yaml
base_frame: 'base_link'           # 机器人基座坐标系
odom_frame: 'odom'               # 里程计坐标系
map_frame: 'map'                 # 地图坐标系
maxUrange: 25.0                  # 最大有效距离
maxRange: 60.0                   # 激光雷达最大距离
delta: 0.05                      # 地图分辨率 (m/pixel)
particles: 30                    # 粒子数量
```

## 调试工具

### 1. 快速诊断脚本

```bash
# 运行快速诊断
./src/turn_on_wheeltec_robot/scripts/quick_diagnosis.sh
```

### 2. 实时调试器

```bash
# 启动实时调试器
ros2 run turn_on_wheeltec_robot slam_debug.py
```

### 3. 手动检查命令

```bash
# 检查话题频率
ros2 topic hz /scan
ros2 topic hz /map

# 检查TF变换
ros2 run tf2_ros tf2_echo map base_link
ros2 run tf2_ros tf2_echo base_link laser_link

# 生成TF树
ros2 run tf2_tools view_frames

# 查看节点状态
ros2 node list
ros2 topic list
```

## 故障排除

### 常见问题及解决方案

#### 1. 激光扫描数据不可获取

**症状**: `/scan` 话题无数据或频率异常

**检查步骤**:
```bash
# 检查点云数据
ros2 topic hz /hesai_ros_driver/points

# 检查点云转激光节点
ros2 node list | grep pcl2scan

# 检查参数配置
ros2 param list | grep pcl2scan
```

**解决方案**:
- 调整 `min_height` 和 `max_height` 参数
- 检查Hesai驱动是否正常启动
- 验证TF变换 `base_link -> laser_link`

#### 2. 地图不更新

**症状**: `/map` 话题无数据或地图不变化

**检查步骤**:
```bash
# 检查gmapping节点
ros2 node list | grep slam_gmapping

# 检查地图元数据
ros2 topic echo /map_metadata --once

# 检查TF变换链
ros2 run tf2_ros tf2_echo map odom
```

**解决方案**:
- 确保机器人正在移动
- 检查激光扫描数据质量
- 调整gmapping参数（particles、delta等）

#### 3. TF变换失败

**症状**: 机器人模型在RViz中不显示或位置错误

**检查步骤**:
```bash
# 检查所有TF变换
ros2 run tf2_tools view_frames

# 检查robot_state_publisher
ros2 node list | grep robot_state_publisher

# 检查URDF配置
ros2 param get /robot_state_publisher robot_description
```

**解决方案**:
- 确保URDF文件正确加载
- 检查joint_state_publisher是否运行
- 验证TF发布频率

#### 4. RViz显示异常

**症状**: 地图、机器人模型或激光扫描显示不正确

**检查步骤**:
- 确认Fixed Frame设置为 `map`
- 检查Display配置是否正确
- 验证话题名称匹配

**解决方案**:
- 使用提供的 `gmapping.rviz` 配置文件
- 重新加载RViz配置
- 检查话题数据类型

## 性能优化

### 1. 参数调优

**室内小环境**:
```yaml
delta: 0.03                      # 提高地图分辨率
particles: 50                    # 增加粒子数量
maxUrange: 15.0                  # 减小有效距离
```

**室外大环境**:
```yaml
delta: 0.05                      # 标准分辨率
particles: 30                    # 标准粒子数量
maxUrange: 25.0                  # 标准有效距离
```

### 2. 系统资源优化

- 调整激光扫描频率
- 优化点云转激光的高度窗口
- 根据环境调整地图边界参数

## 保存地图

### 1. 保存地图

```bash
# 保存地图
ros2 run nav2_map_server map_saver_cli -f my_map

# 或使用map_server
ros2 run map_server map_saver -f my_map
```

### 2. 加载地图

```bash
# 加载地图
ros2 run map_server map_server --ros-args -p yaml_filename:=my_map.yaml
```

## 注意事项

1. **首次启动**: 确保所有硬件连接正常，特别是Hesai激光雷达
2. **环境要求**: 建图环境需要有足够的特征点，避免纯空旷环境
3. **移动策略**: 建图时建议缓慢移动，避免快速转向
4. **参数调整**: 根据实际环境调整点云转激光的高度窗口参数
5. **资源监控**: 注意系统CPU和内存使用情况

## 技术支持

如遇到问题，请按以下顺序进行排查：

1. 运行快速诊断脚本
2. 检查硬件连接
3. 验证软件配置
4. 查看系统日志
5. 联系技术支持

---

**版本**: 1.0  
**更新日期**: 2024年  
**适用系统**: ROS2 Humble + Ubuntu 22.04
