#!/usr/bin/env python3
"""Continuous receive-only Go2 telemetry; two fixed topics, atomic 1 Hz JSON.

Run in go2_sdk2_venv with source_official_go2_sdk.sh. No control clients,
commands, camera, audio, or platform startup. Importing this module opens no DDS.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import tempfile
import threading
import time

from health_status import STALE_SECONDS, summarize_health
from scan_readonly_state import DEFAULT_CONFIG, decode_remote

DEFAULT_OUTPUT = Path('/home/unitree/go2_ros2_ws/runtime/health/status.json')


class HealthReceiver:
    def __init__(self, clock=time.time, monotonic=time.monotonic):
        self.clock, self.monotonic = clock, monotonic
        self.lock = threading.Lock()
        self.latest = {'low': None, 'sport': None}
        self.valid = {'low': False, 'sport': False}
        self.counts = {'low': 0, 'sport': 0}
        self.errors = {'low': None, 'sport': None}

    def _receive(self, key, sample):
        json.dumps(sample, allow_nan=False)
        sample.update(at=self.clock(), monotonic_at=self.monotonic())
        with self.lock:
            self.latest[key] = sample
            self.valid[key] = True
            self.counts[key] += 1
            self.errors[key] = None

    def _error(self, key, error):
        with self.lock:
            self.valid[key] = False
            self.errors[key] = '%s: %s' % (key, error)

    def receive_low(self, message):
        try:
            motors = message.motor_state[:12]
            if len(motors) != 12:
                raise ValueError('expected 12 physical motors')
            self._receive('low', {
                'temperatures': [int(motor.temperature) for motor in motors],
                'motor_faults': [int(motor.reserve[0]) for motor in motors],
                'motor_communication_hz': [int(motor.reserve[1]) for motor in motors],
                'motor_lost': [int(motor.lost) for motor in motors],
                'soc': int(message.bms_state.soc), 'power_v': float(message.power_v),
                'power_a': float(message.power_a), 'bit_flag': int(message.bit_flag),
                'tick': int(message.tick), 'remote': decode_remote(message.wireless_remote)})
        except Exception as error:
            self._error('low', error)

    def receive_sport(self, message):
        try:
            self._receive('sport', {'height': float(message.body_height),
                'rpy': list(message.imu_state.rpy), 'mode': int(message.mode),
                'error_code': int(message.error_code)})
        except Exception as error:
            self._error('sport', error)

    def snapshot(self, running=True):
        now, monotonic = self.clock(), self.monotonic()
        with self.lock:
            low, sport = self.latest['low'] or {}, self.latest['sport'] or {}
            available = running and all(self.valid[key] and self.latest[key] is not None
                and 0 <= monotonic - self.latest[key]['monotonic_at'] <= STALE_SECONDS
                for key in ('low', 'sport'))
            result = {key: value for sample in (low, sport) for key, value in sample.items()
                      if key not in ('at', 'monotonic_at')}
            result.update(sampled_at=now, low_at=low.get('at'), sport_at=sport.get('at'),
                state_available=bool(available), running=bool(running), readonly=True,
                motion_allowed=False, counts=dict(self.counts),
                last_error='; '.join(error for error in self.errors.values() if error) or None)
        # Missing/invalid core fields also fail closed in the pure decoder.
        if not summarize_health(result, now)['connected']:
            result['state_available'] = False
        return result


def write_atomic(path, snapshot):
    """Atomically replace telemetry; runtime samples are not versioned backups."""
    path = Path(path)
    payload = json.dumps(snapshot, ensure_ascii=False, allow_nan=False) + '\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, str(path))
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def run(config=DEFAULT_CONFIG, output=DEFAULT_OUTPUT):
    # Preserve the previous status (or register a new path) once per startup.
    from go2_platform_write import backup
    backup(output)
    # SDK imports occur only in the explicitly launched receiver process.
    from unitree_sdk2py.core import channel
    from unitree_sdk2py.idl.unitree_go.msg.dds_ import LowState_, SportModeState_
    receiver, stopping, subscribers = HealthReceiver(), threading.Event(), []
    previous = {sig: signal.signal(sig, lambda _sig, _frame: stopping.set())
                for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        write_atomic(output, receiver.snapshot())
        channel.ChannelConfigAutoDetermine = Path(config).read_text()
        channel.ChannelFactoryInitialize(0)
        for topic, message_type, callback in (
                ('rt/lf/lowstate', LowState_, receiver.receive_low),
                ('rt/lf/sportmodestate', SportModeState_, receiver.receive_sport)):
            subscriber = channel.ChannelSubscriber(topic, message_type)
            subscribers.append(subscriber)
            subscriber.Init(callback, 4)
        while not stopping.is_set():
            write_atomic(output, receiver.snapshot())
            stopping.wait(1.0)
    finally:
        stopping.set()
        for subscriber in subscribers:
            try:
                subscriber.Close()
            except Exception:
                pass
        write_atomic(output, receiver.snapshot(running=False))
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dds-config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    run(args.dds_config, args.output)


if __name__ == '__main__':
    main()
