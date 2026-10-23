#!/usr/bin/env python3
"""Worker safety regressions in isolated child processes with an entirely fake SDK.

The worker and guard are copied to a TemporaryDirectory. Only the fixed XML path
is redirected and a deterministic fake-telemetry hook precedes each worker tick.
No real SDK import, DDS initialization, network, robot service or policy mutation.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

BASE = Path(__file__).resolve().parent

FAKE_RUNNER = r"""
import builtins,json,math,runpy,sys,time,types
from pathlib import Path
root=Path(sys.argv[1]);scenario=sys.argv[2];worker_args=sys.argv[3:]
log_path=root/'calls.jsonl';callbacks=[];first_motion=None

def record(action,**fields):
    with log_path.open('a') as stream:
        stream.write(json.dumps(dict(action=action,at=time.monotonic(),**fields))+'\n')

def module(name):
    item=types.ModuleType(name);item.__path__=[];sys.modules[name]=item
    if '.' in name:
        parent,child=name.rsplit('.',1);setattr(sys.modules[parent],child,item)
    return item

for name in ['unitree_sdk2py','unitree_sdk2py.core','unitree_sdk2py.core.channel',
             'unitree_sdk2py.idl','unitree_sdk2py.idl.unitree_go',
             'unitree_sdk2py.idl.unitree_go.msg','unitree_sdk2py.idl.unitree_go.msg.dds_',
             'unitree_sdk2py.go2','unitree_sdk2py.go2.sport',
             'unitree_sdk2py.go2.sport.sport_client']:
    module(name)
channel=sys.modules['unitree_sdk2py.core.channel']
types_module=sys.modules['unitree_sdk2py.idl.unitree_go.msg.dds_']
types_module.SportModeState_=type('FakeSportState',(),{})
types_module.LowState_=type('FakeLowState',(),{})

def sample(topic):
    elapsed=0 if first_motion is None else time.monotonic()-first_motion
    fault_window=(.12 <= elapsed < .36)
    if scenario=='observation_fault':fault_window=(.28 <= elapsed < .48)
    if 'sportmodestate' in topic:
        return types.SimpleNamespace(body_height=.30,imu_state=types.SimpleNamespace(rpy=[0.,0.,0.]),
            mode=1,gait_type=0,error_code=100,velocity=[0.,0.,0.],position=[0.,0.,0.])
    hot=scenario=='temperature' and fault_window
    faulty=scenario in ('motor_fault','observation_fault') and fault_window
    motors=[types.SimpleNamespace(q=0.,dq=0.,temperature=75 if hot else 40,lost=0,
            reserve=[4 if faulty and index==9 else 0,0]) for index in range(12)]
    return types.SimpleNamespace(foot_force=[20]*4,motor_state=motors,
        bms_state=types.SimpleNamespace(soc=80),power_v=30.,wireless_remote=[0]*40)

class Subscriber:
    def __init__(self,topic,message_type):self.topic=topic
    def Init(self,callback,depth):
        callbacks.append((self.topic,callback));callback(sample(self.topic))
        if len(callbacks)==2:(root/'ready').touch()
    def Close(self):record('subscriber_close',topic=self.topic)

def refresh():
    for topic,callback in callbacks:callback(sample(topic))
channel.ChannelSubscriber=Subscriber
channel.ChannelFactoryInitialize=lambda *args:record('channel_initialize')
channel._test_refresh=refresh

class SportClient:
    def __init__(self,*args):record('client_construct')
    def SetTimeout(self,*args):pass
    def Init(self):record('client_initialize')
    def Move(self,x,y,w):
        global first_motion
        record('Move',velocity=[x,y,w])
        if any(abs(value)>1e-8 for value in (x,y,w)):
            if first_motion is None:first_motion=time.monotonic()
            if scenario=='active_move_exception':raise RuntimeError('Synthetic active Move error')
        elif scenario=='zero_move_exception':
            raise RuntimeError('Synthetic zero Move error')
        return 0
    def StopMove(self):record('StopMove');return 0
    def __getattr__(self,name):
        def forbidden(*args,**kwargs):
            record('forbidden',method=name)
            raise AssertionError('Gait/posture/other API is forbidden: '+name)
        return forbidden
sys.modules['unitree_sdk2py.go2.sport.sport_client'].SportClient=SportClient
bridge=module('low_speed_bridge');bridge.LIMIT_V=.10

