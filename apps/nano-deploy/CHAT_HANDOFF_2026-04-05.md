# 对话交接总结（截至 2026-04-05）

本文档用于把本次长对话里的关键背景、已完成改动、验证结果、当前系统状态、未解决问题和下一步建议一次性交给新对话。

建议新对话先阅读本文件，再继续工作。

---

## 1. 项目当前目标和路线

当前项目已经收口为一个**稳定演示版 Dashboard**，主路线明确为：

- 前端负责：
  - 实时视频展示
  - 建图 / 保存地图 / 启动导航 / 打开 RViz 的工作流控制
  - 已保存地图的 2D 在线查看
  - 系统状态展示
  - 底盘控制入口
  - 媒体能力（拍照 / 录像 / 图库）
- RViz 负责：
  - 初始位姿设置
  - 专业导航调试
  - TF / path / costmap / 深度调试

已经明确**不再**把前端往“完整替代 RViz”的方向做，也不再把点云调试、复杂 3D 可视化作为主展示重点。

---

## 2. 当前页面结构

### 首页第一层级（当前固定结构）

- 左：实时视频
- 中：实时操控
- 右：建图与导航

补充：
- 视频模块下方保留一个紧凑的“模型联动条”
- 第一层级结构近期多次收口后已经稳定，后续不要随意重排

### 首屏以下结构（当前固定结构）

- 第二层：
  - 左：地图查看
  - 右：媒体
- 第三层：
  - 系统与底盘状态（独占整行）
- 更下方：
  - 日志输出

### 前端视觉风格

前端已经从“偏深色工业冷”切到：

- 柔和现代
- 浅蓝调
- 友好且高效
- 深色/工业/赛博/霓虹风已被明显削弱

风格关键词：

- Modern
- Minimalist
- Clean
- Intuitive
- User-friendly

文案也已做过一轮产品化收口：

- 去掉了原型说明腔
- 去掉了比赛提示腔
- 去掉了“为什么这个模块放在这里”的解释型句子

---

## 3. 三层架构现状

### Agent

路径：

- [dashboard_platform/agent/app.py](./dashboard_platform/agent/app.py)
- [dashboard_platform/agent/runtime.py](./dashboard_platform/agent/runtime.py)
- [dashboard_platform/agent/config.py](./dashboard_platform/agent/config.py)
- [dashboard_platform/agent/motion.py](./dashboard_platform/agent/motion.py)
- [dashboard_platform/agent/schemas.py](./dashboard_platform/agent/schemas.py)

主要职责：

- 模型进程管理
- WebRTC 视频输出
- 建图/导航命令白名单执行
- 地图文件读取
- 媒体（拍照、录像、图库）
- 底盘 bringup 状态检查
- 运动控制安全桥
- 现在还新增了桌面录屏功能

### Server

路径：

- [dashboard_platform/server/app.py](./dashboard_platform/server/app.py)
- [dashboard_platform/server/hub.py](./dashboard_platform/server/hub.py)

主要职责：

- 统一鉴权
- API 转发
- 状态汇总
- WebSocket 广播

### Web 前端

主要文件：

- [dashboard_platform/web/src/App.tsx](./dashboard_platform/web/src/App.tsx)
- [dashboard_platform/web/src/components/Panel.tsx](./dashboard_platform/web/src/components/Panel.tsx)
- [dashboard_platform/web/src/styles.css](./dashboard_platform/web/src/styles.css)
- [dashboard_platform/web/tailwind.config.js](./dashboard_platform/web/tailwind.config.js)

---

## 4. 建图 / 导航 / 地图工作流当前状态

### 已知可用命令背景

历史上已有这些可用链路：

- 建图：
  - `bash /home/nvidia/start_2d_mapping.sh`
- 保存地图：
  - `ros2 run nav2_map_server map_saver_cli ...`
- 导航：
  - `bash /home/nvidia/start_2d_navigation.sh gmap_map`
- RViz：
  - `rviz2 -d /opt/ros/humble/share/nav2_bringup/rviz/nav2_default_view.rviz`

### 前端当前已接入

前端已经接入了这些能力：

- 启动建图
- 保存地图
- 启动导航
- 打开 RViz
- 停止建图
- 停止导航
- 地图目录读取
- 地图元数据查看
- 地图图像在线查看

### 关键修复

#### 保存地图 `save_map`

