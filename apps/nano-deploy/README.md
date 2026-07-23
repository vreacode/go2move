# Low-Latency Vehicle Control and Detection Dashboard

## Directory Structure

```text
Nano_Deploy/
├── .env.example
├── README.md
├── requirements-platform.txt
├── nano_rtsp_detect.py
├── vision/
│   ├── event_rules.py
│   └── test_event_rules.py
├── helmet.pt / fall.pt / smoke.pt / danger.pt
├── dashboard_platform/
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── app.py
│   │   ├── config.py
│   │   ├── logs.py
│   │   ├── runtime.py
│   │   ├── schemas.py
│   │   └── webrtc.py
│   ├── server/
│   │   ├── __init__.py
│   │   ├── app.py
│   │   ├── config.py
│   │   └── hub.py
│   └── web/
│       ├── index.html
│       ├── package.json
│       ├── postcss.config.js
│       ├── tailwind.config.js
│       ├── tsconfig.json
│       ├── tsconfig.node.json
│       ├── vite.config.ts
│       └── src/
│           ├── App.tsx
│           ├── lib.ts
│           ├── main.tsx
│           ├── styles.css
│           ├── types.ts
│           ├── vite-env.d.ts
│           └── components/
│               └── Panel.tsx
└── deploy/
    └── systemd/
        ├── nano-board-agent.service
        └── nano-dashboard-server.service
```

## Overview

This project implements the required three-layer architecture:

1. Frontend dashboard: React + Vite + TypeScript + TailwindCSS.
2. Server-side API/signaling layer: FastAPI.
3. Board-resident agent: FastAPI running on the Jetson/Nano side.

Low latency comes from WebRTC. The browser does **not** pull RTSP directly. The browser also does **not** receive frames over WebSocket. The desktop shell is not the latency optimization point. If you later wrap this in Tauri, the media path remains WebRTC and the latency behavior stays essentially the same.

## Features

- WebRTC-first live video with connection state and stream stats.
- Video source modes for visible raw, infrared raw, and annotated detection output.
- Optional MJPEG fallback preview, disabled by default.
- Model selection and start/stop/restart through the board Agent.
- PID-based process supervision for `nano_rtsp_detect.py`.
- Real-time log aggregation from the Agent to the server and then to the frontend through WebSocket.
- Whitelist-only car control commands with ACK responses.
- Workflow panels for mapping, map saving, navigation, and RViz launch through the Agent whitelist.
- Online 2D map viewer that reads saved `yaml + pgm` maps from the configured maps directory.
- Status cards for Agent availability, video source, model, CPU, memory, GPU, and heartbeat.

The `vision/` package contains the first patrol MVP rule layer: a small IoU
tracker and a no-parking ROI + dwell-time rule. It is intentionally separate
from ROS and motion control. See `vision/README.md` for the interface and
tests.

## Product Direction

The current stable demo route is:

1. Frontend for live video, workflow control, map viewing, and status display.
2. RViz for professional navigation operations such as initial pose setting and deeper navigation debugging.

At this stage, the frontend intentionally does **not** try to replace RViz for full navigation debugging. The browser is used for stable demo workflows, while RViz remains the recommended tool for:

- setting the initial pose
- checking TF and localization details
- path / costmap / planner debugging
- deeper lidar and navigation inspection

## API Summary

### Server

- `POST /api/auth/login`
- `GET /api/system/status`
- `GET /api/camera/status`
- `POST /api/camera/gimbal/pulse`
- `POST /api/camera/gimbal/stop`
- `POST /api/camera/gimbal/center`
- `POST /api/model/start`
- `POST /api/model/stop`
- `POST /api/model/restart`
- `GET /api/model/status`
- `POST /api/control/send`
- `POST /api/preset/run`
- `GET /api/maps`
- `GET /api/maps/{map_name}`
- `GET /api/maps/{map_name}/pgm`
- `POST /api/stream/webrtc-offer`
- `GET /api/stream/fallback`
- `WebSocket /ws/status`
- `WebSocket /ws/logs`
- `WebSocket /ws/sensors`
- `WebSocket /ws/pose`

### Agent

