"""Follow API tests with fake camera and no real search or robot side effects."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI
from person_follow import FollowController, attach_follow
import test_person_search_lifecycle as lifecycle_fixture

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'apps/nano-deploy'))
from dashboard_platform.server import app as dashboard


class FollowApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.camera = {'source': 'realsense_d435i', 'running': True, 'captured_at': time.time()}
        self.store_camera()
        self.searcher = SimpleNamespace(lock=threading.RLock(), follow=FollowController(),
            target=[1], target_info={'id': 'target', 'kind': 'photo'}, tasks=Mock(),
            generation=0, active=True, speak=True, rejected_track_ids={1}, last={'old': True})
        self.app = FastAPI(); self.stop_scan = AsyncMock()
        attach_follow(self.app, self.searcher, self.stop_scan)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test')
        self.patcher = patch('person_camera.camera_directory', return_value=self.path); self.patcher.start()

    def store_camera(self):
        (self.path/'status.json').write_text(json.dumps(self.camera))

    async def asyncTearDown(self):
        await self.client.aclose(); self.patcher.stop(); self.temp.cleanup()

    async def test_start_resets_old_observations_and_disables_speech_without_movement(self):
        r = await self.client.post('/follow/start', json={})
        self.assertEqual(200, r.status_code)
        self.assertTrue(r.json()['active']); self.assertFalse(r.json()['motion_enabled'])
        self.assertIsNone(self.searcher.last)
        self.assertFalse(self.searcher.active); self.assertFalse(self.searcher.speak)
        self.assertTrue(self.searcher.reset_tracker)
        self.stop_scan.assert_awaited_once()
        r = await self.client.post('/follow/stop')
        self.assertFalse(r.json()['active'])

    async def test_photo_required_stale_camera_and_execution_rejected(self):
        self.searcher.target_info['kind'] = 'description'
        self.assertEqual(409, (await self.client.post('/follow/start', json={})).status_code)
        self.camera['captured_at'] -= 10; self.store_camera()
        self.assertEqual(409, (await self.client.post('/follow/start', json={})).status_code)
        self.assertEqual(409, (await self.client.post('/follow/execute')).status_code)
        self.assertFalse(self.searcher.follow.active)

    async def test_validation_cannot_enable_motion(self):
        for payload in ({'armed': True}, {'mode': 'live'}, {'distance_m': 99}):
            self.assertEqual(422, (await self.client.post('/follow/start', json=payload)).status_code)
        self.stop_scan.assert_not_awaited()

    async def test_dashboard_auth_and_forwarding(self):
        calls = []
        async def backend(method, path, payload=None):
            calls.append((method, path, payload))
            return httpx.Response(200, json={'motion_enabled': False})
        with patch.object(dashboard, 'go2_person_request', side_effect=backend):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=dashboard.app), base_url='http://test') as c:
                auth = {'Authorization': 'Bearer '+dashboard.settings.auth_token}
                for method, path, body in (('GET','status',None),('POST','start',{}),('POST','stop',None),('POST','execute',None)):
                    r = await c.request(method, '/api/go2/person/follow/'+path, json=body)
                    self.assertEqual(401, r.status_code)
                self.assertEqual([], calls)
                r = await c.post('/api/go2/person/follow/start', headers=auth, json={})
                self.assertEqual(200, r.status_code)
                self.assertEqual([('POST', '/follow/start', {'mode': 'preview', 'distance_m': 1.8})], calls)
                for body in ({'armed': True}, {'mode': 'execute'}, {'distance_m': .1}):
                    self.assertEqual(422, (await c.post('/api/go2/person/follow/start', headers=auth, json=body)).status_code)
                self.assertEqual(409, (await c.post('/api/go2/person/follow/execute', headers=auth)).status_code)
                self.assertEqual(1, len(calls))


class FollowPipelineTests(unittest.TestCase):
    def setUp(self):
        # Reuse isolated detector/tracker/cache fixtures, not production files.
        self.fixture = lifecycle_fixture.LifecycleTests('test_stop_clears_result_and_returns_503')
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_follow_inference_survives_completed_search_without_creating_discoveries(self):
        import person_search as product
        import numpy as np
        s = self.fixture.searcher
        s.active = False
        s.tasks.finish('stopped', 'preview')
        s.follow.start(s.target_info['id'], 1.8, time.time())
        with patch.object(product, 'load_depth', return_value=np.full((64,96), 3., dtype=np.float32)):
            for value in range(5):
                self.fixture.write_camera((50+value*20, 80, 20))
                path = self.fixture.camera/'status.json'
                meta = json.loads(path.read_text())
                meta.update(source='realsense_d435i', frame_id='optical', width=96, height=64,
                            intrinsics=[100.,0.,40.,0.,100.,32.,0.,0.,1.])
                path.write_text(json.dumps(meta))
                result = s.inspect_frame()
                s.follow.update(result, s.target_info['id'], time.time())
        self.assertEqual('tracking', s.follow.state)
        self.assertEqual([.2, 0, 0], s.follow.command)
        self.assertEqual(100., result['camera_geometry']['fx'])
        self.assertEqual(0, s.history.list(1)['total'])
        self.assertFalse(s.active)
        self.assertFalse(result['motion_enabled'])

    def test_global_stop_cancels_follow_and_clears_advice(self):
        import asyncio
        import person_search as product
        s = self.fixture.searcher
        s.follow.start(s.target_info['id'], 1.8, time.time())
        asyncio.run(product.stop())
        self.assertFalse(s.follow.active)
        self.assertEqual([0,0,0], s.follow.command)
        self.assertIsNone(s.inspect_frame())


if __name__ == '__main__': unittest.main(verbosity=2)
