#!/usr/bin/env python3
"""Local person detection using official YOLOX-Tiny ONNX and JetPack TensorRT.

No torch/PyCUDA/network calls. Preprocessing, grid decode and NMS come from
Megvii's Apache-2.0 YOLOX implementation stored next to the model. Engines
are compiled locally and reused. This detects people; it does not identify them.
"""
import argparse
import ctypes
import fcntl
import importlib.util
import json
from pathlib import Path
import threading
import time

import cv2
import numpy as np

DEFAULT_MODEL = "/home/unitree/go2_ros2_ws/models/person_search/yolox_tiny.onnx"


def _load_upstream(directory):
    path = directory / "yolox_postprocess.py"
    spec = importlib.util.spec_from_file_location("_go2_yolox_postprocess", str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError("YOLOX preprocessing is missing: %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_engine(model_path, trt, logger, disable_jit=False):
    lock_path = model_path.with_suffix(".build.lock")
    if not lock_path.exists():
        from go2_platform_write import backup
        backup(lock_path)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return _build_engine_locked(model_path, trt, logger, disable_jit=disable_jit)


def _build_engine_locked(model_path, trt, logger, disable_jit=False):
    engine_path = model_path.with_suffix(".trt%s.fp16.engine" % trt.__version__)
    if engine_path.is_file() and engine_path.stat().st_mtime >= model_path.stat().st_mtime:
        return engine_path
    with trt.Builder(logger) as builder, builder.create_network(1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH)) as network, trt.OnnxParser(network, logger) as parser:
        if not parser.parse(model_path.read_bytes()):
            errors = [str(parser.get_error(index)) for index in range(parser.num_errors)]
            raise RuntimeError("Cannot parse official YOLOX ONNX: %s" % "; ".join(errors))
        config = builder.create_builder_config()
        config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 256 * 1024 * 1024)
        config.avg_timing_iterations = 1
        if disable_jit:
            # Original YOLOv5 on this NX otherwise spends many minutes compiling
            # source-fused convolution tactics. Keep the installed library tactics.
            config.set_tactic_sources(config.get_tactic_sources() &
                                     ~(1 << int(trt.TacticSource.JIT_CONVOLUTIONS)))
        if builder.platform_has_fast_fp16:
            config.set_flag(trt.BuilderFlag.FP16)
        serialized = builder.build_serialized_network(network, config)
        if serialized is None:
            raise RuntimeError("TensorRT engine compilation failed")
        from go2_platform_write import backup
        backup(engine_path)
        temporary = engine_path.with_name(engine_path.name + ".tmp")
        backup(temporary)
        temporary.write_bytes(bytes(serialized))
        temporary.replace(engine_path)
    return engine_path


class _CudaRuntime:
    """Small ctypes binding to the installed CUDA runtime, without PyCUDA."""

    def __init__(self):
        self.lib = ctypes.CDLL("/usr/local/cuda/lib64/libcudart.so.11.0")
        self.lib.cudaGetErrorString.argtypes = [ctypes.c_int]
        self.lib.cudaGetErrorString.restype = ctypes.c_char_p
        self.lib.cudaSetDevice.argtypes = [ctypes.c_int]
        self.lib.cudaMalloc.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t]
        self.lib.cudaFree.argtypes = [ctypes.c_void_p]
        self.lib.cudaMemcpy.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
        self._check(self.lib.cudaSetDevice(0))

    def _check(self, code):
        if code:
            message = self.lib.cudaGetErrorString(code)
            raise RuntimeError("CUDA: %s" % (message.decode("utf-8") if message else code))

    def allocate(self, size):
        pointer = ctypes.c_void_p()
        self._check(self.lib.cudaMalloc(ctypes.byref(pointer), size))
        return pointer

    def copy_to_device(self, pointer, array):
        self._check(self.lib.cudaMemcpy(pointer, ctypes.c_void_p(array.ctypes.data), array.nbytes, 1))

    def copy_from_device(self, array, pointer):
        self._check(self.lib.cudaMemcpy(ctypes.c_void_p(array.ctypes.data), pointer, array.nbytes, 2))


