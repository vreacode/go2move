from __future__ import annotations

import asyncio
import json
import os
import shlex
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import AgentSettings
from .logs import LogBuffer


@dataclass
class PoseState:
    last_frame: dict[str, Any] = field(default_factory=dict)
    pose: dict[str, Any] | None = None
    source: str | None = None
    error: str | None = None


class PoseStreamBuffer:
    def __init__(self, limit: int = 50) -> None:
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


class RobotPoseManager:
    def __init__(self, settings: AgentSettings, log_buffer: LogBuffer) -> None:
        self.settings = settings
        self.log_buffer = log_buffer
        self.state = PoseState()
        self.stream = PoseStreamBuffer(settings.sensor_buffer_size)
        self.process: asyncio.subprocess.Process | None = None
        self._stdout_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._wait_task: asyncio.Task[None] | None = None

    def _bridge_command(self) -> str:
        module_path = Path(__file__).with_name("pose_bridge.py")
        parts = [
            "source /opt/ros/humble/setup.bash",
            "export PYTHONUNBUFFERED=1",
            " ".join(
                [
                    shlex.quote(self.settings.ros2_bridge_python_bin),
                    shlex.quote(str(module_path)),
                    "--map-frame",
                    shlex.quote(self.settings.robot_pose_map_frame),
                    "--base-frame",
                    shlex.quote(self.settings.robot_pose_base_frame),
                    "--fallback-base-frame",
                    shlex.quote(self.settings.robot_pose_fallback_base_frame),
                    "--amcl-topic",
                    shlex.quote(self.settings.robot_pose_amcl_topic),
                    "--send-hz",
                    "5.0",
                    "--keep-seconds",
                    "2.0",
                ]
            ),
        ]
        return " && ".join(parts)

    async def start(self) -> None:
        if not self.settings.robot_pose_enabled:
            self.log_buffer.add("system", "info", "robot pose bridge disabled by config")
            return
        if self.process and self.process.returncode is None:
            return
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        self.process = await asyncio.create_subprocess_exec(
            "/bin/bash",
            "-lc",
            self._bridge_command(),
            cwd=str(self.settings.model_workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self.log_buffer.add("system", "info", f"pose bridge started pid={self.process.pid}")
        self._stdout_task = asyncio.create_task(self._consume_stdout())
        self._stderr_task = asyncio.create_task(self._consume_stderr())
        self._wait_task = asyncio.create_task(self._wait())

    async def _consume_stdout(self) -> None:
        assert self.process is not None
        stream = self.process.stdout
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                return
            raw = line.decode("utf-8", errors="ignore").strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                self.log_buffer.add("system", "warning", f"pose bridge invalid json: {raw[:200]}")
                continue
            self.stream.push(payload)
            if payload.get("type") == "pose_frame":
                self.state.last_frame = payload
                self.state.pose = payload.get("pose")
                self.state.source = payload.get("pose_source")
                self.state.error = payload.get("error")
            elif payload.get("type") == "pose_error":
                self.state.error = payload.get("message")
                self.log_buffer.add("system", "warning", f"pose bridge: {self.state.error}")

    async def _consume_stderr(self) -> None:
        assert self.process is not None
        stream = self.process.stderr
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                return
            self.log_buffer.add("system", "warning", f"pose bridge stderr: {line.decode('utf-8', errors='ignore').strip()}")

    async def _wait(self) -> None:
        assert self.process is not None
        code = await self.process.wait()
        self.state.error = f"pose bridge exited with code {code}"
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
