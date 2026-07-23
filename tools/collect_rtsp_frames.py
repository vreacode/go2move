#!/usr/bin/env python3
"""Capture sparse frames from an explicit camera adapter.

This tool only reads a video source and writes image files plus a CSV manifest.
It never starts navigation, motion, or vehicle-control commands. Go2 ROS2
adapters are opt-in through ``--source-type`` and are shared with the
Dashboard/Agent camera path.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
import time
from pathlib import Path

try:
    from .camera_adapter import open_camera
except ImportError:  # direct script execution from the tools directory
    from camera_adapter import open_camera


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="adapter source: /api/videohub, topic, camera index, URL, or video file")
    parser.add_argument(
        "--source-type",
        choices=("opencv", "go2_ros2", "go2_ros2_videohub", "unitree_sdk2"),
        default="opencv",
        help=(
            "source adapter; opencv handles files/USB/verified URLs, "
            "go2_ros2 reads /frontvideostream, go2_ros2_videohub requests a JPEG "
            "from the official API, unitree_sdk2 reads the official SDK2 camera"
        ),
    )
    parser.add_argument(
        "--output",
        default="datasets/local/roadside_v1/images",
        help="directory for captured frames",
    )
    parser.add_argument(
        "--manifest",
        default="",
        help="CSV path; defaults to dataset root/manifest.csv",
    )
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between saved frames")
    parser.add_argument("--warmup-seconds", type=float, default=2.0, help="seconds to read and discard after opening the stream")
    parser.add_argument("--max-frames", type=int, default=0, help="0 means run until Ctrl+C")
    parser.add_argument("--duration", type=float, default=0.0, help="maximum capture duration in seconds; 0 means no time limit")
    parser.add_argument("--jpeg-quality", type=int, default=100, help="JPEG quality 1-100")
    parser.add_argument(
        "--max-read-failures",
        type=int,
        default=10,
        help="maximum consecutive read failures before capture stops",
    )
    parser.add_argument(
        "--reopen-after-failures",
        type=int,
        default=3,
        help="recreate the adapter after this many consecutive read failures",
    )
    parser.add_argument(
        "--read-retry-seconds",
        type=float,
        default=0.1,
        help="delay between failed reads or adapter reopen attempts",
    )
    parser.add_argument("--scene", default="", help="scene identifier, for example gate_a_day")
    parser.add_argument("--note", default="", help="free-form capture note")
    parser.add_argument("--preview", action="store_true", help="show preview; press q to stop")
    return parser.parse_args()


def show_preview(cv2, frame, args: argparse.Namespace, saved: int, next_save_at: float) -> bool:
    """Show a small heads-up display without changing the saved frame."""
    display = frame.copy()
    remaining = max(0.0, next_save_at - time.monotonic())
    next_save = "now" if remaining <= 0.0 else f"{remaining:.1f}s"
    height, width = display.shape[:2]
    lines = [
        f"scene: {args.scene or '-'}",
        f"saved: {saved}   next: {next_save}   interval: {args.interval:.1f}s",
        f"size: {width}x{height}   q: stop",
    ]
    panel_height = 26 + 24 * len(lines)
    cv2.rectangle(display, (0, 0), (min(width, 760), panel_height), (0, 0, 0), -1)
    for index, line in enumerate(lines, start=1):
        cv2.putText(
            display,
            line,
            (12, 24 * index),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            (0, 255, 0),
            1,
            cv2.LINE_AA,
        )
    cv2.imshow("RTSP capture", display)
    return cv2.waitKey(1) & 0xFF == ord("q")


def write_jpeg_atomically(cv2, frame, frame_path: Path, quality: int) -> tuple[int, int, int]:
    """Encode and atomically publish one valid JPEG.

    The manifest is written only after the final rename. A browser, gallery
    reader, or dataset preparation job therefore never observes a half-written
    image. The decode check also catches an encoder result that cannot be read
    back before it is published.
    """

    if frame is None or not getattr(frame, "shape", None) or len(frame.shape) < 2:
        raise RuntimeError("camera returned an empty frame")
    height, width = int(frame.shape[0]), int(frame.shape[1])
    if width < 2 or height < 2:
        raise RuntimeError(f"camera returned an invalid frame size: {width}x{height}")

    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok or encoded is None or int(encoded.size) < 32:
        raise RuntimeError("JPEG encoding failed")
    decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if decoded is None or decoded.size == 0:
        raise RuntimeError("JPEG validation failed")

    temporary_path = frame_path.with_name(f".{frame_path.name}.tmp")
    try:
        with temporary_path.open("wb") as handle:
            handle.write(encoded.tobytes())
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(frame_path)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
    return width, height, int(encoded.size)


def main() -> int:
    args = parse_args()
    try:
        import cv2
    except ImportError:
        print("OpenCV is required in the active environment.", file=sys.stderr)
        return 2

    if args.interval <= 0:
        print("--interval must be greater than zero", file=sys.stderr)
        return 2
    if args.warmup_seconds < 0:
        print("--warmup-seconds must be zero or greater", file=sys.stderr)
        return 2
    if args.duration < 0:
        print("--duration must be zero or greater", file=sys.stderr)
        return 2
    if not 1 <= args.jpeg_quality <= 100:
        print("--jpeg-quality must be between 1 and 100", file=sys.stderr)
        return 2
    if args.max_read_failures < 1:
        print("--max-read-failures must be at least one", file=sys.stderr)
        return 2
    if args.reopen_after_failures < 1 or args.reopen_after_failures > args.max_read_failures:
        print("--reopen-after-failures must be between one and --max-read-failures", file=sys.stderr)
        return 2
    if args.read_retry_seconds < 0:
        print("--read-retry-seconds must be zero or greater", file=sys.stderr)
        return 2

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = Path(args.manifest) if args.manifest else output_dir.parent / "manifest.csv"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_exists = manifest_path.exists() and manifest_path.stat().st_size > 0

    try:
        capture = open_camera(args.source, args.source_type)
    except Exception as exc:
        print(f"Unable to initialize video source type={args.source_type}: {exc}", file=sys.stderr)
        return 1
    if not capture.isOpened():
        print(f"Unable to open video source type={args.source_type}: {args.source}", file=sys.stderr)
        return 1

    warmup_valid = False
    if args.warmup_seconds > 0:
        warmup_deadline = time.monotonic() + args.warmup_seconds
        discarded = 0
        while time.monotonic() < warmup_deadline:
            ok, frame = capture.read()
            discarded += 1
            if ok and frame is not None and getattr(frame, "size", 0):
                warmup_valid = True
            elif args.read_retry_seconds:
                time.sleep(args.read_retry_seconds)
        if not warmup_valid:
            capture.release()
            print("video source opened but produced no valid frame during warm-up", file=sys.stderr)
            return 1
        print(f"stream warm-up complete: discarded {discarded} frame(s)")

    saved = 0
    next_save_at = 0.0
    writer = manifest_path.open("a", newline="", encoding="utf-8")
    manifest = csv.writer(writer)
    base_fields = ["frame_path", "captured_at", "scene", "source", "note"]
    metadata_fields = ["source_type", "width", "height", "jpeg_quality", "jpeg_bytes"]
    fieldnames = base_fields + metadata_fields
    if manifest_exists:
        with manifest_path.open("r", newline="", encoding="utf-8-sig") as existing_handle:
            existing_fields = next(csv.reader(existing_handle), [])
        if existing_fields:
            fieldnames = existing_fields
    else:
        manifest.writerow(fieldnames)

    print(f"capturing from type={args.source_type} source={args.source}")
    print(f"frames: {output_dir}")
    print(f"manifest: {manifest_path}")
    limit_text = f", duration limit: {args.duration:.1f}s" if args.duration else ""
    print("stop with Ctrl+C" + (" or q in the preview window" if args.preview else "") + limit_text)

    capture_failed = False
    consecutive_failures = 0
    try:
        started_at = time.monotonic()
        while args.max_frames <= 0 or saved < args.max_frames:
            if args.duration and time.monotonic() - started_at >= args.duration:
                print(f"duration limit reached: {args.duration:.1f}s")
                break
            if capture is None or not capture.isOpened():
                consecutive_failures += 1
                try:
                    capture = open_camera(args.source, args.source_type)
                except Exception as exc:
                    print(f"camera adapter reopen failed: {exc}", file=sys.stderr)
                    capture = None
                if capture is not None and capture.isOpened():
                    print("camera adapter reopened")
                else:
                    capture = None
                    if consecutive_failures >= args.max_read_failures:
                        print("camera adapter could not be reopened; stopping", file=sys.stderr)
                        capture_failed = True
                        break
                    if args.read_retry_seconds:
                        time.sleep(args.read_retry_seconds)
                    continue
            ok, frame = capture.read()
            if not ok or frame is None or not getattr(frame, "size", 0):
                consecutive_failures += 1
                print(
                    f"stream read failed ({consecutive_failures}/{args.max_read_failures}); retrying",
                    file=sys.stderr,
                )
                if consecutive_failures >= args.reopen_after_failures:
                    capture.release()
                    capture = None
                    print("reopening camera adapter after read failures")
                if consecutive_failures >= args.max_read_failures:
                    print("stream read failed repeatedly; stopping", file=sys.stderr)
                    capture_failed = True
                    break
                if args.read_retry_seconds:
                    time.sleep(args.read_retry_seconds)
                continue
            consecutive_failures = 0

            now = time.monotonic()
            if now < next_save_at:
                if args.preview:
                    if show_preview(cv2, frame, args, saved, next_save_at):
                        break
                time.sleep(min(0.02, max(0.0, next_save_at - now)))
                continue

            captured_at = dt.datetime.now().astimezone()
            filename = captured_at.strftime("%Y%m%d_%H%M%S_%f")[:-3] + ".jpg"
            frame_path = output_dir / filename
            try:
                width, height, jpeg_bytes = write_jpeg_atomically(cv2, frame, frame_path, args.jpeg_quality)
            except (OSError, RuntimeError) as exc:
                print(f"failed to publish {frame_path}: {exc}", file=sys.stderr)
                capture_failed = True
                break

            row = {
                "frame_path": str(frame_path),
                "captured_at": captured_at.isoformat(timespec="milliseconds"),
                "scene": args.scene,
                "source": args.source,
                "note": args.note,
                "source_type": args.source_type,
                "width": str(width),
                "height": str(height),
                "jpeg_quality": str(args.jpeg_quality),
                "jpeg_bytes": str(jpeg_bytes),
            }
            manifest.writerow([row.get(field, "") for field in fieldnames])
            writer.flush()
            saved += 1
            next_save_at = now + args.interval
            print(f"saved {saved}: {frame_path}")

            if args.preview:
                if show_preview(cv2, frame, args, saved, next_save_at):
                    break
    except KeyboardInterrupt:
        print("\ninterrupted by user")
    finally:
        if capture is not None:
            capture.release()
        writer.close()
        if args.preview:
            cv2.destroyAllWindows()

    print(f"capture complete: {saved} frame(s)")
    return 1 if capture_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
