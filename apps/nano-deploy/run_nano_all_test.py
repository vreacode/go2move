import sys
import os
import cv2
import torch
from detect_visual import (YOLOPredict)

# --- 配置区域 ---
# 你的思翼摄像头 RTSP 地址
RTSP_URL = os.environ.get("CAMERA_RTSP_URL", "")
# 如果没有摄像头，先填 '0' 用 USB 摄像头测试，或者填视频文件路径 'test_video.mp4'
SOURCE = RTSP_URL

MODELS = {
    '1': {'name': '安全帽检测', 'path': 'helmet.pt'},
    '2': {'name': '跌倒检测', 'path': 'fall.pt'},
    '3': {'name': '烟雾检测', 'path': 'smoke.pt'},
    '4': {'name': '危险区域', 'path': 'helmet.pt'}  # 复用安全帽模型检测人
}


def main():
    print("===================================")
    print("   Jetson Orin Nano 多功能巡检系统")
    print("===================================")
    print("请选择要启动的功能：")
    print("1. 安全帽佩戴识别")
    print("2. 异常行为(跌倒)检测")
    print("3. 烟雾/火灾检测")
    print("4. 危险区域入侵检测")

    choice = input("请输入序号 (1-4): ")

    if choice not in MODELS:
        print("无效输入！")
        return

    selected = MODELS[choice]
    print(f"正在加载 [{selected['name']}] 模型...")
    print(f"权重路径: {selected['path']}")

    # 初始化模型
    # Jetson 上建议使用 device='0' (GPU)
    # 如果报错，改回 'cpu'
    try:
        model = YOLOPredict(selected['path'], "inference/output")
        # 强制指定设备为 GPU (如果环境配好的话)
        #model.device = torch.device('cpu')
        print(f"使用设备: {model.device}")
    except Exception as e:
        print(f"模型加载失败: {e}")
        return

    print(f"正在连接摄像头: {SOURCE} ...")

    # --- 核心循环 ---
    # 这里我们简化处理：利用 detect_visual 的 detect 方法
    # 它支持直接传入 RTSP 地址或视频路径

    try:
        # view_img=True 会直接弹出窗口显示
        model.view_img = True
        model.detect(SOURCE)
    except KeyboardInterrupt:
        print("停止检测。")
    except Exception as e:
        print(f"运行出错: {e}")


if __name__ == "__main__":
    main()
