import asyncio
import datetime
import json
import logging
import subprocess
import threading
import time
import os
from pathlib import Path
from typing import Dict, List, TextIO

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# --- 配置 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 路径设置
BASE_DIR = Path(__file__).parent.resolve()
REC_DIR = BASE_DIR / "rec"
LIVE_DIR = BASE_DIR / "live"
TEMPLATES_DIR = BASE_DIR / "templates"

# 确保目录存在
REC_DIR.mkdir(exist_ok=True)
LIVE_DIR.mkdir(exist_ok=True)

# RTSP流配置
RTSP_STREAMS = {
    'video1': os.environ.get('CAMERA_RTSP_VISIBLE_URL', ''),
    'video2': os.environ.get('CAMERA_RTSP_IR_URL', ''),
    # 你可以添加更多，或使用假的URL进行测试，例如：
    # 'test_stream': 'rtsp://wowzaec2demo.streamlock.net/vod/mp4:BigBuckBunny_115k.mov'
}

# --- 状态管理 ---
# 使用字典来存储正在运行的进程
# Popen对象适用于需要交互或管理的长时间运行的子进程
recording_processes: Dict[str, subprocess.Popen] = {}
recording_info: Dict[str, Dict] = {}
live_stream_processes: Dict[str, subprocess.Popen] = {}
live_stream_log_handles: Dict[str, TextIO] = {}
live_stream_log_paths: Dict[str, Path] = {}

# --- 核心功能函数 ---

def start_live_stream(stream_name: str, rtsp_url: str):
    """为指定的RTSP流启动一个ffmpeg进程，生成低延迟HLS流"""
    live_output_dir = LIVE_DIR / stream_name
    live_output_dir.mkdir(exist_ok=True)
    
    # 清理旧的直播片段
    for item in live_output_dir.iterdir():
        item.unlink()

    command = [
        "ffmpeg",
        "-loglevel", "warning",
        "-rtsp_transport", "tcp",
        "-stimeout", "5000000",
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-analyzeduration", "1000000",
        "-probesize", "1000000",
        "-i", rtsp_url,
        "-map", "0:v:0",
        "-an",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-g", "30",
        "-keyint_min", "30",
        "-sc_threshold", "0",
        "-pix_fmt", "yuv420p",
        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "5",
        "-hls_flags", "delete_segments+independent_segments+omit_endlist",
        "-hls_allow_cache", "0",
        "-hls_segment_type", "mpegts",
        "-hls_segment_filename", str(live_output_dir / "stream%d.ts"),
        "-reconnect", "1",
        "-reconnect_at_eof", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        str(live_output_dir / "stream.m3u8"),
    ]
    logging.info(f"Starting live stream for '{stream_name}': {' '.join(command)}")

    log_path = live_output_dir / "ffmpeg.log"
    log_handle = open(log_path, "a", encoding="utf-8")
    live_stream_log_handles[stream_name] = log_handle
    live_stream_log_paths[stream_name] = log_path

    # 启动进程，写日志到文件，避免stderr阻塞
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=log_handle,
    )
    live_stream_processes[stream_name] = process
    
    # 启动监控线程
    monitor_thread = threading.Thread(target=monitor_live_stream, args=(stream_name, process))
    monitor_thread.daemon = True
    monitor_thread.start()
    
    logging.info(f"Live stream for '{stream_name}' started with PID {process.pid}.")

def monitor_live_stream(stream_name: str, process: subprocess.Popen):
    """监控直播流进程状态"""
    while process.poll() is None:
        time.sleep(5)
        # 检查进程是否还在运行
        if process.poll() is not None:
            break
            
        # 检查是否有新的分片文件生成
        live_output_dir = LIVE_DIR / stream_name
        ts_files = list(live_output_dir.glob('*.ts'))
        if ts_files:
            # 获取最新文件的修改时间
            latest_file = max(ts_files, key=lambda p: p.stat().st_mtime)
            last_modified = time.time() - latest_file.stat().st_mtime
            
            # 如果最新文件超过10秒没更新，可能有问题
            if last_modified > 10:
                logging.warning(f"Live stream '{stream_name}' may be stalled. Last segment was {last_modified:.1f}s ago.")
    
    # 进程结束时的处理
    if process.poll() is not None:
        return_code = process.poll()
        log_path = live_stream_log_paths.get(stream_name)
        if return_code != 0:
            logging.error(f"Live stream '{stream_name}' process exited with code {return_code}")
            if log_path:
                logging.error(f"FFmpeg log saved to: {log_path}")
        else:
            logging.info(f"Live stream '{stream_name}' process ended normally")

