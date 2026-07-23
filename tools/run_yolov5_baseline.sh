#!/usr/bin/env bash
set -euo pipefail

# Train only after the local dataset validator and local-weight checks pass.
# This script never downloads weights implicitly.

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DATASET_YAML=""
WEIGHTS_PATH=""
IMG_SIZE=640
BATCH_SIZE=4
EPOCHS=100
RUN_NAME="yolov5s_baseline"
CACHE_MODE=""

usage() {
  cat <<'EOF'
Usage:
  tools/run_yolov5_baseline.sh --dataset DATASET_YAML --weights WEIGHTS [options]

Options:
  --dataset PATH   YOLO dataset.yaml; validation runs before training
  --weights PATH   local .pt weights; no implicit download
  --img N          input image size (default: 640)
  --batch N        batch size (default: 4)
  --epochs N       epochs (default: 100)
  --name NAME      run name (default: yolov5s_baseline)
  --cache MODE     optional YOLOv5 cache mode, for example ram
  -h, --help       show this help
EOF
}

while (($#)); do
  case "$1" in
    --dataset)
      [[ $# -ge 2 ]] || { echo "missing value for --dataset" >&2; exit 2; }
      DATASET_YAML="$2"
      shift 2
      ;;
    --weights)
      [[ $# -ge 2 ]] || { echo "missing value for --weights" >&2; exit 2; }
      WEIGHTS_PATH="$2"
      shift 2
      ;;
    --img)
      [[ $# -ge 2 ]] || { echo "missing value for --img" >&2; exit 2; }
      IMG_SIZE="$2"
      shift 2
      ;;
    --batch)
      [[ $# -ge 2 ]] || { echo "missing value for --batch" >&2; exit 2; }
      BATCH_SIZE="$2"
      shift 2
      ;;
    --epochs)
      [[ $# -ge 2 ]] || { echo "missing value for --epochs" >&2; exit 2; }
      EPOCHS="$2"
      shift 2
      ;;
    --name)
      [[ $# -ge 2 ]] || { echo "missing value for --name" >&2; exit 2; }
      RUN_NAME="$2"
      shift 2
      ;;
    --cache)
      [[ $# -ge 2 ]] || { echo "missing value for --cache" >&2; exit 2; }
      CACHE_MODE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ -n "$DATASET_YAML" ]] || { echo "--dataset is required" >&2; usage >&2; exit 2; }
[[ -n "$WEIGHTS_PATH" ]] || { echo "--weights is required; refusing implicit downloads" >&2; exit 2; }
[[ -f "$DATASET_YAML" ]] || { echo "dataset YAML not found: $DATASET_YAML" >&2; exit 2; }
[[ -f "$WEIGHTS_PATH" ]] || { echo "local weights not found: $WEIGHTS_PATH" >&2; exit 2; }

VALIDATION_REPORT="${DATASET_YAML%.yaml}.validation.json"
"$PYTHON_BIN" "$REPO_ROOT/tools/validate_yolo_dataset.py" \
  --dataset "$DATASET_YAML" \
  --report "$VALIDATION_REPORT"

YOLO_TRAIN="$REPO_ROOT/apps/nano-deploy/yolov5/train.py"
[[ -f "$YOLO_TRAIN" ]] || { echo "YOLOv5 train.py not found: $YOLO_TRAIN" >&2; exit 2; }

TRAIN_ARGS=(
  "$YOLO_TRAIN"
  --img "$IMG_SIZE"
  --batch "$BATCH_SIZE"
  --epochs "$EPOCHS"
  --data "$DATASET_YAML"
  --weights "$WEIGHTS_PATH"
  --project "$REPO_ROOT/datasets/local/roadside_v1/runs"
  --name "$RUN_NAME"
)
if [[ -n "$CACHE_MODE" ]]; then
  TRAIN_ARGS+=(--cache "$CACHE_MODE")
fi

echo "dataset validation: passed"
echo "starting local-weight YOLOv5 baseline: $RUN_NAME"
exec "$PYTHON_BIN" "${TRAIN_ARGS[@]}"
