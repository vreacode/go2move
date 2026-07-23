# Go2 first-stage deployment

This profile migrates the CarMove data-collection and dashboard path to the
Go2 upper computer. It is intentionally data-only:

- enabled: Agent health, local-file/verified-camera frame capture, manifests,
  gallery APIs, Dashboard, and Tailscale;
- disabled: Go2 motion, ROS navigation, Wheeltec chassis, `/cmd_vel_*`, C16,
  `slam_toolbox`, Nav2, SIYI ZT6, battery serial, pose, LiDAR, and IMU.

## LiDAR preference

When the LiDAR phase is authorized, this profile will prefer the user-mounted
Hesai XT on the dog's back. The built-in Go2 LiDAR remains disabled in the
example configuration, and the Hesai driver is not started until its physical
connection, model, network interface, and live packets are verified. The
current stage only records this selection as configuration; it does not start
any LiDAR, SLAM, Nav2, or motion process.

The isolated official Hesai ROS 2 driver workspace is:
`/home/unitree/hesai_ros2_ws`. It is outside this repository and is not part of
the GoMove commit. The expected future data path is UDP port `2368`, PTC port
`9347`, and ROS topic `/hesai_xt/points`; the device IP is intentionally blank
until it is discovered from the actual network. When the sensor bridge is
explicitly enabled later, the Go2 profile selects this workspace and topic
automatically instead of the legacy C16 topic.

The real local configuration is `go2.env`, which is ignored by Git. Start from
`go2.env.example`; never put passwords, tokens, private addresses, or camera
credentials in the example file.

## Current camera decision

The upper computer currently has no `/dev/video*`, no verified RTSP/WebRTC
process, and no confirmed Go2 ROS 2 video topic. Therefore the first test uses
`runtime/testdata/go2_offline_test.mp4` through the OpenCV adapter.

The official Go2 ROS 2 front-camera path can later use the optional adapter in
`tools/camera_adapter.py`:

```bash
source /opt/ros/foxy/setup.bash
source /path/to/unitree_ros2/setup.sh
/home/unitree/go2move/.venv/bin/python \
  /home/unitree/go2move/tools/collect_rtsp_frames.py \
  --source /frontvideostream \
  --source-type go2_ros2 \
  --output /home/unitree/go2move/runtime/data/images/go2_ros2_test \
  --manifest /home/unitree/go2move/runtime/data/manifests/go2_ros2_test.csv
```

That adapter only subscribes to JPEG bytes in the official Go2 front-video
message. It does not import or call Sport, low-level, stand, lie-down, or
velocity-control APIs.

## Tailscale

Install the official package if it is absent, then authenticate interactively
on the machine. Do not put a Tailscale auth key in this project. After `tailscale
status` is healthy, put the machine IP or MagicDNS name in the local
`TAILSCALE_HOST` value. The server continues to reach the Agent via
`127.0.0.1`.

## Services

The user units in `systemd/` are designed for:

- `nano-board-agent.service`: `127.0.0.1:9001` Agent API;
- `nano-dashboard-server.service`: `0.0.0.0:8080` Dashboard API and web UI.

Both use absolute paths, `EnvironmentFile`, `Restart=on-failure`, and logs in
`runtime/logs`. The Dashboard unit waits for the Agent and the system
`tailscaled.service`.
