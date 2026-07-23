#!/usr/bin/env python3
"""Capture sparse RTSP frames for local roadside dataset construction.

This tool only reads a video stream and writes image files plus a CSV manifest.
It never starts ROS nodes and never sends vehicle-control commands.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
import time
from pathlib import Path

try:
    from .camera_adapter import open_camera
except ImportError:  # direct script execution from the tools directory
    from camera_adapter import open_camera


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="RTSP URL, camera index, or video file")
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
    parser.add_argument("--jpeg-quality", type=int, default=95, help="JPEG quality 1-100")
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

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = Path(args.manifest) if args.manifest else output_dir.parent / "manifest.csv"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_exists = manifest_path.exists() and manifest_path.stat().st_size > 0

    capture = open_camera(args.source, args.source_type)
    if not capture.isOpened():
        print(f"Unable to open video source: {args.source}", file=sys.stderr)
        return 1

    if args.warmup_seconds > 0:
        warmup_deadline = time.monotonic() + args.warmup_seconds
        discarded = 0
        while time.monotonic() < warmup_deadline:
            ok, _ = capture.read()
            discarded += 1
            if not ok:
                time.sleep(0.05)
        print(f"stream warm-up complete: discarded {discarded} frame(s)")

    saved = 0
    next_save_at = 0.0
    writer = manifest_path.open("a", newline="", encoding="utf-8")
    manifest = csv.writer(writer)
    if not manifest_exists:
        manifest.writerow(["frame_path", "captured_at", "scene", "source", "note"])

    print(f"capturing from {args.source}")
    print(f"frames: {output_dir}")
    print(f"manifest: {manifest_path}")
    limit_text = f", duration limit: {args.duration:.1f}s" if args.duration else ""
    print("stop with Ctrl+C" + (" or q in the preview window" if args.preview else "") + limit_text)

    try:
        started_at = time.monotonic()
        while args.max_frames <= 0 or saved < args.max_frames:
            if args.duration and time.monotonic() - started_at >= args.duration:
                print(f"duration limit reached: {args.duration:.1f}s")
                break
            ok, frame = capture.read()
            if not ok:
                print("stream read failed; stopping", file=sys.stderr)
                break

            now = time.monotonic()
            if now < next_save_at:
                if args.preview:
                    if show_preview(cv2, frame, args, saved, next_save_at):
                        break
                continue

            captured_at = dt.datetime.now().astimezone()
            filename = captured_at.strftime("%Y%m%d_%H%M%S_%f")[:-3] + ".jpg"
            frame_path = output_dir / filename
            ok = cv2.imwrite(
                str(frame_path),
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), args.jpeg_quality],
            )
            if not ok:
                print(f"failed to write {frame_path}", file=sys.stderr)
                break

            manifest.writerow(
                [
                    str(frame_path),
                    captured_at.isoformat(timespec="seconds"),
                    args.scene,
                    args.source,
                    args.note,
                ]
            )
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
        capture.release()
        writer.close()
        if args.preview:
            cv2.destroyAllWindows()

    print(f"capture complete: {saved} frame(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
