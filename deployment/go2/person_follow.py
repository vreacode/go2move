"""RGB-D person-follow controller in camera coordinates; preview only.

No SDK, ROS publisher, subprocess or robot RPC is present. The numerical command
is intentionally not wired to an actuator until mount TF, stopping distance and
obstacle/health interlocks pass supervised physical validation.
"""
import math
import time
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field


class FollowRequest(BaseModel):
    model_config = {'extra': 'forbid', 'allow_inf_nan': False}
    mode: Literal['preview'] = 'preview'
    distance_m: float = Field(default=1.8, ge=1.5, le=2.5)


class FollowController:
    MAX_AGE = .6  # Keep stale observations stopped; do not relax for slow inference.
    MAX_SECONDS = 120

    def __init__(self):
        self.active = False
        self.target_id = None
        self.track_id = None
        self.pending_track = None
        self.hits = 0
        self.last_stamp = None
        self.last_clock = None
        self.started_at = 0.
        self.distance_m = 1.8
        self.state = 'idle'
        self.reason = '尚未开始跟随预览。'
        self.command = [0., 0., 0.]
        self.depth_m = self.bearing_rad = self.observation_age_s = None

    def start(self, target_id, distance_m, now):
        if not target_id or not math.isfinite(now) or not math.isfinite(distance_m) or not 1.5 <= distance_m <= 2.5:
            raise ValueError('目标或跟随距离无效。')
        self.__init__()
        self.active, self.target_id = True, target_id
        self.started_at = self.last_clock = now
        self.distance_m = distance_m
        self.state, self.reason = 'acquiring', '等待同一目标的三帧新鲜 RGB-D 观测。'

    def stop(self, reason='用户停止跟随预览。'):
        self.active = False
        self.state, self.reason = 'stopped', reason
        self.command = [0., 0., 0.]
        self.depth_m = self.bearing_rad = None

    @staticmethod
    def number(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('跟随观测数值无效。')
        return float(value)

    def wait(self, reason):
        self.command = [0., 0., 0.]
        self.depth_m = self.bearing_rad = None
        if self.track_id is not None:
            self.stop(reason+' 请重新确认目标后启动。')
        else:
            self.hits, self.pending_track = 0, None
            self.state, self.reason = 'acquiring', reason

    def update(self, result, target_id, now):
        self.command = [0., 0., 0.]
        if not self.active:
            return self.snapshot()
        if not math.isfinite(now) or now < self.last_clock:
            self.stop('时钟异常，跟随预览已停止。')
            return self.snapshot()
        self.last_clock = now
        if target_id != self.target_id:
            self.stop('参考目标已改变。')
        elif now-self.started_at >= self.MAX_SECONDS:
            self.stop('本轮 120 秒跟随预览结束。')
        elif self.track_id is None and now-self.started_at > 15:
            self.stop('15 秒内未锁定新鲜目标，请检查画面后重试。')
        else:
            try:
                self._observe(result, now)
            except (ValueError, KeyError, TypeError, IndexError, OverflowError) as exc:
                self.wait(str(exc) or '观测无效。')
        return self.snapshot()

    def _observe(self, result, now):
        if not result or result.get('target_id') != self.target_id:
            raise ValueError('等待当前目标的实时观测。')
        stamp = self.number(result.get('captured_at'))
        self.observation_age_s = round(now-stamp, 3)
        if not 0 <= now-stamp <= self.MAX_AGE:
            raise ValueError('画面延迟过大，建议速度保持为零。')
        if self.last_stamp is not None and stamp < self.last_stamp:
            raise ValueError('相机时间戳回退。')
        if result.get('camera_source') != 'realsense_d435i' or result.get('depth_error'):
            raise ValueError('需要匹配的 D435i 对齐深度。')
        candidates = [c for c in result.get('candidates', []) if not c.get('excluded_by_review')]
        if not result.get('suspected_target') or not candidates:
            raise ValueError('目标未确认或已遮挡。')
        best = candidates[0]
        score = self.number(best.get('similarity'))
        threshold = self.number(result.get('similarity_threshold'))
        if result.get('target_kind') != 'photo' or score < threshold:
            raise ValueError('跟随需要照片匹配目标，颜色条件不足以锁定身份。')
        if len(candidates) > 1 and score-self.number(candidates[1].get('similarity')) < .06:
            raise ValueError('出现相似人物，暂不选择。')
        track = best.get('track_id')
        if isinstance(track, bool) or not isinstance(track, int) or track < 0:
            raise ValueError('目标跟踪编号无效。')
        if self.track_id is not None and track != self.track_id:
            raise ValueError('跟踪编号变化，禁止自动换人。')
        depth = best.get('depth') or {}
        z = self.number(depth.get('depth_m'))
        if depth.get('measurement') != 'optical_axis_depth' or not .2 <= z <= 6:
            raise ValueError('人物深度无效。')
        geometry = result.get('camera_geometry') or {}
        if not depth.get('frame_id') or depth['frame_id'] != geometry.get('frame_id'):
            raise ValueError('深度与相机坐标系不一致。')
        fx, cx = self.number(geometry.get('fx')), self.number(geometry.get('cx'))
        width, height = self.number(geometry.get('width')), self.number(geometry.get('height'))
        x1, y1, x2, y2 = [self.number(v) for v in best['bbox']]
        if fx <= 0 or not 0 <= cx <= width or not 0 <= x1 < x2 <= width or not 0 <= y1 < y2 <= height:
            raise ValueError('人物框或相机内参无效。')
        bearing = -math.atan2((x1+x2)/2-cx, fx)  # Camera right -> negative yaw.
        if abs(bearing) > .65:
            raise ValueError('目标偏离正前方过大。')
        if stamp != self.last_stamp:
            self.hits = self.hits+1 if self.pending_track == track else 1
            self.pending_track, self.last_stamp = track, stamp
        if self.track_id is None and self.hits < 3:
            self.state, self.reason = 'acquiring', '目标锁定 %d/3 帧。' % self.hits
            return
        self.track_id = track
        self.depth_m, self.bearing_rad = z, round(bearing, 3)
        if z < self.distance_m-.25:
            self.state, self.reason = 'holding', '目标过近，停止且不自动后退。'
        elif z <= self.distance_m+.25:
            self.state, self.reason = 'holding', '已在期望距离范围内。'
        else:
            vx = min(.20, .35*(z-self.distance_m)) if abs(bearing) < .12 else 0.
            wz = max(-.25, min(.25, .8*bearing)) if abs(bearing) > .05 else 0.
            self.command = [round(vx, 3), 0., round(wz, 3)]
            self.state = 'tracking'
            self.reason = '先转向目标。' if not vx else '保持距离，建议低速跟进。'

    def snapshot(self):
        return {'active': self.active, 'mode': 'preview', 'state': self.state,
                'reason': self.reason, 'target_id': self.target_id, 'track_id': self.track_id,
                'distance_m': self.distance_m, 'depth_m': self.depth_m,
                'bearing_rad': self.bearing_rad, 'observation_age_s': self.observation_age_s,
                'suggested_velocity': list(self.command), 'motion_enabled': False,
                'physical_execution': False,
                'coordinate_frame': 'camera_forward_left_yaw_preview',
                'blocking_reason': '相机机身外参与停车距离、避障和健康门禁联动尚未完成实机验收。'}


def attach_follow(app, searcher, stop_scan):
    @app.get('/follow/status')
    async def status():
        with searcher.lock:
            return searcher.follow.update(searcher.last, (searcher.target_info or {}).get('id'), time.time())

    @app.post('/follow/start')
    async def start(payload: FollowRequest):
        from person_camera import camera_directory
        import json
        try:
            camera = json.loads((camera_directory()/'status.json').read_text())
            if (camera.get('source') != 'realsense_d435i' or not camera.get('running') or
                    not 0 <= time.time()-camera.get('captured_at', 0) <= 2):
                raise ValueError('请先启动 D435i 彩色与对齐深度。')
        except (OSError, ValueError, TypeError) as exc:
            raise HTTPException(409, str(exc)) from exc
        await stop_scan()
        with searcher.lock:
            if searcher.target is None or (searcher.target_info or {}).get('kind', 'photo') != 'photo':
                raise HTTPException(409, '请先拍照选定目标人物；跟随不使用上衣颜色代替身份。')
            if getattr(searcher,'helmet',None):searcher.helmet.deactivate()
            searcher.tasks.finish('stopped', '切换到无运动跟随预览。')
            searcher.active, searcher.speak = False, False
            searcher.threshold = .75
            searcher.generation += 1
            searcher.last, searcher.last_hash = None, None
            searcher.reset_tracker = True
            searcher.streak, searcher.last_track_id = 0, None
            searcher.rejected_track_ids.clear()
            searcher.follow.start(searcher.target_info['id'], payload.distance_m, time.time())
            return searcher.follow.snapshot()

    @app.post('/follow/stop')
    async def stop():
        with searcher.lock:
            searcher.follow.stop()
            return searcher.follow.snapshot()

    @app.post('/follow/execute')
    async def execute():
        raise HTTPException(409, '实际跟随未解锁：需先完成外参、避障和健康门禁验收及现场运动授权。')
