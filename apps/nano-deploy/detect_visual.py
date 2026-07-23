# -*- coding: utf-8 -*-
# @Time    : 2021/3/6 19:02
# @Author  : PeterH (Modified for YOLOv5 v7.0 & Video Stream)
# @File    : detect_visual.py
import shutil
import time
import os
import cv2
import random
import torch
import numpy as np
import pathlib
from pathlib import Path
from copy import deepcopy
from models.experimental import attempt_load

# --- 兼容性导入 ---
try:
    from utils.datasets import LoadStreams, LoadImages
except ImportError:
    from utils.dataloaders import LoadStreams, LoadImages

try:
    from utils.utils import check_img_size, non_max_suppression, scale_coords, xyxy2xywh, plot_one_box
except ImportError:
    from utils.general import check_img_size, non_max_suppression, xyxy2xywh

    try:
        from utils.general import scale_coords
    except ImportError:
        from utils.general import scale_boxes as scale_coords
    try:
        from utils.plots import plot_one_box
    except ImportError:
        def plot_one_box(x, img, color=None, label=None, line_thickness=3):
            tl = line_thickness or round(0.002 * (img.shape[0] + img.shape[1]) / 2) + 1
            c1, c2 = (int(x[0]), int(x[1])), (int(x[2]), int(x[3]))
            cv2.rectangle(img, c1, c2, color, thickness=tl, lineType=cv2.LINE_AA)
            if label:
                tf = max(tl - 1, 1)
                t_size = cv2.getTextSize(label, 0, fontScale=tl / 3, thickness=tf)[0]
                c2 = c1[0] + t_size[0], c1[1] - t_size[1] - 3
                cv2.rectangle(img, c1, c2, color, -1, cv2.LINE_AA)  # filled
                cv2.putText(img, label, (c1[0], c1[1] - 2), 0, tl / 3, [225, 255, 255], thickness=tf,
                            lineType=cv2.LINE_AA)


# ------------------

# 兼容 Windows 上保存的权重在 Linux 加载时报 WindowsPath 错误的问题
if os.name != "nt":
    pathlib.WindowsPath = pathlib.PosixPath

class YOLOPredict(object):
    def __init__(
        self,
        weights,
        out_file_path,
        conf_thres=0.4,
        iou_thres=0.5,
        img_size=640,
        device=None,
        half=False,
        vid_stride=1,
    ):
        self.weights = weights
        self.output = out_file_path
        self.conf_thres = conf_thres
        self.iou_thres = iou_thres
        self.img_size = img_size

        # 默认使用 CPU，在 Nano 上如果装了 PyTorch GPU 版，代码会自动切到 CUDA
        # 修正写法：必须写 'cuda:0'，不能只写 '0'
        if device is None or device == "":
            self.device = torch.device('cuda:0') if torch.cuda.is_available() else torch.device('cpu')
        else:
            self.device = torch.device(device) if not isinstance(device, torch.device) else device
        self.half = half
        self.vid_stride = vid_stride

        self.classes = None
        self.agnostic_nms = False
        self.augment = False
        self.save_txt = False
        self.view_img = False  # 在 run_nano_all.py 里设为 True 即可弹窗
        self.update = False
        self.model, self.names, self.colors = self.load_model()

    def load_model(self):
        print(f"正在加载模型到 {self.device} ...")
        # 强制使用新版写法
        model = attempt_load(self.weights, device=self.device)
        self.img_size = check_img_size(self.img_size, s=model.stride.max())
        if self.half and self.device.type != 'cpu':
            model.half()
        else:
            model.float()

        names = model.module.names if hasattr(model, 'module') else model.names
        colors = [[random.randint(0, 255) for _ in range(3)] for _ in range(len(names))]

        # 预热
        img = torch.zeros((1, 3, self.img_size, self.img_size), device=self.device)
        # 模型使用 CUDA FP16 时，预热输入也必须是 half；否则首帧前就会因
        # float 输入与 half bias 类型不一致而失败。
        img = img.half() if self.half and self.device.type != 'cpu' else img.float()
        _ = model(img)
        return model, names, colors

    def detect(self, source, save_img=False, qt_input=None, qt_output=None, vid_stride=None):
        out = self.output
        if not os.path.exists(out): os.makedirs(out, exist_ok=True)

        # 判断源类型
        webcam = source == '0' or source.startswith(('rtsp', 'http', 'https'))

        if webcam:
            print(f"正在连接视频流: {source}")
            dataset = LoadStreams(source, img_size=self.img_size, vid_stride=vid_stride or self.vid_stride)
        else:
            print(f"正在加载文件: {source}")
            dataset = LoadImages(source, img_size=self.img_size, vid_stride=vid_stride or self.vid_stride)

        for item in dataset:
            # 兼容性解包
            if len(item) == 5:
                path, img, im0s, vid_cap, info_str = item
            else:
                path, img, im0s, vid_cap = item[:4]

            # 处理多路流的情况 (LoadStreams 返回的是 list)
            if webcam:
                # 默认只取第一路流
                if isinstance(im0s, list):
                    im0s = im0s[0].copy()
                else:
                    im0s = im0s.copy()
            else:
                im0s = im0s.copy()

            model_device = next(self.model.parameters()).device
            img = torch.from_numpy(img).to(model_device)
            # 处理多路流的 img shape
            if webcam and img.ndimension() == 4:
                img = img[0]  # 取第一张图

            img = img.half() if self.half and self.device.type != 'cpu' else img.float()
            img /= 255.0
            if img.ndimension() == 3: img = img.unsqueeze(0)

            pred = self.model(img, augment=self.augment)[0]
            pred = non_max_suppression(pred, self.conf_thres, self.iou_thres, classes=self.classes,
                                       agnostic=self.agnostic_nms)

            for i, det in enumerate(pred):
                im0 = im0s.copy()  # 当前帧的画布

                if len(det):
                    det[:, :4] = scale_coords(img.shape[2:], det[:, :4], im0.shape).round()
                    for *xyxy, conf, cls in reversed(det):
                        label = f'{self.names[int(cls)]} {conf:.2f}'
                        plot_one_box(xyxy, im0, label=label, color=self.colors[int(cls)], line_thickness=3)

                # --- 视频流显示逻辑 ---
                if self.view_img:
                    cv2.imshow("Nano Detection (Press 'q' to exit)", im0)
                    # 1ms 延时，如果是视频文件则根据FPS调整
                    if cv2.waitKey(1) == ord('q'):
                        print("用户退出")
                        return

        if not webcam:
            print("文件处理完成。")
