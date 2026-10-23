"""Offline Go2 health tests. No SDK, DDS, services, or robot connection."""
import ast
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from health_status import summarize_health
from go2_health_monitor import HealthReceiver, write_atomic


def sample():
    temperatures = [30] * 12
    temperatures[6], temperatures[9] = 64, 70
    faults = [0] * 12
    faults[9] = 4
    return {'sampled_at': 100., 'low_at': 99.8, 'sport_at': 99.9,
            'state_available': True, 'running': True, 'temperatures': temperatures,
            'motor_faults': faults, 'height': .0638, 'soc': 56, 'power_v': 29.836,
            'rpy': [0., 0., 0.], 'bit_flag': 0, 'remote': {'keys': 0, 'axes': {}}}


class SummaryTests(unittest.TestCase):
    def test_confirmed_driver_overheat_on_rear_left_hip(self):
        result = summarize_health(sample(), 100.)
        self.assertTrue(result['connected'])
        self.assertFalse(result['motion_allowed'])
        self.assertEqual(result['rear_left_hip_temperature_c'], 70)
        self.assertEqual(result['rear_right_hip_temperature_c'], 64)
        self.assertEqual(result['faults'], [{'index': 9, 'joint': 'RL_0',
            'code_hex': '0x04', 'messages': ['驱动过热']}])
        self.assertIn('过热', result['reason'])

    def test_combined_motor_fault_flags(self):
        raw = sample()
        raw['motor_faults'][9] = 0x01 | 0x02 | 0x04 | 0x08 | 0x10 | 0x20 | 0x100
        self.assertEqual(summarize_health(raw, 100.)['faults'][0]['messages'],
            ['过流', '过压', '驱动过热', '母线欠压', '绕组过热', '编码器异常', '电机通信中断'])

    def test_communication_reason(self):
        raw = sample()
        raw['motor_faults'][9] = 0x100
        self.assertIn('通信异常', summarize_health(raw, 100.)['reason'])

    def test_source_100_current_communication_bits(self):
        raw = sample()
        raw['motor_faults'] = [0] * 12
        raw['bit_flag'] = 0xd0
        result = summarize_health(raw, 100.)
        self.assertEqual(result['system_faults'], ['MCU通信异常', '电机通信异常', '电池通信异常'])
        self.assertIn('通信异常', result['reason'])

    def test_deprecated_bits_not_reinterpreted(self):
        raw = sample()
        raw['motor_faults'] = [0] * 12
        raw['bit_flag'] = 0x25
        result = summarize_health(raw, 100.)
        self.assertEqual(result['system_faults'], [])
        self.assertEqual(result['low_state_bit_flag'], 0x25)

    def test_unknown_motor_bits_are_preserved(self):
        raw = sample()
        raw['motor_faults'][9] = 0x84
        fault = summarize_health(raw, 100.)['faults'][0]
        self.assertEqual(fault['code_hex'], '0x84')
        self.assertEqual(fault['messages'], ['驱动过热', '未定义故障位 0x80'])

    def test_zero_faults_do_not_authorize_motion(self):
        raw = sample()
        raw['motor_faults'] = [0] * 12
        result = summarize_health(raw, 100.)
        self.assertTrue(result['connected'])
        self.assertEqual(result['faults'], [])
        self.assertFalse(result['motion_allowed'])
        self.assertIn('动作未授权', result['reason'])

    def test_stale_writer(self):
        result = summarize_health(sample(), 103.)
        self.assertFalse(result['connected'])
        self.assertGreater(result['age_s'], 3.)
        self.assertEqual(result['faults'][0]['code_hex'], '0x04')
        self.assertIn('离线', result['reason'])

    def test_fresh_writer_cannot_hide_stale_topic(self):
        for key in ('low_at', 'sport_at'):
            with self.subTest(key=key):
                raw = sample()
                raw[key] = 90.
                result = summarize_health(raw, 100.)
                self.assertFalse(result['connected'])
                self.assertEqual(result['age_s'], 10.)

    def test_explicit_unavailable_and_stopped(self):
        for key in ('state_available', 'running'):
            with self.subTest(key=key):
                raw = sample()
                raw[key] = False
                self.assertFalse(summarize_health(raw, 100.)['connected'])

    def test_missing_data_fail_closed(self):
        for raw in (None, {}, [], {'sampled_at': 100.}):
            with self.subTest(raw=raw):
                result = summarize_health(raw, 100.)
                self.assertFalse(result['connected'])
                self.assertFalse(result['motion_allowed'])
                self.assertIsNone(result['age_s'])
                json.dumps(result, allow_nan=False)

    def test_future_nan_missing_timestamps(self):
        for stamp in (101., float('nan'), None, '100'):
            for key in ('sampled_at', 'low_at', 'sport_at'):
                raw = sample()
                raw[key] = stamp
                result = summarize_health(raw, 100.)
                self.assertFalse(result['connected'])
                self.assertIsNone(result['age_s'])
                json.dumps(result, allow_nan=False)

    def test_malformed_core_fields(self):
        for key, value in [('motor_faults', [0] * 11), ('motor_faults', [True] * 12),
                           ('motor_faults', [-1] * 12), ('temperatures', [float('nan')] * 12),
                           ('temperatures', [256] * 12), ('soc', 101), ('height', None),
                           ('bit_flag', []), ('state_available', 1)]:
            raw = sample()
            raw[key] = value
            result = summarize_health(raw, 100.)
            self.assertFalse(result['connected'], (key, value))
            self.assertFalse(result['motion_allowed'])
            json.dumps(result, allow_nan=False)


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.now, self.mono = 100., 20.
        self.receiver = HealthReceiver(lambda: self.now, lambda: self.mono)
        motors = [types.SimpleNamespace(temperature=30, reserve=[0, 525], lost=0) for _ in range(20)]
        motors[9].temperature, motors[9].reserve = 70, [4, 525]
        self.low = types.SimpleNamespace(motor_state=motors,
            bms_state=types.SimpleNamespace(soc=56), power_v=29.836, power_a=.2,
            bit_flag=0, tick=123, wireless_remote=bytes(40))
        self.sport = types.SimpleNamespace(body_height=.0638,
            imu_state=types.SimpleNamespace(rpy=[0., 0., 0.]), mode=0, error_code=100)

    def receive(self):
        self.receiver.receive_low(self.low)
        self.receiver.receive_sport(self.sport)

    def test_initially_offline(self):
        result = self.receiver.snapshot()
        self.assertFalse(result['state_available'])
        self.assertEqual(result['counts'], {'low': 0, 'sport': 0})

    def test_real_schema_fields_and_frequency_not_fault(self):
        self.receive()
        result = self.receiver.snapshot()
        self.assertTrue(result['state_available'])
        self.assertEqual(result['motor_faults'], [0] * 9 + [4, 0, 0])
        self.assertEqual(result['motor_communication_hz'], [525] * 12)
        self.assertEqual(len(result['temperatures']), 12)
        self.assertEqual(result['remote']['keys'], 0)
        self.assertEqual(result['low_at'], 100.)
        self.assertEqual(result['sport_at'], 100.)
        self.assertFalse(result['motion_allowed'])

    def test_topic_disconnect_despite_writer_heartbeat(self):
        self.receive()
        self.now, self.mono = 103., 23.
        self.receiver.receive_low(self.low)
        result = self.receiver.snapshot()
        self.assertFalse(result['state_available'])
        self.assertEqual(result['sampled_at'], 103.)
        self.assertEqual(result['sport_at'], 100.)

    def test_stopped_snapshot_not_online(self):
        self.receive()
        result = self.receiver.snapshot(running=False)
        self.assertFalse(result['state_available'])
        self.assertFalse(summarize_health(result, self.now)['connected'])

    def test_bad_sample_invalidates_until_good_recovery(self):
        self.receive()
        self.sport.imu_state.rpy = [float('nan'), 0., 0.]
        self.receiver.receive_sport(self.sport)
        self.assertFalse(self.receiver.snapshot()['state_available'])
        self.assertIn('sport:', self.receiver.snapshot()['last_error'])
        self.sport.imu_state.rpy = [0., 0., 0.]
        self.receiver.receive_sport(self.sport)
        self.assertTrue(self.receiver.snapshot()['state_available'])

    def test_backward_host_clock_fails_closed(self):
        self.receive()
        self.now = 99.
        self.assertFalse(self.receiver.snapshot()['state_available'])

    def test_real_atomic_replace_keeps_complete_previous_json_until_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'health' / 'status.json'
            old = {'state_available': False, 'sequence': 1}
            new = {'state_available': True, 'sequence': 2, 'text': '驱动过热'}
            write_atomic(path, old)
            real_replace = os.replace
            observed = []

            def inspect_replace(source, destination):
                self.assertEqual(Path(source).parent, path.parent)
                self.assertEqual(json.loads(path.read_text()), old)
                self.assertEqual(json.loads(Path(source).read_text()), new)
                observed.append(source)
                real_replace(source, destination)

            with patch('go2_health_monitor.os.replace', side_effect=inspect_replace):
                write_atomic(path, new)
            self.assertEqual(len(observed), 1)
            self.assertEqual(json.loads(path.read_text()), new)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_failed_replace_cleans_temp_and_preserves_old_status(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'status.json'
            old = {'state_available': False, 'sequence': 1}
            write_atomic(path, old)
            with patch('go2_health_monitor.os.replace', side_effect=OSError('simulated replacement error')):
                with self.assertRaises(OSError):
                    write_atomic(path, {'sequence': 2})
            self.assertEqual(json.loads(path.read_text()), old)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_failed_serialization_creates_no_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'status.json'
            with self.assertRaises(ValueError):
                write_atomic(path, {'temperature': float('nan')})
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_runtime_updates_do_not_create_backups(self):
        helper = types.ModuleType('go2_platform_write')
        helper.backup = lambda _path: self.fail('runtime update unexpectedly creates backup')
        helper.write = lambda *_args: self.fail('runtime update unexpectedly creates backup')
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {'go2_platform_write': helper}):
            path = Path(directory) / 'status.json'
            for sequence in range(3):
                write_atomic(path, {'sequence': sequence})
            self.assertEqual(json.loads(path.read_text()), {'sequence': 2})
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_no_control_import_or_command_in_module(self):
        source = Path(__file__).with_name('go2_health_monitor.py').read_text()
        tree = ast.parse(source)
        forbidden = {'ChannelPublisher', 'SportClient', 'ObstacleAvoidClient', 'VuiClient', 'UnitreeClient'}
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        self.assertFalse(forbidden & (attributes | names))
        self.assertNotIn('unitree_sdk2py.core.channel', sys.modules)
        self.assertIn("'rt/lf/lowstate'", source)
        self.assertIn("'rt/lf/sportmodestate'", source)
        self.assertNotIn('cmd_vel', source)


if __name__ == '__main__':
    unittest.main()
