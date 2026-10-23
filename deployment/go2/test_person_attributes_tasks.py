"""Temporary-file and fake-model tests only; no real camera, GPU, RTC or motion."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import ModuleType
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from fastapi import HTTPException
from pydantic import ValidationError
import person_search as product
from person_attributes import BGR, color_fraction, parse_description
from person_tasks import SearchTasks
from test_person_search_lifecycle import StubDetector, StubExtractor, StubTracker


class AttributeTests(unittest.TestCase):
    def test_nine_supported_colors(self):
        for label, color in [('红','red'), ('橙','orange'), ('黄','yellow'), ('绿','green'),
                             ('蓝','blue'), ('紫','purple'), ('黑','black'), ('白','white'), ('灰','gray')]:
            with self.subTest(color=color):
                self.assertEqual(parse_description('找穿'+label+'色上衣的人')['color'], color)
                image = np.full((180, 90, 3), BGR[color], np.uint8)
                score, region = color_fraction(image, [0, 0, 90, 180], color)
                self.assertGreater(score, .99)
                self.assertEqual(region, [18, 32, 72, 99])

    def test_negation_multiple_colors_and_unsupported_attributes_rejected(self):
        for text in ('找不穿红色上衣的人', '除了红色上衣的人', '找穿红色和蓝色上衣的人',
                     '找穿红色上衣戴帽子的人', '找穿红色裤子的人', '找高个子男生',
                     '找穿粉色上衣的人', '找红色的人', '找穿蓝色上衣的女生', '红红色上衣'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_description(text)

    def test_torso_excludes_red_head_and_trousers(self):
        image = np.full((180, 90, 3), BGR['blue'], np.uint8)
        image[:30], image[110:] = BGR['red'], BGR['red']
        red, _ = color_fraction(image, [0,0,90,180], 'red')
        blue, _ = color_fraction(image, [0,0,90,180], 'blue')
        self.assertEqual(red, 0.)
        self.assertEqual(blue, 1.)

    def test_red_hue_wrap_and_achromatic_colors(self):
        for hue in (0, 179):
            hsv = np.full((48, 24, 3), (hue, 255, 220), np.uint8)
            image = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
            self.assertEqual(color_fraction(image, [0,0,24,48], 'red')[0], 1.)
        image = np.full((48,24,3), 240, np.uint8)
        self.assertEqual(color_fraction(image, [0,0,24,48], 'red')[0], 0.)


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 100.
        self.path = Path(self.temp.name)/'tasks.json'
        self.backup = patch('go2_platform_write.backup', lambda *_: None)
        self.backup.start()
        self.tasks = SearchTasks(self.path, lambda: self.now)
        self.target = {'id':'test-target', 'name':'红上衣', 'kind':'description'}

    def tearDown(self):
        self.backup.stop()
        self.temp.cleanup()

    def test_start_stop_timeout_and_persistence(self):
        first = self.tasks.start(self.target)
        self.assertEqual(first['deadline_at'], 220.)
        self.assertEqual(first['state'], 'running')
        self.assertTrue(self.tasks.finish())
        self.assertEqual(self.tasks.current['state'], 'stopped')
        self.tasks.start(self.target, 30)
        self.now = 130.
        self.assertTrue(self.tasks.expire())
        self.assertEqual(self.tasks.current['state'], 'timed_out')
        restored = SearchTasks(self.path, lambda: self.now)
        self.assertEqual(restored.current['state'], 'timed_out')
        self.assertEqual(restored.list()['total'], 2)

    def test_restart_interrupts_and_does_not_resume(self):
        self.tasks.start(self.target)
        restored = SearchTasks(self.path, lambda: self.now)
        self.assertEqual(restored.current['state'], 'interrupted')
        self.assertIsNotNone(restored.current['finished_at'])
        self.assertFalse(restored.expire())

    def test_found_is_pending_and_review_is_required_for_completion(self):
        self.tasks.start(self.target)
        self.tasks.found({'id':'event1','status':'suspected'})
        self.assertEqual(self.tasks.current['state'], 'found_pending_confirmation')
        self.assertIsNone(self.tasks.apply_review('suspected'))
        self.assertEqual(self.tasks.apply_review('confirmed'), 'completed')
        self.assertEqual(self.tasks.current['state'], 'completed')
        self.assertIn('人工确认', self.tasks.current['reason'])

    def test_rejected_only_continues_nonended_task(self):
        self.tasks.start(self.target, 30)
        self.tasks.found({'id':'event1','status':'suspected'})
        self.assertEqual(self.tasks.apply_review('rejected'), 'running')
        self.tasks.found({'id':'event2','status':'suspected'})
        self.now = 131.
        self.assertTrue(self.tasks.expire())
        self.assertEqual(self.tasks.current['state'], 'found_pending_confirmation')
        self.assertIsNotNone(self.tasks.current['finished_at'])
        self.assertEqual(self.tasks.apply_review('rejected'), 'stopped')
        self.assertEqual(self.tasks.current['state'], 'stopped')

    def test_timeout_pending_survives_restart_without_resuming(self):
        self.tasks.start(self.target, 30)
        self.tasks.found({'id':'event','status':'suspected'})
        self.now = 131.
        self.tasks.expire()
        restored = SearchTasks(self.path, lambda: self.now)
        self.assertEqual(restored.current['state'], 'found_pending_confirmation')
        self.assertIsNotNone(restored.current['finished_at'])
        self.assertFalse(restored.expire())

    def test_timeout_bounds(self):
        for timeout in (29,601,True,30.5):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.tasks.start(self.target, timeout)
        for timeout in (29,601):
            with self.assertRaises(ValidationError):
                product.SearchRequest(timeout_seconds=timeout)


class DescriptionSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='go2-description-test-')
        root = Path(self.temp.name)
        self.camera, self.runtime = root/'camera', root/'runtime'
        self.camera.mkdir()
        self.runtime.mkdir()
        self.searcher = self.new_searcher()
        tracker_module = ModuleType('person_tracker')
        tracker_module.Tracker = StubTracker
        self.patches = [patch.object(product, 'RUNTIME', self.runtime),
                        patch.object(product, 'CAMERA', self.camera),
                        patch.object(product, 'searcher', self.searcher),
                        patch.dict(sys.modules, {'person_tracker': tracker_module}),
                        patch('go2_platform_write.backup', lambda *_: None),
                        patch.object(product.httpx, 'AsyncClient', side_effect=AssertionError('No real HTTP allowed')),
                        patch.object(asyncio, 'create_subprocess_exec', side_effect=AssertionError('No processes allowed'))]
        for p in self.patches:
            p.start()
        self.searcher.set_description(product.DescriptionRequest(text='找穿红色上衣的人'))

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    @staticmethod
    def new_searcher():
        searcher = product.Searcher()
        searcher.detector, searcher.tracker = StubDetector(), StubTracker()
        return searcher

    def frame(self, red=240):
        image = np.full((64,96,3), (0,0,red), np.uint8)
        ok, encoded = cv2.imencode('.jpg', image)
        self.assertTrue(ok)
        raw = encoded.tobytes()
        (self.camera/'latest.jpg').write_bytes(raw)
        (self.camera/'status.json').write_text(json.dumps({'running':True,'captured_at':time.time(),
            'sha256':hashlib.sha256(raw).hexdigest()}))
        return self.searcher.inspect_frame()

    def find(self):
        asyncio.run(product.start(product.SearchRequest(speak=False)))
        results = [self.frame(240-index*4) for index in range(3)]
        self.assertFalse(results[0]['suspected_target'])
        self.assertFalse(results[1]['suspected_target'])
        self.assertTrue(results[2]['suspected_target'])
        return results[2]

    def test_description_restored_and_photo_without_kind_compatible(self):
        restored = self.new_searcher()
        restored.restore_target()
        self.assertIsNone(restored.error)
        self.assertEqual(restored.target_info['kind'], 'description')
        self.assertEqual(restored.target, {'color':'red'})
        self.assertFalse(restored.active)
        self.assertIsNone(restored.extractor)
        self.searcher.extractor = StubExtractor()
        image = np.full((64,96,3), 100, np.uint8)
        feature = self.searcher.extractor.embed(image)
        self.searcher.store_target(image,[10,10,70,56],feature,'Photo','upload')
        metadata_path = self.runtime/'target.json'
        info = json.loads(metadata_path.read_text())
        info.pop('kind')
        info.pop('score_kind')
        metadata_path.write_text(json.dumps(info))
        photo = self.new_searcher()
        photo.extractor = StubExtractor()
        photo.restore_target()
        self.assertEqual(photo.target_info['kind'], 'photo')
        self.assertEqual(photo.target_info['score_kind'], 'appearance_similarity')
        self.assertFalse(photo.active)

    def test_three_frames_record_color_evidence_without_reid_or_audio(self):
        result = self.find()
        self.assertIsNone(self.searcher.extractor)
        self.assertEqual(result['score_kind'], 'color_fraction')
        self.assertEqual(result['candidates'][0]['score'], 1.)
        task = self.searcher.status()['search_task']
        self.assertEqual(task['state'], 'found_pending_confirmation')
        self.assertEqual(task['found_event_id'], result['event_id'])
        event = self.searcher.history.get(result['event_id'])
        self.assertEqual(event['target_kind'], 'description')
        self.assertEqual(event['score_kind'], 'color_fraction')
        self.assertEqual(event['status'], 'suspected')
        self.assertFalse(self.searcher.speak)
        self.assertIsNone(self.searcher.speech_task)

    def test_explicit_review_completes_and_stops_active_task(self):
        result = self.find()
        self.searcher.history.review(result['event_id'], 'confirmed', '现场确认')
        status = self.searcher.status()
        self.assertEqual(status['search_task']['state'], 'completed')
        self.assertFalse(status['active'])

    def reject_candidate(self):
        result=self.find()
        self.searcher.history.review(result['event_id'],'rejected','不是要找的人')
        self.assertEqual('running',self.searcher.status()['search_task']['state'])
        return result['event_id']

    def test_rejected_track_is_not_presented_again_in_the_same_task(self):
        self.reject_candidate()
        results=[self.frame(220-index*5) for index in range(4)]
        self.assertTrue(all(not result['suspected_target'] for result in results))
        self.assertEqual('running',self.searcher.status()['search_task']['state'])
        self.assertEqual(1,self.searcher.history.list()['total'])

    def test_other_track_can_be_found_and_confirmed_immediately_after_rejection(self):
        rejected_id=self.reject_candidate()
        def other_track(tracker,detections,shape):
            return [dict(item,track_id=8) for item in detections]
        with patch.object(StubTracker,'update',other_track):
            results=[self.frame(220-index*5) for index in range(3)]
        event_id=results[-1]['event_id']
        self.assertNotEqual(rejected_id,event_id)
        self.assertEqual('rejected',self.searcher.history.get(rejected_id)['status'])
        self.assertEqual('found_pending_confirmation',self.searcher.status()['search_task']['state'])
        self.searcher.history.review(event_id,'confirmed','确认是目标')
        self.assertEqual('completed',self.searcher.status()['search_task']['state'])
        self.assertFalse(self.searcher.active)

    def test_rejected_top_track_does_not_hide_another_matching_person(self):
        self.reject_candidate()
        def two_tracks(tracker,detections,shape):
            return [dict(item,track_id=track) for item in detections for track in (7,8)]
        with patch.object(StubTracker,'update',two_tracks):
            results=[self.frame(220-index*5) for index in range(3)]
        result=results[-1]
        self.assertTrue(result['suspected_target'])
        self.assertEqual(8,result['candidates'][0]['track_id'])
        self.assertEqual(8,self.searcher.history.get(result['event_id'])['track_id'])

    def test_new_task_can_reconsider_a_previously_rejected_track(self):
        rejected_id=self.reject_candidate()
        result=self.find()
        self.assertTrue(result['suspected_target'])
        self.assertNotEqual(rejected_id,result['event_id'])
        self.assertEqual('found_pending_confirmation',self.searcher.status()['search_task']['state'])

    def test_new_person_track_keeps_the_previous_candidate_evidence_separate(self):
        previous=self.find()['event_id']
        original=self.searcher.history.image(previous,'snapshot')
        def other_track(tracker,detections,shape):
            return [dict(item,track_id=8) for item in detections]
        with patch.object(StubTracker,'update',other_track):
            result=[self.frame(220-index*5) for index in range(3)][-1]
        self.assertNotEqual(previous,result['event_id'])
        self.assertEqual(original,self.searcher.history.image(previous,'snapshot'))
        self.assertEqual(2,self.searcher.history.list()['total'])

    def test_rejection_discards_an_inflight_result_for_the_old_candidate(self):
        event_id=self.find()['event_id']
        detector=self.searcher.detector;detector.pause=True
        results=[]
        worker=threading.Thread(target=lambda:results.append(self.frame(215)))
        worker.start()
        try:
            self.assertTrue(detector.entered.wait(1))
            self.searcher.history.review(event_id,'rejected','不是目标')
            self.assertEqual('running',self.searcher.status()['search_task']['state'])
        finally:
            detector.release.set();worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual([None],results)
        self.assertIsNone(self.searcher.last)

    def test_reject_continues_existing_task_but_timeout_never_resumes(self):
        result = self.find()
        self.searcher.history.review(result['event_id'], 'rejected', '不是目标')
        self.assertEqual(self.searcher.status()['search_task']['state'], 'running')
        self.assertTrue(self.searcher.active)
        self.frame(220)
        self.assertEqual(self.searcher.status()['search_task']['state'], 'running')
        deadline = self.searcher.tasks.current['deadline_at']
        self.searcher.tasks.clock = lambda: deadline+1
        self.assertIsNone(self.frame(210))
        self.assertFalse(self.searcher.active)
        self.assertEqual(self.searcher.status()['search_task']['state'], 'timed_out')

    def test_invalid_description_http400_preserves_target_and_task(self):
        asyncio.run(product.start(product.SearchRequest()))
        before = self.searcher.target_info['id']
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(product.description(product.DescriptionRequest(text='找穿红蓝上衣的人')))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(self.searcher.target_info['id'], before)
        self.assertTrue(self.searcher.active)

    def test_stopped_task_stays_stopped_after_restart_with_camera_offline(self):
        self.find()
        asyncio.run(product.stop())
        (self.camera/'status.json').write_text(json.dumps({'running':False, 'captured_at':0}))
        restarted = self.new_searcher()
        restarted.restore_target()
        status = restarted.status()
        self.assertEqual(status['search_task']['state'], 'stopped')
        self.assertFalse(status['active'])
        self.assertFalse(status['camera']['connected'])
        self.assertIsNone(restarted.inspect_frame())
        self.assertEqual(restarted.frames, 0)

    def test_unfinished_task_interrupted_after_restart_with_camera_offline(self):
        asyncio.run(product.start(product.SearchRequest()))
        old_id = self.searcher.tasks.current['id']
        (self.camera/'status.json').write_text(json.dumps({'running':False, 'captured_at':0}))
        restarted = self.new_searcher()
        restarted.restore_target()
        status = restarted.status()
        self.assertEqual(status['search_task']['id'], old_id)
        self.assertEqual(status['search_task']['state'], 'interrupted')
        self.assertFalse(status['active'])
        self.assertIsNone(restarted.inspect_frame())
        self.assertEqual(restarted.frames, 0)

    def test_stop_and_tasks_contract(self):
        self.find()
        response = asyncio.run(product.stop())
        self.assertFalse(response['active'])
        self.assertEqual(response['search_task']['state'], 'stopped')
        listing = asyncio.run(product.tasks(20))
        self.assertEqual(listing['total'], 1)
        self.assertEqual(listing['current']['state'], 'stopped')
        self.assertFalse(listing['current']['motion_enabled'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
