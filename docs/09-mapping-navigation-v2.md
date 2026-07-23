# 建图与导航链路 v2

本轮没有整体替换 ROS 2 栈：当前板卡是 Jetson Orin NX，已经安装 ROS 2 Humble、slam_toolbox 和 Nav2 相关插件；现有短距离 Nav2 测试也已经成功。因此采用“修正接口 + 增加安全层”的方案。

## 当前链路

```text
C16 PointCloud2
    -> pointcloud_to_laserscan (target_frame=laser)
    -> /scan
    -> slam_toolbox async_slam_toolbox_node    # 建图

map + /scan + odom_combined
    -> Nav2 AMCL + Smac Hybrid-A* (DUBIN) + RPP
    -> velocity_smoother
    -> /cmd_vel
    -> Collision Monitor
    -> /cmd_vel_safe
    -> Wheeltec chassis
```

手动控制也使用 `/cmd_vel_safe`，这样不会因为模式切换而绕过底盘最后一层速度入口。导航运行时，Agent 继续锁定手动控制。

## 这轮修正

- 建图从同步 SLAM 改为在线异步 SLAM，降低点云转 LaserScan 与扫描匹配互相阻塞的概率。
- 建图和导航统一使用 `base_footprint -> laser`；旧建图脚本使用了 `laser_link`，与当前 top_akm_dl 的 TF 描述不一致。
- 使用 slam_toolbox 正确的 `min_laser_range` 参数，避免旧配置中的 `minimum_laser_range` 被忽略。
- top_akm_dl 是前进约束的 Ackermann 配置，规划器从 Reeds-Shepp 改为 Dubins，避免规划出底盘不能执行的倒车段。
- RPP 的速度和前视距离调为更适合低速巡查的保守值。
- 增加 Collision Monitor：激光数据超时或进入前方停止区时，最后安全输出为零。

## 验收顺序

1. 先启动底盘基础链路和雷达，确认 `/odom`、`/c16/lslidar_point_cloud`、`/tf_static` 存在。
2. 启动建图，原地观察 `/scan` 和 RViz，再在开阔区域低速走一圈后保存新地图。
3. 停止建图后启动导航，确认 `collision_monitor`、`velocity_smoother`、`amcl`、`controller_server` 和两个 costmap 均 active。
4. 不载人、不靠近障碍物，先发约 0.25 m 的单目标；确认 `/cmd_vel_safe` 有输出而底盘不再订阅原始 `/cmd_vel_nav`。
5. 单目标稳定后再测试多个航点和动态障碍物。

## 网页目标点工作区

导航页现在支持在地图图像上完成坐标预览：

1. 选择“设置起点”，点击地图并填写起点朝向，启动导航后点击“发布起点”。
2. 选择“设置目标”，点击地图并填写目标朝向，确认区域安全后点击“发送目标”。
3. 目标执行中可点击“取消当前目标”；地图点击本身不会驱动车辆。

网页坐标按地图 YAML 的 `origin`、`resolution` 和 PGM 尺寸换算为 `map` 坐标。后端分别发布 `/initialpose` 和调用 Nav2 `/navigate_to_pose`，因此没有启动导航时会拒绝发送目标。

当前已完成接口和前端验证，但尚未进行实车移动验收；首次使用仍应按上面的 0.25 m 单目标流程，在空旷区域进行。

MPPI 已安装但暂不作为默认控制器。完成上述基线后，可以仅替换 FollowPath 配置做 A/B 测试；如果 CPU 占用、局部规划频率和路径跟踪误差都更好，再切换默认。
