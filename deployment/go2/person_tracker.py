#!/usr/bin/env python3
"""Small NumPy adapter around official FoundationVision/ByteTrack.

The vendored Kalman, high/low-score matching and track lifecycle algorithm is
unchanged apart from an inclusive high-score boundary fix. This module adds
input/output conversion and optional score-fusion/new-track controls; no torch is loaded.
Dependencies lap/cython_bbox live in a separate local target directory so the
existing NumPy/SciPy/ROS environment remains unchanged.
"""
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np

_DEPENDENCIES = Path('/home/unitree/go2_ros2_ws/models/person_search/tracker_deps')
if str(_DEPENDENCIES) not in sys.path:
    sys.path.insert(0, str(_DEPENDENCIES))
from bytetrack_vendor.byte_tracker import BYTETracker


class Tracker:
    def __init__(self, frame_rate=2, track_thresh=.4, match_thresh=.8, lost_seconds=3,
                 new_track_thresh=None, fuse_score=True):
        if (not np.isfinite([frame_rate, track_thresh, match_thresh, lost_seconds]).all()
                or frame_rate <= 0 or not 0 < track_thresh < 1 or not 0 < match_thresh <= 1):
            raise ValueError('Tracker rates and thresholds must be positive')
        if lost_seconds <= 0:
            raise ValueError('lost_seconds must be positive')
        if new_track_thresh is not None and not track_thresh <= new_track_thresh <= 1:
            raise ValueError('new_track_thresh must be between track_thresh and 1')
        # Upstream rescales this 30-fps buffer by frame_rate / 30.
        args = SimpleNamespace(track_thresh=float(track_thresh), match_thresh=float(match_thresh),
                               track_buffer=max(1, round(float(lost_seconds) * 30)),
                               # Upstream uses mot20 solely to disable score fusion.
                               mot20=not fuse_score)
        self.tracker = BYTETracker(args, frame_rate=float(frame_rate))
        if new_track_thresh is not None:
            self.tracker.det_thresh = float(new_track_thresh)

    def update(self, detections, image_shape):
        """Accept Nx5 [x1,y1,x2,y2,score] or Detector's bbox/confidence dicts.

        Boxes are already in the supplied frame's pixels. Empty detections are
        passed through the original tracker to age lost tracks without inventing
        targets. Returns only currently observed, activated tracks.
        """
        if len(image_shape) < 2 or min(image_shape[:2]) <= 0:
            raise ValueError('image_shape must contain positive height and width')
        if isinstance(detections, np.ndarray):
            array = np.array(detections, dtype=np.float64, copy=True)
        else:
            array = np.asarray([list(item['bbox']) + [item['confidence']] for item in detections], dtype=np.float64)
        if array.size == 0:
            array = np.empty((0, 5), dtype=np.float64)
        if array.ndim != 2 or array.shape[1] != 5 or not np.isfinite(array).all():
            raise ValueError('detections must have five finite columns')
        if len(array) and ((array[:, 2] <= array[:, 0]).any() or (array[:, 3] <= array[:, 1]).any()
                           or (array[:, 4] < 0).any() or (array[:, 4] > 1).any()):
            raise ValueError('Detection boxes and confidence values are invalid')
        size = tuple(map(int, image_shape[:2]))
        tracks = self.tracker.update(array, size, size)
        return [{'bbox': track.tlbr.astype(float).tolist(), 'track_id': int(track.track_id),
                 'confidence': float(track.score)} for track in tracks]
