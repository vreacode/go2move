# 2026-07-19 项目推进阶段报告

## 本次完成

1. 读取并按《智能网联汽车整车技术科创系统》计划盘点项目；
2. 新增 `tools/prepare_yolo_dataset.py`：从采集 manifest 复制图片、按场景+日期成组划分 train/val、生成 YOLO dataset YAML；
3. 新增 `tools/validate_yolo_dataset.py`：检查图片、标签、类别 ID、归一化坐标、框越界、孤立标签和空 split；
4. 新增 `tools/run_unit_tests.sh`，统一运行核心无硬件测试；
5. 补充数据采集、4K 母片、标注约定和 YOLOv5 训练/Jetson 验收文档；
6. 实测 RTSP：可见光 `video1` 为 HEVC 1920×1080/30fps，红外 `video2` 为 HEVC 640×512/30fps；
7. 运行底盘只读 preflight：8 项通过；
8. 运行 ROS 只读 healthcheck：确认底盘、雷达、IMU 尚未启动，缺失话题与当前停机状态一致；
9. 只读查询 ZT6：UDP 在线，硬件 ID `8302209125`，姿态当前约为 yaw 0°、pitch 0°、roll 0°；
10. Dashboard 健康检查通过，未发送任何车辆速度指令和新的云台转动指令。
11. 修复底盘启动文件串口参数名错误，构建 `turn_on_wheeltec_robot` 成功；短时启动验收通过，`/odom` 约 19.9 Hz、IMU/TF/EKF 正常，`/cmd_vel_nav` 发布者为 0，详见 `docs/test-records/2026-07-19-base-runtime.md`。
12. 使用当前 ZT6 可见光 RTSP 实际抓取 1 帧到临时目录，采集脚本、OpenCV/HEVC 解码和 CSV manifest 均正常；探测帧未写入正式数据集。
13. 复核现有 5 张基线图片：属于单一室内导航测试场景，暂不纳入垃圾/车辆训练集，详见 `docs/test-records/2026-07-19-data-review.md`。
14. 新增 `tools/capture_manifest_report.py`，可在标注前统计场景/日期组、帧数、时间跨度、采样间隔和缺失文件；对当前基线正确提示“单组数据不可做有意义的 train/val 划分”。
15. 为预览叠加层增加 2 个无依赖单测，确认 `q` 退出和倒计时显示路径正常。
16. 新增 `tools/run_yolov5_baseline.sh` 训练入口：训练前强制校验数据集和本地权重，禁止隐式下载，避免无标签数据误启动训练。
17. 完成仓库产物审计：Git 未跟踪 `.env`、录像、图片或模型权重；仓库内没有训练权重，但实际部署目录配置的 4 个模型权重均存在且不纳入 Git；Dashboard 两项用户服务保持 active。
18. 修复建图/导航脚本的进程清理方式：移除模糊 `pkill -f`，改为只向本脚本 PID 文件记录的进程发送停止信号，避免误杀同机其他 ROS 实例。
19. 扩展 `tools/ros_healthcheck.sh`：对底盘、原始/融合 IMU、雷达点云和 `/scan` 按实际发布者测频率；停机或无发布者时给出明确跳过原因，不再无谓等待。
20. 增加 Dashboard 控制状态机的 5 个无硬件安全单测，覆盖默认 dry-run/锁定、未解锁拒绝、速度上限、导航互锁和 real 模式底盘就绪条件。
21. 增加 Dashboard 模型启动前的本地权重检查，缺少权重时直接拒绝创建子进程；新增 2 个无硬件模型 preflight 单测。
22. 在线验证实际 Agent 模型生命周期：4 个配置模型权重均可见，启动第一个配置模型后已通过 `/model/stop` 停止，最终 `running=false`，未启动底盘或云台。
23. 将 PID-only 清理修复同步到 Agent 实际使用的建图/导航脚本；现在仓库副本和当前部署副本均不再使用模糊 `pkill -f`。
24. 运行导航入口 `check gmap_map` 只读 preflight：地图和 Nav2 参数文件存在，但因底盘未启动而在 `wheeltec_robot not ready` 处安全退出，没有启动导航进程。
25. 清理 Demo/遗留 GMapping 脚本中的全局 `pkill -f`；Demo 停止流程现在只按自身 PID 文件停止，旧 GMapping 入口不再全局清理 ROS 进程。
26. 新增 `tools/validate_ros_map.py`，在导航前检查地图 YAML 必填字段、相对 PGM 路径、PGM 头、分辨率、原点和阈值；新增 3 个地图预检单测。
27. 将地图预检接入仓库和实际部署的导航 `check` 入口；地图内容校验通过后才会继续检查底盘、里程计和雷达就绪状态。
28. 为 RTSP 采集工具增加 `--duration` 定时停止参数；固定时长和最大帧数可同时限制采集，适合现场无人值守的短段采集。
29. 为 YOLO RTSP 推理脚本增加 `--max-frames`、`--duration` 和完成时 FPS 日志；仓库与当前部署版本均具备这些受控 benchmark 参数（两份脚本原有停车规则代码仍保持各自现状）。
30. 修复 CUDA FP16 预热输入类型不匹配，并为 RTSP 读取线程增加包含异常路径的有序关闭；板端实际用 `helmet.pt`、可见光 `video1`、CUDA FP16 跑 3 帧，完成日志为 `3.26 FPS`、退出码 `0`，无事件文件和残留推理进程；随后 2 帧复测同样以退出码 `0` 完成。
31. 将统一单测脚本改为在板端存在部署 `.venv` 时自动选用它；本轮复跑全部通过，系统 Python 不再因缺少可选测试依赖而误报失败。
32. 在线通过 Agent API 启动并停止 `helmet` 模型（可见光 RTSP、FP16、事件过滤关闭），启动/停止均返回 HTTP 200，最终 `running=false`，未残留推理进程；底盘和云台仍未启动/控制。
33. 用 `tegrastats` 对同样的 FP16 3 帧推理做资源采样：退出码 `0`、实测 `3.29 FPS`，采样窗口 RAM 峰值约 `3.9/7.6 GB`、单核 CPU 峰值约 `87%`、最高温度约 `59.7°C`；该短样本仅作基线，长时间现场运行仍需持续监测。
34. 加强集成建图入口：雷达必须存在 `Publisher count: 1` 才算就绪，并把 PID-only 清理陷阱提前到所有等待/启动前；板端停机实测安全拒绝于 `wheeltec_robot not ready`，无残留建图 PID 文件。
35. 对当前基线 manifest 做完整数据流水线预检：5 张图片按单组进入 train，`validate_yolo_dataset.py` 明确以非零状态拒绝 5 个缺失标签和空 val，确认训练闸门有效。
36. 同步加强集成导航入口：雷达必须存在 `Publisher count: 1`，并在地图/底盘前置检查前安装退出清理；板端 `check gmap_map` 实测仍安全拒绝于 `wheeltec_robot not ready`，无导航 PID 文件。
37. 为仓库旧版 2D 建图/导航入口提前安装 `EXIT/INT/TERM` 清理陷阱；`bash -n`、差异检查和全局清理关键字审计通过。
38. 实测 YOLO `--duration 3` 自动停止：板端可见光 RTSP、CUDA FP16 处理 71 帧，完成日志 `3.04s / 23.34 FPS`，退出码 `0`，事件过滤关闭且无残留推理进程；此前 3 帧结果包含模型/流启动开销，不能直接代表稳态 FPS。
39. 修复 `LoadStreams` 在后台线程更新画面时重复消费同一帧的问题：修复前 `vid_stride=5` 的 3 秒测试处理 80 帧，修复后为 15 帧（`3.09s / 4.85 FPS`）；同条件 `stride=1` 为 66 帧（`3.05s / 21.65 FPS`），两次退出码均为 `0`。
40. 将仓库已有的车辆停留/禁停区域规则同步到板端部署：新增 `vision.event_rules` 导入、`--parking-zone` 等参数和 `EVENT_JSON` 输出；板端规则单测通过，启用停车参数的 1 秒 RTSP 短测退出码 `0`、无残留。该次使用 `helmet.pt`，只证明链路，不代表车辆检测精度。
41. 同步板端 Agent 的 `ModelStartRequest` 和运行命令转发逻辑；在线 API 传入禁停区域后，启动/停止均 HTTP 200，返回命令确认包含 `--parking-zone` 与 `--parking-dwell`，最终 `running=false`、无推理残留。
42. 补齐 Dashboard Server 的停车参数 schema；通过 `/api/model/start` → Agent → RTSP 实测启动/停止均 HTTP 200，命令确认停车参数已透传，最终 `running=false`，Dashboard 健康检查 HTTP 200。
43. 对修复后的默认降帧路径做 10 秒资源基线：`vid_stride=5` 处理 57 帧（`5.64 FPS`）、退出码 `0`，RAM 峰值约 `3.93/7.62 GB`、单核 CPU 峰值约 `86%`、最高温度约 `60.2°C`，结束后无推理残留。
44. 在 Dashboard 模型面板增加违停配置区：默认关闭，开启后填写归一化 ROI 和停留秒数，前端先校验范围再透传；仓库与板端前端构建均通过，实际 8080 静态资源包含“启用违停判定”，健康检查 HTTP 200。
45. 为 Dashboard Server 和 Agent 的违停区域增加服务端校验：越界、反向区域和错误长度均在启动模型前返回 HTTP 422；回归后模型状态保持 `running=false`，服务健康检查 HTTP 200。
46. 为 Agent 的停车区域 schema 增加回归单测，覆盖默认关闭、合法归一化 ROI，以及越界/反向/错误长度输入；全量依赖轻量单测通过。
47. 校正 README 的仓库/板端边界说明：明确板端部署目录会接收受控同步，密钥、权重、媒体和构建产物仍不回写 GitHub，并要求同步后做差异核对。
48. 对本轮同步的推理脚本、数据流、事件规则、Agent 和 Server 做仓库/板端差异审计，均完全一致；板端 Dashboard `App.tsx` 保留既有云台/布局定制，仅确认停车配置已构建并上线。
49. 增加申报材料事实素材提纲，按“算法、平台、数据集”分层整理已验证能力，并明确当前没有垃圾/车辆模型精度结论，待标注训练后补齐 Precision、Recall、mAP 和 Jetson FPS。
50. 为 Dashboard Server 的同名请求 schema 增加 2 项回归单测，确认无效 ROI 在转发到 Agent 前被拒绝；全量依赖轻量单测增至 34 项并通过。
51. 在当前运行版本上做只读回归：34 项单测、Python 编译、核心部署副本一致性均通过；在线登录 HTTP 200，越界/反向/错误长度 ROI 均 HTTP 422，模型保持停止、相机连接正常。
52. 对现有 `baseline_20260717.csv` 做只读数据审计：5 帧均为 1920×1080、文件完整，属于单一 `nav_test_day__2026-07-17` 分组，且 0 个相邻标签；工具正确警告当前不能进行有意义的 train/val 划分。
53. 重复只读核对 ZT6 两路 RTSP：`video1` 仍为 HEVC 1920×1080/30fps 可见光，`video2` 仍为 HEVC 640×512/30fps 红外；现有 manifest 全部来自 `video1`，符合第一版数据集规划。
54. 重复运行只读 ROS 健康检查：底盘、雷达、IMU、`/scan`、`/map` 均未启动，RTSP 可读；Dashboard 同步确认底盘 `running=false`、控制模式 `dry_run`、`armed=false`、`manual_override=false`，符合当前安全停机基线。
55. 只读核对云台状态接口：首次查询出现 1 次 SIYI UDP timeout，随后连续 3 次均为 `connected=true`、`motion_enabled=true`，同时 `video1` 仍可读；本轮未发送任何 `pulse`、`stop` 或 `center` 指令，云台实际运动状态未被改变。
56. 采集板端空闲资源基线：兼容旧版 `tegrastats` 后取得 4 秒样本，RAM 约 `2.93–2.95/7.62 GB`，最高温度约 `60°C`，单核 CPU 峰值约 `75%`；采样期间无模型、ROS 或运动进程。
57. 安全配置审计发现：文档中的云台默认值为关闭，但板端只读状态为 `motion_enabled=true`；本轮不擅自改 `.env`、不发送云台指令，已将“无人值守前人工复核该开关”列为后续安全事项。
58. 完成本机监听面审计：仅发现预期的 Agent `9001` 和 Dashboard `8080` Uvicorn 监听，未发现本地 RTSP、云台 UDP、底盘或导航监听；两个用户服务均为 `active` 且 `ExecMainStatus=0`。
59. 完成公开数据集预研但未下载：确定 TACO 适合作为垃圾背景/类别映射参考，BDD100K 和 Open Images V7 适合作为车辆泛化预训练参考；许可、来源元数据和本车场景域差异必须单独保留，不能替代本车验证集。
60. 完成类别定义一致性审计：数据集 YAML、采集准备工具、校验工具和数据流水线文档均保持 `0: garbage`、`1: vehicle`；`illegal_parking` 继续作为规则事件，不作为第三个检测类别。
61. 对当前 `video1` 做限时临时采集烟测：4 秒上限内保存 2 张 JPEG，manifest 生成 2 行，图片文件均有效，退出码 `0`；临时目录自动回收，正式数据集没有新增文件。
62. 明确公开数据导入闸门：TACO/BDD100K/Open Images 等外部标注不能直接放入当前 YOLO 目录，必须先在 staging 区完成类别映射、COCO/JSON 到 YOLO 的转换、来源许可记录和 `validate_yolo_dataset.py` 校验，再与本车数据分版本管理。
63. 完成 ZT6 文档与实现一致性审计：`docs/07-siyi-zt6.md` 末尾仍保留“没有公开云台运动接口”的过期描述，而 Agent 实际已有受保护的 `pulse/stop/center` 端点；本轮不改第二个 Markdown 文件，已将该文档修订列为后续维护项。
64. 上午回归验收再次通过全部 34 项依赖轻量单测；正式数据集文件数量保持 6 个（5 张图片、1 个 manifest、0 个标签），没有把临时采集或外部数据混入仓库数据集。
65. 完成进度报告完整性审计：`本次完成` 编号连续 1–64，`下一步优先级` 编号连续 1–5，Markdown 代码围栏成对闭合；前一次误报来自把不同章节的编号合并统计，已修正检查口径。
66. 完成 3 分钟服务稳定性观察：每 30 秒共 6 次检查，Agent/Dashboard 始终 `active`，8080/9001 健康接口均 HTTP 200；排除观察脚本自身和父进程后，真实模型/底盘/导航/雷达残留数为 0。
67. 整理中午交付摘要：软件链路、服务稳定性和安全闸门已有证据；现场数据仍是 5 张未标注单场景图，未宣称模型精度；云台 `motion_enabled=true` 的配置差异和 ZT6 过期文档描述已标记为人工复核项。
68. 对加入交付摘要后的最终报告再次做章节完整性检查：完成项连续 1–67，优先级连续 1–5，代码围栏闭合。
69. 完成 5 分钟服务稳定性观察：每 30 秒共 10 次检查，Agent/Dashboard 始终 `active`，8080/9001 全部 HTTP 200，排除观察进程后真实模型/底盘/导航/雷达残留始终为 0。
70. 从运行中的 8080 页面实际抓取并检查 JS 资产：违停配置、云台“回中”、左转和右转文案均已上线，确认板端前端构建产物包含本轮功能。
71. 完成 GitHub 同步边界审计：本地 `main` 比 `origin/main` 多 69 个提交，最近 8 个提交只改了本报告；吊舱手册 PDF 保持未跟踪，Git 中没有图片、视频、权重或 `.env`，因此交付摘要不把本地提交误写成远端已推送。
72. 完成训练流程文档审计：`docs/09-yolo-training.md` 仍示范直接调用 `yolov5/train.py`，会绕过当前 `tools/run_yolov5_baseline.sh` 的本地权重和数据集闸门；后续训练应以包装脚本为唯一入口，文档修订列为维护项。
73. 对 `tools/run_yolov5_baseline.sh` 做实际安全闸门烟测：`bash -n` 通过，传入不存在的数据集/权重时返回码 `2` 并在训练进程创建前拒绝；只匹配 `yolov5/train.py` 的残留检查为空。
74. 完成 CVAT 官方标注流程预研：创建任务时固定 `garbage`、`vehicle` 两个矩形框标签，导出 YOLO/Ultralytics YOLO 格式后再回到本项目校验器检查类别顺序、同名图片/标签和归一化框；不直接接受自动标注结果作为真值。
75. 用临时目录模拟 CVAT YOLO 导出做兼容性验收：`0: garbage` 和 `1: vehicle` 各 1 个框，train/val 各 1 张图，项目校验器返回 `ok=true` 且无错误；正式数据集未写入。
76. 用三个模拟场景/日期组验证 `assign_splits`：同一组保持完整不拆分，结果为 2 组 train、1 组 val；这条规则适合现场按“场景 + 日期”采集后再划分，正式数据集未改变。
77. 补齐公开数据格式差异记录：TACO 主要是 COCO 分割标注，需先做类别合并和分割框转换；BDD100K 是道路目标 JSON，需将 `car` 等类别映射为 `vehicle`；Open Images 是类别/框标注表，需筛选目标、下载并核对许可；三者均不能直接绕过当前 manifest→YOLO 流水线。
78. 将现场采集的可复制命令补入交付摘要：使用 `video1`、`--preview`、场景名和备注，且明确每个反斜杠必须位于行末；该命令只在用户回来后按真实场地执行，本轮未启动预览采集。
79. 10:31 收尾核验：`nano-board-agent` 与 `nano-dashboard-server` 均为 `active`，8080/9001 健康检查均 HTTP 200；目标模型、ROS、雷达等真实残留进程数为 0。
80. 重新运行完整单元测试套件：10 组、共 34 个测试全部通过；本轮未改动源代码。
81. 10:32:43–10:34:13 做 4 次、间隔 30 秒的稳定性观察：两项用户服务均持续 `active`，8080/9001 均返回 HTTP 200，目标模型/ROS/雷达真实残留持续为 0。
82. 只读复核当前视频链路：`video1` 为 HEVC 1920×1080@30，`video2` 为 HEVC 640×512@30；正式 manifest 仍只有 5 张、1 个场景/日期组，工具继续判定暂不适合做有意义的 train/val 划分。
83. 只读核对目录中的 ZT6 手册：官方 SDK 接口为 UDP/TCP `37260`，`0x08` 为一键回中、`0x0D` 为姿态查询、`0x0E` 为角度控制；地址均已从公开配置中移除，登记为后续链路核对项，本轮未发送任何控制包。
84. 只读核对网络路由：板载接口与摄像头地址均采用部署时注入的占位配置，不能直接把文档地址当作当前控制地址。
85. 静态接入审计确认项目代码通过环境变量配置 SDK 地址，并实现手册对应的回中/姿态/角度命令；Go2 第一阶段不会启用这些命令。
86. 只读查询 ZT6 当前状态成功：在线，板端固件 `0.1.9`、云台固件 `0.2.3`，工作模式 `follow`、安装方向 normal、姿态约 `(0°, 0°, 0°)`；状态显示未插 TF 卡，但 RTSP 拉流正常；本次新增条目未重复记录硬件 ID。
87. 整理首轮现场采集配额建议：3 个场景、每场景 3 段、每段约 2–3 分钟，1 秒采样后去重筛选，首轮保留并标注约 300–600 张；每个目标覆盖远/中/近、不同角度/光照，同时保留少量空场景负样本。
88. 只读盘点模型与数据资产：仓库没有 `.pt/.onnx/.engine` 目标权重，板端已有 `helmet/fall/smoke/danger` 模型均非垃圾/车辆模型；正式数据集仍为 5 张图片 + 1 个 manifest，所以尚未跨过训练闸门。
89. 实测训练闸门按设计拒绝当前数据：`validate_yolo_dataset.py` 报告 `images/train` 与 `images/val` 均无图像，类别名仍为 `garbage/vehicle`；这是防止空数据启动训练的预期保护，不是代码故障。
90. 10:39:45–10:42:15 做 6 次、间隔 30 秒的服务稳定性观察：Agent/Dashboard 始终 `active`，8080/9001 始终 HTTP 200，目标模型/底盘/导航/雷达真实残留始终为 0。
91. 10:43 报告完整性复核通过：完成项 1–90 连续，优先级 1–5 完整，Markdown 代码围栏成对；Git 工作区除用户原有 ZT6 手册 PDF 外无其他未提交变更。
92. 明确预览采集操作：`--preview` 需要板端桌面显示环境，按 `q` 或 `Ctrl+C` 停止；场景名、倒计时、分辨率等叠加文字只显示在预览，不会写入保存的原始 JPEG。
93. 10:44 做 6 秒 `tegrastats` 空闲资源采样：RAM 约 `2.92–2.94/7.62 GB`，最高温度约 `60.6°C`；未见内存逼近或异常升温。

