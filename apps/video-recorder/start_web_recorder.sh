#!/bin/bash

# Web录制系统启动脚本（仅视频录制）

echo "启动Web录制系统（仅视频录制）..."

# 启动Web录制应用
echo "启动Web录制应用..."
cd ~/video_rec && python3 aaa.py &
WEB_APP_PID=$!

echo "Web录制应用启动成功，PID: $WEB_APP_PID"
echo ""
echo "系统已启动！"
echo "- Web应用 PID: $WEB_APP_PID"
echo "- Web界面地址: http://localhost:8000"
echo ""
echo "功能说明："
echo "- 视频文件将保存到 /home/nvidia/video_rec/rec/ 目录"
echo ""
echo "按 Ctrl+C 停止所有服务"

# 等待用户中断
wait 
