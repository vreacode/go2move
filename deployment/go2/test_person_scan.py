#!/usr/bin/env python3
"""Offline scan safety checks. No DDS, SDK, robot, audio, or subprocess calls."""
import asyncio
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import person_scan as product


class FakeMotion:
    """Advance a test angle on fake commands; this is not physical validation."""
    def __init__(self):
        self.commands = []
        self.angle = 0.
        self.opened = self.closed = 0
        self.observer = None
        self.stop_event = None
        self.fail_open = self.fail_close = False
        self.fail_command = False
        self.stale_snapshot = self.not_ready = self.latched = False
        self.wrong_direction = False
        self.cancel_on_positive = False

    async def open(self):
        self.opened += 1
        if self.fail_open:
            raise RuntimeError('fake open failure')

    async def command(self, yaw):
        self.commands.append(yaw)
        if self.fail_command:
            raise RuntimeError('fake command failure')
        if yaw > 0:
            self.angle += -4. if self.wrong_direction else 4.
            if self.cancel_on_positive:
                self.stop_event.set()
        if self.observer:
            self.observer.advance(yaw)

    def snapshot(self):
        return {'worker_ready': not self.not_ready, 'latched_stop': self.latched,
                'guard_reason': 'fake protection' if self.latched else '',
                'at': time.monotonic()-(1 if self.stale_snapshot else 0),
                'rotation': {'angle_deg': abs(self.angle),
                             'net_angle_deg': self.angle, 'displacement_m': 0.}}

    async def close(self):
        self.closed += 1
        if self.fail_close:
            raise RuntimeError('fake close failure')


class FakeObserver:
    def __init__(self, motion, mode='fresh', match_stage=None):
        self.motion, self.mode, self.match_stage = motion, mode, match_stage
        self.active = self.connected = True
        self.target_id, self.error = 'target-a', None
        self.stage = self.frames = 0
        self.last = None
        self.fixed_stamp = None
        self.fail_on_rotation = None
        motion.observer = self
        self.advance(0.)

    def reset_confirmation(self):
        self.stage += 1
        self.last = None
        self.fixed_stamp = time.time()

    def advance(self, yaw):
        self.frames += 1
        if yaw > 0 and self.fail_on_rotation:
            if self.fail_on_rotation == 'target':
                self.target_id = 'target-b'
            elif self.fail_on_rotation == 'camera':
                self.connected = False
            elif self.fail_on_rotation == 'error':
                self.error = 'fake inference failure'
            elif self.fail_on_rotation == 'missing':
                self.mode = 'missing'
            elif self.fail_on_rotation == 'stale':
                self.mode = 'stale'
        if self.mode == 'missing':
            self.last = None
            return
        now = time.time()
        stamp = now
        if self.mode == 'stale':
            stamp -= 3
        elif self.mode == 'future':
            stamp += 3
        elif self.mode == 'repeat' and self.stage:
            stamp = self.fixed_stamp
        matched = self.match_stage is not None and self.stage >= self.match_stage
        track = self.frames % 2 if self.mode == 'changing-track' else 7
        self.last = {'target_id': 'foreign' if self.mode == 'foreign' else self.target_id,
                     'captured_at': stamp, 'suspected_target': matched,
                     'streak': 3 if matched else 0,
                     'candidates': [{'track_id': track}] if matched else []}

    def __call__(self):
        return {'active': self.active, 'target': {'id': self.target_id},
                'camera': {'connected': self.connected}, 'error': self.error,
                'last': self.last}


