from __future__ import annotations

import asyncio
import getpass
import json
import os
import pwd
import re
import signal
import shutil
import subprocess
import time
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import psutil
from PIL import Image, ImageStat

from .config import AgentSettings, ModelEntry
from .battery import BatteryManager
from .logs import LogBuffer
from .motion import MotionControlManager
from .pose import RobotPoseManager
from .sensors import SensorBridgeManager
from .schemas import AgentStatus, BaseStatus, DevScreenRecordingStatus, GalleryItem, MapEntry, MapMetadata, MediaStatus, ModelRuntimeStatus, ModelStartRequest


class ModelProcessManager:
    def __init__(self, settings: AgentSettings, log_buffer: LogBuffer) -> None:
        self.settings = settings
        self.log_buffer = log_buffer
        self.process: asyncio.subprocess.Process | None = None
        self.runtime: ModelRuntimeStatus = ModelRuntimeStatus(running=False)
        self._stdout_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._wait_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self.settings.snapshot_path.mkdir(parents=True, exist_ok=True)
        self.settings.pid_path.parent.mkdir(parents=True, exist_ok=True)
        self._load_existing_pid()

    def _load_existing_pid(self) -> None:
        if not self.settings.pid_path.exists():
            return
        try:
            payload = json.loads(self.settings.pid_path.read_text(encoding="utf-8"))
        except Exception:
            return
        pid = int(payload.get("pid", 0))
        if pid <= 0 or not psutil.pid_exists(pid):
            with suppress(OSError):
                self.settings.pid_path.unlink()
            return
        proc = psutil.Process(pid)
        self.runtime = ModelRuntimeStatus(
            running=proc.is_running(),
            model_id=payload.get("model_id"),
            weight=payload.get("weight"),
            source=payload.get("source"),
            pid=pid,
            started_at=payload.get("started_at"),
            command=payload.get("command", []),
            view_enabled=bool(payload.get("view_enabled", False)),
        )

    def _write_pid(self) -> None:
        if not self.runtime.running or not self.runtime.pid:
            with suppress(OSError):
                self.settings.pid_path.unlink()
            return
        payload = self.runtime.model_dump()
        self.settings.pid_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    def get_model(self, model_id: str) -> ModelEntry:
        for model in self.settings.models:
            if model.id == model_id:
                return model
        raise ValueError(f"unknown model '{model_id}'")

    def resolve_model_weight(self, weight: str) -> Path:
        candidate = Path(weight).expanduser()
        if not candidate.is_absolute():
            candidate = self.settings.model_workdir / candidate
        return candidate.resolve()

    async def _read_stream(self, stream: asyncio.StreamReader | None, level: str) -> None:
        if stream is None:
            return
        while True:
            line_bytes = await stream.readline()
            if not line_bytes:
                return
            line = line_bytes.decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            
            # Check for special markers from nano_rtsp_detect.py
            if line.startswith("EVENT_SAVED:"):
                filename = line.replace("EVENT_SAVED:", "").strip()
                self.log_buffer.add(channel="model", level="warning", message=f"检测触发：已保存事件快照 {filename}")
            elif line.startswith("DETECT_JSON:"):
                # Silently skip detailed JSON in main logs to avoid clutter, 
                # but could be used for a dedicated stream in the future.
                pass
            else:
                self.log_buffer.add(channel="model", level=level, message=line)

    async def _wait_for_exit(self) -> None:
        if self.process is None:
            return
        code = await self.process.wait()
        self.log_buffer.add("system", "info", f"model process exited with code {code}")
        self.process = None
        self.runtime = ModelRuntimeStatus(running=False)
        self._write_pid()

    async def start(self, request: ModelStartRequest) -> ModelRuntimeStatus:
        async with self._lock:
            if self.process and self.process.returncode is None:
                raise RuntimeError("model is already running")

            model = self.get_model(request.model_id)
            weight_path = self.resolve_model_weight(model.weight)
            if not weight_path.is_file():
                raise RuntimeError(f"model weight is not available locally: {weight_path}")
            source = request.source or self.settings.model_default_source or self.settings.visible_rtsp_url
            if not source:
                raise RuntimeError("camera source is not configured")

            command = [
                self.settings.python_bin,
                self.settings.model_script,
                "--weights",
                model.weight,
                "--source",
                source,
                "--vid-stride",
                str(request.vid_stride or self.settings.model_default_vid_stride),
                "--img-size",
                str(request.img_size or self.settings.model_default_img_size),
                "--conf",
                str(request.conf or self.settings.model_default_conf),
                "--iou",
                str(request.iou or self.settings.model_default_iou),
                "--save-dir",
                "events",
                "--snapshot-dir",
                str(self.settings.snapshot_path),
                "--snapshot-interval",
                str(self.settings.model_snapshot_interval),
                "--snapshot-max-size",
                str(self.settings.model_snapshot_max_size),
            ]
            device = request.device if request.device is not None else self.settings.model_default_device
            if device:
                command.extend(["--device", device])
            if request.half if request.half is not None else self.settings.model_default_half:
                command.append("--half")
            if request.view:
                command.append("--view")
            if request.annotate:
                command.append("--annotate")
            if request.event_classes:
                command.extend(["--event-classes", request.event_classes])
            if request.parking_zone is not None:
                command.extend(["--parking-zone", *(str(value) for value in request.parking_zone)])
                command.extend(["--parking-dwell", str(request.parking_dwell_seconds)])
                command.extend(["--parking-cooldown", str(request.parking_cooldown_seconds)])
                command.extend(["--parking-min-conf", str(request.parking_min_confidence)])
                command.extend(["--parking-track-iou", str(request.parking_track_iou)])
            command.extend(self.settings.extra_args)

            env = os.environ.copy()
            env.setdefault("PYTHONUNBUFFERED", "1")
            env.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
            self.process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(self.settings.model_workdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            self.runtime = ModelRuntimeStatus(
                running=True,
                model_id=model.id,
                weight=model.weight,
                source=source,
                pid=self.process.pid,
                started_at=datetime.now(timezone.utc).isoformat(),
                command=command,
                view_enabled=request.view,
            )
            self._write_pid()
            self.log_buffer.add("system", "info", f"started model '{model.id}' with pid={self.process.pid}")
            self._stdout_task = asyncio.create_task(self._read_stream(self.process.stdout, "info"))
            self._stderr_task = asyncio.create_task(self._read_stream(self.process.stderr, "error"))
            self._wait_task = asyncio.create_task(self._wait_for_exit())
            return self.runtime

    async def stop(self) -> ModelRuntimeStatus:
        async with self._lock:
            if self.process is None or self.process.returncode is not None:
                self.runtime = ModelRuntimeStatus(running=False)
                self._write_pid()
                return self.runtime
            self.log_buffer.add("system", "info", "stopping model process")
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=8)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
            self.process = None
            self.runtime = ModelRuntimeStatus(running=False)
            self._write_pid()
            return self.runtime

    async def restart(self, request: ModelStartRequest) -> ModelRuntimeStatus:
        await self.stop()
        return await self.start(request)


