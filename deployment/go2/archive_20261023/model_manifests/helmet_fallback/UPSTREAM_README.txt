---
license: agpl-3.0
library_name: ultralytics
pipeline_tag: object-detection
language:
  - en
tags:
  - yolo26
  - yolo11
  - ultralytics
  - pytorch
  - onnx
  - tensorrt
  - int8
  - fp16
  - construction-safety
  - hazard-detection
datasets:
  - custom
---

# Construction-Hazard-Detection

YOLO26 construction-site safety detection models, with legacy YOLO11
checkpoints. The repository includes portable PyTorch and ONNX weights, plus
TensorRT INT8 artifacts evaluated on the original, unaugmented validation
split.

The full application, training utilities, and evaluation tooling live in the
[main project](https://github.com/yihong1120/Construction-Hazard-Detection).

## Project and data links

- [GitHub: full Construction-Hazard-Detection system](https://github.com/yihong1120/Construction-Hazard-Detection)
- [Roboflow: Construction Hazard Detection dataset reference](https://app.roboflow.com/object-detection-qn97p/construction-hazard-detection)

The GitHub repository contains the production services, training code, and
deployment configuration. The Roboflow project is the dataset reference for
this work; the validation images and labels used for the published metrics are
not redistributed in this model repository.

![Detection example](./data/examples/demo.jpg)

## Recommended artifacts

- Use models/yolo26/pt/*.pt for the most portable Ultralytics workflow.
- Use models/yolo26/onnx/*.onnx for ONNX Runtime or custom deployment.
- Use models/yolo26/tensorrt/mixed/*.engine on the documented RTX 4090
  environment when accuracy and TensorRT throughput both matter.
- Treat models/yolo26/tensorrt/int8/*.engine as a lower-accuracy option.
  It is not recommended for the m or x variants on this validation set.

## Labels

    0: Hardhat
    1: Mask
    2: NO-Hardhat
    3: NO-Mask
    4: NO-Safety Vest
    5: Person
    6: Safety Cone
    7: Safety Vest
    8: machinery
    9: utility pole
    10: vehicle

## Validation results

All results use 6,664 original validation images (61,458 instances), at
imgsz=640, batch size 16. A duplicate label in one source annotation was
removed automatically by Ultralytics during validation. The table reports
box mAP50-95; the full precision, recall, mAP50, timing, checksum, and file
size records are in
[evaluation/yolo26_precision.csv](./evaluation/yolo26_precision.csv).

| Model | Parameters | FP32 | FP16 | Mixed INT8/FP16 | Pure INT8 |
|---|---:|---:|---:|---:|---:|
| YOLO26n | 2,508,090 | 0.4765 | 0.4764 | 0.4689 | 0.4368 |
| YOLO26s | 9,956,378 | 0.5996 | 0.5994 | 0.5956 | 0.5742 |
| YOLO26m | 21,789,850 | 0.5891 | 0.5889 | 0.5882 | 0.4979 |
| YOLO26l | 26,193,306 | 0.6482 | 0.6480 | 0.6361 | 0.6012 |
| YOLO26x | 58,833,978 | 0.6673 | 0.6670 | 0.6642 | 0.5560 |

FP16 is evaluated from the same PyTorch checkpoint as FP32. "Mixed" denotes
a TensorRT INT8 engine whose YOLO Detect head stays FP16. "Pure INT8" quantizes
the full graph. These are validation results, not an independent held-out test:
the TensorRT calibration set is a 512-image, class-balanced sample from the
same original validation split. See [evaluation/README.md](./evaluation/README.md)
for the exact methodology and limitations.

## Inference efficiency

The table below is a dedicated model-forward microbenchmark, measured with
synchronized CUDA events after 50 warmup iterations and over 200 timed
iterations. Each cell is batch 1 latency in milliseconds / batch 16 model-only
throughput in images per second. Lower latency and higher throughput are
better.

| Model | FP32 | FP16 | Mixed INT8/FP16 | Pure INT8 |
|---|---:|---:|---:|---:|
| YOLO26n | 2.16 ms / 2,325 img/s | 2.36 ms / 3,623 img/s | 1.02 ms / 7,303 img/s | 0.98 ms / 7,402 img/s |
| YOLO26s | 2.28 ms / 964 img/s | 2.38 ms / 1,727 img/s | 1.32 ms / 4,706 img/s | 1.22 ms / 5,240 img/s |
| YOLO26m | 3.26 ms / 385 img/s | 2.56 ms / 739 img/s | 1.80 ms / 2,223 img/s | 1.68 ms / 2,620 img/s |
| YOLO26l | 4.32 ms / 312 img/s | 3.64 ms / 598 img/s | 2.52 ms / 1,753 img/s | 2.39 ms / 2,002 img/s |
| YOLO26x | 8.05 ms / 158 img/s | 4.86 ms / 315 img/s | 2.86 ms / 1,019 img/s | 2.54 ms / 1,189 img/s |

At batch 1, FP16 can be marginally slower for the smallest models because
launch and datatype-conversion costs outweigh tensor-core gains. At batch 16,
FP16 is consistently faster. These are model-forward timings, not camera-stream
FPS: they exclude RTSP decode, frame acquisition, queueing, tracking,
annotation, encoding, and browser playback. The raw benchmark, environment,
and reproducible implementation are available in
[evaluation/yolo26_inference_benchmark.csv](./evaluation/yolo26_inference_benchmark.csv),
[evaluation/inference_benchmark_environment.json](./evaluation/inference_benchmark_environment.json),
and [scripts/benchmark_yolo26_inference.py](./scripts/benchmark_yolo26_inference.py).

## TensorRT compatibility

The published engines were built and tested on:

    OS: Linux x86_64
    GPU: NVIDIA GeForce RTX 4090 (compute capability 8.9)
    NVIDIA driver: 580.173.02
    CUDA: 13.0
    TensorRT: 11.1.0.106
    PyTorch: 2.13.0+cu130
    Ultralytics: 8.4.113
    Engine profile: dynamic batch, max batch 16, imgsz 640

TensorRT engines are hardware-, runtime-, and platform-dependent. Rebuild an
engine for another GPU or TensorRT environment rather than assuming it will
load. Only deserialize engine files from a trusted release. The reproducible
export implementation is available in the
[main project's export script](https://github.com/yihong1120/Construction-Hazard-Detection/blob/main/examples/YOLO_train/export_int8_trt.py).

## Quick start

    from huggingface_hub import hf_hub_download
    from ultralytics import YOLO

    checkpoint = hf_hub_download(
        repo_id="yihong1120/Construction-Hazard-Detection",
        filename="models/yolo26/pt/yolo26x.pt",
    )
    model = YOLO(checkpoint)
    results = model("data/examples/demo.jpg", imgsz=640, conf=0.25)

For the compatible TensorRT environment:

    from ultralytics import YOLO

    model = YOLO("models/yolo26/tensorrt/mixed/yolo26x.engine")
    results = model("data/examples/demo.jpg", imgsz=640, conf=0.25)

## Repository layout

    models/
      yolo26/
        pt/                 # FP32/FP16 runtime checkpoints
        onnx/               # Portable ONNX exports
        tensorrt/
          mixed/            # INT8 backbone/neck with FP16 Detect head
          int8/             # Full-graph INT8
      yolo11/               # Legacy PyTorch and ONNX checkpoints
    evaluation/
      yolo26_precision.csv  # Raw metrics, artifact hashes, and validation timing
      yolo26_inference_benchmark.csv  # CUDA-event latency and throughput
      environment.json      # Evaluation software and hardware environment
      inference_benchmark_environment.json
      README.md             # Methodology and reproducibility instructions
    scripts/
      evaluate_yolo26_precision.py
      benchmark_yolo26_inference.py

## Intended use and limitations

These models support research and prototyping for construction safety
monitoring. Detection quality depends on camera placement, lighting,
occlusion, PPE appearance, and site-specific conditions. Evaluate on the
target site before safety-critical use, and combine detections with tracking
and appropriate human review.

The original validation images and annotations are not redistributed in this
repository. Do not treat the published validation metrics as a replacement for
an independently licensed and held-out test set.

## License

This repository, its models, and the associated Ultralytics workflow are
released under [AGPL-3.0](./LICENSE). Review the license obligations before
using the models in a networked or commercial deployment.
