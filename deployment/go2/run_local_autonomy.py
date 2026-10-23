#!/usr/bin/env python3
"""Preview or explicitly supervise a bounded low-speed autonomous Go2 session.

The preview only subscribes to ROS. Execute requires an onsite confirmation and
uses the existing official SDK worker, with an independent telemetry watchdog.
"""
import argparse
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import threading
import time

from local_autonomy import FreshInputs, Limits, LocalRoamer, number, scan_points

BASE = Path(__file__).resolve().parent
HEALTH = Path('/home/unitree/go2_ros2_ws/runtime/health/status.json')


def posture_hold(path=HEALTH):
    from health_status import summarize_health
    try:
        raw = json.loads(path.read_text())
        summary = summarize_health(raw, time.time())
        if not summary['connected'] or summary['faults'] or summary['system_faults']:
            return 'Health telemetry unavailable or motor/system fault present'
        if not .25 <= number(raw.get('height')) <= .40:
            return 'Robot must be standing stably at 25..40 cm before execution'
        if raw.get('error_code') in (1001, 1002):
            return 'MCF standing state is locked; operator must unlock with START before autonomy'
        rpy = raw.get('rpy', [])
        if len(rpy) != 3 or max(abs(number(v)) for v in rpy[:2]) >= .25:
            return 'Robot attitude is outside the conservative tilt limit'
        if max(raw['temperatures']) >= 65 or raw['soc'] < 30:
            return 'Temperature or battery preflight rejected'
        return None
    except (OSError, ValueError, TypeError, KeyError):
        return 'Valid health telemetry is required before execution'


