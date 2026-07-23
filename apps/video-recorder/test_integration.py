#!/usr/bin/env python3
"""
测试Web视频录制系统功能
"""

import requests
import sys
import time

# 配置
WEB_BASE_URL = "http://localhost:8000"

def test_web_server():
    """测试Web服务器是否运行"""
    try:
        response = requests.get(f"{WEB_BASE_URL}/api/status", timeout=5)
        if response.status_code == 200:
            print("✅ Web服务器运行正常")
            return True
        else:
            print(f"❌ Web服务器响应异常: {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"❌ 无法连接到Web服务器: {e}")
        return False

def test_video_recording():
    """测试视频录制功能"""
    print("\n=== 测试视频录制功能 ===")
    
    # 开始录制（会同时录制视频和雷达）
    try:
        response = requests.post(f"{WEB_BASE_URL}/api/record/start/video1", timeout=10)
        if response.status_code == 200:
            result = response.json()
            print("✅ 录制开始成功")
            print(f"   视频录制: {result.get('filename', 'N/A')}")
        else:
            print(f"❌ 同步录制开始失败: {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"❌ 同步录制开始请求失败: {e}")
        return False
    
    # 等待录制进行
    print("录制进行中...")
    time.sleep(5)
    
    # 停止录制
    try:
        response = requests.post(f"{WEB_BASE_URL}/api/record/stop/video1", timeout=10)
        if response.status_code == 200:
            result = response.json()
            print("✅ 录制停止成功")
            print(f"   视频录制: {result.get('filename', 'N/A')}")
        else:
            print(f"❌ 同步录制停止失败: {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"❌ 同步录制停止请求失败: {e}")
        return False
    
    return True

def test_api_endpoints():
    """测试API端点"""
    print("\n=== 测试API端点 ===")
    
    endpoints = [
        ("GET", "/api/status", "状态查询"),
        ("GET", "/api/videos", "视频文件列表"),
    ]
    
    for method, endpoint, description in endpoints:
        try:
            response = requests.request(method, f"{WEB_BASE_URL}{endpoint}", timeout=5)
            if response.status_code == 200:
                print(f"✅ {description} - {method} {endpoint}")
            else:
                print(f"❌ {description} - {method} {endpoint} (状态码: {response.status_code})")
        except requests.exceptions.RequestException as e:
            print(f"❌ {description} - {method} {endpoint} (错误: {e})")

def main():
    """主测试函数"""
    print("=== Web视频录制系统测试 ===")
    
    # 测试Web服务器
    if not test_web_server():
        print("\n❌ Web服务器测试失败，请确保系统已启动")
        sys.exit(1)
    
    # 测试API端点
    test_api_endpoints()
    
    # 测试视频录制
    if test_video_recording():
        print("\n✅ 视频录制测试通过")
    else:
        print("\n❌ 视频录制测试失败")
    
    print("\n=== 测试完成 ===")
    print("如果所有测试都通过，说明视频录制功能正常工作！")

if __name__ == "__main__":
    main() 
