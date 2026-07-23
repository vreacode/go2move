# YOLO 训练与 Jetson 验证

第一版先训练 `garbage` 和 `vehicle` 两类。`illegal_parking` 不直接作为检测类别，而由 `vehicle` 检测结果、禁停区域和停留时间规则判定。

## 开始训练前

必须先通过数据集检查：

```bash
cd /home/nvidia/carmove
python3 tools/validate_yolo_dataset.py \
  --dataset datasets/local/roadside_v1/yolo_v1/dataset.yaml \
  --report datasets/local/roadside_v1/yolo_v1/validation.json
```

如果提示缺少标签、train/val 为空、类别越界或框越界，先修数据，不要开始训练。

## YOLOv5 第一轮基线

仓库内已有 YOLOv5 源码，但不包含数据集和权重。推荐先用 `yolov5s` 做基线，输入 640，先获得可比较的结果，再考虑更大的模型：

```bash
cd /home/nvidia/carmove/apps/nano-deploy

# 有预训练权重时使用；权重文件不要提交 GitHub
python3 yolov5/train.py \
  --img 640 \
  --batch 4 \
  --epochs 100 \
  --data ../../datasets/local/roadside_v1/yolo_v1/dataset.yaml \
  --weights yolov5s.pt \
  --project ../../datasets/local/roadside_v1/runs \
  --name yolov5s_baseline \
  --cache ram
```

如果板上没有 `yolov5s.pt`，不要让板子在现场自动下载；可以在有网络的开发机准备权重后，通过受控方式复制到板上。没有预训练权重时也可以显式使用 `--weights '' --cfg models/yolov5s.yaml`，但需要更多样本和训练时间。

## 训练记录

每次实验至少保存：

| 字段 | 示例 |
|---|---|
| dataset | roadside_v1/yolo_v1 |
| commit | Git commit |
| classes | garbage, vehicle |
| model | yolov5s |
| image size | 640 |
| epochs/batch | 100/4 |
| best weight | 本地路径，不入库 |
| precision/recall/mAP | 从 val 输出填写 |
| Jetson FPS | 实机测量 |
| 误报/漏报 | 按场景记录 |

## Jetson 推理验收

训练完成后先单独验证模型，再和 Dashboard、导航叠加。推荐顺序：

1. 单路 RTSP + 模型，记录 FPS、CPU、GPU 和显存；
2. RTSP + 模型 + Dashboard WebRTC；
3. 再叠加雷达/IMU；
4. 最后才叠加导航或底盘控制。

推理时保持与采集链路一致：使用可见光主码流，输入尺寸先保持 640，Jetson 上确认稳定后再尝试 768 或更大尺寸。任何训练权重、运行事件图片、录像和 `runs/` 结果都只保留在板上本地。
