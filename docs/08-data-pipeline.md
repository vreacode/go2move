# 数据集流水线

当前项目的第一版视觉任务采用两个 YOLO 检测类别：

- `0: garbage`：垃圾，第一版不再拆分瓶子、纸张、塑料袋等子类；
- `1: vehicle`：车辆。违停不是视觉类别，而是由车辆检测、禁停区域和停留时间规则共同判定。

## 推荐数据链路

1. 使用 ZT6 主码流 RTSP `video1` 采集 1080p 可见光原始帧；
2. 每次采集填写 `scene`、时间、路线、天气和备注；
3. 按“场景 + 日期”分组划分 train/val，不把同一段视频的相邻帧随机拆开；
4. 用 CVAT、LabelImg 或 Roboflow 标注 YOLO 格式；
5. 训练前运行数据集校验工具；
6. 训练完成后记录模型、数据集版本、输入尺寸、FPS、误报和漏报。

标注前可先检查采集覆盖情况：

```bash
python3 tools/capture_manifest_report.py \
  --manifest datasets/local/roadside_v1/manifests/<capture>.csv \
  --report datasets/local/roadside_v1/manifests/<capture>.report.json
```

它会按场景+日期统计帧数、时间跨度、采样间隔、视频源和缺失文件，并在场景组不足时给出警告。

4K TF 卡录像可以作为高质量母片，用于补充远距离、小目标和遮挡样本；它不应和同一时段的 1080p 相邻帧无差别混入数据集。

2026-07-19 在板上用 `ffprobe` 只读确认了当前 RTSP：

| 地址 | 编码 | 分辨率 | 帧率 | 用途 |
|---|---|---:|---:|---|
| `rtsp://<CAMERA_HOST>:8554/video1` | HEVC | 1920×1080 | 30fps | 可见光主数据集 |
| `rtsp://<CAMERA_HOST>:8554/video2` | HEVC | 640×512 | 30fps | 红外辅助数据 |

因此当前垃圾/车辆第一版数据集应优先使用 `video1`。红外数据单独建集，不要和可见光图片混在同一个类别分布里。

现场需要边看画面边采集时加入 `--preview`。预览窗口会显示场景名、已保存数量、下一张倒计时、当前分辨率和退出提示；它只给画面加显示层，不会把文字写入保存的原图。

## 从采集 manifest 准备 YOLO 目录

原始采集目录不会被修改。准备命令会复制图片，并为每个场景+日期整体分组：

```bash
cd /home/nvidia/carmove
python3 tools/prepare_yolo_dataset.py \
  --manifest datasets/local/roadside_v1/manifests/gate_a_day.csv \
  --output datasets/local/roadside_v1/yolo_v1 \
  --val-ratio 0.2 \
  --seed 20260719 \
  --names garbage vehicle
```

多行命令中的反斜杠 `\` 必须是该行最后一个字符；不要写成 `\ --source`，否则 shell 会把参数前的空格一起传入，导致程序识别不到 `--source`。

如果原图旁边已有同名 `.txt` 文件，工具会一并复制；没有标签的图片会保留，等待标注。

## 训练前校验

```bash
python3 tools/validate_yolo_dataset.py \
  --dataset datasets/local/roadside_v1/yolo_v1/dataset.yaml \
  --report datasets/local/roadside_v1/yolo_v1/validation.json
```

校验内容包括：图片是否存在、标签是否缺失、标签字段数量、类别 ID、归一化坐标、正宽高、框是否越界、孤立标签以及 train/val 是否为空。缺少标签或图片时命令返回非零状态，不应直接开始训练。

## 标注约定

- 每个图片对应同名 `.txt`；
- 每行是 `class_id x_center y_center width height`；
- 坐标归一化到 `0..1`；
- 框住可见垃圾主体，不把道路阴影、树叶和反光误标为垃圾；
- 车辆标注完整车身，严重遮挡时仍保留可见目标框；
- 车牌和人脸只作为场景背景，不新增个人信息类别，也不在申报材料中直接公开未脱敏样本。

当前仓库中的 2026-07-17 基线只有 5 张、单一场景、尚无标签，因此只能作为采集链路基线，不能作为训练集。
