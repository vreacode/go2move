"""Static original YOLOv5 / fallback YOLO11 adapter using JetPack TensorRT.

Reuses engine compilation/CUDA helpers from the working person detector. No torch,
package upgrades or foreign TensorRT binaries. Class mapping is pinned to the
downloaded model's verified embedded metadata, not guessed from a filename.
"""
from pathlib import Path
import hashlib
import json
import threading
import cv2
import numpy as np
from person_detector import _CudaRuntime, _build_engine

MODEL_DIR = Path('/home/unitree/go2_ros2_ws/models/helmet_demo')
MODEL_CONFIG = Path(__file__).with_name('config')/'helmet_model.json'


def model_spec(directory=None):
    if directory is not None:
        directory=Path(directory)
        manifest=directory/('helmet.labels.json' if (directory/'helmet.labels.json').is_file()
                            else 'yolo11n.labels.json')
    elif MODEL_CONFIG.is_file():
        manifest=Path(json.loads(MODEL_CONFIG.read_text())['manifest'])
    else:
        manifest=MODEL_DIR/'yolo11n.labels.json'
    metadata=json.loads(manifest.read_text())
    layout=metadata.get('format','yolo11_class_scores')
    labels=metadata['labels']
    if layout=='yolov5_objectness':
        if labels!=['helmet','head'] or metadata.get('class_map')!={'helmet':'Hardhat','head':'NO-Hardhat'}:
            raise ValueError('原安全帽模型类别与已核验的 helmet/head 映射不匹配。')
        canonical=[metadata['class_map'][name] for name in labels]
    elif layout=='yolo11_class_scores':
        if labels[:3]!=['Hardhat','Mask','NO-Hardhat']:
            raise ValueError('备用安全帽模型类别映射不一致。')
        canonical=labels
    else:
        raise ValueError('不支持的安全帽模型输出格式。')
    model=manifest.parent/metadata.get('model_file','yolo11n.onnx')
    if hashlib.sha256(model.read_bytes()).hexdigest()!=metadata['model_sha256']:
        raise ValueError('安全帽模型与已核验的类别映射不匹配。')
    return model,metadata,layout,canonical


def decode_predictions(output, labels, layout, image_shape, ratio, left, top):
    """Convert exact export layouts to head/person boxes; YOLOv5 uses obj*cls."""
    values=np.asarray(output)
    if values.ndim!=3 or values.shape[0]!=1:
        raise ValueError('安全帽模型输出需要 batch=1 的三维张量。')
    if layout=='yolov5_objectness':
        values=values[0]
        if values.shape[1]!=5+len(labels):raise ValueError('YOLOv5 输出与类别数量不一致。')
        probabilities=values[:,5:]*values[:,4:5]
    elif layout=='yolo11_class_scores':
        if values.shape[1]!=4+len(labels):raise ValueError('YOLO11 输出与类别数量不一致。')
        values=values[0].T
        probabilities=values[:,4:]
    else:
        raise ValueError('不支持的安全帽模型输出格式。')
    if not np.isfinite(values).all():raise ValueError('安全帽模型输出包含无效值。')
    classes=probabilities.argmax(axis=1);scores=probabilities.max(axis=1)
    eligible=[i for i,name in enumerate(labels) if name in ('Hardhat','NO-Hardhat','Person')]
    mask=(scores>=.35)&np.isin(classes,eligible)
    values,classes,scores=values[mask],classes[mask],scores[mask]
    if not len(values):return []
    h,w=image_shape[:2]
    boxes=np.column_stack((values[:,0]-values[:,2]/2,values[:,1]-values[:,3]/2,
                           values[:,0]+values[:,2]/2,values[:,1]+values[:,3]/2))
    boxes[:,[0,2]]=(boxes[:,[0,2]]-left)/ratio
    boxes[:,[1,3]]=(boxes[:,[1,3]]-top)/ratio
    boxes[:,[0,2]]=boxes[:,[0,2]].clip(0,w)
    boxes[:,[1,3]]=boxes[:,[1,3]].clip(0,h)
    result=[]
    for cls in eligible:
        indices=np.where(classes==cls)[0]
        xywh=boxes[indices].copy();xywh[:,2:]-=xywh[:,:2]
        keep=cv2.dnn.NMSBoxes(xywh.tolist(),scores[indices].tolist(),.35,.45)
        for offset in np.asarray(keep).reshape(-1):
            i=indices[int(offset)]
            if boxes[i,2]-boxes[i,0]<4 or boxes[i,3]-boxes[i,1]<4:continue
            result.append({'label':labels[int(cls)],'confidence':float(scores[i]),
                           'bbox':boxes[i].astype(float).tolist()})
    return result


