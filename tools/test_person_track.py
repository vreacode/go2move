"""视觉回归：真实 ByteTrack、采集帧新鲜度、视频输出和退出状态。"""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("go2_person_track", str(ROOT / "person_track(1).py"))
tracking = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tracking)


def detection(x, confidence=0.9):
    return {"bbox": [x, 30, x + 45, 140], "confidence": confidence}


def walking_turn_fixture():
    full = [{"bbox": [100, 20, 180, 170], "confidence": 0.9}]
    profile = [{"bbox": [130, 20, 150, 170], "confidence": 0.6}]
    low_profile = [{"bbox": [131, 20, 151, 170], "confidence": 0.22}]
    sequence = [full] * 3 + [profile] * 6 + [[]] * 3 + [low_profile] * 3 + [full] * 3
    return [[{"bbox": [box["bbox"][0] + index * 2, box["bbox"][1],
                       box["bbox"][2] + index * 2, box["bbox"][3]],
              "confidence": box["confidence"]} for box in detections]
            for index, detections in enumerate(sequence)]


class CameraFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.camera = tracking.NativeCamera(self.directory, read_timeout=0.04)
        ok, jpeg = cv2.imencode(".jpg", np.zeros((160, 320, 3), dtype=np.uint8))
        self.assertTrue(ok)
        self.jpeg = jpeg.tobytes()

    def tearDown(self):
        self.temporary.cleanup()

    def publish(self, **changes):
        status = {"running": True, "captured_at": time.time(),
                  "sha256": hashlib.sha256(self.jpeg).hexdigest()}
        status.update(changes)
        (self.directory / "latest.jpg").write_bytes(self.jpeg)
        (self.directory / "status.json").write_text(json.dumps(status))

    def test_new_frame_is_read_once(self):
        self.publish()
        ok, frame = self.camera.read()
        self.assertTrue(ok)
        self.assertEqual(frame.shape, (160, 320, 3))
        self.assertIsNone(self.camera.latest())
        with self.assertRaises(TimeoutError):
            self.camera.read()
        self.publish(captured_at=self.camera.last_capture + 0.05)
        self.assertTrue(self.camera.read()[0])

    def test_stale_stopped_future_and_mismatched_frames_are_rejected(self):
        for changes in ({"captured_at": time.time() - 10}, {"running": False},
                        {"captured_at": time.time() + 10}, {"sha256": "wrong"}):
            with self.subTest(changes=changes):
                self.publish(**changes)
                self.assertIsNone(self.camera.latest())


class ByteTrackTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((180, 400, 3), dtype=np.uint8)
        # 使用本机已安装的官方 ByteTrack；不模拟跟踪算法，不需要 GPU。
        self.tracker = tracking.PersonTracker("tensorrt", fps=10, lost_seconds=1)

    def update(self, detections):
        return tracking.normalize_tracks(self.tracker.update(detections, self.frame), self.frame.shape)

    def test_two_moving_people_keep_ids_when_detection_order_changes(self):
        initial = self.update([detection(20), detection(240)])
        ids = [person["track_id"] for person in initial]
        self.assertEqual(len(set(ids)), 2)
        for offset in range(2, 20, 2):
            result = self.update([detection(240 - offset), detection(20 + offset)])
            self.assertEqual([person["track_id"] for person in result], ids)
            self.assertLess(result[0]["bbox"][0], result[1]["bbox"][0])

    def test_synthetic_two_person_crossing_with_occlusion_only_regression(self):
        initial_ids = None
        for step in range(29):
            first = detection(40 + step * 8)
            second = {"bbox": [260 - step * 8, 55, 305 - step * 8, 165], "confidence": 0.9}
            boxes = ([] if 12 <= step <= 15 else [first]) + ([] if 19 <= step <= 21 else [second])
            people = self.update(boxes[::-1] if step % 2 else boxes)
            if initial_ids is None:
                initial_ids = [person["track_id"] for person in people]
                self.assertEqual(len(initial_ids), 2)
            expected = set(initial_ids)
            if 12 <= step <= 15:
                expected.remove(initial_ids[0])
            if 19 <= step <= 21:
                expected.remove(initial_ids[1])
            self.assertEqual({person["track_id"] for person in people}, expected)
            # 使用已知合成运动轨迹核对身份；不只检查 ID 集合。
            for person in people:
                target = first if person["track_id"] == initial_ids[0] else second
                self.assertLess(abs(person["bbox"][0] - target["bbox"][0]), 8)
                self.assertLess(abs(person["bbox"][1] - target["bbox"][1]), 8)

    def test_low_confidence_and_short_occlusion_keep_id(self):
        tid = self.update([detection(50)])[0]["track_id"]
        self.assertEqual(self.update([detection(52, 0.2)])[0]["track_id"], tid)
        self.assertEqual(self.update([]), [])
        self.assertEqual(self.update([]), [])
        recovered = self.update([detection(58)])
        self.assertEqual(recovered[0]["track_id"], tid)

    def test_long_real_time_gap_expires_old_id(self):
        tid = self.update([detection(50)])[0]["track_id"]
        self.tracker.age_gap(100.0, 110.0, 10, self.frame)
        self.assertEqual(self.update([detection(50)]), [])
        recovered = self.update([detection(51)])
        self.assertEqual(len(recovered), 1)
        self.assertNotEqual(recovered[0]["track_id"], tid)

    def test_drawing_uses_clipped_coordinates_without_changing_input(self):
        people = tracking.normalize_tracks([
            {"track_id": 1, "bbox": [-5, -3, 60, 190], "confidence": 0.9},
            {"track_id": 2, "bbox": [500, 0, 550, 20], "confidence": 0.9}], self.frame.shape)
        self.assertEqual(people[0]["bbox"], [0, 0, 60, 179])
        self.assertEqual(len(people), 1)
        annotated = tracking.draw_people(self.frame, people, 10, 1)
        self.assertGreater(np.count_nonzero(annotated), 0)
        self.assertEqual(np.count_nonzero(self.frame), 0)

    def test_exact_high_score_boundary_keeps_id(self):
        tid = self.update([detection(50)])[0]["track_id"]
        # 恰好等于高分阈值时也应进入高分关联，不能落入两个阶段之间。
        self.assertEqual(self.update([detection(51, 0.2)])[0]["track_id"], tid)

    def test_low_score_candidates_cannot_create_ids(self):
        for _ in range(5):
            self.assertEqual(self.update([detection(50, 0.22)]), [])
        self.assertEqual(self.tracker.alive_ids(), set())

    def test_cached_track_is_alive_but_does_not_draw_a_predicted_person(self):
        tid = self.update([detection(50)])[0]["track_id"]
        self.assertEqual(self.update([]), [])
        self.assertEqual(self.tracker.alive_ids(), {tid})
        for _ in range(12):
            self.assertEqual(self.update([]), [])
        self.assertEqual(self.tracker.alive_ids(), set())


