"""Bounded stationary search using the existing SDK2 worker and person pipeline.

No worker starts at import, GET, failed consent, or while scan_policy is held.
The separate SDK process enforces physical limits even if HTTP/vision stops.
Current deployment is held after a failed thermal precheck, not field validated.
"""
import asyncio
from contextlib import suppress
import json
import math
import os
from pathlib import Path
import time
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel

BASE = Path(__file__).resolve().parent
BUSY = {'checking', 'scanning', 'observing'}


def scan_policy():
    try:
        value = json.loads((BASE/'config/scan_policy.json').read_text())
        if not isinstance(value,dict):
            raise ValueError('invalid policy')
        value.setdefault('reason','扫描尚未完成现场验证。')
        enabled = value.get('enabled') is True and value.get('physical_validated') is True
        return dict(value, available=enabled)
    except (OSError, ValueError, TypeError):
        return {'available':False, 'physical_validated':False,
                'reason':'扫描许可未就绪，保持停止。', 'automatic_resume':False}


class ScanRequest(BaseModel):
    model_config = {'extra':'forbid'}
    confirmed: Literal[True]


class OfficialRotationProcess:
    """Only used after explicit start and policy checks; XY is always zero."""
    def __init__(self):
        self.process = None
        self.reader = None
        self.latest = {}

    async def open(self):
        env = os.environ.copy()
        env['CYCLONEDDS_HOME'] = '/home/unitree/cyclonedds_ws/install/cyclonedds'
        env['LD_LIBRARY_PATH'] = env['CYCLONEDDS_HOME']+'/lib:'+env.get('LD_LIBRARY_PATH','')
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        self.process = await asyncio.create_subprocess_exec(
            '/home/unitree/go2_sdk2_venv/bin/python', '-u', str(BASE/'official_sdk_worker.py'),
            '--armed', '--rotation-only', '--max-speed', '.10', env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        self.reader = asyncio.create_task(self._read())

    async def _read(self):
        while True:
            line = await self.process.stdout.readline()
            if not line:
                break
            try:
                value = json.loads(line)
                if 'worker_ready' in value:
                    self.latest = dict(value, at=time.monotonic())
            except (ValueError, TypeError):
                pass

    async def command(self, yaw):
        if not math.isfinite(yaw) or not 0 <= yaw <= .15:
            raise RuntimeError('扫描角速度超出限制。')
        if self.process is None or self.process.returncode is not None:
            raise RuntimeError('转动控制进程不可用。')
        self.process.stdin.write((json.dumps({'velocity':[0.,0.,yaw], 'valid':True})+'\n').encode())
        await asyncio.wait_for(self.process.stdin.drain(), .15)

    def snapshot(self):
        return dict(self.latest)

    async def close(self):
        if self.process is not None:
            # EOF invokes existing Move(0)/StopMove cleanup only if movement occurred.
            if self.process.stdin:
                self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 2)
            except asyncio.TimeoutError:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), 2)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
        if self.reader:
            self.reader.cancel()
            with suppress(asyncio.CancelledError):
                await self.reader


