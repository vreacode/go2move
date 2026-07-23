# SIYI ZT6 吊舱接入

当前板卡通过网口访问 ZT6：

- 板卡：`<BOARD_HOST>`
- ZT6：`<ZT6_HOST>`
- RTSP 主码流：`rtsp://<ZT6_HOST>:8554/video1`
- RTSP 副码流：`rtsp://<ZT6_HOST>:8554/video2`
- SIYI SDK：UDP `<ZT6_HOST>:37260`

## USB 与控制接口的区别

ZT6 用户手册 v1.1 将 Type-C 接口定义为“升级与调参”，将另一个控制信号接口定义为 `S.Bus / UART`。因此，板上出现的 `/dev/ttyACM0` 目前只记录为 USB 调参/升级链路，不能直接假定为实时云台控制串口。

当前项目优先使用 UDP SDK，视频继续使用 RTSP。UART 控制如需启用，应使用控制信号接口的 `TX / RX / GND`，并确认线序和电平后再接线。

## 只读状态查询

协议库位于 `apps/nano-deploy/camera/siyi_zt6.py`，命令行工具只执行硬件 ID、固件、状态和姿态查询，不发送转动、回中、变焦、录像或配置指令：

```bash
cd /home/nvidia/carmove
python3 tools/siyi_zt6_status.py
```

也可以指定地址和超时：

```bash
python3 tools/siyi_zt6_status.py \\
  --host <ZT6_HOST> \\
  --port 37260 \\
  --timeout 2
```

板端 Agent 提供只读接口 `GET /camera/status`，服务端代理为
`GET /api/camera/status`；Dashboard 每 5 秒读取一次并显示链路、模式、录像状态和 Yaw/Pitch。该接口通过线程执行网络查询，不阻塞 Agent 的异步事件循环。

Agent 还提供了受保护的 `POST /camera/gimbal/pulse`、
`POST /camera/gimbal/stop` 和 `POST /camera/gimbal/center`。脉冲控制默认关闭（`SIYI_ZT6_MOTION_ENABLED=false`），并限制速度与持续时间；回中使用手册规定的 `CMD_ID=0x08` 原生指令。

手册规定的 UART 参数为 `115200 8N1`；SDK 数据帧以 `55 66` 开始，CRC16 使用多项式 `0x1021`、初值 `0`、无最终异或。

协议库目前提供了受限的 `turn(yaw_speed, pitch_speed)`、`stop()` 和 `center()` 方法，速度范围固定为 `-100..100`。Dashboard 的左右按钮按现场实际需求映射为左转 `Yaw -10`、右转 `Yaw +10`，上下按钮保持上仰 `Pitch +10`、下俯 `Pitch -10`。

## 当前现场记录

2026-07-18 的只读查询结果：

- UDP 查询成功，收到状态 ACK
- 状态：HDR 关闭、未录像、未插 TF 卡、跟随模式、正常安装方向、HDMI 输出
- 姿态：`yaw=0°、pitch=0°、roll=0°`
- 硬件 ID：`8302209125`

## 首次低速运动测试

在确认云台周围安全、车辆停止后，执行了一次单独的云台测试：

- 指令：Yaw `+10`、Pitch `0`
- 持续时间：`0.5 s`
- 转向 ACK：成功
- 停止 ACK：成功，并补发一次停止指令
- 姿态：Yaw 从 `0°` 变化到约 `-7.2°`，Pitch/Roll 保持 `0°`
- 停止后静置 `1 s`，Yaw 变化 `0.0°`

因此当前设备的实际 Yaw 正方向与手册示例文字相反，需要在控制界面做方向映射。

根据当前现场反馈，Dashboard 已将“左转/右转”按钮对调为 `Yaw-10/Yaw+10`；上下方向仍为 `Pitch+10/Pitch-10`。设备本身的协议方向不做修改。

手册的产品 ID 表将 ZT6 标为 `0x82`，当前设备返回 ID 前缀为 `83`，暂作为固件/硬件版本差异记录，不影响 RTSP 和 UDP 查询链路。

## 安全边界

目前没有在协议库中公开云台运动接口。首次运动测试前，需要确认云台周围没有障碍物、车体处于停止状态，并单独记录测试角度和停止方式。
