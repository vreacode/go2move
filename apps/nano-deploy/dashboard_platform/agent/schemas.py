from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class LoginlessAck(BaseModel):
    ok: bool = True
    status: Literal["sent", "acked", "failed"]
    detail: str = ""


class ModelStartRequest(BaseModel):
    model_id: str
    source: str | None = None
    vid_stride: int | None = Field(default=None, ge=1, le=30)
    img_size: int | None = Field(default=None, ge=160, le=1920)
    conf: float | None = Field(default=None, ge=0.01, le=1.0)
    iou: float | None = Field(default=None, ge=0.01, le=1.0)
    device: str | None = None
    half: bool | None = None
    view: bool = False
    annotate: bool = True
    event_classes: str = ""
    parking_zone: list[float] | None = Field(default=None, min_length=4, max_length=4)
    parking_dwell_seconds: float = Field(default=5.0, ge=0.0, le=3600.0)
    parking_cooldown_seconds: float = Field(default=30.0, ge=0.0, le=3600.0)
    parking_min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    parking_track_iou: float = Field(default=0.3, gt=0.0, le=1.0)

    @field_validator("parking_zone")
    @classmethod
    def validate_parking_zone(cls, value: list[float] | None) -> list[float] | None:
        if value is None:
            return value
        if any(coordinate < 0.0 or coordinate > 1.0 for coordinate in value):
            raise ValueError("parking_zone coordinates must be normalized to [0, 1]")
        if value[0] >= value[2] or value[1] >= value[3]:
            raise ValueError("parking_zone must have positive area")
        return value


class ControlRequest(BaseModel):
    command: Literal["forward", "backward", "left", "right", "stop"]
    speed: Literal["low", "medium", "high"] = "medium"


class CruiseStartRequest(BaseModel):
    speed: Literal["low", "medium", "high"] = "medium"
    auto_arm: bool = False


class CruiseSteeringRequest(BaseModel):
    direction: Literal["center", "left", "right"] = "center"


class GimbalPulseRequest(BaseModel):
    yaw_speed: int = Field(default=0, ge=-100, le=100)
    pitch_speed: int = Field(default=0, ge=-100, le=100)
    duration_ms: int = Field(default=250, ge=50, le=1000)


class MotionArmRequest(BaseModel):
    armed: bool


class MotionOverrideRequest(BaseModel):
    enabled: bool


class MotionModeRequest(BaseModel):
    mode: Literal["dry_run", "real"]


class PresetRequest(BaseModel):
    name: str
    variables: dict[str, str] = Field(default_factory=dict)


class NavigationPointRequest(BaseModel):
    x: float
    y: float
    yaw: float = 0.0


class WebRTCOffer(BaseModel):
    sdp: str
    type: str = "offer"
    stream_mode: Literal["visible_raw", "ir_raw", "annotated"] = "visible_raw"


class MediaRecordingStartRequest(BaseModel):
    stream_mode: Literal["visible_raw", "ir_raw"] = "visible_raw"


class MediaSnapshotRequest(BaseModel):
    stream_mode: Literal["visible_raw", "ir_raw", "annotated"] = "visible_raw"


class CaptureStartRequest(BaseModel):
    stream_mode: Literal["visible_raw", "ir_raw"] = "visible_raw"
    scene: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=240)
    interval_seconds: float = Field(default=1.0, ge=0.2, le=60.0)
    duration_seconds: float = Field(default=0.0, ge=0.0, le=3600.0)
    max_frames: int = Field(default=0, ge=0, le=10000)
    jpeg_quality: int = Field(default=95, ge=50, le=100)


class CaptureStatus(BaseModel):
    enabled: bool = True
    running: bool = False
    stream_mode: Literal["visible_raw", "ir_raw"] | None = None
    scene: str | None = None
    note: str = ""
    interval_seconds: float | None = None
    duration_seconds: float = 0.0
    max_frames: int = 0
    started_at: str | None = None
    pid: int | None = None
    frames_saved: int = 0
    output_dir: str = ""
    manifest_path: str = ""
    latest_frame: str | None = None
    last_line: str | None = None
    last_error: str | None = None
    command: list[str] = Field(default_factory=list)


class CaptureGalleryItem(BaseModel):
    group: str
    scene: str
    stream_mode: Literal["visible_raw", "ir_raw", "unknown"] = "unknown"
    frames_saved: int = 0
    first_captured_at: str | None = None
    last_captured_at: str | None = None
    note: str = ""
    manifest_name: str
    latest_frame_name: str | None = None