class ScanRunner:
    """Observe first, rotate 20 degrees at a time, stop and reacquire evidence.

    Targets are limited to 80 degrees; the worker stops at 85 degrees to retain
    margin beneath the user's 90-degree limit. This does not perform navigation.
    """
    def __init__(self, motion, observer, target_id, update, stop_event,
                 observation_seconds=4, settle_seconds=.6, tick_seconds=.05, max_seconds=45):
        self.motion, self.observer = motion, observer
        self.target_id, self.update, self.stop_event = target_id, update, stop_event
        self.observation_seconds, self.settle_seconds = observation_seconds, settle_seconds
        self.tick_seconds, self.max_seconds = tick_seconds, max_seconds
        self.started = None
        self.commanded_motion = False

    def observation(self):
        value = self.observer()
        if not value.get('active'):
            raise RuntimeError('人物查找已停止。')
        if (value.get('target') or {}).get('id') != self.target_id:
            raise RuntimeError('目标已更换，扫描停止。')
        if not value.get('camera', {}).get('connected'):
            raise RuntimeError('摄像头画面中断，扫描停止。')
        if value.get('error'):
            raise RuntimeError('识别异常：'+str(value['error'])[:120])
        return value.get('last')

    async def tick(self, yaw=0., warmup=False):
        if self.stop_event.is_set():
            raise asyncio.CancelledError()
        if time.monotonic()-self.started >= self.max_seconds:
            raise RuntimeError('扫描超时，已停止。')
        last = self.observation()
        if yaw > 0 and (not last or not 0 <= time.time()-last.get('captured_at',0) <= 2):
            raise RuntimeError('识别结果未更新，扫描停止。')
        snapshot = self.motion.snapshot()
        if snapshot.get('latched_stop'):
            raise RuntimeError(snapshot.get('guard_reason') or '机身保护条件触发，已停止。')
        if not warmup:
            if not 0 <= time.monotonic()-snapshot.get('at',0) <= .25:
                raise RuntimeError('控制状态中断，扫描停止。')
            if not snapshot.get('worker_ready'):
                raise RuntimeError(snapshot.get('guard_reason') or '机身状态不满足试转条件。')
        await self.motion.command(yaw)
        self.commanded_motion = self.commanded_motion or yaw > 0
        progress = snapshot.get('rotation', {})
        self.update(angle_deg=round(progress.get('angle_deg',0),1),
                    displacement_m=progress.get('displacement_m',0),
                    motion_output_enabled=yaw > 0)
        await asyncio.sleep(self.tick_seconds)
        return snapshot

    def fresh_match(self, after):
        last = self.observation()
        return bool(last and last.get('target_id') == self.target_id and
                    last.get('suspected_target') and last.get('streak',0) >= 3 and
                    after <= last.get('captured_at',0) <= time.time() and
                    time.time()-last['captured_at'] <= 2)

    async def observe(self):
        self.update(state='observing', reason='已停下，正在辨认目标。')
        settling = time.monotonic()+self.settle_seconds
        while time.monotonic() < settling:
            await self.tick()
        after = time.time()
        # Reset the existing track confirmation window, without replacing the target.
        reset = getattr(self.observer, 'reset_confirmation', None)
        if reset:
            reset()
        until = time.monotonic()+self.observation_seconds
        stamps, track = [], None
        saw_fresh = False
        while time.monotonic() < until:
            await self.tick()
            last = self.observation()
            stamp = (last or {}).get('captured_at',0)
            if after <= stamp <= time.time() and time.time()-stamp <= 2:
                saw_fresh = True
                candidates = last.get('candidates') or []
                candidate_track = candidates[0].get('track_id') if candidates else None
                if not self.fresh_match(after) or candidate_track is None:
                    stamps, track = [], None
                elif stamp not in stamps:
                    if track != candidate_track:
                        stamps = []
                    track = candidate_track
                    stamps.append(stamp)
                    if len(stamps) >= 3:
                        return True
        if not saw_fresh:
            raise RuntimeError('未收到新的识别结果，扫描停止。')
        return False

    async def run(self):
        self.started = time.monotonic()
        outcome = {'state':'failed', 'reason':'扫描未完成。'}
        try:
            await self.motion.open()
            self.update(state='checking', reason='检查站姿、温度和手柄状态。')
            until = time.monotonic()+4
            ready_since = None
            while time.monotonic() < until:
                snapshot = await self.tick(warmup=True)
                if snapshot.get('worker_ready'):
                    ready_since = ready_since or time.monotonic()
                    if time.monotonic()-ready_since >= .5:
                        break
                else:
                    ready_since = None
                    if snapshot.get('guard_reason') and snapshot.get('native_low'):
                        raise RuntimeError(snapshot['guard_reason'])
            else:
                raise RuntimeError('机身状态预检未通过。')
            found = await self.observe()
            for goal in [20.,40.,60.,80.]:
                if found:
                    break
                self.update(state='scanning', reason='缓慢转动，随后停下识别。')
                leg_started = time.monotonic()
                leg_initial = self.motion.snapshot().get('rotation',{}).get('net_angle_deg',0)
                while True:
                    snapshot = await self.tick(.15)
                    progress = snapshot.get('rotation',{})
                    current = progress.get('net_angle_deg',0)
                    # Pause early if an existing multi-frame match appears mid-turn.
                    if current >= goal or self.fresh_match(time.time()-1):
                        break
                    if current < leg_initial-3:
                        raise RuntimeError('转动方向异常，已停止。')
                    if time.monotonic()-leg_started > 3 and current-leg_initial < 2:
                        raise RuntimeError('未检测到有效转动，已停止。')
                    if time.monotonic()-leg_started > 8:
                        raise RuntimeError('单段转动超时，已停止。')
                await self.tick()
                found = await self.observe()
            outcome = {'state':'found' if found else 'completed',
                       'reason':'发现疑似目标，已停下，请确认照片。' if found else '本轮扫描结束，当前范围未确认目标。',
                       'found':found}
        except asyncio.CancelledError:
            outcome = {'state':'stopped', 'reason':'扫描已停止。'}
        except Exception as exc:
            outcome = {'state':'failed', 'reason':str(exc)[:240]}
        finally:
            try:
                await self.motion.close()
            except Exception as exc:
                outcome = {'state':'failed','reason':'停止进程退出异常，请保持手柄接管：'+str(exc)[:120]}
            outcome.update(motion_output_enabled=False, commanded_motion=self.commanded_motion,
                           completed_at=time.time())
            self.update(**outcome)
        return outcome


