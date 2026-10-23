"""PPE pipeline and authenticated APIs, isolated files and fake models only."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from unittest.mock import Mock,AsyncMock

import cv2
import httpx
import numpy as np
from helmet_monitor import HelmetMonitor,attach_helmet
from fastapi import FastAPI
from test_helmet_rules import head,people
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'apps/nano-deploy'))
from dashboard_platform.server import app as dashboard


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.camera=self.root/'camera';self.camera.mkdir()
        bodies=people(3)
        self.searcher=SimpleNamespace(model_lock=threading.RLock(),
            detector=SimpleNamespace(detect=lambda image:[dict(p,confidence=.95) for p in bodies]))
        self.monitor=HelmetMonitor(self.searcher,self.root/'monitor')
        self.monitor.detector=SimpleNamespace(detect=lambda image:[head(0,'Hardhat'),head(1,'NO-Hardhat'),head(2,'NO-Hardhat')])
        self.monitor.tracker=SimpleNamespace(update=lambda bodies,shape:bodies)
        self.monitor.active=True;self.monitor.session_id='unit-test'
        self.camera_patch=patch('helmet_monitor.camera_directory',return_value=self.camera);self.camera_patch.start()

    def tearDown(self):self.camera_patch.stop();self.tmp.cleanup()

    def frame(self,value):
        image=np.full((320,400,3),value,np.uint8)
        ok,encoded=cv2.imencode('.jpg',image);self.assertTrue(ok)
        raw=encoded.tobytes();(self.camera/'latest.jpg').write_bytes(raw)
        meta={'running':True,'captured_at':time.time(),'sha256':hashlib.sha256(raw).hexdigest(),'source':'native_camera'}
        (self.camera/'status.json').write_text(json.dumps(meta))

    def test_multi_person_end_to_end_distinct_records_and_reload_without_auto_resume(self):
        for value in (50,70,90):self.frame(value);self.monitor.inspect()
        result=self.monitor.status()
        self.assertEqual({'helmet':1,'no_helmet':2,'unknown':0},result['last']['counts'])
        self.assertEqual(2,result['record_count'])
        events=self.monitor.evidence.list()['items']
        self.assertEqual({2,3},{e['track_id'] for e in events})
        self.assertTrue(all(self.monitor.evidence.image(e['id'],'crop').startswith(b'\xff\xd8') for e in events))
        self.assertFalse(result['motion_enabled'])
        restored=HelmetMonitor(self.searcher,self.root/'monitor')
        self.assertFalse(restored.active);self.assertEqual(2,restored.status()['record_count'])

    def test_stop_during_inference_drops_old_result_without_recording(self):
        self.frame(50)
        entered=threading.Event();release=threading.Event()
        def pause(image):entered.set();release.wait(2);return []
        self.monitor.detector.detect=pause
        worker=threading.Thread(target=self.monitor.inspect);worker.start()
        self.assertTrue(entered.wait(1));self.monitor.deactivate();release.set();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertIsNone(self.monitor.last)
        self.assertEqual(0,self.monitor.status()['record_count'])

    def test_stationary_identical_pixels_with_fresh_stamps_still_count_as_new_frames(self):
        for _ in range(3):
            self.frame(50)
            self.monitor.inspect()
        self.assertEqual(3,self.monitor.frames)
        self.assertEqual(2,self.monitor.status()['record_count'])
        # Polling the very same capture again must not add another observation.
        frames=self.monitor.frames
        self.monitor.inspect()
        self.assertEqual(frames,self.monitor.frames)
        self.assertTrue(all(e['observations']==1 for e in self.monitor.evidence.list()['items']))


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_during_model_load_cannot_resume_after_load_finishes(self):
        with tempfile.TemporaryDirectory() as folder:
            s=SimpleNamespace(lock=threading.RLock(),follow=Mock(),tasks=Mock(),active=True,speak=True,generation=0)
            s.helmet=HelmetMonitor(s,folder)
            entered,release=threading.Event(),threading.Event()
            def load():entered.set();release.wait(3)
            s.helmet.load=load
            app=FastAPI();stop_scan=AsyncMock();attach_helmet(app,s,stop_scan)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
                request=asyncio.create_task(c.post('/helmet/start'))
                for _ in range(100):
                    if entered.is_set():break
                    await asyncio.sleep(.01)
                self.assertTrue(entered.is_set())
                stopped=await c.post('/helmet/stop');self.assertFalse(stopped.json()['active'])
                release.set();r=await request
                self.assertEqual(200,r.status_code);self.assertFalse(r.json()['active'])
                self.assertFalse(s.helmet.active);self.assertFalse(s.active);s.follow.stop.assert_called_once()

    async def test_all_ppe_routes_authenticated_and_paths_bounded(self):
        calls=[]
        async def fake(method,path,payload=None):
            calls.append((method,path))
            return httpx.Response(200,json={'active':False} if not path.endswith('.jpg') else None,
                content=None if not path.endswith('.jpg') else b'\xff\xd8fake')
        with patch.object(dashboard,'go2_person_request',side_effect=fake):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=dashboard.app),base_url='http://test') as c:
                auth={'Authorization':'Bearer '+dashboard.settings.auth_token}
                prefix='/api/go2/person/helmet/'
                for method,path in [('GET','status'),('POST','start'),('POST','stop'),('GET','records'),
                                    ('GET','preview.jpg'),('GET','export.json'),('GET','records/'+'a'*32+'/crop.jpg')]:
                    self.assertEqual(401,(await c.request(method,prefix+path)).status_code)
                    self.assertEqual(200,(await c.request(method,prefix+path,headers=auth)).status_code)
                self.assertEqual(7,len(calls))
                self.assertEqual(404,(await c.get(prefix+'records/not-valid/crop.jpg',headers=auth)).status_code)
                self.assertEqual(422,(await c.get(prefix+'records/'+'a'*32+'/unknown.jpg',headers=auth)).status_code)
                self.assertEqual(7,len(calls))


if __name__=='__main__':unittest.main(verbosity=2)
