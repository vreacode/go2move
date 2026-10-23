#!/usr/bin/env python3
"""Deterministic search lifecycle regressions; no GPU, camera or robot calls.

All camera/result files live in TemporaryDirectory. Detector/embedding/tracker
stubs test cancellation and coherent preview publication, not model accuracy.
"""
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
import person_search as product


class StubDetector:
    def __init__(self):
        self.pause = False
        self.entered = threading.Event()
        self.release = threading.Event()

    def detect(self, image):
        if self.pause:
            self.entered.set()
            if not self.release.wait(2):
                raise AssertionError('Test did not release the blocked detector')
        return [{'bbox': [10, 10, 70, 56], 'confidence': .95}]


class StubExtractor:
    def embed(self, image):
        feature = np.zeros(512, dtype=np.float32)
        feature[0] = 1
        return feature

    def embed_many(self, images):
        return np.asarray([self.embed(image) for image in images]).reshape(len(images), 512)


class StubTracker:
    def __init__(self, frame_rate=2):
        pass

    def update(self, detections, shape):
        return [dict(detection, track_id=7) for detection in detections]


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='go2-search-lifecycle-')
        root = Path(self.temporary.name)
        self.camera, self.runtime = root / 'camera', root / 'results'
        self.camera.mkdir()
        self.runtime.mkdir()
        self.searcher = product.Searcher()
        self.searcher.detector = StubDetector()
        self.searcher.extractor = StubExtractor()
        self.searcher.tracker = StubTracker()
        self.searcher.target = self.searcher.extractor.embed(np.zeros((64, 96, 3), np.uint8))
        self.searcher.target_info = {'id': 'same-target', 'name': 'Test reference'}
        tracker_module = ModuleType('person_tracker')
        tracker_module.Tracker = StubTracker
        self.patches = [patch.object(product, 'CAMERA', self.camera),
                        patch.object(product, 'RUNTIME', self.runtime),
                        patch.object(product, 'searcher', self.searcher),
                        patch.dict(sys.modules, {'person_tracker': tracker_module}),
                        patch('go2_platform_write.backup', lambda *_: None)]
        for item in self.patches:
            item.start()
        self.write_camera((180, 25, 10))
        self.start_search()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def write_camera(self, color):
        image = np.empty((64, 96, 3), dtype=np.uint8)
        image[:] = color
        ok, encoded = cv2.imencode('.jpg', image)
        self.assertTrue(ok)
        raw = encoded.tobytes()
        captured = time.time()
        (self.camera / 'latest.jpg').write_bytes(raw)
        (self.camera / 'status.json').write_text(json.dumps({
            'running': True, 'captured_at': captured,
            'sha256': hashlib.sha256(raw).hexdigest()}))
        return captured

    def start_search(self, threshold=.75):
        return asyncio.run(product.start(product.SearchRequest(
            similarity_threshold=threshold, speak=False)))

    def assert_preview_unavailable(self):
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(product.preview())
        self.assertEqual(503, raised.exception.status_code)

    def test_stop_restart_rejects_frame_from_previous_generation(self):
        old_generation = self.searcher.generation
        detector = self.searcher.detector
        detector.pause = True
        results, errors = [], []

        def inspect():
            try:
                results.append(self.searcher.inspect_frame())
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=inspect)
        worker.start()
        try:
            self.assertTrue(detector.entered.wait(1), 'Detector was not entered')
            asyncio.run(product.stop())
            self.start_search(.9)
            self.assertEqual('same-target', self.searcher.target_info['id'])
            self.assertGreater(self.searcher.generation, old_generation)
        finally:
            detector.release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual([], errors)
        self.assertEqual([None], results)
        self.assertEqual(0, self.searcher.frames)
        self.assertIsNone(self.searcher.last)
        self.assertIsNone(self.searcher.preview_bytes)
        self.assertFalse((self.runtime / 'latest.jpg').exists())
        self.assert_preview_unavailable()
        detector.pause = False
        new_result = self.searcher.inspect_frame()
        self.assertEqual(self.searcher.generation, new_result['generation'])
        self.assertEqual(.9, new_result['similarity_threshold'])

    def test_helmet_error_does_not_pollute_saved_person_search_status(self):
        self.searcher.active=False
        self.searcher.helmet.active=True
        async def check():
            with patch.object(self.searcher.helmet,'inspect',side_effect=RuntimeError('temporary helmet frame error')):
                task=asyncio.create_task(self.searcher.run())
                await asyncio.sleep(.04)
                task.cancel()
                try:await task
                except asyncio.CancelledError:pass
        asyncio.run(check())
        self.assertIsNone(self.searcher.error)
        self.assertEqual('temporary helmet frame error',self.searcher.helmet.error)

    def test_preview_is_new_committed_image_even_if_disk_copy_changes(self):
        self.assert_preview_unavailable()
        first = self.searcher.inspect_frame()
        old_preview = asyncio.run(product.preview()).body
        self.write_camera((12, 210, 28))
        second = self.searcher.inspect_frame()
        response = asyncio.run(product.preview())
        self.assertIs(self.searcher.last, second)
        self.assertEqual(second['generation'], self.searcher.generation)
        self.assertNotEqual(first['captured_at'], second['captured_at'])
        self.assertNotEqual(old_preview, response.body)
        self.assertEqual((self.runtime / 'latest.jpg').read_bytes(), response.body)
        self.assertIsInstance(response.body, bytes)
        image = cv2.imdecode(np.frombuffer(response.body, np.uint8), cv2.IMREAD_COLOR)
        self.assertTrue(np.max(np.abs(image[-1, -1].astype(int) - [12, 210, 28])) <= 3)
        # HTTP must retain the committed image, rather than re-reading old files.
        (self.runtime / 'latest.jpg').write_bytes(old_preview)
        self.assertEqual(response.body, asyncio.run(product.preview()).body)

    def test_stop_clears_result_and_returns_503(self):
        self.assertIsNotNone(self.searcher.inspect_frame())
        old_generation = self.searcher.generation
        status = asyncio.run(product.stop())
        self.assertFalse(status['active'])
        self.assertIsNone(status['last'])
        self.assertIsNone(self.searcher.preview_bytes)
        self.assertGreater(self.searcher.generation, old_generation)
        self.assertIsNone(self.searcher.inspect_frame())
        self.assert_preview_unavailable()

    def test_expired_result_and_camera_are_rejected_without_sleep(self):
        result = self.searcher.inspect_frame()
        with patch.object(product.time, 'time', return_value=result['captured_at'] + 3):
            self.assertIsNone(self.searcher.status()['last'])
            self.assert_preview_unavailable()
            with self.assertRaisesRegex(RuntimeError, '摄像头画面已中断'):
                self.searcher.inspect_frame()

    def test_upload_changes_generation_and_invalidates_previous_preview(self):
        self.searcher.inspect_frame()
        old_generation = self.searcher.generation
        raw = (self.camera / 'latest.jpg').read_bytes()
        response = self.searcher.set_target(product.TargetRequest(
            image_base64=base64.b64encode(raw).decode(), name='Replacement'))
        self.assertNotEqual('same-target', response['target']['id'])
        self.assertGreater(self.searcher.generation, old_generation)
        self.assertFalse(self.searcher.active)
        self.assertIsNone(self.searcher.last)
        self.assertIsNone(self.searcher.preview_bytes)
        self.assert_preview_unavailable()


if __name__ == '__main__':
    assert 'tensorrt' not in sys.modules and 'torch' not in sys.modules
    unittest.main(verbosity=2)
