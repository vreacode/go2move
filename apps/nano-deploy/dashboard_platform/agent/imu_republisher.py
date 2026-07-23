#!/usr/bin/env python3
from __future__ import annotations

import argparse

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSHistoryPolicy, QoSProfile, QoSReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import Imu


def parse_args():
    parser = argparse.ArgumentParser(description="Republish IMU data onto a standard ROS2 topic")
    parser.add_argument("--source-topic", default="/imu/data_raw")
    parser.add_argument("--target-topic", default="/imu/data")
    parser.add_argument("--frame-id", default="")
    return parser.parse_args()


class ImuRepublisher(Node):
    def __init__(self, source_topic: str, target_topic: str, frame_id: str) -> None:
        super().__init__("dashboard_imu_republisher")
        self._frame_id = frame_id
        reliable_qos = QoSProfile(
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=20,
            reliability=QoSReliabilityPolicy.RELIABLE,
        )
        self._publisher = self.create_publisher(Imu, target_topic, reliable_qos)
        self.create_subscription(Imu, source_topic, self.on_imu, qos_profile_sensor_data)
        self.get_logger().info(f"republishing {source_topic} -> {target_topic}")

    def on_imu(self, msg: Imu) -> None:
        if self._frame_id:
            msg.header.frame_id = self._frame_id
        self._publisher.publish(msg)


def main() -> int:
    args = parse_args()
    rclpy.init()
    node = ImuRepublisher(args.source_topic, args.target_topic, args.frame_id)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
