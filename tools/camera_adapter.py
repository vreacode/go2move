"""Camera source adapters used by the data-only capture pipeline.

OpenCV handles local files, USB camera indexes, and verified network URLs.  The
Go2 adapters are intentionally opt-in: one reads the official SDK2
``VideoClient.GetImageSample`` response and the other subscribes to the official
``/frontvideostream`` message.  No sport, low-level, pose, or motion API is
imported here.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Protocol


class CameraAdapter(Protocol):
    def isOpened(self) -> bool:  # noqa: N802 - matches cv2.VideoCapture
        ...

    def read(self) -> tuple[bool, Any]:
        ...

    def release(self) -> None:
        ...


class OpenCVCameraAdapter:
    """Adapter for a local file, V4L2 index, RTSP URL, or another OpenCV source."""

    def __init__(self, source: str) -> None:
        import cv2

        value: int | str = int(source) if source.isdigit() else source
        self._cv2 = cv2
        self._source = value
        self._loop_file = isinstance(value, str) and Path(value).expanduser().is_file()
        self._capture = cv2.VideoCapture(value)

    def isOpened(self) -> bool:  # noqa: N802 - matches cv2.VideoCapture
        return bool(self._capture.isOpened())

    def read(self) -> tuple[bool, Any]:
        ok, frame = self._capture.read()
        if ok or not self._loop_file:
            return ok, frame
        # Offline MP4 fixtures are reusable test sources. Reopen only local
        # files at EOF; live cameras and network streams keep normal failure
        # semantics so disconnects are visible to the caller.
        self._capture.release()
        self._capture = self._cv2.VideoCapture(self._source)
        return self._capture.read()

    def release(self) -> None:
        self._capture.release()


class Go2Ros2FrontVideoAdapter:
    """Decode official Go2 ROS 2 front-camera JPEG messages.

    ``source`` is the ROS 2 topic and defaults to ``/frontvideostream``.  This
    adapter is not selected automatically because this machine currently has
    no confirmed Go2 ROS 2 video topic.
    """

    def __init__(self, source: str = "/frontvideostream", resolution: str = "video720p") -> None:
        import cv2
        import numpy as np
        import rclpy
        from rclpy.node import Node
        from unitree_go.msg import Go2FrontVideoData

        if resolution not in {"video720p", "video360p", "video180p"}:
            raise ValueError("resolution must be video720p, video360p, or video180p")
        if not rclpy.ok():
            rclpy.init(args=None)
        self._rclpy = rclpy
        self._cv2 = cv2
        self._np = np
        self._node = Node("go2_front_camera_adapter")
        self._latest = None
        self._resolution = resolution
        self._subscription = self._node.create_subscription(
            Go2FrontVideoData,
            source or "/frontvideostream",
            self._on_frame,
            10,
        )

    def _on_frame(self, message: Any) -> None:
        payload = bytes(getattr(message, self._resolution, b""))
        if payload:
            self._latest = payload

    def isOpened(self) -> bool:  # noqa: N802 - matches cv2.VideoCapture
        return self._node is not None

    def read(self) -> tuple[bool, Any]:
        deadline = time.monotonic() + 0.25
        while time.monotonic() < deadline:
            self._rclpy.spin_once(self._node, timeout_sec=0.05)
            if self._latest:
                payload = self._latest
                self._latest = None
                frame = self._cv2.imdecode(self._np.frombuffer(payload, dtype=self._np.uint8), self._cv2.IMREAD_COLOR)
                if frame is not None:
                    return True, frame
        return False, None

    def release(self) -> None:
        if self._node is not None:
            self._node.destroy_node()
            self._node = None
        if self._rclpy.ok():
            self._rclpy.shutdown()


class Go2Sdk2FrontVideoAdapter:
    """Read the Go2 front camera through the official SDK2 VideoClient.

    ``source`` is the network interface used by CycloneDDS, for example
    ``eth0``.  The SDK is an optional dependency and is imported only for this
    explicit source type.  The official camera client is read-only; this
    adapter does not construct SportClient, MotionSwitcher, or low-level
    motor clients.

    ``UNITREE_SDK2_PYTHONPATH`` may point at a source checkout when the SDK is
    not installed into the application's virtual environment.
    """

    def __init__(self, source: str = "", timeout_seconds: float = 3.0) -> None:
        import importlib
        import os
        import sys

        import cv2
        import numpy as np

        sdk_path = os.environ.get("UNITREE_SDK2_PYTHONPATH", "").strip()
        if sdk_path and sdk_path not in sys.path:
            sys.path.insert(0, sdk_path)

        try:
            channel = importlib.import_module("unitree_sdk2py.core.channel")
            video = importlib.import_module("unitree_sdk2py.go2.video.video_client")
        except ImportError as exc:
            raise RuntimeError(
                "unitree_sdk2py is required for CAMERA_SOURCE_TYPE=unitree_sdk2; "
                "install the official SDK or set UNITREE_SDK2_PYTHONPATH"
            ) from exc

        interface = (source or os.environ.get("UNITREE_SDK2_INTERFACE", "")).strip()
        if interface.lower() in {"auto", "default"}:
            interface = ""
        if interface:
            channel.ChannelFactoryInitialize(0, interface)
        else:
            channel.ChannelFactoryInitialize(0)

        client = video.VideoClient()
        client.SetTimeout(float(timeout_seconds))
        client.Init()
        self._cv2 = cv2
        self._np = np
        self._client = client
        self._interface = interface or "auto"
        self._released = False

    @property
    def interface(self) -> str:
        return self._interface

    def isOpened(self) -> bool:  # noqa: N802 - matches cv2.VideoCapture
        return not self._released

    def read(self) -> tuple[bool, Any]:
        if self._released:
            return False, None
        code, payload = self._client.GetImageSample()
        if code != 0 or not payload:
            return False, None
        encoded = self._np.frombuffer(bytes(payload), dtype=self._np.uint8)
        frame = self._cv2.imdecode(encoded, self._cv2.IMREAD_COLOR)
        if frame is None or frame.size == 0:
            return False, None
        return True, frame

    def release(self) -> None:
        # The SDK's ChannelFactory is process-global and has no public close
        # operation. Releasing this adapter stops reads without tearing down a
        # channel that another read-only adapter may still use.
        self._released = True


def open_camera(source: str, source_type: str = "opencv") -> CameraAdapter:
    """Open a configured source without guessing that a URL is RTSP."""

    if source_type == "go2_ros2":
        return Go2Ros2FrontVideoAdapter(source or "/frontvideostream")
    if source_type == "unitree_sdk2":
        return Go2Sdk2FrontVideoAdapter(source)
    if source_type != "opencv":
        raise ValueError(f"unsupported camera source type: {source_type}")
    return OpenCVCameraAdapter(source)
