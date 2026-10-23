"""Exercise the compiled upstream driver on an isolated ROS network.

Synthetic messages verify protocol behavior; they are not hardware evidence.
The script requires a dedicated localhost-only ROS domain and destroys all
owned child processes when finished.
"""
import argparse
import json
import math
import os
import signal
import struct
import subprocess
import time
from pathlib import Path

import rclpy
from composition_interfaces.srv import LoadNode
from geometry_msgs.msg import PoseStamped, Twist
from go2_interfaces import srv
from nav_msgs.msg import Odometry
from rcl_interfaces.srv import SetParametersAtomically
from rclpy.parameter import Parameter
from sensor_msgs.msg import PointCloud2, PointField
from unitree_api.msg import Request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if os.environ.get('ROS_DOMAIN_ID') != '87' or os.environ.get('RMW_IMPLEMENTATION') != 'rmw_cyclonedds_cpp':
        raise SystemExit('Requires ROS_DOMAIN_ID=87 and rmw_cyclonedds_cpp')
    if 'name="lo"' not in os.environ.get('CYCLONEDDS_URI', ''):
        raise SystemExit('Requires a CycloneDDS configuration bound only to lo')

    report = {'synthetic_inputs': True, 'ros_domain_id': 87, 'checks': {}}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    log = output.with_suffix('.log').open('w')
    rclpy.init()
    node = rclpy.create_node('go2_protocol_test', enable_rosout=False)
    process = subprocess.Popen([
        '/opt/ros/foxy/lib/rclcpp_components/component_container',
        '--ros-args', '-r', '__node:=go2_test_container',
    ], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    clients = []
    subscriptions = []
    received = []
    odom = []
    clouds = []

    def spin(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            rclpy.spin_once(node, timeout_sec=0.02)

    def call(kind, path, request):
        client = node.create_client(kind, path)
        clients.append(client)
        assert client.wait_for_service(timeout_sec=12), path + ' unavailable'
        future = client.call_async(request)
        rclpy.spin_until_future_complete(node, future, timeout_sec=5)
        assert future.done() and future.result() is not None, path + ' timed out'
        return future.result()

    def check(name, condition):
        report['checks'][name] = bool(condition)
        assert condition, name

    cases = [
        ('body_height', srv.BodyHeight, {'height': 0.0}, 1013, {'data': 0.0}),
        ('continuous_gait', srv.ContinuousGait, {'flag': True}, 1019, {'data': True}),
        ('euler', srv.Euler, {'roll': 0.1, 'pitch': 0.1, 'yaw': 0.1}, 1007, None),
        ('foot_raise_height', srv.FootRaiseHeight, {'height': 0.05}, 1014, None),
        ('mode', srv.Mode, {'mode': 'hello'}, 1016, None),
        ('pose', srv.Pose, {'flag': False}, 1028, {'data': False}),
        ('speed_level', srv.SpeedLevel, {'level': 0}, 1015, {'data': 0}),
        ('switch_gait', srv.SwitchGait, {'d': 0}, 1011, {'data': 0}),
        ('switch_joystick', srv.SwitchJoystick, {'flag': True}, 1027, {'data': True}),
    ]
    try:
        for namespace, enabled in [('blocked', False), ('enabled', True)]:
            request = LoadNode.Request()
            request.package_name = 'go2_driver'
            request.plugin_name = 'go2_driver::Go2Driver'
            request.node_name = 'go2_driver'
            request.node_namespace = '/' + namespace
            request.remap_rules = ['/utlidar/cloud:=/audit/cloud',
                                   '/utlidar/robot_pose:=/audit/pose']
            request.parameters = [Parameter('motion_enabled', value=enabled).to_parameter_msg(),
                                  Parameter('publish_state', value=enabled).to_parameter_msg(),
                                  Parameter('preserve_cloud_header', value=True).to_parameter_msg()]
            result = call(LoadNode, '/go2_test_container/_container/load_node', request)
            check(namespace + '_component_load', result.success)

        subscriptions.append(node.create_subscription(Request, '/enabled/api/sport/request',
                                                        received.append, 100))
        subscriptions.append(node.create_subscription(Odometry, '/enabled/odom', odom.append, 10))
        subscriptions.append(node.create_subscription(PointCloud2, '/enabled/pointcloud', clouds.append, 10))
        velocity = node.create_publisher(Twist, '/enabled/cmd_vel', 10)
        blocked_velocity = node.create_publisher(Twist, '/blocked/cmd_vel', 10)
        pose_publisher = node.create_publisher(PoseStamped, '/audit/pose', 10)
        cloud_publisher = node.create_publisher(PointCloud2, '/audit/cloud', 10)
        spin(2)

        for path, kind, fields, _, _ in cases:
            response = call(kind, '/blocked/' + path, kind.Request(**fields))
            check('blocked_' + path, not response.success and
                  (not hasattr(response, 'message') or 'motion_enabled=false' in response.message))
        attempted = Twist()
        attempted.linear.x = 0.1
        blocked_velocity.publish(attempted)
        spin(0.2)
        check('blocked_has_no_robot_command_publisher',
              node.count_publishers('/blocked/api/sport/request') == 0)
        mutation = SetParametersAtomically.Request()
        mutation.parameters = [Parameter('motion_enabled', value=True).to_parameter_msg()]
        result = call(SetParametersAtomically, '/blocked/go2_driver/set_parameters_atomically', mutation)
        check('motion_parameter_immutable', not result.result.successful)

        for path, kind, fields, api, expected in cases:
            before = len(received)
            response = call(kind, '/enabled/' + path, kind.Request(**fields))
            spin(0.1)
            check('accepted_' + path, response.success)
            messages = received[before:]
            check('encoded_' + path, len(messages) == 1 and messages[0].header.identity.api_id == api
                  and messages[0].header.identity.id > 0)
            if expected is not None:
                check('payload_' + path, json.loads(messages[0].parameter) == expected)
        check('request_ids_unique', len({m.header.identity.id for m in received}) == len(received))

        for path, kind, fields in [
            ('body_height', srv.BodyHeight, {'height': float('nan')}),
            ('foot_raise_height', srv.FootRaiseHeight, {'height': float('inf')}),
            ('euler', srv.Euler, {'roll': float('nan')}),
            ('mode', srv.Mode, {'mode': 'front_flip'}),
            ('mode', srv.Mode, {'mode': 'unknown'}),
            ('speed_level', srv.SpeedLevel, {'level': 2}),
            ('switch_gait', srv.SwitchGait, {'d': 99}),
        ]:
            before = len(received)
            response = call(kind, '/enabled/' + path, kind.Request(**fields))
            spin(0.1)
            check('invalid_' + path + '_' + str(fields), not response.success and len(received) == before)

        before = len(received)
        command = Twist()
        command.linear.x = command.linear.y = command.angular.z = 1.0
        velocity.publish(command)
        spin(0.65)
        packets = received[before:]
        moves = [json.loads(m.parameter) for m in packets if m.header.identity.api_id == 1008]
        check('cmd_vel_linear_limit', bool(moves) and abs(math.hypot(moves[0]['x'], moves[0]['y']) - 0.2) < 1e-6)
        check('cmd_vel_angular_limit', abs(moves[0]['z'] - 0.2) < 1e-6)
        check('cmd_vel_watchdog_zero_and_stop', len(moves) >= 2 and moves[-1] == {'x': 0.0, 'y': 0.0, 'z': 0.0}
              and any(m.header.identity.api_id == 1003 for m in packets))
        before = len(received)
        command.linear.x = float('nan')
        velocity.publish(command)
        spin(0.4)
        check('nonfinite_velocity_rejected', len(received) == before)

        for x in [0.1, 0.2, 0.3]:
            pose = PoseStamped()
            pose.header.stamp = node.get_clock().now().to_msg()
            pose.pose.position.x = x
            pose.pose.orientation.w = 1.0
            pose_publisher.publish(pose)
            spin(0.15)
        check('odom_publishes_repeated_poses', len(odom) >= 3
              and abs(odom[-1].pose.pose.position.x - 0.3) < 1e-6)
        cloud = PointCloud2()
        cloud.header.stamp = node.get_clock().now().to_msg()
        cloud.header.frame_id = 'hesai_lidar'
        cloud.height = cloud.width = 1
        cloud.fields = [PointField(name=name, offset=index * 4, datatype=PointField.FLOAT32, count=1)
                        for index, name in enumerate(['x', 'y', 'z'])]
        cloud.point_step = cloud.row_step = 12
        cloud.data = struct.pack('<fff', 1.0, 2.0, 3.0)
        cloud_publisher.publish(cloud)
        spin(0.25)
        check('cloud_frame_stamp_and_data_preserved', bool(clouds) and clouds[-1].header == cloud.header
              and bytes(clouds[-1].data) == bytes(cloud.data))
        report['passed'] = True
        report['request_count'] = len(received)
    except Exception as error:
        report['passed'] = False
        report['error'] = str(error)
        raise
    finally:
        node.destroy_node()
        rclpy.shutdown()
        os.killpg(process.pid, signal.SIGINT)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        report['child_exit_code'] = process.returncode
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
        log.close()
        print(json.dumps({'passed': report.get('passed'), 'checks': len(report['checks']),
                          'output': str(output), 'error': report.get('error')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