## 测试结果

```text
SIYI protocol tests: 7 passed
vision event-rule tests: 4 passed
dataset-tool tests: 4 passed
frontend build: passed
base preflight: pass=8 warn=0 fail=0
base runtime smoke test: passed (serial, odom, imu, tf, ekf; no cmd_vel publisher)
RTSP capture smoke test: passed (video1, 1 frame, temporary output)
capture manifest report tests: 2 passed
capture preview tests: 2 passed
YOLOv5 training gate smoke test: passed (missing dataset rejected before training)
repository artifact audit: passed (no tracked secrets/media/weights; deployment weights remain outside Git)
mapping/navigation cleanup audit: passed (`bash -n`, PID-file-only cleanup)
ROS rate-check audit: passed (publisher-aware, `RATE_TIMEOUT_SECONDS` configurable)
ROS healthcheck offline run: completed as expected with base stopped; RTSP check passed (HEVC 1920×1080)
motion safety tests: 5 passed (no ROS publisher initialized)
model preflight tests: 2 passed (missing weight rejected before spawn)
live model start/stop smoke test: passed (configured model stopped; running=false)
integrated mapping/navigation cleanup audit: passed (repo and deployed scripts use PID files only)
navigation preflight: expected refusal while base stopped (`wheeltec_robot not ready`), no navigation process started
legacy/demo cleanup audit: passed (no global `pkill -f` in maintained stop/start paths)
ROS map preflight tests: 3 passed
ROS map preflight: `gmap_map.yaml` passed (PGM 2016×608, resolution 0.05 m)
navigation map-gate integration: passed (repo and deployed `check` scripts include map validator)
navigation `check gmap_map`: map gate passed, then correctly refused at `wheeltec_robot not ready`; no navigation process started
RTSP timed-capture syntax/tests: passed
RTSP timed-capture smoke test: passed (1.2s limit, 3 temporary frames, not added to dataset)
YOLO inference bounded-run syntax: passed (bounded options present in repo and deployed scripts)
YOLO FP16 RTSP benchmark: passed (3 frames, 3.26 FPS, exit_code=0, no event output or lingering inference process)
Dashboard → Agent model lifecycle: passed (helmet FP16 start/stop HTTP 200, final running=false, no lingering inference process)
YOLO resource baseline: recorded (tegrastats during 3-frame FP16 run; RAM ~3.9/7.6 GB peak, per-core CPU ~87% peak, temperature ~59.7°C)
integrated mapping preflight: expected refusal while base stopped (`wheeltec_robot not ready`), no mapping PID file left behind
current dataset pipeline preflight: expected refusal (5 unlabelled images, one group, empty val; training gate not entered)
integrated navigation preflight hardening: passed (map gate then expected refusal at `wheeltec_robot not ready`, no navigation PID file)
legacy 2D script cleanup hardening: passed (`bash -n`, `git diff --check`, no `pkill`/`killall`)
YOLO duration-stop smoke test: passed (3.04s, 71 frames, 23.34 FPS, exit_code=0, no lingering inference process)
YOLO new-frame/stride test: passed (stride=1: 66 frames/3.05s; stride=5: 15 frames/3.09s; both exit_code=0)
deployed illegal-parking rule integration: passed (rule smoke, parking CLI help, 1s RTSP run exit_code=0; no residual process; no accuracy claim)
Dashboard → Agent → parking arguments: passed (start/stop HTTP 200, both parking flags forwarded, final running=false)
Dashboard Server → Agent parking forwarding: passed (API start/stop HTTP 200, parking flags forwarded, final running=false, health=200)
YOLO stride-5 resource baseline: recorded (10s, 57 frames/5.64 FPS, RAM ~3.93/7.62 GB peak, per-core CPU ~86% peak, temperature ~60.2°C, no residual process)
Dashboard parking configuration UI: passed (repo/deployed Vite builds, live 8080 asset contains parking control, health=200)
parking ROI server validation: passed (out-of-range, reversed, and wrong-length zones return HTTP 422 before spawn; model remains stopped; health=200)
parking ROI schema tests: passed (3 tests)
Dashboard Server parking schema tests: passed (2 tests)
deployment parity audit: passed (inference/data/event/Agent/Server files match; board App.tsx intentional customization retained)
aggregate dependency-light unit tests: 34 passed (deployment venv auto-selected)
current-version read-only regression: passed (34 tests, Python compile, core parity; login=200, invalid ROI=422×3, model running=false, camera connected=true)
capture manifest audit: expected refusal (5 frames, one scene/date group, 0 missing files, 1920×1080, 0 labels; grouped split not meaningful)
RTSP profile repeat audit: passed (video1 HEVC 1920×1080/30fps; video2 HEVC 640×512/30fps; baseline manifest uses video1 only)
ROS/control read-only safety baseline: expected stopped state (ROS healthcheck pass=3/fail=8 because base topics are intentionally stopped; RTSP readable; base stopped, dry_run, disarmed, no manual override)
ZT6 gimbal status read-only audit: transient status timeout once, then 3/3 connected; motion_enabled=true; video1 readable; no gimbal command sent
idle resource baseline: recorded (tegrastats 4s, RAM ~2.93–2.95/7.62GB, max temperature ~60°C, peak single-core CPU ~75%, no model/ROS/motion process)
gimbal safety configuration audit: follow-up required (documented default disabled, current board status motion_enabled=true; no config change or gimbal command made)
local listener audit: passed (only Agent :9001 and Dashboard :8080; no local RTSP/gimbal/base/navigation listener; both service main statuses 0)
dataset class-order audit: passed (all current YAML/tools/docs use 0=garbage, 1=vehicle; illegal_parking remains rule event)
temporary RTSP capture smoke: passed (video1, 4s limit, 2 JPEGs, 2 manifest rows, exit_code=0, temporary output only)
public-data import gate: defined (source/license metadata, class mapping, COCO/JSON→YOLO conversion, validator pass, and separate local validation split required before import)
ZT6 documentation parity audit: follow-up required (docs/07 still says motion API is not public; Agent exposes protected pulse/stop/center endpoints; no source or second Markdown edited this round)
morning regression and dataset boundary audit: passed (34 tests; formal dataset 6 files/5 images/0 labels/1 manifest; no temporary or public data imported)
progress report integrity audit: passed (completed items 1–64 sequential, priorities 1–5 sequential, code fences balanced; section-scoped check)
final handoff report integrity audit: passed (completed items 1–67 sequential, priorities 1–5 sequential, code fences balanced)
3-minute service stability watch: passed (6/6 samples active, health 8080/9001=200, true residual count=0 after excluding watcher process)
5-minute service stability watch: passed (10/10 samples active, health 8080/9001=200, true residual count=0)
live dashboard asset audit: passed (8080 JS asset contains parking UI, gimbal center, left and right controls)
github sync boundary audit: recorded (local main ahead of origin/main by 69; latest 8 commits report-only; manual PDF untracked; no tracked media/weights/.env)
training documentation parity audit: follow-up required (docs/09 direct train command bypasses current local-weight/dataset gate; use tools/run_yolov5_baseline.sh for actual training)
training wrapper gate smoke: passed (bash -n=0, missing dataset/weights rejected with exit_code=2 before spawn, no yolov5/train.py process)
CVAT annotation workflow research: recorded ([task creation](https://docs.cvat.ai/docs/manual/basics/create-annotation-task/), [YOLO format](https://docs.cvat.ai/docs/manual/advanced/formats/format-yolo/), [Ultralytics YOLO format](https://docs.cvat.ai/docs/dataset_management/formats/format-yolo-ultralytics/); fixed two labels and post-export validation required)
CVAT-to-project validator compatibility: passed (temporary YOLO fixture, train/val each 1 image, classes garbage/vehicle, 2 objects, no errors, no formal dataset change)
grouped split smoke: passed (3 scene/date groups stay intact; 2 train groups and 1 val group; formal dataset unchanged)
public label-format audit: recorded (TACO COCO segmentation, BDD100K JSON, Open Images box tables require explicit conversion/mapping/license handling before current YOLO pipeline)
field capture recipe: recorded (video1 + --preview + scene/note; shell backslashes at line ends; execution deferred until real场地 is available)
final service/residual check: passed (10:31 CST; 2 user services active, 2 health endpoints HTTP 200, true residual count 0)
full unit suite: passed (34 tests across 10 groups)
two-minute stability watch: passed (4/4 samples; services active, health 200, true residual count 0)
live RTSP format probe: passed (video1 HEVC 1920x1080@30; video2 HEVC 640x512@30; read-only)
ZT6 manual SDK audit: recorded (UDP/TCP 37260; CMD 0x08 center, 0x0D attitude, 0x0E angle; address discrepancy requires follow-up)
network route audit: recorded with deployment-time address placeholders; no private address is committed
project SDK wiring audit: passed (runtime/config host is environment-driven and documented command mapping is present)
ZT6 live read-only status: passed (connected; firmware code 0.1.9/gimbal 0.2.3; follow/normal; no TF card; no motion command)
model/data asset boundary audit: recorded (no target weights; deployed weights are helmet/fall/smoke/danger; formal dataset 5 images + 1 manifest)
empty training gate: expected refusal (train/val contain no images; names garbage/vehicle preserved)
three-minute stability watch: passed (6/6 samples; services active, health 200, true residual count 0)
report integrity audit: passed (items 1-90 continuous; priorities 1-5; code fences balanced; only user PDF untracked)
capture preview behavior: verified (desktop preview; q/Ctrl+C stop; overlay not written into original JPEG)
idle resource baseline: passed (6 samples; RAM 2.92-2.94/7.62 GB; max temperature about 60.6C)
Dashboard health: HTTP 200
Dashboard camera status: HTTP 200 with local Bearer authentication
ZT6 read-only status: connected=true
```

