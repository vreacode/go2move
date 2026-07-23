import streamlit as st
import subprocess
import json
import shutil
import os
from pathlib import Path
from datetime import datetime
from streamlit_webrtc import webrtc_streamer, ClientSettings

# --- 1. 初始化和配置 ---

st.set_page_config(page_title="视频录制程序", layout="wide")
st.title("📹 实时视频录制与管理程序")
st.markdown("---")

# 定义常量
RECORD_DIR = Path("./rec")
RTSP_URLS = {
    'video1': os.environ.get('CAMERA_RTSP_VISIBLE_URL', ''),
    'video2': os.environ.get('CAMERA_RTSP_IR_URL', ''),
}
# 确保录制目录存在
RECORD_DIR.mkdir(exist_ok=True)

# 检查 ffmpeg 是否安装
if not shutil.which("ffmpeg"):
    st.error("错误：未在本系统中找到 ffmpeg。请先安装 ffmpeg 并确保它在系统的 PATH 中。")
    st.stop()
    
# 初始化 session_state 来跟踪录制状态和进程
if 'recorders' not in st.session_state:
    st.session_state.recorders = {}
    for video_id in RTSP_URLS:
        st.session_state.recorders[video_id] = {
            "is_recording": False,
            "process": None,
            "start_time": None,
            "output_path": None,
        }

# --- 2. 核心功能函数 ---

def start_recording(video_id: str):
    """开始录制指定的视频流"""
    if st.session_state.recorders[video_id]["is_recording"]:
        st.toast(f"{video_id} 已经在录制中。", icon="⚠️")
        return

    rtsp_url = RTSP_URLS[video_id]
    start_time = datetime.now()
    # 使用时间和 video_id 创建唯一的文件名
    filename = f"{video_id}_{start_time.strftime('%Y%m%d_%H%M%S')}.mp4"
    output_path = RECORD_DIR / filename
    
    # ffmpeg 命令:
    # -i {rtsp_url}: 指定输入源
    # -c copy: 直接复制流，不进行重新编码，效率最高
    # -y: 如果文件已存在则覆盖（虽然时间戳基本保证唯一性，但仍是好习惯）
    command = [
        "ffmpeg", "-i", rtsp_url, "-c", "copy", "-y", str(output_path)
    ]

    try:
        # 使用 subprocess.Popen 在后台启动 ffmpeg 进程
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        # 更新 session_state
        st.session_state.recorders[video_id].update({
            "is_recording": True,
            "process": process,
            "start_time": start_time,
            "output_path": output_path,
        })
        st.toast(f"✅ 开始录制 {video_id}...", icon="🔴")

    except FileNotFoundError:
        st.error(f"ffmpeg 命令未找到。请确保已正确安装并配置。")
    except Exception as e:
        st.error(f"启动录制 {video_id} 时出错: {e}")

def stop_recording(video_id: str):
    """停止录制并保存元数据"""
    recorder_state = st.session_state.recorders[video_id]
    if not recorder_state["is_recording"] or recorder_state["process"] is None:
        st.toast(f"{video_id} 并未在录制中。", icon="⚠️")
        return

    try:
        # 终止 ffmpeg 进程
        recorder_state["process"].terminate()
        # 等待进程完全结束
        recorder_state["process"].wait(timeout=5)
        st.toast(f"⏹️ 已停止录制 {video_id}。", icon="✅")

        # 保存元数据到 JSON 文件
        start_time = recorder_state["start_time"]
        output_path = recorder_state["output_path"]
        json_path = output_path.with_suffix(".json")
        
        metadata = {
            "video_id": video_id,
            "rtsp_url": RTSP_URLS[video_id],
            "start_time_iso": start_time.isoformat(),
            "start_time_readable": start_time.strftime('%Y-%m-%d %H:%M:%S'),
            "video_filename": output_path.name,
        }
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=4)

    except subprocess.TimeoutExpired:
        st.error(f"停止 {video_id} 进程超时，可能需要手动处理。")
    except Exception as e:
        st.error(f"停止录制 {video_id} 时出错: {e}")
    finally:
        # 清理 session_state
        st.session_state.recorders[video_id].update({
            "is_recording": False,
            "process": None,
            "start_time": None,
            "output_path": None,
        })

