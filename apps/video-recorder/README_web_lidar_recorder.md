# Web视频录制系统

这是一个用于RTSP视频录制的Web应用系统，提供低延迟预览与文件管理。

## 功能特性

### 视频录制功能
- 支持多个RTSP视频流同时录制
- 实时HLS直播流
- 视频文件管理（重命名、删除）
- Web界面控制

### 核心特性
- 低延迟HLS直播预览
- 一键开始/停止录制
- 自动文件命名和时间戳记录
- 实时状态监控

## 系统架构

```
Web界面 (FastAPI) 
    ↓
视频录制 (FFmpeg)
    ↓
视频文件 (.mp4)
```

## 安装和配置

### 1. 依赖安装

```bash
# 安装Python依赖
pip3 install fastapi uvicorn jinja2

#
# 仅需Python依赖即可运行
```

### 2. 文件结构

```
/home/nvidia/video_rec/
├── aaa.py                    # 主Web应用
├── start_web_recorder.sh     # 启动脚本
├── templates/                # Web模板
├── rec/                      # 视频录制目录
├── live/                     # 直播流目录
└── README_web_lidar_recorder.md
```

## 使用方法

### 1. 启动系统

```bash
cd /home/nvidia/video_rec
./start_web_recorder.sh
```

启动脚本会自动：
- 启动Web录制应用
- 显示访问地址

### 2. 访问Web界面

打开浏览器访问：`http://localhost:8000`

### 3. 录制操作

#### 单个流录制
1. 在Web界面点击对应流的"开始录制"按钮
2. 点击"停止录制"按钮停止录制

#### 批量录制
1. 点击"开始所有录制"同时录制所有流
2. 点击"停止所有录制"停止所有录制

## API接口

### 状态查询
```bash
GET /api/status
```
返回所有流的录制状态

### 录制控制
```bash
# 开始单个流录制
POST /api/record/start/{stream_name}

# 停止单个流录制
POST /api/record/stop/{stream_name}

# 开始所有录制
POST /api/record/start_all

# 停止所有录制
POST /api/record/stop_all
```

### 文件管理
```bash
# 获取视频文件列表
GET /api/videos

# 重命名视频文件
PUT /api/videos/{filename}

# 删除视频文件
DELETE /api/videos/{filename}
```

## 文件存储

### 视频文件
- 位置：`/home/nvidia/video_rec/rec/`
- 格式：`.mp4`
- 命名：`{stream_name}_{timestamp}.mp4`

### 元数据文件
- 位置：与视频文件同目录
- 格式：`.json`
- 包含录制开始时间等信息

## 配置说明

### RTSP流配置
在 `aaa.py` 中修改 `RTSP_STREAMS` 字典：

```python
RTSP_STREAMS = {
    'video1': 'rtsp://<CAMERA_HOST>:8554/video1',
    'video2': 'rtsp://<CAMERA_HOST>:8554/video2',
    # 添加更多流...
}
```

## 故障排除

### 1. 视频录制失败
- 检查RTSP流地址是否正确
- 确认网络连接正常
- 检查FFmpeg是否安装

### 2. Web界面无法访问
- 确认端口8000未被占用
- 检查防火墙设置
- 查看应用日志

### 3. 文件权限问题
```bash
# 确保目录有写入权限
chmod 755 /home/nvidia/video_rec/rec
```

## 日志查看

### 应用日志
Web应用会在控制台输出详细日志，包括：
- 录制开始/停止状态
- 错误信息

## 扩展功能

### 1. 添加新的视频流
1. 在 `RTSP_STREAMS` 中添加新流
2. 重启Web应用

### 2. 修改录制参数
- 视频编码参数在 `start_recording` 函数中

### 3. 自定义Web界面
- 修改 `templates/index.html`
- 添加新的JavaScript功能

## 注意事项

1. **资源占用**：同时录制多个流会占用较多系统资源
2. **存储空间**：确保有足够的磁盘空间存储录制文件
3. **网络带宽**：RTSP流需要稳定的网络连接
4. **时间同步**：视频的时间戳会自动记录到JSON文件

## 技术支持

如遇到问题，请检查：
1. 系统日志
2. 网络连接
3. 文件权限
4. 应用启动日志 