def check_live_stream_health(stream_name: str) -> bool:
    """检查直播流是否健康"""
    if stream_name not in live_stream_processes:
        return False
        
    process = live_stream_processes[stream_name]
    if process.poll() is not None:
        return False
        
    # 检查是否有新的HLS文件
    live_output_dir = LIVE_DIR / stream_name
    m3u8_file = live_output_dir / 'stream.m3u8'
    
    if not m3u8_file.exists():
        return False
        
    # 检查m3u8文件是否在最近更新过
    last_modified = time.time() - m3u8_file.stat().st_mtime
    return last_modified < 10  # 10秒内有更新认为是健康的

def stop_all_processes():
    """停止所有进程"""
    # 停止视频录制
    for name in list(recording_processes.keys()):
        process = recording_processes[name]
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
    
    # 停止直播流
    for name in list(live_stream_processes.keys()):
        process = live_stream_processes[name]
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        log_handle = live_stream_log_handles.pop(name, None)
        if log_handle:
            log_handle.close()

# --- FastAPI应用实例 ---
app = FastAPI(title="Web Video Recorder")

# Allow cross-origin HLS playback from the Windows frontend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件目录
app.mount("/rec", StaticFiles(directory=REC_DIR), name="rec")
app.mount("/live", StaticFiles(directory=LIVE_DIR), name="live")
# 添加静态资源目录挂载
app.mount("/static", StaticFiles(directory=TEMPLATES_DIR), name="static")

# 设置Jinja2模板
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# --- FastAPI生命周期事件 ---

@app.on_event("startup")
async def startup_event():
    """应用启动时，为所有配置的RTSP流开启HLS直播"""
    logging.info("Application startup: Starting all live streams.")
    for name, url in RTSP_STREAMS.items():
        try:
            start_live_stream(name, url)
            # 给ffmpeg一些时间来初始化
            await asyncio.sleep(1)
        except Exception as e:
            logging.error(f"Failed to start live stream for {name}: {e}")

@app.on_event("shutdown")
def shutdown_event():
    """应用关闭时，清理所有子进程"""
    logging.info("Application shutdown: Stopping all processes.")
    stop_all_processes()

# --- API 端点 ---

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """提供主页面"""
    return templates.TemplateResponse("index.html", {
        "request": request,
        "streams": RTSP_STREAMS,
    })

@app.get("/api/status")
async def get_status():
    """获取所有流的当前状态"""
    status = {}
    for name in RTSP_STREAMS.keys():
        is_recording = name in recording_processes and recording_processes[name].poll() is None
        is_live_healthy = check_live_stream_health(name)
        
        status[name] = {
            "recording": is_recording,
            "live_online": is_live_healthy,
        }
    
    return JSONResponse(content=status)

# --- 录制控制API ---

@app.post("/api/record/start/{stream_name}")
async def start_recording(stream_name: str):
    """开始录制指定的视频流"""
    if stream_name not in RTSP_STREAMS:
        raise HTTPException(status_code=404, detail="Stream not found")
    if stream_name in recording_processes:
        raise HTTPException(status_code=400, detail="Already recording")

    now = datetime.datetime.now()
    filename = f"{stream_name}_{now.strftime('%Y%m%d_%H%M%S')}.mp4"
    output_path = REC_DIR / filename
    
    # 录制时直接用 RTSP 源
    rtsp_url = RTSP_STREAMS[stream_name]
    command = [
        "ffmpeg",
        "-rtsp_transport", "tcp",
        "-i", rtsp_url,
        "-c", "copy",
        "-fflags", "+genpts",
        str(output_path),
    ]

    logging.info(f"Starting recording for '{stream_name}': {' '.join(command)}")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    recording_processes[stream_name] = process
    recording_info[stream_name] = {
        "start_time": now.isoformat(),
        "output_path": str(output_path),
        "filename": filename
    }
    
    return JSONResponse(content={
        "status": "Recording started",
        "filename": filename,
    })

