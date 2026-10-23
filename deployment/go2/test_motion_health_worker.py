#!/usr/bin/env python3
"""Run both real generic writers against fake transports in isolated processes."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from test_rotation_worker import FAKE_RUNNER

BASE = Path(__file__).resolve().parent

FAKE_ROS = r'''
for name in ['rclpy','rclpy.qos','unitree_api','unitree_api.msg','unitree_go','unitree_go.msg']:
    module(name)
ros=sys.modules['rclpy'];messages=sys.modules['unitree_api.msg']
class Request:
    def __init__(self):
        self.header=types.SimpleNamespace(identity=types.SimpleNamespace(id=0,api_id=0),
            policy=types.SimpleNamespace(noreply=False))
messages.Request=Request;messages.Response=type('Response',(),{})
sys.modules['unitree_go.msg'].SportModeState=types_module.SportModeState_
sys.modules['unitree_go.msg'].LowState=types_module.LowState_
sys.modules['rclpy.qos'].QoSProfile=lambda **kwargs:None
sys.modules['rclpy.qos'].ReliabilityPolicy=types.SimpleNamespace(RELIABLE=1)
class Publisher:
    def publish(self,message):
        api=message.header.identity.api_id
        if api==1008:
            value=json.loads(message.parameter)
            SportClient.Move(None,value['x'],value['y'],value['z'])
        elif api==1003:record('StopMove')
        else:record('forbidden',method=str(api))
    def get_subscription_count(self):return 1
class Node:
    def create_publisher(self,*args):return Publisher()
    def create_subscription(self,kind,topic,callback,*args):
        if 'state' in topic:
            callbacks.append((topic,callback));callback(sample(topic))
            if len(callbacks)==2:(root/'ready').touch()
        return object()
    def create_timer(self,period,callback):
        self.tick=callback;self.cancelled=False
        return types.SimpleNamespace(cancel=lambda:setattr(self,'cancelled',True))
    def destroy_node(self):pass
node=Node()
ros.init=lambda:None;ros.shutdown=lambda:None;ros.create_node=lambda *a,**kw:node
def spin_once(node,timeout_sec):
    refresh()
    if not node.cancelled:node.tick()
    time.sleep(.05)
ros.spin_once=spin_once
'''


class MotionHealthWorkerTests(unittest.TestCase):
    def run_worker(self, backend, scenario='healthy', phases=None):
        phases = phases if phases is not None else [([.08,0.,0.],.72)]
        with tempfile.TemporaryDirectory(prefix='go2-motion-health-fake-') as directory:
            root = Path(directory)
            for name in ('local_autonomy.py','autonomy_guard.py','rotation_guard.py','motion_health_guard.py'):
                if (BASE/name).exists():
                    (root/name).write_text((BASE/name).read_text())
            filename = 'official_sdk_worker.py' if backend=='sdk2' else 'low_speed_bridge.py'
            source = (BASE/filename).read_text().replace(
                '/home/unitree/go2move/deployment/go2/cyclonedds_go2.xml',str(root/'dds.xml'))
            source = source.replace('/tmp/go2-motion-writer.lock',str(root/'writer.lock'))
            if backend=='sdk2':
                self.assertEqual(1,source.count('  begin=time.monotonic()'))
                source=source.replace('  begin=time.monotonic()',
                                      '  channel._test_refresh()\n  begin=time.monotonic()')
            (root/filename).write_text(source)
            (root/'dds.xml').write_text('<CycloneDDS/>')
            bootstrap = FAKE_RUNNER.replace('error_code=100,',
                'error_code=1002 if scenario=="initial_locked" else 100,')
            bootstrap = bootstrap.replace('elapsed=0 if first_motion is None else time.monotonic()-first_motion',
                'elapsed=(time.monotonic()-float((root/"feed_started").read_text()) '
                'if scenario=="idle_fault" and (root/"feed_started").exists() else '
                '0 if first_motion is None else time.monotonic()-first_motion)')
            bootstrap = bootstrap.replace("hot=scenario=='temperature' and fault_window",
                "hot=scenario=='initial_hot' or (scenario=='temperature' and fault_window)")
            bootstrap = bootstrap.replace("faulty=scenario in ('motor_fault','observation_fault') and fault_window",
                "faulty=scenario=='initial_fault' or (scenario in ('motor_fault','observation_fault','idle_fault') and fault_window)")
            bootstrap = bootstrap.replace('wireless_remote=[0]*40)',
                'wireless_remote=([0,0,1]+[0]*37 if scenario=="remote" and fault_window else [0]*40),'
                'bit_flag=4,tick=int(time.monotonic()*1000))')
            bootstrap = bootstrap.replace('for topic,callback in callbacks:callback(sample(topic))',
                'for topic,callback in callbacks:\n'
                '        elapsed=0 if first_motion is None else time.monotonic()-first_motion\n'
                '        if scenario=="stale_low" and "lowstate" in topic and .12<elapsed<.60:continue\n'
                '        callback(sample(topic))')
            if backend=='ros2':
                bootstrap=bootstrap.replace('original_import=builtins.__import__',
                                            FAKE_ROS+'\noriginal_import=builtins.__import__')
                bootstrap=bootstrap.replace("str(root/'official_sdk_worker.py')", "str(root/'low_speed_bridge.py')")
            (root/'bootstrap.py').write_text(bootstrap)
            flags=['--armed','--max-speed','.10','--duration','2']
            if backend=='ros2':flags.append('--worker')
            process=subprocess.Popen([sys.executable,'-I','-B','-u',str(root/'bootstrap.py'),str(root),scenario,*flags],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,cwd=str(root))
            pipe=process.stdin;process.stdin=None
            def feed():
                try:
                    deadline=time.monotonic()+2
                    while not (root/'ready').exists():
                        if process.poll() is not None or time.monotonic()>deadline:return
                        time.sleep(.005)
                    (root/'feed_started').write_text(str(time.monotonic()))
                    for velocity,duration in phases:
                        deadline=time.monotonic()+duration
                        while time.monotonic()<deadline:
                            if velocity is not None:
                                pipe.write(json.dumps({'velocity':velocity,'valid':True})+'\n');pipe.flush()
                            time.sleep(.025)
                except (OSError,ValueError):pass
                finally:
                    try:pipe.close()
                    except (OSError,ValueError):pass
            feeder=threading.Thread(target=feed,daemon=True);feeder.start()
            try:stdout,stderr=process.communicate(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill();stdout,stderr=process.communicate()
                self.fail('Fake writer did not exit: '+stderr[-500:])
            finally:feeder.join(timeout=.5)
            statuses=[json.loads(line) for line in stdout.splitlines() if line.startswith('{')]
            calls_path=root/'calls.jsonl'
            calls=[json.loads(line) for line in calls_path.read_text().splitlines()] if calls_path.exists() else []
        self.assertEqual(0,process.returncode,stderr)
        self.assertFalse(any(c['action']=='forbidden' for c in calls))
        return statuses,calls

    def assert_stopped_and_latched(self,statuses,calls):
        self.assertTrue(any(c['action']=='Move' and any(c['velocity']) for c in calls))
        latched=[s for s in statuses if s.get('latched_stop')]
        self.assertTrue(latched,'An active fault must latch the writer')
        first=latched[0]['native_state']['at']
        self.assertFalse(any(c['action']=='Move' and any(c['velocity']) and c['at']>=first for c in calls))
        self.assertGreaterEqual(sum(c['action']=='StopMove' for c in calls),3)
        seen=False
        for status in statuses:
            seen=seen or status.get('latched_stop',False)
            if seen and 'worker_ready' in status:
                self.assertTrue(status['latched_stop']);self.assertFalse(status['worker_ready'])

    def test_healthy_motion_then_eof_stops_both_backends(self):
        for backend in ('sdk2','ros2'):
            with self.subTest(backend=backend):
                statuses,calls=self.run_worker(backend)
                self.assertTrue(any(s.get('worker_ready') for s in statuses))
                self.assertTrue(any(c['action']=='Move' and any(c['velocity']) for c in calls))
                self.assertGreaterEqual(sum(c['action']=='StopMove' for c in calls),3)
                stopped=min(c['at'] for c in calls if c['action']=='StopMove')
                self.assertFalse(any(c['action']=='Move' and any(c['velocity']) and c['at']>stopped for c in calls),
                                 'Reply draining after shutdown must not run the active motion timer')

    def test_temperature_fault_remote_and_stale_state_stop_without_resuming(self):
        for backend in ('sdk2','ros2'):
            for scenario in ('temperature','motor_fault','remote','stale_low'):
                with self.subTest(backend=backend,scenario=scenario):
                    statuses,calls=self.run_worker(backend,scenario)
                    self.assert_stopped_and_latched(statuses,calls)
                    self.assertTrue(any(s.get('guard_reason') for s in statuses))

    def test_hot_faulty_or_locked_robot_never_receives_a_motion_request(self):
        for backend in ('sdk2','ros2'):
            for scenario in ('initial_hot','initial_fault','initial_locked'):
                with self.subTest(backend=backend,scenario=scenario):
                    statuses,calls=self.run_worker(backend,scenario)
                    self.assertFalse(any(c['action'] in ('Move','StopMove') for c in calls))
                    self.assertFalse(any(s.get('worker_ready') for s in statuses))

    def test_fault_during_stationary_observation_cannot_resume(self):
        for backend in ('sdk2','ros2'):
            with self.subTest(backend=backend):
                self.assert_stopped_and_latched(*self.run_worker(backend,'observation_fault',[
                    ([.08,0,0],.18),([0,0,0],.48),([.08,0,0],.18)]))

    def test_fault_after_zero_command_readiness_blocks_the_first_movement(self):
        for backend in ('sdk2','ros2'):
            with self.subTest(backend=backend):
                statuses,calls=self.run_worker(backend,'idle_fault',[
                    ([0,0,0],.55),([.08,0,0],.20)])
                self.assertTrue(any(s.get('worker_ready') for s in statuses))
                self.assertTrue(any(s.get('latched_stop') for s in statuses))
                self.assertFalse(any(c['action'] in ('Move','StopMove') for c in calls))

    def test_command_timeout_cannot_resume_on_new_commands(self):
        for backend in ('sdk2','ros2'):
            with self.subTest(backend=backend):
                self.assert_stopped_and_latched(*self.run_worker(backend,phases=[
                    ([.08,0,0],.18),(None,.42),([.08,0,0],.18)]))

    def test_independent_deadline_stops_even_while_commands_and_sensors_stay_fresh(self):
        for backend in ('sdk2','ros2'):
            with self.subTest(backend=backend):
                statuses,calls=self.run_worker(backend,phases=[([.08,0,0],2.3)])
                self.assert_stopped_and_latched(statuses,calls)
                self.assertTrue(any('2秒' in (s.get('guard_reason') or '') for s in statuses))


if __name__=='__main__':unittest.main(verbosity=2)
