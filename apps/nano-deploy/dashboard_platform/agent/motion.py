from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from typing import Literal

from .config import AgentSettings
from .logs import LogBuffer
from .schemas import MotionLastCommand, MotionState


SpeedLevel = Literal["low", "medium", "high"]
ControlCommand = Literal["forward", "backward", "left", "right", "stop"]
CruiseSteering = Literal["center", "left", "right"]


class MotionControlManager:
    def __init__(self, settings: AgentSettings, log_buffer: LogBuffer) -> None:
        self.settings = settings
        self.log_buffer = log_buffer
        self.armed = not settings.require_manual_arm
        self.manual_override = False
        self._mode: Literal["dry_run", "real"] = "real" if settings.motion_real_enabled else "dry_run"
        self.last_command: MotionLastCommand | None = None
        self.last_error: str | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._cruise_task: asyncio.Task[None] | None = None
        self._cruise_speed: SpeedLevel | None = None
        self._cruise_steering: CruiseSteering = "center"
        self._cruise_last_heartbeat = 0.0
        self._cruise_auto_armed = False
        self._last_nonzero_sent_at = 0.0
        self._rclpy = None
        self._node = None
        self._publisher = None

    async def start(self) -> None:
        if self._watchdog_task is None:
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        if self._mode == "real":
            self._ensure_ros_publisher()
        self.log_buffer.add(
            "control",
            "info",
            f"motion control ready mode={self._mode} topic={self.settings.cmd_vel_topic} armed={self.armed}",
        )

    async def stop(self) -> None:
        if self.cruise_active:
            await self.stop_cruise(emergency=True)
        if self._watchdog_task:
            self._watchdog_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._watchdog_task
            self._watchdog_task = None
        with suppress(Exception):
            await self._publish_stop_burst(real_only=True)
        if self._node is not None:
            with suppress(Exception):
                self._node.destroy_node()
            self._node = None
        if self._rclpy is not None:
            with suppress(Exception):
                if self._rclpy.ok():
                    self._rclpy.shutdown()
            self._rclpy = None

    @property
    def dry_run(self) -> bool:
        return self._mode != "real"

    @property
    def cruise_active(self) -> bool:
        return self._cruise_speed is not None

    def snapshot(self, navigation_running: bool) -> MotionState:
        return MotionState(
            armed=self.armed,
            mode=self._mode,
            real_publish_enabled=self._mode == "real",
            manual_override=self.manual_override,
            nav_lockout=bool(navigation_running and self.settings.disable_manual_when_nav_running and not self.manual_override),
            require_manual_arm=self.settings.require_manual_arm,
            cmd_vel_topic=self.settings.cmd_vel_topic,
            timeout_ms=self.settings.control_command_timeout_ms,
            max_linear_vel_safe=self.settings.max_linear_vel_safe,
            max_angular_vel_safe=self.settings.max_angular_vel_safe,
            cruise_active=self.cruise_active,
            cruise_speed=self._cruise_speed,
            cruise_steering=self._cruise_steering,
            last_command=self.last_command,
            last_error=self.last_error,
        )

    async def set_mode(self, mode: Literal["dry_run", "real"], base_ready: bool) -> MotionState:
        if mode == "real" and not base_ready:
            self.last_error = "base is not ready; start chassis bringup before enabling real mode"
            raise RuntimeError(self.last_error)
        if mode == "real":
            self._mode = "real"
            self._ensure_ros_publisher()
            self.last_error = None
            self.log_buffer.add("control", "warning", f"motion mode switched to real on {self.settings.cmd_vel_topic}")
        else:
            if self.cruise_active:
                await self.stop_cruise()
            if not self.dry_run:
                await self._publish_stop_burst(real_only=False)
            self._mode = "dry_run"
            self.armed = not self.settings.require_manual_arm
            self.manual_override = False
            self.last_error = None
            self.log_buffer.add("control", "warning", "motion mode switched to dry-run and control relocked")
        return self.snapshot(navigation_running=False)

    async def force_safe_lock(self, reason: str) -> MotionState:
        if self.cruise_active:
            await self.stop_cruise(emergency=True)
        elif not self.dry_run:
            await self._publish_stop_burst(real_only=False, emergency=True)
        self._mode = "dry_run"
        self.armed = not self.settings.require_manual_arm
        self.manual_override = False
        self.last_error = reason
        self.log_buffer.add("control", "error", reason)
        return self.snapshot(navigation_running=False)

    async def set_armed(self, armed: bool) -> MotionState:
        self.armed = armed or not self.settings.require_manual_arm
        self.last_error = None
        if not self.armed:
            if self.cruise_active:
                await self.stop_cruise()
            await self._publish_stop_burst(real_only=False)
            self.manual_override = False
        self.log_buffer.add("control", "warning", f"manual control {'armed' if self.armed else 'locked'}")
        return self.snapshot(navigation_running=False)

    async def set_manual_override(self, enabled: bool, navigation_running: bool) -> MotionState:
        if enabled and self.settings.require_manual_arm and not self.armed:
            raise RuntimeError("manual control is locked; arm it before enabling override")
        self.manual_override = enabled
        self.last_error = None
        self.log_buffer.add(
            "control",
            "warning",
            f"manual override {'enabled' if enabled else 'disabled'} while navigation_running={navigation_running}",
        )
        return self.snapshot(navigation_running=navigation_running)

    async def emergency_stop(self) -> MotionState:
        self.last_error = None
        if self.cruise_active:
            await self.stop_cruise(emergency=True)
        else:
            await self._publish_stop_burst(real_only=False, emergency=True)
        return self.snapshot(navigation_running=False)

    async def send_control(self, command: ControlCommand, speed: SpeedLevel, navigation_running: bool) -> MotionState:
        if command != "stop":
            if self.settings.require_manual_arm and not self.armed:
                self.last_error = "manual control is locked; arm it first"
                raise RuntimeError(self.last_error)
            if navigation_running and self.settings.disable_manual_when_nav_running and not self.manual_override:
                self.last_error = "manual control is locked while navigation is running; enable manual override first"
                raise RuntimeError(self.last_error)

        if self.cruise_active:
            if command == "stop":
                return await self.stop_cruise(navigation_running=navigation_running)
            await self.stop_cruise(navigation_running=navigation_running)

        linear_x, angular_z = self._command_to_twist(command, speed)
        await self._dispatch(command, speed, linear_x, angular_z)
        return self.snapshot(navigation_running=navigation_running)

    async def start_cruise(self, speed: SpeedLevel, navigation_running: bool, auto_arm: bool = False) -> MotionState:
        if navigation_running and self.settings.disable_manual_when_nav_running and not self.manual_override:
            self.last_error = "manual control is locked while navigation is running; enable manual override first"
            raise RuntimeError(self.last_error)
        if self.settings.require_manual_arm and not self.armed:
            if not auto_arm:
                self.last_error = "manual control is locked; arm it first"
                raise RuntimeError(self.last_error)
            self.armed = True
            self._cruise_auto_armed = True
            self.log_buffer.add("control", "info", "cruise start auto-armed manual control")
        else:
            self._cruise_auto_armed = False
        self._validate_manual_motion(navigation_running)
        self._cruise_speed = speed
        self._cruise_steering = "center"
        self._cruise_last_heartbeat = asyncio.get_running_loop().time()
        if self._cruise_task is None:
            self._cruise_task = asyncio.create_task(self._cruise_loop())
        await self._dispatch_cruise(log=True)
        self.log_buffer.add("control", "info", f"cruise forward started speed={speed}")
        return self.snapshot(navigation_running=navigation_running)

    async def set_cruise_steering(self, direction: CruiseSteering, navigation_running: bool) -> MotionState:
        self._validate_manual_motion(navigation_running)
        if not self.cruise_active:
            self.last_error = "cruise mode is not active"
            raise RuntimeError(self.last_error)
        self._cruise_steering = direction
        self._cruise_last_heartbeat = asyncio.get_running_loop().time()
        await self._dispatch_cruise(log=True)
        return self.snapshot(navigation_running=navigation_running)

    async def cruise_heartbeat(self, navigation_running: bool) -> MotionState:
        if self.cruise_active:
            self._validate_manual_motion(navigation_running)
            self._cruise_last_heartbeat = asyncio.get_running_loop().time()
        return self.snapshot(navigation_running=navigation_running)

    async def stop_cruise(self, navigation_running: bool = False, emergency: bool = False) -> MotionState:
        task = self._cruise_task
        auto_armed = self._cruise_auto_armed
        self._cruise_task = None
        self._cruise_speed = None
        self._cruise_steering = "center"
        self._cruise_last_heartbeat = 0.0
        self._cruise_auto_armed = False
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        await self._publish_stop_burst(real_only=False, emergency=emergency)
        if auto_armed:
            self.armed = False
            self.manual_override = False
        return self.snapshot(navigation_running=navigation_running)

    async def stop_cruise_if_active(self, navigation_running: bool = False) -> MotionState:
        if self.cruise_active:
            return await self.stop_cruise(navigation_running=navigation_running)
        return self.snapshot(navigation_running=navigation_running)

    def _validate_manual_motion(self, navigation_running: bool) -> None:
        if self.settings.require_manual_arm and not self.armed:
            self.last_error = "manual control is locked; arm it first"
            raise RuntimeError(self.last_error)
        if navigation_running and self.settings.disable_manual_when_nav_running and not self.manual_override:
            self.last_error = "manual control is locked while navigation is running; enable manual override first"
            raise RuntimeError(self.last_error)

    def _command_to_twist(self, command: ControlCommand, speed: SpeedLevel) -> tuple[float, float]:
        scale_map = {"low": 0.35, "medium": 0.65, "high": 1.0}
        scale = scale_map[speed]
        linear = round(self.settings.max_linear_vel_safe * scale, 4)
        angular = round(self.settings.max_angular_vel_safe * scale, 4)
        if speed == "low":
            linear = max(linear, 0.03)
        if command == "forward":
            return linear, 0.0
        if command == "backward":
            return -linear, 0.0
        if command == "left":
            return linear, angular
        if command == "right":
            return linear, -angular
        return 0.0, 0.0

    async def _dispatch(self, command: str, speed: str, linear_x: float, angular_z: float, log: bool = True) -> None:
        self.last_error = None
        dry_run = self.dry_run
        if dry_run:
            if log:
                self.log_buffer.add(
                    "control",
                    "info",
                    f"dry-run {command} speed={speed} topic={self.settings.cmd_vel_topic} linear_x={linear_x:.4f} angular_z={angular_z:.4f}",
                )
        else:
            self._ensure_ros_publisher()
            self._publish_twist(linear_x, angular_z)
            if log:
                self.log_buffer.add(
                    "control",
                    "info",
                    f"published {command} speed={speed} topic={self.settings.cmd_vel_topic} linear_x={linear_x:.4f} angular_z={angular_z:.4f}",
                )

        self.last_command = MotionLastCommand(
            command=command,
            speed=speed,
            linear_x=linear_x,
            angular_z=angular_z,
            dry_run=dry_run,
            topic=self.settings.cmd_vel_topic,
            status="acked",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._last_nonzero_sent_at = asyncio.get_running_loop().time() if (linear_x or angular_z) else 0.0

    async def _dispatch_cruise(self, log: bool) -> None:
        if self._cruise_speed is None:
            return
        command = "forward" if self._cruise_steering == "center" else self._cruise_steering
        label = {
            "center": "cruise_forward",
            "left": "cruise_left",
            "right": "cruise_right",
        }[self._cruise_steering]
        linear_x, angular_z = self._command_to_twist(command, self._cruise_speed)
        await self._dispatch(label, self._cruise_speed, linear_x, angular_z, log=log)

    async def _cruise_loop(self) -> None:
        heartbeat_timeout = max(0.75, self.settings.control_command_timeout_ms / 1000.0 * 2.5)
        try:
            while self.cruise_active:
                await asyncio.sleep(0.1)
                if not self.cruise_active:
                    return
                now = asyncio.get_running_loop().time()
                if now - self._cruise_last_heartbeat > heartbeat_timeout:
                    auto_armed = self._cruise_auto_armed
                    self._cruise_speed = None
                    self._cruise_steering = "center"
                    self._cruise_last_heartbeat = 0.0
                    self._cruise_task = None
                    self._cruise_auto_armed = False
                    await self._publish_stop_burst(real_only=False)
                    if auto_armed:
                        self.armed = False
                        self.manual_override = False
                    self.last_error = "cruise heartbeat timeout; stopped safely"
                    self.log_buffer.add("control", "warning", "cruise heartbeat timeout -> stop")
                    return
                await self._dispatch_cruise(log=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            auto_armed = self._cruise_auto_armed
            self._cruise_speed = None
            self._cruise_steering = "center"
            self._cruise_last_heartbeat = 0.0
            self._cruise_task = None
            self._cruise_auto_armed = False
            with suppress(Exception):
                await self._publish_stop_burst(real_only=False, emergency=True)
            if auto_armed:
                self.armed = False
                self.manual_override = False
            self.last_error = f"cruise loop failed: {exc}"
            self.log_buffer.add("control", "error", self.last_error)

    async def _publish_stop_burst(self, real_only: bool, emergency: bool = False) -> None:
        command = "emergency_stop" if emergency else "stop"
        dry_run = self.dry_run
        if not real_only:
            self.last_command = MotionLastCommand(
                command=command,
                speed="low",
                linear_x=0.0,
                angular_z=0.0,
                dry_run=dry_run,
                topic=self.settings.cmd_vel_topic,
                status="acked",
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        if dry_run:
            self.log_buffer.add("control", "warning", f"{command} dry-run stop burst topic={self.settings.cmd_vel_topic}")
            self._last_nonzero_sent_at = 0.0
            return
        self._ensure_ros_publisher()
        for _ in range(3 if emergency else 2):
            self._publish_twist(0.0, 0.0)
            await asyncio.sleep(0.08)
        self.log_buffer.add("control", "warning", f"{command} published stop burst topic={self.settings.cmd_vel_topic}")
        self._last_nonzero_sent_at = 0.0

    async def _watchdog_loop(self) -> None:
        interval = max(0.05, self.settings.control_command_timeout_ms / 3000.0)
        while True:
            await asyncio.sleep(interval)
            if self.dry_run or self._last_nonzero_sent_at <= 0:
                continue
            elapsed_ms = (asyncio.get_running_loop().time() - self._last_nonzero_sent_at) * 1000.0
            if elapsed_ms < self.settings.control_command_timeout_ms:
                continue
            self._publish_twist(0.0, 0.0)
            self.log_buffer.add(
                "control",
                "warning",
                f"motion watchdog timeout -> stop after {elapsed_ms:.0f} ms on {self.settings.cmd_vel_topic}",
            )
            self._last_nonzero_sent_at = 0.0

    def _ensure_ros_publisher(self) -> None:
        if self._publisher is not None:
            return
        import rclpy
        from geometry_msgs.msg import Twist

        if not rclpy.ok():
            rclpy.init(args=None)
        node = rclpy.create_node("dashboard_motion_bridge")
        publisher = node.create_publisher(Twist, self.settings.cmd_vel_topic, 10)
        self._rclpy = rclpy
        self._node = node
        self._publisher = publisher

    def _publish_twist(self, linear_x: float, angular_z: float) -> None:
        from geometry_msgs.msg import Twist

        if self._publisher is None:
            raise RuntimeError("motion publisher is not initialized")
        msg = Twist()
        msg.linear.x = float(linear_x)
        msg.linear.y = 0.0
        msg.linear.z = 0.0
        msg.angular.x = 0.0
        msg.angular.y = 0.0
        msg.angular.z = float(angular_z)
        self._publisher.publish(msg)