## 当前数据集状态

当前仓库只有 2026-07-17 的 5 张未标注基线图，且只有一个场景，因此不能开始训练。下一次现场采集至少需要两个以上场景/日期组，并完成垃圾和车辆的 YOLO 标签。

## 申报材料事实素材（当前可用）

- 算法：已形成 YOLOv5 RTSP 推理、CUDA FP16、受控降帧、事件快照和“车辆检测 + 禁停 ROI + 停留时间”规则链路；当前实测证明的是工程链路和资源基线，不是垃圾/车辆识别精度。
- 平台：已形成 Jetson + ROS2 底盘/传感器接口、ZT6 可见光/红外视频接入、Dashboard、Agent/Server、云台控制和安全前置检查；模型、建图、导航均可独立停止，未授权时不会启动运动链路。
- 数据集：已确定 `garbage`、`vehicle` 两类第一版定义，具备带场景/日期/备注的 manifest、分组 train/val、YOLO 标注格式检查和训练闸门；当前仅有 5 张未标注单场景基线图，不能包装为已完成训练集。
- 待补证据：至少两个以上日期/场景组的现场数据、完整标签、训练曲线、验证集 Precision/Recall/mAP、Jetson 稳态 FPS 和误报/漏报案例。

## 公开数据集预研（仅作候选，不自动下载）