def bounded(value):
    if len(value)!=3 or not all(math.isfinite(float(item)) for item in value):raise ValueError('invalid command')
    x,y,w=map(float,value);norm=math.hypot(x,y)
    if norm>bridge.LIMIT_V:x*=bridge.LIMIT_V/norm;y*=bridge.LIMIT_V/norm
    return [x,y,max(-.20,min(.20,w))]
bridge.bounded=bounded
original_import=builtins.__import__

def safe_import(name,*args,**kwargs):
    if name.startswith('unitree_sdk2py'):
        record('sdk_import',name=name)
        if name not in sys.modules:raise AssertionError('Real SDK imports are forbidden')
    return original_import(name,*args,**kwargs)
builtins.__import__=safe_import
sys.path.insert(0,str(root));sys.argv=[str(root/'official_sdk_worker.py')]+worker_args
runpy.run_path(str(root/'official_sdk_worker.py'),run_name='__main__')
"""


class WorkerTests(unittest.TestCase):
    def run_worker(self,scenario='healthy',phases=None,enabled=True,validated=True,extra_args=()):
        phases=phases if phases is not None else [([0.,0.,.10],.22)]
        with tempfile.TemporaryDirectory(prefix='go2-worker-stub-') as directory:
            root=Path(directory)
            source=(BASE/'official_sdk_worker.py').read_text()
            xml='/home/unitree/go2move/deployment/go2/cyclonedds_go2.xml'
            self.assertEqual(1,source.count(xml),'Only redirect the fixed, read-only XML path')
            source=source.replace(xml,str(root/'stub.xml'))
            tick='  begin=time.monotonic()'
            self.assertEqual(1,source.count(tick),'Keep a single deterministic telemetry injection')
            source=source.replace(tick,'  channel._test_refresh()\n'+tick)
            (root/'official_sdk_worker.py').write_text(source)
            (root/'rotation_guard.py').write_text((BASE/'rotation_guard.py').read_text())
            (root/'stub.xml').write_text('<CycloneDDS/>')
            (root/'bootstrap.py').write_text(FAKE_RUNNER)
            (root/'config').mkdir()
            (root/'config/scan_policy.json').write_text(json.dumps({'enabled':enabled,'physical_validated':validated}))
            process=subprocess.Popen([sys.executable,'-I','-u',str(root/'bootstrap.py'),str(root),scenario,
                '--armed','--rotation-only',*extra_args],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,text=True,cwd=str(root))
            pipe=process.stdin
            process.stdin=None  # The feeder owns stdin; communicate only drains stdout/stderr.
            def feed():
                try:
                    deadline=time.monotonic()+2
                    while phases and not (root/'ready').exists():
                        if process.poll() is not None or time.monotonic()>deadline:return
                        time.sleep(.005)
                    for velocity,duration in phases:
                        deadline=time.monotonic()+duration
                        while time.monotonic()<deadline:
                            if velocity is not None:
                                pipe.write(json.dumps({'velocity':velocity,'valid':True})+'\n');pipe.flush()
                            time.sleep(.025)
                except (BrokenPipeError,ValueError):
                    pass
                finally:
                    try:pipe.close()
                    except (BrokenPipeError,ValueError):pass
            thread=threading.Thread(target=feed,daemon=True);thread.start()
            try:
                stdout,stderr=process.communicate(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill();stdout,stderr=process.communicate()
                self.fail('Synthetic worker did not exit: '+stderr[-400:])
            finally:
                thread.join(timeout=.5)
            path=root/'calls.jsonl'
            calls=[json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
            statuses=[json.loads(line) for line in stdout.splitlines() if line.startswith('{')]
        self.assertFalse(any(call['action']=='forbidden' for call in calls),'No gait or posture methods may run')
        for call in calls:
            if call['action']=='Move':
                self.assertEqual([0.,0.],call['velocity'][:2])
                self.assertLessEqual(call['velocity'][2],.15)
        return process.returncode,statuses,calls,stderr

    def assert_stopped(self,statuses,calls):
        moves=[call for call in calls if call['action']=='Move' and call['velocity'][2]>0]
        self.assertTrue(moves,'Fixture must first issue a fake nonzero request')
        self.assertGreaterEqual(sum(call['action']=='StopMove' for call in calls),3)
        self.assertTrue(statuses[-1].get('shutdown'))
        self.assertEqual(3,len(statuses[-1]['stop_responses']))

    def assert_latched_and_never_resumed(self,statuses,calls):
        latched=[status for status in statuses if status.get('latched_stop')]
        self.assertTrue(latched,'A post-motion failure must latch')
        first=latched[0]['native_state']['at']
        self.assertFalse(any(call['action']=='Move' and call['velocity'][2]>0 and call['at']>=first for call in calls))
        started=False
        for status in statuses:
            if status.get('latched_stop'):started=True
            if started and 'latched_stop' in status:
                self.assertTrue(status['latched_stop'])
                self.assertFalse(status['worker_ready'])

    def test_held_policy_exits_before_any_sdk_import_or_client(self):
        for enabled,validated in [(False,False),(False,True),(True,False)]:
            with self.subTest(enabled=enabled,validated=validated):
                code,statuses,calls,stderr=self.run_worker(phases=[],enabled=enabled,validated=validated)
                self.assertNotEqual(0,code)
                self.assertIn('Rotation policy is held',stderr)
                self.assertEqual([],calls)
                self.assertEqual([],statuses)

    def test_rotation_and_continuous_gait_flags_exit_before_sdk(self):
        code,statuses,calls,stderr=self.run_worker(phases=[],extra_args=('--continuous-gait',))
        self.assertNotEqual(0,code)
        self.assertIn('Rotation forbids gait or posture changes',stderr)
        self.assertEqual([],calls)

    def test_nonzero_xy_negative_or_large_yaw_and_nan_are_rejected(self):
        for velocity in [[.001,0.,.1],[0.,.001,.1],[0.,0.,-.001],[0.,0.,.15001],
                         [0.,0.,float('nan')]]:
            with self.subTest(velocity=velocity):
                code,statuses,calls,stderr=self.run_worker(phases=[(velocity,.10)])
                self.assertEqual(0,code,stderr)
                self.assertFalse(any(call['action']=='Move' for call in calls))
                self.assertFalse(any(status.get('commanded',[0,0,0])[2]>0 for status in statuses))

    def test_eof_after_motion_issues_three_stop_retries_and_exits(self):
        code,statuses,calls,stderr=self.run_worker()
        self.assertEqual(0,code,stderr)
        self.assert_stopped(statuses,calls)

    def test_command_timeout_stops_latches_and_rejects_fresh_recovery(self):
        code,statuses,calls,stderr=self.run_worker(phases=[([0.,0.,.1],.18),(None,.42),([0.,0.,.1],.18)])
        self.assertEqual(0,code,stderr)
        self.assert_stopped(statuses,calls)
        self.assert_latched_and_never_resumed(statuses,calls)

    def test_temperature_or_motor_fault_stops_and_stays_latched_after_recovery(self):
        for scenario,reason in [('temperature','温度'),('motor_fault','故障码')]:
            with self.subTest(scenario=scenario):
                code,statuses,calls,stderr=self.run_worker(scenario,phases=[([0.,0.,.1],.76)])
                self.assertEqual(0,code,stderr)
                self.assert_stopped(statuses,calls)
                self.assertTrue(any(reason in (status.get('guard_reason') or '') for status in statuses))
                self.assert_latched_and_never_resumed(statuses,calls)
                self.assertTrue(any(status.get('latched_stop') and status.get('guard_reason') is None for status in statuses),
                                'Synthetic telemetry must recover while the worker stays latched')

    def test_fault_while_observing_already_stopped_still_latches(self):
        code,statuses,calls,stderr=self.run_worker('observation_fault',phases=[
            ([0.,0.,.1],.18),([0.,0.,0.],.48),([0.,0.,.1],.18)])
        self.assertEqual(0,code,stderr)
        self.assert_stopped(statuses,calls)
        self.assert_latched_and_never_resumed(statuses,calls)
        self.assertTrue(any('故障码' in (status.get('guard_reason') or '') for status in statuses))

    def test_zero_move_exception_does_not_suppress_stopmove_retries(self):
        code,statuses,calls,stderr=self.run_worker('zero_move_exception')
        self.assertEqual(0,code,stderr)
        self.assert_stopped(statuses,calls)
        self.assertTrue(all('zero_move: RuntimeError' in result['errors'] for result in statuses[-1]['stop_responses']))

    def test_first_active_move_exception_still_attempts_stopmove(self):
        code,statuses,calls,stderr=self.run_worker('active_move_exception')
        self.assertNotEqual(0,code)
        self.assertIn('Synthetic active Move error',stderr)
        self.assert_stopped(statuses,calls)


if __name__=='__main__':
    unittest.main(verbosity=2)
