#!/usr/bin/env python3
"""Bounded Go2 state subscription; never initializes a robot control client.

Run in go2_sdk2_venv after source_official_go2_sdk.sh. JSON stdout only.
Uses the existing receive-only DDS configuration and two fixed state topics.
No output files, service startup, camera/audio, or robot command endpoints.
"""
import argparse
import json
import math
import os
from pathlib import Path
import struct
import sys
import threading
import time

DEFAULT_CONFIG = Path(__file__).with_name('cyclonedds_go2.xml')


def decode_remote(raw):
    """Offsets from official unitree_sdk2py/utils/joystick.py extract()."""
    data = bytes(raw)
    axes = {key: struct.unpack_from('<f', data, offset)[0]
            for key, offset in [('lx', 4), ('rx', 8), ('ry', 12), ('ly', 20)]}
    return {'keys': int.from_bytes(data[2:4], 'little'), 'axes': axes}


def summarize(histories, started_at, duration, ended_monotonic):
    latest = {key: {name: value for name, value in samples[-1].items() if name != '_at'}
              for key, samples in histories.items() if samples}
    windows = {}
    for key, samples in histories.items():
        if not samples:
            continue
        times = [sample['_at'] for sample in samples]
        windows[key] = {'age_seconds': max(0., ended_monotonic - times[-1]),
            'max_receive_gap_seconds': max([b-a for a,b in zip(times,times[1:])] or [0.]),
            'received_span_seconds': max(0., times[-1]-times[0])}
    low, sport = histories['low'], histories['sport']
    temperature_max = [max(sample['motor_temperatures'][index] for sample in low) for index in range(12)] if low else []
    if low:
        ticks = [sample['tick'] for sample in low]
        windows['low'].update(tick_first=ticks[0], tick_last=ticks[-1], tick_delta=ticks[-1]-ticks[0],
            tick_nondecreasing=all(b>=a for a,b in zip(ticks,ticks[1:])),
            motor_temperature_max=temperature_max, soc_min=min(sample['soc'] for sample in low),
            remote_keys_observed=sorted(set(sample['remote']['keys'] for sample in low)),
            remote_max_abs_axis=max(abs(value) for sample in low for value in sample['remote']['axes'].values()))
    if sport:
        windows['sport'].update(body_height_min=min(sample['body_height'] for sample in sport),
            body_height_max=max(sample['body_height'] for sample in sport),
            max_abs_roll=max(abs(sample['rpy'][0]) for sample in sport),
            max_abs_pitch=max(abs(sample['rpy'][1]) for sample in sport),
            max_linear_speed=max(math.sqrt(sum(value*value for value in sample['velocity'])) for sample in sport),
            max_abs_yaw_speed=max(abs(sample['yaw_speed']) for sample in sport),
            modes=sorted(set(sample['mode'] for sample in sport)),
            error_codes=sorted(set(sample['error_code'] for sample in sport)))
    available = all(histories[key] and windows[key]['age_seconds'] <= .25 for key in ('low', 'sport'))
    return {'readonly': True, 'motion_output_enabled': False, 'state_available': bool(available),
        'started_at': started_at, 'sampled_at': time.time(), 'requested_duration_seconds': duration,
        'latest': latest, 'counts': {key:len(samples) for key,samples in histories.items()},
        'windows': windows, 'temp_max': max(temperature_max) if temperature_max else None,
        'error': None if available else {'code':'state_unavailable', 'message':'Low or sport state is missing or stale'}}


