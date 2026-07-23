from __future__ import annotations

import asyncio
import fractions
import os
import threading
import time
from contextlib import suppress
from pathlib import Path

import cv2
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCRtpSender, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaStreamError
from av import VideoFrame

from .config import AgentSettings
from .logs import LogBuffer

VIDEO_CLOCK_RATE = 90000
VIDEO_TIME_BASE = fractions.Fraction(1, VIDEO_CLOCK_RATE)


def _video_sdp_summary(label: str, sdp: str) -> str:
    lines = sdp.splitlines()
    selected: list[str] = []
    in_video = False
    for line in lines:
        if line.startswith("m="):
            in_video = line.startswith("m=video ")
        if in_video and (
            line.startswith("m=video ")
            or line.startswith("a=mid:")
            or line.startswith("a=sendrecv")
            or line.startswith("a=sendonly")
            or line.startswith("a=recvonly")
            or line.startswith("a=inactive")
            or line.startswith("a=rtpmap:")
        ):
            selected.append(line)
    return f"{label}: " + (" | ".join(selected) if selected else "no video m-line")


def _scale_size(src_width: int, src_height: int, max_width: int, max_height: int) -> tuple[int, int]:
    if src_width <= 0 or src_height <= 0 or max_width <= 0 or max_height <= 0:
        return src_width, src_height
    scale = min(max_width / src_width, max_height / src_height, 1.0)
    width = max(2, int(src_width * scale) // 2 * 2)
    height = max(2, int(src_height * scale) // 2 * 2)
    return width, height


class LatestFrameVideoTrack(VideoStreamTrack):
    kind = "video"

    def __init__(self, settings: AgentSettings, log_buffer: LogBuffer, source_label: str) -> None:
        super().__init__()
        self.settings = settings
        self.log_buffer = log_buffer
        self.source_label = source_label
        self._fps_limit = max(float(settings.webrtc_output_fps), 1.0)
        self._frame_interval = 1.0 / self._fps_limit
        self._timestamp_step = max(int(VIDEO_CLOCK_RATE / self._fps_limit), 1)
        self._last_sent_ts = 0.0
        self._timestamp = 0

        self._lock = threading.Lock()
        self._latest_frame = None
        self._latest_capture_ts = 0.0
        self._latest_seq = 0
        self._last_sent_seq = 0
        self._last_frame_shape: tuple[int, int] | None = None

        self._captured_frames = 0
        self._returned_frames = 0
        self._overwritten_frames = 0
        self._read_failures = 0
        self._recv_calls = 0
        self._last_log_ts = 0.0
        self._metrics_started_at = time.time()
        self._first_frame_logged = False

        self._stop_event = threading.Event()
        self._worker = threading.Thread(target=self._capture_loop, name=f"{source_label}-webrtc-reader", daemon=True)
        self.log_buffer.add(
            "system",
            "info",
            (
                f"webrtc track init source={source_label} "
                f"target={settings.webrtc_output_width}x{settings.webrtc_output_height}@{self._fps_limit:.1f}fps "
                f"codec={settings.webrtc_preferred_codec}"
            ),
        )
        self._worker.start()

    def _prepare_frame(self, frame):
        src_height, src_width = frame.shape[:2]
        out_width, out_height = _scale_size(
            src_width,
            src_height,
            self.settings.webrtc_output_width,
            self.settings.webrtc_output_height,
        )
        if out_width > 0 and out_height > 0 and (out_width != src_width or out_height != src_height):
            frame = cv2.resize(frame, (out_width, out_height), interpolation=cv2.INTER_AREA)
        return frame

    def _publish_frame(self, frame, capture_ts: float) -> None:
        frame = self._prepare_frame(frame)
        with self._lock:
            if self._latest_seq > self._last_sent_seq:
                self._overwritten_frames += 1
            self._captured_frames += 1
            self._latest_seq += 1
            self._latest_frame = frame
            self._latest_capture_ts = capture_ts
            self._last_frame_shape = (frame.shape[1], frame.shape[0])

        if not self._first_frame_logged:
            self._first_frame_logged = True
            self.log_buffer.add(
                "system",
                "info",
                f"webrtc first captured frame source={self.source_label} shape={frame.shape[1]}x{frame.shape[0]}",
            )

    def _capture_loop(self) -> None:
        raise NotImplementedError

    async def _next_send_timestamp(self) -> tuple[int, fractions.Fraction]:
        if self.readyState != "live":
            raise MediaStreamError

        now = time.time()
        if self._last_sent_ts:
            wait = self._last_sent_ts + self._frame_interval - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._timestamp += self._timestamp_step
        else:
            self._timestamp = 0
        self._last_sent_ts = time.time()
        return self._timestamp, VIDEO_TIME_BASE

    async def recv(self) -> VideoFrame:
        self._recv_calls += 1
        if self._recv_calls == 1:
            self.log_buffer.add("system", "info", f"webrtc recv entered first time source={self.source_label}")

        pts, time_base = await self._next_send_timestamp()
        wait_started = time.time()
        latest_frame = None
        latest_capture_ts = 0.0
        latest_seq = 0

        while True:
            with self._lock:
                latest_frame = self._latest_frame
                latest_capture_ts = self._latest_capture_ts
                latest_seq = self._latest_seq
            if latest_frame is not None:
                break
            if time.time() - wait_started > self.settings.webrtc_capture_open_timeout_ms / 1000:
                raise MediaStreamError
            await asyncio.sleep(0.01)

        send_ts = time.time()
        frame_age_ms = max((send_ts - latest_capture_ts) * 1000.0, 0.0)
        self._last_sent_seq = latest_seq

        video_frame = VideoFrame.from_ndarray(latest_frame, format="bgr24")
        video_frame.pts = pts
        video_frame.time_base = time_base
        self._returned_frames += 1

        if self._returned_frames == 1:
            width, height = self._last_frame_shape or (latest_frame.shape[1], latest_frame.shape[0])
            self.log_buffer.add(
                "system",
                "info",
                (
                    f"webrtc first frame returned source={self.source_label} "
                    f"pts={pts} time_base={time_base} shape={width}x{height} age_ms={frame_age_ms:.1f}"
                ),
            )

        if send_ts - self._last_log_ts >= 1.0:
            self._last_log_ts = send_ts
            elapsed = max(send_ts - self._metrics_started_at, 0.001)
            capture_fps = self._captured_frames / elapsed
            send_fps = self._returned_frames / elapsed
            self.log_buffer.add(
                "system",
                "info",
                (
                    f"webrtc frame stats source={self.source_label} "
                    f"recv_calls={self._recv_calls} returned={self._returned_frames} captured={self._captured_frames} "
                    f"overwritten={self._overwritten_frames} age_ms={frame_age_ms:.1f} capture_fps~={capture_fps:.1f} "
                    f"send_fps~={send_fps:.1f} send_fps_target={self._fps_limit:.1f}"
                ),
            )
            if frame_age_ms >= self.settings.webrtc_stale_frame_ms:
                self.log_buffer.add("system", "warning", f"webrtc stale frame source={self.source_label} age_ms={frame_age_ms:.1f}")

        return video_frame

    def stop(self) -> None:
        self._stop_event.set()
        super().stop()


class RtspVideoTrack(LatestFrameVideoTrack):
    def __init__(self, settings: AgentSettings, log_buffer: LogBuffer, rtsp_url: str, mode_label: str) -> None:
        self.rtsp_url = rtsp_url
        super().__init__(settings, log_buffer, source_label=f"{mode_label}:{self.rtsp_url}")

    def _open_capture(self) -> cv2.VideoCapture:
        os.environ.setdefault(
            "OPENCV_FFMPEG_CAPTURE_OPTIONS",
            "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|max_delay;0|reorder_queue_size;0",
        )
        cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _capture_loop(self) -> None:
        cap: cv2.VideoCapture | None = None
        while not self._stop_event.is_set():
            try:
                if cap is None or not cap.isOpened():
                    if cap is not None:
                        cap.release()
                    cap = self._open_capture()
                    self.log_buffer.add("system", "info", f"webrtc raw capture opened={cap.isOpened()}")
                    if not cap.isOpened():
                        time.sleep(0.3)
                        continue

                ok, frame = cap.read()
                if not ok or frame is None:
                    self._read_failures += 1
                    if self._read_failures in {1, 10, 20, 30}:
                        self.log_buffer.add("system", "warning", f"webrtc raw capture read failed count={self._read_failures}")
                    if self._read_failures >= 30:
                        self.log_buffer.add("system", "warning", "webrtc raw capture reopening after repeated read failures")
                        cap.release()
                        cap = None
                        self._read_failures = 0
                        time.sleep(0.2)
                    else:
                        time.sleep(0.02)
                    continue

                self._read_failures = 0
                self._publish_frame(frame, time.time())
            except Exception as exc:
                self.log_buffer.add("system", "error", f"webrtc raw capture loop error: {exc!r}")
                time.sleep(0.2)

        if cap is not None and cap.isOpened():
            cap.release()


class SnapshotVideoTrack(LatestFrameVideoTrack):
    def __init__(self, settings: AgentSettings, log_buffer: LogBuffer) -> None:
        self.snapshot_path = settings.snapshot_path / "latest.jpg"
        super().__init__(settings, log_buffer, source_label=f"annotated:{self.snapshot_path}")
        self._last_mtime = 0.0

    def _capture_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if not self.snapshot_path.exists():
                    time.sleep(0.05)
                    continue
                stat = self.snapshot_path.stat()
                if stat.st_mtime <= self._last_mtime:
                    time.sleep(0.01)
                    continue
                frame = cv2.imread(str(self.snapshot_path))
                if frame is None:
                    self._read_failures += 1
                    if self._read_failures in {1, 10, 20}:
                        self.log_buffer.add("system", "warning", f"webrtc annotated snapshot read failed count={self._read_failures}")
                    time.sleep(0.02)
                    continue

                self._read_failures = 0
                self._last_mtime = stat.st_mtime
                self._publish_frame(frame, stat.st_mtime)
            except Exception as exc:
                self.log_buffer.add("system", "error", f"webrtc annotated capture loop error: {exc!r}")
                time.sleep(0.05)


class WebRTCManager:
    def __init__(self, settings: AgentSettings, log_buffer: LogBuffer) -> None:
        self.settings = settings
        self.log_buffer = log_buffer
        self.peers: set[RTCPeerConnection] = set()

    def _configuration(self) -> RTCConfiguration:
        raw = self.settings.parse_json(self.settings.webrtc_ice_servers, [])
        servers = []
        for item in raw:
            if isinstance(item, str):
                servers.append(RTCIceServer(urls=item))
            elif isinstance(item, dict):
                servers.append(RTCIceServer(**item))
        return RTCConfiguration(iceServers=servers)

    def _preferred_codecs(self) -> list:
        preferred = (self.settings.webrtc_preferred_codec or "").strip().upper()
        codecs = list(RTCRtpSender.getCapabilities("video").codecs)
        if not preferred:
            return codecs
        exact = [codec for codec in codecs if codec.mimeType.upper() == f"VIDEO/{preferred}"]
        return exact if exact else codecs

    async def _wait_ice_complete(self, pc: RTCPeerConnection) -> None:
        for _ in range(100):
            if pc.iceGatheringState == "complete":
                return
            await asyncio.sleep(0.1)

    def _build_track(self, stream_mode: str) -> LatestFrameVideoTrack:
        if stream_mode == "annotated":
            return SnapshotVideoTrack(self.settings, self.log_buffer)
        if stream_mode == "ir_raw":
            return RtspVideoTrack(self.settings, self.log_buffer, self.settings.ir_rtsp_url, "ir_raw")
        return RtspVideoTrack(
            self.settings,
            self.log_buffer,
            self.settings.visible_rtsp_url or self.settings.model_default_source,
            "visible_raw",
        )

    async def create_answer(self, sdp: str, type_: str, stream_mode: str) -> dict[str, str]:
        pc = RTCPeerConnection(configuration=self._configuration())
        self.peers.add(pc)
        track = self._build_track(stream_mode)
        self.log_buffer.add("system", "info", f"webrtc requested stream_mode={stream_mode}")
        self.log_buffer.add("system", "info", _video_sdp_summary("webrtc remote offer", sdp))

        @pc.on("connectionstatechange")
        async def _on_state_change() -> None:
            self.log_buffer.add("system", "info", f"webrtc state={pc.connectionState}")
            if pc.connectionState in {"failed", "closed", "disconnected"}:
                with suppress(Exception):
                    track.stop()
                await pc.close()
                self.peers.discard(pc)

        await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=type_))
        sender = pc.addTrack(track)
        transceiver = next((item for item in pc.getTransceivers() if item.sender == sender), None)
        if transceiver is not None:
            preferred = self._preferred_codecs()
            transceiver.setCodecPreferences(preferred)
            codec_names = ",".join(
                codec.mimeType[len("video/"):] if codec.mimeType.startswith("video/") else codec.mimeType
                for codec in preferred
            )
            self.log_buffer.add("system", "info", f"webrtc codec preference={codec_names}")
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        await self._wait_ice_complete(pc)
        local = pc.localDescription
        self.log_buffer.add("system", "info", _video_sdp_summary("webrtc local answer", local.sdp))
        return {"sdp": local.sdp, "type": local.type}

    async def close(self) -> None:
        for pc in list(self.peers):
            with suppress(Exception):
                await pc.close()
        self.peers.clear()
