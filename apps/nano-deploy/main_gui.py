import sys
import cv2
import torch
import numpy as np
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *

# 导入你现有的类
from detect_visual import YOLOPredict


# --- 1. 核心桥梁：检测线程 ---
class DetectionThread(QThread):
    # 定义一个信号，用于把检测后的图片传给界面显示
    change_pixmap_signal = pyqtSignal(np.ndarray)
    log_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.weights = ""
        self.source = ""  # configure a verified source through the deployment environment
        self.running = True

    def set_task(self, weights, source):
        self.weights = weights
        self.source = source

    def run(self):
        try:
            # 实例化你的 YOLOPredict
            # 注意：这里我们关闭 view_img，因为我们要把图传给 PyQt 界面显示
            predictor = YOLOPredict(self.weights, "inference/output")
            predictor.view_img = False

            # 参考你 detect_visual.py 里的逻辑
            webcam = self.source == '0' or self.source.startswith(('rtsp', 'http', 'https'))

            # 这里由于要实时控制界面，我们需要手动拆解 predictor.detect 的内部循环
            # 为了演示方便，我们重新初始化 dataset
            from utils.dataloaders import LoadStreams, LoadImages
            if webcam:
                dataset = LoadStreams(self.source, img_size=predictor.img_size)
            else:
                dataset = LoadImages(self.source, img_size=predictor.img_size)

            self.log_signal.emit(f"模型加载成功，正在分析流: {self.source}")

            for path, img, im0s, vid_cap, s in dataset:
                if not self.running: break

                # 你的原有检测逻辑...
                img = torch.from_numpy(img).to(predictor.device)
                img = img.half() if predictor.half else img.float()
                img /= 255.0
                if img.ndimension() == 3: img = img.unsqueeze(0)

                pred = predictor.model(img, augment=predictor.augment)[0]
                from utils.general import non_max_suppression, scale_coords
                from utils.plots import plot_one_box

                pred = non_max_suppression(pred, predictor.conf_thres, predictor.iou_thres)

                for i, det in enumerate(pred):
                    im0 = im0s[i].copy() if webcam else im0s.copy()
                    if len(det):
                        det[:, :4] = scale_coords(img.shape[2:], det[:, :4], im0.shape).round()
                        for *xyxy, conf, cls in reversed(det):
                            label = f'{predictor.names[int(cls)]} {conf:.2f}'
                            plot_one_box(xyxy, im0, label=label, color=predictor.colors[int(cls)], line_thickness=3)

                    # 【关键点】不调用 cv2.imshow，而是发射信号
                    self.change_pixmap_signal.emit(im0)

        except Exception as e:
            self.log_signal.emit(f"错误: {str(e)}")

    def stop(self):
        self.running = False
        self.wait()


# --- 2. 界面展示类 (基于之前的 UI 代码) ---
class SmartPatrolGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("智巡卫士 - 工业安全机器人监控平台")
        self.resize(1300, 800)
        self.init_ui()

        # 初始化检测线程
        self.det_thread = DetectionThread()
        self.det_thread.change_pixmap_signal.connect(self.update_image)
        self.det_thread.log_signal.connect(self.add_log)

    def init_ui(self):
        # 这里的 QSS 样式表参考我之前给你的“深色科技感”
        self.setStyleSheet("QMainWindow { background-color: #0b0e14; color: white; }")

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QHBoxLayout(main_widget)

        # 左侧控制栏
        left_bar = QVBoxLayout()
        title = QLabel("智巡卫士 AI 控制台")
        title.setStyleSheet("font-size: 20px; color: #58a6ff; font-weight: bold;")
        left_bar.addWidget(title)

        self.btn_helmet = QPushButton("⛑️ 安全帽检测")
        self.btn_smoke = QPushButton("🚬 烟雾吸烟检测")
        self.btn_fall = QPushButton("👤 跌倒行为检测")

        for btn in [self.btn_helmet, self.btn_smoke, self.btn_fall]:
            btn.setFixedSize(200, 50)
            btn.setStyleSheet("background-color: #21262d; color: white; border-radius: 5px;")
            left_bar.addWidget(btn)

        self.btn_helmet.clicked.connect(lambda: self.start_detection("helmet.pt"))
        self.btn_smoke.clicked.connect(lambda: self.start_detection("smoke.pt"))
        self.btn_fall.clicked.connect(lambda: self.start_detection("fall.pt"))

        left_bar.addStretch()
        layout.addLayout(left_bar)

        # 中间视频区
        self.video_label = QLabel("等待视频流接入...")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet("background-color: black; border: 2px solid #30363d;")
        layout.addWidget(self.video_label, stretch=3)

        # 右侧日志区
        self.log_list = QListWidget()
        self.log_list.setStyleSheet("background-color: #0d1117; color: #8b949e;")
        layout.addWidget(self.log_list, stretch=1)

    def start_detection(self, weight_file):
        if self.det_thread.isRunning():
            self.det_thread.stop()

        self.add_log(f"正在切换模型: {weight_file}")
        # 实际地址根据你的 Nano 配置修改
        self.det_thread.set_task(weight_file, self.det_thread.source)
        self.det_thread.running = True
        self.det_thread.start()

    @pyqtSlot(np.ndarray)
    def update_image(self, cv_img):
        # 将 OpenCV 的 BGR 格式转换为 PyQt 的 QPixmap
        rgb_image = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        bytes_per_line = ch * w
        convert_to_Qt_format = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format_RGB888)
        p = convert_to_Qt_format.scaled(self.video_label.width(), self.video_label.height(), Qt.KeepAspectRatio)
        self.video_label.setPixmap(QPixmap.fromImage(p))

    def add_log(self, text):
        self.log_list.insertItem(0, f"[{QTime.currentTime().toString()}] {text}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SmartPatrolGUI()
    window.show()
    sys.exit(app.exec_())
