#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
import time

import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.node import Node
from rclpy.time import Time
from tf2_ros import Buffer, TransformException, TransformListener


def parse_args():
    parser = argparse.ArgumentParser(description="ROS2 pose bridge for map robot pose")
    parser.add_argument("--map-frame", default="map")
    parser.add_argument("--base-frame", default="base_footprint")
    parser.add_argument("--fallback-base-frame", default="base_link")
    parser.add_argument("--amcl-topic", default="/amcl_pose")
    parser.add_argument("--send-hz", type=float, default=5.0)
    parser.add_argument("--keep-seconds", type=float, default=2.0)
    return parser.parse_args()


def yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


class PoseBridgeNode(Node):
    def __init__(self, args) -> None:
        super().__init__("dashboard_pose_bridge")
        self.args = args
        self.keep_seconds = max(args.keep_seconds, 0.5)
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self, spin_thread=False)
        self.latest_pose: dict[str, object] | None = None
        self.last_pose_ts = 0.0
        self.pose_source = ""
        self.error: str | None = None
        self.seq = 0
        self.create_subscription(PoseWithCovarianceStamped, args.amcl_topic, self.on_amcl_pose, 10)
        self.create_timer(1.0 / max(args.send_hz, 0.2), self.publish_pose)

    def on_amcl_pose(self, msg: PoseWithCovarianceStamped) -> None:
        yaw = yaw_from_quaternion(
            float(msg.pose.pose.orientation.x),
            float(msg.pose.pose.orientation.y),
            float(msg.pose.pose.orientation.z),
            float(msg.pose.pose.orientation.w),
        )
        self.latest_pose = {
            "x": round(float(msg.pose.pose.position.x), 3),
            "y": round(float(msg.pose.pose.position.y), 3),
            "yaw": round(yaw, 4),
            "frame_id": msg.header.frame_id or self.args.map_frame,
            "source": "amcl_pose",
        }
        self.last_pose_ts = time.time()
        self.pose_source = "amcl_pose"

    def _lookup_tf_pose(self, source_frame: str, source_label: str) -> dict[str, object] | None:
        try:
            transform = self.tf_buffer.lookup_transform(self.args.map_frame, source_frame, Time())
        except TransformException:
            return None
        yaw = yaw_from_quaternion(
            float(transform.transform.rotation.x),
            float(transform.transform.rotation.y),
            float(transform.transform.rotation.z),
            float(transform.transform.rotation.w),
        )
        return {
            "x": round(float(transform.transform.translation.x), 3),
            "y": round(float(transform.transform.translation.y), 3),
            "yaw": round(yaw, 4),
            "frame_id": self.args.map_frame,
            "source": source_label,
        }

    def current_pose(self) -> dict[str, object] | None:
        pose = self._lookup_tf_pose(self.args.base_frame, f"tf:{self.args.map_frame}->{self.args.base_frame}")
        if pose:
            self.latest_pose = pose
            self.last_pose_ts = time.time()
            self.pose_source = str(pose["source"])
            self.error = None
            return pose
        if self.args.fallback_base_frame and self.args.fallback_base_frame != self.args.base_frame:
            pose = self._lookup_tf_pose(
                self.args.fallback_base_frame,
                f"tf:{self.args.map_frame}->{self.args.fallback_base_frame}",
            )
            if pose:
                self.latest_pose = pose
                self.last_pose_ts = time.time()
                self.pose_source = str(pose["source"])
                self.error = None
                return pose
        if self.latest_pose and time.time() - self.last_pose_ts <= self.keep_seconds:
            return self.latest_pose
        self.error = f"waiting for pose source {self.args.map_frame}->{self.args.base_frame}"
        return None

    def publish_pose(self) -> None:
        pose = self.current_pose()
        payload = {
            "type": "pose_frame",
            "timestamp": time.time(),
            "seq": self.seq,
            "pose": pose,
            "pose_source": self.pose_source or (pose.get("source") if pose else None),
            "error": self.error,
        }
        self.seq += 1
        sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
        sys.stdout.flush()


def main() -> int:
    args = parse_args()
    rclpy.init()
    node = PoseBridgeNode(args)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        sys.stdout.write(json.dumps({"type": "pose_error", "message": repr(exc)}, ensure_ascii=True) + "\n")
        sys.stdout.flush()
        return 1
    finally:
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
