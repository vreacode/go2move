#!/usr/bin/env python3
"""Real cached-model history tests using isolated files, without robot calls."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import cv2
import numpy as np
import person_search as product
from person_detector import Detector
from person_reid import Extractor

MODELS = Path('/home/unitree/go2_ros2_ws/models/person_search')

class HistoryTests(unittest.TestCase):
    records = {}

    @classmethod
    def setUpClass(cls):
        cls.detector, cls.extractor = Detector(), Extractor()
        cls.photo = cv2.imread(str(MODELS/'public_bus.jpg'))
        cls.reference = (MODELS/'public_person_crop.jpg').read_bytes()
        cls.empty = cv2.imread(str(MODELS/'native_empty_scene.jpg'))
        assert cls.photo is not None and cls.empty is not None

    @classmethod
    def tearDownClass(cls):
        cls.detector.close()
        cls.extractor.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='go2-history-test-')
        root = Path(self.temp.name)
        self.camera, self.runtime = root/'camera', root/'runtime'
        self.camera.mkdir()
        self.runtime.mkdir()
        self.searcher = self.new_searcher()
        self.patches = [patch.object(product, 'RUNTIME', self.runtime),
                        patch.object(product, 'CAMERA', self.camera),
                        patch.object(product, 'searcher', self.searcher),
                        patch('go2_platform_write.backup', lambda *_: None)]
        for item in self.patches:
            item.start()
        self.searcher.set_target(product.TargetRequest(
            image_base64=base64.b64encode(self.reference).decode(), name='Public test person'))
        asyncio.run(product.start(product.SearchRequest(speak=False)))
        self.clock = time.time()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def new_searcher(self):
        searcher = product.Searcher()
        searcher.detector, searcher.extractor = self.detector, self.extractor
        return searcher

    def inspect(self, shift, offset=0, image=None, age=0, running=True, invalid_hash=False):
        frame = image if image is not None else cv2.warpAffine(self.photo,
            np.float32([[1,0,shift],[0,1,0]]), (self.photo.shape[1],self.photo.shape[0]),
            borderMode=cv2.BORDER_REPLICATE)
        ok, encoded = cv2.imencode('.jpg', frame)
        assert ok
        raw = encoded.tobytes()
        now = self.clock + offset
        (self.camera/'latest.jpg').write_bytes(raw)
        (self.camera/'status.json').write_text(json.dumps({
            'running':running, 'captured_at':now-age,
            'sha256':'invalid' if invalid_hash else hashlib.sha256(raw).hexdigest()}))
        with patch.object(product.time, 'time', return_value=now):
            return self.searcher.inspect_frame()

    def six_matches(self):
        results = [self.inspect(2*i, offset=.25*i) for i in range(6)]
        self.assertTrue(all(r['suspected_target'] for r in results[2:]))
        ids = [r['event_id'] for r in results[2:]]
        self.assertEqual(1, len(set(ids)))
        self.assertIsNone(self.searcher.status()['recording_error'])
        return results, ids[0]

    def assert_saved_images(self, owner, event_id, original):
        for kind in ('snapshot','target'):
            raw = owner.history.image(event_id,kind)
            self.assertEqual(original[kind],raw)
            self.assertIsNotNone(cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR))
        with patch.object(product,'searcher',owner):
            self.assertGreater(len(asyncio.run(product.history_image(event_id,'snapshot')).body),100)
            self.assertGreater(len(asyncio.run(product.history_image(event_id,'target')).body),100)
            self.assertEqual(1,asyncio.run(product.history(limit=20))['total'])

    def test_continuous_dedup_sqlite_and_images_survive_stop_swap_restart(self):
        results,event_id = self.six_matches()
        listing = self.searcher.history.list()
        self.assertEqual(1,listing['total'])
        event = listing['items'][0]
        # The first two frames establish the 3-frame match; frames 3..6 are observations.
        self.assertEqual(4,event['observations'])
        self.assertEqual('suspected',event['status'])
        self.assertEqual('live_camera',event['source'])
        self.assertEqual(self.searcher.target_info['id'],event['target_id'])
        with sqlite3.connect(str(self.searcher.history.database)) as db:
            db.row_factory=sqlite3.Row
            row=dict(db.execute('SELECT * FROM events').fetchone())
        self.assertEqual(4,row['observations'])
        self.assertLessEqual(row['found_at'],row['snapshot_at'])
        self.assertLessEqual(row['snapshot_at'],row['last_seen_at'])
        original={kind:self.searcher.history.image(event_id,kind) for kind in ('snapshot','target')}
        asyncio.run(product.stop())
        self.assert_saved_images(self.searcher,event_id,original)
        self.searcher.set_target(product.TargetRequest(
            image_base64=base64.b64encode(self.reference).decode(),name='Replacement target'))
        self.assertNotEqual(row['target_id'],self.searcher.target_info['id'])
        self.assert_saved_images(self.searcher,event_id,original)
        restarted=self.new_searcher()
        self.assert_saved_images(restarted,event_id,original)
        self.records['continuous_and_persistence']={'passed':True,'frames':6,
            'observations':4,'event_ids':list(set(r['event_id'] for r in results[2:])),
            'sqlite':row,'images_after_stop_target_change_restart':True}

    def test_gap_more_than_five_seconds_creates_new_event(self):
        _,old_id=self.six_matches()
        result=self.inspect(14,offset=7)
        self.assertTrue(result['suspected_target'])
        self.assertNotEqual(old_id,result['event_id'])
        listing=self.searcher.history.list()
        self.assertEqual(2,listing['total'])
        self.assertEqual(1,listing['items'][0]['observations'])
        self.assertIsNone(self.searcher.status()['recording_error'])
        self.records['gap_new_event']={'passed':True,'gap_seconds':5.75,'total':2}

    def test_stale_stopped_unmatched_and_incoherent_sources_do_not_record(self):
        self.six_matches()
        before=self.searcher.history.list()
        result=self.inspect(0,offset=2,image=self.empty)
        self.assertFalse(result['suspected_target'])
        self.assertNotIn('event_id',result)
        for label,kwargs in [('stale',{'age':3}),('stopped',{'running':False})]:
            with self.subTest(source=label), self.assertRaisesRegex(RuntimeError,'摄像头画面已中断'):
                self.inspect(18,offset=3,**kwargs)
        self.assertIsNone(self.inspect(20,offset=3,invalid_hash=True))
        self.assertEqual(before,self.searcher.history.list())
        self.assertIsNone(self.searcher.status()['recording_error'])
        self.records['invalid_sources']={'passed':True,'stale':True,'stopped':True,
                                         'unmatched':True,'hash_mismatch':True}

    def test_restore_target_and_explicit_recording_errors(self):
        info=dict(self.searcher.target_info)
        feature=self.searcher.target.copy()
        restarted=self.new_searcher()
        restarted.restore_target()
        self.assertIsNone(restarted.error)
        self.assertEqual(info,restarted.target_info)
        self.assertFalse(restarted.active)
        self.assertEqual((512,),restarted.target.shape)
        cosine=float(np.dot(feature,restarted.target))
        self.assertGreater(cosine,.99)
        self.assertIsNotNone(cv2.imdecode(np.frombuffer(restarted.reference_jpeg,np.uint8),cv2.IMREAD_COLOR))
        with patch.object(product,'searcher',restarted):
            self.assertEqual(restarted.reference_jpeg,asyncio.run(product.target_preview()).body)
        self.six_matches()
        with patch.object(self.searcher.history,'record',side_effect=OSError('Injected write failure')):
            result=self.inspect(14,offset=2)
        self.assertTrue(result['suspected_target'])
        self.assertNotIn('event_id',result)
        self.assertIn('Injected write failure',self.searcher.status()['recording_error'])
        result=self.inspect(16,offset=2.25)
        self.assertIn('event_id',result)
        self.assertIsNone(self.searcher.status()['recording_error'])
        self.records['restoration_and_errors']={'passed':True,'restored_id':info['id'],
            'restored_embedding_dimension':512,'restored_cosine':cosine,
            'active_after_restore':restarted.active,'write_failure_visible_and_next_save_recovers':True}

    def test_saved_reference_restores_after_another_visual_mode_has_started(self):
        expected=dict(self.searcher.target_info)
        restarted=self.new_searcher()
        restarted.generation=3  # Starting/stopping helmet mode changes search generation.
        restarted.helmet.active=True
        restarted.restore_target()
        self.assertEqual(expected,restarted.target_info)
        self.assertIsNotNone(restarted.reference_jpeg)
        self.assertFalse(restarted.active)
        self.assertTrue(restarted.helmet.active)

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(HistoryTests))
    from go2_platform_write import write
    report=dict(HistoryTests.records,tests_run=result.testsRun,failures=len(result.failures),
        errors=len(result.errors),real_user_camera_or_target_files_modified=False,motion_commands_sent=0)
    write(Path(__file__).with_name('person_search_history_test_20261007.json'),
          json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