- `GET /health`
- `GET /status`
- `GET /camera/status`
- `POST /camera/gimbal/pulse`
- `POST /camera/gimbal/stop`
- `POST /camera/gimbal/center`
- `GET /models`
- `POST /model/start`
- `POST /model/stop`
- `POST /model/restart`
- `GET /model/status`
- `POST /control/send`
- `POST /preset/run`
- `GET /maps`
- `GET /maps/{map_name}`
- `GET /maps/{map_name}/pgm`
- `GET /logs/stream`
- `GET /sensors/stream`
- `GET /pose/current`
- `GET /pose/stream`
- `POST /stream/webrtc-offer`
- `GET /preview.mjpeg`

## Configuration

Copy the example file:

```bash
cd /home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy
cp .env.example .env
```

Key required variables:

- `AUTH_PASSWORD`
- `AUTH_TOKEN`
- `AGENT_TOKEN`

Everything else is already aligned to the current device profile and should normally stay as shipped:

- `BOARD_HOST=<BOARD_HOST>`
- `TAILSCALE_HOST=` (must stay empty for the current LAN-only setup)
- `CAMERA_RTSP_VISIBLE_URL=rtsp://<CAMERA_HOST>:8554/video1`
- `CAMERA_RTSP_IR_URL=rtsp://<CAMERA_HOST>:8554/video2`
- `AVAILABLE_MODELS=helmet/fall/smoke/danger`
- `MAPS_DIRECTORY=/home/nvidia/Desktop/wheeltec_ros2/maps`
- `DEFAULT_MAP_NAME=gmap_map`
- `PRESET_COMMANDS=start_mapping/save_map/start_navigation/open_rviz/stop_mapping/stop_navigation/stop_all`
- `LIDAR_POINT_TOPIC=/c16/lslidar_point_cloud`
- `IMU_TOPIC=/imu/data`
- `ROS2_LIDAR_SETUP_BASH=/home/nvidia/fastlio2_ws/install/setup.bash`
- `ROS2_IMU_SETUP_BASH=/home/nvidia/serial-ros2/install/setup.bash;/home/nvidia/imu-driver/ROS2 SDK/yesense_ros2/install/setup.bash`
- `SENSOR_BRIDGE_ENABLED=false` for the default stable demo profile
- `CONTROL_COMMAND_MAP` stays on the safe placeholder whitelist until you provide the real chassis command set
- `ENABLE_MOTION_CONTROL=false`
- `MOTION_CONTROL_MODE=dry_run`
- `CMD_VEL_TOPIC=/cmd_vel_nav`
- `REQUIRE_MANUAL_ARM=true`
- `DISABLE_MANUAL_WHEN_NAV_RUNNING=true`

No production IP or command is hardcoded outside the config layer.

Current device-aligned defaults in `.env.example`:

- Board host: `<BOARD_HOST>`
- Tailscale host: empty
- Visible RTSP: `rtsp://<CAMERA_HOST>:8554/video1`
- Infrared RTSP: `rtsp://<CAMERA_HOST>:8554/video2`
- Lidar topic: `/c16/lslidar_point_cloud`
- IMU topic: `/imu/data`
- Models: `helmet.pt`, `fall.pt`, `smoke.pt`, `danger.pt`
- Presets:
  - `start_mapping -> background bash /home/nvidia/start_2d_mapping.sh`
  - `save_map -> ros2 run nav2_map_server map_saver_cli -f /home/nvidia/Desktop/wheeltec_ros2/maps/{map_name}`
  - `start_navigation -> background bash /home/nvidia/start_2d_navigation.sh {map_name}`
  - `open_rviz -> background rviz2 -d /opt/ros/humble/share/nav2_bringup/rviz/nav2_default_view.rviz`
  - `stop_mapping -> bash /home/nvidia/start_2d_mapping.sh stop`
  - `stop_navigation -> bash /home/nvidia/start_2d_navigation.sh stop`
- Control commands are still safe placeholders and must be replaced only after you confirm the real chassis command line.
- Recording is not started through a preset command. Use `POST /api/media/recording/start` and `POST /api/media/recording/stop` for RTSP recording, and `POST /api/dev-recording/start` and `POST /api/dev-recording/stop` for on-demand desktop capture.

## Local Development

### 1. Install Python dependencies

