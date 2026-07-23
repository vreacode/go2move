#!/usr/bin/env python3
"""Validate a YOLO detection dataset without third-party dependencies."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="YOLO dataset YAML")
    parser.add_argument("--report", default="", help="optional JSON report path")
    parser.add_argument("--allow-missing-labels", action="store_true", help="warn instead of failing for missing labels")
    return parser.parse_args()


def parse_dataset_yaml(path: Path) -> tuple[Path, dict[str, str], list[str]]:
    values: dict[str, str] = {}
    names: dict[int, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^(path|train|val):\s*(.+)$", line)
        if match:
            values[match.group(1)] = match.group(2).strip().strip("'\"")
            continue
        match = re.match(r"^(\d+):\s*(.+)$", line)
        if match:
            names[int(match.group(1))] = match.group(2).strip().strip("'\"")
    if "train" not in values or "val" not in values or not names:
        raise ValueError("dataset YAML must define train, val and names")

    dataset_path = Path(values.get("path", "."))
    candidates = []
    if dataset_path.is_absolute():
        candidates.append(dataset_path)
    else:
        candidates.extend(
            [
                path.parent / dataset_path,
                Path.cwd() / dataset_path,
                path.parent.parent / dataset_path,
            ]
        )
    root = next((candidate.resolve() for candidate in candidates if candidate.exists()), candidates[0].resolve())
    return root, values, [names[index] for index in sorted(names)]


def label_path_for(image: Path) -> Path:
    parts = list(image.parts)
    try:
        image_index = parts.index("images")
    except ValueError:
        return image.with_suffix(".txt")
    parts[image_index] = "labels"
    return Path(*parts).with_suffix(".txt")


def validate_split(root: Path, split_path: Path, names: list[str], allow_missing: bool) -> dict[str, object]:
    images = sorted(path for path in split_path.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS)
    errors: list[str] = []
    warnings: list[str] = []
    label_count = 0
    object_count = 0
    class_counts = [0 for _ in names]
    image_stems = {image.stem for image in images}

    for image in images:
        label = label_path_for(image)
        if not label.is_file():
            message = f"missing label: {image.relative_to(root)}"
            (warnings if allow_missing else errors).append(message)
            continue
        label_count += 1
        for line_number, raw_line in enumerate(label.read_text(encoding="utf-8").splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            fields = line.split()
            if len(fields) != 5:
                errors.append(f"{label.relative_to(root)}:{line_number}: expected 5 fields")
                continue
            try:
                class_id = int(fields[0])
                coordinates = [float(value) for value in fields[1:]]
            except ValueError:
                errors.append(f"{label.relative_to(root)}:{line_number}: non-numeric label")
                continue
            if not 0 <= class_id < len(names):
                errors.append(f"{label.relative_to(root)}:{line_number}: class id {class_id} is outside [0, {len(names) - 1}]")
            if not all(math.isfinite(value) for value in coordinates):
                errors.append(f"{label.relative_to(root)}:{line_number}: coordinate is not finite")
                continue
            x_center, y_center, width, height = coordinates
            if width <= 0 or height <= 0:
                errors.append(f"{label.relative_to(root)}:{line_number}: width and height must be positive")
            if any(value < 0 or value > 1 for value in coordinates):
                errors.append(f"{label.relative_to(root)}:{line_number}: coordinates must be normalized to [0, 1]")
            if x_center - width / 2 < 0 or x_center + width / 2 > 1 or y_center - height / 2 < 0 or y_center + height / 2 > 1:
                errors.append(f"{label.relative_to(root)}:{line_number}: bounding box extends outside image")
            if 0 <= class_id < len(class_counts):
                class_counts[class_id] += 1
            object_count += 1

    labels_dir = split_path.parent.parent / "labels" / split_path.name
    if labels_dir.exists():
        for label in labels_dir.rglob("*.txt"):
            if label.stem not in image_stems:
                errors.append(f"orphan label: {label.relative_to(root)}")

    if not images:
        errors.append(f"no images found in {split_path.relative_to(root)}")
    return {
        "images": len(images),
        "labels": label_count,
        "objects": object_count,
        "class_counts": dict(zip(names, class_counts)),
        "errors": errors,
        "warnings": warnings,
    }


def validate(dataset_yaml: Path, allow_missing: bool) -> dict[str, object]:
    root, values, names = parse_dataset_yaml(dataset_yaml)
    splits: dict[str, object] = {}
    all_errors: list[str] = []
    all_warnings: list[str] = []
    for split in ("train", "val"):
        split_path = root / values[split]
        result = validate_split(root, split_path, names, allow_missing)
        splits[split] = result
        all_errors.extend(f"{split}: {item}" for item in result["errors"])
        all_warnings.extend(f"{split}: {item}" for item in result["warnings"])
    return {
        "dataset": str(dataset_yaml),
        "root": str(root),
        "names": names,
        "splits": splits,
        "errors": all_errors,
        "warnings": all_warnings,
        "ok": not all_errors,
    }


def main() -> int:
    args = parse_args()
    try:
        dataset_yaml = Path(args.dataset).expanduser().resolve()
        result = validate(dataset_yaml, args.allow_missing_labels)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.report:
        report_path = Path(args.report).expanduser()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({"ok": result["ok"], "names": result["names"], "splits": result["splits"]}, ensure_ascii=False, indent=2))
    for warning in result["warnings"]:
        print(f"warning: {warning}", file=sys.stderr)
    for error in result["errors"]:
        print(f"error: {error}", file=sys.stderr)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
