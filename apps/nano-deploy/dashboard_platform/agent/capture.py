from __future__ import annotations

import asyncio
import csv
import os
import re
import signal
from contextlib import suppress
from datetime import datetime
from pathlib import Path

from .config import AgentSettings
from .logs import LogBuffer
from .schemas import CaptureFrameItem, CaptureGalleryItem, CaptureStartRequest, CaptureStatus


class CaptureManager:
    """Own one bounded, manifest-producing RTSP capture process."""

    def __init__(self, settings: AgentSettings, log_buffer: LogBuffer) -> None:
        self.settings = settings
        self.log_buffer = log_buffer
        self.process: asyncio.subprocess.Process | None = None
        self._stdout_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._wait_task: asyncio.Task[None] | None = None
        # Create the lock lazily inside an async call. Python 3.8 binds an
        # asyncio.Lock to the current event loop during construction, while
        # gallery-only callers and unit tests may not have a loop yet.
        self._lock: asyncio.Lock | None = None
        self._status = CaptureStatus()
        self._latest_frame: Path | None = None

    @staticmethod
    def slug_scene(scene: str) -> str:
        value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", scene.strip())
        value = value.strip("._-")
        if not value:
            raise ValueError("scene must contain letters, numbers, Chinese characters, '_' or '-'")
        return value[:64]

    def status(self) -> CaptureStatus:
        running = bool(self.process and self.process.returncode is None)
        latest = str(self._latest_frame) if self._latest_frame and self._latest_frame.is_file() else None
        return self._status.model_copy(
            update={
                "running": running,
                "pid": self.process.pid if running and self.process else None,
                "latest_frame": latest,
            }
        )

    def _capture_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def latest_frame_path(self) -> Path | None:
        if self._latest_frame and self._latest_frame.is_file():
            return self._latest_frame
        return None

    @staticmethod
    def _stream_mode(source: str, source_type: str = "") -> str:
        if source_type.strip().lower() in {"go2_ros2", "go2_ros2_videohub", "unitree_sdk2"}:
            return "visible_raw"
        value = source.lower()
        if "video2" in value or "infrared" in value or "ir_raw" in value:
            return "ir_raw"
        if "video1" in value or "visible" in value or "videohub" in value or "frontvideostream" in value:
            return "visible_raw"
        return "unknown"

    @staticmethod
    def _is_transient_decoder_warning(line: str) -> bool:
        return bool(re.search(r"(?:hevc|h264).*Could not find ref with POC|Could not find ref with POC", line, re.IGNORECASE))

    def list_gallery(self, page: int = 1, page_size: int = 20) -> list[CaptureGalleryItem]:
        """Read completed capture sessions without exposing absolute filesystem paths."""
        if page < 1 or page_size < 1:
            raise ValueError("page and page_size must be positive")
        manifest_root = (self.settings.capture_output_root.expanduser().resolve() / "manifests").resolve()
        root = self.settings.capture_output_root.expanduser().resolve()
        if root not in manifest_root.parents:
            raise RuntimeError("capture manifest path must stay inside capture_output_root")

        items: list[CaptureGalleryItem] = []
        for manifest_path in sorted(manifest_root.glob("*.csv"), key=lambda path: path.stat().st_mtime, reverse=True):
            rows: list[dict[str, str]] = []
            try:
                with manifest_path.open("r", newline="", encoding="utf-8") as handle:
                    rows = [row for row in csv.DictReader(handle) if row.get("frame_path")]
            except (OSError, UnicodeError, csv.Error) as exc:
                self.log_buffer.add("capture", "warning", f"skip invalid manifest {manifest_path.name}: {exc}")
                continue
            if not rows:
                continue

            timestamps = sorted(row.get("captured_at", "").strip() for row in rows if row.get("captured_at", "").strip())
            latest_frame_name: str | None = None
            latest_timestamp = ""
            stream_modes: set[str] = set()
            for row in rows:
                source = row.get("source", "")
                stream_modes.add(self._stream_mode(source, row.get("source_type", "")))
                frame_path = Path(row["frame_path"]).expanduser().resolve()
                if root not in frame_path.parents or not frame_path.is_file():
                    continue
                captured_at = row.get("captured_at", "")
                if not latest_frame_name or captured_at >= latest_timestamp:
                    latest_frame_name = frame_path.name
                    latest_timestamp = captured_at

            scene = next((row.get("scene", "").strip() for row in rows if row.get("scene", "").strip()), manifest_path.stem)
            modes = stream_modes - {"unknown"}
            stream_mode = next(iter(modes)) if len(modes) == 1 else "unknown"
            items.append(
                CaptureGalleryItem(
                    group=manifest_path.stem,
                    scene=scene,
                    stream_mode=stream_mode,
                    frames_saved=len(rows),
                    first_captured_at=timestamps[0] if timestamps else None,
                    last_captured_at=timestamps[-1] if timestamps else None,
                    note=next((row.get("note", "").strip() for row in rows if row.get("note", "").strip()), ""),
                    manifest_name=manifest_path.name,
                    latest_frame_name=latest_frame_name,
                )
            )

        items.sort(key=lambda item: item.last_captured_at or "", reverse=True)
        start = (page - 1) * page_size
        return items[start : start + page_size]

    def gallery_latest_frame_path(self, group: str) -> Path | None:
        if not re.fullmatch(r"[0-9A-Za-z\u4e00-\u9fff_-]{1,160}", group):
            raise ValueError("invalid capture group")
        root = self.settings.capture_output_root.expanduser().resolve()
        manifest_path = root / "manifests" / f"{group}.csv"
        if not manifest_path.is_file():
            return None
        try:
            with manifest_path.open("r", newline="", encoding="utf-8") as handle:
                rows = [row for row in csv.DictReader(handle) if row.get("frame_path")]
        except (OSError, UnicodeError, csv.Error) as exc:
            raise RuntimeError(f"unable to read capture manifest: {manifest_path.name}") from exc

        candidates: list[tuple[str, Path]] = []
        for row in rows:
            frame_path = Path(row["frame_path"]).expanduser().resolve()
            if root in frame_path.parents and frame_path.is_file():
                candidates.append((row.get("captured_at", ""), frame_path))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    def gallery_frames(self, group: str) -> list[CaptureFrameItem]:
        """Return the readable frames in one manifest, in capture order."""
        if not re.fullmatch(r"[0-9A-Za-z\u4e00-\u9fff_-]{1,160}", group):
            raise ValueError("invalid capture group")
        root = self.settings.capture_output_root.expanduser().resolve()
        manifest_path = root / "manifests" / f"{group}.csv"
        if not manifest_path.is_file():
            return []
        try:
            with manifest_path.open("r", newline="", encoding="utf-8") as handle:
                rows = [row for row in csv.DictReader(handle) if row.get("frame_path")]
        except (OSError, UnicodeError, csv.Error) as exc:
            raise RuntimeError(f"unable to read capture manifest: {manifest_path.name}") from exc

        frames: list[CaptureFrameItem] = []
        for row in rows:
            frame_path = Path(row["frame_path"]).expanduser().resolve()
            if root not in frame_path.parents or not frame_path.is_file():
                continue
            frames.append(
                CaptureFrameItem(
                    name=frame_path.name,
                    captured_at=row.get("captured_at", "") or None,
                    stream_mode=self._stream_mode(row.get("source", ""), row.get("source_type", "")),
                )
            )
        return frames

    def gallery_frame_path(self, group: str, frame_name: str) -> Path | None:
        if not re.fullmatch(r"[0-9A-Za-z\u4e00-\u9fff_.-]{1,200}", frame_name):
            raise ValueError("invalid capture frame")
        root = self.settings.capture_output_root.expanduser().resolve()
        manifest_path = root / "manifests" / f"{group}.csv"
        if not manifest_path.is_file():
            return None
        try:
            with manifest_path.open("r", newline="", encoding="utf-8") as handle:
                rows = [row for row in csv.DictReader(handle) if row.get("frame_path")]
        except (OSError, UnicodeError, csv.Error) as exc:
            raise RuntimeError(f"unable to read capture manifest: {manifest_path.name}") from exc
        for row in rows:
            frame_path = Path(row["frame_path"]).expanduser().resolve()
            if frame_path.name == frame_name and root in frame_path.parents and frame_path.is_file():
                return frame_path
        return None

    async def _read_stdout(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        async for raw in stream:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            self._status = self._status.model_copy(update={"last_line": line})
            match = re.match(r"saved\s+(\d+):\s+(.+)$", line)
            if match:
                path = Path(match.group(2).strip()).resolve()
                output_dir = Path(self._status.output_dir).resolve() if self._status.output_dir else None
                if output_dir and output_dir == path.parent and path.suffix.lower() == ".jpg":
                    self._latest_frame = path
                self._status = self._status.model_copy(update={"frames_saved": int(match.group(1))})
            self.log_buffer.add("capture", "info", line)

    async def _read_stderr(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        async for raw in stream:
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                if self._is_transient_decoder_warning(line):
                    self._status = self._status.model_copy(update={"last_line": line})
                    self.log_buffer.add("capture", "warning", line)
                    continue
                self._status = self._status.model_copy(update={"last_error": line, "last_line": line})
                self.log_buffer.add("capture", "error", line)

    async def _wait_for_exit(self) -> None:
        if self.process is None:
            return
        code = await self.process.wait()
        if code != 0 and not self._status.last_error:
            self._status = self._status.model_copy(update={"last_error": f"capture process exited with code {code}"})
        self.log_buffer.add("capture", "info", f"capture process exited with code {code}")
        self.process = None
        self._status = self._status.model_copy(update={"running": False, "pid": None})

    async def start(self, request: CaptureStartRequest) -> CaptureStatus:
        async with self._capture_lock():
            if self.process and self.process.returncode is None:
                raise RuntimeError("capture is already running")
            script = self.settings.capture_script.expanduser().resolve()
            if not script.is_file():
                raise RuntimeError(f"capture script not found: {script}")

            source = self.settings.ir_rtsp_url if request.stream_mode == "ir_raw" else self.settings.visible_rtsp_url
            if not source:
                raise RuntimeError(f"{request.stream_mode} source is not configured")

            scene = self.slug_scene(request.scene)
            session_tag = datetime.now().astimezone().strftime("%Y-%m-%d_%H%M%S_%f")[:-3]
            group = f"{scene}_{session_tag}"
            output_dir = (self.settings.capture_output_root / "images" / group).resolve()
            manifest_path = (self.settings.capture_output_root / "manifests" / f"{group}.csv").resolve()
            root = self.settings.capture_output_root.expanduser().resolve()
            if root not in output_dir.parents or root not in manifest_path.parents:
                raise RuntimeError("capture paths must stay inside capture_output_root")
            output_dir.mkdir(parents=True, exist_ok=True)
            manifest_path.parent.mkdir(parents=True, exist_ok=True)

            command = [
                self.settings.capture_python_bin or self.settings.python_bin,
                str(script),
                "--source",
                source,
                "--source-type",
                self.settings.camera_source_type,
                "--output",
                str(output_dir),
                "--manifest",
                str(manifest_path),
                "--interval",
                str(request.interval_seconds),
                "--warmup-seconds",
                "2.0",
                "--scene",
                scene,
                "--note",
                request.note,
                "--jpeg-quality",
                str(request.jpeg_quality),
            ]
            if request.duration_seconds:
                command.extend(["--duration", str(request.duration_seconds)])
            if request.max_frames:
                command.extend(["--max-frames", str(request.max_frames)])

            self.process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(self.settings.capture_workdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                start_new_session=True,
            )
            self._latest_frame = None
            self._status = CaptureStatus(
                running=True,
                stream_mode=request.stream_mode,
                scene=scene,
                note=request.note,
                interval_seconds=request.interval_seconds,
                duration_seconds=request.duration_seconds,
                max_frames=request.max_frames,
                started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                pid=self.process.pid,
                output_dir=str(output_dir),
                manifest_path=str(manifest_path),
                command=command,
            )
            self._stdout_task = asyncio.create_task(self._read_stdout(self.process.stdout))
            self._stderr_task = asyncio.create_task(self._read_stderr(self.process.stderr))
            self._wait_task = asyncio.create_task(self._wait_for_exit())
            self.log_buffer.add("capture", "info", f"capture started scene={scene} mode={request.stream_mode}")
            return self.status()

    async def stop(self) -> CaptureStatus:
        async with self._capture_lock():
            if not self.process or self.process.returncode is not None:
                return self.status()
            process = self.process
            with suppress(ProcessLookupError):
                process.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(process.wait(), timeout=8)
            except asyncio.TimeoutError:
                with suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=4)
                except asyncio.TimeoutError:
                    with suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
            return self.status()