class Detector:
    """Thread-safe TensorRT decoder. Returns only COCO person-class boxes."""

    def __init__(self, model_path=None, confidence=0.35, nms_threshold=0.45):
        import tensorrt as trt
        self.confidence = float(confidence)
        self.nms_threshold = float(nms_threshold)
        if not 0 < self.confidence <= 1 or not 0 < self.nms_threshold <= 1:
            raise ValueError("Confidence and NMS thresholds must lie in (0, 1]")
        path = Path(model_path or DEFAULT_MODEL)
        if not path.is_file():
            raise RuntimeError("Person detector model is missing: %s" % path)
        self.model_path = str(path)
        self._upstream = _load_upstream(path.parent)
        self._lock = threading.Lock()
        self._logger = trt.Logger(trt.Logger.WARNING)
        self.engine_path = str(_build_engine(path, trt, self._logger) if path.suffix == ".onnx" else path)
        self._cuda = _CudaRuntime()
        self._runtime = trt.Runtime(self._logger)
        self._engine = self._runtime.deserialize_cuda_engine(Path(self.engine_path).read_bytes())
        if self._engine is None:
            raise RuntimeError("TensorRT could not load local person detector engine")
        self._context = self._engine.create_execution_context()
        if self._context is None:
            raise RuntimeError("TensorRT could not create execution context")
        self._buffers = []
        self._bindings = []
        self._input_index = self._output_index = None
        try:
            for index in range(self._engine.num_bindings):
                shape = tuple(self._engine.get_binding_shape(index))
                if any(value <= 0 for value in shape):
                    raise RuntimeError("Only fixed-shape YOLOX engines are supported")
                host = np.empty(shape, dtype=trt.nptype(self._engine.get_binding_dtype(index)))
                device = self._cuda.allocate(host.nbytes)
                self._buffers.append((host, device))
                self._bindings.append(device.value)
                if self._engine.binding_is_input(index):
                    if self._input_index is not None:
                        raise RuntimeError("YOLOX engine must have exactly one input")
                    self._input_index = index
                else:
                    if self._output_index is not None:
                        raise RuntimeError("YOLOX engine must have exactly one output")
                    self._output_index = index
            if self._input_index is None or self._output_index is None:
                raise RuntimeError("YOLOX engine input/output is missing")
            shape = self._buffers[self._input_index][0].shape
            if len(shape) != 4 or shape[:2] != (1, 3):
                raise RuntimeError("YOLOX input must have shape [1, 3, height, width]")
            self.input_shape = shape[2:]
            self.backend = "tensorrt-fp16"
        except Exception:
            self.close()
            raise

    def detect(self, image_bgr):
        if not isinstance(image_bgr, np.ndarray) or image_bgr.dtype != np.uint8 or image_bgr.ndim != 3 or image_bgr.shape[2] != 3 or min(image_bgr.shape[:2]) <= 0:
            raise ValueError("image_bgr must be a nonempty uint8 BGR image")
        with self._lock:
            if not self._buffers:
                raise RuntimeError("Person detector is closed")
            self._cuda._check(self._cuda.lib.cudaSetDevice(0))
            image, ratio = self._upstream.preproc(image_bgr, self.input_shape)
            host, device = self._buffers[self._input_index]
            np.copyto(host, image[None])
            self._cuda.copy_to_device(device, host)
            if not self._context.execute_v2(self._bindings):
                raise RuntimeError("TensorRT person inference failed")
            output, pointer = self._buffers[self._output_index]
            self._cuda.copy_from_device(output, pointer)
            predictions = self._upstream.demo_postprocess(output.copy(), self.input_shape)[0]
            if predictions.ndim != 2 or predictions.shape[1] != 85:
                raise RuntimeError("Unexpected official YOLOX output shape")
            scores = predictions[:, 4] * predictions[:, 5]
            mask = (scores >= self.confidence) & (predictions[:, 5:].argmax(axis=1) == 0)
            if not mask.any():
                return []
            centers = predictions[mask, :4]
            scores = scores[mask]
            boxes = np.column_stack((centers[:, 0] - centers[:, 2] / 2,
                                     centers[:, 1] - centers[:, 3] / 2,
                                     centers[:, 0] + centers[:, 2] / 2,
                                     centers[:, 1] + centers[:, 3] / 2)) / ratio
            height, width = image_bgr.shape[:2]
            boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, width)
            boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, height)
            keep = self._upstream.nms(boxes, scores, self.nms_threshold)
            return [{"bbox": [int(round(float(value))) for value in boxes[index]],
                     "confidence": round(float(scores[index]), 5)} for index in keep
                    if boxes[index, 2] > boxes[index, 0] and boxes[index, 3] > boxes[index, 1]]

    def close(self):
        with self._lock:
            for _, pointer in self._buffers:
                self._cuda.lib.cudaFree(pointer)
            self._buffers = []

    def __del__(self):
        if hasattr(self, "_buffers"):
            try:
                self.close()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--model", default=None)
    parser.add_argument("--confidence", type=float, default=0.35)
    args = parser.parse_args()
    try:
        detector = Detector(args.model, args.confidence)
        image = cv2.imread(args.image)
        start = time.monotonic()
        people = detector.detect(image)
        print(json.dumps({"people": people, "count": len(people), "backend": detector.backend,
                          "elapsed_seconds": round(time.monotonic() - start, 4),
                          "engine": detector.engine_path}, ensure_ascii=False))
        detector.close()
    except (RuntimeError, ValueError, OSError, ImportError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
