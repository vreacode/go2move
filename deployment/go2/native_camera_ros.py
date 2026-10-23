#!/usr/bin/env python3
"""Publish Go2 front-camera files to ROS without importing the hardware SDK.

Run after source_go2_platform.sh, with CYCLONEDDS_URI=native_camera_dds.xml.
The existing front_camera URDF link is connected to its optical frame by TF.
Camera intrinsics are not supplied because the vendor calibration is unknown.
"""
import argparse
from array import array
import hashlib
import json
import signal
import time
from pathlib import Path

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image, CompressedImage
from geometry_msgs.msg import TransformStamped
from tf2_ros import StaticTransformBroadcaster

FRAME = 'front_camera_optical_frame'


class FrontCameraBridge(Node):
    def __init__(self, args):
        super().__init__('go2_native_front_camera')
        self.args = args
        self.published = 0
        self.last_capture = None
        self.last_notice = 0
        self.compressed = self.create_publisher(CompressedImage, '/camera/front/image_raw/compressed', qos_profile_sensor_data)
        self.raw = self.create_publisher(Image, '/camera/front/image_raw', qos_profile_sensor_data) if args.raw else None
        self.tf = StaticTransformBroadcaster(self)
        transform = TransformStamped()
        transform.header.stamp = self.get_clock().now().to_msg()
        transform.header.frame_id = 'front_camera'
        transform.child_frame_id = FRAME
        # rpy(-pi/2, 0, -pi/2): optical x right, y down, z forward.
        transform.transform.rotation.x = -0.5
        transform.transform.rotation.y = 0.5
        transform.transform.rotation.z = -0.5
        transform.transform.rotation.w = 0.5
        self.tf.sendTransform(transform)
        self.timer = self.create_timer(1 / (2 * args.fps), self.publish_latest)
        self.get_logger().info('Front camera bridge ready: original JPEG and ' +
                               (str(args.width) + 'x' + str(args.height) + ' bgr8' if args.raw else 'raw disabled'))

    def publish_latest(self):
        try:
            status = json.loads((self.args.input_dir / 'status.json').read_text())
            captured_at = float(status.get('captured_at', 0))
            if not status.get('running') or not -0.25 <= time.time() - captured_at <= self.args.max_age:
                return
            if self.last_capture is not None and captured_at - self.last_capture < 0.95 / self.args.fps:
                return
            jpeg = (self.args.input_dir / 'latest.jpg').read_bytes()
            # The producer atomically replaces each file; reject an update between them.
            if hashlib.sha256(jpeg).hexdigest() != status.get('sha256'):
                return
            decoded = None
            if self.raw:
                decoded = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                if decoded is None:
                    raise ValueError('Unable to decode native JPEG')
                decoded = cv2.resize(decoded, (self.args.width, self.args.height), interpolation=cv2.INTER_AREA)
            message = CompressedImage()
            message.header.frame_id = FRAME
            message.header.stamp.sec = int(captured_at)
            message.header.stamp.nanosec = int((captured_at - int(captured_at)) * 1_000_000_000)
            message.format = 'jpeg'
            message.data = array('B', jpeg)
            self.compressed.publish(message)
            if decoded is not None:
                raw = Image()
                raw.header = message.header
                raw.height, raw.width = decoded.shape[:2]
                raw.encoding = 'bgr8'
                raw.is_bigendian = 0
                raw.step = self.args.width * 3
                raw.data = array('B', decoded.tobytes())
                self.raw.publish(raw)
            self.last_capture = captured_at
            self.published += 1
        except (OSError, ValueError, TypeError) as error:
            now = time.monotonic()
            if now - self.last_notice > 5:
                self.get_logger().warning('Waiting for valid camera files: ' + str(error))
                self.last_notice = now


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=Path(__file__).with_name('runtime') / 'native_camera')
    parser.add_argument('--fps', type=float, default=5.0)
    parser.add_argument('--max-age', type=float, default=2.0)
    parser.add_argument('--width', type=int, default=640)
    parser.add_argument('--height', type=int, default=360)
    parser.add_argument('--no-raw', dest='raw', action='store_false')
    parser.add_argument('--duration', type=float, default=0.0, help='Seconds; zero keeps running')
    args, ros_args = parser.parse_known_args()
    if args.fps <= 0 or args.max_age <= 0 or args.width <= 0 or args.height <= 0 or args.duration < 0:
        parser.error('fps, max-age and dimensions must be positive; duration nonnegative')
    rclpy.init(args=ros_args)
    node = FrontCameraBridge(args)
    stopped = [False]
    signal.signal(signal.SIGTERM, lambda *_: stopped.__setitem__(0, True))
    started = time.monotonic()
    try:
        while rclpy.ok() and not stopped[0] and (not args.duration or time.monotonic() - started < args.duration):
            rclpy.spin_once(node, timeout_sec=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        print(json.dumps({'published_frames': node.published, 'raw_enabled': args.raw,
                          'frame_id': FRAME, 'duration_seconds': round(time.monotonic() - started, 3)}), flush=True)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
