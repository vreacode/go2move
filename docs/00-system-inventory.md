# 系统盘点

盘点时间：2026-07-17。以下结论来自 /home/nvidia 上的实际目录和运行文档。

## 核心组件

| 组件 | 板上目录 | 作用 | 本仓库位置 |
|---|---|---|---|
| Nano Dashboard | /home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy | Agent、Server、React/Vite 前端、WebRTC、模型管理、地图/导航工作流 | apps/nano-deploy |
| FAST-LIO2 / 雷达 | /home/nvidia/fastlio2_ws | 激光雷达驱动、FAST-LIO2、点云话题 | ros2/fastlio2 |
| Yesense IMU | /home/nvidia/imu-driver/ROS2 SDK/yesense_ros2 | 串口 IMU 驱动，原始话题 /imu/data_raw | ros2/imu |
| 车辆 ROS2 包 | /home/nvidia/Desktop/wheeltec_ros2/src | 底盘、消息、Nav2、点云转 LaserScan 等厂商/车辆适配 | ros2/vehicle（选取必要源码） |
| 录制服务 | /home/nvidia/video_rec | RTSP/网页视频及激光雷达数据录制 | apps/video-recorder |
| 建图/导航入口 | /home/nvidia/start_2d_mapping.sh、/home/nvidia/start_2d_navigation.sh | 旧版整车启动脚本 | deployment/board-scripts |

## 目前可确认的链路

    相机 RTSP ──> nano_rtsp_detect.py ──> 模型事件/快照 ──> Dashboard
    激光雷达 ──> /c16/lslidar_point_cloud ──> 点云转 /scan ──> SLAM/Nav2
    Yesense IMU ──> /imu/data_raw ──> republisher ──> /imu/data
    底盘 ROS2 ──> /cmd_vel_nav ──> 阿克曼线控底盘
    Agent <──> Server <──> Web 前端

## 运行时数据不入库

以下内容只属于板上运行状态，不属于首版源代码：.env、.venv、build/install/log、事件图片、桌面录屏、RTSP 录像、rosbag/db3、地图运行数据、模型权重和旧比赛压缩包。

## 重要边界

当前 Dashboard 的控制白名单和运动桥默认是锁定/dry-run，真实底盘控制命令不能根据猜测填写。车辆是阿克曼底盘，控制语义必须保持“前进、后退、前进左转、前进右转、停止、急停”，不引入横移或全向控制。