曾经失败，现已修好。

根因：

- 保存时 `/map` 没有发布者，或保存脚本判断过于脆弱

修复点：

- `scripts/save_map.sh`
  - 去掉 `set -u` 对 ROS setup 的干扰
  - 等待 `/map` 真正 ready
  - 通过 `Publisher count > 0` 判断

验证结果：

- 可以真实保存出 `.yaml + .pgm`

#### 导航 / 建图预设

已经从“旧的重复 bringup 脚本”切到集成脚本：

- [scripts/start_navigation_integrated.sh](./scripts/start_navigation_integrated.sh)
- [scripts/start_mapping_integrated.sh](./scripts/start_mapping_integrated.sh)

目的：

- 不再重复拉起 base / IMU / lidar
- 不再自动打开 RViz
- 避免 CPU 被旧路径拖爆

### 当前状态结论

- 地图查看已通
- 保存地图已通
- 导航预设已改成集成方式
- 仍建议把 RViz 当作专业调试工具，不作为默认主链

---

## 5. 点云 / 位姿 / 前端展示路线调整

这部分走过多轮收口，最终路线如下：

### 已明确弱化或移除

- 默认主界面不再突出雷达调试视图
- 地图上的机器人位姿叠加功能曾接入过，但后来被从默认主界面拿掉
- 原因：
  - 数据不够稳定
  - 价值不如视频 + 地图 + 工作流主线

### 当前结论

默认演示版主线是：

- 视频
- 地图查看
- 建图 / 导航工作流
- 系统状态
- 模型控制
- 小车控制

而不是：

- 点云调试
- 复杂位姿叠加
- 前端复刻 RViz

---

## 6. 底盘控制当前状态（非常重要）

### 已确认的底盘事实

当前车是：

- **阿克曼底盘**

后续所有控制语义都必须按阿克曼理解：

- 允许：
  - 前进
  - 后退
  - 左转
  - 右转
  - 停止
  - 急停
- 不允许：
  - 左右平移
  - 全向控制
  - 横移 UI / 命令

### 底盘链路审计结论

关键结论：

- 控制入口是 `Twist`
- 当前运行链路下，正确 topic 是：
  - `/cmd_vel_nav`
- 不是直接控制串口
- 不应该绕过 ROS2 控制链

源码/launch 结论：

- `wheeltec_robot_node` 源码里订阅 `cmd_vel`
- 但 launch 会 remap 到 `/cmd_vel_nav`
- 官方完整 bringup 是：
  - `ros2 launch turn_on_wheeltec_robot turn_on_wheeltec_robot.launch.py`

### Agent 安全控制桥已实现

已落地功能：

- `dry_run / real`
- `locked / unlocked`
- `manual override`
- `emergency stop`
- watchdog 自动停车
- `real` 模式必须在底盘 ready 后才能切
- `navigation_running` 时默认 lockout

关键接口：

- `GET /control/state`
- `POST /control/arm`
- `POST /control/manual-override`
- `POST /control/emergency-stop`
- `POST /control/mode`
- `POST /control/send`

### 运行时底盘 bringup 管理已接入

用户已经不需要：

- 手动开底盘终端
- 手改 `.env` 切 `real/dry_run`

已接入运行时 API：

- `GET /base/status`
- `POST /base/start`
- `POST /base/stop`

### 阿克曼控制语义已收口

之前把 `left/right` 当作“纯 angular.z 原地左右转”，这是不适合阿克曼的。

后来已改为更合理的阿克曼语义：

- `forward`：`linear_x > 0, angular_z = 0`
- `backward`：`linear_x < 0, angular_z = 0`
- `left`：`linear_x > 0, angular_z > 0`
- `right`：`linear_x > 0, angular_z < 0`

也就是：

- 左右现在本质是“前进左转 / 前进右转”
- 不再是差速式原地左右转

### 当前底盘相关未完成项

虽然安全桥、bringup、real/dry_run 切换都接好了，但实际实机上仍有这些现实问题：

- `forward / backward` 已经能看到响应
- `left / right` 旧语义下几乎没响应
- 现已改成阿克曼语义，但后续仍建议继续架空验证“前进左转 / 前进右转”的机械响应

---

## 7. 首轮实机问题排查结论（CPU / 资源竞争）

这部分非常关键。

### 实机出现的问题

昨天实机测试暴露过：