@app.post("/api/record/stop/{stream_name}")
async def stop_recording(stream_name: str):
    """停止录制指定的视频流"""
    if stream_name not in recording_processes:
        raise HTTPException(status_code=404, detail="Not currently recording this stream")

    process = recording_processes.pop(stream_name)
    info = recording_info.pop(stream_name)

    logging.info(f"Stopping recording for '{stream_name}' (PID: {process.pid})")
    process.terminate()
    try:
        process.wait(timeout=10)  # 等待ffmpeg完成文件写入
    except subprocess.TimeoutExpired:
        logging.warning(f"ffmpeg for {stream_name} did not terminate gracefully. Killing.")
        process.kill()

    # 写入JSON元数据文件
    json_path = Path(info["output_path"]).with_suffix(".json")
    with open(json_path, 'w') as f:
        json.dump({"start_time": info["start_time"]}, f, indent=2)

    logging.info(f"Recording stopped for '{stream_name}'. Video saved to {info['filename']}")
    return JSONResponse(content={
        "status": "Recording stopped",
        "filename": info["filename"],
    })

@app.post("/api/record/start_all")
async def start_all_recordings():
    """同时开始所有流的录制"""
    results = {}

    # 启动视频录制
    for name in RTSP_STREAMS.keys():
        if name not in recording_processes:
            try:
                await start_recording(name)
                results[name] = "Started"
            except Exception as e:
                results[name] = f"Failed: {e}"
        else:
            results[name] = "Already recording"
    
    return JSONResponse(content=results)

@app.post("/api/record/stop_all")
async def stop_all_recordings():
    """同时停止所有流的录制"""
    results = {}

    # 停止视频录制
    for name in list(recording_processes.keys()):
        try:
            await stop_recording(name)
            results[name] = "Stopped"
        except Exception as e:
            results[name] = f"Failed to stop: {e}"
    
    return JSONResponse(content=results)

# --- 文件管理CRUD API ---

@app.get("/api/videos", response_model=List[dict])
async def list_videos():
    """获取所有已录制的视频列表"""
    videos = []
    for video_file in sorted(REC_DIR.glob('*.mp4'), key=lambda p: p.stat().st_mtime, reverse=True):
        json_file = video_file.with_suffix(".json")
        meta = {"start_time": None}
        if json_file.exists():
            with open(json_file, 'r') as f:
                try:
                    meta = json.load(f)
                except json.JSONDecodeError:
                    logging.warning(f"Could not decode JSON for {video_file.name}")
        
        videos.append({
            "filename": video_file.name,
            "url": f"/rec/{video_file.name}",
            "size": video_file.stat().st_size,
            "start_time": meta.get("start_time"),
            "creation_time": datetime.datetime.fromtimestamp(video_file.stat().st_ctime).isoformat()
        })
    return videos

class RenameRequest(BaseModel):
    new_filename: str

@app.put("/api/videos/{filename}")
async def rename_video(filename: str, body: RenameRequest):
    """重命名视频文件和其关联的JSON文件"""
    if '..' in filename or '/' in filename or '..' in body.new_filename or '/' in body.new_filename:
        raise HTTPException(status_code=400, detail="Invalid filename.")
        
    old_video_path = REC_DIR / filename
    old_json_path = old_video_path.with_suffix(".json")
    
    if not old_video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found.")

    new_video_name = body.new_filename
    if not new_video_name.endswith('.mp4'):
        new_video_name += '.mp4'
        
    new_video_path = REC_DIR / new_video_name
    new_json_path = new_video_path.with_suffix(".json")

    if new_video_path.exists():
        raise HTTPException(status_code=400, detail="New filename already exists.")

    try:
        old_video_path.rename(new_video_path)
        if old_json_path.exists():
            old_json_path.rename(new_json_path)
        logging.info(f"Renamed '{filename}' to '{new_video_name}'")
        return JSONResponse(content={"status": "Renamed successfully"})
    except Exception as e:
        logging.error(f"Error renaming file: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/videos/{filename}")
async def delete_video(filename: str):
    """删除视频文件和其关联的JSON文件"""
    if '..' in filename or '/' in filename:
        raise HTTPException(status_code=400, detail="Invalid filename.")
        
    video_path = REC_DIR / filename
    json_path = video_path.with_suffix(".json")

    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found.")

    try:
        video_path.unlink()
        if json_path.exists():
            json_path.unlink()
        logging.info(f"Deleted '{filename}' and its JSON file.")
        return JSONResponse(content={"status": "Deleted successfully"})
    except Exception as e:
        logging.error(f"Error deleting file: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    # 注意：热重载(reload=True)在Windows上与subprocess管理可能存在问题
    # 在生产或稳定测试时，建议使用 reload=False
    uvicorn.run(app, host="0.0.0.0", port=8000)
