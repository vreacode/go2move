#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Go2 视觉：实时相机 -> YOLO 行人检测 -> ByteTrack -> 框、ID、坐标。

Jetson 默认使用已有的 YOLOX-Tiny TensorRT 模型，不需要 torch。
普通电脑可指定 --backend ultralytics --model /path/to/yolov8n.pt。

    python3 'person_track(1).py' --source go2
    python3 'person_track(1).py' --source file:/path/to/people.mp4

go2/native 读取已运行的 go2-native-camera.service 的原始 JPEG。
默认关联参数针对单人走动、转身及局部遮挡；多人的交错遮挡只做代码回归。
无图形窗口，保留标注 JPG、AVI 与 JSON 输出，不使用 ReID。
ID 是本次运行的轨迹号，不代表已确认某个特征人物的身份；
长时间离场、严重遮挡或相机大幅移动可能换号。坐标是原图像素 xyxy。
"""
import argparse
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import signal
import sys
import time
from types import SimpleNamespace

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
GO2_DIR = ROOT / "deployment" / "go2"
CAMERA_DIR = GO2_DIR / "runtime" / "native_camera"
MODEL_DIR = Path("/home/unitree/go2_ros2_ws/models/person_search")


class NativeCamera:
    """读取服务提供的原始 JPEG；拒绝重复时间戳、过期帧和写入竞态。"""

    def __init__(self, directory, fps=5.0, max_age=2.0, read_timeout=5.0):
        self.directory = Path(directory)
        self.fps = fps
        self.max_age = max_age
        self.read_timeout = read_timeout
        self.live = True
        self.captured_at = self.timestamp = self.last_capture = None

    def latest(self):
        try:
            status = json.loads((self.directory / "status.json").read_text())
            captured_at = float(status.get("captured_at", 0))
            if (not status.get("running") or not math.isfinite(captured_at)
                    or not -0.25 <= time.time() - captured_at <= self.max_age
                    or (self.last_capture is not None and captured_at <= self.last_capture)):
                return None
            jpeg = (self.directory / "latest.jpg").read_bytes()
            if hashlib.sha256(jpeg).hexdigest() != status.get("sha256"):
                return None
            frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
            return (frame, captured_at) if frame is not None else None
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def read(self):
        deadline = time.monotonic() + self.read_timeout
        while time.monotonic() < deadline:
            item = self.latest()
            if item is not None:
                frame, self.captured_at = item
                self.last_capture = self.timestamp = self.captured_at
                return True, frame
            time.sleep(0.02)
        raise TimeoutError("相机在 %.1f 秒内没有提供新图像：%s。"
                           "检查连接，并启动 systemctl --user start go2-native-camera.service。"
                           % (self.read_timeout, self.directory))

    def release(self):
        pass


class VideoCamera:
    """OpenCV 视频/USB 输入；视频只播放一次，保留原视频帧率与尺寸。"""

    def __init__(self, source, fallback_fps=5.0):
        if source.startswith("usb:") or source.isdigit():
            value = int(source.split(":")[-1])
            self.live = True
        elif source.startswith(("rtsp://", "http://", "https://", "/dev/video")):
            value = source
            self.live = True
        else:
            value = str(Path(source[5:] if source.startswith("file:") else source).expanduser())
            if not Path(value).is_file():
                raise FileNotFoundError("输入视频不存在：" + value)
            self.live = False
        self.capture = cv2.VideoCapture(value)
        if not self.capture.isOpened():
            self.capture.release()
            raise RuntimeError("无法打开相机或视频：%s" % value)
        fps = self.capture.get(cv2.CAP_PROP_FPS)
        self.fps = fps if math.isfinite(fps) and 0 < fps <= 240 else fallback_fps
        self.captured_at = self.timestamp = None
        self.frames = 0

    def read(self):
        ok, frame = self.capture.read()
        if not ok and self.live:
            raise RuntimeError("实时相机读取失败：连接中断或没有图像。")
        if ok:
            self.captured_at = time.time() if self.live else None
            self.timestamp = self.captured_at if self.live else self.frames / self.fps
            self.frames += 1
        return ok, frame

    def release(self):
        self.capture.release()


def make_camera(args):
    if args.source in ("go2", "native"):
        print("[相机] 读取 Go2 实时图像：%s" % args.camera_dir)
        return NativeCamera(args.camera_dir, args.fps, args.max_age, args.read_timeout)
    return VideoCamera(args.source, args.fps)


def resolve_backend(backend, model):
    if backend != "auto":
        return backend
    if model:
        return "tensorrt" if Path(model).suffix == ".engine" else "ultralytics"
    if GO2_DIR.is_dir() and list(MODEL_DIR.glob("yolox_tiny*.engine")):
        return "tensorrt"
    return "ultralytics"


class PersonDetector:
    """两个 YOLO 后端统一返回 bbox/confidence，只检测 COCO person 类。"""

    def __init__(self, args):
        self.backend = resolve_backend(args.backend, args.model)
        self.args = args
        if self.backend == "tensorrt":
            sys.path.insert(0, str(GO2_DIR))
            from person_detector import Detector
            import tensorrt as trt
            path = Path(args.model).expanduser() if args.model else (
                MODEL_DIR / ("yolox_tiny.trt%s.fp16.engine" % trt.__version__))
            if path.suffix != ".engine" or not path.is_file():
                raise RuntimeError("TensorRT 后端需要已有 YOLOX-Tiny .engine：%s" % path)
            self.model_path = str(path)
            self.model = Detector(self.model_path, confidence=args.conf, nms_threshold=args.iou)
        else:
            try:
                from ultralytics import YOLO
            except ImportError as exc:
                raise RuntimeError("该 Python 环境未安装 ultralytics；Jetson 使用 --backend tensorrt，"
                                   "普通电脑在独立环境安装 ultralytics/torch。") from exc
            path = Path(args.model or "yolov8n.pt").expanduser()
            if not path.is_file():
                raise FileNotFoundError("YOLO 权重不存在：%s；用 --model 指定本地 COCO 检测权重。" % path)
            self.model_path = str(path)
            self.model = YOLO(self.model_path)

    def detect(self, frame):
        if self.backend == "tensorrt":
            return self.model.detect(frame)
        result = self.model.predict(frame, classes=[0], conf=self.args.conf,
                                    iou=self.args.iou, imgsz=self.args.imgsz,
                                    device=self.args.device, verbose=False)[0]
        if result.boxes is None:
            return []
        boxes = result.boxes.cpu().numpy()
        return [{"bbox": box.tolist(), "confidence": float(score)}
                for box, score in zip(boxes.xyxy, boxes.conf)]

    def close(self):
        if self.backend == "tensorrt":
            self.model.close()


class PersonTracker:
    """保留一个 ByteTrack 实例；无检测帧也 update，让轨迹正确变为丢失。"""

    def __init__(self, backend, fps, high_thresh=0.2, match_thresh=0.9, lost_seconds=6.0,
                 new_track_thresh=0.35, fuse_score=False):
        if (not all(math.isfinite(value) for value in
                    (fps, high_thresh, match_thresh, lost_seconds, new_track_thresh))
                or fps <= 0 or lost_seconds <= 0 or not 0.1 < high_thresh <= new_track_thresh <= 1
                or not 0 < match_thresh <= 1):
            raise ValueError("ByteTrack 帧率、缓存和阈值不合法。")
        self.backend = backend
        self.max_gap_frames = max(1, math.ceil(fps * lost_seconds)) + 2
        if backend == "tensorrt":
            sys.path.insert(0, str(GO2_DIR))
            from person_tracker import Tracker
            self.tracker = Tracker(frame_rate=fps, track_thresh=high_thresh,
                                   match_thresh=match_thresh, lost_seconds=lost_seconds,
                                   new_track_thresh=new_track_thresh, fuse_score=fuse_score)
        else:
            from ultralytics.engine.results import Boxes
            from ultralytics.trackers.byte_tracker import BYTETracker
            self.boxes_class = Boxes
            options = SimpleNamespace(track_high_thresh=high_thresh, track_low_thresh=0.1,
                                      new_track_thresh=new_track_thresh,
                                      match_thresh=match_thresh, fuse_score=fuse_score)
            # 旧版按 frame_rate 缩放 30fps buffer；新版直接使用 buffer 帧数。
            if "frame_rate" in inspect.signature(BYTETracker.__init__).parameters:
                options.track_buffer = max(1, round(lost_seconds * 30))
                self.tracker = BYTETracker(options, frame_rate=fps)
            else:
                options.track_buffer = max(1, round(lost_seconds * fps))
                self.tracker = BYTETracker(options)

    def update(self, detections, frame):
        if self.backend == "tensorrt":
            return self.tracker.update(detections, frame.shape)
        data = np.asarray([list(item["bbox"]) + [item["confidence"], 0]
                           for item in detections], dtype=np.float32).reshape(-1, 6)
        boxes = self.boxes_class(data, frame.shape[:2])
        tracks = self.tracker.update(boxes, frame)
        return [{"bbox": row[:4].tolist(), "track_id": int(row[4]),
                 "confidence": float(row[5])} for row in tracks]

    def age_gap(self, previous, current, fps, frame):
        """实时输入跳过采集帧时，按时间差老化轨迹，避免旧 ID 无限保留。"""
        if previous is not None and current is not None:
            missing = max(0, round((current - previous) * fps) - 1)
            for _ in range(min(missing, self.max_gap_frames)):
                self.update([], frame)

    def alive_ids(self):
        """已确认且仍在跟踪/遮挡缓存内的 ID，不输出缓存框或过期 ID。"""
        core = self.tracker.tracker if self.backend == "tensorrt" else self.tracker
        limit = (core.max_time_lost if hasattr(core, "max_time_lost")
                 else core.max_frames_lost)
        return {int(track.track_id)
                for track in core.tracked_stracks + core.lost_stracks
                if track.is_activated and track.state in (1, 2)
                and core.frame_id - track.end_frame <= limit}


class TrackingMetrics:
    """单人实验统计，帧数按实际处理帧计算，不把补偿跳帧当作观测。"""

    def __init__(self):
        self.tracks = {}
        self.total_id_switches = 0
        self.last_single_id = None
        self.ambiguous_frames = 0
        self.confirmed_frames = 0
        self.processed_frames = 0

    def update(self, frame_id, people, alive_ids):
        if frame_id != self.processed_frames + 1:
            raise ValueError("统计必须按处理帧顺序更新，每帧一次。")
        self.processed_frames = frame_id
        visible_ids = {person["track_id"] for person in people}
        for person in people:
            tid = person["track_id"]
            stat = self.tracks.setdefault(tid, {
                "first_frame": frame_id, "last_frame": None, "observed_frames": 0,
                "max_confidence": 0.0, "consecutive_observed_frames": 0,
                "max_consecutive_observed_frames": 0, "last_alive_frame": None,
                "alive_frames": 0, "consecutive_alive_frames": 0, "max_alive_frames": 0})
            stat["consecutive_observed_frames"] = (
                stat["consecutive_observed_frames"] + 1
                if stat["last_frame"] == frame_id - 1 else 1)
            stat["last_frame"] = frame_id
            stat["observed_frames"] += 1
            stat["max_confidence"] = max(stat["max_confidence"], person["confidence"])
            stat["max_consecutive_observed_frames"] = max(
                stat["max_consecutive_observed_frames"], stat["consecutive_observed_frames"])
        alive = set(alive_ids) | visible_ids
        for tid, stat in self.tracks.items():
            if tid not in visible_ids:
                stat["consecutive_observed_frames"] = 0
            if tid in alive:
                stat["consecutive_alive_frames"] = (
                    stat["consecutive_alive_frames"] + 1
                    if stat["last_alive_frame"] == frame_id - 1 else 1)
                stat["last_alive_frame"] = frame_id
                stat["alive_frames"] += 1
                stat["max_alive_frames"] = max(stat["max_alive_frames"], stat["consecutive_alive_frames"])
            else:
                stat["consecutive_alive_frames"] = 0
        if visible_ids:
            self.confirmed_frames += 1
        if len(visible_ids) == 1:
            tid = next(iter(visible_ids))
            if self.last_single_id is not None and tid != self.last_single_id:
                self.total_id_switches += 1
            self.last_single_id = tid
        elif len(visible_ids) > 1:
            # 没有身份真值，不能在多轨迹中猜同一个人；重置比较参照。
            self.ambiguous_frames += 1
            self.last_single_id = None

    def snapshot(self):
        return {
            "processed_frames": self.processed_frames,
            "confirmed_frames": self.confirmed_frames,
            "has_confirmed_tracks": bool(self.tracks),
            "unique_ids": len(self.tracks),
            "max_alive_frames": max((s["max_alive_frames"] for s in self.tracks.values()), default=0),
            "max_consecutive_observed_frames": max(
                (s["max_consecutive_observed_frames"] for s in self.tracks.values()), default=0),
            "total_id_switches": self.total_id_switches,
            "ambiguous_frames": self.ambiguous_frames}

    def print_totals(self):
        values = self.snapshot()
        print("[统计] 单ID最大连续存活帧数=%d(含遮挡缓存) | 最大连续观测帧数=%d | "
              "总ID切换次数=%d | 多轨迹歧义帧数=%d" %
              (values["max_alive_frames"], values["max_consecutive_observed_frames"],
               values["total_id_switches"], values["ambiguous_frames"]), flush=True)


def normalize_tracks(tracks, shape):
    """输出原图像素 xyxy；排除无效或完全在画面外的预测框。"""
    height, width = shape[:2]
    people = []
    for track in tracks:
        values = np.asarray(track["bbox"], dtype=float)
        if values.shape != (4,) or not np.isfinite(values).all():
            continue
        x1, y1, x2, y2 = (int(round(float(value))) for value in values)
        x1, x2 = max(0, min(width - 1, x1)), max(0, min(width - 1, x2))
        y1, y2 = max(0, min(height - 1, y1)), max(0, min(height - 1, y2))
        if x2 <= x1 or y2 <= y1:
            continue
        people.append({"track_id": int(track["track_id"]), "bbox": [x1, y1, x2, y2],
                       "confidence": round(float(track["confidence"]), 5)})
    return sorted(people, key=lambda person: person["track_id"])


def draw_people(frame, people, fps, frame_id):
    annotated = frame.copy()
    for person in people:
        tid = person["track_id"]
        x1, y1, x2, y2 = person["bbox"]
        hsv = np.array([[[tid * 37 % 180, 220, 255]]], dtype=np.uint8)
        color = tuple(int(value) for value in cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0])
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
        label = "person ID:%d %.2f" % (tid, person["confidence"])
        cv2.putText(annotated, label, (x1, max(20, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    cv2.putText(annotated, "Frame:%d People:%d FPS:%.1f" % (frame_id, len(people), fps),
                (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
    return annotated


def atomic_jpeg(path, frame):
    ok, encoded = cv2.imencode(".jpg", frame)
    if not ok:
        raise RuntimeError("标注图像编码失败。")
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(encoded.tobytes())
    temporary.replace(path)


def interrupt_handler(_signum, _frame):
    raise KeyboardInterrupt


def run(args):
    name = time.strftime("%Y%m%d_%H%M%S") + "_%d_%06d" % (os.getpid(), time.time_ns() % 1000000)
    output = args.out_dir.expanduser() / name
    output.mkdir(parents=True, exist_ok=False)
    camera = detector = writer = None
    metrics = TrackingMetrics()
    frames = 0
    reason, error, backend = "finished", None, None
    started = time.monotonic()
    last_timestamp = previous_shape = None
    previous_sigterm = signal.signal(signal.SIGTERM, interrupt_handler)
    print("[输出] %s" % output, flush=True)
    try:
        with (output / "tracks.jsonl").open("w", encoding="utf-8") as records:
            try:
                detector = PersonDetector(args)
                backend = detector.backend
                camera = make_camera(args)
                tracker = PersonTracker(backend, camera.fps, args.track_thresh,
                                        args.match_thresh, args.lost_seconds,
                                        args.new_track_thresh, args.fuse_score)
                print("[模型] %s | %s | ByteTrack | 输入帧率 %.2f" %
                      (backend, detector.model_path, camera.fps), flush=True)
                print("[单人配置] 缓存=%.1f秒 | 关联代价上限=%.2f | 高分阈值=%.2f | "
                      "新轨迹阈值=%.2f | 置信度融合=%s" %
                      (args.lost_seconds, args.match_thresh, args.track_thresh,
                       args.new_track_thresh, args.fuse_score), flush=True)
                while True:
                    if args.duration and time.monotonic() - started >= args.duration:
                        reason = "duration"
                        break
                    ok, frame = camera.read()
                    if not ok:
                        if not frames:
                            raise RuntimeError("输入视频没有可解码的图像。")
                        reason = "video_end"
                        break
                    shape = frame.shape[:2]
                    if previous_shape is not None and shape != previous_shape:
                        raise RuntimeError("输入图像尺寸改变：%s -> %s；请固定相机分辨率。" %
                                           (previous_shape, shape))
                    previous_shape = shape
                    if camera.live:
                        tracker.age_gap(last_timestamp, camera.timestamp, camera.fps, frame)
                    last_timestamp = camera.timestamp
                    detections = detector.detect(frame)
                    people = normalize_tracks(tracker.update(detections, frame), frame.shape)
                    frames += 1
                    alive_ids = tracker.alive_ids()
                    metrics.update(frames, people, alive_ids)
                    fps = frames / max(1e-6, time.monotonic() - started)
                    record = {"event": "frame", "frame": frames, "timestamp": camera.timestamp,
                              "captured_at": camera.captured_at, "live": camera.live,
                              "width": shape[1], "height": shape[0],
                              "detection_count": len(detections), "people": people,
                              "cached_track_ids": sorted(alive_ids - {p["track_id"] for p in people}),
                              "tracking_metrics": metrics.snapshot()}
                    records.write(json.dumps(record, ensure_ascii=False) + "\n")
                    records.flush()
                    if frames % args.log_every == 0:
                        if not people:
                            print("帧%06d | 无已确认行人轨迹 | 检测框=%d" %
                                  (frames, len(detections)), flush=True)
                        for person in people:
                            print("帧%06d | 目标ID=%d | bbox(x1,y1,x2,y2)=%s | conf=%.3f" %
                                  (frames, person["track_id"], tuple(person["bbox"]), person["confidence"]),
                                  flush=True)
                        metrics.print_totals()
                    annotated = draw_people(frame, people, fps, frames)
                    atomic_jpeg(output / "latest.jpg", annotated)
                    if not args.no_save_video:
                        if writer is None:
                            writer = cv2.VideoWriter(str(output / "person_track.avi"),
                                                     cv2.VideoWriter_fourcc(*"MJPG"), camera.fps,
                                                     (shape[1], shape[0]))
                            if not writer.isOpened():
                                raise RuntimeError("无法创建标注 AVI；使用 --no-save-video 只保存最新图像。")
                        writer.write(annotated)
                    if args.max_frames and frames >= args.max_frames:
                        reason = "max_frames"
                        break
            except KeyboardInterrupt:
                reason = "interrupted"
            except Exception as exc:
                reason, error = "error", str(exc)
            finally:
                records.write(json.dumps({"event": "stopped", "reason": reason, "error": error,
                                          "frame": frames, "people": [],
                                          "tracking_metrics": metrics.snapshot()}, ensure_ascii=False) + "\n")
    finally:
        try:
            if camera is not None:
                camera.release()
        finally:
            try:
                if writer is not None:
                    writer.release()
                if detector is not None:
                    detector.close()
            finally:
                signal.signal(signal.SIGTERM, previous_sigterm)
    elapsed = max(1e-6, time.monotonic() - started)
    summary = {"source": args.source, "backend": backend, "frames": frames,
               "elapsed_seconds": round(elapsed, 3), "average_fps": round(frames / elapsed, 2),
               "reason": reason, "error": error, "tracks": metrics.tracks,
               "tracking_metrics": metrics.snapshot(),
               "tracker_config": {"track_thresh": args.track_thresh,
                                  "new_track_thresh": args.new_track_thresh,
                                  "match_thresh": args.match_thresh,
                                  "lost_seconds": args.lost_seconds,
                                  "fuse_score": args.fuse_score,
                                  "input_fps": camera.fps if camera is not None else None},
               "metric_scope": "single-person experiment; sequential confirmed ID changes, not ground-truth MOT IDSW; "
                               "empty frames retain the last ID; ambiguous multi-track frames reset comparison; "
                               "alive frames include cached occlusion, observed streaks do not; counts use processed frames",
               "coordinate_format": "xyxy, pixels in original image; origin at top-left",
               "id_scope": "this run; trajectory ID, not confirmed person identity"}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("[结束] 帧数=%d | 累计轨迹ID=%d | 平均FPS=%.2f | 原因=%s" %
          (frames, len(metrics.tracks), frames / elapsed, reason), flush=True)
    metrics.print_totals()
    for tid, stat in sorted(metrics.tracks.items()):
        print("[轨迹] ID=%d | 最大连续存活帧数=%d | 最大连续观测帧数=%d | 观测帧数=%d" %
              (tid, stat["max_alive_frames"], stat["max_consecutive_observed_frames"],
               stat["observed_frames"]), flush=True)
    if not metrics.tracks:
        print("[统计说明] 没有已确认人员轨迹，本次无法评价 ID 稳定性。", flush=True)
    if error:
        print("[错误] " + error, file=sys.stderr, flush=True)
    print("[结果] %s" % output, flush=True)
    return 1 if error else 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default="go2", help="go2 | native | usb:0 | file:/path/video.mp4 | RTSP URL")
    parser.add_argument("--backend", choices=["auto", "tensorrt", "ultralytics"], default="auto")
    parser.add_argument("--model", default=None, help="本地 YOLOX-Tiny .engine 或 COCO YOLO .pt 权重")
    parser.add_argument("--device", default="cpu", help="ultralytics 设备：cpu 或 0；TensorRT 使用 Jetson GPU")
    parser.add_argument("--conf", type=float, default=0.1, help="保留低置信度检测供 ByteTrack 二次匹配")
    parser.add_argument("--iou", type=float, default=0.45, help="检测 NMS 阈值，非跟踪关联阈值")
    parser.add_argument("--imgsz", type=int, default=640, help="ultralytics 输入尺寸；TensorRT 使用引擎固定尺寸")
    parser.add_argument("--track-thresh", type=float, default=0.2, help="ByteTrack 高置信度匹配阈值")
    parser.add_argument("--new-track-thresh", type=float, default=0.35, help="独立的新轨迹门槛，避免低分候选直接创建 ID")
    parser.add_argument("--match-thresh", type=float, default=0.9, help="关联代价上限；越大允许越小的框重叠")
    parser.add_argument("--lost-seconds", type=float, default=6.0, help="单人遮挡缓存秒数，按输入帧率折算")
    parser.add_argument("--fuse-score", action="store_true", help="在框重叠代价中融合置信度；单人配置默认关闭")
    parser.add_argument("--fps", type=float, default=5.0, help="Go2 采集服务帧率，及无帧率信息输入的备用帧率")
    parser.add_argument("--camera-dir", type=Path, default=CAMERA_DIR, help="已有 Go2 相机 JPEG/status 目录")
    parser.add_argument("--max-age", type=float, default=2.0, help="Go2 图像最大允许年龄（秒）")
    parser.add_argument("--read-timeout", type=float, default=5.0, help="等待 Go2 新帧的超时（秒）")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "runs" / "track")
    parser.add_argument("--no-save-video", action="store_true", help="只保存 latest.jpg、JSONL 与统计")
    parser.add_argument("--log-every", type=int, default=1, help="每 N 帧打印 ID 和坐标，默认每帧")
    parser.add_argument("--max-frames", type=int, default=0, help="处理帧数上限；0 表示不限")
    parser.add_argument("--duration", type=float, default=0.0, help="运行时长上限（秒）；0 表示不限")
    args = parser.parse_args(argv)
    positive = (args.fps, args.lost_seconds, args.max_age, args.read_timeout)
    if not all(math.isfinite(value) and value > 0 for value in positive) or args.fps > 30:
        parser.error("fps 必须在 (0,30]，lost-seconds/max-age/read-timeout 必须为正有限数")
    if not (0 < args.conf <= args.track_thresh < 0.9
            and 0.1 < args.track_thresh <= args.new_track_thresh <= 1
            and 0 < args.iou <= 1 and 0 < args.match_thresh <= 1):
        parser.error("需满足 0 < conf <= track-thresh < 0.9，0.1 < track-thresh <= new-track-thresh <= 1；"
                     "iou/match-thresh 在 (0,1]")
    if args.imgsz <= 0 or args.log_every <= 0 or args.max_frames < 0 or not math.isfinite(args.duration) or args.duration < 0:
        parser.error("imgsz/log-every 必须为正，max-frames/duration 必须非负")
    args.camera_dir = args.camera_dir.expanduser()
    return args


if __name__ == "__main__":
    try:
        raise SystemExit(run(parse_args()))
    except (RuntimeError, OSError, ImportError, ValueError, cv2.error) as exc:
        print("[错误] " + str(exc), file=sys.stderr, flush=True)
        raise SystemExit(1)
