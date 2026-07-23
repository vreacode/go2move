# 2026-07-20 项目推进报告

## 本轮目标

继续推进手机现场采集/监控闭环；补齐采集历史回看；确认是否可以读取小车电量；完成源码和实际运行树验证；报告完成后按要求关机。

## 本轮完成

1. 手机“记录”页新增历史采集段列表，不再只显示 Agent 进程内存中的当前任务。
2. Agent 扫描 `CAPTURE_OUTPUT_ROOT/manifests/*.csv`，按最近采集时间排序，返回场景、来源、帧数、时间、备注和代表帧。
3. 增加受保护的历史代表帧接口：`/api/capture/gallery/{group}/latest`；组名校验和文件路径校验均限制在采集根目录内。
4. Android/PWA 手机端历史列表显示代表帧、可见光/红外来源、帧数、时间和备注。
5. 根据 Wheeltec 底盘源码接入只读电量链路：
   - `/PowerVoltage`：`std_msgs/Float32`，单位 V；
   - `/robot_charging_flag`：充电状态；
   - `/robot_charging_current`：充电电流。
6. Agent 新增后台 ROS 订阅桥，不发布任何底盘或云台命令；手机监控页显示电压/百分比/充电状态。
7. 百分比计算要求显式配置 `BATTERY_EMPTY_VOLTAGE` 和 `BATTERY_FULL_VOLTAGE`；未标定时只显示真实电压，不伪造百分比和低电量告警。
8. 修复电量桥在 SIGTERM 下的 ROS shutdown 双重调用问题。

## 源码提交

- `4eb4fd1 feat: add capture session history gallery`
- `532ab6b feat: expose read-only robot battery status`

前端书籍目录和 ZT6 手册仍保持未跟踪，未进入提交。

## 验证证据

- 依赖轻量单测：38 项通过。
- 源码 Dashboard `npm run build`：通过。
- 实际 systemd 工作树 Dashboard `npm run build`：通过。
- Agent/Server Python 编译和路由导入：通过。
- `nano-board-agent.service`：`active`。
- `nano-dashboard-server.service`：`active`。
- 登录、Agent `/health`、Dashboard `/api/system/status`：HTTP 200。
- 实际手机采集历史接口：HTTP 200，返回 2 个会话。
- 实际历史代表帧接口：HTTP 200，`image/jpeg`，读取成功。

## 电量实机结果

为验证真实链路，临时启动了底盘基础 ROS 链路；未发送速度命令、未启动导航、未操作云台。

- `/PowerVoltage` 真实消息：约 `22.717 V`。
- Agent 连续读取：约 `22.717–22.720 V`。
- 启动底盘 readiness：底盘节点、`cmd_vel` 订阅和 `/odom` 均就绪。
- 读取完成后已停止底盘；最终状态为 `running=false`、`armed=false`、`dry_run`、`manual_override=false`、`real_publish_enabled=false`。
- 底盘停止后电池状态变为 `available=false`，这是因为没有新的 ROS 发布者；Agent 保留最后读数但不会把它误报为当前在线电量。

## 当前限制

当前已补充底盘实测标定：稳定满电运行电压约 `22.6 V`，安全下限设为 `20.0 V`，约 `19.5 V` 会触发底盘自动断电。因此 Agent 默认按 `22.6 V = 100%`、`20.0 V = 0%` 换算，并把 `20.0 V` 作为低电压告警线，把 `19.5 V` 视为硬件断电边界。电压采用最近 5 次采样的中位数滤波，低电压状态连续 3 次确认，避免单次跳动影响状态。

```dotenv
BATTERY_EMPTY_VOLTAGE=20.0
BATTERY_FULL_VOLTAGE=22.6
BATTERY_LOW_VOLTAGE=20.0
```

手机端现在会显示按该标定换算的百分比和稳定电压；如需现场微调，可在实际 `.env` 覆盖上述参数。

## 后续串口电量部署

为避免“底盘上电但未启动 ROS 驱动”时手机显示未知，Agent 增加了只读串口电压服务。它读取 `/dev/ttyCH343USB0` 的 Wheeltec 24 字节遥测帧，只发布 `/PowerVoltage`，不发送速度命令。与官方底盘驱动采用串口单一所有者切换：

- 无完整底盘 ROS 时，只读服务实测读取 `22.495 V`，手机接口显示约 `96.0%`。
- 启动官方底盘后，只读服务释放串口，`wheeltec_robot` 接管并实测读取 `22.494 V`，手机接口显示约 `95.9%`。
- 停止官方底盘后，只读服务自动恢复并实测读取 `22.496 V`，手机接口显示约 `96.0%`。
- 验证期间未发送速度命令，最终底盘停止、控制保持 `dry_run` 且未 armed。

## 实际运行树说明

本轮新增功能已同步到 `/home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy` 并重启验证。该工作树原本包含用户自己的云台、停车、事件记录和推理改动，因此没有把整棵树提交；源码仓库的可审查版本已由相关提交保存。

## 关机前状态

报告写入后执行过关机命令。关机前保持底盘停止、控制 dry-run/locked、无模型和导航任务；原报告记录时尚未得到满电标定，随后根据稳定满电 `22.6V`、安全下限 `20.0V` 补充了百分比换算，并增加了抗跳动滤波和连续确认。
