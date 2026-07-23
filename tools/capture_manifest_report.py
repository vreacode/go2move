#!/usr/bin/env python3
"""Summarize an RTSP capture manifest before annotation or dataset splitting."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

try:
    from tools.prepare_yolo_dataset import CaptureRow, read_manifest
except ModuleNotFoundError:  # direct execution: python3 tools/capture_manifest_report.py
    from prepare_yolo_dataset import CaptureRow, read_manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="capture CSV produced by collect_rtsp_frames.py")
    parser.add_argument("--report", default="", help="optional JSON report path")
    return parser.parse_args()


def _timestamp(value: str) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def _group_summary(rows: list[CaptureRow]) -> list[dict[str, object]]:
    grouped: dict[str, list[CaptureRow]] = defaultdict(list)
    for row in rows:
        grouped[row.group].append(row)

    summaries: list[dict[str, object]] = []
    for group in sorted(grouped):
        group_rows = grouped[group]
        timestamps = sorted(timestamp for timestamp in (_timestamp(row.captured_at) for row in group_rows) if timestamp)
        intervals = [
            (later - earlier).total_seconds()
            for earlier, later in zip(timestamps, timestamps[1:])
            if (later - earlier).total_seconds() >= 0
        ]
        summaries.append(
            {
                "group": group,
                "scene": group_rows[0].scene,
                "frames": len(group_rows),
                "first_captured_at": timestamps[0].isoformat() if timestamps else "",
                "last_captured_at": timestamps[-1].isoformat() if timestamps else "",
                "duration_seconds": round((timestamps[-1] - timestamps[0]).total_seconds(), 3) if len(timestamps) >= 2 else 0.0,
                "median_interval_seconds": round(median(intervals), 3) if intervals else 0.0,
                "sources": sorted({row.source for row in group_rows if row.source}),
                "missing_files": sum(not row.source_path.is_file() for row in group_rows),
            }
        )
    return summaries


def summarize(rows: list[CaptureRow]) -> dict[str, object]:
    groups = _group_summary(rows)
    scenes = sorted({row.scene for row in rows})
    sources = sorted({row.source for row in rows if row.source})
    warnings: list[str] = []
    if len(groups) < 2:
        warnings.append("fewer than two scene/date groups; train/val split cannot be meaningful")
    if len(scenes) < 2:
        warnings.append("only one scene; collect multiple routes or viewpoints")
    if any(group["missing_files"] for group in groups):
        warnings.append("manifest contains missing image files")
    if len(sources) > 1:
        warnings.append("manifest mixes multiple video sources; review visible/infrared separation")
    return {
        "manifest_rows": len(rows),
        "scenes": scenes,
        "sources": sources,
        "groups": groups,
        "warnings": warnings,
        "ready_for_grouped_split": not warnings,
    }


def main() -> int:
    args = parse_args()
    try:
        rows = read_manifest(Path(args.manifest).expanduser().resolve())
        result = summarize(rows)
        if args.report:
            report_path = Path(args.report).expanduser()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
