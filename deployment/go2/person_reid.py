#!/usr/bin/env python3
"""Real OSNet person embeddings using TensorRT and the installed CUDA runtime.

No torch, pycuda, or additional packages are imported. Input is a cropped
person in OpenCV BGR uint8 format. Output is a unit-length float32 descriptor.
The cached ONNX was published by Kornia AI, not the original OSNet author.
"""
import ctypes
import threading
from pathlib import Path

import cv2
import numpy as np
import tensorrt as trt

MODEL_DIR = Path('/home/unitree/go2_ros2_ws/models/person_search')
DEFAULT_MODEL = MODEL_DIR / 'osnet_x0_25_msmt17_fp16.engine'
DEFAULT_ONNX = MODEL_DIR / 'osnet_x0_25_msmt17.onnx'
MODEL_SHA256 = 'e78604f4ccda49b8f41cd0f8f7303800ce75d2361895ebb0729513c1bf53d277'


class Extractor:
    """GPU context and buffers are protected by an instance thread lock."""
    def __init__(self, model_path=None):
        path = Path(model_path) if model_path is not None else DEFAULT_MODEL
        if path.suffix == '.onnx':
            path = path.with_name(path.stem + '_fp16.engine')
        if not path.is_file():
            raise FileNotFoundError('OSNet TensorRT engine not found: ' + str(path))
        self.model_path = str(path)
        self.lock = threading.Lock()
        self._closed = False
        self._device_buffers = []
        self.cuda = ctypes.CDLL('/usr/local/cuda/lib64/libcudart.so')
        self.cuda.cudaSetDevice.argtypes = [ctypes.c_int]
        self.cuda.cudaMalloc.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t]
        self.cuda.cudaMemcpy.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
        self.cuda.cudaFree.argtypes = [ctypes.c_void_p]
        self.cuda.cudaGetErrorString.argtypes = [ctypes.c_int]
        self.cuda.cudaGetErrorString.restype = ctypes.c_char_p
        self._check(self.cuda.cudaSetDevice(0))
        self.logger = trt.Logger(trt.Logger.WARNING)
        self.runtime = trt.Runtime(self.logger)
        self.engine = self.runtime.deserialize_cuda_engine(path.read_bytes())
        if self.engine is None:
            raise RuntimeError('Could not deserialize OSNet engine for this GPU/TensorRT version')
        self.context = self.engine.create_execution_context()
        self.input_indices = [i for i in range(self.engine.num_bindings) if self.engine.binding_is_input(i)]
        self.output_indices = [i for i in range(self.engine.num_bindings) if not self.engine.binding_is_input(i)]
        if len(self.input_indices) != 1 or len(self.output_indices) != 1:
            raise ValueError('Expected one image input and one embedding output')
        self.input_index, self.output_index = self.input_indices[0], self.output_indices[0]
        input_shape = tuple(self.engine.get_binding_shape(self.input_index))
        if any(n <= 0 for n in input_shape):
            self.context.set_binding_shape(self.input_index, (1, 3, 256, 128))
            input_shape = tuple(self.context.get_binding_shape(self.input_index))
        output_shape = tuple(self.context.get_binding_shape(self.output_index))
        if len(input_shape) != 4 or input_shape[1:] != (3, 256, 128) or output_shape[-1] != 512:
            raise ValueError('Unexpected OSNet input/output shape: ' + str((input_shape, output_shape)))
        self.batch_size = input_shape[0]
        dtypes = {trt.float32: np.float32, trt.float16: np.float16}
        self.input = np.zeros(input_shape, dtype=dtypes[self.engine.get_binding_dtype(self.input_index)])
        self.output = np.empty(output_shape, dtype=dtypes[self.engine.get_binding_dtype(self.output_index)])
        self.bindings = [0] * self.engine.num_bindings
        for index, host in [(self.input_index, self.input), (self.output_index, self.output)]:
            pointer = ctypes.c_void_p()
            self._check(self.cuda.cudaMalloc(ctypes.byref(pointer), host.nbytes))
            self._device_buffers.append(pointer)
            self.bindings[index] = pointer.value

    def _check(self, code):
        if code:
            reason = self.cuda.cudaGetErrorString(code).decode('utf-8', 'replace')
            raise RuntimeError('CUDA error ' + str(code) + ': ' + reason)

    @staticmethod
    def _preprocess(image_bgr):
        if not isinstance(image_bgr, np.ndarray) or image_bgr.ndim != 3 or image_bgr.shape[2] != 3 or image_bgr.size == 0:
            raise ValueError('Expected a nonempty BGR image with three channels')
        if image_bgr.dtype != np.uint8:
            raise ValueError('Expected BGR uint8 pixels in [0, 255]')
        image = cv2.cvtColor(cv2.resize(image_bgr, (128, 256), interpolation=cv2.INTER_LINEAR), cv2.COLOR_BGR2RGB)
        image = image.astype(np.float32) / 255.0
        image = (image - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array([0.229, 0.224, 0.225], dtype=np.float32)
        return np.ascontiguousarray(image.transpose(2, 0, 1))

    def embed(self, image_bgr):
        """Return the L2-normalized 512-dimensional descriptor for one person."""
        return self.embed_many([image_bgr])[0]

    def embed_many(self, images_bgr):
        """Reuse the exported model's batch for efficient crowd processing."""
        images = list(images_bgr)
        if not images:
            return np.empty((0, 512), dtype=np.float32)
        descriptors = []
        with self.lock:
            if self._closed:
                raise RuntimeError('OSNet extractor has been closed')
            self._check(self.cuda.cudaSetDevice(0))
            for start in range(0, len(images), self.batch_size):
                chunk = images[start:start + self.batch_size]
                self.input.fill(0)
                for i, image in enumerate(chunk):
                    self.input[i] = self._preprocess(image)
                self._check(self.cuda.cudaMemcpy(self.bindings[self.input_index], self.input.ctypes.data, self.input.nbytes, 1))
                if not self.context.execute_v2(self.bindings):
                    raise RuntimeError('OSNet TensorRT inference failed')
                self._check(self.cuda.cudaMemcpy(self.output.ctypes.data, self.bindings[self.output_index], self.output.nbytes, 2))
                features = self.output[:len(chunk)].astype(np.float32, copy=True).reshape(len(chunk), -1)
                norms = np.linalg.norm(features, axis=1, keepdims=True)
                if not np.isfinite(features).all() or not np.isfinite(norms).all() or (norms < 1e-12).any():
                    raise RuntimeError('OSNet produced invalid or zero embeddings')
                descriptors.append(features / norms)
        return np.concatenate(descriptors, axis=0)

    def close(self):
        with self.lock:
            if not self._closed:
                self._check(self.cuda.cudaSetDevice(0))
                for pointer in self._device_buffers:
                    self.cuda.cudaFree(pointer)
                self._device_buffers.clear()
                self._closed = True
                self.context = None
                self.engine = None
                self.runtime = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