Use the existing project virtual environment for the board-side runtime:

```bash
cd /home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy
source .venv/bin/activate
pip install -r requirements-platform.txt
```

If you want strict environment isolation, keep `PYTHONNOUSERSITE=1` in the systemd units below so runtime imports do not mix with user site-packages.

### 2. Run the board Agent

```bash
cd /home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy
source .venv/bin/activate
uvicorn dashboard_platform.agent.app:app --host 0.0.0.0 --port 9001
```

### 3. Run the server

You can reuse the same Python environment:

```bash
cd /home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy
source .venv/bin/activate
uvicorn dashboard_platform.server.app:app --host 0.0.0.0 --port 8080
```

### 4. Run the frontend in development mode

```bash
cd /home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy/dashboard_platform/web
npm install
npm run dev
```

Create a frontend dev env if needed:

```bash
echo 'VITE_API_BASE=http://<BOARD_HOST>:8080' > .env.local
```

## Demo Startup

For repeatable live demos, use the unified scripts instead of launching each process manually.

### Recommended startup

```bash
cd /home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy
bash scripts/start_demo_stack.sh
```

This entrypoint will:

1. Stop stale old processes first.
2. Start the lidar driver.
3. Start the raw yesense IMU driver.
4. Start the IMU republisher `/imu/data_raw -> /imu/data`.
5. Start the Agent.
6. Start the Server.

Notes:

- The default demo startup is headless. It does not open RViz.
- The stable demo path does not depend on frontend lidar debugging.
- `sensor_bridge` is retained as a debug capability, but it is not part of the default stable demo flow.
- Logs go to `dashboard_platform/runtime/logs/`.
- PID files go to `dashboard_platform/runtime/pids/`.

## Desktop Screen Recording

Desktop screen recording is an on-demand demo helper. It records the full X11 desktop, so RViz windows are included in the output.

Use these commands from the project root:

```bash
bash scripts/start_screen_recording.sh
bash scripts/status_screen_recording.sh
bash scripts/stop_screen_recording.sh
```

The scripts automatically read `AUTH_TOKEN` from `.env` and call the authenticated server APIs for you. You do not need to export tokens or build curl headers manually.

Recorded files are written to:

```text
dashboard_platform/runtime/media/screen_recordings
```

Do not leave desktop recording running by default. Start it only when you explicitly need a demo capture.

## Stable Demo v1 Usage

### Recommended startup order

1. Start the full demo stack:

```bash
cd /home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy
bash scripts/start_demo_stack.sh
```

2. Open the dashboard in the browser:

```text
http://<BOARD_HOST>:8080
```

3. Log in with the configured dashboard account.

### Recommended demo order

1. Confirm the top status shows `Agent online` and the video is connected.
2. In `建图与导航`, click `启动建图`.
3. Move the robot and show the live video plus the workflow state.
4. Click `保存地图`.
5. In `地图查看`, refresh the map list if needed and select the saved map.
6. Show the saved 2D map image and metadata:
   - map name
   - resolution
   - origin
   - size
7. Click `启动导航`.
8. Open `RViz` only when you need professional navigation operations such as initial pose setting or deeper nav debugging.

### How to verify after saving a map

1. The frontend should return from `保存地图` without an error banner.
2. The new map name should appear in the map list after refresh.
3. The saved files should exist on disk:

```bash
ls -l /home/nvidia/Desktop/wheeltec_ros2/maps/<map_name>.yaml
ls -l /home/nvidia/Desktop/wheeltec_ros2/maps/<map_name>.pgm
```

4. Selecting the map in `地图查看` should load the image and metadata successfully.

### How to verify after starting navigation

1. The `Navigation` status in the dashboard should change to `running`.
2. RViz can then be opened for:
   - initial pose setting
   - goal setting
   - nav debugging
3. If needed, confirm the navigation stack from the host:

```bash
pgrep -af 'start_2d_navigation.sh|amcl|nav2'
```

### When to open RViz

Use RViz when you need professional navigation interaction that the frontend intentionally does not replace:

- set the initial pose
- inspect localization quality
- inspect TF / costmap / planner state
- debug navigation failures

### Recommended stop

