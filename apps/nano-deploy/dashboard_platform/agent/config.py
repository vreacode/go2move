from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelEntry(BaseModel):
    id: str
    label: str
    weight: str


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "nano-board-agent"
    agent_host: str = "0.0.0.0"
    agent_port: int = 9001
    agent_token: str = "change-me-agent-token"
    board_name: str = "jetson-nano"
    device_profile: str = "generic"
    go2_data_only: bool = False

    camera_rtsp_visible_url: str = ""
    camera_rtsp_ir_url: str = ""
    camera_rtsp_url: str = ""
    camera_source_type: str = "opencv"
    capture_script: Path = Path("/home/nvidia/carmove/tools/collect_rtsp_frames.py")
    capture_workdir: Path = Path("/home/nvidia/carmove")
    capture_output_root: Path = Path("/home/nvidia/carmove/datasets/local/roadside_v1")
    capture_python_bin: str = ""
    battery_enabled: bool = True
    battery_voltage_topic: str = "/PowerVoltage"
    battery_charging_topic: str = "/robot_charging_flag"
    battery_current_topic: str = "/robot_charging_current"
    battery_ros_setup_bash: str = "/opt/ros/humble/setup.bash"
    battery_python_bin: str = "python3"
    battery_stale_seconds: float = 10.0
    battery_empty_voltage: float = 20.0
    battery_full_voltage: float = 22.6
    battery_low_voltage: float = 20.0
    battery_low_percentage: float = 20.0
    battery_filter_window: int = 5
    battery_low_confirm_samples: int = 3
    battery_direct_serial_enabled: bool = True
    battery_serial_port: str = "/dev/ttyCH343USB0"
    battery_serial_baudrate: int = 115200
    siyi_zt6_enabled: bool = True
    siyi_zt6_host: str = ""
    siyi_zt6_port: int = 37260
    siyi_zt6_query_timeout_seconds: float = 1.0
    siyi_zt6_motion_enabled: bool = False
    siyi_zt6_motion_max_speed: int = 10
    siyi_zt6_motion_max_duration_ms: int = 500
    allow_mjpeg_fallback: bool = False
    webrtc_ice_servers: str = "[]"
    webrtc_output_width: int = 960
    webrtc_output_height: int = 540
    webrtc_output_fps: float = 12.0
    webrtc_preferred_codec: str = "H264"
    webrtc_stale_frame_ms: int = 1500
    webrtc_capture_open_timeout_ms: int = 3000

    model_workdir: Path = Path("/home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy")
    model_python_bin: str = ""
    model_venv_activate: str = ""
    model_script: str = "nano_rtsp_detect.py"
    model_output_dir: str = "inference/output"
    model_snapshot_dir: str = "dashboard_platform/runtime/snapshots"
    model_snapshot_interval: float = 0.1
    model_snapshot_max_size: int = 960
    model_default_source: str = ""
    model_default_vid_stride: int = 5
    model_default_img_size: int = 640
    model_default_conf: float = 0.4
    model_default_iou: float = 0.5
    model_default_device: str = ""
    model_default_half: bool = False
    model_extra_args: str = "[]"
    available_models: str = "[]"

    control_command_map: str = "{}"
    preset_commands: str = "{}"
    enable_motion_control: bool = False
    motion_control_mode: str = "dry_run"
    # Manual commands go through the same final base-input topic as Nav2.
    # Nav2 raw output remains /cmd_vel_nav; velocity_smoother -> collision_monitor
    # produces /cmd_vel_safe.
    cmd_vel_topic: str = "/cmd_vel_safe"
    max_linear_vel_safe: float = 0.05
    max_angular_vel_safe: float = 0.2
    control_command_timeout_ms: int = 300
    require_manual_arm: bool = True
    disable_manual_when_nav_running: bool = True
    base_start_script: str = "/home/nvidia/Desktop/wheeltec_ros2/start_base.sh"
    base_cmd_vel_topic: str = "/cmd_vel_safe"
    base_odom_topic: str = "/odom"
    base_node_name: str = "/wheeltec_robot"
    base_bringup_start_timeout_seconds: float = 10.0
    base_status_timeout_seconds: float = 4.0
    base_refresh_cache_seconds: float = 2.5
    base_monitor_interval_seconds: float = 2.0
    log_buffer_size: int = 500
    pid_file: Path = Path("dashboard_platform/runtime/model.pid")
    sensor_buffer_size: int = 50
    maps_directory: Path = Path("/home/nvidia/Desktop/wheeltec_ros2/maps")
    default_map_name: str = "gmap_map"
    events_directory: Path = Path("/home/nvidia/Desktop/wheeltec_ros2/shijue/Nano_Deploy/events")
    recording_enabled: bool = True
    recording_workdir: Path = Path("/home/nvidia/video_rec")
    recording_output_dir: Path = Path("/home/nvidia/video_rec/rec")
    snapshot_output_dir: Path = Path("dashboard_platform/runtime/media/snapshots")
    gallery_enabled: bool = True
    gallery_page_size: int = 20
    recording_use_legacy_web_recorder: bool = False
    recording_use_ffmpeg_direct: bool = True
    dev_screen_recording_enabled: bool = True
    dev_screen_recording_backend: str = "auto"
    dev_screen_recording_output_dir: Path = Path("dashboard_platform/runtime/media/screen_recordings")
    dev_screen_recording_fps: int = 10
    dev_screen_recording_width: int = 1280
    dev_screen_recording_height: int = 720
    dev_screen_recording_display: str = ""
    dev_screen_recording_xauthority: str = ""
    dev_screen_recording_bitrate_kbps: int = 1800
    dev_screen_recording_show_pointer: bool = True
    dev_screen_recording_use_damage: bool = False

    heartbeat_interval_seconds: float = 2.0
    status_cache_seconds: float = 1.0

    lidar_enabled: bool = True
    lidar_ip: str = ""
    lidar_msop_port: int = 2363
    lidar_difop_port: int = 2364
    lidar_point_topic: str = "/c16/lslidar_point_cloud"
    imu_enabled: bool = True
    imu_topic: str = "/imu/data"
    imu_raw_topic: str = "/imu/data_raw"
    ros2_lidar_setup_bash: str = "/home/nvidia/fastlio2_ws/install/setup.bash"
    ros2_imu_setup_bash: str = "/home/nvidia/serial-ros2/install/setup.bash;/home/nvidia/imu-driver/ROS2 SDK/yesense_ros2/install/setup.bash"
    ros2_bridge_python_bin: str = "python3"
    sensor_bridge_enabled: bool = False
    pointcloud_max_points: int = 600
    pointcloud_send_hz: float = 5.0
    pointcloud_keep_seconds: float = 1.0
    lidar_to_base_translation: str = "[0.0, 0.0, 0.0]"
    lidar_to_base_rotation_rpy: str = "[0.0, 0.0, 0.0]"
    imu_to_base_rotation_rpy: str = "[0.0, 0.0, 0.0]"
    robot_pose_enabled: bool = True
    robot_pose_map_frame: str = "map"
    robot_pose_base_frame: str = "base_footprint"
    robot_pose_fallback_base_frame: str = "base_link"
    robot_pose_amcl_topic: str = "/amcl_pose"
    car_model_forward_axis: str = "positive_z"
    car_model_up_axis: str = "positive_y"
    pointcloud_history_seconds: float = 0.75

    def parse_json(self, raw: str, fallback: Any) -> Any:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return fallback

    @property
    def models(self) -> list[ModelEntry]:
        parsed = self.parse_json(self.available_models, [])
        return [ModelEntry.model_validate(item) for item in parsed]

    @property
    def visible_rtsp_url(self) -> str:
        return self.camera_rtsp_visible_url or self.camera_rtsp_url or self.model_default_source

    @property
    def ir_rtsp_url(self) -> str:
        return self.camera_rtsp_ir_url

    @property
    def control_commands(self) -> dict[str, list[str]]:
        parsed = self.parse_json(self.control_command_map, {})
        return {k: [str(v) for v in values] for k, values in parsed.items()}

    @property
    def motion_real_enabled(self) -> bool:
        return self.enable_motion_control and self.motion_control_mode == "real"

    @property
    def preset_command_map(self) -> dict[str, list[str]]:
        parsed = self.parse_json(self.preset_commands, {})
        return {k: [str(v) for v in values] for k, values in parsed.items()}

    @property
    def extra_args(self) -> list[str]:
        return [str(v) for v in self.parse_json(self.model_extra_args, [])]

    @property
    def python_bin(self) -> str:
        if self.model_python_bin:
            return self.model_python_bin
        if self.model_venv_activate:
            activate = Path(self.model_venv_activate)
            return str(activate.parent / "python")
        return "python3"

    @property
    def snapshot_path(self) -> Path:
        return (self.model_workdir / self.model_snapshot_dir).resolve()

    @property
    def pid_path(self) -> Path:
        if self.pid_file.is_absolute():
            return self.pid_file
        return (self.model_workdir / self.pid_file).resolve()

    @property
    def media_snapshot_path(self) -> Path:
        if self.snapshot_output_dir.is_absolute():
            return self.snapshot_output_dir
        return (self.model_workdir / self.snapshot_output_dir).resolve()

    @property
    def dev_screen_recording_path(self) -> Path:
        if self.dev_screen_recording_output_dir.is_absolute():
            return self.dev_screen_recording_output_dir
        return (self.model_workdir / self.dev_screen_recording_output_dir).resolve()