class CaptureFrameItem(BaseModel):
    name: str
    captured_at: str | None = None
    stream_mode: Literal["visible_raw", "ir_raw", "unknown"] = "unknown"


class BatteryStatus(BaseModel):
    available: bool = False
    voltage: float | None = None
    percentage: float | None = None
    charging: bool | None = None
    charging_current: float | None = None
    source: str = "/PowerVoltage"
    updated_at: str | None = None
    low: bool = False
    error: str | None = None
    updated_at_epoch: float = Field(default=0.0, exclude=True)


class ModelRuntimeStatus(BaseModel):
    running: bool
    model_id: str | None = None
    weight: str | None = None
    source: str | None = None
    pid: int | None = None
    started_at: str | None = None
    command: list[str] = Field(default_factory=list)
    view_enabled: bool = False


class MotionLastCommand(BaseModel):
    command: str
    speed: str
    linear_x: float
    angular_z: float
    dry_run: bool
    topic: str
    status: Literal["acked", "failed"] = "acked"
    timestamp: str


class MotionState(BaseModel):
    armed: bool = False
    mode: Literal["dry_run", "real"] = "dry_run"
    real_publish_enabled: bool = False
    manual_override: bool = False
    nav_lockout: bool = False
    require_manual_arm: bool = True
    cmd_vel_topic: str = "/cmd_vel_nav"
    timeout_ms: int = 300
    max_linear_vel_safe: float = 0.05
    max_angular_vel_safe: float = 0.2
    cruise_active: bool = False
    cruise_speed: Literal["low", "medium", "high"] | None = None
    cruise_steering: Literal["center", "left", "right"] = "center"
    last_command: MotionLastCommand | None = None
    last_error: str | None = None


class BaseStatus(BaseModel):
    running: bool = False
    started_by_agent: bool = False
    pid: int | None = None
    wheeltec_robot_online: bool = False
    cmd_vel_subscriber_online: bool = False
    odom_online: bool = False
    last_error: str | None = None


class AgentStatus(BaseModel):
    agent_online: bool
    board_name: str
    board_online: bool
    current_video_source: str | None
    current_video_mode: Literal["visible_raw", "ir_raw", "annotated"] = "visible_raw"
    current_model: str | None
    current_map_name: str | None = None
    mapping_running: bool = False
    navigation_running: bool = False
    rviz_available: bool = False
    rviz_running: bool = False
    base_status: BaseStatus | None = None
    battery_status: BatteryStatus | None = None
    model_status: ModelRuntimeStatus
    motion_state: MotionState | None = None
    cpu_percent: float | None = None
    memory_percent: float | None = None
    gpu_percent: float | None = None
    lidar_online: bool = False
    imu_online: bool = False
    pointcloud_hz: float | None = None
    imu_hz: float | None = None
    point_count: int = 0
    sensor_error: str | None = None
    lidar_to_base_translation: str = "[0.0, 0.0, 0.0]"
    lidar_to_base_rotation_rpy: str = "[0.0, 0.0, 0.0]"
    imu_to_base_rotation_rpy: str = "[0.0, 0.0, 0.0]"
    car_model_forward_axis: str = "positive_z"
    car_model_up_axis: str = "positive_y"
    pointcloud_history_seconds: float = 0.75
    last_heartbeat: str
    available_models: list[dict[str, Any]]
    allow_mjpeg_fallback: bool = False


class MapEntry(BaseModel):
    name: str
    yaml_path: str
    image_path: str
    updated_at: str


class MapMetadata(BaseModel):
    name: str
    yaml_path: str
    image_path: str
    resolution: float
    origin: list[float]
    negate: int
    occupied_thresh: float
    free_thresh: float
    width: int | None = None
    height: int | None = None


class MediaStatus(BaseModel):
    enabled: bool = False
    gallery_enabled: bool = False
    recording_running: bool = False
    recording_mode: Literal["visible_raw", "ir_raw"] | None = None
    recording_filename: str | None = None
    recording_started_at: str | None = None
    recording_backend: str = "disabled"
    recording_output_dir: str = ""
    snapshot_output_dir: str = ""
    last_error: str | None = None


class GalleryItem(BaseModel):
    storage_kind: Literal["recording", "snapshot", "event"]
    media_type: Literal["video", "image"]
    filename: str
    created_at: str
    size_bytes: int


class DevScreenRecordingStatus(BaseModel):
    enabled: bool = False
    running: bool = False
    backend_configured: str = "auto"
    backend_active: str | None = None
    output_dir: str = ""
    filename: str | None = None
    started_at: str | None = None
    display: str = ""
    fps: int = 10
    width: int = 1280
    height: int = 720
    last_error: str | None = None
