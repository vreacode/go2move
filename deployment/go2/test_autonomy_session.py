#!/usr/bin/env python3
"""Run the session controller against a synthetic ROS graph and fake worker.

These exercise subscriptions, readiness, command dispatch and shutdown together.
The real ROS and SDK modules are never imported.
"""
import json
import math
from pathlib import Path
import tempfile
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch

import run_local_autonomy as session


class SessionTests(unittest.TestCase):
    def simulate(self, execute=False, scenario='clear', posture=None, forward_only=False):
        clock, callbacks, workers = [1000.], {}, []
        def now(): return clock[0]
        def header(frame):
            sec = int(now())
            return NS(frame_id=frame, stamp=NS(sec=sec,nanosec=int((now()-sec)*1e9)))
        class Node:
            def create_subscription(self, kind, topic, callback, qos):
                callbacks[topic] = callback
                return callback
            def get_clock(self): return NS(now=lambda: NS(nanoseconds=int(now()*1e9)))
            def get_publishers_info_by_topic(self, topic): return []
            def destroy_node(self): pass
        class Worker:
            def __init__(self, limits, duration, forward_only=False):
                self.sent, self.closed = [], False
                self.forward_only = forward_only
                workers.append(self)
            def snapshot(self):
                return dict(worker_ready=True,latched_stop=scenario=='native_fault' and now()>1003.,
                    native_low=dict(foot_force=[40]*4,joint_q=[.5]*12),
                    received_at=1002.5 if scenario=='native_stale' and now()>1003. else now())
            def send(self, velocity, valid=True): self.sent.append((now(),list(velocity),valid))
            def close(self):
                self.closed = True
                return dict(shutdown=True,stop_responses=[dict(code=1 if scenario=='stop_rejected' else 0,zero_move_code=0,errors=[])]*3)
        def spin_once(node, timeout_sec):
            clock[0] += .02
            # All fake sensors describe a stationary robot in a populated room.
            if not (scenario=='scan_stale' and now()>1003.):
                ranges = [4.]*720
                if scenario=='close_obstacle' and now()>1003.:
                    ranges[360] = .60
                if scenario=='front_obstacle' and now()>1003.:
                    ranges[360] = .85
                callbacks['/scan'](NS(header=header('base_footprint'),ranges=ranges,
                    angle_min=-math.pi,angle_increment=math.pi/360,range_min=.45,range_max=20.))
            callbacks['/odom'](NS(header=header('odom'),child_frame_id='base_footprint',
                pose=NS(pose=NS(position=NS(x=0.,y=0.,z=0.),orientation=NS(x=0.,y=0.,z=0.,w=1.))),
                twist=NS(twist=NS(linear=NS(x=0.,y=0.),angular=NS(z=0.)))))
            callbacks['/lidar_points'](NS(header=header('hesai_lidar'),width=128,height=1,
                fields=[NS(name=n) for n in ('x','y','z')],point_step=12,row_step=1536,data=bytes(1536)))
        rclpy = ModuleType('rclpy')
        rclpy.init = lambda: None
        rclpy.shutdown = lambda: None
        rclpy.create_node = lambda name: Node()
        rclpy.spin_once = spin_once
        modules = {'rclpy':rclpy}
        for name in ('rclpy.qos','nav_msgs','nav_msgs.msg','sensor_msgs','sensor_msgs.msg'):
            modules[name] = ModuleType(name)
        modules['rclpy.qos'].qos_profile_sensor_data = object()
        modules['nav_msgs.msg'].Odometry = object
        modules['sensor_msgs.msg'].LaserScan = object
        modules['sensor_msgs.msg'].PointCloud2 = object
        with tempfile.TemporaryDirectory(prefix='go2-session-fake-') as directory:
            args = NS(execute=execute,radius=1.,speed=.08,duration=5.,forward_only=forward_only,output=Path(directory)/'report.json')
            with patch.dict('sys.modules',modules), patch.object(session.time,'monotonic',now), \
                 patch.object(session.time,'time',now), patch.object(session,'posture_hold',return_value=posture), \
                 patch.object(session,'OfficialWorker',Worker), patch('builtins.print'):
                result = session.run(args)
            report = json.loads(args.output.read_text())
        return result,report,workers

    def test_preview_collects_without_constructing_motion_worker(self):
        code,report,workers = self.simulate()
        self.assertEqual(0,code)
        self.assertTrue(report['completed'])
        self.assertFalse(report['motion_started'])
        self.assertEqual([],workers)
        self.assertTrue(any(s['state']=='walking' for s in report['samples']))

    def test_execute_waits_for_preflight_then_closes_worker(self):
        code,report,workers = self.simulate(execute=True)
        self.assertEqual(0,code)
        self.assertTrue(report['motion_started'])
        self.assertTrue(report['stop_acknowledged'])
        self.assertTrue(workers[0].closed)
        first_move = next(t for t,v,valid in workers[0].sent if any(v))
        self.assertGreaterEqual(first_move,1002.)
        self.assertTrue(any(s.get('native_motion',{}).get('native_low',{}).get('foot_force')==[40]*4
                            for s in report['samples']))

    def test_forward_only_closes_at_obstacle_without_sending_any_turn(self):
        code,report,workers = self.simulate(execute=True,scenario='front_obstacle',forward_only=True)
        self.assertEqual(1,code)
        self.assertIn('without turning',report['error'])
        self.assertTrue(report['forward_only'])
        self.assertTrue(workers[0].forward_only)
        self.assertTrue(workers[0].closed)
        self.assertTrue(any(v[0]>0 for t,v,valid in workers[0].sent))
        self.assertFalse(any(v[1] or v[2] for t,v,valid in workers[0].sent))

    def test_bad_posture_never_opens_worker(self):
        code,report,workers = self.simulate(execute=True,posture='Robot is prone')
        self.assertEqual(1,code)
        self.assertEqual([],workers)
        self.assertFalse(report['motion_started'])

    def test_health_preflight_requires_unlock_in_addition_to_standing_height(self):
        import health_status
        with tempfile.TemporaryDirectory(prefix='go2-health-fake-') as directory:
            path=Path(directory)/'health.json'
            raw=dict(height=.33,rpy=[0,0,0],temperatures=[40]*12,soc=80)
            with patch.object(health_status,'summarize_health',return_value=dict(connected=True,faults=[],system_faults=[])):
                for state_code in (1001,1002):
                    path.write_text(json.dumps(dict(raw,error_code=state_code)))
                    self.assertIn('START',session.posture_hold(path))
                path.write_text(json.dumps(dict(raw,error_code=100)))
                self.assertIsNone(session.posture_hold(path))

    def test_live_failure_always_closes_motion_session(self):
        for scenario in ('scan_stale','close_obstacle','native_fault','native_stale'):
            with self.subTest(scenario=scenario):
                code,report,workers = self.simulate(execute=True,scenario=scenario)
                self.assertEqual(1,code)
                self.assertTrue(report['error'])
                self.assertTrue(report['motion_started'])
                self.assertTrue(workers[0].closed)
                self.assertTrue(report['stop_acknowledged'])

    def test_stop_rejection_never_reports_completed(self):
        code,report,workers = self.simulate(execute=True,scenario='stop_rejected')
        self.assertEqual(1,code)
        self.assertFalse(report['completed'])
        self.assertFalse(report['stop_acknowledged'])
        self.assertTrue(report['stop_error'])

    def test_execute_requires_explicit_onsite_confirmation_before_ros(self):
        with patch('sys.argv',['run_local_autonomy.py','--execute']),patch('sys.stderr'),self.assertRaises(SystemExit) as raised:
            session.main()
        self.assertEqual(2,raised.exception.code)

    def test_forward_only_rejects_long_session_before_ros(self):
        with patch('sys.argv',['run_local_autonomy.py','--forward-only','--duration','9']),patch('sys.stderr'),self.assertRaises(SystemExit) as raised:
            session.main()
        self.assertEqual(2,raised.exception.code)


if __name__ == '__main__':
    unittest.main(verbosity=2)
