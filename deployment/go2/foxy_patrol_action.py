"""Foxy NavigateToPose adapter restricted to the isolated software test graph.

Matches installed nav2_msgs 0.4.7 and the upstream waypoint-follower action flow.
No SDK, cmd_vel, controller startup, or live-domain execution is provided.
https://github.com/ros-navigation/navigation2/blob/0.4.7/nav2_waypoint_follower/src/waypoint_follower.cpp
"""
import asyncio
import math
import os

ACTION_NAME = '/go2_patrol_sim/navigate_to_pose'


def require_isolated_graph():
    if (os.environ.get('ROS_DOMAIN_ID') != '77' or os.environ.get('ROS_LOCALHOST_ONLY') != '1'
            or os.environ.get('RMW_IMPLEMENTATION') != 'rmw_fastrtps_cpp'):
        raise RuntimeError('This adapter is restricted to localhost FastDDS domain 77 software tests.')


async def ros_future(future):
    # ROS futures are completed by the explicitly owned executor thread.
    while not future.done():
        await asyncio.sleep(.01)
    if future.exception() is not None:
        raise RuntimeError(str(future.exception()))
    return future.result()


class FoxyGoal:
    def __init__(self, goal):
        self.goal = goal
        self.result = goal.get_result_async()

    async def wait_result(self):
        return int((await ros_future(self.result)).status)

    async def cancel(self):
        require_isolated_graph()
        if self.result.done():
            return int(self.result.result().status) in (4, 5, 6)
        response = await ros_future(self.goal.cancel_goal_async())
        return response.return_code == 0 and bool(response.goals_canceling)


class FoxyLoopbackAction:
    """Supply a node already attached to a spinning executor; never starts ROS."""
    def __init__(self, node):
        require_isolated_graph()
        from rclpy.action import ActionClient
        from nav2_msgs.action import NavigateToPose
        self.node, self.action_type = node, NavigateToPose
        self.client = ActionClient(node, NavigateToPose, ACTION_NAME)
        self.feedback = []

    async def send_goal(self, point):
        require_isolated_graph()
        coordinates = [point.get(key) for key in ('x', 'y', 'yaw')]
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in coordinates):
            raise ValueError('Goal coordinates must be finite numbers.')
        if not self.client.server_is_ready():
            raise RuntimeError('Isolated navigation action server is unavailable.')
        goal = self.action_type.Goal()
        goal.pose.header.frame_id = 'map'
        goal.pose.header.stamp = self.node.get_clock().now().to_msg()
        goal.pose.pose.position.x, goal.pose.pose.position.y = map(float, coordinates[:2])
        goal.pose.pose.orientation.z = math.sin(coordinates[2] / 2)
        goal.pose.pose.orientation.w = math.cos(coordinates[2] / 2)
        goal.behavior_tree = ''
        handle = await ros_future(self.client.send_goal_async(goal, feedback_callback=self._feedback))
        if handle is None or not handle.accepted:
            return None
        return FoxyGoal(handle)

    def _feedback(self, message):
        feedback = message.feedback
        self.feedback.append({'distance_remaining': float(feedback.distance_remaining),
                              'recoveries': int(feedback.number_of_recoveries)})
        self.feedback = self.feedback[-50:]

    def close(self):
        self.client.destroy()
