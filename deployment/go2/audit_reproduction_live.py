"""Receive-only evidence for the live Go2 perception and standard interfaces."""
import argparse
import collections
import json
import time
from pathlib import Path

import rclpy
from nav_msgs.msg import OccupancyGrid, Odometry
from rcl_interfaces.srv import GetParameters
from rclpy.qos import DurabilityPolicy, QoSProfile, qos_profile_sensor_data
from sensor_msgs.msg import Imu, JointState, LaserScan, PointCloud2
from tf2_ros import Buffer, TransformListener


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    rclpy.init()
    node = rclpy.create_node('go2_reproduction_live_observer', enable_rosout=False)
    buffer = Buffer()
    listener = TransformListener(buffer, node)
    records = collections.defaultdict(list)
    last = {}
    subscribers = []
    sampling = False

    def receive(topic, message):
        last[topic] = message
        if sampling:
            records[topic].append(time.monotonic())

    for topic, kind, qos in [
        ('/lidar_points', PointCloud2, qos_profile_sensor_data),
        ('/pointcloud', PointCloud2, qos_profile_sensor_data),
        ('/scan', LaserScan, qos_profile_sensor_data),
        ('/odom', Odometry, qos_profile_sensor_data),
        ('/joint_states', JointState, qos_profile_sensor_data),
        ('/imu/data', Imu, qos_profile_sensor_data),
        ('/map', OccupancyGrid, QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)),
    ]:
        subscribers.append(node.create_subscription(kind, topic, lambda m, t=topic: receive(t, m), qos))

    def spin(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            rclpy.spin_once(node, timeout_sec=0.02)

    report = {'readonly': True, 'robot_commands_sent': 0, 'navigation_goals_sent': 0,
              'topics': {}, 'tf': {}, 'checks': {}}
    try:
        spin(12)
        sampling = True
        spin(15)
        sampling = False
        for topic in last:
            timestamps = records[topic]
            message = last[topic]
            item = {'count': len(timestamps),
                    'hz': (len(timestamps) - 1) / (timestamps[-1] - timestamps[0]) if len(timestamps) > 1 else 0}
            if hasattr(message, 'header'):
                item['frame'] = message.header.frame_id
                item['age_s'] = time.time() - message.header.stamp.sec - message.header.stamp.nanosec / 1e9
            if isinstance(message, PointCloud2):
                item['points'] = message.width * message.height
            report['topics'][topic] = item
        for target, source in [('map', 'base_footprint'), ('odom', 'base_footprint'),
                               ('base_link', 'hesai_lidar')]:
            try:
                transform = buffer.lookup_transform(target, source, rclpy.time.Time())
                xyz = transform.transform.translation
                report['tf'][target + '->' + source] = {'xyz': [xyz.x, xyz.y, xyz.z], 'available': True}
            except Exception as error:
                report['tf'][target + '->' + source] = {'available': False, 'error': str(error)}
        services = dict(node.get_service_names_and_types())
        expected = ['/mode', '/body_height', '/continuous_gait', '/euler', '/foot_raise_height',
                    '/pose', '/speed_level', '/switch_gait', '/switch_joystick']
        report['services'] = {path: services.get(path) for path in expected}
        client = node.create_client(GetParameters, '/go2_driver/get_parameters')
        assert client.wait_for_service(timeout_sec=5), 'Go2 parameter service unavailable'
        future = client.call_async(GetParameters.Request(names=['motion_enabled', 'publish_state', 'preserve_cloud_header']))
        rclpy.spin_until_future_complete(node, future, timeout_sec=5)
        assert future.done() and future.result() is not None, 'Go2 parameter query timeout'
        values = [value.bool_value for value in future.result().values]
        report['driver_parameters'] = dict(zip(['motion_enabled', 'publish_state', 'preserve_cloud_header'], values))
        report['robot_command_publishers'] = {
            path: [entry.node_name for entry in node.get_publishers_info_by_topic(path)]
            for path in ['/api/sport/request', '/cmd_vel', '/lowcmd']}
        report['checks']['nine_standard_services_available'] = all(report['services'].values())
        report['checks']['driver_motion_and_duplicate_state_disabled'] = values == [False, False, True]
        report['checks']['no_robot_command_publishers'] = not any(report['robot_command_publishers'].values())
        report['checks']['perception_topics_live'] = all(len(records[path]) >= 5 for path in
                                                       ['/lidar_points', '/pointcloud', '/scan', '/odom', '/joint_states', '/imu/data'])
        report['checks']['map_live'] = len(records['/map']) >= 3
        report['checks']['tf_chain_available'] = all(item['available'] for item in report['tf'].values())
        report['passed'] = all(report['checks'].values())
    except Exception as error:
        report['passed'] = False
        report['error'] = str(error)
        raise
    finally:
        node.destroy_node()
        rclpy.shutdown()
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
        print(json.dumps(report, ensure_ascii=False))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