- 底盘启动失败
- 无法切 `real`
- 解锁控制异常
- 导航启动失败
- RViz 打开后模型不完整
- 拍照灰屏
- CPU 逼近 `99%`

### 已归因结论

#### CPU 主瓶颈

最大负载源是：

- `nano_rtsp_detect.py`

实测：

- 约 `84%~99% CPU`
- RSS 可接近 `1.1 GB`

另外旧导航路径也会把 CPU 拖爆，因为它会重复 bringup：

- base
- IMU
- EKF
- lidar
- pointcloud_to_laserscan
- nav2
- 并自动开 RViz

旧最重组合实测接近：

- `99.4% CPU`

#### 内存不是主瓶颈

最重组合下：

- 内存约 `56.9%`

所以当前核心问题是：

- CPU 瓶颈
- 不是 OOM

### 已做的最小修复

#### 1. 状态链降载

在 [dashboard_platform/agent/runtime.py](./dashboard_platform/agent/runtime.py) 里：

- base 状态刷新加缓存
- `/status` 不再每次都做重型 ROS CLI 检查
- 监控轮询降频

配套配置已更新：

- `STATUS_CACHE_SECONDS=2.0`
- `WEBSOCKET_STATUS_INTERVAL=3.0`
- `BASE_BRINGUP_START_TIMEOUT_SECONDS=18`
- `BASE_STATUS_TIMEOUT_SECONDS=8`

#### 2. 导航预设改成集成模式

前端导航预设不再走旧的 `/home/nvidia/start_2d_navigation.sh`

而是：

- `scripts/start_navigation_integrated.sh`

结果：

- 不再重复 bringup base/lidar/imu
- 不再自动打开 RViz

#### 3. 建图预设同样去重 bringup

- `scripts/start_mapping_integrated.sh`

并绕开了老的 `slam_toolbox` posegraph 反序列化依赖。

### 当前演示降载建议（已形成结论）

默认建议：

- 开：
  - 视频主链
  - 地图查看
  - 底盘 bringup
  - 控制界面
- 默认关：
  - 模型检测
  - 录像
  - RViz
  - 桌面录屏

高风险组合：

- 模型检测 + 导航 + 录像 + 桌面录屏

---

## 8. 媒体能力当前状态

### 已做的媒体路线

媒体不是旧 `localhost:8000` 方案，而是 Agent 统一管理：

- 拍照
- 录像
- 图库

前端只通过 Server -> Agent 调用。

### 已知结论

#### 旧录像方案不适合并入主链

原因：

- 会启动 `localhost:8000` Web 服务
- 会做 HLS 转码
- 会重复拉流
- 会增加 CPU / I/O / RTSP 压力

#### 当前录像方案

当前录像已改成更轻的方式：

- `ffmpeg -c copy` 直录 RTSP 到 `.mp4`
- 不再启旧 web 服务

### 拍照灰屏问题已修复

修复文件：

- [dashboard_platform/agent/runtime.py](./dashboard_platform/agent/runtime.py)

修复点：

- `-skip_frame nokey`
- 重试
- PIL 有效性校验

验证通过：

- 现在能拍出有效图片，不再是约 1KB 的灰屏图

---

## 9. 新增的桌面录屏能力（这轮最后做的）

这是本对话最后新增的一个功能，目标是：

- 最低占用
- 最稳定
- 适合开发板比赛演示
- 录整个桌面
- RViz 能录进去

### 路线

明确路线是：

- Agent 侧系统级桌面录屏
- 优先 GStreamer
- 优先 Jetson / 硬件 H.264
- 录的是整个 X11 桌面，不是浏览器页面

### 环境检查结果

实测：

- `gst-launch-1.0`：有
- `ximagesrc`：有
- `X11`：是
- `nvv4l2h264enc`：**没有**
- `nvvidconv`：有
- `avenc_h264_omx`：有，但和当前桌面抓取管线协商失败

### 最终可用后端

最终落地是：

- `gstreamer_x264`

不是最理想的 Jetson 专用硬编，但当前板子上这是：

- 最稳
- 已实测可用
- 可优雅收尾
- 可播放

### 新增接口

Agent：

- `GET /dev-recording/status`
- `POST /dev-recording/start`
- `POST /dev-recording/stop`

Server：

- `GET /api/dev-recording/status`
- `POST /api/dev-recording/start`
- `POST /api/dev-recording/stop`

