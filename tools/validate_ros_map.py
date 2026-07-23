#!/usr/bin/env python3
"""Validate a ROS occupancy-grid map YAML and its PGM image header."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path


REQUIRED_KEYS = ("image", "resolution", "origin", "negate", "occupied_thresh", "free_thresh")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", dest="map_yaml", required=True, help="ROS map YAML path")
    parser.add_argument("--report", default="", help="optional JSON report path")
    return parser.parse_args()


def parse_map_yaml(path: Path) -> dict[str, object]:
    values: dict[str, object] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip().strip("'\"")
        if key == "origin":
            try:
                values[key] = ast.literal_eval(raw_value)
            except (ValueError, SyntaxError):
                values[key] = raw_value
        elif re.fullmatch(r"[-+]?\d+", raw_value):
            values[key] = int(raw_value)
        else:
            try:
                values[key] = float(raw_value)
            except ValueError:
                values[key] = raw_value
    return values


def pgm_header(path: Path) -> dict[str, object]:
    tokens: list[bytes] = []
    with path.open("rb") as handle:
        while len(tokens) < 4:
            line = handle.readline()
            if not line:
                break
            if line.startswith(b"#"):
                continue
            tokens.extend(line.split())
    if len(tokens) < 4 or tokens[0] not in {b"P2", b"P5"}:
        raise ValueError("image is not a readable P2/P5 PGM")
    try:
        width, height, max_value = (int(item) for item in tokens[1:4])
    except ValueError as exc:
        raise ValueError("PGM width, height or max value is invalid") from exc
    if width <= 0 or height <= 0 or not 1 <= max_value <= 65535:
        raise ValueError("PGM dimensions or max value is invalid")
    return {"format": tokens[0].decode("ascii"), "width": width, "height": height, "max_value": max_value}


def validate_map(map_yaml: Path) -> dict[str, object]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        values = parse_map_yaml(map_yaml)
    except OSError as exc:
        return {"map": str(map_yaml), "ok": False, "errors": [str(exc)], "warnings": []}

    missing = [key for key in REQUIRED_KEYS if key not in values]
    errors.extend(f"missing YAML key: {key}" for key in missing)
    image_path: Path | None = None
    image_info: dict[str, object] = {}
    if "image" in values and isinstance(values["image"], str):
        image_path = (map_yaml.parent / values["image"]).resolve()
        if not image_path.is_file():
            errors.append(f"map image not found: {image_path}")
        else:
            try:
                image_info = pgm_header(image_path)
            except (OSError, ValueError) as exc:
                errors.append(f"invalid map image: {exc}")

    resolution = values.get("resolution")
    if not isinstance(resolution, (int, float)) or resolution <= 0:
        errors.append("resolution must be a positive number")
    origin = values.get("origin")
    if not isinstance(origin, (list, tuple)) or len(origin) != 3:
        errors.append("origin must contain exactly three numbers")
    else:
        try:
            if not all(isinstance(item, (int, float)) for item in origin):
                raise ValueError
        except ValueError:
            errors.append("origin must contain numeric values")

    for key in ("occupied_thresh", "free_thresh"):
        value = values.get(key)
        if not isinstance(value, (int, float)) or not 0 <= value <= 1:
            errors.append(f"{key} must be between 0 and 1")
    if all(isinstance(values.get(key), (int, float)) for key in ("occupied_thresh", "free_thresh")):
        if values["free_thresh"] >= values["occupied_thresh"]:
            warnings.append("free_thresh is not lower than occupied_thresh")

    negate = values.get("negate")
    if not isinstance(negate, int) or negate not in (0, 1):
        errors.append("negate must be 0 or 1")

    return {
        "map": str(map_yaml),
        "image": str(image_path) if image_path else "",
        "image_info": image_info,
        "values": values,
        "errors": errors,
        "warnings": warnings,
        "ok": not errors,
    }


def main() -> int:
    args = parse_args()
    map_yaml = Path(args.map_yaml).expanduser().resolve()
    try:
        result = validate_map(map_yaml)
        if args.report:
            report_path = Path(args.report).expanduser()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
