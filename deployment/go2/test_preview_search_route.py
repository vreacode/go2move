#!/usr/bin/env python3
"""Offline route-preview checks. No ROS initialization, action goal or robot I/O."""
import ast
import copy
import math
from pathlib import Path
from types import SimpleNamespace as Obj
import unittest
from unittest.mock import Mock
import preview_search_route as tool


def grid():
    return Obj(header=Obj(frame_id='map'), info=Obj(resolution=1., width=3, height=3,
        origin=Obj(position=Obj(x=0., y=0.), orientation=Obj(x=0., y=0., z=0., w=1.))), data=[0]*9)


def path(points):
    return Obj(header=Obj(frame_id='map'), poses=[Obj(header=Obj(frame_id='map'),
        pose=Obj(position=Obj(x=float(x), y=float(y)))) for x, y in points])


def pose_goal():
    return Obj(pose=Obj(header=Obj(frame_id='', stamp=None), pose=Obj(
        position=Obj(x=0., y=0.), orientation=Obj(z=0., w=1.))), planner_id='')


class FakeBackend:
    def __init__(self, failure=None):
        self.failure, self.calls = failure, []

    def preflight(self, deadline):
        if self.failure:
            raise tool.PreviewError(self.failure, 'Offline simulated refusal')
        return {'planner_state': {'id': 3}}

    def plan(self, point, deadline):
        self.calls.append(point)
        return {'path': [[0., 0.], [point['x'], point['y']]], 'start': {'x': 0., 'y': 0., 'yaw': 0.}}


class RoutePreviewTests(unittest.TestCase):
    def request(self, **changes):
        request = {'points': [{'name': 'A', 'x': 1., 'y': 1., 'yaw': 0.}], 'frame_id': 'map'}
        request.update(changes)
        return request

    def test_rejects_bad_geometry_and_unexpected_input(self):
        cases = [self.request(points=[]), self.request(frame_id='odom'), self.request(execute=True)]
        for value in [True, float('nan'), float('inf'), '1']:
            bad = self.request()
            bad['points'][0]['x'] = value
            cases.append(bad)
        duplicate = self.request()
        duplicate['points'] *= 2
        cases.append(duplicate)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(tool.PreviewError):
                tool.validate_request(case)

    def test_rotated_map_and_occupied_unknown_outside_goals(self):
        message = grid()
        message.info.origin.orientation.z = math.sin(math.pi/4)
        message.info.origin.orientation.w = math.cos(math.pi/4)
        metadata = tool.validate_grid(message)
        point = {'name': 'A', 'x': -.5, 'y': .5}
        tool.check_goal_cell(point, message, metadata)
        for occupancy in [-1, 65, 100]:
            message.data[0] = occupancy
            with self.assertRaises(tool.PreviewError) as caught:
                tool.check_goal_cell(point, message, metadata)
            self.assertEqual(caught.exception.code, 'goal_not_free')
        message.data[0] = 0
        with self.assertRaises(tool.PreviewError) as caught:
            tool.check_goal_cell({'name':'outside', 'x':10., 'y':10.}, message, metadata)
        self.assertEqual(caught.exception.code, 'goal_outside_map')

    def test_malformed_maps_and_paths_are_refused(self):
        message = grid()
        message.data.pop()
        with self.assertRaises(tool.PreviewError):
            tool.validate_grid(message)
        goal = {'x':1., 'y':1.}
        for result in [path([]), path([(0.,0.)]), path([(float('nan'),1.)])]:
            with self.assertRaises(tool.PreviewError):
                tool.summarize_path(result, goal, 'map', .35)
        result = tool.summarize_path(path([(0.,0.),(1.,1.)]), goal, 'map', .35)
        self.assertAlmostEqual(result['length_m'], math.sqrt(2))

    def test_preflight_failures_never_plan(self):
        for code in ['map_unavailable', 'tf_unavailable', 'planner_inactive', 'costmap_unavailable']:
            backend = FakeBackend(code)
            result = tool.preview(tool.validate_request(self.request()), backend)
            self.assertFalse(result['passed'])
            self.assertEqual(result['error']['code'], code)
            self.assertEqual(backend.calls, [])

    def test_multiple_points_never_claim_a_connected_route(self):
        request = self.request(points=[{'name':'A','x':1.,'y':1.,'yaw':0.},
                                       {'name':'B','x':2.,'y':1.,'yaw':0.}])
        backend = FakeBackend()
        result = tool.preview(tool.validate_request(request), backend)
        self.assertTrue(result['passed'])
        self.assertFalse(result['ordered_route_validated'])
        self.assertFalse(result['motion_sent'])
        self.assertEqual(result['scope'], 'current_pose_to_each_point')
        self.assertEqual([segment['start']['x'] for segment in result['segments']], [0., 0.])

    def test_planning_timeout_cancels_only_planning_handle(self):
        backend = tool.RosPlanner.__new__(tool.RosPlanner)
        backend.args = Obj(goal_timeout=5., endpoint_tolerance=.35, planner_id='GridBased')
        backend.maps = {'map':grid(), 'costmap':grid()}
        backend.active = Mock()
        backend.current_pose = Mock(return_value={'x':0.,'y':0.,'yaw':0.})
        backend.node = Obj(get_clock=lambda: Obj(now=lambda: Obj(to_msg=lambda: None)))
        backend.action_type = Obj(Goal=pose_goal)
        handle = Obj(accepted=True, get_result_async=Mock(return_value='result'), cancel_goal_async=Mock(return_value='cancel'))
        backend.action = Obj(send_goal_async=Mock(return_value='accept'))
        backend.rclpy = Obj(spin_until_future_complete=Mock())
        def wait(future, deadline, code):
            if future == 'accept':
                return handle
            raise tool.PreviewError(code, 'Timeout')
        backend.wait = wait
        with self.assertRaises(tool.PreviewError) as caught:
            backend.plan({'name':'A','x':1.,'y':1.,'yaw':0.}, tool.time.monotonic()+10)
        self.assertEqual(caught.exception.code, 'planning_timeout')
        handle.cancel_goal_async.assert_called_once()
        sent = backend.action.send_goal_async.call_args[0][0]
        self.assertEqual(sent.planner_id, 'GridBased')
        self.assertEqual(sent.pose.header.frame_id, 'map')

    def test_control_api_boundary(self):
        tree = ast.parse(Path(tool.__file__).read_text())
        imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module == 'nav2_msgs.action' for alias in node.names]
        self.assertEqual(imports, ['ComputePathToPose'])
        calls = [node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
        self.assertNotIn('create_publisher', calls)
        self.assertNotIn('write_text', calls)
        self.assertNotIn('write_bytes', calls)
        clients = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'create_client']
        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0].args[0].id, 'GetState')


if __name__ == '__main__':
    unittest.main()
