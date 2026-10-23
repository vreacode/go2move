#!/usr/bin/env python3
"""Bounded, planning-only Nav2 Foxy waypoint reachability preview.

JSON stdin: {"points": [{"name": "A", "x": 1, "y": 0, "yaw": 0}], "frame_id": "map"}
JSON stdout contains paths/errors; this tool writes no map or output files.

Reuses installed NavfnPlanner through the standard action and geometry2 TF.
Foxy 0.4.7 has no explicit start pose: every path starts at the current TF pose.
Multiple paths are reachability checks, never an asserted waypoint-to-waypoint route.
Upstream: https://github.com/ros-navigation/navigation2/blob/0.4.7/nav2_planner/src/planner_server.cpp
"""
import argparse
import json
import math
import os
import sys
import time


class PreviewError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PreviewError('invalid_input', field + ' must be a finite number')
    return float(value)


def validate_request(payload):
    if not isinstance(payload, dict) or set(payload) - {'points', 'frame_id'}:
        raise PreviewError('invalid_input', 'Expected points and optional frame_id only')
    frame = payload.get('frame_id', 'map')
    if frame != 'map':
        raise PreviewError('invalid_input', 'Only the map frame is supported')
    points = payload.get('points')
    if not isinstance(points, list) or not 1 <= len(points) <= 20:
        raise PreviewError('invalid_input', 'points must contain 1 to 20 waypoints')
    normalized, names = [], set()
    for index, point in enumerate(points):
        if not isinstance(point, dict) or set(point) - {'name', 'x', 'y', 'yaw'}:
            raise PreviewError('invalid_input', 'Invalid waypoint fields')
        name = point.get('name', 'point_' + str(index + 1))
        if not isinstance(name, str) or not name.strip() or len(name) > 64 or name in names:
            raise PreviewError('invalid_input', 'Waypoint names must be unique, nonempty and at most 64 characters')
        names.add(name)
        values = {key: number(point.get(key), name + '.' + key) for key in ('x', 'y', 'yaw')}
        if abs(values['x']) > 10000 or abs(values['y']) > 10000:
            raise PreviewError('invalid_input', 'Waypoint coordinates exceed 10000 m')
        values['yaw'] = math.atan2(math.sin(values['yaw']), math.cos(values['yaw']))
        normalized.append(dict(values, name=name))
    return {'frame_id': frame, 'points': normalized}


def yaw_of(quaternion):
    values = [number(getattr(quaternion, key), 'quaternion.' + key) for key in ('x', 'y', 'z', 'w')]
    x, y, z, w = values
    norm = math.sqrt(sum(value * value for value in values))
    if not .99 <= norm <= 1.01:
        raise PreviewError('invalid_geometry', 'Quaternion is not normalized')
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def validate_grid(grid, frame='map'):
    info = grid.info
    resolution = number(info.resolution, 'map.resolution')
    if grid.header.frame_id != frame or resolution <= 0 or info.width <= 0 or info.height <= 0:
        raise PreviewError('map_invalid', 'Map frame, size or resolution is invalid')
    if info.width * info.height != len(grid.data):
        raise PreviewError('map_invalid', 'Map data length does not match its dimensions')
    if any(value < -1 or value > 100 for value in grid.data):
        raise PreviewError('map_invalid', 'Map contains invalid occupancy values')
    return {'frame_id': frame, 'width': int(info.width), 'height': int(info.height),
            'resolution': resolution, 'origin': [number(info.origin.position.x, 'map.origin.x'),
            number(info.origin.position.y, 'map.origin.y'), yaw_of(info.origin.orientation)]}


def check_goal_cell(point, grid, metadata):
    ox, oy, heading = metadata['origin']
    dx, dy = point['x'] - ox, point['y'] - oy
    c, s = math.cos(heading), math.sin(heading)
    ix = math.floor((c * dx + s * dy) / metadata['resolution'])
    iy = math.floor((-s * dx + c * dy) / metadata['resolution'])
    if not 0 <= ix < metadata['width'] or not 0 <= iy < metadata['height']:
        raise PreviewError('goal_outside_map', point['name'] + ' lies outside the map')
    occupancy = int(grid.data[iy * metadata['width'] + ix])
    if occupancy < 0 or occupancy >= 65:
        raise PreviewError('goal_not_free', point['name'] + ' is unknown or occupied on the map')


def summarize_path(path, point, frame, endpoint_tolerance):
    if path.header.frame_id != frame or not path.poses:
        raise PreviewError('planning_failed', 'Planner returned an empty path or wrong frame')
    if len(path.poses) > 20000:
        raise PreviewError('path_too_large', 'Planner returned more than 20000 poses')
    coordinates = []
    for pose in path.poses:
        if pose.header.frame_id not in ('', frame):
            raise PreviewError('planning_failed', 'Path pose has a different frame')
        coordinates.append([number(pose.pose.position.x, 'path.x'), number(pose.pose.position.y, 'path.y')])
    length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(coordinates, coordinates[1:]))
    error = math.hypot(coordinates[-1][0] - point['x'], coordinates[-1][1] - point['y'])
    if error > endpoint_tolerance:
        raise PreviewError('endpoint_mismatch', 'Planner endpoint exceeds the configured tolerance')
    return {'path': coordinates, 'poses': len(coordinates), 'length_m': length, 'endpoint_error_m': error}


