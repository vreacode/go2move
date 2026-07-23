#!/usr/bin/env python3
"""Read Wheeltec's voltage frames from the chassis UART and publish only voltage."""

from __future__ import annotations

import argparse

import rclpy
import serial
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import Float32

try:
    from .serial_voltage_protocol import FRAME_HEADER, FRAME_SIZE, decode_voltage_frame
except ImportError:
    from serial_voltage_protocol import FRAME_HEADER, FRAME_SIZE, decode_voltage_frame


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/ttyCH343USB0")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--topic", default="/PowerVoltage")
    return parser.parse_args()


class SerialVoltageNode(Node):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__("dashboard_serial_voltage_reader")
        self.publisher = self.create_publisher(Float32, args.topic, 10)
        self.serial_port = serial.Serial(
            port=args.port,
            baudrate=args.baudrate,
            timeout=0,
            exclusive=True,
        )
        self._raw_buffer = bytearray()
        self._timer = self.create_timer(0.02, self._poll_serial)
        self.get_logger().info(f"read-only voltage serial opened port={args.port} baudrate={args.baudrate}")

    def _poll_serial(self) -> None:
        try:
            waiting = self.serial_port.in_waiting
            if waiting:
                self._raw_buffer.extend(self.serial_port.read(min(waiting, 512)))
        except serial.SerialException as exc:
            self.get_logger().error(f"voltage serial read failed: {exc}")
            self._timer.cancel()
            if rclpy.ok():
                rclpy.shutdown()
            return

        while self._raw_buffer:
            header_index = self._raw_buffer.find(bytes([FRAME_HEADER]))
            if header_index < 0:
                self._raw_buffer.clear()
                return
            if header_index:
                del self._raw_buffer[:header_index]
            if len(self._raw_buffer) < FRAME_SIZE:
                return
            frame = bytes(self._raw_buffer[:FRAME_SIZE])
            voltage = decode_voltage_frame(frame)
            if voltage is None:
                del self._raw_buffer[0]
                continue
            self.publisher.publish(Float32(data=voltage))
            del self._raw_buffer[:FRAME_SIZE]

    def close(self) -> None:
        if self.serial_port.is_open:
            self.serial_port.close()


def main() -> None:
    args = parse_args()
    rclpy.init()
    node: SerialVoltageNode | None = None
    try:
        node = SerialVoltageNode(args)
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if node is not None:
            node.close()
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