class CommandRunner:
    def __init__(self, settings: AgentSettings, log_buffer: LogBuffer, model_manager: ModelProcessManager) -> None:
        self.settings = settings
        self.log_buffer = log_buffer
        self.model_manager = model_manager

    async def run_command(self, parts: list[str], name: str) -> dict[str, Any]:
        proc = await asyncio.create_subprocess_exec(
            *parts,
            cwd=str(self.settings.model_workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        stdout, stderr = await proc.communicate()
        stdout_text = stdout.decode("utf-8", errors="ignore").strip() if stdout else ""
        stderr_text = stderr.decode("utf-8", errors="ignore").strip() if stderr else ""
        if stdout_text:
            self.log_buffer.add("control", "info", stdout_text)
        if stderr_text:
            self.log_buffer.add("control", "error", stderr_text)
        if proc.returncode != 0:
            detail = stderr_text or stdout_text or f"code {proc.returncode}"
            raise RuntimeError(f"command '{name}' failed: {detail}")
        self.log_buffer.add("control", "info", f"command '{name}' acked")
        return {"status": "acked", "command": parts, "stdout": stdout_text}

    async def run_whitelist_command(self, mapping: dict[str, list[str]], name: str, variables: dict[str, Any]) -> dict[str, Any]:
        if name not in mapping:
            raise ValueError(f"command '{name}' is not allowed")
        parts = [part.format(**variables) for part in mapping[name]]
        if not parts:
            raise ValueError(f"command '{name}' is empty")
        if parts[0] == "internal:stop_model":
            await self.model_manager.stop()
            self.log_buffer.add("control", "info", f"executed internal command '{name}'")
            return {"status": "acked", "command": parts}
        if parts[0] == "internal:spawn":
            if len(parts) < 2:
                raise ValueError(f"command '{name}' missing spawn target")
            proc = await asyncio.create_subprocess_exec(
                *parts[1:],
                cwd=str(self.settings.model_workdir),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=os.environ.copy(),
                start_new_session=True,
            )
            self.log_buffer.add("control", "info", f"spawned background command '{name}' pid={proc.pid}")
            return {"status": "acked", "command": parts, "pid": proc.pid}

        return await self.run_command(parts, name)


class MapManager:
    def __init__(self, settings: AgentSettings) -> None:
        self.settings = settings

    def _yaml_path(self, map_name: str) -> Path:
        if "/" in map_name or "\\" in map_name or not map_name:
            raise ValueError("invalid map name")
        return (self.settings.maps_directory / f"{map_name}.yaml").resolve()

    def _parse_simple_yaml(self, path: Path) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            payload[key.strip()] = value.strip()
        return payload

    def _read_pgm_size(self, path: Path) -> tuple[int | None, int | None]:
        try:
            with path.open("rb") as handle:
                magic = handle.readline().strip()
                if magic not in {b"P5", b"P2"}:
                    return None, None
                tokens: list[bytes] = []
                while len(tokens) < 3:
                    line = handle.readline()
                    if not line:
                        break
                    if line.startswith(b"#"):
                        continue
                    tokens.extend(line.split())
                if len(tokens) < 3:
                    return None, None
                return int(tokens[0]), int(tokens[1])
        except Exception:
            return None, None

    def list_maps(self) -> list[MapEntry]:
        self.settings.maps_directory.mkdir(parents=True, exist_ok=True)
        entries: list[MapEntry] = []
        for yaml_path in sorted(self.settings.maps_directory.glob("*.yaml")):
            meta = self._parse_simple_yaml(yaml_path)
            image_ref = meta.get("image", f"{yaml_path.stem}.pgm")
            image_path = (yaml_path.parent / image_ref).resolve()
            entries.append(
                MapEntry(
                    name=yaml_path.stem,
                    yaml_path=str(yaml_path),
                    image_path=str(image_path),
                    updated_at=datetime.fromtimestamp(yaml_path.stat().st_mtime, tz=timezone.utc).isoformat(),
                )
            )
        return entries

    def get_map(self, map_name: str) -> MapMetadata:
        yaml_path = self._yaml_path(map_name)
        if not yaml_path.exists():
            raise FileNotFoundError(map_name)
        meta = self._parse_simple_yaml(yaml_path)
        image_ref = meta.get("image", f"{yaml_path.stem}.pgm")
        image_path = (yaml_path.parent / image_ref).resolve()
        width, height = self._read_pgm_size(image_path)
        origin_raw = meta.get("origin", "[0, 0, 0]").strip().strip("[]")
        origin = [float(item.strip()) for item in origin_raw.split(",") if item.strip()]
        while len(origin) < 3:
            origin.append(0.0)
        return MapMetadata(
            name=yaml_path.stem,
            yaml_path=str(yaml_path),
            image_path=str(image_path),
            resolution=float(meta.get("resolution", 0.05)),
            origin=origin[:3],
            negate=int(float(meta.get("negate", 0))),
            occupied_thresh=float(meta.get("occupied_thresh", 0.65)),
            free_thresh=float(meta.get("free_thresh", 0.25)),
            width=width,
            height=height,
        )

    def get_map_image_path(self, map_name: str) -> Path:
        meta = self.get_map(map_name)
        path = Path(meta.image_path)
        if not path.exists():
            raise FileNotFoundError(path)
        return path


class MediaManager:
    def __init__(self, settings: AgentSettings, log_buffer: LogBuffer) -> None:
        self.settings = settings
        self.log_buffer = log_buffer
        self.process: asyncio.subprocess.Process | None = None
        self.recording_mode: Literal["visible_raw", "ir_raw"] | None = None
        self.recording_filename: str | None = None
        self.recording_started_at: str | None = None
        self.last_error: str | None = None
        self._wait_task: asyncio.Task[None] | None = None
        self.settings.recording_output_dir.mkdir(parents=True, exist_ok=True)
        self.settings.media_snapshot_path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _snapshot_looks_valid(path: Path) -> bool:
        if not path.exists() or path.stat().st_size < 8 * 1024:
            return False
        try:
            with Image.open(path) as image:
                grayscale = image.convert("L")
                extrema = grayscale.getextrema()
                if extrema is None:
                    return False
                dynamic_range = int(extrema[1]) - int(extrema[0])
                stddev = float(ImageStat.Stat(grayscale).stddev[0])
                return dynamic_range >= 40 or stddev >= 6.0
        except Exception:
            return False

    def _recording_backend(self) -> str:
        if not self.settings.recording_enabled:
            return "disabled"
        if self.settings.recording_use_ffmpeg_direct:
            return "ffmpeg_direct"
        if self.settings.recording_use_legacy_web_recorder:
            return "legacy_web_recorder"
        return "disabled"

    def _stream_url(self, stream_mode: str) -> str:
        if stream_mode == "ir_raw":
            return self.settings.ir_rtsp_url
        return self.settings.visible_rtsp_url or self.settings.model_default_source

    def _sanitize_filename(self, filename: str) -> str:
        if "/" in filename or "\\" in filename or not filename:
            raise ValueError("invalid filename")
        return filename

    def status(self) -> MediaStatus:
        recording_running = bool(self.process and self.process.returncode is None)
        return MediaStatus(
            enabled=self.settings.recording_enabled,
            gallery_enabled=self.settings.gallery_enabled,
            recording_running=recording_running,
            recording_mode=self.recording_mode,
            recording_filename=self.recording_filename,
            recording_started_at=self.recording_started_at,
            recording_backend=self._recording_backend(),
            recording_output_dir=str(self.settings.recording_output_dir),
            snapshot_output_dir=str(self.settings.media_snapshot_path),
            last_error=self.last_error,
        )

    async def _wait_for_recording_exit(self) -> None:
        if self.process is None:
            return
        code = await self.process.wait()
        if code != 0:
            self.last_error = f"recording process exited with code {code}"
            self.log_buffer.add("system", "error", self.last_error)
        self.process = None
        self.recording_mode = None
        self.recording_filename = None
        self.recording_started_at = None

    async def start_recording(self, stream_mode: Literal["visible_raw", "ir_raw"]) -> MediaStatus:
        if not self.settings.recording_enabled:
            raise RuntimeError("recording is disabled")
        if self.process and self.process.returncode is None:
            raise RuntimeError("recording is already running")
        if not self.settings.recording_use_ffmpeg_direct:
            raise RuntimeError("legacy web recorder is not integrated into this dashboard flow")
        stream_url = self._stream_url(stream_mode)
        if not stream_url:
            raise RuntimeError(f"{stream_mode} source is not configured")

        now = datetime.now(timezone.utc)
        prefix = "visible" if stream_mode == "visible_raw" else "ir"
        filename = f"{prefix}_{now.strftime('%Y%m%d_%H%M%S')}.mp4"
        output_path = self.settings.recording_output_dir / filename
        # Native Go2 MJPEG is already shared by the SDK camera worker.
        # Keep RTSP-only options on RTSP inputs and encode MJPEG as browser H264.
        rtsp_options = ["-rtsp_transport", "tcp"] if stream_url.lower().startswith("rtsp://") else []
        native_mjpeg = stream_url == "http://127.0.0.1:8766/stream.mjpg"
        input_options = ["-f", "mpjpeg", "-r", "5", "-probesize", "32768", "-analyzeduration", "0"] if native_mjpeg else rtsp_options
        codec_options = (["-vf", "scale=960:-2", "-c:v", "libx264", "-preset", "ultrafast",
                          "-crf", "23", "-pix_fmt", "yuv420p", "-r", "5", "-movflags", "+faststart"]
                         if native_mjpeg else ["-c", "copy"])
        command = ["ffmpeg", "-nostdin", "-loglevel", "error"] + input_options + [
            "-i", stream_url, "-map", "0:v:0", "-an"] + codec_options + [
            "-fflags", "+genpts", "-y", str(output_path)]
        env = os.environ.copy()
        self.process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(self.settings.recording_workdir),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
        self.recording_mode = stream_mode
        self.recording_filename = filename
        self.recording_started_at = now.isoformat()
        self.last_error = None
        self._wait_task = asyncio.create_task(self._wait_for_recording_exit())
        self.log_buffer.add("system", "info", f"recording started mode={stream_mode} file={filename} pid={self.process.pid}")
        return self.status()

    async def stop_recording(self) -> MediaStatus:
        if not self.process or self.process.returncode is not None:
            return self.status()
        filename = self.recording_filename
        process = self.process
        with suppress(ProcessLookupError):
            process.send_signal(signal.SIGINT)
        try:
            await asyncio.wait_for(process.wait(), timeout=12)
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        self.process = None
        if filename:
            meta_path = (self.settings.recording_output_dir / filename).with_suffix(".json")
            meta = {
                "filename": filename,
                "stream_mode": self.recording_mode,
                "started_at": self.recording_started_at,
                "stopped_at": datetime.now(timezone.utc).isoformat(),
                "backend": self._recording_backend(),
            }
            meta_path.write_text(json.dumps(meta, ensure_ascii=True, indent=2), encoding="utf-8")
        self.log_buffer.add("system", "info", f"recording stopped file={filename or '--'}")
        self.recording_mode = None
        self.recording_filename = None
        self.recording_started_at = None
        self.last_error = None
        return self.status()

    async def take_snapshot(self, stream_mode: Literal["visible_raw", "ir_raw", "annotated"]) -> GalleryItem:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        if stream_mode == "annotated":
            source_path = self.settings.snapshot_path / "latest.jpg"
            if not source_path.exists():
                raise RuntimeError("annotated snapshot is not ready; start a model first")
            filename = f"annotated_{timestamp}.jpg"
            output_path = self.settings.media_snapshot_path / filename
            shutil.copy2(source_path, output_path)
        else:
            stream_url = self._stream_url(stream_mode)
            if not stream_url:
                raise RuntimeError(f"{stream_mode} source is not configured")
            prefix = "visible" if stream_mode == "visible_raw" else "ir"
            filename = f"{prefix}_{timestamp}.jpg"
            output_path = self.settings.media_snapshot_path / filename
            # The shared native worker already has a complete original JPEG.
            # Request it directly instead of asking ffmpeg to probe an endless stream.
            if stream_url == "http://127.0.0.1:8766/stream.mjpg":
                import httpx
                try:
                    async with httpx.AsyncClient(trust_env=False, timeout=4.0) as client:
                        response = await client.get("http://127.0.0.1:8766/snapshot.jpg")
                        response.raise_for_status()
                        jpeg = response.content
                    if not jpeg.startswith(b"\xff\xd8"):
                        raise RuntimeError("native camera returned an invalid JPEG")
                    output_path.write_bytes(jpeg)
                except httpx.HTTPError as exc:
                    raise RuntimeError("native camera snapshot is unavailable") from exc
                return GalleryItem(storage_kind="snapshot", media_type="image", filename=filename,
                    created_at=datetime.fromtimestamp(output_path.stat().st_mtime, tz=timezone.utc).isoformat(),
                    size_bytes=output_path.stat().st_size)
            capture_error: RuntimeError | None = None
            for attempt in range(2):
                with suppress(FileNotFoundError):
                    output_path.unlink()
                proc = await asyncio.create_subprocess_exec(
                    "ffmpeg",
                    "-nostdin",
                    "-loglevel",
                    "error",
                    *(["-rtsp_transport", "tcp"] if stream_url.lower().startswith("rtsp://") else []),
                    "-skip_frame",
                    "nokey",
                    "-i",
                    stream_url,
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    "-y",
                    str(output_path),
                    cwd=str(self.settings.recording_workdir),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=os.environ.copy(),
                )
                try:
                    await asyncio.wait_for(proc.wait(), timeout=12)
                except asyncio.TimeoutError as exc:
                    with suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()
                    capture_error = RuntimeError("snapshot capture timed out")
                    if attempt == 0:
                        await asyncio.sleep(0.4)
                        continue
                    raise capture_error from exc
                if proc.returncode == 0 and self._snapshot_looks_valid(output_path):
                    break
                capture_error = RuntimeError("snapshot capture produced an invalid frame")
                with suppress(FileNotFoundError):
                    output_path.unlink()
                if attempt == 0:
                    await asyncio.sleep(0.4)
                    continue
                raise capture_error
            if not output_path.exists():
                raise capture_error or RuntimeError("snapshot capture failed")
        self.log_buffer.add("system", "info", f"snapshot captured mode={stream_mode} file={filename}")
        return GalleryItem(
            storage_kind="snapshot",
            media_type="image",
            filename=filename,
            created_at=datetime.fromtimestamp(output_path.stat().st_mtime, tz=timezone.utc).isoformat(),
            size_bytes=output_path.stat().st_size,
        )

    def list_gallery(self, page: int = 1, page_size: int | None = None) -> list[GalleryItem]:
        if not self.settings.gallery_enabled:
            return []
        size = max(1, min(page_size or self.settings.gallery_page_size, 100))
        current_page = max(1, page)
        items: list[GalleryItem] = []
        for video_file in sorted(self.settings.recording_output_dir.glob("*.mp4")):
            items.append(
                GalleryItem(
                    storage_kind="recording",
                    media_type="video",
                    filename=video_file.name,
                    created_at=datetime.fromtimestamp(video_file.stat().st_mtime, tz=timezone.utc).isoformat(),
                    size_bytes=video_file.stat().st_size,
                )
            )
        for image_file in sorted(self.settings.media_snapshot_path.glob("*.jpg")):
            items.append(
                GalleryItem(
                    storage_kind="snapshot",
                    media_type="image",
                    filename=image_file.name,
                    created_at=datetime.fromtimestamp(image_file.stat().st_mtime, tz=timezone.utc).isoformat(),
                    size_bytes=image_file.stat().st_size,
                )
            )
        # Include detection events
        self.settings.events_directory.mkdir(parents=True, exist_ok=True)
        for event_file in sorted(self.settings.events_directory.glob("*.jpg")):
            items.append(
                GalleryItem(
                    storage_kind="event",
                    media_type="image",
                    filename=event_file.name,
                    created_at=datetime.fromtimestamp(event_file.stat().st_mtime, tz=timezone.utc).isoformat(),
                    size_bytes=event_file.stat().st_size,
                )
            )
        items.sort(key=lambda item: item.created_at, reverse=True)
        start = (current_page - 1) * size
        return items[start : start + size]

    def get_media_file(self, storage_kind: Literal["recording", "snapshot", "event"] | str, filename: str) -> Path:
        safe_name = self._sanitize_filename(filename)
        if storage_kind == "recording":
            base_dir = self.settings.recording_output_dir
        elif storage_kind == "snapshot":
            base_dir = self.settings.media_snapshot_path
        elif storage_kind == "event":
            base_dir = self.settings.events_directory
        else:
            raise ValueError("invalid media storage kind")
        path = (base_dir / safe_name).resolve()
        if not path.exists() or base_dir.resolve() not in path.parents:
            raise FileNotFoundError(safe_name)
        return path


class DevScreenRecordingManager:
    def __init__(self, settings: AgentSettings, log_buffer: LogBuffer) -> None:
        self.settings = settings
        self.log_buffer = log_buffer
        self.process: asyncio.subprocess.Process | None = None
        self.backend_active: str | None = None
        self.filename: str | None = None
        self.started_at: str | None = None
        self.last_error: str | None = None
        self.display_active: str | None = None
        self.xauthority_active: str | None = None
        self._stdout_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._wait_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self.settings.dev_screen_recording_path.mkdir(parents=True, exist_ok=True)

    def status(self) -> DevScreenRecordingStatus:
        return DevScreenRecordingStatus(
            enabled=self.settings.dev_screen_recording_enabled,
            running=bool(self.process and self.process.returncode is None),
            backend_configured=self.settings.dev_screen_recording_backend,
            backend_active=self.backend_active,
            output_dir=str(self.settings.dev_screen_recording_path),
            filename=self.filename,
            started_at=self.started_at,
            display=self.display_active or self.settings.dev_screen_recording_display,
            fps=self.settings.dev_screen_recording_fps,
            width=self.settings.dev_screen_recording_width,
            height=self.settings.dev_screen_recording_height,
            last_error=self.last_error,
        )

    def _gst_plugin_available(self, plugin: str) -> bool:
        return shutil.which("gst-inspect-1.0") is not None and os.system(
            f"gst-inspect-1.0 {plugin} >/dev/null 2>&1"
        ) == 0

    def _x11_display_names(self) -> list[str]:
        names: list[str] = []
        socket_dir = Path("/tmp/.X11-unix")
        if socket_dir.exists():
            for entry in sorted(socket_dir.iterdir()):
                match = re.fullmatch(r"X(\d+)", entry.name)
                if match:
                    names.append(f":{match.group(1)}")
        return names

    def _candidate_displays(self) -> list[str]:
        candidates: list[str] = []
        for value in (
            self.settings.dev_screen_recording_display,
            os.environ.get("DISPLAY", ""),
        ):
            value = (value or "").strip()
            if value and value not in candidates:
                candidates.append(value)
        try:
            output = subprocess.run(
                ["who"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout
            for match in re.findall(r"\((:[0-9]+)\)", output):
                if match not in candidates:
                    candidates.append(match)
        except Exception:
            pass
        for display in self._x11_display_names():
            if display not in candidates:
                candidates.append(display)
        return candidates

    def _candidate_xauthorities(self) -> list[str]:
        candidates: list[str] = []
        for value in (
            self.settings.dev_screen_recording_xauthority,
            os.environ.get("XAUTHORITY", ""),
        ):
            value = (value or "").strip()
            if value and Path(value).exists() and value not in candidates:
                candidates.append(value)
        try:
            home_dir = Path(pwd.getpwuid(os.getuid()).pw_dir)
        except Exception:
            home_dir = Path.home()
        for path in (
            home_dir / ".Xauthority",
            Path(f"/home/{getpass.getuser()}/.Xauthority"),
        ):
            resolved = str(path)
            if path.exists() and resolved not in candidates:
                candidates.append(resolved)
        return candidates

    def _can_access_display(self, display: str, xauthority: str | None) -> tuple[bool, str]:
        env = os.environ.copy()
        env["DISPLAY"] = display
        if xauthority:
            env["XAUTHORITY"] = xauthority
        if shutil.which("xset") is not None:
            probe = subprocess.run(
                ["xset", "q"],
                check=False,
                capture_output=True,
                text=True,
                env=env,
                timeout=3,
            )
            detail = (probe.stderr or probe.stdout or "").strip()
            return probe.returncode == 0, detail
        probe = subprocess.run(
            [
                "gst-launch-1.0",
                "-q",
                "ximagesrc",
                f"display-name={display}",
                "use-damage=false",
                "num-buffers=1",
                "!",
                "videoconvert",
                "!",
                "video/x-raw,format=I420",
                "!",
                "fakesink",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=5,
        )
        detail = (probe.stderr or probe.stdout or "").strip()
        return probe.returncode == 0, detail

    def _resolve_x11_session(self) -> tuple[str, str | None]:
        attempts: list[str] = []
        displays = self._candidate_displays()
        xauths = self._candidate_xauthorities() or [""]
        for display in displays:
            if not display.startswith(":"):
                continue
            for xauthority in xauths:
                ok, detail = self._can_access_display(display, xauthority or None)
                if ok:
                    return display, (xauthority or None)
                label = f"{display} via {xauthority}" if xauthority else display
                attempts.append(f"{label}: {detail or 'access denied'}")
        tried = ", ".join(displays) if displays else "(none)"
        detail = "; ".join(attempts[:3])
        raise RuntimeError(
            "no accessible X11 desktop session found for screen recording"
            f" (tried displays: {tried})"
            + (f": {detail}" if detail else "")
        )

    def _candidate_backends(self) -> list[str]:
        configured = (self.settings.dev_screen_recording_backend or "auto").strip().lower()
        if configured != "auto":
            return [configured]
        return [
            "gstreamer_nvv4l2h264enc",
            "gstreamer_omx_h264",
            "gstreamer_x264",
        ]

    def _backend_supported(self, backend: str) -> bool:
        if backend == "gstreamer_nvv4l2h264enc":
            return self._gst_plugin_available("ximagesrc") and self._gst_plugin_available("nvv4l2h264enc") and self._gst_plugin_available("nvvidconv")
        if backend == "gstreamer_omx_h264":
            return self._gst_plugin_available("ximagesrc") and self._gst_plugin_available("avenc_h264_omx")
        if backend == "gstreamer_x264":
            return self._gst_plugin_available("ximagesrc") and self._gst_plugin_available("x264enc")
        return False

    def _humanize_backend_error(self, backend: str, detail: str, display: str, xauthority: str | None) -> str:
        normalized = detail.strip()
        lowered = normalized.lower()
        if "could not open x display for reading" in lowered or "xcontext" in lowered:
            auth_hint = f", XAUTHORITY={xauthority}" if xauthority else ""
            return f"{backend}: X11 display is not accessible (DISPLAY={display}{auth_hint})"
        if "failed to start" in lowered and "ximagesrc" in lowered:
            return f"{backend}: X11 desktop capture failed to start on DISPLAY={display}"
        if "backend plugin unavailable" in lowered:
            return f"{backend}: backend plugin unavailable"
        if normalized:
            first_line = normalized.splitlines()[0].strip()
            return f"{backend}: {first_line}"
        return f"{backend}: failed to start"

    def _build_command(self, backend: str, output_path: Path, display: str) -> list[str]:
        fps = max(1, int(self.settings.dev_screen_recording_fps))
        width = max(320, int(self.settings.dev_screen_recording_width))
        height = max(240, int(self.settings.dev_screen_recording_height))
        show_pointer = "true" if self.settings.dev_screen_recording_show_pointer else "false"
        use_damage = "true" if self.settings.dev_screen_recording_use_damage else "false"
        bitrate = max(256, int(self.settings.dev_screen_recording_bitrate_kbps))
        base = [
            "gst-launch-1.0",
            "-e",
            "ximagesrc",
            f"display-name={display}",
            f"use-damage={use_damage}",
            f"show-pointer={show_pointer}",
            "!",
            "videoconvert",
            "!",
            "videoscale",
        ]
        if backend == "gstreamer_nvv4l2h264enc":
            return base + [
                "!",
                "video/x-raw,framerate=%d/1,format=I420" % fps,
                "!",
                "nvvidconv",
                "!",
                f"video/x-raw(memory:NVMM),width={width},height={height},format=NV12",
                "!",
                "nvv4l2h264enc",
                f"bitrate={bitrate * 1000}",
                "control-rate=1",
                f"iframeinterval={max(10, fps * 2)}",
                f"idrinterval={max(10, fps * 2)}",
                "insert-sps-pps=true",
                "preset-level=1",
                "maxperf-enable=false",
                "!",
                "h264parse",
                "!",
                "matroskamux",
                "!",
                "filesink",
                f"location={output_path}",
            ]
        if backend == "gstreamer_omx_h264":
            return base + [
                "!",
                f"video/x-raw,framerate={fps}/1,width={width},height={height},format=I420",
                "!",
                "avenc_h264_omx",
                f"bitrate={bitrate * 1000}",
                "!",
                "h264parse",
                "!",
                "matroskamux",
                "!",
                "filesink",
                f"location={output_path}",
            ]
        if backend == "gstreamer_x264":
            return base + [
                "!",
                f"video/x-raw,framerate={fps}/1,width={width},height={height},format=I420",
                "!",
                "x264enc",
                "tune=zerolatency",
                "speed-preset=ultrafast",
                f"bitrate={bitrate}",
                f"key-int-max={max(10, fps * 2)}",
                "threads=2",
                "bframes=0",
                "cabac=false",
                "!",
                "h264parse",
                "!",
                "matroskamux",
                "!",
                "filesink",
                f"location={output_path}",
            ]
        raise RuntimeError(f"unsupported recording backend '{backend}'")

    async def _read_stream(self, stream: asyncio.StreamReader | None, level: str) -> None:
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                return
            message = line.decode("utf-8", errors="ignore").strip()
            if message:
                self.log_buffer.add("system", level, f"[dev-recording] {message}")

    async def _wait_for_exit(self) -> None:
        if self.process is None:
            return
        code = await self.process.wait()
        if code != 0:
            self.last_error = f"screen recording exited with code {code}"
            self.log_buffer.add("system", "error", self.last_error)
        self.process = None
        self.backend_active = None
        self.display_active = None
        self.xauthority_active = None
        self.filename = None
        self.started_at = None

    async def start(self) -> DevScreenRecordingStatus:
        async with self._lock:
            if not self.settings.dev_screen_recording_enabled:
                raise RuntimeError("developer screen recording is disabled")
            if self.process and self.process.returncode is None:
                raise RuntimeError("screen recording is already running")
            display, xauthority = self._resolve_x11_session()

            timestamp = datetime.now(timezone.utc)
            filename = f"desktop_{timestamp.strftime('%Y%m%d_%H%M%S')}.mkv"
            output_path = self.settings.dev_screen_recording_path / filename
            startup_errors: list[str] = []

            for backend in self._candidate_backends():
                if not self._backend_supported(backend):
                    startup_errors.append(f"{backend}: backend plugin unavailable")
                    continue
                command = self._build_command(backend, output_path, display)
                env = os.environ.copy()
                env["DISPLAY"] = display
                if xauthority:
                    env["XAUTHORITY"] = xauthority
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=str(self.settings.model_workdir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                    start_new_session=True,
                )
                try:
                    await asyncio.wait_for(process.wait(), timeout=1.5)
                except asyncio.TimeoutError:
                    self.process = process
                    self.backend_active = backend
                    self.display_active = display
                    self.xauthority_active = xauthority
                    self.filename = filename
                    self.started_at = timestamp.isoformat()
                    self.last_error = None
                    self._stdout_task = asyncio.create_task(self._read_stream(process.stdout, "info"))
                    self._stderr_task = asyncio.create_task(self._read_stream(process.stderr, "error"))
                    self._wait_task = asyncio.create_task(self._wait_for_exit())
                    self.log_buffer.add("system", "info", f"desktop recording started backend={backend} file={filename} pid={process.pid}")
                    return self.status()
                stdout, stderr = await process.communicate()
                detail = (
                    stderr.decode("utf-8", errors="ignore").strip()
                    or stdout.decode("utf-8", errors="ignore").strip()
                    or f"exit code {process.returncode}"
                )
                startup_errors.append(self._humanize_backend_error(backend, detail, display, xauthority))
                with suppress(FileNotFoundError):
                    output_path.unlink()

            self.last_error = "; ".join(startup_errors) or "no recording backend is available"
            raise RuntimeError(self.last_error)

    async def stop(self) -> DevScreenRecordingStatus:
        async with self._lock:
            if not self.process or self.process.returncode is not None:
                return self.status()
            filename = self.filename
            backend = self.backend_active
            started_at = self.started_at
            display_active = self.display_active
            xauthority_active = self.xauthority_active
            process = self.process
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGINT)
            try:
                await asyncio.wait_for(process.wait(), timeout=15)
            except asyncio.TimeoutError:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
            self.process = None
            if filename:
                meta_path = (self.settings.dev_screen_recording_path / filename).with_suffix(".json")
                meta = {
                    "filename": filename,
                    "backend": backend,
                    "started_at": started_at,
                    "stopped_at": datetime.now(timezone.utc).isoformat(),
                    "display": display_active,
                    "xauthority": xauthority_active,
                    "fps": self.settings.dev_screen_recording_fps,
                    "width": self.settings.dev_screen_recording_width,
                    "height": self.settings.dev_screen_recording_height,
                }
                meta_path.write_text(json.dumps(meta, ensure_ascii=True, indent=2), encoding="utf-8")
            self.log_buffer.add("system", "info", f"desktop recording stopped file={filename or '--'} backend={backend or '--'}")
            self.backend_active = None
            self.filename = None
            self.started_at = None
            self.last_error = None
            return self.status()


class BaseBringupManager:
    def __init__(
        self,
        settings: AgentSettings,
        log_buffer: LogBuffer,
        motion_manager: MotionControlManager,
        battery_manager: BatteryManager | None = None,
    ) -> None:
        self.settings = settings
        self.log_buffer = log_buffer
        self.motion_manager = motion_manager
        self.battery_manager = battery_manager
        self.process: asyncio.subprocess.Process | None = None
        self.state = BaseStatus()
        self._stdout_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._wait_task: asyncio.Task[None] | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._refresh_lock = asyncio.Lock()
        self._last_refresh_ts = 0.0

    async def start(self) -> None:
        if self.settings.go2_data_only:
            self.log_buffer.add("system", "info", "base bringup monitor disabled by Go2 data-only profile")
            return
        if self._monitor_task is None:
            self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        if self.settings.go2_data_only:
            return
        if self._monitor_task:
            self._monitor_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._monitor_task
            self._monitor_task = None
        await self.stop_bringup(restore_battery=False)

    async def _read_stream(self, stream: asyncio.StreamReader | None, level: str) -> None:
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                return
            self.log_buffer.add("system", level, f"[base] {line.decode('utf-8', errors='ignore').rstrip()}")

    async def _wait_for_exit(self) -> None:
        if self.process is None:
            return
        code = await self.process.wait()
        self.log_buffer.add("system", "warning", f"base bringup exited with code {code}")
        self.process = None
        self.state.running = False
        self.state.pid = None
        self.state.started_by_agent = False

    async def start_bringup(self) -> BaseStatus:
        async with self._lock:
            if self.process and self.process.returncode is None:
                return await self.refresh()
            existing = await self.refresh(force=True)
            if existing.wheeltec_robot_online:
                return existing
            if self.battery_manager:
                await self.battery_manager.stop_serial_reader()
            env = os.environ.copy()
            env.setdefault("PYTHONNOUSERSITE", "1")
            command = (
                "source /opt/ros/humble/setup.bash && "
                "source /home/nvidia/Desktop/wheeltec_ros2/install/local_setup.bash && "
                f"bash {self.settings.base_start_script}"
            )
            try:
                self.process = await asyncio.create_subprocess_exec(
                    "/bin/bash",
                    "-lc",
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                    start_new_session=True,
                )
            except OSError:
                if self.battery_manager:
                    await self.battery_manager.start_serial_reader()
                raise
            self.state.started_by_agent = True
            self.state.running = True
            self.state.pid = self.process.pid
            self.state.last_error = None
            self._stdout_task = asyncio.create_task(self._read_stream(self.process.stdout, "info"))
            self._stderr_task = asyncio.create_task(self._read_stream(self.process.stderr, "error"))
            self._wait_task = asyncio.create_task(self._wait_for_exit())
            self.log_buffer.add("system", "info", f"started base bringup pid={self.process.pid}")
        deadline = time.time() + self.settings.base_bringup_start_timeout_seconds
        while time.time() < deadline:
            state = await self.refresh()
            if state.wheeltec_robot_online and state.cmd_vel_subscriber_online and state.odom_online:
                return state
            await asyncio.sleep(0.5)
        self.state.last_error = "base bringup started but chassis topics are not ready yet"
        if not self.process or self.process.returncode is not None:
            if self.battery_manager:
                await self.battery_manager.start_serial_reader()
        return self.state

    async def stop_bringup(self, restore_battery: bool = True) -> BaseStatus:
        async with self._lock:
            proc = self.process
            if proc and proc.returncode is None:
                with suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGINT)
                try:
                    await asyncio.wait_for(proc.wait(), timeout=8)
                except asyncio.TimeoutError:
                    with suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        with suppress(ProcessLookupError):
                            os.killpg(proc.pid, signal.SIGKILL)
                        await proc.wait()
            self.process = None
            self.state.running = False
            self.state.started_by_agent = False
            self.state.pid = None
        state = await self.refresh(force=True)
        if restore_battery and self.battery_manager:
            await self.battery_manager.start_serial_reader()
        return state

    async def _run_ros_cli(self, script: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "/bin/bash",
            "-lc",
            f"source /opt/ros/humble/setup.bash && ROS2CLI_NO_DAEMON=1 timeout {self.settings.base_status_timeout_seconds}s {script}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.settings.base_status_timeout_seconds)
        except asyncio.TimeoutError as exc:
            with suppress(ProcessLookupError):
                proc.kill()
            await proc.communicate()
            raise RuntimeError(f"timeout running: {script}") from exc
        if proc.returncode != 0:
            detail = stderr.decode("utf-8", errors="ignore").strip() or stdout.decode("utf-8", errors="ignore").strip()
            raise RuntimeError(detail or f"command failed: {script}")
        return stdout.decode("utf-8", errors="ignore")

    @staticmethod
    def _extract_count(text: str, label: str) -> int:
        match = re.search(rf"{label}:\s*(\d+)", text)
        return int(match.group(1)) if match else 0

    async def refresh(self, force: bool = False) -> BaseStatus:
        if self.settings.go2_data_only:
            self.state = BaseStatus(last_error="base bringup disabled by Go2 data-only profile")
            return self.state
        if not force and self._last_refresh_ts and (time.time() - self._last_refresh_ts) <= self.settings.base_refresh_cache_seconds:
            return self.state
        async with self._refresh_lock:
            if not force and self._last_refresh_ts and (time.time() - self._last_refresh_ts) <= self.settings.base_refresh_cache_seconds:
                return self.state
            running = bool(self.process and self.process.returncode is None)
            wheeltec_robot_online = False
            cmd_vel_subscriber_online = False
            odom_online = False
            last_error: str | None = None
            try:
                nodes = await self._run_ros_cli("ros2 node list")
                wheeltec_robot_online = self.settings.base_node_name in nodes.splitlines()
                cmd_vel_info = await self._run_ros_cli(f"ros2 topic info {self.settings.base_cmd_vel_topic} -v")
                cmd_vel_subscriber_online = self._extract_count(cmd_vel_info, "Subscription count") > 0
                odom_info = await self._run_ros_cli(f"ros2 topic info {self.settings.base_odom_topic} -v")
                odom_online = self._extract_count(odom_info, "Publisher count") > 0
            except Exception as exc:
                last_error = str(exc)

            self.state = BaseStatus(
                running=running or wheeltec_robot_online,
                started_by_agent=self.state.started_by_agent,
                pid=self.process.pid if self.process and self.process.returncode is None else None,
                wheeltec_robot_online=wheeltec_robot_online,
                cmd_vel_subscriber_online=cmd_vel_subscriber_online,
                odom_online=odom_online,
                last_error=last_error,
            )
            self._last_refresh_ts = time.time()
            return self.state

    async def _monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.base_monitor_interval_seconds)
            state = await self.refresh()
            if self.motion_manager.snapshot(navigation_running=False).mode == "real" and not (
                state.wheeltec_robot_online and state.cmd_vel_subscriber_online and state.odom_online
            ):
                await self.motion_manager.force_safe_lock("base went offline; reverted to dry-run and relocked control")


class StatusCollector:
    def __init__(
        self,
        settings: AgentSettings,
        model_manager: ModelProcessManager,
        base_manager: BaseBringupManager,
        sensor_manager: SensorBridgeManager,
        pose_manager: RobotPoseManager,
        motion_manager: MotionControlManager,
        battery_manager: BatteryManager,
    ) -> None:
        self.settings = settings
        self.model_manager = model_manager
        self.base_manager = base_manager
        self.sensor_manager = sensor_manager
        self.pose_manager = pose_manager
        self.motion_manager = motion_manager
        self.battery_manager = battery_manager
        self._last_payload: AgentStatus | None = None
        self._last_ts = 0.0

    def _read_gpu_percent(self) -> float | None:
        candidates = [
            Path("/sys/devices/gpu.0/load"),
            Path("/sys/class/devfreq/17000000.gv11b/load"),
        ]
        for path in candidates:
            if path.exists():
                try:
                    raw = path.read_text(encoding="utf-8").strip()
                    value = float(raw)
                    return round(value / 10.0 if value > 100 else value, 1)
                except Exception:
                    return None
        return None

    def _find_processes(self, *patterns: str) -> list[psutil.Process]:
        found: list[psutil.Process] = []
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            with suppress(psutil.Error):
                cmdline = " ".join(proc.info.get("cmdline") or [])
                name = proc.info.get("name") or ""
                text = f"{name} {cmdline}"
                if any(pattern in text for pattern in patterns):
                    found.append(proc)
        return found

    def _workflow_status(self) -> tuple[bool, bool, bool, bool, str | None]:
        mapping_running = bool(
            self._find_processes(
                "/home/nvidia/start_2d_mapping.sh",
                "scripts/start_mapping_integrated.sh",
                "slam_toolbox",
                "slam_gmapping",
            )
        )
        navigation_procs = self._find_processes(
            "/home/nvidia/start_2d_navigation.sh",
            "scripts/start_navigation_integrated.sh",
            "bt_navigator",
            "controller_server",
            "planner_server",
            "behavior_server",
            "smoother_server",
            "nav2_lifecycle_manager",
        )
        navigation_running = bool(navigation_procs)
        rviz_running = bool(self._find_processes("rviz2 -d /opt/ros/humble/share/nav2_bringup/rviz/nav2_default_view.rviz", "rviz2"))
        rviz_available = shutil.which("rviz2") is not None
        current_map_name: str | None = None
        for proc in navigation_procs:
            with suppress(psutil.Error):
                cmdline = proc.cmdline()
                for idx, part in enumerate(cmdline):
                    if part.endswith("/home/nvidia/start_2d_navigation.sh") and idx + 1 < len(cmdline):
                        current_map_name = cmdline[idx + 1]
                        break
                    if part.startswith("map:=") and part.endswith(".yaml"):
                        prefix = "map:="
                        map_value = part[len(prefix):] if part.startswith(prefix) else part
                        current_map_name = Path(map_value).stem
                        break
                if current_map_name:
                    break
        if not current_map_name:
            default_yaml = self.settings.maps_directory / f"{self.settings.default_map_name}.yaml"
            if default_yaml.exists():
                current_map_name = self.settings.default_map_name
        return mapping_running, navigation_running, rviz_available, rviz_running, current_map_name

    def collect(self) -> AgentStatus:
        now = time.time()
        if self._last_payload and now - self._last_ts <= self.settings.status_cache_seconds:
            return self._last_payload

        mapping_running, navigation_running, rviz_available, rviz_running, current_map_name = self._workflow_status()
        payload = AgentStatus(
            agent_online=True,
            board_name=self.settings.board_name,
            board_online=True,
            current_video_source=self.model_manager.runtime.source or self.settings.visible_rtsp_url,
            current_video_mode="annotated" if self.model_manager.runtime.running else "visible_raw",
            camera_source_type=self.settings.camera_source_type,
            camera_source_label=self.settings.camera_source_label,
            camera_live_confirmed=self.settings.camera_live_confirmed,
            ir_camera_enabled=self.settings.ir_camera_enabled,
            current_model=self.model_manager.runtime.model_id,
            current_map_name=current_map_name,
            mapping_running=mapping_running,
            navigation_running=navigation_running,
            rviz_available=rviz_available,
            rviz_running=rviz_running,
            base_status=self.base_manager.state,
            battery_status=self.battery_manager.status(),
            model_status=self.model_manager.runtime,
            motion_state=self.motion_manager.snapshot(navigation_running=navigation_running),
            cpu_percent=psutil.cpu_percent(interval=None),
            memory_percent=psutil.virtual_memory().percent,
            gpu_percent=self._read_gpu_percent(),
            lidar_online=self.sensor_manager.state.lidar_online,
            imu_online=self.sensor_manager.state.imu_online,
            pointcloud_hz=self.sensor_manager.state.pointcloud_hz,
            imu_hz=self.sensor_manager.state.imu_hz,
            point_count=self.sensor_manager.state.point_count,
            sensor_error=self.sensor_manager.state.error,
            lidar_to_base_translation=self.settings.lidar_to_base_translation,
            lidar_to_base_rotation_rpy=self.settings.lidar_to_base_rotation_rpy,
            imu_to_base_rotation_rpy=self.settings.imu_to_base_rotation_rpy,
            car_model_forward_axis=self.settings.car_model_forward_axis,
            car_model_up_axis=self.settings.car_model_up_axis,
            pointcloud_history_seconds=self.settings.pointcloud_history_seconds,
            last_heartbeat=datetime.now(timezone.utc).isoformat(),
            available_models=[model.model_dump() for model in self.settings.models],
            allow_mjpeg_fallback=self.settings.allow_mjpeg_fallback,
        )
        self._last_payload = payload
        self._last_ts = now
        return payload
