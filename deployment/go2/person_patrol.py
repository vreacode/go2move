"""Search-route preparation and cancellable orchestration, reusing Nav2 planning.

The HTTP API offers route storage, ComputePathToPose previews and explicit software
rehearsals only. It has no robot, velocity publisher or live navigation execution.
"""
import asyncio
import json
import math
from pathlib import Path
import threading
import time
import uuid
from typing import List

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from fastapi.responses import Response

BASE = Path(__file__).resolve().parent
BLOCKED = '实机巡航暂停：左后髋驱动过热报警尚未解除，导航与原生避障联动仍待验证。'


class StrictModel(BaseModel):
    model_config = {'extra': 'forbid', 'allow_inf_nan': False}


class SearchPoint(StrictModel):
    name: str = Field(min_length=1, max_length=24)
    x: float = Field(ge=-1000, le=1000)
    y: float = Field(ge=-1000, le=1000)
    yaw: float = Field(ge=-math.pi, le=math.pi)

    @field_validator('name')
    @classmethod
    def named(cls, value):
        if not value.strip():
            raise ValueError('观察点名称不能为空。')
        return value.strip()


class SearchRoute(StrictModel):
    name: str = Field(min_length=1, max_length=32)
    frame_id: str = 'map'
    points: List[SearchPoint] = Field(min_length=1, max_length=12)

    @field_validator('name')
    @classmethod
    def named(cls, value):
        if not value.strip():
            raise ValueError('区域名称不能为空。')
        return value.strip()

    @field_validator('frame_id')
    @classmethod
    def map_only(cls, value):
        if value != 'map':
            raise ValueError('观察点使用地图坐标。')
        return value

    @field_validator('points')
    @classmethod
    def distinct(cls, points):
        if len({point.name for point in points}) != len(points):
            raise ValueError('观察点名称不能重复。')
        if len({(point.x, point.y, point.yaw) for point in points}) != len(points):
            raise ValueError('观察点位置与朝向不能完全重复。')
        return points


class RouteRequest(StrictModel):
    route_id: str = Field(pattern=r'^[0-9a-f]{32}$')


class SuggestedRouteRequest(StrictModel):
    map_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$')
    count: int = Field(default=3, ge=1, le=6)
    radius_m: float = Field(default=2.0, ge=0.5, le=5.0)


class RouteStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory/'routes.json'
        self.lock = threading.RLock()
        self.last_report = None
        report_path = self.directory/'last_report.json'
        if report_path.exists():
            self.last_report = json.loads(report_path.read_text())

    def routes(self):
        with self.lock:
            if not self.path.exists():
                return []
            value = json.loads(self.path.read_text())
            if not isinstance(value, list):
                raise ValueError('路线文件无效，请保留文件后检查。')
            return value

    def save(self, route, generation=None, warning=None):
        from go2_platform_write import backup
        with self.lock:
            routes = self.routes()
            if len(routes) >= 20:
                raise ValueError('最多保存20条路线。')
            item = dict(route.model_dump(), id=uuid.uuid4().hex, saved_at=time.time())
            if generation is not None:
                item.update(generation=generation, warning=warning, motion_enabled=False)
            backup(self.path)
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp.json')
            temporary.write_text(json.dumps(routes+[item], ensure_ascii=False, indent=2)+'\n')
            temporary.replace(self.path)
            return item

    def get(self, route_id):
        for route in self.routes():
            if route['id'] == route_id:
                return route
        raise HTTPException(status_code=404, detail='路线不存在，请先保存。')

    def report(self, value):
        from go2_platform_write import backup
        with self.lock:
            path = self.directory/'last_report.json'
            backup(path)
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp.json')
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
            temporary.replace(path)
            self.last_report = value
        return value


