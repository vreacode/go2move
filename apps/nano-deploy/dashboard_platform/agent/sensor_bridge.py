#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from typing import Iterable

import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time
from sensor_msgs.msg import Imu, PointCloud2
from sensor_msgs_py import point_cloud2
from tf2_ros import Buffer, TransformException, TransformListener


def parse_args():
    parser = argparse.ArgumentParser(description="ROS2 sensor bridge for lidar + IMU")
    parser.add_argument("--lidar-topic", default="/c16/lslidar_point_cloud")
    parser.add_argument("--imu-topic", default="/imu/data")
    parser.add_argument("--max-points", type=int, default=1500)
    parser.add_argument("--send-hz", type=float, default=5.0)
    parser.add_argument("--keep-seconds", type=float, default=1.0)
    parser.add_argument("--lidar-enabled", default="true")
    parser.add_argument("--imu-enabled", default="true")
    parser.add_argument("--lidar-to-base-translation", default="[0.0, 0.0, 0.0]")
    parser.add_argument("--lidar-to-base-rotation-rpy", default="[0.0, 0.0, 0.0]")
    parser.add_argument("--imu-to-base-rotation-rpy", default="[0.0, 0.0, 0.0]")
    parser.add_argument("--robot-pose-enabled", default="true")
    parser.add_argument("--robot-pose-map-frame", default="map")
    parser.add_argument("--robot-pose-base-frame", default="base_footprint")
    parser.add_argument("--robot-pose-fallback-base-frame", default="base_link")
    parser.add_argument("--robot-pose-amcl-topic", default="/amcl_pose")
    return parser.parse_args()


def as_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def parse_vec3(raw: str) -> tuple[float, float, float]:
    data = json.loads(raw)
    return float(data[0]), float(data[1]), float(data[2])


