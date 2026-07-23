# 小车巡查数据集

这是垃圾识别和违停识别的本地数据集工作区。原始图片、标注和导出文件不提交 GitHub；只提交类别定义、采集规范和实验结果。

## v1 任务定义

### 垃圾

第一版先使用单一类别 garbage，避免在样本不足时把瓶子、纸张、塑料袋拆成过多类别。样本量稳定后，再按实际误差拆分类别。

### 违停

违停不是单纯的外观类别。第一版采用：

1. YOLO 检测 vehicle；
2. 记录车辆在禁停区域或停车线内的轨迹；
3. 用停留时间、区域和运动状态规则判定 illegal_parking 事件。

这样模型负责“看见车”，规则负责“判断是否违停”，比直接训练 illegal_parking 更容易解释、调试和申报。

## YOLO 数据目录

在板上本地建立以下目录，具体数据不会被 Git 跟踪：

    datasets/local/roadside_v1/
    ├── images/train/
    ├── images/val/
    ├── labels/train/
    ├── labels/val/
    └── manifest.csv

每张图片对应一个同名 .txt 标注文件。标注格式为：

    class_id x_center y_center width height

坐标全部归一化到 0–1。训练/验证集应按“场景、日期、路线”划分，不要把同一段视频的相邻帧随机拆到两边。

## 采集要求

- 至少覆盖白天、夜间/弱光、逆光、阴影、不同距离和不同车速；
- 垃圾要覆盖遮挡、大小、颜色、材质和路面背景；
- 违停场景要记录道路区域、禁停区域、停车线和车辆持续停留时间；
- 不采集或公开人脸、车牌等不必要的个人信息；申报材料中的样例要先脱敏；
- 每次采集填写 scene、时间、相机位置、天气、路线和备注。

采集工具：tools/collect_rtsp_frames.py。它只读取 RTSP 并生成图片和 manifest，不会控制小车。

当前板上已确认：`video1` 是 HEVC 1920×1080/30fps 可见光主码流，`video2` 是 HEVC 640×512/30fps 红外流。垃圾和车辆第一版使用 `video1`；红外另行建集。

需要在屏幕上观察画面时加 `--preview`。多行命令中的 `\` 必须放在行末，不能写成 `\ --source`。

预览窗口会显示当前场景、已保存数量、下一张倒计时、分辨率和 `q` 退出提示；保存的 JPEG 不包含这些提示文字。

## 数据处理工具

采集完成后，先按场景和日期成组准备 train/val，避免同一段视频的相邻帧泄漏到验证集：

```bash
python3 tools/prepare_yolo_dataset.py \
  --manifest datasets/local/roadside_v1/manifests/<capture>.csv \
  --output datasets/local/roadside_v1/yolo_v1 \
  --val-ratio 0.2 \
  --names garbage vehicle
```

标注完成后运行：

```bash
python3 tools/validate_yolo_dataset.py \
  --dataset datasets/local/roadside_v1/yolo_v1/dataset.yaml \
  --report datasets/local/roadside_v1/yolo_v1/validation.json
```

详细的采集、4K 母片、划分和标注约定见 `docs/08-data-pipeline.md`。

标注前可用 `python3 tools/capture_manifest_report.py --manifest <采集CSV>` 检查场景/日期组、时间跨度和缺失图片。