class HardwareForbidden(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.process_guard = patch.object(product.asyncio, 'create_subprocess_exec',
            new=AsyncMock(side_effect=AssertionError('real subprocess forbidden in offline test')))
        self.sdk_guard = patch.object(product.OfficialRotationProcess, '__init__',
            side_effect=AssertionError('real SDK factory forbidden in offline test'))
        self.process_mock = self.process_guard.start()
        self.sdk_mock = self.sdk_guard.start()

    async def asyncTearDown(self):
        self.process_mock.assert_not_called()
        self.sdk_mock.assert_not_called()
        self.sdk_guard.stop()
        self.process_guard.stop()


class ScanRunnerTests(HardwareForbidden):
    def make_runner(self, mode='fresh', match_stage=None, **parameters):
        motion = FakeMotion()
        observer = FakeObserver(motion, mode, match_stage)
        updates = []
        stop = asyncio.Event()
        motion.stop_event = stop
        options = dict(observation_seconds=.025, settle_seconds=0,
                       tick_seconds=.001, max_seconds=3)
        options.update(parameters)
        runner = product.ScanRunner(motion, observer, 'target-a',
            lambda **fields: updates.append(fields), stop, **options)
        return runner, motion, observer, updates

    async def assert_terminal(self, runner, motion, state):
        result = await runner.run()
        self.assertEqual(state, result['state'], result)
        self.assertEqual(1, motion.opened)
        self.assertEqual(1, motion.closed)
        self.assertFalse(result['motion_output_enabled'])
        return result

    async def test_initial_fresh_match_needs_no_turn(self):
        runner, motion, observer, updates = self.make_runner(match_stage=1)
        result = await self.assert_terminal(runner, motion, 'found')
        self.assertTrue(result['found'])
        self.assertFalse(result['commanded_motion'])
        self.assertTrue(motion.commands)
        self.assertTrue(all(yaw == 0 for yaw in motion.commands))
        self.assertEqual(1, observer.stage)
        self.assertEqual('found', updates[-1]['state'])

    async def test_new_match_after_first_segment_stops_scan(self):
        runner, motion, observer, updates = self.make_runner(match_stage=2)
        result = await self.assert_terminal(runner, motion, 'found')
        self.assertTrue(result['commanded_motion'])
        self.assertEqual(2, observer.stage)
        self.assertGreaterEqual(motion.angle, 20)
        self.assertLess(motion.angle, 40)
        self.assertEqual(0., motion.commands[-1])
        self.assertTrue(all(0 <= yaw <= .15 for yaw in motion.commands))

    async def test_no_match_finishes_bounded_four_segments(self):
        runner, motion, observer, _ = self.make_runner()
        result = await self.assert_terminal(runner, motion, 'completed')
        self.assertFalse(result['found'])
        self.assertEqual(5, observer.stage)
        self.assertGreaterEqual(motion.angle, 80)
        self.assertLessEqual(motion.angle, 85)
        self.assertEqual(0., motion.commands[-1])

    async def test_repeated_timestamp_never_confirms(self):
        runner, motion, _, _ = self.make_runner(mode='repeat', match_stage=1)
        result = await self.assert_terminal(runner, motion, 'completed')
        self.assertFalse(result['found'])

    async def test_track_changes_never_confirm(self):
        runner, motion, _, _ = self.make_runner(mode='changing-track', match_stage=1)
        result = await self.assert_terminal(runner, motion, 'completed')
        self.assertFalse(result['found'])

    async def test_foreign_target_evidence_never_confirms(self):
        runner, motion, _, _ = self.make_runner(mode='foreign', match_stage=1)
        result = await self.assert_terminal(runner, motion, 'completed')
        self.assertFalse(result['found'])

    async def test_missing_stale_and_future_frames_fail_before_turn(self):
        for mode in ('missing', 'stale', 'future'):
            with self.subTest(mode=mode):
                runner, motion, _, _ = self.make_runner(mode=mode)
                result = await self.assert_terminal(runner, motion, 'failed')
                self.assertIn('未收到新的识别结果', result['reason'])
                self.assertFalse(result['commanded_motion'])

    async def test_target_change_camera_disconnect_and_inference_error_close(self):
        for fault, reason in (('target', '目标已更换'), ('camera', '摄像头画面中断'),
                              ('error', '识别异常')):
            with self.subTest(fault=fault):
                runner, motion, observer, _ = self.make_runner()
                observer.fail_on_rotation = fault
                result = await self.assert_terminal(runner, motion, 'failed')
                self.assertIn(reason, result['reason'])
                self.assertTrue(result['commanded_motion'])

    async def test_vision_stops_updating_during_turn_closes(self):
        for fault in ('missing', 'stale'):
            with self.subTest(fault=fault):
                runner, motion, observer, _ = self.make_runner()
                observer.fail_on_rotation = fault
                result = await self.assert_terminal(runner, motion, 'failed')
                self.assertIn('识别结果未更新', result['reason'])

    async def test_stop_before_open_cleanup_has_zero_commands(self):
        runner, motion, _, _ = self.make_runner()
        runner.stop_event.set()
        result = await self.assert_terminal(runner, motion, 'stopped')
        self.assertFalse(result['commanded_motion'])
        self.assertEqual([], motion.commands)

    async def test_stop_during_rotation_is_terminal_and_closes(self):
        runner, motion, _, _ = self.make_runner()
        motion.cancel_on_positive = True
        result = await self.assert_terminal(runner, motion, 'stopped')
        self.assertTrue(result['commanded_motion'])
        self.assertEqual(1, sum(yaw > 0 for yaw in motion.commands))

    async def test_open_command_and_close_errors_report_failure(self):
        for failure in ('fail_open', 'fail_command', 'fail_close'):
            with self.subTest(failure=failure):
                runner, motion, _, _ = self.make_runner(match_stage=1)
                setattr(motion, failure, True)
                result = await self.assert_terminal(runner, motion, 'failed')
                self.assertIn('failure', result['reason'])

    async def test_latched_guard_and_stale_control_snapshot_fail(self):
        for fault, reason in (('latched', 'fake protection'),
                              ('stale_snapshot', '控制状态中断')):
            with self.subTest(fault=fault):
                runner, motion, _, _ = self.make_runner()
                setattr(motion, fault, True)
                result = await self.assert_terminal(runner, motion, 'failed')
                self.assertIn(reason, result['reason'])
                self.assertFalse(result['commanded_motion'])

    async def test_global_timeout_closes_without_turn(self):
        runner, motion, _, _ = self.make_runner(max_seconds=.01)
        result = await self.assert_terminal(runner, motion, 'failed')
        self.assertIn('扫描超时', result['reason'])
        self.assertFalse(result['commanded_motion'])

    async def test_wrong_direction_stops_first_segment(self):
        runner, motion, _, _ = self.make_runner()
        motion.wrong_direction = True
        result = await self.assert_terminal(runner, motion, 'failed')
        self.assertIn('转动方向异常', result['reason'])
        self.assertLessEqual(sum(yaw > 0 for yaw in motion.commands), 2)


    async def test_matching_frames_during_rotation_do_not_confirm_after_stop(self):
        runner, motion, observer, _ = self.make_runner()
        original_advance = observer.advance
        def advance(yaw):
            original_advance(yaw)
            observer.last.update(suspected_target=yaw > 0, streak=3 if yaw > 0 else 0,
                                 candidates=[{'track_id':7}] if yaw > 0 else [])
        observer.advance = advance
        result = await self.assert_terminal(runner, motion, 'completed')
        self.assertFalse(result['found'])
        self.assertEqual(5, observer.stage)


class ScanControllerTests(HardwareForbidden):
    def controller(self, available=True, state=None):
        searcher = Mock()
        searcher.status.return_value = state or {
            'models_ready': True, 'target': {'id': 'target-a'},
            'camera': {'connected': True}}
        factory = Mock(return_value=FakeMotion())
        activate = AsyncMock()
        policy = lambda: {'available': available, 'physical_validated': available,
                          'reason': 'offline policy hold'}
        return product.ScanController(searcher, activate, factory, policy), factory, activate

    async def test_missing_confirmation_never_activates_or_constructs_motion(self):
        controller, factory, activate = self.controller()
        for value in (False, None, 1, 'true'):
            with self.subTest(value=value):
                with self.assertRaises(product.HTTPException) as error:
                    await controller.start(value)
                self.assertEqual(409, error.exception.status_code)
        factory.assert_not_called()
        activate.assert_not_awaited()
        self.assertIsNone(controller.task)

    async def test_policy_hold_never_activates_or_constructs_motion(self):
        controller, factory, activate = self.controller(available=False)
        with self.assertRaises(product.HTTPException) as error:
            await controller.start(True)
        self.assertEqual(409, error.exception.status_code)
        self.assertEqual('blocked', controller.status()['state'])
        self.assertFalse(controller.status()['automatic_resume'])
        factory.assert_not_called()
        activate.assert_not_awaited()
        self.assertIsNone(controller.task)
        await controller.stop()
        factory.assert_not_called()

    async def test_unready_model_target_or_camera_refuses_before_factory(self):
        for field in ('models_ready', 'target', 'camera'):
            with self.subTest(field=field):
                state = {'models_ready': True, 'target': {'id': 'target-a'},
                         'camera': {'connected': True}}
                state[field] = {'connected': False} if field == 'camera' else None
                controller, factory, activate = self.controller(state=state)
                with self.assertRaises(product.HTTPException) as error:
                    await controller.start(True)
                self.assertEqual(409, error.exception.status_code)
                factory.assert_not_called()
                activate.assert_not_awaited()


    async def test_concurrent_start_is_refused_during_activation(self):
        controller, factory, activate = self.controller()
        entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def delayed_activate():
            entered.set()
            await release.wait()
        activate.side_effect = delayed_activate
        class FakeRunner:
            def __init__(self, *args, **kwargs):
                pass
            async def run(self):
                await finished.wait()
                return {'state':'stopped', 'motion_output_enabled':False}
        with patch.object(product, 'ScanRunner', FakeRunner), \
                patch('go2_platform_write.write') as writes:
            first = asyncio.create_task(controller.start(True))
            await asyncio.wait_for(entered.wait(), 1)
            try:
                with self.assertRaises(product.HTTPException) as error:
                    await controller.start(True)
                self.assertEqual(409, error.exception.status_code)
                factory.assert_not_called()
            finally:
                release.set()
                await asyncio.wait_for(first, 1)
                finished.set()
                await asyncio.wait_for(controller.task, 1)
            self.assertEqual(1, factory.call_count)
            self.assertEqual(1, activate.await_count)
            self.assertEqual(1, writes.call_count)

    async def test_stop_during_activation_cannot_restart_pending_scan(self):
        controller, factory, activate = self.controller()
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed_activate():
            entered.set()
            await release.wait()
        activate.side_effect = delayed_activate
        first = asyncio.create_task(controller.start(True))
        await asyncio.wait_for(entered.wait(), 1)
        stopped = await controller.stop()
        self.assertFalse(stopped['motion_output_enabled'])
        release.set()
        with self.assertRaises(product.HTTPException) as error:
            await asyncio.wait_for(first, 1)
        self.assertEqual(409, error.exception.status_code)
        factory.assert_not_called()
        self.assertIsNone(controller.task)
        self.assertFalse(controller.starting)

    async def test_policy_revoked_during_activation_refuses_before_factory(self):
        controller, factory, activate = self.controller()
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed_activate():
            entered.set()
            await release.wait()
        activate.side_effect = delayed_activate
        first = asyncio.create_task(controller.start(True))
        await asyncio.wait_for(entered.wait(), 1)
        controller.policy = lambda: {'available':False, 'physical_validated':False,
                                     'reason':'offline revoked'}
        release.set()
        with self.assertRaises(product.HTTPException) as error:
            await asyncio.wait_for(first, 1)
        self.assertEqual(409, error.exception.status_code)
        factory.assert_not_called()
        self.assertIsNone(controller.task)
        self.assertEqual('blocked', controller.status()['state'])

    async def test_activation_failure_cleans_start_reservation(self):
        controller, factory, activate = self.controller()
        activate.side_effect = RuntimeError('fake activation failure')
        with self.assertRaisesRegex(RuntimeError, 'fake activation failure'):
            await controller.start(True)
        factory.assert_not_called()
        self.assertIsNone(controller.task)
        self.assertFalse(controller.starting)
        self.assertFalse(controller.status()['motion_output_enabled'])

    async def test_malformed_policy_fails_closed_without_file_writes(self):
        for raw in ('[]', 'null', 'not-json', '{}',
                    '{"enabled":true,"physical_validated":false}'):
            with self.subTest(raw=raw), patch.object(product.Path, 'read_text', return_value=raw):
                policy = product.scan_policy()
                self.assertFalse(policy['available'])
                self.assertTrue(policy['reason'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