class SearchObservation:
    def __init__(self, searcher):
        self.searcher = searcher

    def __call__(self):
        try:
            camera = json.loads((BASE/'runtime/native_camera/status.json').read_text())
            connected = bool(camera.get('running') and 0 <= time.time()-camera.get('captured_at',0) < 2)
        except (OSError,ValueError):
            connected = False
        with self.searcher.lock:
            return {'active':self.searcher.active, 'target':self.searcher.target_info,
                    'error':self.searcher.error, 'last':self.searcher.last,
                    'camera':{'connected':connected}}

    def reset_confirmation(self):
        with self.searcher.lock:
            self.searcher.streak = 0
            self.searcher.last = None
            self.searcher.last_track_id = None
            self.searcher.reset_tracker = True


class ScanController:
    def __init__(self, searcher, activate, motion_factory=OfficialRotationProcess,
                 policy=scan_policy, directory=None):
        self.searcher, self.activate = searcher, activate
        self.motion_factory, self.policy = motion_factory, policy
        self.directory = Path(directory) if directory else BASE/'runtime/scan'
        self.task = None
        self.starting = False
        self.stop_event = asyncio.Event()
        self.value = {'state':'idle', 'reason':'拍照记录目标后，可原地分段扫描。',
                      'angle_deg':0., 'max_angle_deg':90, 'motion_output_enabled':False}
        self.run_id = 0

    def status(self):
        policy = self.policy()
        value = dict(self.value, available=policy['available'],
                     physical_validated=bool(policy.get('physical_validated')),
                     max_angle_deg=90, automatic_resume=False, speak=False)
        if not policy['available'] and value['state'] not in BUSY:
            value.update(state='blocked', reason=policy['reason'])
        return value

    async def start(self, confirmed):
        if confirmed is not True:
            raise HTTPException(409, '请先确认现场条件。')
        policy = self.policy()
        if not policy['available']:
            raise HTTPException(409, policy['reason'])
        if self.starting or (self.task and not self.task.done()):
            raise HTTPException(409, '扫描进行中，请先停止。')
        state = self.searcher.status()
        if not state.get('models_ready') or not state.get('target'):
            raise HTTPException(409, '请先拍照记录目标，等待识别就绪。')
        if not state.get('camera',{}).get('connected'):
            raise HTTPException(409, '机身摄像头尚未就绪。')
        self.starting = True
        self.run_id += 1
        run_id = self.run_id
        self.stop_event = asyncio.Event()
        self.value = dict(state='checking', reason='正在检查，尚未转动。',angle_deg=0.,
                          max_angle_deg=90,motion_output_enabled=False,started_at=time.time())
        def update(**fields):
            if run_id == self.run_id:
                self.value.update(fields)
        async def execute():
            try:
                runner = ScanRunner(self.motion_factory(), SearchObservation(self.searcher),
                                    state['target']['id'], update, self.stop_event)
                result = await runner.run()
                from go2_platform_write import write
                write((self.directory/'last_report.json').resolve(),
                      json.dumps(dict(self.value,**result),ensure_ascii=False,indent=2)+'\n')
            except Exception as exc:
                update(state='failed',reason=str(exc)[:200],motion_output_enabled=False)
        try:
            await self.activate()
            if self.stop_event.is_set() or run_id != self.run_id:
                raise HTTPException(409, '扫描启动已取消。')
            if not self.policy()['available']:
                raise HTTPException(409, '扫描许可已撤销。')
            self.task = asyncio.create_task(execute())
        except Exception:
            if run_id == self.run_id:
                self.value.update(state='stopped',reason='扫描未启动。',motion_output_enabled=False)
            raise
        finally:
            self.starting = False
        return self.status()

    async def stop(self):
        self.stop_event.set()
        if self.starting:
            self.run_id += 1
            self.value.update(state='stopped',reason='扫描启动已取消。',motion_output_enabled=False)
        if self.task and not self.task.done():
            # Let the next 50ms tick enter cleanup. Cancellation during Popen could
            # orphan a child, so stop cooperatively with the SDK's independent watchdog.
            await asyncio.wait_for(asyncio.shield(self.task), 6)
        return self.status()


def attach_scan(app, searcher, activate):
    controller = ScanController(searcher, activate)

    @app.get('/scan/status')
    async def status():
        return controller.status()

    @app.post('/scan/start')
    async def start(payload: ScanRequest):
        return await controller.start(payload.confirmed)

    @app.post('/scan/stop')
    async def stop():
        return await controller.stop()

    return controller
