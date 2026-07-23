#!/usr/bin/env python3
"""Prepare a YOLO directory from an RTSP capture manifest.

The command copies images into a new working directory and never modifies or
deletes the raw capture.  Train/validation assignment is grouped by scene and
capture date so adjacent frames from one route are kept in the same split.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import random
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


@dataclass(frozen=True)
class CaptureRow:
    source_path: Path
    captured_at: str
    scene: str
    source: str
    note: str
    group: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="capture CSV produced by collect_rtsp_frames.py")
    parser.add_argument(
        "--output",
        required=True,
        help="new local YOLO dataset directory; it must be empty or not exist",
    )
    parser.add_argument("--val-ratio", type=float, default=0.2, help="validation group ratio, default 0.2")
    parser.add_argument("--seed", type=int, default=20260719, help="deterministic split seed")
    parser.add_argument(
        "--names",
        nargs="+",
        default=["garbage", "vehicle"],
        help="class names in class-id order, default: garbage vehicle",
    )
    return parser.parse_args()


def _capture_date(value: str) -> str:
    if not value:
        return "unknown-date"
    try:
        return dt.datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        return value[:10] or "unknown-date"


def _resolve_frame_path(raw: str, manifest_path: Path) -> Path:
    candidate = Path(raw).expanduser()
    candidates = [candidate]
    if not candidate.is_absolute():
        candidates.extend([manifest_path.parent / candidate, Path.cwd() / candidate])
    for item in candidates:
        if item.is_file():
            return item.resolve()
    return candidate.resolve()


def read_manifest(manifest_path: Path) -> list[CaptureRow]:
    with manifest_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "frame_path" not in reader.fieldnames:
            raise ValueError("manifest must contain a frame_path column")
        rows: list[CaptureRow] = []
        for line_number, row in enumerate(reader, start=2):
            raw_path = (row.get("frame_path") or "").strip()
            if not raw_path:
                raise ValueError(f"manifest line {line_number}: frame_path is empty")
            scene = (row.get("scene") or "unknown-scene").strip() or "unknown-scene"
            captured_at = (row.get("captured_at") or "").strip()
            group = f"{scene}__{_capture_date(captured_at)}"
            rows.append(
                CaptureRow(
                    source_path=_resolve_frame_path(raw_path, manifest_path),
                    captured_at=captured_at,
                    scene=scene,
                    source=(row.get("source") or "").strip(),
                    note=(row.get("note") or "").strip(),
                    group=group,
                )
            )
    if not rows:
        raise ValueError("manifest contains no capture rows")
    return rows


def assign_splits(rows: list[CaptureRow], val_ratio: float, seed: int) -> dict[str, str]:
    if not 0.0 <= val_ratio < 1.0:
        raise ValueError("--val-ratio must be in [0, 1)")
    groups = sorted({row.group for row in rows})
    validation_count = 0
    if val_ratio > 0 and len(groups) >= 2:
        validation_count = max(1, round(len(groups) * val_ratio))
        validation_count = min(validation_count, len(groups) - 1)
    shuffled = groups[:]
    random.Random(seed).shuffle(shuffled)
    validation_groups = set(shuffled[:validation_count])
    return {group: ("val" if group in validation_groups else "train") for group in groups}


def write_dataset_yaml(output: Path, names: list[str]) -> None:
    lines = ["path: .", "train: images/train", "val: images/val", "", "names:"]
    lines.extend(f"  {index}: {name}" for index, name in enumerate(names))
    (output / "dataset.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def prepare(rows: list[CaptureRow], output: Path, val_ratio: float, seed: int, names: list[str]) -> dict[str, int]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    for split in ("train", "val"):
        (output / "images" / split).mkdir(parents=True, exist_ok=True)
        (output / "labels" / split).mkdir(parents=True, exist_ok=True)

    split_by_group = assign_splits(rows, val_ratio, seed)
    split_counts = {"train": 0, "val": 0}
    manifest_path = output / "split_manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["image_path", "split", "group", "scene", "captured_at", "source", "note"])
        for index, row in enumerate(rows, start=1):
            if not row.source_path.is_file():
                raise FileNotFoundError(f"capture image not found: {row.source_path}")
            if row.source_path.suffix.lower() not in IMAGE_EXTENSIONS:
                raise ValueError(f"unsupported image extension: {row.source_path}")
            split = split_by_group[row.group]
            filename = f"{index:06d}_{row.source_path.name}"
            destination = output / "images" / split / filename
            shutil.copy2(row.source_path, destination)

            source_label = row.source_path.with_suffix(".txt")
            if source_label.is_file():
                shutil.copy2(source_label, output / "labels" / split / f"{destination.stem}.txt")
            writer.writerow([destination.relative_to(output), split, row.group, row.scene, row.captured_at, row.source, row.note])
            split_counts[split] += 1

    write_dataset_yaml(output, names)
    return split_counts


def main() -> int:
    args = parse_args()
    try:
        manifest = Path(args.manifest).expanduser().resolve()
        output = Path(args.output).expanduser().resolve()
        rows = read_manifest(manifest)
        counts = prepare(rows, output, args.val_ratio, args.seed, args.names)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"prepared {sum(counts.values())} image(s) at {output}")
    print(f"train={counts['train']} val={counts['val']}")
    print(f"dataset yaml: {output / 'dataset.yaml'}")
    if counts["val"] == 0:
        print("warning: no validation images; collect at least two scene/date groups")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
