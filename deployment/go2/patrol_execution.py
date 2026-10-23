"""Cancellable route rehearsals composed with the existing PatrolRunner.

No planner, ROS node, SDK or goal is initialized by import or construction.
Nav2Navigator accepts only an explicitly supplied action adapter:
send_goal(point) -> handle; handle.wait_result() -> ROS GoalStatus;
handle.cancel() -> accepted bool; optional adapter.wait_stable() -> bool.
A cancel acknowledgement waits for the actual terminal goal result. Production
motion remains held elsewhere; these endpoints only create synthetic adapters.
"""
import asyncio
from contextlib import suppress
from copy import deepcopy
import json
import math
from pathlib import Path
import re
import time
from typing import Literal
import uuid

from fastapi import HTTPException
from pydantic import Field
from person_patrol import PatrolRunner, SearchRoute, StrictModel

BUSY = {'preparing','navigating','settling','observing','cancelling'}
TERMINAL_RESULTS = (4,5,6,'succeeded','canceled','aborted')
SCENARIOS = ('not_found','found','navigation_failed')
LABELS = {'idle':'尚未演练。','preparing':'正在准备模拟任务。','navigating':'模拟前往观察点。',
          'settling':'模拟到达后等待停稳。','observing':'停稳后观察模拟证据。',
          'cancelling':'正在等待导航结束确认。','found':'模拟候选已连续确认，演练结束。',
          'not_found':'所有观察点演练完成，未发现模拟候选。','cancelled':'演练已取消。',
          'interrupted':'上次演练已中断，不会自动恢复。'}


def _consume(task):
    if not task.cancelled():
        task.exception()  # Python 3.8 wait_for can otherwise discard a late error.


class Nav2Navigator:
    """Nav2 action lifecycle adapter, never a default motion implementation."""
    def __init__(self, action_adapter, settle=None):
        if action_adapter is None:
            raise ValueError('必须显式提供隔离导航适配器。')
        self.adapter, self.settle_callback = action_adapter, settle
        self.sending = self.result = self.handle = None

    async def go(self, point):
        if self.sending is not None:
            raise RuntimeError('上一导航目标尚未结束。')
        self.sending = asyncio.create_task(self.adapter.send_goal(deepcopy(point)))
        self.handle = await asyncio.shield(self.sending)
        if self.handle is None:
            self.sending = None
            return False
        self.result = asyncio.create_task(self.handle.wait_result())
        result = await asyncio.shield(self.result)
        if result not in TERMINAL_RESULTS:
            raise RuntimeError('导航结果不是终态。')
        self.sending = self.result = self.handle = None
        return result in (4,'succeeded')

    async def settle(self, point):
        callback = self.settle_callback or getattr(self.adapter,'wait_stable',None)
        if callback is None:
            raise RuntimeError('停稳确认适配器未配置。')
        return await callback()

    async def cancel(self):
        if self.sending is not None and self.handle is None:
            self.handle = await asyncio.shield(self.sending)
        if self.handle is not None:
            if await self.handle.cancel() is not True:
                raise RuntimeError('导航取消未被接受。')
            if self.result is None:
                self.result = asyncio.create_task(self.handle.wait_result())
            if await asyncio.shield(self.result) not in TERMINAL_RESULTS:
                raise RuntimeError('导航取消尚未结束。')
        self.sending = self.result = self.handle = None
        return True


class SimulationNavigation:
    def __init__(self, scenario, delay=.03):
        self.scenario, self.delay = scenario, delay

    async def go(self, point):
        await asyncio.sleep(self.delay)
        return self.scenario != 'navigation_failed'

    async def settle(self, point):
        await asyncio.sleep(self.delay)
        return True

    async def cancel(self):
        await asyncio.sleep(0)
        return True


class SimulationObservation:
    def __init__(self, scenario):
        self.scenario = scenario

    async def observe(self, point, target_id):
        await asyncio.sleep(.01)
        return {'target_id':target_id, 'captured_at':time.time(), 'track_id':'synthetic-track',
                'suspected_target':self.scenario=='found', 'synthetic':True,
                'source':'simulation', 'identity_confirmed':False}