def quaternion_from_rpy(roll: float, pitch: float, yaw: float) -> tuple[float, float, float, float]:
    cr = math.cos(roll * 0.5)
    sr = math.sin(roll * 0.5)
    cp = math.cos(pitch * 0.5)
    sp = math.sin(pitch * 0.5)
    cy = math.cos(yaw * 0.5)
    sy = math.sin(yaw * 0.5)
    return (
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


def quaternion_multiply(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def yaw_from_quaternion(q: tuple[float, float, float, float]) -> float:
    x, y, z, w = q
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def rotate_point(point: tuple[float, float, float], quat: tuple[float, float, float, float]) -> tuple[float, float, float]:
    x, y, z = point
    qx, qy, qz, qw = quat
    ix = qw * x + qy * z - qz * y
    iy = qw * y + qz * x - qx * z
    iz = qw * z + qx * y - qy * x
    iw = -qx * x - qy * y - qz * z
    return (
        ix * qw + iw * -qx + iy * -qz - iz * -qy,
        iy * qw + iw * -qy + iz * -qx - ix * -qz,
        iz * qw + iw * -qz + ix * -qy - iy * -qx,
    )


class SensorBridgeNode(Node):
    def __init__(self, args) -> None:
        super().__init__("dashboard_sensor_bridge")
        self.args = args
        self.lidar_enabled = as_bool(args.lidar_enabled)
        self.imu_enabled = as_bool(args.imu_enabled)
        self.robot_pose_enabled = as_bool(args.robot_pose_enabled)
        self.max_points = max(args.max_points, 1)
        self.send_interval = 1.0 / max(args.send_hz, 0.1)
        self.keep_seconds = max(args.keep_seconds, 0.2)
        self.lidar_translation = parse_vec3(args.lidar_to_base_translation)
        self.lidar_rotation = quaternion_from_rpy(*parse_vec3(args.lidar_to_base_rotation_rpy))
        self.imu_rotation = quaternion_from_rpy(*parse_vec3(args.imu_to_base_rotation_rpy))
        self.robot_pose_map_frame = args.robot_pose_map_frame
        self.robot_pose_base_frame = args.robot_pose_base_frame
        self.robot_pose_fallback_base_frame = args.robot_pose_fallback_base_frame
        self.robot_pose_amcl_topic = args.robot_pose_amcl_topic
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self, spin_thread=False)

        self.latest_points: list[float] = []
        self.latest_point_count = 0
        self.latest_imu: dict[str, object] = {}
        self.latest_pose: dict[str, object] = {}
        self.last_lidar_ts = 0.0
        self.last_imu_ts = 0.0
        self.last_pose_ts = 0.0
        self.last_lidar_recv = 0.0
        self.last_imu_recv = 0.0
        self.pointcloud_hz = 0.0
        self.imu_hz = 0.0
        self.frame_seq = 0

        if self.lidar_enabled:
            self.create_subscription(PointCloud2, args.lidar_topic, self.on_pointcloud, qos_profile_sensor_data)
        if self.imu_enabled:
            self.create_subscription(Imu, args.imu_topic, self.on_imu, qos_profile_sensor_data)
        if self.robot_pose_enabled:
            self.create_subscription(PoseWithCovarianceStamped, args.robot_pose_amcl_topic, self.on_amcl_pose, 10)
        self.create_timer(self.send_interval, self.publish_frame)

    def on_pointcloud(self, msg: PointCloud2) -> None:
        now = time.time()
        if self.last_lidar_recv:
            dt = max(now - self.last_lidar_recv, 1e-3)
            self.pointcloud_hz = 1.0 / dt
        self.last_lidar_recv = now

        raw_points = list(point_cloud2.read_points(msg, field_names=("x", "y", "z"), skip_nans=True))
        if not raw_points:
            self.latest_points = []
            self.latest_point_count = 0
            self.last_lidar_ts = now
            return

        stride = max(len(raw_points) // self.max_points, 1)
        sampled = raw_points[::stride][: self.max_points]
        points: list[float] = []
        tx, ty, tz = self.lidar_translation
        for x, y, z in sampled:
            rx, ry, rz = rotate_point((float(x), float(y), float(z)), self.lidar_rotation)
            points.extend([round(rx + tx, 3), round(ry + ty, 3), round(rz + tz, 3)])
        self.latest_points = points
        self.latest_point_count = len(sampled)
        self.last_lidar_ts = now

    def on_imu(self, msg: Imu) -> None:
        now = time.time()
        if self.last_imu_recv:
            dt = max(now - self.last_imu_recv, 1e-3)
            self.imu_hz = 1.0 / dt
        self.last_imu_recv = now
        q = (
            float(msg.orientation.x),
            float(msg.orientation.y),
            float(msg.orientation.z),
            float(msg.orientation.w),
        )
        q_base = quaternion_multiply(self.imu_rotation, q)
        self.latest_imu = {
            "orientation": {"x": q_base[0], "y": q_base[1], "z": q_base[2], "w": q_base[3]},
            "angular_velocity": {
                "x": float(msg.angular_velocity.x),
                "y": float(msg.angular_velocity.y),
                "z": float(msg.angular_velocity.z),
            },
            "linear_acceleration": {
                "x": float(msg.linear_acceleration.x),
                "y": float(msg.linear_acceleration.y),
                "z": float(msg.linear_acceleration.z),
            },
        }
        self.last_imu_ts = now

    def on_amcl_pose(self, msg: PoseWithCovarianceStamped) -> None:
        q = (
            float(msg.pose.pose.orientation.x),
            float(msg.pose.pose.orientation.y),
            float(msg.pose.pose.orientation.z),
            float(msg.pose.pose.orientation.w),
        )
        self.latest_pose = {
            "x": round(float(msg.pose.pose.position.x), 3),
            "y": round(float(msg.pose.pose.position.y), 3),
            "yaw": round(yaw_from_quaternion(q), 4),
            "frame_id": msg.header.frame_id or self.robot_pose_map_frame,
            "source": "amcl_pose",
        }
        self.last_pose_ts = time.time()

    def _lookup_tf_pose(self, target_frame: str, source_frame: str, source_label: str) -> dict[str, object] | None:
        try:
            transform = self.tf_buffer.lookup_transform(target_frame, source_frame, Time())
        except TransformException:
            return None
        q = (
            float(transform.transform.rotation.x),
            float(transform.transform.rotation.y),
            float(transform.transform.rotation.z),
            float(transform.transform.rotation.w),
        )
        return {
            "x": round(float(transform.transform.translation.x), 3),
            "y": round(float(transform.transform.translation.y), 3),
            "yaw": round(yaw_from_quaternion(q), 4),
            "frame_id": target_frame,
            "source": source_label,
        }

    def current_robot_pose(self, now: float) -> dict[str, object] | None:
        if not self.robot_pose_enabled:
            return None
        pose = self._lookup_tf_pose(self.robot_pose_map_frame, self.robot_pose_base_frame, f"tf:{self.robot_pose_map_frame}->{self.robot_pose_base_frame}")
        if pose:
            self.latest_pose = pose
            self.last_pose_ts = now
            return pose
        if self.robot_pose_fallback_base_frame and self.robot_pose_fallback_base_frame != self.robot_pose_base_frame:
            pose = self._lookup_tf_pose(
                self.robot_pose_map_frame,
                self.robot_pose_fallback_base_frame,
                f"tf:{self.robot_pose_map_frame}->{self.robot_pose_fallback_base_frame}",
            )
            if pose:
                self.latest_pose = pose
                self.last_pose_ts = now
                return pose
        if self.latest_pose and now - self.last_pose_ts <= self.keep_seconds * 2:
            return self.latest_pose
        return None

    def publish_frame(self) -> None:
        now = time.time()
        errors: list[str] = []
        pose = self.current_robot_pose(now)
        if self.lidar_enabled and now - self.last_lidar_ts > self.keep_seconds:
            errors.append(f"waiting for lidar topic {self.args.lidar_topic}")
        if self.imu_enabled and now - self.last_imu_ts > self.keep_seconds:
            errors.append(f"waiting for imu topic {self.args.imu_topic}")
        payload = {
            "type": "sensor_frame",
            "timestamp": now,
            "seq": self.frame_seq,
            "lidar_online": bool(self.lidar_enabled and now - self.last_lidar_ts <= self.keep_seconds),
            "imu_online": bool(self.imu_enabled and now - self.last_imu_ts <= self.keep_seconds),
            "pointcloud_hz": round(self.pointcloud_hz, 2) if self.pointcloud_hz else None,
            "imu_hz": round(self.imu_hz, 2) if self.imu_hz else None,
            "point_count": self.latest_point_count,
            "points": self.latest_points,
            "imu": self.latest_imu,
            "robot_pose": pose,
            "error": "; ".join(errors) if errors else None,
        }
        self.frame_seq += 1
        sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
        sys.stdout.flush()


def main() -> int:
    args = parse_args()
    rclpy.init()
    node = SensorBridgeNode(args)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        sys.stdout.write(json.dumps({"type": "sensor_error", "message": repr(exc)}, ensure_ascii=True) + "\n")
        sys.stdout.flush()
        return 1
    finally:
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
