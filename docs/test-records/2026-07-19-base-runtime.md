# 2026-07-19 底盘基础链路短时验收

## 范围

- 使用 `/home/nvidia/Desktop/wheeltec_ros2/start_base.sh` 启动官方底盘基础入口；
- 只验证底盘串口驱动、里程计、IMU、TF 和 EKF；
- 未启动雷达、建图、导航；未发布 `cmd_vel`/`cmd_vel_nav`；
- 验收结束后已正常发送 SIGINT 停止启动树。

## 结果

- `wheeltec_robot` 节点启动成功；
- 串口 `/dev/ttyCH343USB0` 打开成功；
- `/odom` 存在，采样约 19.9 Hz，静止样本线速度和角速度均为 0；
- `/imu/data_raw` 存在并持续发布，`/imu/data` 由 Madgwick 节点产生；
- `/tf` 有 3 个发布者，底盘、雷达和相机静态坐标变换正常；
- `/cmd_vel_nav` 只有底盘订阅者，发布者数量为 0；
- `/c16/lslidar_point_cloud`、`/scan`、`/map` 未出现，符合本次只启动底盘基础入口的预期。

## 修复项

启动文件原来传入 `serial_port`，但驱动实际声明和读取的是 `usart_port_name`，导致参数不会覆盖默认串口。已在仓库源码和当前部署源码中统一修正为 `/dev/ttyCH343USB0`，并完成 `turn_on_wheeltec_robot` 构建。

## 备注

启动日志有上游 ROS 包的旧式 TF 参数和 URDF 惯量警告，不影响本次话题验收；后续建图前仍需单独启动并验证雷达链路。
