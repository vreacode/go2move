import tempfile
import unittest
from pathlib import Path

from tools.validate_ros_map import validate_map


VALID_YAML = """image: test.pgm
resolution: 0.05
origin: [-1.0, -2.0, 0.0]
negate: 0
occupied_thresh: 0.65
free_thresh: 0.196
"""


class RosMapValidationTests(unittest.TestCase):
    def test_valid_map_header_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test.yaml").write_text(VALID_YAML, encoding="utf-8")
            (root / "test.pgm").write_bytes(b"P5\n2 3\n255\n" + b"\x00" * 6)
            result = validate_map(root / "test.yaml")
            self.assertTrue(result["ok"], result["errors"])
            self.assertEqual(result["image_info"]["width"], 2)
            self.assertEqual(result["image_info"]["height"], 3)

    def test_missing_image_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            yaml = root / "test.yaml"
            yaml.write_text(VALID_YAML, encoding="utf-8")
            result = validate_map(yaml)
            self.assertFalse(result["ok"])
            self.assertTrue(any("image not found" in error for error in result["errors"]))

    def test_invalid_threshold_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            yaml = root / "test.yaml"
            yaml.write_text(VALID_YAML.replace("free_thresh: 0.196", "free_thresh: 1.2"), encoding="utf-8")
            (root / "test.pgm").write_bytes(b"P5\n1 1\n255\n\x00")
            result = validate_map(yaml)
            self.assertFalse(result["ok"])
            self.assertTrue(any("free_thresh" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
