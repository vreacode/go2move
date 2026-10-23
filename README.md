# GoMove

Go2 upper-computer deployment of the CarMove data-collection system.

The current first-stage profile is data-only. It enables the Dashboard, Agent,
camera adapter, local/video-source frame capture, manifests, gallery APIs and
Tailscale access. It deliberately does not start or send commands to Go2
motion APIs, a Wheeltec chassis, `/cmd_vel_*`, C16 LiDAR, `slam_toolbox`, Nav2,
SIYI ZT6 or battery serial hardware.

## Current snapshot: 2026-10-23

The current Go2 development snapshot includes person search and target management,
multi-person helmet monitoring with the original trained YOLOv5 model, native
camera/audio integration, and ROS2 platform/SLAM/navigation preparation. See
[`deployment/go2/archive_20261023/ARCHIVE.txt`](deployment/go2/archive_20261023/ARCHIVE.txt)
for the exact scope, validation limits, dependency pins and restore instructions.
Motion follow and autonomous patrol still require separate physical validation.
The first-stage notes below describe the original deployment baseline.

## First-stage deployment

Deployment files are under [`deployment/go2`](deployment/go2/):

- copy `go2.env.example` to the local ignored `go2.env`;
- set local paths, credentials and a verified camera source;
- install the two user systemd units from `deployment/go2/systemd/`;
- access the Dashboard through the upper computer's Tailscale address.

The checked-in example uses a local MP4 source until a real Go2 camera path is
confirmed. `tools/camera_adapter.py` provides separate OpenCV and optional
Go2 ROS 2 front-video adapters; it never imports motion-control APIs.

## Repository layout

- `apps/nano-deploy/dashboard_platform`: Agent, Dashboard server and web UI;
- `tools/collect_rtsp_frames.py`: bounded frame capture and CSV manifests;
- `tools/camera_adapter.py`: local file, USB/verified URL and optional Go2 ROS 2 adapters;
- `tools/prepare_yolo_dataset.py` and `tools/validate_yolo_dataset.py`: dataset tooling;
- `deployment/go2`: Go2 profile, systemd units and deployment notes;
- `apps/video-recorder`, `ros2` and legacy launch scripts: retained source reference only;
  they are not part of the Go2 first-stage services.

## Safety boundary

The Go2 profile sets `GO2_DATA_ONLY=true`, disables motion publication and
hardware bridges, uses dry-run motion state, and rejects motion/navigation/base
requests. The local `.env`, runtime data, recordings, model weights, virtual
environments and build artifacts are ignored and must not be committed.
