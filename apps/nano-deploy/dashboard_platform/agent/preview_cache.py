"""Persistent read-only preview cache for browser-compatible snapshots."""

from __future__ import annotations

import threading
from typing import Any

from tools.camera_adapter import open_camera


def encode_preview_frame(cv2: Any, frame: Any, max_width: int, max_height: int) -> bytes | None:
    if frame is None or not getattr(frame, "size", 0):
        return None
    height, width = frame.shape[:2]
    scale = min(max(int(max_width), 2) / width, max(int(max_height), 2) / height, 1.0)
    if scale < 1.0:
        output_width = max(2, int(width * scale) // 2 * 2)
        output_height = max(2, int(height * scale) // 2 * 2)
        frame = cv2.resize(frame, (output_width, output_height), interpolation=cv2.INTER_AREA)
    ok, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    return jpeg.tobytes() if ok else None


class PreviewFrameCache:
    """Keep one persistent adapter and publish its newest complete JPEG."""

    def __init__(self, source: str, source_type: str, width: int, height: int) -> None:
        self.source = source
        self.source_type = source_type
        self.width = width
        self.height = height
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._latest: bytes | None = None
        self._thread: threading.Thread | None = None

    def _ensure_started(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="go2-preview-cache", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        import cv2

        capture = None
        failures = 0
        try:
            while not self._stop.is_set():
                try:
                    if capture is None or not capture.isOpened():
                        if capture is not None:
                            capture.release()
                        capture = open_camera(self.source, self.source_type)
                        if not capture.isOpened():
                            capture.release()
                            capture = None
                            self._stop.wait(0.25)
                            continue

                    ok, frame = capture.read()
                    if not ok or frame is None:
                        failures += 1
                        if failures >= 5:
                            capture.release()
                            capture = None
                            failures = 0
                        self._stop.wait(0.05)
                        continue

                    failures = 0
                    jpeg = encode_preview_frame(cv2, frame, self.width, self.height)
                    if jpeg:
                        with self._lock:
                            self._latest = jpeg
                        self._ready.set()
                except Exception:
                    if capture is not None:
                        try:
                            capture.release()
                        except Exception:
                            pass
                        capture = None
                    self._stop.wait(0.25)
        finally:
            if capture is not None:
                try:
                    capture.release()
                except Exception:
                    pass

    def get(self, timeout_seconds: float = 5.0) -> bytes | None:
        self._ensure_started()
        self._ready.wait(timeout=max(float(timeout_seconds), 0.1))
        with self._lock:
            return self._latest

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)


class PreviewCacheManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._caches: dict[str, PreviewFrameCache] = {}

    def get(self, mode: str, source: str, source_type: str, width: int, height: int) -> PreviewFrameCache:
        key = f"{mode}:{source_type}:{source}"
        with self._lock:
            cache = self._caches.get(key)
            if cache is None:
                cache = PreviewFrameCache(source, source_type, width, height)
                self._caches[key] = cache
            return cache

    def stop(self) -> None:
        with self._lock:
            caches = list(self._caches.values())
        for cache in caches:
            cache.stop()
