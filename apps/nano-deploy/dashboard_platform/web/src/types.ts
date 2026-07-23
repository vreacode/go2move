export type ConnectionState = "idle" | "connecting" | "connected" | "reconnecting" | "failed";

export type LogItem = {
  channel: "system" | "model" | "control" | string;
  level: string;
  message: string;
  timestamp: string;
  source?: string;
};

export type ModelStatus = {
  running: boolean;
  model_id?: string | null;
  weight?: string | null;
  source?: string | null;
  pid?: number | null;
  started_at?: string | null;
  command?: string[];
  view_enabled?: boolean;
};

export type MotionLastCommand = {
  command: string;
  speed: string;
  linear_x: number;
  angular_z: number;
  dry_run: boolean;
  topic: string;
  status: "acked" | "failed";
  timestamp: string;
};

export type MotionState = {
  armed: boolean;
  mode: "dry_run" | "real";
  real_publish_enabled: boolean;
  manual_override: boolean;
  nav_lockout: boolean;
  require_manual_arm: boolean;
  cmd_vel_topic: string;
  timeout_ms: number;
  max_linear_vel_safe: number;
  max_angular_vel_safe: number;
  cruise_active: boolean;
  cruise_speed?: "low" | "medium" | "high" | null;
  cruise_steering: "center" | "left" | "right";
  last_command?: MotionLastCommand | null;
  last_error?: string | null;
};

export type BaseStatus = {
  running: boolean;
  started_by_agent: boolean;
  pid?: number | null;
  wheeltec_robot_online: boolean;
  cmd_vel_subscriber_online: boolean;
  odom_online: boolean;
  last_error?: string | null;
};

export type BatteryStatus = {
  available: boolean;
  voltage?: number | null;
  percentage?: number | null;
  charging?: boolean | null;
  charging_current?: number | null;
  source?: string;
  updated_at?: string | null;
  low?: boolean;
  error?: string | null;
};

export type SiyiCameraStatus = {
  enabled: boolean;
  motion_enabled?: boolean;
  connected: boolean;
  target?: string;
  hardware_id?: string | null;
  firmware?: Record<string, string> | null;
  status?: {
    motion_mode_name?: string;
    record_state_name?: string;
    mounting_direction_name?: string;
    video_output_name?: string;
  } | null;
  attitude?: {
    yaw_deg?: number;
    pitch_deg?: number;
    roll_deg?: number;
  } | null;
  error?: string | null;
};

export type SystemStatus = {
  agent_online: boolean;
  board_online: boolean;
  board_name?: string;
  current_video_source?: string | null;
  current_video_mode?: "visible_raw" | "ir_raw" | "annotated";
  current_model?: string | null;
  current_map_name?: string | null;
  mapping_running?: boolean;
  navigation_running?: boolean;
  rviz_available?: boolean;
  rviz_running?: boolean;
  base_status?: BaseStatus | null;
  battery_status?: BatteryStatus | null;
  cpu_percent?: number | null;
  memory_percent?: number | null;
  gpu_percent?: number | null;
  last_heartbeat?: string;
  available_models?: Array<{ id: string; label: string; weight: string }>;
  model_status?: ModelStatus;
  motion_state?: MotionState | null;
  allow_mjpeg_fallback?: boolean;
  lidar_online?: boolean;
  imu_online?: boolean;
  pointcloud_hz?: number | null;
  imu_hz?: number | null;
  point_count?: number;
  sensor_error?: string | null;
  lidar_to_base_translation?: string;
  lidar_to_base_rotation_rpy?: string;
  imu_to_base_rotation_rpy?: string;
  car_model_forward_axis?: string;
  car_model_up_axis?: string;
  pointcloud_history_seconds?: number;
  error?: string;
};

export type StreamStats = {
  fps?: number;
  bitrateKbps?: number;
  width?: number;
  height?: number;
  rttMs?: number;
  frameAgeMs?: number;
  frozen?: boolean;
};

export type SensorFrame = {
  type: "sensor_frame";
  timestamp: number;
  seq: number;
  lidar_online: boolean;
  imu_online: boolean;
  pointcloud_hz?: number | null;
  imu_hz?: number | null;
  point_count: number;
  points: number[];
  imu?: {
    orientation?: { x: number; y: number; z: number; w: number };
    angular_velocity?: { x: number; y: number; z: number };
    linear_acceleration?: { x: number; y: number; z: number };
  };
  robot_pose?: {
    x: number;
    y: number;
    yaw: number;
    frame_id: string;
    source: string;
  } | null;
  error?: string | null;
};

export type PoseFrame = {
  type: "pose_frame";
  timestamp: number;
  seq: number;
  pose?: {
    x: number;
    y: number;
    yaw: number;
    frame_id: string;
    source: string;
  } | null;
  pose_source?: string | null;
  error?: string | null;
};

export type MapEntry = {
  name: string;
  yaml_path: string;
  image_path: string;
  updated_at: string;
};

export type MapMetadata = {
  name: string;
  yaml_path: string;
  image_path: string;
  resolution: number;
  origin: number[];
  negate: number;
  occupied_thresh: number;
  free_thresh: number;
  width?: number | null;
  height?: number | null;
};

export type MapPoint = {
  x: number;
  y: number;
  yaw: number;
  frame_id: string;
};

export type NavigationState = {
  navigation_running: boolean;
  goal_status: "idle" | "sent" | "completed" | "succeeded" | "failed" | "cancelled";
  goal?: MapPoint | null;
  last_result?: string | null;
  last_error?: string | null;
  updated_at?: string | null;
};

export type MediaStatus = {
  enabled: boolean;
  gallery_enabled: boolean;
  recording_running: boolean;
  recording_mode?: "visible_raw" | "ir_raw" | null;
  recording_filename?: string | null;
  recording_started_at?: string | null;
  recording_backend: string;
  recording_output_dir: string;
  snapshot_output_dir: string;
  last_error?: string | null;
};

export type MediaItem = {
  storage_kind: "recording" | "snapshot" | "event";
  media_type: "video" | "image";
  filename: string;
  created_at: string;
  size_bytes: number;
};

export type CaptureStatus = {
  enabled: boolean;
  running: boolean;
  stream_mode?: "visible_raw" | "ir_raw" | null;
  scene?: string | null;
  note?: string;
  interval_seconds?: number | null;
  duration_seconds?: number;
  max_frames?: number;
  started_at?: string | null;
  pid?: number | null;
  frames_saved: number;
  output_dir?: string;
  manifest_path?: string;
  latest_frame?: string | null;
  last_line?: string | null;
  last_error?: string | null;
  command?: string[];
};

export type CaptureGalleryItem = {
  group: string;
  scene: string;
  stream_mode: "visible_raw" | "ir_raw" | "unknown";
  frames_saved: number;
  first_captured_at?: string | null;
  last_captured_at?: string | null;
  note?: string;
  manifest_name: string;
  latest_frame_name?: string | null;
};

export type CaptureFrameItem = {
  name: string;
  captured_at?: string | null;
  stream_mode: "visible_raw" | "ir_raw" | "unknown";
};
