#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import logging
import time
from contextlib import contextmanager
from pathlib import Path

import cv2
import torch

from detect_visual import YOLOPredict

try:
    from vision.event_rules import Detection, IllegalParkingRule, IoUTracker, NoParkingZone
except ImportError:
    from event_rules import Detection, IllegalParkingRule, IoUTracker, NoParkingZone

try:
    from utils.dataloaders import LoadStreams, LoadImages
except ImportError:
    from utils.datasets import LoadStreams, LoadImages

try:
    from utils.general import non_max_suppression, scale_coords
except ImportError:
    from utils.general import non_max_suppression
    try:
        from utils.general import scale_coords
    except ImportError:
        from utils.general import scale_boxes as scale_coords

try:
    from utils.plots import plot_one_box
except Exception:
    def plot_one_box(x, img, color=None, label=None, line_thickness=2):
        tl = line_thickness or 2
        c1, c2 = (int(x[0]), int(x[1])), (int(x[2]), int(x[3]))
        color = color or (0, 255, 0)
        cv2.rectangle(img, c1, c2, color, thickness=tl, lineType=cv2.LINE_AA)
        if label:
            tf = max(tl - 1, 1)
            # Use a slightly larger font scale for better visibility on Nano
            fs = tl / 2.5
            t_size = cv2.getTextSize(label, 0, fontScale=fs, thickness=tf)[0]
            # Draw label background
            c2 = c1[0] + t_size[0], c1[1] - t_size[1] - 5
            cv2.rectangle(img, c1, c2, color, -1, cv2.LINE_AA)
            # Draw text shadow (black) for better contrast
            cv2.putText(img, label, (c1[0] + 1, c1[1] - 3), 0, fs, (0, 0, 0), thickness=tf + 1, lineType=cv2.LINE_AA)
            # Draw main text (white)
            cv2.putText(img, label, (c1[0], c1[1] - 4), 0, fs, (255, 255, 255), thickness=tf, lineType=cv2.LINE_AA)

try:
    import requests
except Exception:
    requests = None


def parse_args():
    parser = argparse.ArgumentParser(description="RTSP inference with frame skipping on Jetson Nano")
    parser.add_argument("--weights", default="helmet.pt", help="model weights path")
    parser.add_argument("--source", default="", help="verified video URL, camera index, or video path")
    parser.add_argument("--vid-stride", type=int, default=3, help="process every Nth frame")
    parser.add_argument("--max-frames", type=int, default=0, help="stop after this many processed frames; 0 means no frame limit")
    parser.add_argument("--duration", type=float, default=0.0, help="stop after this many seconds; 0 means no time limit")
    parser.add_argument("--img-size", type=int, default=640, help="inference image size")
    parser.add_argument("--conf", type=float, default=0.4, help="confidence threshold")
    parser.add_argument("--iou", type=float, default=0.5, help="IoU threshold")
    parser.add_argument("--device", default="", help="cuda:0, cpu, etc.")
    parser.add_argument("--half", action="store_true", help="use FP16 on CUDA")
    parser.add_argument("--view", action="store_true", help="show window")
    parser.add_argument("--save-dir", default="events", help="event frame output dir")
    parser.add_argument("--event-classes", default="", help="comma-separated class names; empty means all")
    parser.add_argument("--min-event-interval", type=float, default=2.0, help="seconds per class")
    parser.add_argument("--annotate", action="store_true", help="draw boxes on saved frames")
    parser.add_argument("--upload-url", default="", help="HTTP endpoint for event image upload")
    parser.add_argument("--upload-timeout", type=float, default=5.0, help="upload timeout seconds")
    parser.add_argument("--upload-header", action="append", default=[], help="extra header, format Key:Value")
    parser.add_argument("--snapshot-dir", default="", help="write latest annotated frame here (for web)")
    parser.add_argument("--snapshot-interval", type=float, default=2.0, help="seconds between snapshot updates")
    parser.add_argument("--snapshot-max-size", type=int, default=0, help="max width for snapshot (0 = no resize)")
    parser.add_argument("--upload-snapshot", action="store_true", help="upload snapshot frames to upload-url")
    parser.add_argument(
        "--parking-zone",
        type=float,
        nargs=4,
        default=None,
        metavar=("X1", "Y1", "X2", "Y2"),
        help="normalized no-parking ROI; enables vehicle dwell-time rules",
    )
    parser.add_argument("--parking-dwell", type=float, default=5.0, help="seconds a vehicle must dwell in ROI")
    parser.add_argument("--parking-cooldown", type=float, default=30.0, help="seconds between repeated events")
    parser.add_argument("--parking-min-conf", type=float, default=0.5, help="minimum vehicle confidence")
    parser.add_argument("--parking-track-iou", type=float, default=0.3, help="IoU threshold for vehicle tracking")
    return parser.parse_args()


def normalize_event_classes(value):
    value = value.strip()
    if not value:
        return set()
    return {v.strip() for v in value.split(",") if v.strip()}


