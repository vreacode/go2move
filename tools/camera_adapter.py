"""Camera source adapters used by the data-only capture pipeline.

OpenCV handles local files, USB camera indexes, and verified network URLs.  The
Go2 ROS 2 adapter is intentionally opt-in: it subscribes to the official
``/frontvideostream`` message and decodes the JPEG bytes published in
``video720p``/``video360p``/``video180p``.  No SDK control client or motion API
is imported here.
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


def open_camera(source: str, source_type: str = "opencv") -> CameraAdapter:
    """Open a configured source without guessing that a URL is RTSP."""

    if source_type == "go2_ros2":
        return Go2Ros2FrontVideoAdapter(source or "/frontvideostream")
    if source_type != "opencv":
        raise ValueError(f"unsupported camera source type: {source_type}")
    return OpenCVCameraAdapter(source)