- [TACO 官方仓库](https://github.com/pedropro/TACO)：面向真实环境垃圾图像，提供 COCO 标注；适合补充垃圾类别和背景，但原始类别较细、数据规模较小，需映射到本项目的 `garbage`，并逐项核对图片来源许可。
- [BDD100K 官方仓库](https://github.com/bdd100k/bdd100k)：面向道路驾驶场景，包含 road object detection 等任务；适合车辆泛化预训练参考，但与 ZT6 车载视角、园区道路和目标尺寸存在域差异。
- [Open Images V7 官方说明](https://storage.googleapis.com/openimages/web/factsfigures_v7.html)：提供大规模目标框和类别层级，可筛选 `Vehicle` 相关类别；官方明确标注许可与图片许可需分别理解，并要求使用者自行核验每张图片的许可状态。
- 使用原则：不从搜索引擎随机抓图；下载时保存来源 URL、数据集版本、原始许可和转换日志；公开数据只用于预训练/补充，最终验证集必须来自本车 `video1` 采集场景。

## 下一步优先级

1. 现场采集 `gate_a_day`、`road_b_day`、`parking_c_day` 等可见光场景；
2. 用 CVAT/LabelImg/Roboflow 标注 `garbage` 和 `vehicle`；
3. 运行数据集校验并记录误标/漏标；
4. 训练 YOLOv5s 640 基线；
5. 单路 RTSP 推理 benchmark 后，再与 Dashboard、雷达/IMU、导航逐级叠加。

本次新增的底盘启动参数修复已保留在 `ros2/vehicle/turn_on_wheeltec_robot/launch/base_serial.launch.py`；实际底盘基础链路短测结束后已停止，当前不留底盘进程。

## 中午交付摘要（截至 10:42）

- 已完成：ROS/RTSP/YOLO 受控链路、Dashboard/Agent、停车规则、数据集闸门、服务稳定性和部署一致性核验；当前 34 项依赖轻量单测通过。
- 未完成：本车垃圾/车辆现场采集、标注、训练和 Precision/Recall/mAP/FPS 结果；当前数据不足以启动训练。
- 安全状态：底盘 stopped、`dry_run`、未 armed；无模型/ROS/运动残留。云台接口可用且当前 `motion_enabled=true`，无人值守前需人工复核该开关。
- 你回来后的直接顺序：用 `video1 + --preview` 采集至少 3 个场景 → 标注 `garbage/vehicle` → 按 manifest 分组准备并校验 → 再启动 YOLOv5s 640 基线训练。
- 首轮采集量建议：每个场景 3 段、每段 2–3 分钟；1 秒采样产生的相邻重复帧先筛掉，再把约 300–600 张送入标注，优先保证场景多样性而不是堆叠连续帧。

现场采集命令模板（反斜杠必须是行末最后一个字符）：

```bash
cd /home/nvidia/carmove
source /home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy/.venv/bin/activate
python tools/collect_rtsp_frames.py \
  --source rtsp://<CAMERA_HOST>:8554/video1 \
  --output datasets/local/roadside_v1/images/raw_gate_a_day \
  --interval 1.0 \
  --scene gate_a_day \
  --note "园区入口，白天，垃圾和车辆" \
  --preview
```
