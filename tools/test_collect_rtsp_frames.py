import time
import unittest
from types import SimpleNamespace

from tools.collect_rtsp_frames import show_preview


class FakeFrame:
    shape = (480, 640, 3)

    def copy(self):
        return self


class FakeCv2:
    FONT_HERSHEY_SIMPLEX = 0
    LINE_AA = 0

    def __init__(self, key: int):
        self.key = key
        self.imshow_calls = 0

    def rectangle(self, *_args):
        return None

    def putText(self, *_args):
        return None

    def imshow(self, *_args):
        self.imshow_calls += 1

    def waitKey(self, _delay):
        return self.key


class CapturePreviewTests(unittest.TestCase):
    def test_preview_returns_true_for_q(self):
        cv2 = FakeCv2(ord("q"))
        args = SimpleNamespace(scene="gate_a_day", interval=1.0, duration=0.0)
        self.assertTrue(show_preview(cv2, FakeFrame(), args, saved=3, next_save_at=time.monotonic()))
        self.assertEqual(cv2.imshow_calls, 1)

    def test_preview_continues_for_other_key(self):
        cv2 = FakeCv2(-1)
        args = SimpleNamespace(scene="gate_a_day", interval=1.0, duration=0.0)
        self.assertFalse(show_preview(cv2, FakeFrame(), args, saved=0, next_save_at=time.monotonic() + 1))


if __name__ == "__main__":
    unittest.main()