```bash
cd /home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy
bash scripts/stop_demo_stack.sh
```

Stop order is the reverse of startup:

1. Server
2. Agent
3. IMU republisher
4. IMU raw driver
5. Lidar driver

### Demo Troubleshooting

- If you see GUI windows appear during demo startup, you are not using the headless script.
- If the script says `failed: lidar topic`, check `dashboard_platform/runtime/logs/lidar_driver.log`.
- If the script says `failed: imu raw topic`, check `dashboard_platform/runtime/logs/imu_driver.log`.
- If the script says `failed: imu standard topic`, check `dashboard_platform/runtime/logs/imu_republisher.log`.
- If the script says `failed: agent not ready`, check `dashboard_platform/runtime/logs/agent.log`.
- If the script says `failed: server status api`, check `dashboard_platform/runtime/logs/server.log`.
- If the dashboard opens but lidar/imu stay offline, call `GET /api/system/status` and inspect `sensor_error`.

## Common Troubleshooting

### Video does not display

1. Check that `Agent online` is visible in the header.
2. Check that the selected video mode is valid:
   - visible
   - infrared
   - annotated only after a model is started
3. If WebRTC fails, temporarily switch to `Use Fallback` to confirm the camera path still works.
4. Check:
   - `dashboard_platform/runtime/logs/agent.log`
   - `dashboard_platform/runtime/logs/server.log`

### Map list is empty

1. Confirm the maps directory exists:

```bash
ls -l /home/nvidia/Desktop/wheeltec_ros2/maps
```

2. Confirm `MAPS_DIRECTORY` in `.env` points to the correct path.
3. Refresh the map list in the frontend.

### Map cannot be displayed

1. Confirm both files exist for the selected map:

```bash
ls -l /home/nvidia/Desktop/wheeltec_ros2/maps/<map_name>.yaml
ls -l /home/nvidia/Desktop/wheeltec_ros2/maps/<map_name>.pgm
```

2. Confirm the YAML references the correct image file.
3. If the frontend shows a map load error, check `server.log` and `agent.log`.

### save_map fails

1. Confirm mapping is actually running first.
2. Confirm `/map` is published:

```bash
source /opt/ros/humble/setup.bash
ROS2CLI_NO_DAEMON=1 ros2 topic info /map -v
```

3. If `/map` is not ready, start mapping before saving.
4. Check:
   - `dashboard_platform/runtime/logs/agent.log`
   - `dashboard_platform/runtime/logs/server.log`

### Navigation start fails

1. Confirm the target map exists in `/home/nvidia/Desktop/wheeltec_ros2/maps`.
2. Confirm the selected map name matches the actual saved map.
3. Check whether nav-related processes started:

```bash
pgrep -af 'start_2d_navigation.sh|amcl|nav2'
```

4. If needed, open RViz and inspect localization / initial pose / planner state.

### RViz cannot open

1. Confirm `rviz2` is installed and available:

```bash
command -v rviz2
```

2. Confirm you are in a GUI-capable session.
3. If the frontend button returns success but no window appears, inspect:
   - `dashboard_platform/runtime/logs/agent.log`
4. If you only need the stable demo, RViz is optional and not required for the browser-side workflow itself.

### Optional RViz Debug

RViz is now optional and disabled by default for demos.

If you want the original lidar RViz debug session later, run it manually in a separate terminal:

```bash
cd /home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy
bash scripts/start_lidar_rviz_debug.sh
```

That script is for local debugging only. Do not use it as the default demo startup path.

## Production Deployment

### 1. Build the frontend

```bash
cd /home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy/dashboard_platform/web
npm install
npm run build
```

The FastAPI server automatically serves `dashboard_platform/web/dist`.

### 2. Start the services

