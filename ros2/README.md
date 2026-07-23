# ROS2 组件

仓库只纳入当前无人车链路需要的源码快照：

- fastlio2/FAST_LIO：FAST-LIO2 算法源码；
- fastlio2/lslidar_ros：激光雷达 ROS2 驱动；
- imu/yesense_ros2：Yesense 串口 IMU 驱动；
- vehicle/turn_on_wheeltec_robot：车辆底盘和基础传感器包；
- vehicle/wheeltec_robot_nav2：车辆 Nav2 配置和地图示例；
- vehicle/wheeltec_robot_msg：车辆消息定义；
- vehicle/pointcloud_to_laserscan-humble：点云到 LaserScan 适配。

ROS2 的 build、install、log、地图、串口设备规则和厂商未使用的大型包仍保留在板上，不直接提交。部分目录来自第三方或厂商分发包，公开仓库前需要再次确认许可证和再分发范围。