### 默认参数

当前默认：

- `DEV_SCREEN_RECORDING_ENABLED=true`
- `DEV_SCREEN_RECORDING_BACKEND=auto`
- `DEV_SCREEN_RECORDING_OUTPUT_DIR=dashboard_platform/runtime/media/screen_recordings`
- `DEV_SCREEN_RECORDING_FPS=10`
- `DEV_SCREEN_RECORDING_WIDTH=1280`
- `DEV_SCREEN_RECORDING_HEIGHT=720`
- `DEV_SCREEN_RECORDING_DISPLAY=:1`
- `DEV_SCREEN_RECORDING_BITRATE_KBPS=1800`
- `DEV_SCREEN_RECORDING_SHOW_POINTER=true`
- `DEV_SCREEN_RECORDING_USE_DAMAGE=false`

### 文件格式

使用：

- `.mkv`

原因：

- 优雅停止时比 `.mp4` 更稳
- 更适合 `SIGINT` 收尾

### 实际验证结果

已验证：

- `GET /api/dev-recording/status` 成功
- `POST /api/dev-recording/start` 成功
- `POST /api/dev-recording/stop` 成功

生成文件示例：

- [dashboard_platform/runtime/media/screen_recordings/desktop_20260405_023324.mkv](./dashboard_platform/runtime/media/screen_recordings/desktop_20260405_023324.mkv)
- [dashboard_platform/runtime/media/screen_recordings/desktop_20260405_023324.json](./dashboard_platform/runtime/media/screen_recordings/desktop_20260405_023324.json)

`ffprobe` 能正确识别视频流，说明录屏文件可播放。

### 当前对录屏的判断

桌面录屏：

- 不建议默认一直开
- 只在需要时显式启动

原因：

- 当前实际后端是 `x264`，不是 Jetson 硬件编码器
- 录制时仍会增加 CPU，大约三成左右

---

## 10. 配置层当前关键项

关键配置都在：

- [.env](./.env)
- [.env.example](./.env.example)

当前值得注意的项：

### 安全控制

- `ENABLE_MOTION_CONTROL=false`
- `MOTION_CONTROL_MODE=dry_run`
- `CMD_VEL_TOPIC=/cmd_vel_nav`
- `MAX_LINEAR_VEL_SAFE=0.05`
- `MAX_ANGULAR_VEL_SAFE=0.20`
- `CONTROL_COMMAND_TIMEOUT_MS=300`

### 底盘

- `BASE_START_SCRIPT=/home/nvidia/Desktop/wheeltec_ros2/start_base.sh`
- `BASE_NODE_NAME=/wheeltec_robot`
- `BASE_CMD_VEL_TOPIC=/cmd_vel_nav`
- `BASE_ODOM_TOPIC=/odom`
- `BASE_BRINGUP_START_TIMEOUT_SECONDS=18`
- `BASE_STATUS_TIMEOUT_SECONDS=8`

### 状态链

- `STATUS_CACHE_SECONDS=2.0`
- `WEBSOCKET_STATUS_INTERVAL=3.0`

### 地图 / 导航

- `MAPS_DIRECTORY=/home/nvidia/Desktop/wheeltec_ros2/maps`
- `DEFAULT_MAP_NAME=gmap_map`
- `PRESET_COMMANDS` 已切到集成脚本

### 桌面录屏

- `DEV_SCREEN_RECORDING_ENABLED=true`
- `DEV_SCREEN_RECORDING_BACKEND=auto`
- `DEV_SCREEN_RECORDING_OUTPUT_DIR=dashboard_platform/runtime/media/screen_recordings`
- `DEV_SCREEN_RECORDING_FPS=10`
- `DEV_SCREEN_RECORDING_WIDTH=1280`
- `DEV_SCREEN_RECORDING_HEIGHT=720`
- `DEV_SCREEN_RECORDING_DISPLAY=:1`
- `DEV_SCREEN_RECORDING_BITRATE_KBPS=1800`

---

## 11. 当前已经修改过的重要文件清单

以下文件在本对话里被多轮修改过，后续新对话若继续排查或优化，优先看这些：

### Agent / Server