class OfficialWorker:
    def __init__(self, limits, duration, forward_only=False):
        self.latest, self.lock, self.process = {}, threading.Lock(), None
        env = os.environ.copy()
        env['CYCLONEDDS_HOME'] = '/home/unitree/cyclonedds_ws/install/cyclonedds'
        env['LD_LIBRARY_PATH'] = env['CYCLONEDDS_HOME'] + '/lib:' + env.get('LD_LIBRARY_PATH', '')
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        self.process = subprocess.Popen([
            '/home/unitree/go2_sdk2_venv/bin/python', '-B', '-u', str(BASE / 'official_sdk_worker.py'),
            '--armed', '--autonomy', '--max-speed', str(limits.speed),
            '--radius', str(limits.radius), '--duration', str(duration),
        ] + (['--forward-only'] if forward_only else []),
            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        def read():
            for line in self.process.stdout:
                try:
                    payload = json.loads(line)
                    with self.lock:
                        self.latest = dict(payload, received_at=time.monotonic())
                except (ValueError, TypeError):
                    pass
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()

    def snapshot(self):
        with self.lock:
            return dict(self.latest)

    def send(self, velocity, valid=True):
        if self.process.poll() is not None:
            raise RuntimeError('Native motion worker exited')
        self.process.stdin.write(json.dumps(dict(velocity=velocity, valid=valid), allow_nan=False) + '\n')
        self.process.stdin.flush()

    def close(self):
        try:
            self.send([0., 0., 0.], False)
        except (OSError, ValueError, RuntimeError):
            pass
        try:
            self.process.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.reader.join(timeout=1)
        self.process.stdout.close()
        return self.snapshot()


def run(args):
    import rclpy
    from nav_msgs.msg import Odometry
    from rclpy.qos import qos_profile_sensor_data
    from sensor_msgs.msg import LaserScan, PointCloud2

    limits = Limits(radius=args.radius, speed=args.speed)
    forward_only = bool(getattr(args, 'forward_only', False))
    roamer, inputs = LocalRoamer(limits), FreshInputs()
    rclpy.init()
    node = rclpy.create_node('go2_local_autonomy_observer' if not args.execute else 'go2_local_autonomy')
    stop_event = threading.Event()
    previous = {sig: signal.signal(sig, lambda *_: stop_event.set()) for sig in (signal.SIGTERM, signal.SIGINT)}
    worker, sensor_failure = None, None
    report = dict(mode='execute' if args.execute else 'preview', motion_started=False,
                  radius_m=limits.radius, max_speed_m_s=limits.speed, max_yaw_rad_s=limits.yaw_speed,
                  clearance_m=limits.clearance, duration_s=args.duration,
                  forward_only=forward_only,
                  started_at=time.time(), samples=[], completed=False)

    def receive(key, message, convert):
        nonlocal sensor_failure
        try:
            value = convert(message)
            stamp = message.header.stamp.sec + message.header.stamp.nanosec / 1e9
            inputs.receive(key, value, stamp, time.monotonic(), node.get_clock().now().nanoseconds / 1e9)
            if worker and key in inputs.errors:
                sensor_failure = inputs.errors[key]
        except (ValueError, TypeError, AttributeError) as exc:
            inputs.errors[key] = str(exc)
            if worker:
                sensor_failure = str(exc)

    def scan(m):
        return scan_points(m.ranges, m.angle_min, m.angle_increment, m.range_min, m.range_max, m.header.frame_id)

    def odometry(m):
        if m.header.frame_id != 'odom' or m.child_frame_id != 'base_footprint':
            raise ValueError('Unexpected odometry frame')
        p, q, v = m.pose.pose.position, m.pose.pose.orientation, m.twist.twist
        xyz = list(map(number, (p.x, p.y, p.z)))
        quat = list(map(number, (q.x, q.y, q.z, q.w)))
        if abs(sum(x*x for x in quat) - 1.) > .02:
            raise ValueError('Invalid odometry quaternion')
        yaw = math.atan2(2*(q.w*q.z + q.x*q.y), 1-2*(q.y*q.y + q.z*q.z))
        return dict(pose=(xyz[0], xyz[1], yaw), speed=math.hypot(number(v.linear.x), number(v.linear.y)),
                    yaw_speed=number(v.angular.z))

    def cloud(m):
        if m.header.frame_id != 'hesai_lidar' or m.width*m.height < 128:
            raise ValueError('Empty cloud or unexpected lidar frame')
        if not {'x', 'y', 'z'}.issubset({field.name for field in m.fields}):
            raise ValueError('Cloud has no XYZ fields')
        if not m.point_step or len(m.data) != m.row_step*m.height:
            raise ValueError('Invalid point cloud payload')
        return dict(points=m.width*m.height)

    subscriptions = [node.create_subscription(kind, topic, lambda m, k=key, c=convert: receive(k, m, c), qos_profile_sensor_data)
                     for key, kind, topic, convert in [('scan', LaserScan, '/scan', scan),
                        ('odom', Odometry, '/odom', odometry), ('cloud', PointCloud2, '/lidar_points', cloud)]]
    begin, next_tick, healthy_since, ready_since, blocked_since = time.monotonic(), 0., None, None, None
    last_state = None
    try:
        while not stop_event.is_set():
            rclpy.spin_once(node, timeout_sec=.01)
            now = time.monotonic()
            if now < next_tick:
                continue
            next_tick = now + .05
            if now - begin >= args.duration:
                if not report['samples'] or all(s['state'] in ('waiting', 'preflight') for s in report['samples']):
                    raise RuntimeError('Session ended before valid inputs and preflight were ready')
                report['completed'] = True
                report['reason'] = 'Session time limit reached'
                break
            fault = sensor_failure or inputs.reason(now, node.get_clock().now().nanoseconds / 1e9)
            if fault:
                healthy_since = None
                if worker or now - begin > 8:
                    raise RuntimeError(fault)
                plan = dict(state='waiting', reason=fault, velocity=[0., 0., 0.])
            else:
                odom = inputs.samples['odom']['value']
                points = inputs.samples['scan']['value']
                nearest = min(points, key=lambda p: math.hypot(*p))
                report['nearest_obstacle'] = dict(distance_m=math.hypot(*nearest), x_base_m=nearest[0], y_base_m=nearest[1])
                active = bool(worker and worker.snapshot().get('worker_ready'))
                plan = roamer.step(odom['pose'], points, now, odom['speed'], odom['yaw_speed'], monitor_progress=active)
                if plan['state'] == 'fault':
                    report['samples'].append(dict(plan, elapsed_s=now-begin, motion_output_enabled=False))
                    raise RuntimeError(plan['reason'])
                if forward_only and plan['state'] in ('braking', 'turning', 'blocked'):
                    raise RuntimeError('Forward-only calibration has no clear forward corridor; stopped without turning')
                if args.execute and worker is None:
                    hold = posture_hold()
                    if hold:
                        raise RuntimeError(hold)
                    healthy_since = healthy_since or now
                    if now - healthy_since >= 2:
                        conflicts = {topic:[info.node_name for info in node.get_publishers_info_by_topic(topic)]
                            for topic in ('/api/sport/request','/cmd_vel','/lowcmd')}
                        if any(conflicts.values()):
                            raise RuntimeError('Another motion publisher is present: ' + json.dumps(conflicts))
                        worker = OfficialWorker(limits, args.duration, forward_only=forward_only)
                        ready_since = now
                    # Preflight cannot issue the planner's nonzero velocity.
                    plan = dict(state='preflight', reason='Waiting for stable sensors and native worker', velocity=[0., 0., 0.])
            if worker:
                status = worker.snapshot()
                if status.get('latched_stop'):
                    raise RuntimeError(status.get('guard_reason') or 'Native guard latched stop')
                if not status.get('worker_ready'):
                    if now - ready_since > 5:
                        raise RuntimeError('Native motion preflight timeout')
                    plan = dict(state='preflight', reason='Waiting for native healthy standing telemetry', velocity=[0., 0., 0.])
                elif now - status.get('received_at', 0) > .25:
                    raise RuntimeError('Native motion worker heartbeat is stale')
                if any(plan['velocity']):
                    report['motion_started'] = True  # Attempted delivery, not proof of physical movement.
                worker.send(plan['velocity'])
            if plan['state'] == 'blocked':
                blocked_since = blocked_since or now
                if now - blocked_since > 5:
                    raise RuntimeError('No safe direction for 5 seconds; session stopped')
            else:
                blocked_since = None
            sample = dict(plan, elapsed_s=now-begin, displacement_m=roamer.max_displacement,
                          motion_output_enabled=bool(worker), execute_hold=posture_hold() if not args.execute else None)
            if 'odom' in inputs.samples:
                sample['observed_odometry'] = dict(inputs.samples['odom']['value'])
            if worker:
                native = worker.snapshot()
                sample['native_motion'] = {key:native.get(key) for key in
                    ('commanded','native_state','native_low','last_response','guard_reason','autonomy_preparation')}
            if len(report['samples']) < 1500:
                report['samples'].append(sample)
            if plan['state'] != last_state:
                print(json.dumps(sample, ensure_ascii=False), flush=True)
                last_state = plan['state']
        else:
            report['reason'] = 'Stopped by operator signal'
    except (Exception, KeyboardInterrupt) as exc:
        report['error'] = str(exc)
    finally:
        if worker:
            try:
                report['worker_shutdown'] = worker.close()
            except Exception as exc:
                report['worker_shutdown'] = {'shutdown_error':str(exc)}
            stop_replies = report['worker_shutdown'].get('stop_responses', [])
            report['stop_acknowledged'] = report['worker_shutdown'].get('shutdown') is True and bool(stop_replies) and all(
                reply.get('code') == 0 and reply.get('zero_move_code') == 0 and not reply.get('errors') for reply in stop_replies)
            if report['motion_started'] and not report['stop_acknowledged']:
                report['completed'] = False
                report['stop_error'] = 'Stop confirmation missing or rejected; onsite check required'
        report.update(max_displacement_m=roamer.max_displacement, ended_at=time.time(),
                      source_errors=dict(inputs.errors), execute_hold=posture_hold())
        report['input_status'] = {key: dict(source_stamp_s=s['stamp'], receive_age_s=time.monotonic()-s['at'],
            source_age_s=node.get_clock().now().nanoseconds/1e9-s['stamp']) for key,s in inputs.samples.items()}
        report['robot_command_publishers'] = {topic: [info.node_name for info in node.get_publishers_info_by_topic(topic)]
            for topic in ('/api/sport/request', '/cmd_vel', '/lowcmd')}
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        node.destroy_node()
        rclpy.shutdown()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
        print(json.dumps({k: v for k, v in report.items() if k != 'samples'}, ensure_ascii=False), flush=True)
    return 0 if report['completed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Enable one supervised real motion session')
    parser.add_argument('--onsite-confirmed', action='store_true', help='Operator checked prior stance incidents and has the remote ready')
    parser.add_argument('--forward-only', action='store_true', help='At most 8 seconds: stop instead of choosing a turn, for step calibration')
    parser.add_argument('--radius', type=float, default=1.)
    parser.add_argument('--speed', type=float, default=.08)
    parser.add_argument('--duration', type=float, default=30.)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    Limits(radius=args.radius, speed=args.speed)
    if not 5 <= number(args.duration) <= 60:
        parser.error('Duration must be 5..60 seconds')
    if args.forward_only and args.duration > 8:
        parser.error('Forward-only calibration duration must be 5..8 seconds')
    if args.execute and not args.onsite_confirmed:
        parser.error('Prior stance incidents require onsite inspection and --onsite-confirmed')
    if args.output is None:
        args.output = BASE / 'runtime/autonomy' / (time.strftime('%Y%m%d-%H%M%S') + ('-execute.json' if args.execute else '-preview.json'))
    if args.output.exists():
        parser.error('Choose a new evidence path; existing reports are preserved')
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
