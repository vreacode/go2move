import csv
import tempfile
import unittest
from pathlib import Path

from tools.prepare_yolo_dataset import assign_splits, prepare, read_manifest
from tools.validate_yolo_dataset import validate


class DatasetToolTests(unittest.TestCase):
    def test_split_keeps_each_group_in_one_partition(self):
        rows = []
        for group in ("gate__2026-07-18", "road__2026-07-18", "gate__2026-07-19", "road__2026-07-19"):
            rows.extend([type("Row", (), {"group": group})(), type("Row", (), {"group": group})()])
        assigned = assign_splits(rows, val_ratio=0.5, seed=7)
        self.assertEqual(set(assigned), {"gate__2026-07-18", "road__2026-07-18", "gate__2026-07-19", "road__2026-07-19"})

    def test_prepare_and_validate_accept_valid_label(self):
        with tempfile.TemporaryDirectory() as raw_dir, tempfile.TemporaryDirectory() as output_dir:
            raw = Path(raw_dir)
            source = raw / "frame.jpg"
            source.write_bytes(b"test image bytes")
            manifest = raw / "capture.csv"
            with manifest.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["frame_path", "captured_at", "scene", "source", "note"])
                writer.writerow([str(source), "2026-07-18T10:00:00+08:00", "gate", "rtsp://test", "fixture"])
            source.with_suffix(".txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")

            output = Path(output_dir) / "dataset"
            rows = read_manifest(manifest)
            counts = prepare(rows, output, val_ratio=0.2, seed=1, names=["garbage", "vehicle"])
            self.assertEqual(counts, {"train": 1, "val": 0})
            result = validate(output / "dataset.yaml", allow_missing=False)
            self.assertFalse(result["ok"], "a one-group fixture must fail because val is empty")

    def test_two_groups_produce_a_train_and_validation_split(self):
        with tempfile.TemporaryDirectory() as raw_dir, tempfile.TemporaryDirectory() as output_dir:
            raw = Path(raw_dir)
            manifest = raw / "capture.csv"
            with manifest.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["frame_path", "captured_at", "scene", "source", "note"])
                for index, scene in enumerate(("gate", "road"), start=1):
                    source = raw / f"frame_{index}.jpg"
                    source.write_bytes(b"fixture")
                    source.with_suffix(".txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
                    writer.writerow([str(source), f"2026-07-{17 + index}T10:00:00+08:00", scene, "rtsp://test", "fixture"])

            output = Path(output_dir) / "dataset"
            counts = prepare(read_manifest(manifest), output, val_ratio=0.5, seed=1, names=["garbage", "vehicle"])
            self.assertEqual(counts, {"train": 1, "val": 1})
            result = validate(output / "dataset.yaml", allow_missing=False)
            self.assertTrue(result["ok"], result["errors"])

    def test_invalid_box_is_rejected(self):
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            (root / "images/train").mkdir(parents=True)
            (root / "images/val").mkdir(parents=True)
            (root / "labels/train").mkdir(parents=True)
            (root / "labels/val").mkdir(parents=True)
            (root / "images/train/frame.jpg").write_bytes(b"fixture")
            (root / "labels/train/frame.txt").write_text("1 0.5 0.5 2.0 0.2\n", encoding="utf-8")
            yaml = root / "dataset.yaml"
            yaml.write_text("path: .\ntrain: images/train\nval: images/val\n\nnames:\n  0: garbage\n  1: vehicle\n", encoding="utf-8")
            result = validate(yaml, allow_missing=False)
            self.assertFalse(result["ok"])
            self.assertTrue(any("outside" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