class SinglePersonTuningTests(unittest.TestCase):
    """检测框夹具模拟转身/局部遮挡，不把它当作真人或 YOLO 准确率实测。"""

    def evaluate(self, sequence, baseline=False):
        options = dict(high_thresh=0.25, match_thresh=0.8, lost_seconds=3,
                       new_track_thresh=0.35, fuse_score=True) if baseline else {}
        tracker = tracking.PersonTracker("tensorrt", fps=5, **options)
        frame = np.zeros((200, 640, 3), dtype=np.uint8)
        metrics = tracking.TrackingMetrics()
        observations = []
        for number, detections in enumerate(sequence, 1):
            people = tracking.normalize_tracks(tracker.update(detections, frame), frame.shape)
            metrics.update(number, people, tracker.alive_ids())
            observations.append(people)
        return metrics, observations

    def test_turn_partial_body_and_occlusion_reduce_switches_against_previous_defaults(self):
        sequence = walking_turn_fixture()
        baseline, _ = self.evaluate(sequence, baseline=True)
        tuned, observations = self.evaluate(sequence)
        self.assertGreater(baseline.total_id_switches, 0)
        self.assertEqual(tuned.total_id_switches, 0)
        self.assertEqual(len(tuned.tracks), 1)
        self.assertEqual(tuned.snapshot()["max_alive_frames"], len(sequence))
        self.assertEqual(observations[9:12], [[], [], []])
        self.assertTrue(all(observations[index] for index in (3, 8, 12, 14, 17)))

    def test_four_second_occlusion_recovers_with_same_id_within_six_second_buffer(self):
        observed = [detection(100)]
        sequence = [observed] * 3 + [[]] * 20 + [observed] * 3
        baseline, _ = self.evaluate(sequence, baseline=True)
        tuned, observations = self.evaluate(sequence)
        self.assertEqual(baseline.total_id_switches, 1)
        self.assertEqual(tuned.total_id_switches, 0)
        self.assertEqual(observations[0][0]["track_id"], observations[-1][0]["track_id"])
        self.assertEqual(tuned.snapshot()["max_alive_frames"], len(sequence))
        self.assertEqual(tuned.snapshot()["max_consecutive_observed_frames"], 3)

    def test_after_buffer_expires_new_id_is_counted_as_one_switch(self):
        observed = [detection(100)]
        metrics, observations = self.evaluate([observed] * 3 + [[]] * 35 + [observed] * 3)
        self.assertNotEqual(observations[0][0]["track_id"], observations[-1][0]["track_id"])
        self.assertEqual(metrics.total_id_switches, 1)
        self.assertLess(metrics.snapshot()["max_alive_frames"], metrics.processed_frames)

    def test_nonoverlapping_box_is_not_forced_to_reuse_an_old_id(self):
        metrics, observations = self.evaluate([[detection(20)]] * 3 + [[detection(400)]] * 3)
        self.assertNotEqual(observations[0][0]["track_id"], observations[-1][0]["track_id"])
        self.assertEqual(metrics.total_id_switches, 1)

    def test_existing_service_adapter_keeps_original_defaults(self):
        # 视觉脚本的单人调参不能静默改变既有 person_search 服务的默认配置。
        from person_tracker import Tracker
        core = Tracker(frame_rate=5).tracker
        self.assertAlmostEqual(core.args.track_thresh, 0.4)
        self.assertAlmostEqual(core.args.match_thresh, 0.8)
        self.assertFalse(core.args.mot20)
        self.assertAlmostEqual(core.det_thresh, 0.5)
        self.assertEqual(core.max_time_lost, 15)

    def test_real_time_gap_ages_cache_without_creating_observations(self):
        frame = np.zeros((200, 640, 3), dtype=np.uint8)
        tracker = tracking.PersonTracker("tensorrt", fps=5)
        initial = tracker.update([detection(100)], frame)
        tracker.age_gap(100, 104, 5, frame)
        self.assertTrue(tracker.alive_ids())
        recovered = tracker.update([detection(100)], frame)
        self.assertEqual(initial[0]["track_id"], recovered[0]["track_id"])
        tracker.age_gap(104, 120, 5, frame)
        self.assertEqual(tracker.alive_ids(), set())


