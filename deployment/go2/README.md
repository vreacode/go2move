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

## Camera adapters

The profile does not assume that a Go2 camera is RTSP. The available explicit
adapters are:

- `opencv`: local MP4, USB index, or a separately verified URL;
- `go2_ros2`: the official `/frontvideostream` message;
- `go2_ros2_videohub`: the official read-only video sample API, returning one
  JPEG per request;
- `unitree_sdk2`: the official SDK2 `VideoClient` camera API.

On this upper computer, `go2_ros2_videohub` is the verified live path. The
`/frontvideostream` topic is discoverable but its byte sequences are malformed
when decoded by the installed Foxy middleware, so it is not selected.

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

The verified live adapter can be tested with:

```bash
source /home/unitree/go2_ros2_ws/setup_go2_foxy.sh
export CYCLONEDDS_URI=file:///path/to/local/cyclonedds_go2.xml
/home/unitree/go2move/.venv/bin/python \
  /home/unitree/go2move/tools/collect_rtsp_frames.py \
  --source /api/videohub \
  --source-type go2_ros2_videohub \
  --output /home/unitree/go2move/runtime/data/images/go2_videohub_test \
  --manifest /home/unitree/go2move/runtime/data/manifests/go2_videohub_test.csv
```

The Dashboard capture button invokes this same script and passes the active
`CAMERA_SOURCE_TYPE` and `CAMERA_SOURCE` from the Go2 profile. Captured JPEGs
are published atomically only after a decode check, and each new manifest also
records the adapter type, image dimensions, JPEG quality, and encoded byte
count. The default Dashboard capture quality is 100; use `--jpeg-quality` to
choose a smaller file size for large datasets.

For the Go2 videohub profile, the live Dashboard view defaults to an
authenticated HTTP image proxy backed by a persistent camera worker. It is
more compatible with mobile browsers than browser WebRTC and keeps the last
complete frame while the next one is being fetched. The WebRTC path remains
available from the UI for testing.

The local CycloneDDS file must bind the Ethernet interface to its configured
Go2-network address; keep that machine-specific address out of Git.

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

## Bounded autonomous walking

The local XT16 controller supports forward walking, stationary obstacle-avoidance
turns, and a radius around the starting odometry position. Defaults are a 1 m
radius, 0.08 m/s, and 30 seconds. Preview only subscribes to sensors:

```bash
cd /home/unitree/go2move
bash deployment/go2/start_local_autonomy.sh --radius 1 --duration 30
```

See [LOCAL_AUTONOMY.txt](LOCAL_AUTONOMY.txt) for the supervised execution command,
stop behavior, evidence, and current acceptance limits. Actual walking remains
unverified: the latest real preview rejected the prone robot and a scan return
inside its conservative clearance. This controller requires a flat floor and
does not detect drop-offs. Prior stance incidents require onsite inspection
before the explicit `--execute --onsite-confirmed` mode is used.
