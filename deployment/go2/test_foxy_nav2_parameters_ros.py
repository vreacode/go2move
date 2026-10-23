"""Verify the real Foxy Nav2 plugins consume the corrected parameters.

Only lifecycle configuration is requested. No navigation or velocity goal is
sent, and the test is restricted to a separate localhost-only ROS domain.
"""
import argparse
import json
import os
import signal
import subprocess
from pathlib import Path

import rclpy
import yaml
from lifecycle_msgs.msg import Transition
from lifecycle_msgs.srv import ChangeState
from rcl_interfaces.srv import GetParameters


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if os.environ.get('ROS_DOMAIN_ID') != '88' or os.environ.get('RMW_IMPLEMENTATION') != 'rmw_cyclonedds_cpp':
        raise SystemExit('Requires ROS_DOMAIN_ID=88 and rmw_cyclonedds_cpp')
    if 'name="lo"' not in os.environ.get('CYCLONEDDS_URI', ''):
        raise SystemExit('Requires CycloneDDS bound only to lo')
    config = yaml.safe_load(Path(args.config).read_text())
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {'config': args.config, 'ros_domain_id': 88, 'checks': {},
              'navigation_goals_sent': 0, 'robot_requests_sent': 0}
    children = []
    logs = []
    clients = []
    rclpy.init()
    node = rclpy.create_node('foxy_nav2_parameter_test', enable_rosout=False)

    def call(kind, path, request):
        client = node.create_client(kind, path)
        clients.append(client)
        assert client.wait_for_service(timeout_sec=15), path + ' unavailable'
        future = client.call_async(request)
        rclpy.spin_until_future_complete(node, future, timeout_sec=15)
        assert future.done() and future.result() is not None, path + ' timed out'
        return future.result()

    def check(name, condition):
        report['checks'][name] = bool(condition)
        assert condition, name

    try:
        for package, executable in [('nav2_planner', 'planner_server'),
                                    ('nav2_controller', 'controller_server')]:
            log = output.with_name(output.stem + '_' + executable + '.log').open('w')
            logs.append(log)
            children.append(subprocess.Popen([
                '/opt/ros/foxy/lib/' + package + '/' + executable,
                '--ros-args', '--params-file', args.config,
                '-r', '__node:=' + executable,
                '-r', 'cmd_vel:=/audit/cmd_vel_preview',
            ], stdout=log, stderr=subprocess.STDOUT, start_new_session=True))
            request = ChangeState.Request()
            request.transition.id = Transition.TRANSITION_CONFIGURE
            response = call(ChangeState, '/' + executable + '/change_state', request)
            check(executable + '_configured', response.success)

        for costmap in ['global_costmap', 'local_costmap']:
            scan = config[costmap][costmap]['ros__parameters']['obstacle_layer']['scan']
            names = ['obstacle_layer.scan.obstacle_range', 'obstacle_layer.scan.raytrace_range']
            response = call(GetParameters, '/' + costmap + '/' + costmap + '/get_parameters',
                            GetParameters.Request(names=names))
            actual = [value.double_value for value in response.values]
            check(costmap + '_obstacle_ranges_consumed', actual == [scan['obstacle_range'], scan['raytrace_range']])
            report[costmap] = dict(zip(names, actual))

        follow = config['controller_server']['ros__parameters']['FollowPath']
        names = ['FollowPath.max_allowed_time_to_collision', 'FollowPath.goal_dist_tol']
        response = call(GetParameters, '/controller_server/get_parameters', GetParameters.Request(names=names))
        actual = [value.double_value for value in response.values]
        check('controller_collision_and_goal_parameters_consumed',
              actual == [follow['max_allowed_time_to_collision'], follow['goal_dist_tol']])
        report['controller'] = dict(zip(names, actual))
        check('no_robot_command_publisher', node.count_publishers('/api/sport/request') == 0)
        report['passed'] = True
    except Exception as error:
        report['passed'] = False
        report['error'] = str(error)
        raise
    finally:
        node.destroy_node()
        rclpy.shutdown()
        for process in children:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
        for log in logs:
            log.close()
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
        print(json.dumps({'passed': report.get('passed'), 'checks': len(report['checks']),
                          'output': str(output), 'error': report.get('error')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