class _Navigation:
    def __init__(self, owner, execution_id, target_id, navigator):
        self.owner, self.execution_id, self.target_id, self.navigator = owner,execution_id,target_id,navigator

    async def go(self, point):
        asyncio.current_task().add_done_callback(_consume)
        owner = self.owner
        owner._check(self.execution_id,self.target_id)
        owner._emit(state='navigating',point=point['name'],index=owner.value['index']+1,step=True)
        before = time.monotonic()
        arrived = await self.navigator.go(point)
        owner._check(self.execution_id,self.target_id)
        if time.monotonic()-before >= owner.navigation_timeout:
            raise RuntimeError('导航超时，迟到结果已丢弃。')
        if arrived is not True:
            return False
        owner._emit(state='settling',step=True,step_state='arrived')
        owner._emit(state='settling',step=True)
        stable = await self.navigator.settle(point)
        owner._check(self.execution_id,self.target_id)
        if time.monotonic()-before >= owner.navigation_timeout:
            raise RuntimeError('到达后停稳确认超时。')
        if stable is not True:
            raise RuntimeError('未确认停稳，禁止观察或前往下一点。')
        return True

    async def cancel(self):
        owner = self.owner
        owner.cleanup = asyncio.create_task(self.navigator.cancel())
        owner.cleanup.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        deadline = time.monotonic()+owner.cancel_timeout
        try:
            try:
                confirmed = await asyncio.wait_for(asyncio.shield(owner.cleanup),owner.cancel_timeout)
            except asyncio.CancelledError:
                if owner.cleanup.cancelled():
                    raise RuntimeError('导航取消确认任务中断。')
                # A user cancellation during normal terminal cleanup still must
                # wait for that same navigation acknowledgement, not cancel it.
                confirmed = await asyncio.wait_for(asyncio.shield(owner.cleanup),
                    max(.001,deadline-time.monotonic()))
            if confirmed is not True:
                raise RuntimeError('导航未确认结束。')
        except asyncio.CancelledError as exc:
            owner.cleanup_failed = True
            owner.cleanup.cancel()
            raise RuntimeError('导航取消未确认，保持阻断。') from exc
        except Exception as exc:
            owner.cleanup_failed = True
            owner.cleanup.cancel()
            if isinstance(exc,asyncio.TimeoutError):
                raise RuntimeError('导航取消确认超时，保持阻断。') from exc
            raise


class _Observation:
    def __init__(self, owner, execution_id, target_id, observer):
        self.owner, self.execution_id, self.target_id, self.observer = owner,execution_id,target_id,observer

    async def observe(self, point, target_id):
        asyncio.current_task().add_done_callback(_consume)
        owner = self.owner
        owner._check(self.execution_id,self.target_id)
        owner._emit(state='observing',step=True)
        after, before = time.time(),time.monotonic()
        until = before+owner.observation_seconds
        stamps, track, saw_fresh = [],None,False
        while time.monotonic() < until:
            owner._check(self.execution_id,self.target_id)
            result = await self.observer.observe(point,target_id)
            owner._check(self.execution_id,self.target_id)
            if time.monotonic()-before >= owner.observation_timeout:
                raise RuntimeError('观察超时，迟到结果已丢弃。')
            if result is None:
                stamps,track = [],None
            else:
                if not isinstance(result,dict) or result.get('target_id') != target_id:
                    raise RuntimeError('观察目标已更换，原演练停止。')
                stamp = result.get('captured_at')
                now = time.time()
                if (isinstance(stamp,bool) or not isinstance(stamp,(int,float)) or not math.isfinite(stamp)
                        or not after <= stamp <= now or now-stamp > 2):
                    raise RuntimeError('观察证据已过期，原演练停止。')
                saw_fresh = True
                candidate_track = result.get('track_id')
                if candidate_track is None and result.get('candidates'):
                    candidate_track = result['candidates'][0].get('track_id')
                if not result.get('suspected_target') or candidate_track is None:
                    stamps,track = [],None
                else:
                    if track != candidate_track:
                        stamps = []
                    track = candidate_track
                    if stamp not in stamps:
                        stamps.append(stamp)
                    if len(stamps) >= owner.confirmation_frames:
                        return dict(result,synthetic=True,source='simulation',identity_confirmed=False,
                                    confirmation_frames=len(stamps))
            await asyncio.sleep(owner.tick_seconds)
        if not saw_fresh:
            raise RuntimeError('没有新鲜观察结果，原演练停止。')
        owner._emit(state='observing',step=True,step_state='not_found_at_point')
        return None