class TrackingMetricsTests(unittest.TestCase):
    def person(self, tid):
        return {"track_id": tid, "bbox": [20, 30, 65, 140], "confidence": 0.9}

    def test_cached_occlusion_extends_life_but_breaks_observed_streak(self):
        metrics = tracking.TrackingMetrics()
        metrics.update(1, [self.person(7)], {7})
        metrics.update(2, [self.person(7)], {7})
        metrics.update(3, [], {7})
        metrics.update(4, [self.person(7)], {7})
        result = metrics.snapshot()
        self.assertEqual(result["max_alive_frames"], 4)
        self.assertEqual(result["max_consecutive_observed_frames"], 2)
        self.assertEqual(result["total_id_switches"], 0)
        self.assertEqual(metrics.tracks[7]["observed_frames"], 3)

    def test_id_changes_across_empty_frames_and_returns_are_counted(self):
        metrics = tracking.TrackingMetrics()
        for number, tid in enumerate((1, None, 1, 2, None, 3, 1), 1):
            metrics.update(number, [self.person(tid)] if tid else [], {tid} if tid else set())
        self.assertEqual(metrics.total_id_switches, 3)
        self.assertEqual(metrics.snapshot()["confirmed_frames"], 5)
        self.assertEqual(metrics.snapshot()["max_alive_frames"], 1)

    def test_multiple_tracks_are_reported_as_ambiguous_without_guessing_a_person(self):
        metrics = tracking.TrackingMetrics()
        metrics.update(1, [self.person(1)], {1})
        metrics.update(2, [self.person(1), self.person(2)], {1, 2})
        metrics.update(3, [self.person(2)], {2})
        metrics.update(4, [], set())
        metrics.update(5, [self.person(3)], {3})
        self.assertEqual(metrics.total_id_switches, 1)
        self.assertEqual(metrics.ambiguous_frames, 1)

    def test_no_confirmed_tracks_cannot_be_used_as_stability_evidence(self):
        metrics = tracking.TrackingMetrics()
        metrics.update(1, [], set())
        metrics.update(2, [], set())
        result = metrics.snapshot()
        self.assertFalse(result["has_confirmed_tracks"])
        self.assertEqual(result["total_id_switches"], 0)
        self.assertEqual(result["max_alive_frames"], 0)

    def test_duplicate_or_skipped_metric_updates_are_rejected(self):
        metrics = tracking.TrackingMetrics()
        with self.assertRaises(ValueError):
            metrics.update(2, [], set())
        metrics.update(1, [], set())
        with self.assertRaises(ValueError):
            metrics.update(1, [], set())

    def test_default_parameters_and_invalid_new_track_gate(self):
        args = tracking.parse_args([])
        self.assertEqual(args.lost_seconds, 6)
        self.assertEqual(args.match_thresh, 0.9)
        self.assertEqual(args.track_thresh, 0.2)
        self.assertEqual(args.new_track_thresh, 0.35)
        self.assertFalse(args.fuse_score)
        for argv in (["--new-track-thresh", "0.1"], ["--new-track-thresh", "nan"],
                     ["--track-thresh", "0.1"], ["--match-thresh", "nan"], ["--show"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    tracking.parse_args(argv)


class VideoPipelineTests(unittest.TestCase):
    def test_video_eof_ids_json_and_encoded_output(self):
        class FixtureDetector:
            backend = "tensorrt"
            model_path = "fixture boxes, NOT actual YOLO inference"

            def __init__(self, _args):
                self.frame_id = 0

            def detect(self, _frame):
                self.frame_id += 1
                return [] if self.frame_id in (4, 5) else [detection(20 + self.frame_id)]

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            video = directory / "input.avi"
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (320, 180))
            self.assertTrue(writer.isOpened())
            for _ in range(8):
                writer.write(np.zeros((180, 320, 3), dtype=np.uint8))
            writer.release()
            args = tracking.parse_args(["--source", str(video), "--out-dir", str(directory / "output")])
            terminal = io.StringIO()
            with patch.object(tracking, "PersonDetector", FixtureDetector), \
                    patch.object(cv2, "imshow", side_effect=AssertionError("GUI must not run")), \
                    patch.object(cv2, "waitKey", side_effect=AssertionError("GUI must not run")), \
                    contextlib.redirect_stdout(terminal):
                self.assertEqual(tracking.run(args), 0)
            self.assertIn("单ID最大连续存活帧数=8", terminal.getvalue())
            self.assertIn("总ID切换次数=0", terminal.getvalue())
            output = next((directory / "output").iterdir())
            records = [json.loads(line) for line in (output / "tracks.jsonl").read_text().splitlines()]
            self.assertEqual(len(records), 9)
            self.assertEqual(records[-1]["reason"], "video_end")
            self.assertEqual(records[-1]["people"], [])
            self.assertFalse(records[0]["live"])
            ids = {person["track_id"] for record in records for person in record["people"]}
            self.assertEqual(len(ids), 1)
            self.assertEqual(records[3]["people"], [])
            summary = json.loads((output / "summary.json").read_text())
            self.assertEqual(summary["frames"], 8)
            self.assertEqual(summary["tracking_metrics"]["max_alive_frames"], 8)
            self.assertEqual(summary["tracking_metrics"]["max_consecutive_observed_frames"], 3)
            self.assertEqual(summary["tracking_metrics"]["total_id_switches"], 0)
            self.assertEqual(summary["tracker_config"]["lost_seconds"], 6)
            self.assertEqual(records[3]["cached_track_ids"], list(ids))
            self.assertEqual(records[-1]["tracking_metrics"], summary["tracking_metrics"])
            capture = cv2.VideoCapture(str(output / "person_track.avi"))
            self.assertTrue(capture.isOpened())
            self.assertAlmostEqual(capture.get(cv2.CAP_PROP_FPS), 10)
            decoded = 0
            while capture.read()[0]:
                decoded += 1
            capture.release()
            self.assertEqual(decoded, 8)
            self.assertEqual(cv2.imread(str(output / "latest.jpg")).shape, (180, 320, 3))


if __name__ == "__main__":
    unittest.main()
