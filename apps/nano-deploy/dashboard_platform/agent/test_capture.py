from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from .capture import CaptureManager
from .config import AgentSettings
from .logs import LogBuffer


class CaptureGalleryTests(unittest.TestCase):
    def write_manifest(self, root: Path, name: str, rows: list[list[str]]) -> None:
        manifest_dir = root / "manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        with (manifest_dir / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["frame_path", "captured_at", "scene", "source", "note"])
            writer.writerows(rows)

    def test_lists_completed_sessions_without_absolute_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_frame = root / "images" / "gate_2026-07-20" / "first.jpg"
            second_frame = root / "images" / "gate_2026-07-20" / "second.jpg"
            ir_frame = root / "images" / "road_2026-07-21" / "first.jpg"
            for frame in (first_frame, second_frame, ir_frame):
                frame.parent.mkdir(parents=True, exist_ok=True)
                frame.write_bytes(b"fixture")
            self.write_manifest(
                root,
                "gate_2026-07-20.csv",
                [
                    [str(first_frame), "2026-07-20T09:00:00+08:00", "gate_a_day", "rtsp://camera/video1", "入口白天"],
                    [str(second_frame), "2026-07-20T09:00:01+08:00", "gate_a_day", "rtsp://camera/video1", "入口白天"],
                ],
            )
            self.write_manifest(
                root,
                "road_2026-07-21.csv",
                [[str(ir_frame), "2026-07-21T20:00:00+08:00", "road_b_night", "rtsp://camera/video2", "夜间红外"]],
            )

            settings = AgentSettings(
                _env_file=None,
                capture_output_root=root,
                capture_workdir=root,
                capture_script=root / "collect.py",
            )
            items = CaptureManager(settings, LogBuffer(20)).list_gallery()

            self.assertEqual([item.group for item in items], ["road_2026-07-21", "gate_2026-07-20"])
            self.assertEqual(items[0].stream_mode, "ir_raw")
            self.assertEqual(items[0].frames_saved, 1)
            self.assertEqual(items[0].latest_frame_name, "first.jpg")
            self.assertEqual(items[1].scene, "gate_a_day")
            self.assertEqual(items[1].frames_saved, 2)
            self.assertNotIn(str(root), items[1].manifest_name)
            manager = CaptureManager(settings, LogBuffer(20))
            self.assertEqual(manager.gallery_latest_frame_path("gate_2026-07-20"), second_frame.resolve())
            self.assertEqual([frame.name for frame in manager.gallery_frames("gate_2026-07-20")], ["first.jpg", "second.jpg"])
            self.assertEqual(manager.gallery_frame_path("gate_2026-07-20", "second.jpg"), second_frame.resolve())
            with self.assertRaises(ValueError):
                manager.gallery_latest_frame_path("../outside")
            with self.assertRaises(ValueError):
                manager.gallery_frame_path("gate_2026-07-20", "../outside.jpg")

    def test_paginates_and_skips_empty_manifests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(3):
                frame = root / "images" / f"scene_{index}" / "frame.jpg"
                frame.parent.mkdir(parents=True, exist_ok=True)
                frame.write_bytes(b"fixture")
                self.write_manifest(
                    root,
                    f"scene_{index}.csv",
                    [[str(frame), f"2026-07-2{index}T10:00:00+08:00", f"scene_{index}", "rtsp://camera/video1", ""]],
                )
            (root / "manifests" / "empty.csv").write_text("frame_path,captured_at,scene,source,note\n", encoding="utf-8")
            settings = AgentSettings(_env_file=None, capture_output_root=root)
            manager = CaptureManager(settings, LogBuffer(20))

            self.assertEqual(len(manager.list_gallery(page=1, page_size=2)), 2)
            self.assertEqual(len(manager.list_gallery(page=2, page_size=2)), 1)
            self.assertEqual(manager.list_gallery(page=3, page_size=2), [])


if __name__ == "__main__":
    unittest.main()
