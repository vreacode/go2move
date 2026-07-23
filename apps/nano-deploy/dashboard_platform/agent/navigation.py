from __future__ import annotations

import asyncio
import json
import math
import os
import shlex
import signal
from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import suppress
from typing import Any

from .config import AgentSettings
from .logs import LogBuffer


@dataclass
class NavigationState:
    navigation_running: bool = False
    goal_status: str = "idle"
    goal: dict[str, Any] | None = None
    last_result: str | None = None
    last_error: str | None = None
    updated_at: str | None = None

    def model_dump(self) -> dict[str, Any]:
        return {
            "navigation_running": self.navigation_running,
            "goal_status": self.goal_status,
            "goal": self.goal,
            "last_result": self.last_result,
            "last_error": self.last_error,
            "updated_at": self.updated_at,
        }


class NavigationManager:
    """Small ROS CLI bridge for explicit initial-pose and Nav2 goal actions."""

    def __init__(self, settings: AgentSettings, log_buffer: LogBuffer) -> None:
        self.settings = settings
        self.log_buffer = log_buffer
        self.state = NavigationState()
        self._goal_process: asyncio.subprocess.Process | None = None
        self._goal_watch: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    def _number(value: float, label: str) -> float:
        if not math.isfinite(value):
            raise ValueError(f"{label} must be finite")
        return float(value)

    @staticmethod
    def _quaternion_z_w(yaw: float) -> tuple[float, float]:
        return math.sin(yaw / 2.0), math.cos(yaw / 2.0)

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _ros_shell(self, command: str) -> str:
        setup = "source /opt/ros/humble/setup.bash"
        return f"{setup} && {command}"

    async def _run_once(self, command: str, name: str) -> tuple[str, str]:
        process = await asyncio.create_subprocess_exec(
            "/bin/bash",
            "-lc",
            self._ros_shell(command),
            cwd=str(self.settings.model_workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        stdout, stderr = await process.communicate()
        out = stdout.decode("utf-8", errors="ignore").strip() if stdout else ""
        err = stderr.decode("utf-8", errors="ignore").strip() if stderr else ""
        if out:
            self.log_buffer.add("control", "info", f"{name}: {out[-1000:]}")
        if err:
            self.log_buffer.add("control", "warning", f"{name}: {err[-1000:]}")
        if process.returncode != 0:
            raise RuntimeError(err or out or f"{name} exited with code {process.returncode}")
        return out, err

    async def publish_initial_pose(self, x: float, y: float, yaw: float) -> dict[str, Any]:
        x = self._number(x, "x")
        y = self._number(y, "y")
        yaw = self._number(yaw, "yaw")
        qz, qw = self._quaternion_z_w(yaw)
        message = json.dumps({
            "header": {"frame_id": "map"},
            "pose": {
                "pose": {
                    "position": {"x": x, "y": y, "z": 0.0},
                    "orientation": {"x": 0.0, "y": 0.0, "z": qz, "w": qw},
                },
                "covariance": [0.0] * 36,
            },
        })
        command = "ros2 topic pub --once /initialpose geometry_msgs/msg/PoseWithCovarianceStamped " + shlex.quote(message)
        await self._run_once(command, "initial_pose")
        payload = {"x": x, "y": y, "yaw": yaw, "frame_id": "map"}
        self.log_buffer.add("control", "info", f"initial pose published: {payload}")
        return {"status": "acked", "pose": payload}

    async def send_goal(self, x: float, y: float, yaw: float) -> dict[str, Any]:
        x = self._number(x, "x")
        y = self._number(y, "y")
        yaw = self._number(yaw, "yaw")
        async with self._lock:
            if self._goal_process and self._goal_process.returncode is None:
                raise RuntimeError("a navigation goal is already active")
            qz, qw = self._quaternion_z_w(yaw)
            message = json.dumps({
                "pose": {
                    "header": {"frame_id": "map"},
                    "pose": {
                        "position": {"x": x, "y": y, "z": 0.0},
                        "orientation": {"x": 0.0, "y": 0.0, "z": qz, "w": qw},
                    },
                },
            })
            command = (
                "ros2 action send_goal /navigate_to_pose nav2_msgs/action/NavigateToPose "
                f"{shlex.quote(message)}"
            )
            self._goal_process = await asyncio.create_subprocess_exec(
                "/bin/bash",
                "-lc",
                self._ros_shell(f"exec {command}"),
                cwd=str(self.settings.model_workdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
                start_new_session=True,
            )
            self.state = NavigationState(
                navigation_running=True,
                goal_status="sent",
                goal={"x": x, "y": y, "yaw": yaw, "frame_id": "map"},
                updated_at=self._timestamp(),
            )
            self._goal_watch = asyncio.create_task(self._watch_goal(self._goal_process))
            self.log_buffer.add("control", "info", f"navigation goal sent: {self.state.goal}")
            return {"status": "sent", **self.state.model_dump()}

    async def _watch_goal(self, process: asyncio.subprocess.Process) -> None:
        stdout, stderr = await process.communicate()
        out = stdout.decode("utf-8", errors="ignore").strip() if stdout else ""
        err = stderr.decode("utf-8", errors="ignore").strip() if stderr else ""
        combined = f"{out}\n{err}".strip()
        if combined:
            self.log_buffer.add("control", "info" if process.returncode == 0 else "warning", f"navigate_to_pose: {combined[-1500:]}")
        if process.returncode == 0 and "SUCCEEDED" in combined.upper():
            status = "succeeded"
            result = "SUCCEEDED"
        elif process.returncode == 0:
            status = "completed"
            result = combined[-500:] or "completed"
        else:
            status = "failed"
            result = combined[-500:] or f"exit code {process.returncode}"
        self.state.goal_status = status
        self.state.last_result = result
        self.state.last_error = None if status in {"succeeded", "completed"} else result
        self.state.updated_at = self._timestamp()
        self._goal_process = None

    async def cancel_goal(self) -> dict[str, Any]:
        process = self._goal_process
        if not process or process.returncode is not None:
            self.state.goal_status = "idle"
            self.state.updated_at = self._timestamp()
            return {"status": "idle", **self.state.model_dump()}
        process.send_signal(signal.SIGINT)
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        if self._goal_watch:
            await self._goal_watch
        self.state.goal_status = "cancelled"
        self.state.last_result = "cancelled by operator"
        self.state.updated_at = self._timestamp()
        return {"status": "cancelled", **self.state.model_dump()}

    def snapshot(self) -> dict[str, Any]:
        return self.state.model_dump()

    async def stop(self) -> None:
        if self._goal_process and self._goal_process.returncode is None:
            await self.cancel_goal()
        if self._goal_watch:
            self._goal_watch.cancel()
            with suppress(asyncio.CancelledError):
                await self._goal_watch
