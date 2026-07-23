"""Small dependency-free tracking and illegal-parking rules.

The detector only needs to provide bounding boxes.  This module deliberately
does not publish ROS messages or send vehicle commands: it turns detections
into reviewable events for the dashboard layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import inf
from typing import Iterable, Sequence

BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    bbox: BBox
    track_id: int | None = None

    def __post_init__(self) -> None:
        x1, y1, x2, y2 = self.bbox
        if x2 < x1 or y2 < y1:
            raise ValueError(f"invalid bbox: {self.bbox}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")

    @property
    def footpoint(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) / 2.0, y2)


@dataclass(frozen=True)
class NoParkingZone:
    """A normalized image ROI, coordinates in the range [0, 1]."""

    name: str
    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self) -> None:
        values = (self.x1, self.y1, self.x2, self.y2)
        if any(value < 0.0 or value > 1.0 for value in values):
            raise ValueError("no-parking zone coordinates must be normalized to [0, 1]")
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("no-parking zone must have positive area")

    def contains_pixel(self, point: tuple[float, float], frame_size: tuple[int, int]) -> bool:
        width, height = frame_size
        if width <= 0 or height <= 0:
            raise ValueError("frame dimensions must be positive")
        x, y = point
        nx, ny = x / width, y / height
        return self.x1 <= nx <= self.x2 and self.y1 <= ny <= self.y2


@dataclass(frozen=True)
class Event:
    event_type: str
    timestamp: float
    track_id: int
    zone: str
    confidence: float
    bbox: BBox

    def as_dict(self) -> dict[str, object]:
        return {
            "type": self.event_type,
            "timestamp": self.timestamp,
            "track_id": self.track_id,
            "zone": self.zone,
            "confidence": self.confidence,
            "bbox": list(self.bbox),
        }


@dataclass
class _Track:
    track_id: int
    label: str
    bbox: BBox
    last_seen: float
    missed: int = 0


def intersection_over_union(first: BBox, second: BBox) -> float:
    ax1, ay1, ax2, ay2 = first
    bx1, by1, bx2, by2 = second
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    first_area = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    second_area = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = first_area + second_area - intersection
    return intersection / union if union else 0.0


class IoUTracker:
    """A conservative frame-to-frame tracker for the first MVP.

    It uses greedy IoU matching and only matches detections with the same
    label.  It is intentionally simple; ByteTrack can replace it later
    without changing the event-rule interface.
    """

    def __init__(self, iou_threshold: float = 0.3, max_missed: int = 5) -> None:
        if not 0.0 < iou_threshold <= 1.0:
            raise ValueError("iou_threshold must be in (0, 1]")
        if max_missed < 0:
            raise ValueError("max_missed must be non-negative")
        self.iou_threshold = iou_threshold
        self.max_missed = max_missed
        self._next_id = 1
        self._tracks: dict[int, _Track] = {}
        self._last_timestamp = -inf

    def update(self, timestamp: float, detections: Sequence[Detection]) -> list[Detection]:
        if timestamp < self._last_timestamp:
            raise ValueError("timestamps must be non-decreasing")
        self._last_timestamp = timestamp

        pairs: list[tuple[float, int, int]] = []
        track_values = list(self._tracks.values())
        for detection_index, detection in enumerate(detections):
            for track in track_values:
                if track.label != detection.label:
                    continue
                score = intersection_over_union(track.bbox, detection.bbox)
                if score >= self.iou_threshold:
                    pairs.append((score, track.track_id, detection_index))

        matched_tracks: set[int] = set()
        matched_detections: set[int] = set()
        assigned: dict[int, int] = {}
        for _, track_id, detection_index in sorted(pairs, reverse=True):
            if track_id in matched_tracks or detection_index in matched_detections:
                continue
            matched_tracks.add(track_id)
            matched_detections.add(detection_index)
            assigned[detection_index] = track_id

        for track in track_values:
            if track.track_id not in matched_tracks:
                track.missed += 1
        for track_id in [tid for tid, track in self._tracks.items() if track.missed > self.max_missed]:
            del self._tracks[track_id]

        output: list[Detection] = []
        for detection_index, detection in enumerate(detections):
            track_id = assigned.get(detection_index)
            if track_id is None:
                track_id = self._next_id
                self._next_id += 1
                self._tracks[track_id] = _Track(track_id, detection.label, detection.bbox, timestamp)
            else:
                track = self._tracks[track_id]
                track.bbox = detection.bbox
                track.last_seen = timestamp
                track.missed = 0
            output.append(
                Detection(detection.label, detection.confidence, detection.bbox, track_id=track_id)
            )
        return output


class IllegalParkingRule:
    """Emit an illegal-parking event after a vehicle dwells inside an ROI."""

    def __init__(
        self,
        zone: NoParkingZone,
        dwell_seconds: float = 5.0,
        min_confidence: float = 0.5,
        cooldown_seconds: float = 30.0,
        max_missing_seconds: float = 1.0,
    ) -> None:
        if dwell_seconds < 0.0 or cooldown_seconds < 0.0 or max_missing_seconds < 0.0:
            raise ValueError("time thresholds must be non-negative")
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be in [0, 1]")
        self.zone = zone
        self.dwell_seconds = dwell_seconds
        self.min_confidence = min_confidence
        self.cooldown_seconds = cooldown_seconds
        self.max_missing_seconds = max_missing_seconds
        self._inside_since: dict[int, float] = {}
        self._last_seen: dict[int, float] = {}
        self._last_emit: dict[int, float] = {}
        self._last_timestamp = -inf

    def update(
        self,
        timestamp: float,
        detections: Iterable[Detection],
        frame_size: tuple[int, int],
    ) -> list[Event]:
        if timestamp < self._last_timestamp:
            raise ValueError("timestamps must be non-decreasing")
        self._last_timestamp = timestamp
        events: list[Event] = []
        seen: set[int] = set()

        for detection in detections:
            if detection.label != "vehicle" or detection.track_id is None:
                continue
            if detection.confidence < self.min_confidence:
                continue
            track_id = detection.track_id
            seen.add(track_id)
            self._last_seen[track_id] = timestamp
            inside = self.zone.contains_pixel(detection.footpoint, frame_size)
            if not inside:
                self._inside_since.pop(track_id, None)
                continue

            started = self._inside_since.setdefault(track_id, timestamp)
            last_emit = self._last_emit.get(track_id, -inf)
            if timestamp - started >= self.dwell_seconds and timestamp - last_emit >= self.cooldown_seconds:
                self._last_emit[track_id] = timestamp
                events.append(
                    Event(
                        event_type="illegal_parking",
                        timestamp=timestamp,
                        track_id=track_id,
                        zone=self.zone.name,
                        confidence=detection.confidence,
                        bbox=detection.bbox,
                    )
                )

        for track_id, last_seen in list(self._last_seen.items()):
            if track_id not in seen and timestamp - last_seen > self.max_missing_seconds:
                self._last_seen.pop(track_id, None)
                self._inside_since.pop(track_id, None)

        return events
