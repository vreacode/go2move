from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import AgentSettings
from .logs import LogBuffer


@dataclass
class SensorState:
    lidar_online: bool = False
    imu_online: bool = False
    pointcloud_hz: float | None = None
    imu_hz: float | None = None
    point_count: int = 0
    last_frame: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class SensorStreamBuffer:
    def __init__(self, limit: int = 50) -> None:
        self.limit = limit
        self._recent: deque[dict[str, Any]] = deque(maxlen=limit)
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def push(self, payload: dict[str, Any]) -> None:
        self._recent.append(payload)
        for queue in list(self._subscribers):
            with suppress(asyncio.QueueFull):
                queue.put_nowait(payload)

    def recent(self) -> list[dict[str, Any]]:
        return list(self._recent)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)


class SensorBridgeManager:
    def __init__(self, settings: AgentSettings, log_buffer: LogBuffer) -> None:
        self.settings = settings
        self.log_buffer = log_buffer
        self.state = SensorState()
        self.stream = SensorStreamBuffer(settings.sensor_buffer_size)
        self.process: asyncio.subprocess.Process | None = None
        self._stdout_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._wait_task: asyncio.Task[None] | None = None

    def _setup_commands(self) -> list[str]:
        commands: list[str] = []
        if self.settings.ros2_lidar_setup_bash:
            commands.append(f"source {shlex.quote(self.settings.ros2_lidar_setup_bash)}")
        for raw in self.settings.ros2_imu_setup_bash.split(";"):
            path = raw.strip()
            if path:
                commands.append(f"source {shlex.quote(path)}")
        return commands

    def _bridge_command(self) -> str:
        module_path = Path(__file__).with_name("sensor_bridge.py")
        parts = self._setup_commands()
        parts.append(f"export PYTHONUNBUFFERED=1")
        parts.append(
            " ".join(
                [
                    shlex.quote(self.settings.ros2_bridge_python_bin),
                    shlex.quote(str(module_path)),
                    "--lidar-topic",
                    shlex.quote(self.settings.lidar_point_topic),
                    "--imu-topic",
                    shlex.quote(self.settings.imu_topic),
                    "--max-points",
                    str(self.settings.pointcloud_max_points),
                    "--send-hz",
                    str(self.settings.pointcloud_send_hz),
                    "--keep-seconds",
                    str(self.settings.pointcloud_keep_seconds),
                    "--lidar-enabled",
                    "true" if self.settings.lidar_enabled else "false",
                    "--imu-enabled",
                    "true" if self.settings.imu_enabled else "false",
                    "--lidar-to-base-translation",
                    shlex.quote(self.settings.lidar_to_base_translation),
                    "--lidar-to-base-rotation-rpy",
                    shlex.quote(self.settings.lidar_to_base_rotation_rpy),
                    "--imu-to-base-rotation-rpy",
                    shlex.quote(self.settings.imu_to_base_rotation_rpy),
                    "--robot-pose-enabled",
                    "true" if self.settings.robot_pose_enabled else "false",
                    "--robot-pose-map-frame",
                    shlex.quote(self.settings.robot_pose_map_frame),
                    "--robot-pose-base-frame",
                    shlex.quote(self.settings.robot_pose_base_frame),
                    "--robot-pose-fallback-base-frame",
                    shlex.quote(self.settings.robot_pose_fallback_base_frame),
                    "--robot-pose-amcl-topic",
                    shlex.quote(self.settings.robot_pose_amcl_topic),
                ]
            )
        )
        return " && ".join(parts)

    async def start(self) -> None:
        if not self.settings.sensor_bridge_enabled:
            self.log_buffer.add("system", "info", "sensor bridge disabled by config")
            return
        if not (self.settings.lidar_enabled or self.settings.imu_enabled):
            self.log_buffer.add("system", "info", "sensor bridge disabled by config")
            return
        if self.process and self.process.returncode is None:
            return

        command = self._bridge_command()
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        self.process = await asyncio.create_subprocess_exec(
            "/bin/bash",
            "-lc",
            command,
            cwd=str(self.settings.model_workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self.log_buffer.add("system", "info", f"sensor bridge started pid={self.process.pid}")
        self._stdout_task = asyncio.create_task(self._consume_stdout())
        self._stderr_task = asyncio.create_task(self._consume_stderr())
        self._wait_task = asyncio.create_task(self._wait())

    async def _consume_stdout(self) -> None:
        assert self.process is not None
        stream = self.process.stdout
        if stream is None:
            return
        while True:
            try:
                line = await stream.readline()
            except ValueError as exc:
                self.state.error = f"sensor bridge stdout frame too large: {exc}"
                self.log_buffer.add("system", "error", self.state.error)
                return
            if not line:
                return
            raw = line.decode("utf-8", errors="ignore").strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                self.log_buffer.add("system", "warning", f"sensor bridge invalid json: {raw[:200]}")
                continue
            self.stream.push(payload)
            if payload.get("type") == "sensor_frame":
                self.state.last_frame = payload
                self.state.lidar_online = bool(payload.get("lidar_online"))
                self.state.imu_online = bool(payload.get("imu_online"))
                self.state.pointcloud_hz = payload.get("pointcloud_hz")
                self.state.imu_hz = payload.get("imu_hz")
                self.state.point_count = int(payload.get("point_count") or 0)
                self.state.error = payload.get("error")
            elif payload.get("type") == "sensor_error":
                self.state.error = payload.get("message")
                self.log_buffer.add("system", "error", f"sensor bridge: {payload.get('message')}")

    async def _consume_stderr(self) -> None:
        assert self.process is not None
        stream = self.process.stderr
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                return
            self.log_buffer.add("system", "warning", f"sensor bridge stderr: {line.decode('utf-8', errors='ignore').strip()}")

    async def _wait(self) -> None:
        assert self.process is not None
        code = await self.process.wait()
        self.state.error = f"sensor bridge exited with code {code}"
        self.state.lidar_online = False
        self.state.imu_online = False
        self.log_buffer.add("system", "warning", self.state.error)
        self.process = None

    async def stop(self) -> None:
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        for task in [self._stdout_task, self._stderr_task, self._wait_task]:
            if task:
                task.cancel()
                with suppress(Exception):
                    await task