def filter_events(det, names, conf_thres, event_classes):
    events = []
    if det is None or len(det) == 0:
        return events
    for *xyxy, conf, cls in det:
        conf = float(conf)
        name = names[int(cls)]
        if conf < conf_thres:
            continue
        if event_classes and name not in event_classes:
            continue
        events.append((name, conf, [int(x) for x in xyxy]))
    return events


def apply_cooldown(events, min_interval, last_emit):
    if min_interval <= 0:
        return events
    now = time.time()
    output = []
    for name, conf, xyxy in events:
        last = last_emit.get(name, 0.0)
        if now - last >= min_interval:
            last_emit[name] = now
            output.append((name, conf, xyxy))
    return output


def save_event_frame(im0, events, save_dir, annotate):
    save_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    label = "_".join([e[0] for e in events])[:50] or "event"
    filename = f"{ts}_{label}.jpg"
    out_path = save_dir / filename

    frame = im0.copy()
    if annotate:
        for name, conf, xyxy in events:
            plot_one_box(xyxy, frame, label=f"{name} {conf:.2f}")

    cv2.imwrite(str(out_path), frame)

    meta = {
        "timestamp": ts,
        "events": [{"label": n, "conf": c, "bbox": b} for n, c, b in events],
    }
    json_path = out_path.with_suffix(".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=True, indent=2)

    return out_path, meta


def parse_headers(pairs):
    headers = {}
    for item in pairs:
        if ":" not in item:
            continue
        k, v = item.split(":", 1)
        headers[k.strip()] = v.strip()
    return headers


def upload_event_image(path, meta, url, timeout, headers):
    if not url:
        return
    if requests is None:
        logging.warning("requests not installed; skip upload")
        return
    with open(path, "rb") as f:
        files = {"image": f}
        data = {"meta": json.dumps(meta, ensure_ascii=True)}
        try:
            resp = requests.post(url, files=files, data=data, headers=headers, timeout=timeout)
            logging.info("upload status=%s, resp=%s", resp.status_code, resp.text[:200])
        except Exception as exc:
            logging.warning("upload failed: %s", exc)

def detections_to_meta(det, names):
    items = []
    if det is None or len(det) == 0:
        return items
    for *xyxy, conf, cls in det:
        items.append({
            "label": names[int(cls)],
            "conf": float(conf),
            "bbox": [int(x) for x in xyxy],
        })
    return items

def filter_detect_items(items, event_classes):
    if not event_classes:
        return items
    return [it for it in items if it.get("label") in event_classes]

def maybe_resize(im, max_w):
    if not max_w or im is None:
        return im
    h, w = im.shape[:2]
    if w <= max_w:
        return im
    scale = max_w / float(w)
    new_h = max(1, int(h * scale))
    return cv2.resize(im, (max_w, new_h), interpolation=cv2.INTER_AREA)


def write_atomic_image(path, frame):
    suffix = path.suffix.lower() or ".jpg"
    ext = suffix if suffix in {".jpg", ".jpeg", ".png"} else ".jpg"
    ok, encoded = cv2.imencode(ext, frame)
    if not ok:
        raise RuntimeError(f"failed to encode snapshot for {path}")
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_bytes(encoded.tobytes())
    tmp_path.replace(path)


def write_atomic_json(path, payload):
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True, indent=2)
    tmp_path.replace(path)


@contextmanager
def close_dataset_after_run(dataset):
    """Always release stream readers, including when inference raises."""
    try:
        yield
    finally:
        close_dataset = getattr(dataset, "close", None)
        if close_dataset is not None:
            close_dataset()


