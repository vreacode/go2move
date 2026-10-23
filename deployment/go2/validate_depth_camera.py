#!/usr/bin/env python3
"""Passive RealSense and XT-16/SLAM stream validation; no control publishers."""
import argparse
import json
import math
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import rclpy
from rclpy.qos import QoSProfile, DurabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import Image, Imu, CameraInfo, PointCloud2, LaserScan
from nav_msgs.msg import OccupancyGrid, Odometry
from tf2_ros import Buffer, TransformListener
from go2_platform_write import backup, write


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--model', choices=('d435i', 'd455'), default='d435i',
                        help='Camera frame prefix from the selected RealSense profile')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 10 <= args.seconds <= 120:
        parser.error('validation duration must be 10..120 seconds')
    rclpy.init()
    node = rclpy.create_node('go2_depth_camera_validation', enable_rosout=False)
    buffer = Buffer()
    listener = TransformListener(buffer, node)
    arrivals, stamps, ages, last = defaultdict(list), defaultdict(list), defaultdict(list), {}
    active = False

    def receive(topic, message):
        last[topic] = message
        if active:
            arrivals[topic].append(time.monotonic())
            stamp = message.header.stamp.sec + message.header.stamp.nanosec / 1e9
            stamps[topic].append(stamp)
            ages[topic].append(time.time() - stamp)

    topics = [
        ('/camera/color/image_raw', Image),
        ('/camera/depth/image_rect_raw', Image),
        ('/camera/aligned_depth_to_color/image_raw', Image),
        ('/camera/imu', Imu), ('/camera/depth/color/points', PointCloud2),
        ('/camera/color/camera_info', CameraInfo),
        ('/camera/depth/camera_info', CameraInfo),
        ('/camera/aligned_depth_to_color/camera_info', CameraInfo),
        ('/lidar_points', PointCloud2), ('/scan', LaserScan),
        ('/odom', Odometry), ('/map', OccupancyGrid),
    ]
    subscriptions = []
    for topic, message_type in topics:
        qos = (QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
               if topic == '/map' else qos_profile_sensor_data)
        subscriptions.append(node.create_subscription(message_type, topic,
            lambda message, key=topic: receive(key, message), qos))

    def spin(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            rclpy.spin_once(node, timeout_sec=.02)

    try:
        spin(5)
        active = True
        start = time.monotonic()
        spin(args.seconds)
        elapsed = time.monotonic() - start
        active = False
        report = {'sampled_at': time.time(), 'duration_s': elapsed, 'motion_sent': False,
                  'camera_model': args.model,
                  'topics': {}, 'tf': {}, 'snapshots': {}}
        for topic, _ in topics:
            times = arrivals[topic]
            gaps = [b-a for a, b in zip(times, times[1:])]
            message = last.get(topic)
            item = {'count': len(times),
                    'hz': (len(times)-1)/(times[-1]-times[0]) if len(times)>1 else 0,
                    'max_gap_s': max(gaps) if gaps else None,
                    'stamp_backwards': sum(b<a for a,b in zip(stamps[topic],stamps[topic][1:])),
                    'max_stamp_age_s': max(ages[topic]) if ages[topic] else None,
                    'min_stamp_age_s': min(ages[topic]) if ages[topic] else None,
                    'latest_frame_age_s': time.monotonic()-times[-1] if times else None,
                    'frame_id': message.header.frame_id if message else None}
            if isinstance(message, Image):
                item.update(width=message.width, height=message.height, encoding=message.encoding)
                if message.encoding == '16UC1':
                    array = np.ndarray((message.height,message.width),
                        dtype='>u2' if message.is_bigendian else '<u2',
                        buffer=message.data,strides=(message.step,2))
                    valid = array[array>0]
                    item['valid_depth_fraction'] = float(valid.size/array.size)
                    item['median_valid_depth_m'] = float(np.median(valid)/1000) if valid.size else None
            elif isinstance(message, CameraInfo):
                item.update(width=message.width,height=message.height,k=list(message.k),
                            distortion_model=message.distortion_model)
            elif isinstance(message, Imu):
                accel = [message.linear_acceleration.x,message.linear_acceleration.y,message.linear_acceleration.z]
                gyro = [message.angular_velocity.x,message.angular_velocity.y,message.angular_velocity.z]
                item.update(accel=accel,gyro=gyro,finite=all(math.isfinite(v) for v in accel+gyro),
                            acceleration_norm=math.sqrt(sum(v*v for v in accel)),
                            orientation_available=bool(message.orientation_covariance[0] != -1))
            elif isinstance(message, PointCloud2):
                item.update(points=message.width*message.height,fields=[v.name for v in message.fields])
                xyz = {f.name: f for f in message.fields if f.name in ('x','y','z')}
                if len(xyz)==3 and all(f.datatype==7 for f in xyz.values()) and message.width and message.height:
                    coords = [np.ndarray((message.height,message.width),
                        dtype='>f4' if message.is_bigendian else '<f4',
                        buffer=message.data,offset=xyz[key].offset,
                        strides=(message.row_step,message.point_step)) for key in ('x','y','z')]
                    sample = [v[::8,::8] for v in coords]
                    finite = np.logical_and.reduce([np.isfinite(v) for v in sample])
                    item['sampled_finite_point_fraction']=float(np.mean(finite))
                    item['sampled_nonzero_points']=int(np.count_nonzero(finite & (sum(v*v for v in sample)>0)))
            elif isinstance(message, OccupancyGrid):
                item.update(width=message.info.width,height=message.info.height,
                            resolution=message.info.resolution,known_cells=sum(v>=0 for v in message.data))
            report['topics'][topic] = item
        for target, source in [('map','base_footprint'),('base_link','hesai_lidar'),
                               (args.model+'_link',args.model+'_color_optical_frame'),
                               (args.model+'_link',args.model+'_depth_optical_frame')]:
            try:
                transform = buffer.lookup_transform(target,source,rclpy.time.Time())
                v,q=transform.transform.translation,transform.transform.rotation
                report['tf'][target+'->'+source]={'xyz':[v.x,v.y,v.z],'xyzw':[q.x,q.y,q.z,q.w]}
            except Exception as error:
                report['tf'][target+'->'+source]={'error':str(error)}
        rgb=last.get('/camera/color/image_raw')
        if rgb is not None and rgb.encoding in ('rgb8','bgr8'):
            image=np.ndarray((rgb.height,rgb.width,3),dtype=np.uint8,
                buffer=rgb.data,strides=(rgb.step,3,1))
            if rgb.encoding=='rgb8':image=cv2.cvtColor(image,cv2.COLOR_RGB2BGR)
            path=args.output.with_suffix('.rgb.jpg')
            backup(path)
            if not cv2.imwrite(str(path),image):raise RuntimeError('RGB snapshot save failed')
            report['snapshots']['rgb']=str(path)
        def ready(topic):
            item=report['topics'][topic]
            return (item['count']>5 and item['latest_frame_age_s']<1 and item['stamp_backwards']==0
                    and item['min_stamp_age_s'] >= -.5 and item['max_stamp_age_s'] <= 2)
        report['depth_ready']=ready('/camera/depth/image_rect_raw')
        report['raw_rgb_depth_ready']=all(ready(t) for t in (
            '/camera/color/image_raw','/camera/depth/image_rect_raw'))
        report['rgb_depth_ready']=all(ready(t) for t in (
            '/camera/color/image_raw','/camera/depth/image_rect_raw','/camera/aligned_depth_to_color/image_raw'))
        calibration = {}
        for image_topic, info_topic in [
            ('/camera/color/image_raw','/camera/color/camera_info'),
            ('/camera/depth/image_rect_raw','/camera/depth/camera_info'),
            ('/camera/aligned_depth_to_color/image_raw','/camera/aligned_depth_to_color/camera_info'),
        ]:
            image, info = last.get(image_topic), last.get(info_topic)
            calibration[image_topic] = bool(image is not None and info is not None and
                ready(image_topic) and ready(info_topic) and
                image.width == info.width and image.height == info.height and
                image.header.frame_id == info.header.frame_id and
                all(math.isfinite(value) for value in info.k) and
                info.k[0] > 0 and info.k[4] > 0 and
                0 <= info.k[2] < info.width and 0 <= info.k[5] < info.height)
        color = last.get('/camera/color/image_raw')
        aligned = last.get('/camera/aligned_depth_to_color/image_raw')
        calibration['aligned_depth_matches_rgb_grid'] = bool(color is not None and aligned is not None and
            color.width == aligned.width and color.height == aligned.height and
            color.header.frame_id == aligned.header.frame_id)
        report['calibration_checks'] = calibration
        report['camera_calibration_ready'] = all(calibration.values())
        imu = report['topics']['/camera/imu']
        report['imu_ready']=(ready('/camera/imu') and imu.get('finite',False)
                             and .1 < imu.get('acceleration_norm',0) < 50)
        report['raw_camera_ready'] = (report['raw_rgb_depth_ready'] and report['imu_ready'] and
            calibration['/camera/color/image_raw'] and calibration['/camera/depth/image_rect_raw'])
        report['pointcloud_ready']=(ready('/camera/depth/color/points') and
            report['topics']['/camera/depth/color/points'].get('sampled_nonzero_points',0)>0)
        report['colored_pointcloud_ready'] = (report['pointcloud_ready'] and
            'rgb' in report['topics']['/camera/depth/color/points'].get('fields',[]))
        report['full_camera_ready'] = (report['rgb_depth_ready'] and report['camera_calibration_ready'] and
                                      report['imu_ready'] and report['colored_pointcloud_ready'])
        report['depth_only_ready']=report['depth_ready'] and report['pointcloud_ready']
        report['slam_ready']=all(ready(t) for t in ('/lidar_points','/scan','/odom')) and bool(last.get('/map'))
        report['base_link_camera_mount_verified']=False
        write(args.output,json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