Recommended order:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nano-board-agent.service
sudo systemctl enable --now nano-dashboard-server.service
```

### 3. Open the dashboard

```text
http://<SERVER_HOST>:8080
```

## Mapping and Navigation Integration

The stable route is:

- Frontend: workflow control, status, saved-map preview
- RViz: professional interaction for TF, laser overlays, costmaps, planner debugging, and full robot visualization

This project intentionally does not try to recreate RViz in the browser. The frontend only integrates the already-working workflow:

- `Start Mapping`
- `Save Map`
- `Start Navigation`
- `Open RViz`
- `Stop Mapping`
- `Stop Navigation`

All of these buttons go through `POST /api/preset/run` and then to the Agent whitelist. The frontend never shells out directly.

### Online Map Viewer

Saved maps are read from `MAPS_DIRECTORY`. The dashboard lists `*.yaml` files, loads their metadata, then fetches the matching `*.pgm` image and renders it in-browser as a 2D occupancy map preview.

Current scope:

- List saved maps
- Select a map name such as `gmap_map`
- Show resolution, origin, and image size
- Render the saved 2D map image online

Intentionally left in RViz:

- TF tree inspection
- Costmap overlays
- Path/goal marker interaction
- Rich robot model display
- Full navigation debugging

That split is the stable choice for this project: the web UI handles workflow and map viewing, while RViz keeps the ROS-native professional displays.

### Reusable Existing Resources

Existing resources found in the current `wheeltec_ros2` workspace:

- Nav2 RViz config:
  `/home/nvidia/Desktop/wheeltec_ros2/src/navigation2-humble/nav2_bringup/rviz/nav2_default_view.rviz`
- Wheeltec navigation RViz config:
  `/home/nvidia/Desktop/wheeltec_ros2/src/wheeltec_robot_nav2/rviz/wheeltec.rviz`
- Gmapping RViz config:
  `/home/nvidia/Desktop/wheeltec_ros2/src/turn_on_wheeltec_robot/rviz/gmapping.rviz`
- Wheeltec URDF models:
  `/home/nvidia/Desktop/wheeltec_ros2/src/wheeltec_robot_urdf/wheeltec_robot_urdf/urdf/`
- Robot description / TF launch references:
  `/home/nvidia/Desktop/wheeltec_ros2/src/turn_on_wheeltec_robot/launch/robot_mode_description.launch.py`

These are good sources of truth for RViz and future refinement. They are not copied into the web UI because RViz display plugins and TF-driven ROS visualization are still the more stable choice for those tasks.

## WebRTC Notes

- The low-latency path is `RTSP camera -> board Agent -> WebRTC -> browser`.
- Visible raw mode uses `CAMERA_RTSP_VISIBLE_URL`.
- Infrared raw mode uses `CAMERA_RTSP_IR_URL`.
- Annotated mode uses `nano_rtsp_detect.py -> snapshot_dir/latest.jpg -> Agent -> WebRTC`.
- The dashboard defaults to visible raw mode.
- The video panel can switch between visible raw, infrared raw, and annotated output.
- In this round, infrared only participates as a raw preview source. Detection still runs on the visible stream.
- WebRTC is the primary transport.
- MJPEG fallback exists only as an operational fallback and is disabled by default.
- Frontend control traffic and video traffic are separated so control ACKs do not block the media path.
- At the current stage, the project is ready for `real video + real model management + placeholder control ACK`.

## Multi-Camera Notes

- `visible_raw` -> visible-light RTSP stream
- `ir_raw` -> infrared RTSP stream
- `annotated` -> visible-light detection result stream
- Default mode: `visible_raw`
- Infrared detection is not implemented in this round.
- A future extension can add an independent infrared detection chain without changing the existing three-layer architecture.

## Lidar + IMU MVP

- The Agent now starts a lightweight ROS2 sensor bridge subprocess inside the current project.
- The bridge sources:
  - `/home/nvidia/fastlio2_ws/install/setup.bash`
  - `/home/nvidia/serial-ros2/install/setup.bash`
  - `/home/nvidia/imu-driver/ROS2 SDK/yesense_ros2/install/setup.bash`
- It subscribes to:
  - `/c16/lslidar_point_cloud`
  - `/imu/data`
- It downsamples pointcloud frames before sending them to the frontend:
  - `POINTCLOUD_MAX_POINTS`
  - `POINTCLOUD_SEND_HZ`
  - `POINTCLOUD_KEEP_SECONDS`
- The frontend receives lidar + IMU over `WebSocket /ws/sensors` and renders:
  - live point cloud
  - simplified vehicle body
  - IMU-driven vehicle pose
- Default behavior:
  - visible video remains the main display path
  - lidar/IMU render in a separate dashboard panel
  - annotated detection still uses the visible-light pipeline only

### Sensor Startup Notes

If `/c16/lslidar_point_cloud` or `/imu/data` is offline, the dashboard will show the sensor panel as offline and surface a waiting message instead of failing silently.

Typical bring-up commands on the board:

```bash
source /home/nvidia/fastlio2_ws/install/setup.bash
ros2 launch lslidar_driver lslidar_c16_launch.py
```

```bash
source /home/nvidia/serial-ros2/install/setup.bash
source "/home/nvidia/imu-driver/ROS2 SDK/yesense_ros2/install/setup.bash"
ros2 launch yesense_std_ros2 yesense_node.launch.py
```

The yesense driver publishes real data on `/imu/data_raw` and `/imu_data`. This project standardizes the IMU stream onto `/imu/data` with a small republisher:

```bash
cd /home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy
bash scripts/start_yesense_std_imu.sh
```

That script keeps the original driver unchanged and republishes `/imu/data_raw -> /imu/data` so the dashboard and sensor bridge can subscribe to the standard topic name.

This MVP uses configurable approximate extrinsics first:

- `LIDAR_TO_BASE_TRANSLATION`
- `LIDAR_TO_BASE_ROTATION_RPY`
- `IMU_TO_BASE_ROTATION_RPY`
- `CAR_MODEL_FORWARD_AXIS`
- `CAR_MODEL_UP_AXIS`
- `POINTCLOUD_HISTORY_SECONDS`

If you later finish exact calibration, you can update those values without changing the architecture.

### Lidar View Alignment

The dashboard uses these scene conventions:

- ROS/base frame: `x=forward, y=left, z=up`
- Three.js scene: `x=right, y=up, z=forward`

The frontend now performs a consistent `ROS -> Three` basis conversion before rendering. Keep the default extrinsics at zero first:

- `LIDAR_TO_BASE_ROTATION_RPY=[0.0,0.0,0.0]`
- `IMU_TO_BASE_ROTATION_RPY=[0.0,0.0,0.0]`
- `CAR_MODEL_FORWARD_AXIS=positive_z`
- `CAR_MODEL_UP_AXIS=positive_y`

Quick calibration guidance:

- If the point cloud looks upright like a wall instead of lying on the ground, adjust `LIDAR_TO_BASE_ROTATION_RPY`.
- If the car model looks sideways or upside down, adjust `CAR_MODEL_FORWARD_AXIS` and `CAR_MODEL_UP_AXIS` first, then `IMU_TO_BASE_ROTATION_RPY`.
- If the cloud flickers too much in single-frame mode, switch the panel to `短时历史` and tune `POINTCLOUD_HISTORY_SECONDS` between `0.5` and `1.0`.

## Security Notes

- Frontend only talks to the server.
- The server authenticates users and forwards to the Agent with a separate shared token.
- All control and preset actions are whitelist-driven through `CONTROL_COMMAND_MAP` and `PRESET_COMMANDS`.
- The frontend never constructs shell commands.
- The shipped `CONTROL_COMMAND_MAP` intentionally does not drive the chassis. Replace it only after validating the real base-control command set.
- Motion control is now routed through an internal Agent-side ROS2 `/cmd_vel` bridge, not direct shell commands per button press.
- Default safety posture is `dry_run + locked`.
- Real motion publishing stays disabled until you manually set `ENABLE_MOTION_CONTROL=true` and `MOTION_CONTROL_MODE=real`.
- Even after enabling real publishing, the recommended sequence is:
  1. wheels off ground
  2. verify `/cmd_vel_nav` traffic and stop timeout behavior
  3. only then perform a very low-speed open-area test
- Manual control is rejected while navigation is running unless you explicitly enable manual override.

## Tauri Packaging Path

Later desktop packaging should use Tauri, not Electron:

1. Keep `dashboard_platform/web` as the UI.
2. Add a `src-tauri/` shell later that loads the same dashboard.
3. Continue using the same HTTP/WebSocket/WebRTC APIs.
4. Do not move media transport into the desktop shell. Let WebRTC remain the real-time path.

This keeps the current architecture intact and avoids rewriting the dashboard just to create a desktop wrapper.
