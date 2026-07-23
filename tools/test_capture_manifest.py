import csv
import tempfile
import unittest
from pathlib import Path

from tools.capture_manifest_report import summarize
from tools.prepare_yolo_dataset import read_manifest


class CaptureManifestTests(unittest.TestCase):
    def test_summary_warns_for_single_scene_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "frame.jpg"
            image.write_bytes(b"fixture")
            manifest = root / "capture.csv"
            with manifest.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["frame_path", "captured_at", "scene", "source", "note"])
                writer.writerow([str(image), "2026-07-19T10:00:00+08:00", "gate", "rtsp://visible", "fixture"])
            result = summarize(read_manifest(manifest))
            self.assertFalse(result["ready_for_grouped_split"])
            self.assertEqual(result["groups"][0]["frames"], 1)
            self.assertTrue(any("two scene/date groups" in warning for warning in result["warnings"]))

    def test_summary_marks_two_groups_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "capture.csv"
            with manifest.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["frame_path", "captured_at", "scene", "source", "note"])
                for index, scene in enumerate(("gate", "road"), start=1):
                    image = root / f"frame_{index}.jpg"
                    image.write_bytes(b"fixture")
                    writer.writerow([str(image), f"2026-07-{index + 18:02d}T10:00:00+08:00", scene, "rtsp://visible", "fixture"])
            result = summarize(read_manifest(manifest))
            self.assertTrue(result["ready_for_grouped_split"])
            self.assertEqual(len(result["groups"]), 2)


if __name__ == "__main__":
    unittest.main()
