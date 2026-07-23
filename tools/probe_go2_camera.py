#!/usr/bin/env python3
"""Read-only probe for the official Go2 front camera.

This command requests image samples through the official SDK2 VideoClient and
optionally writes a single JPEG. It never creates a SportClient or publishes
motion commands.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from camera_adapter import Go2Sdk2FrontVideoAdapter


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", default="eth0", help="network interface connected to Go2")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--wait-seconds", type=float, default=5.0)
    parser.add_argument("--output", type=Path, default=None, help="optional JPEG output path")
    args = parser.parse_args()

    try:
        capture = Go2Sdk2FrontVideoAdapter(args.interface, args.timeout)
    except Exception as exc:
        print(f"camera adapter unavailable: {exc}", file=sys.stderr)
        return 2

    deadline = time.monotonic() + max(args.wait_seconds, 0.0)
    attempts = 0
    try:
        while time.monotonic() < deadline:
            attempts += 1
            ok, frame = capture.read()
            if not ok:
                time.sleep(0.05)
                continue
            height, width = frame.shape[:2]
            print(f"camera_frame_ok interface={capture.interface} size={width}x{height} attempts={attempts}")
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                import cv2

                if not cv2.imwrite(str(args.output), frame):
                    print(f"failed to write {args.output}", file=sys.stderr)
                    return 1
                print(f"saved={args.output}")
            return 0
    finally:
        capture.release()

    print(f"no camera frame received interface={args.interface} attempts={attempts}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