def main():
    args = parse_args()
    if args.max_frames < 0:
        raise SystemExit("--max-frames must be zero or greater")
    if args.duration < 0:
        raise SystemExit("--duration must be zero or greater")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    event_classes = normalize_event_classes(args.event_classes)
    headers = parse_headers(args.upload_header)
    save_dir = Path(args.save_dir)

    parking_tracker = None
    parking_rule = None
    if args.parking_zone is not None:
        zone = NoParkingZone("default_no_parking", *args.parking_zone)
        parking_tracker = IoUTracker(iou_threshold=args.parking_track_iou)
        parking_rule = IllegalParkingRule(
            zone,
            dwell_seconds=args.parking_dwell,
            min_confidence=args.parking_min_conf,
            cooldown_seconds=args.parking_cooldown,
        )

    predictor = YOLOPredict(
        args.weights,
        "inference/output",
        conf_thres=args.conf,
        iou_thres=args.iou,
        img_size=args.img_size,
        device=args.device,
        half=args.half,
        vid_stride=args.vid_stride,
    )
    predictor.view_img = args.view

    snapshot_dir = Path(args.snapshot_dir) if args.snapshot_dir else None
    if snapshot_dir:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
    last_snapshot = 0.0

    webcam = args.source == "0" or args.source.startswith(("rtsp", "http", "https"))
    if webcam:
        dataset = LoadStreams(args.source, img_size=predictor.img_size, vid_stride=args.vid_stride)
    else:
        dataset = LoadImages(args.source, img_size=predictor.img_size, vid_stride=args.vid_stride)

    last_emit = {}
    processed_frames = 0
    started_at = time.monotonic()
    with torch.no_grad(), close_dataset_after_run(dataset):
        for item in dataset:
            if args.max_frames and processed_frames >= args.max_frames:
                break
            if args.duration and time.monotonic() - started_at >= args.duration:
                break
            processed_frames += 1
            if len(item) == 5:
                path, img, im0s, vid_cap, info_str = item
            else:
                path, img, im0s, vid_cap = item[:4]

            if webcam:
                im0 = im0s[0].copy() if isinstance(im0s, list) else im0s.copy()
            else:
                im0 = im0s.copy()

            img = torch.from_numpy(img).to(predictor.device)
            if webcam and img.ndimension() == 4:
                img = img[0]
            img = img.half() if predictor.half and predictor.device.type != "cpu" else img.float()
            img /= 255.0
            if img.ndimension() == 3:
                img = img.unsqueeze(0)

            pred = predictor.model(img, augment=False)[0]
            pred = non_max_suppression(pred, predictor.conf_thres, predictor.iou_thres, classes=predictor.classes,
                                       agnostic=predictor.agnostic_nms)
            det = pred[0] if pred else None

            if det is not None and len(det):
                det[:, :4] = scale_coords(img.shape[2:], det[:, :4], im0.shape).round()

            events = filter_events(det, predictor.names, predictor.conf_thres, event_classes)
            events = apply_cooldown(events, args.min_event_interval, last_emit)

            parking_events = []
            if parking_tracker is not None and parking_rule is not None:
                parking_detections = []
                if det is not None and len(det):
                    for *xyxy, conf, cls in det:
                        parking_detections.append(
                            Detection(
                                predictor.names[int(cls)],
                                float(conf),
                                tuple(float(value) for value in xyxy),
                            )
                        )
                frame_timestamp = time.time()
                tracked = parking_tracker.update(frame_timestamp, parking_detections)
                parking_events = parking_rule.update(frame_timestamp, tracked, (im0.shape[1], im0.shape[0]))
                events.extend(
                    (event.event_type, event.confidence, [int(value) for value in event.bbox])
                    for event in parking_events
                )

            if events:
                out_path, meta = save_event_frame(im0, events, save_dir, args.annotate)
                upload_event_image(out_path, meta, args.upload_url, args.upload_timeout, headers)
                # Print a special marker for the Agent to potentially broadcast
                print(f"EVENT_SAVED: {out_path.name}")
                if parking_events:
                    print(f"EVENT_JSON: {json.dumps([event.as_dict() for event in parking_events])}")

            show_frame = im0.copy()
            det_summary = []
            if det is not None and len(det):
                for *xyxy, conf, cls in reversed(det):
                    name = predictor.names[int(cls)]
                    label = f"{name} {conf:.2f}"
                    plot_one_box(xyxy, show_frame, label=label)
                    det_summary.append({"label": name, "conf": float(conf)})
            
            if det_summary:
                # Print structured JSON for real-time monitoring without spamming too much
                # (Still filtered by vid-stride at the dataset level)
                print(f"DETECT_JSON: {json.dumps(det_summary)}")

            should_snapshot = snapshot_dir and (
                args.snapshot_interval <= 0 or (time.time() - last_snapshot >= args.snapshot_interval)
            )
            if should_snapshot:
                frame = maybe_resize(show_frame, args.snapshot_max_size)
                snapshot_path = snapshot_dir / "latest.jpg"
                write_atomic_image(snapshot_path, frame)
                write_atomic_json(
                    snapshot_dir / "latest.json",
                    {
                        "timestamp": dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
                        "detections": detections_to_meta(det, predictor.names),
                        "source": args.source,
                    },
                )
                if args.upload_snapshot and args.upload_url:
                    det_items = detections_to_meta(det, predictor.names)
                    det_items = filter_detect_items(det_items, event_classes)
                    if det_items:
                        meta = {
                            "timestamp": dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
                            "events": det_items,
                            "snapshot": True,
                        }
                        upload_event_image(snapshot_path, meta, args.upload_url, args.upload_timeout, headers)
                last_snapshot = time.time()

            if predictor.view_img:
                cv2.imshow("Nano Detection (Press 'q' to exit)", show_frame)
                if cv2.waitKey(1) == ord("q"):
                    break

    if predictor.view_img:
        cv2.destroyAllWindows()

    elapsed = max(time.monotonic() - started_at, 1e-6)
    logging.info("inference complete frames=%d elapsed=%.2fs fps=%.2f", processed_frames, elapsed, processed_frames / elapsed)


if __name__ == "__main__":
    main()
