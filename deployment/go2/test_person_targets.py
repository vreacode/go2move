"""Isolated reference-library lifecycle/API tests; no camera or robot calls."""
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import cv2
import httpx
import numpy as np
from fastapi import FastAPI
import person_search as product
from person_history_actions import attach_history_actions
from person_target_actions import attach_target_actions
from person_targets import PersonTargets


class TargetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='go2-target-library-test-')
        self.root = Path(self.tmp.name)
        self.backup = patch('go2_platform_write.backup')
        self.backup.start()
        self.runtime = patch.object(product, 'RUNTIME', self.root)
        self.runtime.start()
        self.owner = product.Searcher()
        self.owner.detector = object()
        self.owner.tracker = object()
        self.owner.extractor = SimpleNamespace(embed=lambda image: np.array([1.,0.],dtype=np.float32))
        self.image = np.full((120,80,3), 100, np.uint8)
        self.app = FastAPI()
        attach_target_actions(self.app, self.owner, SimpleNamespace(stop=AsyncMock()))
        attach_history_actions(self.app, self.owner)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://isolated')

    async def asyncTearDown(self):
        await self.client.aclose()
        self.runtime.stop(); self.backup.stop(); self.tmp.cleanup()

    def save(self, name):
        return self.owner.store_target(self.image,[0,0,80,120],np.array([1.,0.]),name,'test')['target']

    def discovery(self, info):
        return self.owner.history.record(info,{'track_id':7,'similarity':.8},1000.,b'snapshot',self.owner.reference_jpeg)

    async def test_two_targets_persist_and_selection_reuses_id_without_search(self):
        first=self.save('张同学');second=self.save('李同学')
        self.discovery(first)
        listing=(await self.client.get('/targets')).json()
        self.assertEqual(2,listing['total'])
        self.assertEqual(1,next(t for t in listing['items'] if t['id']==first['id'])['discovery_count'])
        response=await self.client.post('/targets/'+first['id']+'/select')
        self.assertEqual(200,response.status_code)
        self.assertEqual(first['id'],response.json()['target']['id'])
        self.assertFalse(self.owner.active)
        self.assertEqual(2,PersonTargets(self.root/'targets').list()['total'])
        self.assertEqual(200,(await self.client.get('/targets/'+second['id']+'/image.jpg')).status_code)
        restarted=product.Searcher();restarted.detector=object();restarted.tracker=object();restarted.extractor=self.owner.extractor
        restarted.restore_target()
        self.assertEqual(first['id'],restarted.target_info['id']);self.assertFalse(restarted.active)

    async def test_delete_current_clears_reference_and_keeps_discovery(self):
        first=self.save('当前目标');event=self.discovery(first)
        self.owner.active=True;self.owner.tasks.start(first,120)
        response=await self.client.delete('/targets/'+first['id'])
        self.assertEqual(200,response.status_code)
        self.assertIsNone(self.owner.target);self.assertIsNone(self.owner.target_info)
        self.assertFalse(self.owner.active)
        self.assertFalse((self.root/'target.json').exists())
        self.assertEqual(1,self.owner.history.list()['total'])
        self.assertEqual(event['id'],self.owner.history.get(event['id'])['id'])
        self.assertEqual(404,(await self.client.get('/targets/'+first['id']+'/image.jpg')).status_code)
        self.assertEqual(404,(await self.client.post('/targets/'+first['id']+'/select')).status_code)
        reopened=PersonTargets(self.root/'targets');reopened.import_existing(self.root,self.owner.history)
        self.assertEqual(0,reopened.list()['total'],'Old evidence must not resurrect a deleted target')

    async def test_delete_noncurrent_keeps_current_and_invalid_paths_fail(self):
        first=self.save('旧');second=self.save('当前')
        self.assertEqual(200,(await self.client.delete('/targets/'+first['id'])).status_code)
        self.assertEqual(second['id'],self.owner.target_info['id'])
        for path in ['/targets/not-an-id','/targets/'+'f'*32]:
            self.assertEqual(404,(await self.client.delete(path)).status_code)
        self.assertEqual(1,self.owner.targets.list()['total'])

    async def test_failed_backup_or_commit_preserves_library_and_active_target(self):
        info=self.save('保留')
        before=self.owner.targets.list()
        with patch('go2_platform_write.backup',side_effect=OSError('test backup failure')):
            self.assertEqual(503,(await self.client.delete('/targets/'+info['id'])).status_code)
        self.assertEqual(before,self.owner.targets.list())
        self.assertEqual(info['id'],self.owner.target_info['id'])
        with patch.object(self.owner.targets,'commit',side_effect=OSError('test disk full')):
            self.assertEqual(503,(await self.client.delete('/targets/'+info['id'])).status_code)
        self.assertEqual(before,self.owner.targets.list())

    async def test_legacy_current_and_history_import_once(self):
        first=self.save('较早的照片');event=self.discovery(first)
        second=self.save('当前照片')
        import shutil
        shutil.rmtree(self.root/'targets')
        self.owner._targets=None
        self.assertEqual(2,self.owner.targets.list()['total'])
        self.owner.targets.import_existing(self.root,self.owner.history)
        self.assertEqual(2,self.owner.targets.list()['total'])
        for info in self.owner.targets.list()['items']:
            self.assertTrue(self.owner.targets.image(info['id']).startswith(b'\xff\xd8'))
        self.assertEqual(second['id'],self.owner.target_info['id'])

    async def test_delete_discovery_excluded_from_export_images_and_restart(self):
        info=self.save('目标');event=self.discovery(info)
        self.owner.current_event_id=event['id'];self.owner.active=True
        self.owner.tasks.start(info,120);self.owner.tasks.found(event)
        response=await self.client.delete('/history/'+event['id'])
        self.assertEqual(200,response.status_code)
        self.assertFalse(self.owner.active)
        self.assertEqual(0,self.owner.history.list()['total'])
        self.assertEqual(0,self.owner.history.export()['total'])
        with self.assertRaises(FileNotFoundError):self.owner.history.image(event['id'],'target')
        self.assertEqual(1,self.owner.targets.list()['total'])
        from person_history import PersonHistory
        self.assertEqual(0,PersonHistory(self.root/'history').list()['total'])
        self.assertEqual(404,(await self.client.delete('/history/'+event['id'])).status_code)

    async def test_corrupt_index_is_never_silently_overwritten(self):
        folder=self.root/'broken';folder.mkdir();index=folder/'targets.json';index.write_text('{broken')
        with self.assertRaises(json.JSONDecodeError):PersonTargets(folder)
        self.assertEqual('{broken',index.read_text())


if __name__=='__main__':unittest.main()