class ExecutionController:
    def __init__(self, directory, update=None, navigator_factory=None, observer_factory=None,
                 target_provider=None, navigation_timeout=5, observation_timeout=2,
                 observation_seconds=.15, tick_seconds=.01, confirmation_frames=3, cancel_timeout=2):
        self.directory = Path(directory)
        self.path = self.directory/'last_execution.json'
        self.update = update
        self.navigator_factory = navigator_factory or SimulationNavigation
        self.observer_factory = observer_factory or SimulationObservation
        self.target_provider = target_provider
        self.navigation_timeout,self.observation_timeout = navigation_timeout,observation_timeout
        self.observation_seconds,self.tick_seconds = observation_seconds,tick_seconds
        self.confirmation_frames,self.cancel_timeout = confirmation_frames,cancel_timeout
        self.lock = asyncio.Lock()
        self.task = self.cleanup = None
        self.stop_event = asyncio.Event()
        self.cleanup_failed = self.cancel_requested = False
        self.failure_reason = None
        self.value = {'id':None,'state':'idle','mode':'simulation','route_id':None,'target_id':None,
                      'index':0,'total':0,'point':None,'steps':[],'error':None,'found':False,
                      'started_at':None,'completed_at':None}
        if self.path.exists():
            restored = json.loads(self.path.read_text())
            if not isinstance(restored,dict):
                raise ValueError('上次演练记录无效。')
            self.value.update(restored)
            self.cleanup_failed = self.value['state']=='cancel_failed'
            if self.value['state'] in BUSY or self.value['state'] in ('arrived','not_found_at_point'):
                self.value.update(state='interrupted',error='进程重启，上次演练中断；不会自动恢复。',completed_at=time.time())
                self._save()

    def status(self):
        return deepcopy(dict(self.value,active=self.value['state'] in BUSY,simulation=True,
            physical_execution=False,motion_enabled=False,automatic_resume=False,
            reason=self.value['error'] or LABELS.get(self.value['state'],'模拟任务处理中。')))

    def _save(self):
        from go2_platform_write import backup
        backup(self.path)
        self.directory.mkdir(parents=True,exist_ok=True)
        temporary = self.path.with_suffix('.tmp.json')
        temporary.write_text(json.dumps(self.status(),ensure_ascii=False,indent=2)+'\n')
        temporary.replace(self.path)

    def _emit(self, step=False, step_state=None, **changes):
        self.value.update(changes)
        if step:
            self.value['steps'].append({'point':self.value['point'],'state':step_state or self.value['state']})
        self._save()
        if self.update:
            self.update(self.status())

    def _check(self, execution_id, target_id):
        if self.value['id'] != execution_id or self.stop_event.is_set():
            raise asyncio.CancelledError()
        if self.target_provider and self.target_provider() != target_id:
            raise RuntimeError('目标已更换，原演练停止。')

    async def _watch_target(self, execution_id, target_id):
        while True:
            await asyncio.sleep(self.tick_seconds)
            if self.value['id'] != execution_id:
                return
            if self.target_provider and self.target_provider() != target_id:
                self.failure_reason = '目标已更换，原演练停止。'
                self.stop_event.set()
                if not self.cancel_requested:
                    self.cancel_requested = True
                    self.task.cancel()
                return

    async def start(self, route, target_id, scenario='not_found'):
        async with self.lock:
            if self.task is not None and not self.task.done():
                raise HTTPException(409,'已有演练在运行或等待取消确认。')
            if self.cleanup_failed:
                raise HTTPException(409,'上次导航结束未确认，保持阻断。')
            if scenario not in SCENARIOS or not isinstance(target_id,str) or not target_id.strip():
                raise ValueError('模拟场景或目标标识无效。')
            if not isinstance(route,dict) or not re.fullmatch(r'[0-9a-f]{32}',str(route.get('id',''))):
                raise ValueError('路线标识无效。')
            validated = SearchRoute(**{key:route[key] for key in ('name','frame_id','points')})
            route = dict(validated.model_dump(),id=route['id'])
            if self.target_provider and self.target_provider() != target_id:
                raise HTTPException(409,'目标已更换，请重新开始演练。')
            self.stop_event = asyncio.Event()
            self.cancel_requested = self.cleanup_failed = False
            self.failure_reason = None
            self.value = dict(id=uuid.uuid4().hex,state='preparing',mode='simulation',route_id=route['id'],
                target_id=target_id,index=0,total=len(route['points']),point=None,steps=[],error=None,
                found=False,started_at=time.time(),completed_at=None,scenario=scenario)
            self._emit()
            navigator = _Navigation(self,self.value['id'],target_id,self.navigator_factory(scenario))
            observer = _Observation(self,self.value['id'],target_id,self.observer_factory(scenario))
            runner = PatrolRunner(navigator,observer,target_id,self.navigation_timeout,self.observation_timeout)
            entered = asyncio.Event()
            self.task = asyncio.create_task(self._run(runner,route,entered))
            await entered.wait()  # Cancellation cannot bypass the runner's cleanup finally.
            return self.status()

    async def _run(self, runner, route, entered):
        watcher = None
        entered.set()
        try:
            if self.target_provider:
                watcher = asyncio.create_task(self._watch_target(self.value['id'],self.value['target_id']))
            report = await runner.run(route)
            if self.stop_event.is_set():
                raise asyncio.CancelledError()
            if report['ok']:
                self.value.update(state='found' if report['found'] else 'not_found',found=report['found'])
                if report.get('evidence'):
                    self.value['evidence'] = dict(report['evidence'],synthetic=True,source='simulation')
            else:
                error = report.get('error','演练未完成。')
                if error=='TimeoutError':
                    error='导航或观察超时，演练已停止。'
                self.value.update(state='failed',error=error)
        except asyncio.CancelledError:
            self.value.update(state='failed' if self.failure_reason else 'cancelled',
                              error=self.failure_reason or '任务已取消。')
        except Exception as exc:
            self.value.update(state='cancel_failed' if self.cleanup_failed else 'failed',
                              error=str(exc) or type(exc).__name__)
        finally:
            if watcher:
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher
            self.value['completed_at'] = time.time()
            self.value['steps'].append({'point':self.value['point'],'state':self.value['state']})
            self._emit()

    async def cancel(self):
        async with self.lock:
            task = self.task
            if task is None or task.done():
                return self.status()
            if not self.cancel_requested:
                self.cancel_requested = True
                self.stop_event.set()
                self._emit(state='cancelling')
                task.cancel()
        await asyncio.shield(task)
        return self.status()

    async def wait(self):
        if self.task is not None:
            await asyncio.shield(self.task)
        return self.status()


class ExecutionRequest(StrictModel):
    route_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    scenario: Literal['not_found','found','navigation_failed'] = 'not_found'


def attach_execution(app, preparation, searcher=None):
    provider = (lambda:(searcher.target_info or {}).get('id') or 'simulation-only') if searcher is not None else None
    controller = ExecutionController(preparation.store.directory,target_provider=provider)

    @app.get('/patrol/execution/status')
    async def execution_status():
        return controller.status()

    @app.post('/patrol/execution/rehearse',status_code=202)
    async def execution_rehearse(payload: ExecutionRequest):
        target_id = provider() if provider else 'simulation-only'
        return await controller.start(preparation.store.get(payload.route_id),target_id,payload.scenario)

    @app.post('/patrol/execution/cancel')
    async def execution_cancel():
        return await controller.cancel()

    return controller
