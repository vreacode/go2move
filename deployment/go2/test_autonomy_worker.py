#!/usr/bin/env python3
"""Execute the actual worker with a fake SDK; no robot transport is loaded."""
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


class AutonomyWorkerTests(unittest.TestCase):
    def run_worker(self, scenario='healthy', phases=None, extra=()):
        phases = phases if phases is not None else [([.08,0.,0.],.25)]
        with tempfile.TemporaryDirectory(prefix='go2-autonomy-fake-') as directory:
            root = Path(directory)
            for name in ('local_autonomy.py','autonomy_guard.py','rotation_guard.py'):
                (root/name).write_text((BASE/name).read_text())
            source = (BASE/'official_sdk_worker.py').read_text()
            source = source.replace('/home/unitree/go2move/deployment/go2/cyclonedds_go2.xml', str(root/'dds.xml'))
            source = source.replace('/tmp/go2-motion-writer.lock', str(root/'writer.lock'))
            source = source.replace('  begin=time.monotonic()', '  channel._test_refresh()\n  begin=time.monotonic()')
            (root/'official_sdk_worker.py').write_text(source)
            (root/'dds.xml').write_text('<CycloneDDS/>')
            bootstrap = FAKE_RUNNER.replace('position=[0.,0.,0.])',
                'position=[1. if scenario=="boundary" and fault_window else 0.,0.,0.],yaw_speed=0.)')
            bootstrap = bootstrap.replace('first_motion=None', 'first_motion=None;stop_seen=False;stop_at=None')
            bootstrap = bootstrap.replace('error_code=100,',
                'error_code=1002 if scenario=="state_transition" and not stop_seen else 100,')
            bootstrap = bootstrap.replace('velocity=[0.,0.,0.]',
                'velocity=[.06 if scenario in ("settling","never_settles") and stop_at is not None and (scenario=="never_settles" or time.monotonic()-stop_at<.35) else 0.,0.,0.]')
            bootstrap = bootstrap.replace('wireless_remote=[0]*40)',
                'wireless_remote=([0,0,1]+[0]*37 if scenario=="remote" and fault_window else [0]*40),'
                'bit_flag=4,tick=int(time.monotonic()*1000))')
            bootstrap = bootstrap.replace("def StopMove(self):record('StopMove');return 0",
                "def StopMove(self):\n        global stop_seen,stop_at\n        stop_seen=True\n        if stop_at is None:stop_at=time.monotonic()\n        record('StopMove');return 1 if scenario=='sync_rejected' else 0")
            (root/'bootstrap.py').write_text(bootstrap)
            process = subprocess.Popen([sys.executable,'-I','-B','-u',str(root/'bootstrap.py'),str(root),scenario,
                '--armed','--autonomy','--max-speed','.08','--radius','1','--duration','5',*extra],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,cwd=str(root))
            pipe = process.stdin
            process.stdin = None
            def feed():
                try:
                    deadline = time.monotonic()+2
                    while not (root/'ready').exists():
                        if process.poll() is not None or time.monotonic()>deadline:
                            return
                        time.sleep(.005)
                    # The real parent streams only zero commands until readiness.
                    for velocity, duration in [([0.,0.,0.],1.)]+phases:
                        deadline = time.monotonic()+duration
                        while time.monotonic()<deadline:
                            if velocity is not None:
                                pipe.write(json.dumps(dict(velocity=velocity,valid=True))+'\n')
                                pipe.flush()
                            time.sleep(.025)
                except (OSError,ValueError):
                    pass
                finally:
                    try: pipe.close()
                    except (OSError,ValueError): pass
            thread = threading.Thread(target=feed,daemon=True)
            thread.start()
            try:
                stdout, stderr = process.communicate(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
                self.fail('Fake worker hung: '+stderr)
            thread.join(timeout=.5)
            statuses = [json.loads(line) for line in stdout.splitlines() if line.startswith('{')]
            path = root/'calls.jsonl'
            calls = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        self.assertFalse(any(c['action']=='forbidden' for c in calls))
        for call in calls:
            if call['action']=='Move':
                x,y,w = call['velocity']
                self.assertTrue(0<=x<=.08 and y==0 and abs(w)<=.15 and not (x and w))
        return process.returncode, statuses, calls, stderr

    def assert_stopped(self, result):
        code, statuses, calls, stderr = result
        self.assertEqual(0,code,stderr)
        self.assertTrue(any(c['action']=='Move' and any(c['velocity']) for c in calls))
        self.assertTrue(statuses[-1].get('shutdown'))
        self.assertEqual(3,len(statuses[-1]['stop_responses']))
        self.assertGreaterEqual(sum(c['action']=='StopMove' for c in calls),3)

    def test_starts_then_eof_retries_both_stop_paths(self):
        self.assert_stopped(self.run_worker())

    def test_safe_left_and_right_stationary_turns(self):
        for yaw in (-.15,.15):
            with self.subTest(yaw=yaw):
                self.assert_stopped(self.run_worker(phases=[([0,0,yaw],.22)]))

    def test_bad_vectors_are_rejected_before_motion(self):
        for velocity in ([.081,0,0],[-.01,0,0],[0,.01,0],[0,0,.151],[.04,0,.1],[float('nan'),0,0]):
            with self.subTest(velocity=velocity):
                code,statuses,calls,stderr = self.run_worker(phases=[(velocity,.1)])
                self.assertEqual(0,code,stderr)
                self.assertFalse(any(c['action']=='Move' and any(c['velocity']) for c in calls))

    def test_fault_remote_boundary_timeout_latch_without_recovery(self):
        cases = [('temperature',[([.08,0,0],.7)]), ('motor_fault',[([.08,0,0],.7)]),
                 ('remote',[([.08,0,0],.7)]), ('boundary',[([.08,0,0],.7)]),
                 ('healthy',[([.08,0,0],.18),(None,.42),([.08,0,0],.18)])]
        for scenario,phases in cases:
            with self.subTest(scenario=scenario):
                result = self.run_worker(scenario,phases)
                self.assert_stopped(result)
                latched = [s for s in result[1] if s.get('latched_stop')]
                self.assertTrue(latched)
                first = latched[0]['native_state']['at']
                self.assertFalse(any(c['action']=='Move' and any(c['velocity']) and c['at']>=first for c in result[2]))

    def test_autonomy_forbids_gait_changes_before_sdk_import(self):
        code,statuses,calls,stderr = self.run_worker(phases=[],extra=('--continuous-gait',))
        self.assertNotEqual(0,code)
        self.assertEqual([],calls)

    def test_forward_only_accepts_forward_motion_but_rejects_turns(self):
        self.assert_stopped(self.run_worker(extra=('--forward-only',)))
        for yaw in (-.15,.15):
            code,statuses,calls,stderr=self.run_worker(phases=[([0,0,yaw],.1)],extra=('--forward-only',))
            self.assertEqual(0,code,stderr)
            self.assertFalse(any(c['action']=='Move' and any(c['velocity']) for c in calls))

    def test_forward_only_long_deadline_rejected_before_sdk_import(self):
        code,statuses,calls,stderr=self.run_worker(phases=[],extra=('--forward-only','--duration','9'))
        self.assertNotEqual(0,code)
        self.assertEqual([],calls)

    def test_stopmove_still_attempted_if_zero_move_raises(self):
        code,statuses,calls,stderr = self.run_worker('zero_move_exception')
        self.assertEqual(0,code,stderr)
        self.assertFalse(any(c['action']=='Move' and any(c['velocity']) for c in calls))
        self.assertTrue(any(c['action']=='StopMove' for c in calls))
        self.assertTrue(any(s.get('latched_stop') for s in statuses))

    def test_initial_stop_must_settle_before_any_nonzero_request(self):
        result = self.run_worker()
        self.assert_stopped(result)
        stops = [c['at'] for c in result[2] if c['action']=='StopMove']
        moves = [c['at'] for c in result[2] if c['action']=='Move' and any(c['velocity'])]
        self.assertGreaterEqual(min(moves)-min(stops),.75)
        self.assertTrue(any(s.get('autonomy_preparation',{}).get('synchronized') for s in result[1]))

    def test_initial_stop_rejection_never_allows_motion(self):
        code,statuses,calls,stderr = self.run_worker('sync_rejected')
        self.assertEqual(0,code,stderr)
        self.assertFalse(any(c['action']=='Move' and any(c['velocity']) for c in calls))
        self.assertTrue(any(s.get('latched_stop') for s in statuses))

    def test_locked_stand_never_receives_even_a_stop_preparation_command(self):
        result = self.run_worker('state_transition')
        self.assertEqual(0,result[0],result[3])
        self.assertFalse(any(c['action'] in ('Move','StopMove') for c in result[2]))
        self.assertFalse(any(s.get('worker_ready') for s in result[1]))
        self.assertTrue(any('START' in (s.get('guard_reason') or '') for s in result[1]))

    def test_brief_measured_settling_finishes_before_any_nonzero_move(self):
        result=self.run_worker('settling')
        self.assert_stopped(result)
        self.assertFalse(any(s.get('latched_stop') for s in result[1]))
        stops=[c['at']for c in result[2]if c['action']=='StopMove']
        moves=[c['at']for c in result[2]if c['action']=='Move' and any(c['velocity'])]
        self.assertGreaterEqual(min(moves)-min(stops),.80)

    def test_preparation_without_stationary_feedback_retries_stop_and_never_moves(self):
        code,statuses,calls,stderr=self.run_worker('never_settles',phases=[([0,0,0],2.5)])
        self.assertEqual(0,code,stderr)
        self.assertFalse(any(c['action']=='Move' and any(c['velocity'])for c in calls))
        self.assertGreaterEqual(sum(c['action']=='StopMove'for c in calls),4)
        self.assertTrue(statuses[-1].get('shutdown'))
        self.assertTrue(any(s.get('latched_stop')for s in statuses))


if __name__ == '__main__':
    unittest.main(verbosity=2)