class PatrolRunner:
    """Sequential arrival/observation decisions; navigation supplied by an adapter.

    Arrival must succeed before observation, and evidence must be fresh and belong
    to the original target. No graph, planner, detector or tracker is reinvented.
    """
    def __init__(self, navigator, observer, target_id, navigation_timeout=60, observation_timeout=10):
        self.navigator, self.observer = navigator, observer
        self.target_id = target_id
        self.navigation_timeout = navigation_timeout
        self.observation_timeout = observation_timeout
        self.steps = []

    async def run(self, route):
        report = {'mode':'simulation', 'ok':False, 'found':False, 'motion_enabled':False,
                  'route_id':route['id'], 'started_at':time.time(), 'steps':self.steps,
                  'evidence_source':'simulation', 'physical_execution':False}
        try:
            for point in route['points']:
                self.steps.append({'point':point['name'], 'state':'navigating'})
                arrived = await asyncio.wait_for(self.navigator.go(point), self.navigation_timeout)
                if not arrived:
                    raise RuntimeError('未到达观察点：'+point['name'])
                self.steps.append({'point':point['name'], 'state':'arrived'})
                observed_after = time.time()
                self.steps.append({'point':point['name'], 'state':'observing'})
                result = await asyncio.wait_for(self.observer.observe(point, self.target_id), self.observation_timeout)
                if result and result.get('target_id') != self.target_id:
                    raise RuntimeError('目标已更换，原任务停止。')
                if result and result.get('suspected_target'):
                    stamp = result.get('captured_at', 0)
                    if not observed_after <= stamp <= time.time()+.1 or time.time()-stamp > 2:
                        raise RuntimeError('观察结果已过期，不能作为发现依据。')
                    self.steps.append({'point':point['name'], 'state':'found'})
                    report.update(ok=True, found=True, evidence=result)
                    break
                self.steps.append({'point':point['name'], 'state':'not_found'})
            else:
                report['ok'] = True
        except asyncio.CancelledError:
            report['error'] = '任务已取消。'
            self.steps.append({'point':'', 'state':'cancelled'})
            raise
        except Exception as exc:
            report['error'] = str(exc) or type(exc).__name__
            self.steps.append({'point':'', 'state':'failed'})
        finally:
            await self.navigator.cancel()
            report['completed_at'] = time.time()
        return report


class RehearsalNavigation:
    async def go(self, point):
        await asyncio.sleep(0)
        return True

    async def cancel(self):
        pass


class RehearsalObservation:
    async def observe(self, point, target_id):
        await asyncio.sleep(0)
        return None  # No fake person, no image and no discovery-history mutation.


