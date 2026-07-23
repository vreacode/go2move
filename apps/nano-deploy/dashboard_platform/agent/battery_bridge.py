#!/usr/bin/env python3
"""Bridge Wheeltec battery topics to newline-delimited JSON for the Agent."""

from __future__ import annotations

import argparse
import json
import math
import time

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import Bool, Float32


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voltage-topic", default="/PowerVoltage")
    parser.add_argument("--charging-topic", default="/robot_charging_flag")
    parser.add_argument("--current-topic", default="/robot_charging_current")
    return parser.parse_args()


class BatteryBridgeNode(Node):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__("dashboard_battery_bridge")
        self.payload: dict[str, object] = {
            "type": "battery",
            "voltage": None,
            "charging": None,
            "charging_current": None,
            "timestamp": None,
        }
        self.create_subscription(Float32, args.voltage_topic, self.on_voltage, 10)
        self.create_subscription(Bool, args.charging_topic, self.on_charging, 10)
        self.create_subscription(Float32, args.current_topic, self.on_current, 10)

    def emit(self) -> None:
        print(json.dumps(self.payload, ensure_ascii=True), flush=True)

    def on_voltage(self, message: Float32) -> None:
        value = float(message.data)
        self.payload["voltage"] = value if math.isfinite(value) else None
        self.payload["timestamp"] = time.time()
        self.emit()

    def on_charging(self, message: Bool) -> None:
        self.payload["charging"] = bool(message.data)
        self.payload["timestamp"] = time.time()
        self.emit()

    def on_current(self, message: Float32) -> None:
        value = float(message.data)
        self.payload["charging_current"] = value if math.isfinite(value) else None
        self.payload["timestamp"] = time.time()
        self.emit()


def main() -> int:
    args = parse_args()
    rclpy.init()
    node = BatteryBridgeNode(args)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
