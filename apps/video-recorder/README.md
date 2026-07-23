我需要你使用streamlit帮我制作一个带有webui的视频录制程序。
这个录制程序使用ffmpeg 来录制rtsp视频流，保存到./rec文件夹中，并在webui中能够实时查看视频流。
界面要求如下：
1. 能够同时实时查看两个视频流，并提供开始，停止录制按钮，停止录制后自动将视频保存（并使用和视频文件名相同的json文件来记录视频开始录制的时间）。
2. 提供查看已保存的视频，并支持对已保存视频的增删改查。
处理路径相关的需要使用pathlib，处理时间相关的需要使用datetime库，不要使用time。
默认rtsp流如下：
'video1': 'rtsp://<CAMERA_HOST>:8554/video1'
'video2': 'rtsp://<CAMERA_HOST>:8554/video2'