def collect(duration=2., config=DEFAULT_CONFIG):
    if not math.isfinite(duration) or not .5 <= duration <= 5:
        raise ValueError('duration must be between 0.5 and 5 seconds')
    xml = Path(config).read_text()
    # Only the factory and subscribers are imported: no control client/publisher.
    from unitree_sdk2py.core import channel
    from unitree_sdk2py.idl.unitree_go.msg.dds_ import LowState_, SportModeState_
    channel.ChannelConfigAutoDetermine = xml
    channel.ChannelFactoryInitialize(0)
    histories, errors, lock, subscribers = {'low':[], 'sport':[]}, [], threading.Lock(), []
    started_at = time.time()

    def append(key, sample):
        json.dumps(sample, allow_nan=False)
        with lock:
            histories[key].append(sample)

    def receive_low(message):
        try:
            bms = message.bms_state
            append('low', {'_at':time.monotonic(), 'received_at':time.time(), 'tick':int(message.tick),
                'soc':int(bms.soc), 'bms_status':int(bms.status), 'bms_current_raw':int(bms.current),
                'bms_bq_ntc':list(bms.bq_ntc), 'bms_mcu_ntc':list(bms.mcu_ntc),
                'power_v':float(message.power_v), 'power_a':float(message.power_a),
                'temperature_ntc1':int(message.temperature_ntc1), 'temperature_ntc2':int(message.temperature_ntc2),
                'motor_temperatures':[int(motor.temperature) for motor in message.motor_state[:12]],
                'motor_modes':[int(motor.mode) for motor in message.motor_state[:12]],
                'motor_lost':[int(motor.lost) for motor in message.motor_state[:12]],
                'motor_reserve_raw':[list(map(int,motor.reserve)) for motor in message.motor_state[:12]],
                'motor_q_rad':[float(motor.q) for motor in message.motor_state[:12]],
                'motor_dq_rad_s':[float(motor.dq) for motor in message.motor_state[:12]],
                'motor_tau_est_nm':[float(motor.tau_est) for motor in message.motor_state[:12]],
                'fan_frequency_raw':list(map(int,message.fan_frequency)),
                'low_reserve_raw':int(message.reserve), 'version_raw':list(map(int,message.version)),
                'bit_flag':int(message.bit_flag), 'foot_force':list(message.foot_force),
                'remote':decode_remote(message.wireless_remote)})
        except Exception as error:
            with lock:
                if len(errors)<4: errors.append('low: '+str(error))

    def receive_sport(message):
        try:
            append('sport', {'_at':time.monotonic(), 'received_at':time.time(),
                'source_stamp':[int(message.stamp.sec),int(message.stamp.nanosec)],
                'mode':int(message.mode), 'error_code':int(message.error_code), 'gait_type':int(message.gait_type),
                'body_height':float(message.body_height), 'position':list(message.position),
                'velocity':list(message.velocity), 'yaw_speed':float(message.yaw_speed), 'rpy':list(message.imu_state.rpy)})
        except Exception as error:
            with lock:
                if len(errors)<4: errors.append('sport: '+str(error))

    try:
        for topic, message_type, callback in [('rt/lf/lowstate',LowState_,receive_low),
                ('rt/lf/sportmodestate',SportModeState_,receive_sport)]:
            subscriber = channel.ChannelSubscriber(topic,message_type)
            subscribers.append(subscriber)
            subscriber.Init(callback,4)
        threading.Event().wait(duration)
        ended = time.monotonic()
    finally:
        for subscriber in subscribers:
            subscriber.Close()
    with lock:
        result = summarize(histories,started_at,duration,ended)
    if errors:
        result['sample_errors'] = errors
        result['state_available'] = False
        result['error'] = {'code':'invalid_state_sample','message':'Invalid telemetry received; see sample_errors'}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration',type=float,default=2.)
    parser.add_argument('--dds-config',type=Path,default=DEFAULT_CONFIG)
    args = parser.parse_args()
    saved_stdout = None
    try:
        # Redirect SDK native/Python messages to stderr, preserving one stdout JSON.
        sys.stdout.flush()
        saved_stdout = os.dup(sys.stdout.fileno())
        os.dup2(sys.stderr.fileno(),sys.stdout.fileno())
        result = collect(args.duration,args.dds_config)
    except Exception as error:
        result = {'readonly':True,'motion_output_enabled':False,'state_available':False,
            'latest':{},'counts':{'low':0,'sport':0},'windows':{},'temp_max':None,
            'error':{'code':'state_read_failed','message':str(error)}}
    finally:
        if saved_stdout is not None:
            sys.stdout.flush()
            os.dup2(saved_stdout,sys.stdout.fileno())
            os.close(saved_stdout)
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
    return 0 if result['state_available'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
