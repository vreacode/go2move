#!/usr/bin/env python3
"""Isolated capture/select tests with cached real detector/ReID engines.

Public upstream photos stand in for camera input. No SDK, RTC, live camera
files, speech service, navigation, or motion interfaces are used.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import person_search as product
from person_detector import Detector
from person_reid import Extractor

MODEL_DIR = Path('/home/unitree/go2_ros2_ws/models/person_search')
REPORT = Path(__file__).with_name('person_search_capture_test_20261007.json')


class CaptureTests(unittest.TestCase):
    records = {}

    @classmethod
    def setUpClass(cls):
        cls.detector = Detector()
        cls.extractor = Extractor()
        cls.bus = cv2.imread(str(MODEL_DIR / 'osnet_test_bus.jpg'))
        if cls.bus is None:
            raise RuntimeError('Public upstream bus.jpg fixture is missing')
        detections = cls.detector.detect(cls.bus)
        if len(detections) < 2:
            raise AssertionError('Public crowd fixture did not produce multiple people')
        # Derive a true single-person fixture using the actual detector, rather
        # than prescribing a stub detection or asking for a live photograph.
        cls.single = None
        for item in detections:
            x1, y1, x2, y2 = product.Searcher.clip_box(item['bbox'], cls.bus)
            candidate = cls.bus[y1:y2, x1:x2].copy()
            if len(cls.detector.detect(candidate)) == 1:
                cls.single = candidate
                break
        if cls.single is None:
            raise AssertionError('Could not isolate a single person from the public fixture')
        cls.records['models'] = {'detector_engine': cls.detector.engine_path,
                                 'reid_engine': cls.extractor.model_path,
                                 'source': 'https://raw.githubusercontent.com/ultralytics/ultralytics/main/ultralytics/assets/bus.jpg',
                                 'full_photo_people': len(detections),
                                 'single_fixture_shape': list(cls.single.shape)}

    @classmethod
    def tearDownClass(cls):
        cls.detector.close()
        cls.extractor.close()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='go2-capture-test-')
        root = Path(self.temporary.name)
        self.camera, self.runtime = root / 'camera', root / 'results'
        self.camera.mkdir()
        self.runtime.mkdir()
        self.searcher = product.Searcher()
        self.searcher.detector = self.detector
        self.searcher.extractor = self.extractor
        self.patches = [patch.object(product, 'CAMERA', self.camera),
                        patch.object(product, 'RUNTIME', self.runtime),
                        patch('go2_platform_write.backup', lambda *_: None)]
        for item in self.patches:
            item.start()
        self.write_camera(self.single)
        self.baseline = self.searcher.capture_target(product.CaptureRequest(name='Existing target'))['target']
        self.baseline_feature = self.searcher.target.copy()
        self.baseline_jpeg = (self.runtime / 'target.jpg').read_bytes()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def write_camera(self, image, age=0, running=True):
        ok, encoded = cv2.imencode('.jpg', image)
        self.assertTrue(ok)
        raw = encoded.tobytes()
        captured_at = time.time() - age
        (self.camera / 'latest.jpg').write_bytes(raw)
        (self.camera / 'status.json').write_text(json.dumps({
            'running': running, 'captured_at': captured_at,
            'sha256': hashlib.sha256(raw).hexdigest()}))
        return captured_at

    def assert_old_target_preserved(self):
        self.assertEqual(self.baseline['id'], self.searcher.target_info['id'])
        np.testing.assert_array_equal(self.baseline_feature, self.searcher.target)
        self.assertEqual(self.baseline_jpeg, (self.runtime / 'target.jpg').read_bytes())

    def crowd_capture(self):
        self.write_camera(self.bus)
        captured = self.searcher.capture_target(product.CaptureRequest(name='Choose person'))
        self.assertTrue(captured['selection_required'])
        self.assertGreaterEqual(len(captured['people']), 2)
        return captured

    def test_single_person_is_recorded_as_native_camera(self):
        info = self.baseline
        self.assertEqual('native_camera', info['source'])
        self.assertEqual(self.single.shape[1], info['width'])
        self.assertEqual(self.single.shape[0], info['height'])
        self.assertEqual((512,), self.searcher.target.shape)
        self.assertAlmostEqual(1, float(np.linalg.norm(self.searcher.target)), places=5)
        self.assertFalse(self.searcher.active)
        self.records['single_automatic_capture'] = {'passed': True, 'source': info['source'],
                                                    'box': info['bbox'], 'dimensions': [info['width'], info['height']]}

    def test_selection_uses_frozen_original_after_camera_changes(self):
        capture = self.crowd_capture()
        index = 0
        frozen = self.searcher.pending_capture['image'].copy()
        box = capture['people'][index]['bbox']
        self.write_camera(self.single)
        result = self.searcher.select_capture(product.CaptureSelection(
            capture_id=capture['capture_id'], index=index, name='Selected person'))
        info = result['target']
        self.assertEqual('native_camera', info['source'])
        self.assertEqual(box, info['bbox'])
        self.assertEqual(capture['captured_at'], info['captured_at'])
        self.assertEqual(frozen.shape[1], info['width'])
        self.assertEqual(frozen.shape[0], info['height'])
        x1, y1, x2, y2 = box
        expected = self.extractor.embed(frozen[y1:y2, x1:x2])
        cosine = float(np.dot(expected, self.searcher.target))
        self.assertGreater(cosine, .99999)
        self.assertIsNone(self.searcher.pending_capture)
        saved = cv2.imread(str(self.runtime / 'target.jpg'))
        self.assertEqual(list(frozen.shape), list(saved.shape))
        self.records['frozen_selection'] = {'passed': True, 'choices': len(capture['people']),
                                           'chosen_index': index, 'bbox': box,
                                           'same_frozen_crop_cosine': cosine,
                                           'saved_original_shape': list(saved.shape),
                                           'replacement_camera_shape': list(self.single.shape)}

    def test_expired_capture_is_rejected_and_keeps_old_target(self):
        capture = self.crowd_capture()
        self.searcher.pending_capture['expires'] = time.monotonic() - 1
        with self.assertRaisesRegex(ValueError, '过期或已被替换'):
            self.searcher.select_capture(product.CaptureSelection(capture_id=capture['capture_id'], index=0))
        self.assert_old_target_preserved()
        self.records['expired_capture_rejected'] = True

    def test_replaced_capture_id_is_rejected_and_keeps_old_target(self):
        first = self.crowd_capture()
        second = self.crowd_capture()
        self.assertNotEqual(first['capture_id'], second['capture_id'])
        with self.assertRaisesRegex(ValueError, '过期或已被替换'):
            self.searcher.select_capture(product.CaptureSelection(capture_id=first['capture_id'], index=0))
        self.assert_old_target_preserved()
        self.records['replaced_capture_rejected'] = True

    def test_out_of_range_person_is_rejected_and_keeps_old_target(self):
        capture = self.crowd_capture()
        with self.assertRaisesRegex(ValueError, '请选择画面中的人物框'):
            self.searcher.select_capture(product.CaptureSelection(capture_id=capture['capture_id'], index=99))
        self.assert_old_target_preserved()
        self.records['invalid_index_rejected'] = True

    def test_no_person_is_rejected_and_keeps_old_target(self):
        self.write_camera(np.zeros((360, 640, 3), dtype=np.uint8))
        with self.assertRaisesRegex(ValueError, '没有清晰的人物'):
            self.searcher.capture_target(product.CaptureRequest())
        self.assert_old_target_preserved()
        self.records['no_person_rejected'] = True

    def test_expired_or_stopped_camera_keeps_old_target(self):
        for label, age, running in [('expired', 3, True), ('stopped', 0, False)]:
            with self.subTest(label=label):
                self.write_camera(self.single, age=age, running=running)
                with self.assertRaisesRegex(ValueError, '摄像头当前不在线'):
                    self.searcher.capture_target(product.CaptureRequest())
                self.assert_old_target_preserved()
        self.records['expired_and_stopped_camera_rejected'] = True


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CaptureTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    from go2_platform_write import write
    report = dict(CaptureTests.records, tested_at_unix=time.time(),
                  tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                  live_camera_files_changed=False, hardware_sdk_used=False, motion_commands=[])
    write(REPORT, json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