def preview(request, backend, timeout=15):
    report = {'passed': False, 'planning_only': True, 'motion_sent': False,
              'scope': 'current_pose_to_each_point', 'ordered_route_validated': False,
              'frame_id': request['frame_id'], 'segments': [],
              'warning': 'Foxy plans each waypoint from the current TF pose; connecting these paths is not a validated patrol route.'}
    started = time.monotonic()
    deadline = started + timeout
    try:
        report['preflight'] = backend.preflight(deadline)
        for index, point in enumerate(request['points']):
            segment = {'index': index, 'name': point['name'], 'goal': point, 'success': False}
            report['segments'].append(segment)
            try:
                segment.update(backend.plan(point, deadline))
                segment['success'] = True
            except PreviewError as error:
                segment['error'] = {'code': error.code, 'message': str(error)}
                raise
        report['passed'] = True
    except PreviewError as error:
        report['error'] = {'code': error.code, 'message': str(error)}
    finally:
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
    return report


class RosPlanner:
    """Only subscriptions, lifecycle GetState and ComputePathToPose requests."""
    def __init__(self, args):
        import rclpy
        from rclpy.action import ActionClient
        from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
        from nav_msgs.msg import OccupancyGrid
        from nav2_msgs.action import ComputePathToPose
        from lifecycle_msgs.srv import GetState
        from tf2_ros import Buffer, TransformListener
        self.args, self.rclpy, self.action_type, self.state_type = args, rclpy, ComputePathToPose, GetState
        self.maps, self.map_metadata = {}, None
        rclpy.init(args=[])
        self.node = rclpy.create_node('go2_search_route_preview', enable_rosout=False)
        self.buffer = Buffer()
        self.listener = TransformListener(self.buffer, self.node)
        qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.RELIABLE)
        self.subscriptions = [self.node.create_subscription(OccupancyGrid, topic,
            lambda message, key=key: self.maps.update({key: message}), qos)
            for key, topic in [('map', args.map_topic), ('costmap', args.costmap_topic)]]
        self.lifecycle = self.node.create_client(GetState, args.planner_node.rstrip('/') + '/get_state')
        self.action = ActionClient(self.node, ComputePathToPose, args.action_name)

    def remaining(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PreviewError('timeout', 'Planning preview deadline expired')
        return remaining

    def wait(self, future, deadline, code):
        self.rclpy.spin_until_future_complete(self.node, future, timeout_sec=self.remaining(deadline))
        if not future.done():
            raise PreviewError(code, 'ROS request timed out')
        if future.exception() is not None or future.result() is None:
            raise PreviewError('ros_error', str(future.exception() or 'Empty ROS response'))
        return future.result()

    def current_pose(self):
        try:
            transform = self.buffer.lookup_transform('map', self.args.base_frame, self.rclpy.time.Time())
        except Exception as error:
            raise PreviewError('tf_unavailable', str(error))
        stamp = transform.header.stamp.sec + transform.header.stamp.nanosec / 1e9
        now = self.node.get_clock().now().nanoseconds / 1e9
        if stamp and not -.25 <= now - stamp <= self.args.tf_max_age:
            raise PreviewError('tf_stale', 'Current map-to-base transform is stale')
        return {'x': number(transform.transform.translation.x, 'start.x'),
                'y': number(transform.transform.translation.y, 'start.y'),
                'yaw': yaw_of(transform.transform.rotation)}

    def active(self, deadline):
        if not self.lifecycle.service_is_ready():
            raise PreviewError('planner_unavailable', 'Planner lifecycle service is unavailable')
        response = self.wait(self.lifecycle.call_async(self.state_type.Request()), deadline, 'planner_timeout')
        if response.current_state.id != 3:
            raise PreviewError('planner_inactive', 'Planner lifecycle state is ' + response.current_state.label)
        return {'id': int(response.current_state.id), 'label': response.current_state.label}

    def preflight(self, deadline):
        until = min(deadline, time.monotonic() + self.args.preflight_timeout)
        last_tf_error = None
        while time.monotonic() < until:
            self.rclpy.spin_once(self.node, timeout_sec=max(0., min(.05, until - time.monotonic())))
            try:
                self.current_pose()
                last_tf_error = None
            except PreviewError as error:
                last_tf_error = error
            if len(self.maps) == 2 and last_tf_error is None and self.lifecycle.service_is_ready() and self.action.server_is_ready():
                break
        if 'map' not in self.maps:
            raise PreviewError('map_unavailable', 'No map was received')
        if 'costmap' not in self.maps:
            raise PreviewError('costmap_unavailable', 'No global costmap was received')
        self.map_metadata = validate_grid(self.maps['map'])
        costmap = validate_grid(self.maps['costmap'])
        start = self.current_pose()
        state = self.active(until)
        if not self.action.server_is_ready():
            raise PreviewError('planner_unavailable', 'Planning action server is unavailable')
        return {'map': self.map_metadata, 'costmap': costmap, 'start': start, 'planner_state': state,
                'planner_id': self.args.planner_id, 'base_frame': self.args.base_frame}

    def plan(self, point, deadline):
        deadline = min(deadline, time.monotonic() + self.args.goal_timeout)
        self.active(deadline)
        start = self.current_pose()
        metadata = validate_grid(self.maps['map'])
        validate_grid(self.maps['costmap'])
        check_goal_cell(point, self.maps['map'], metadata)
        goal = self.action_type.Goal()
        goal.pose.header.frame_id = 'map'
        goal.pose.header.stamp = self.node.get_clock().now().to_msg()
        goal.pose.pose.position.x, goal.pose.pose.position.y = point['x'], point['y']
        goal.pose.pose.orientation.z, goal.pose.pose.orientation.w = math.sin(point['yaw'] / 2), math.cos(point['yaw'] / 2)
        goal.planner_id = self.args.planner_id
        handle = None
        try:
            handle = self.wait(self.action.send_goal_async(goal), deadline, 'goal_response_timeout')
            if not handle.accepted:
                raise PreviewError('goal_rejected', 'Planner rejected the planning request')
            response = self.wait(handle.get_result_async(), deadline, 'planning_timeout')
            if response.status != 4:
                raise PreviewError('planning_failed', 'Planner action status=' + str(response.status))
            result = summarize_path(response.result.path, point, 'map', self.args.endpoint_tolerance)
            result.update(start=start, action_status=int(response.status),
                planning_seconds=response.result.planning_time.sec + response.result.planning_time.nanosec / 1e9)
            return result
        except PreviewError:
            if handle is not None and handle.accepted:
                # Cancellation applies only to this planning request.
                future = handle.cancel_goal_async()
                self.rclpy.spin_until_future_complete(self.node, future, timeout_sec=.2)
            raise

    def close(self):
        self.action.destroy()
        self.listener.unregister()
        self.node.destroy_node()
        self.rclpy.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=float, default=15, help='Total deadline, 1 to 30 seconds')
    parser.add_argument('--preflight-timeout', type=float, default=5)
    parser.add_argument('--goal-timeout', type=float, default=5)
    parser.add_argument('--tf-max-age', type=float, default=2)
    parser.add_argument('--endpoint-tolerance', type=float, default=.35)
    parser.add_argument('--base-frame', default='base_footprint')
    parser.add_argument('--planner-id', default='GridBased')
    parser.add_argument('--planner-node', default='/planner_server')
    parser.add_argument('--action-name', default='/compute_path_to_pose')
    parser.add_argument('--map-topic', default='/map')
    parser.add_argument('--costmap-topic', default='/global_costmap/costmap')
    parser.add_argument('--validate-only', action='store_true', help='Validate JSON without ROS; does not validate any path')
    args = parser.parse_args()
    report = {'passed': False, 'planning_only': True, 'motion_sent': False, 'ordered_route_validated': False, 'segments': []}
    backend = None
    output_fd = None
    try:
        if not 1 <= args.timeout <= 30 or any(not math.isfinite(value) or value <= 0 for value in
                (args.preflight_timeout, args.goal_timeout, args.tf_max_age, args.endpoint_tolerance)):
            raise PreviewError('invalid_input', 'Timeout/tolerance options are invalid')
        payload = sys.stdin.read(65537)
        if len(payload) > 65536:
            raise PreviewError('invalid_input', 'JSON input exceeds 64 KiB')
        request = validate_request(json.loads(payload))
        if args.validate_only:
            report.update(input_valid=True, path_checked=False, request=request)
        else:
            # Keep native ROS logs on stderr so stdout contains one JSON object.
            sys.stdout.flush()
            output_fd = os.dup(sys.stdout.fileno())
            os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
            backend = RosPlanner(args)
            report = preview(request, backend, args.timeout)
    except Exception as error:
        report['error'] = {'code': getattr(error, 'code', 'runtime_error'), 'message': str(error)}
    finally:
        if backend is not None:
            try:
                backend.close()
            except Exception as error:
                report['cleanup_error'] = str(error)
                report['passed'] = False
        if output_fd is not None:
            sys.stdout.flush()
            os.dup2(output_fd, sys.stdout.fileno())
            os.close(output_fd)
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    return 0 if report.get('passed') or report.get('input_valid') else 1


if __name__ == '__main__':
    raise SystemExit(main())