class HelmetDetector:
    def __init__(self, directory=None):
        import tensorrt as trt
        model,metadata,self.layout,self.labels=model_spec(directory)
        self.model_source=metadata.get('source','open_source_fallback_yolo11n')
        self.model_sha256=metadata['model_sha256']
        self.model_path=str(model)
        logger = self.logger = trt.Logger(trt.Logger.WARNING)
        self.engine_path = _build_engine(model, trt, logger,
                                         disable_jit=self.layout=='yolov5_objectness')
        self.runtime = trt.Runtime(logger)
        self.engine = self.runtime.deserialize_cuda_engine(self.engine_path.read_bytes())
        if self.engine is None: raise RuntimeError('安全帽 TensorRT 引擎加载失败。')
        self.context = self.engine.create_execution_context()
        self.cuda, self.buffers, self.bindings = _CudaRuntime(), [], []
        self.lock = threading.Lock()
        self.input_index = self.output_index = None
        for i in range(self.engine.num_bindings):
            shape = tuple(self.engine.get_binding_shape(i))
            if any(v <= 0 for v in shape): raise ValueError('安全帽模型需要固定输入尺寸。')
            host = np.empty(shape, dtype=trt.nptype(self.engine.get_binding_dtype(i)))
            device = self.cuda.allocate(host.nbytes)
            self.buffers.append((host, device)); self.bindings.append(device.value)
            if self.engine.binding_is_input(i): self.input_index = i
            elif (len(shape)==3 and shape[0]==1 and
                  (self.layout=='yolov5_objectness' and shape[2]==5+len(self.labels) or
                   self.layout=='yolo11_class_scores' and shape[1]==4+len(self.labels))):
                # The original checkpoint also exports three auxiliary raw
                # heads. Bind their buffers, decode only the final predictions.
                if self.output_index is not None:
                    raise ValueError('安全帽模型存在多个候选预测输出。')
                self.output_index = i
        if self.input_index is None or self.output_index is None:
            raise ValueError('安全帽模型输入输出缺失。')
        self.input_shape = self.buffers[self.input_index][0].shape
        if self.input_shape != (1,3,640,640):
            raise ValueError('安全帽适配器需要 1×3×640×640 输入。')
        self.backend = metadata.get('backend','TensorRT FP16 / YOLO11n PPE')

    def detect(self, image):
        h,w = image.shape[:2]; ratio = min(640/w, 640/h)
        nw,nh = round(w*ratio), round(h*ratio); left,top = (640-nw)//2, (640-nh)//2
        canvas = np.full((640,640,3), 114, dtype=np.uint8)
        canvas[top:top+nh,left:left+nw] = cv2.resize(image,(nw,nh))
        with self.lock:
            self.cuda._check(self.cuda.lib.cudaSetDevice(0))
            inp,device = self.buffers[self.input_index]
            np.copyto(inp, canvas[:,:,::-1].transpose(2,0,1)[None]/255.)
            self.cuda.copy_to_device(device, inp)
            if not self.context.execute_v2(self.bindings): raise RuntimeError('安全帽推理失败。')
            out,pointer = self.buffers[self.output_index]
            self.cuda.copy_from_device(out,pointer); values = out.copy()
        return decode_predictions(values,self.labels,self.layout,image.shape,ratio,left,top)
