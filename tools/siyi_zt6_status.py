#!/usr/bin/env python3
"""Read-only SIYI ZT6 status query for the board."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "apps" / "nano-deploy"))

from camera.siyi_zt6 import DEFAULT_PORT, SiyiZt6Client, snapshot_json  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Query SIYI ZT6 hardware, firmware, gimbal status and attitude."
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("SIYI_ZT6_HOST", ""),
        help="ZT6 IP address from SIYI_ZT6_HOST; no private default is embedded",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("SIYI_ZT6_PORT", str(DEFAULT_PORT))),
        help=f"ZT6 UDP port (default: {DEFAULT_PORT})",
    )
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args()

    try:
        snapshot = SiyiZt6Client(args.host, args.port, args.timeout).query_snapshot()
    except Exception as exc:  # CLI boundary: show a concise hardware diagnostic.
        print(f"ZT6 查询失败: {exc}", file=sys.stderr)
        return 1

    print(snapshot_json(snapshot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