- [dashboard_platform/agent/app.py](./dashboard_platform/agent/app.py)
- [dashboard_platform/agent/config.py](./dashboard_platform/agent/config.py)
- [dashboard_platform/agent/runtime.py](./dashboard_platform/agent/runtime.py)
- [dashboard_platform/agent/motion.py](./dashboard_platform/agent/motion.py)
- [dashboard_platform/agent/schemas.py](./dashboard_platform/agent/schemas.py)
- [dashboard_platform/server/app.py](./dashboard_platform/server/app.py)
- [dashboard_platform/server/hub.py](./dashboard_platform/server/hub.py)

### 前端

- [dashboard_platform/web/src/App.tsx](./dashboard_platform/web/src/App.tsx)
- [dashboard_platform/web/src/components/Panel.tsx](./dashboard_platform/web/src/components/Panel.tsx)
- [dashboard_platform/web/src/styles.css](./dashboard_platform/web/src/styles.css)
- [dashboard_platform/web/tailwind.config.js](./dashboard_platform/web/tailwind.config.js)

### 脚本

- [scripts/start_demo_stack.sh](./scripts/start_demo_stack.sh)
- [scripts/stop_demo_stack.sh](./scripts/stop_demo_stack.sh)
- [scripts/common.sh](./scripts/common.sh)
- [scripts/save_map.sh](./scripts/save_map.sh)
- [scripts/start_navigation_integrated.sh](./scripts/start_navigation_integrated.sh)
- [scripts/start_mapping_integrated.sh](./scripts/start_mapping_integrated.sh)
- [scripts/start_lidar_rviz_debug.sh](./scripts/start_lidar_rviz_debug.sh)

### 配置

- [.env](./.env)
- [.env.example](./.env.example)

---

## 12. 当前遗留问题 / 下一步建议

### 已解决但仍需继续关注

1. 桌面录屏当前是 `x264` fallback
   - 不是 Jetson 真正硬编
   - 如果后续要继续优化占用，可继续研究：
     - 为什么当前镜像没有 `nvv4l2h264enc`
     - 是否能安装/恢复 Jetson GStreamer 插件

2. 建图集成脚本
   - 已绕开老的 `wheeltec_map` 反序列化错误
   - 但仍缺一个“SLAM 已 ready”的更强健康检查

3. 阿克曼转向验证
   - 语义已经改成更合理的“前进左转 / 前进右转”
   - 但实机上仍建议继续只在架空状态下确认前轮偏转和急停表现

### 最建议的新对话优先级

如果开新对话，最合理的优先顺序是：

1. **继续做实机稳定性收口**
   - 导航 / 建图 readiness
   - CPU 降载
   - 桌面录屏与演示策略

2. **不要再大改前端布局**
   - 前端结构已经基本定型

3. **如果要继续录屏优化**
   - 优先查 Jetson 硬编 GStreamer 插件缺失问题
   - 而不是继续扩媒体 UI

---

## 13. 推荐新对话开场用语

你可以在新对话里直接这样说：

> 先阅读项目里的 `CHAT_HANDOFF_2026-04-05.md`，把里面的当前架构、已完成改动、验证结果和遗留问题作为上下文。读完后按文档里的“当前遗留问题 / 下一步建议”继续，不要重复做已经完成的前端布局调整。

如果你想让新对话优先处理某一块，也可以这样说：

### 继续排查实机稳定性

> 先阅读 `CHAT_HANDOFF_2026-04-05.md`。重点延续文档第 12 节里的遗留问题，继续做实机稳定性收口，尤其是导航/建图 readiness、CPU 降载和演示模式优化。

### 继续优化桌面录屏

> 先阅读 `CHAT_HANDOFF_2026-04-05.md`。重点延续里面“桌面录屏当前是 x264 fallback，不是真硬编”的结论，继续排查 Jetson 硬件 GStreamer 编码链为什么不可用，并给出最低风险修复方案。

### 继续收口底盘控制

> 先阅读 `CHAT_HANDOFF_2026-04-05.md`。按里面的阿克曼约束继续，不要引入全向/横移控制。只继续做架空验证、实机稳定性和最小风险收口。

---

## 14. 一句话总结

当前系统已经从“功能探索期”进入“实机稳定演示收口期”：

- 页面结构基本定型
- 三层架构已完整
- 建图/导航/地图/底盘/媒体/桌面录屏都已接入
- 当前重点不再是新增功能，而是：
  - 降载
  - 修实机问题
  - 保证比赛演示稳定