def get_recorded_videos():
    """获取所有已录制的视频及其元数据"""
    video_files = sorted(RECORD_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    video_data = []
    for video_path in video_files:
        json_path = video_path.with_suffix(".json")
        meta = {"start_time": "N/A", "size_mb": "N/A"}
        if json_path.exists():
            with open(json_path, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
                meta["start_time"] = json_data.get("start_time_readable", "N/A")
        
        size_bytes = video_path.stat().st_size
        meta["size_mb"] = f"{size_bytes / (1024*1024):.2f} MB"
        meta["path"] = video_path
        meta["name"] = video_path.name
        video_data.append(meta)
    return video_data

# --- 3. UI 界面布局 ---

# 全局控制按钮
st.header("全局控制")
is_any_recording = any(st.session_state.recorders[vid]["is_recording"] for vid in RTSP_URLS)

if is_any_recording:
    if st.button("⏹️ 同步停止所有录制", type="primary"):
        for vid in RTSP_URLS:
            if st.session_state.recorders[vid]["is_recording"]:
                stop_recording(vid)
        st.rerun()
else:
    if st.button("🔴 同步开始所有录制"):
        for vid in RTSP_URLS:
            if not st.session_state.recorders[vid]["is_recording"]:
                start_recording(vid)
        st.rerun()

st.markdown("---")

# 实时视频流和独立控制
st.header("实时视频流")
col1, col2 = st.columns(2)

webrtc_client_settings = ClientSettings(
    rtc_configuration={"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]},
    media_stream_constraints={"video": True, "audio": False},
)

for i, (video_id, url) in enumerate(RTSP_URLS.items()):
    container = col1 if i == 0 else col2
    with container:
        st.subheader(f"视频流: {video_id}")
        
        # 使用 streamlit-webrtc 显示实时流
        # 这部分处理了RTSP -> WebRTC 的转码和传输
        webrtc_streamer(
            key=f"webrtc_{video_id}",
            client_settings=webrtc_client_settings,
            # source_video_track=MediaPlayer(url).video, # 旧方法
            # 使用新的推荐方法
            rtsp_source=url
        )
        
        # 独立控制按钮
        is_recording = st.session_state.recorders[video_id]["is_recording"]
        btn_col1, btn_col2 = st.columns(2)
        with btn_col1:
            st.button(
                "开始录制", 
                key=f"start_{video_id}", 
                on_click=start_recording, 
                args=(video_id,), 
                disabled=is_recording
            )
        with btn_col2:
            st.button(
                "停止录制", 
                key=f"stop_{video_id}", 
                on_click=stop_recording, 
                args=(video_id,), 
                disabled=not is_recording,
                type="primary"
            )
        
        # 显示录制状态
        if is_recording:
            start_time = st.session_state.recorders[video_id]['start_time']
            elapsed = datetime.now() - start_time
            st.info(f"🔴 录制中... 已持续: {str(elapsed).split('.')[0]}", icon="🎥")


st.markdown("---")

# --- 4. 已保存视频管理 ---
st.header("已录制视频管理")

recorded_videos = get_recorded_videos()

if not recorded_videos:
    st.info("暂无已录制的视频。")
else:
    for video in recorded_videos:
        with st.expander(f"**{video['name']}** - `开始时间: {video['start_time']}` - `大小: {video['size_mb']}`"):
            
            c1, c2, c3 = st.columns([4, 1, 1])
            
            with c1:
                # 播放视频
                try:
                    video_bytes = video['path'].read_bytes()
                    st.video(video_bytes)
                except Exception as e:
                    st.error(f"无法加载视频文件：{e}")
            
            with c2:
                # 删除按钮
                if st.button("删除", key=f"delete_{video['name']}", type="secondary", use_container_width=True):
                    video['path'].unlink(missing_ok=True)
                    video['path'].with_suffix(".json").unlink(missing_ok=True)
                    st.toast(f"已删除 {video['name']}", icon="🗑️")
                    st.rerun() # 刷新列表
            
            with c3:
                # 下载按钮
                with open(video['path'], "rb") as file:
                    st.download_button(
                        label="下载",
                        data=file,
                        file_name=video['name'],
                        mime="video/mp4",
                        key=f"download_{video['name']}",
                        use_container_width=True
                    )
            
            # 修改文件名功能
            st.subheader("修改文件名")
            new_name_input = st.text_input("新文件名 (不含.mp4后缀)", key=f"rename_input_{video['name']}", placeholder=video['path'].stem)
            if st.button("确认重命名", key=f"rename_btn_{video['name']}"):
                if new_name_input:
                    new_path_mp4 = RECORD_DIR / f"{new_name_input}.mp4"
                    new_path_json = RECORD_DIR / f"{new_name_input}.json"
                    
                    if new_path_mp4.exists():
                        st.warning("文件名已存在，请换一个。")
                    else:
                        video['path'].rename(new_path_mp4)
                        video['path'].with_suffix(".json").rename(new_path_json)
                        st.toast(f"已重命名为 {new_name_input}.mp4", icon="✅")
                        st.rerun()
                else:
                    st.warning("请输入新的文件名。")
