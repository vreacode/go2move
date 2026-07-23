import asyncio
import unittest

from .config import AgentSettings
from .logs import LogBuffer
from .motion import MotionControlManager


def make_manager() -> MotionControlManager:
    settings = AgentSettings(
        enable_motion_control=False,
        motion_control_mode="dry_run",
        require_manual_arm=True,
        max_linear_vel_safe=0.05,
        max_angular_vel_safe=0.2,
    )
    return MotionControlManager(settings, LogBuffer(50))


class MotionSafetyTests(unittest.TestCase):
    def test_default_manager_is_dry_run_and_locked(self):
        manager = make_manager()
        state = manager.snapshot(navigation_running=False)
        self.assertTrue(manager.dry_run)
        self.assertFalse(state.armed)
        self.assertFalse(state.real_publish_enabled)

    def test_unarmed_motion_is_rejected_without_ros_publisher(self):
        manager = make_manager()
        with self.assertRaisesRegex(RuntimeError, "arm it first"):
            asyncio.run(manager.send_control("forward", "low", navigation_running=False))
        self.assertIsNone(manager._publisher)

    def test_dry_run_command_records_bounded_twist(self):
        manager = make_manager()
        asyncio.run(manager.set_armed(True))
        state = asyncio.run(manager.send_control("left", "low", navigation_running=False))
        self.assertIsNotNone(state.last_command)
        assert state.last_command is not None
        self.assertTrue(state.last_command.dry_run)
        self.assertEqual(state.last_command.linear_x, 0.03)
        self.assertEqual(state.last_command.angular_z, 0.07)
        self.assertIsNone(manager._publisher)

    def test_navigation_lockout_rejects_manual_command(self):
        manager = make_manager()
        asyncio.run(manager.set_armed(True))
        with self.assertRaisesRegex(RuntimeError, "navigation is running"):
            asyncio.run(manager.send_control("forward", "low", navigation_running=True))

    def test_real_mode_requires_ready_base(self):
        manager = make_manager()
        with self.assertRaisesRegex(RuntimeError, "base is not ready"):
            asyncio.run(manager.set_mode("real", base_ready=False))
        self.assertTrue(manager.dry_run)

    def test_cruise_keeps_forward_motion_and_accepts_steering(self):
        manager = make_manager()

        async def scenario():
            await manager.set_armed(True)
            started = await manager.start_cruise("medium", navigation_running=False)
            self.assertTrue(started.cruise_active)
            self.assertEqual(started.cruise_steering, "center")
            self.assertEqual(started.last_command.command, "cruise_forward")
            turning = await manager.set_cruise_steering("left", navigation_running=False)
            self.assertEqual(turning.last_command.command, "cruise_left")
            self.assertGreater(turning.last_command.linear_x, 0.0)
            self.assertGreater(turning.last_command.angular_z, 0.0)
            await manager.cruise_heartbeat(navigation_running=False)
            stopped = await manager.stop_cruise()
            self.assertFalse(stopped.cruise_active)
            self.assertEqual(stopped.last_command.command, "stop")

        asyncio.run(scenario())

    def test_cruise_stops_when_browser_heartbeat_disappears(self):
        manager = make_manager()

        async def scenario():
            await manager.set_armed(True)
            await manager.start_cruise("low", navigation_running=False)
            await asyncio.sleep(0.85)
            self.assertFalse(manager.snapshot(navigation_running=False).cruise_active)
            self.assertEqual(manager.last_command.command, "stop")

        asyncio.run(scenario())

    def test_mobile_cruise_auto_arm_relocks_after_stop(self):
        manager = make_manager()

        async def scenario():
            state = await manager.start_cruise("low", navigation_running=False, auto_arm=True)
            self.assertTrue(state.armed)
            self.assertTrue(state.cruise_active)
            stopped = await manager.stop_cruise()
            self.assertFalse(stopped.armed)
            self.assertFalse(stopped.cruise_active)

        asyncio.run(scenario())

    def test_mobile_cruise_auto_arm_does_not_bypass_navigation_lockout(self):
        manager = make_manager()

        with self.assertRaisesRegex(RuntimeError, "navigation is running"):
            asyncio.run(manager.start_cruise("low", navigation_running=True, auto_arm=True))
        self.assertFalse(manager.armed)


if __name__ == "__main__":
    unittest.main()