class PatrolPreparation:
    def __init__(self, directory):
        self.store = RouteStore(directory)
        self.preview_lock = asyncio.Lock()
        self._maps = None

    @property
    def maps(self):
        if self._maps is None:
            from saved_map_routes import SavedMapRoutes
            self._maps = SavedMapRoutes()
        return self._maps

    def suggest(self, payload):
        candidate = self.maps.suggest(payload.map_id, payload.count, payload.radius_m)
        route = SearchRoute.model_validate({key: candidate[key] for key in ('name', 'frame_id', 'points')})
        return self.store.save(route, candidate['generation'], candidate.get('warning'))

    def status(self):
        return {'routes':self.store.routes(), 'last_report':self.store.last_report,
                'motion_enabled':False, 'live_ready':False, 'blocked_reason':BLOCKED,
                'planner':'Nav2 Foxy ComputePathToPose', 'frame_id':'map'}

    async def preview(self, route_id):
        route = self.store.get(route_id)
        if self.preview_lock.locked():
            raise HTTPException(status_code=409, detail='正在检查观察点，请稍候。')
        async with self.preview_lock:
            # Constant shell text; user coordinates travel via stdin, never shell interpolation.
            command = ('source /home/unitree/go2move/deployment/go2/source_go2_platform.sh\n'
                       'export CYCLONEDDS_URI=/home/unitree/go2move/ros2/go2_bringup/config/dds/test.xml\n'
                       'exec /usr/bin/python3 /home/unitree/go2move/deployment/go2/preview_search_route.py')
            process = await asyncio.create_subprocess_exec('/bin/bash','-c',command,
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            try:
                payload = json.dumps({'frame_id':route['frame_id'], 'points':route['points']}).encode()
                stdout, stderr = await asyncio.wait_for(process.communicate(payload), timeout=35)
                lines = stdout.decode(errors='replace').splitlines()
                result = next((json.loads(line) for line in reversed(lines) if line.startswith('{')), None)
                if not isinstance(result, dict):
                    raise RuntimeError('导航规划服务尚未就绪，请先准备地图与定位。')
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                result = {'ok':False, 'segments':[], 'error':str(exc) or type(exc).__name__}
            finally:
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=2)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
            messages = {
                'map_unavailable':'地图尚未就绪，请先完成地图与定位准备。',
                'costmap_unavailable':'导航障碍地图尚未就绪。',
                'tf_unavailable':'尚无当前定位，暂不能检查观察点。',
                'tf_stale':'定位数据已过期，请恢复定位后重试。',
                'planner_unavailable':'导航规划服务尚未启动。',
                'planner_inactive':'导航规划服务尚未就绪。',
                'goal_outside_map':'观察点在地图范围之外。',
                'goal_not_free':'观察点处于障碍或未知区域。',
                'timeout':'观察点检查超时，请检查导航服务。',
                'planning_failed':'暂未找到到达观察点的有效路径。',
            }
            result['ok'] = bool(result.get('ok', result.get('passed', False)))
            for item in [result]+result.get('segments', []):
                if item is not result:
                    item['ok'] = bool(item.get('ok', item.get('success', False)))
                if isinstance(item.get('error'), dict):
                    detail = item['error']
                    item['error_code'] = detail.get('code')
                    item['error'] = messages.get(detail.get('code'), '观察点检查失败，请检查地图与定位。')
            result.update(mode='planning', route_id=route_id, motion_enabled=False,
                          ordered_route_validated=False, completed_at=time.time())
            return self.store.report(result)

    async def rehearse(self, route_id):
        route = self.store.get(route_id)
        runner = PatrolRunner(RehearsalNavigation(), RehearsalObservation(), 'simulation-only')
        return self.store.report(await runner.run(route))


def attach_patrol(app):
    preparation = PatrolPreparation(BASE/'runtime/patrol')

    @app.get('/patrol/status')
    async def patrol_status():
        return preparation.status()

    @app.get('/patrol/maps')
    async def patrol_maps():
        return await asyncio.get_running_loop().run_in_executor(None, preparation.maps.list_maps)

    @app.get('/patrol/maps/{map_id}/preview.png')
    async def patrol_map_preview(map_id: str):
        try:
            content = await asyncio.get_running_loop().run_in_executor(None, preparation.maps.preview, map_id)
            return Response(content, media_type='image/png', headers={'Cache-Control':'no-store'})
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(400, detail=str(exc)) from exc

    @app.post('/patrol/suggest')
    async def patrol_suggest(payload: SuggestedRouteRequest):
        try:
            return await asyncio.get_running_loop().run_in_executor(None, preparation.suggest, payload)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(400, detail=str(exc)) from exc

    @app.post('/patrol/routes')
    async def patrol_save(payload: SearchRoute):
        try:
            return preparation.store.save(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post('/patrol/preview')
    async def patrol_preview(payload: RouteRequest):
        return await preparation.preview(payload.route_id)

    @app.post('/patrol/rehearse')
    async def patrol_rehearse(payload: RouteRequest):
        return await preparation.rehearse(payload.route_id)

    @app.post('/patrol/run')
    async def patrol_run(payload: RouteRequest):
        raise HTTPException(status_code=409, detail=BLOCKED)

    from patrol_execution import attach_execution
    preparation.execution = attach_execution(app, preparation)
    return preparation
