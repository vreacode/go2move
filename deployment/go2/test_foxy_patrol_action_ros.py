#!/usr/bin/env python3
"""Real ROS action transport, synthetic navigator, localhost domain 77 only."""
import asyncio
import json
import os
from pathlib import Path
import threading
import time

from foxy_patrol_action import ACTION_NAME, FoxyLoopbackAction, require_isolated_graph


def main():
    require_isolated_graph()
    import rclpy
    from rclpy.action import ActionServer, GoalResponse, CancelResponse
    from rclpy.callback_groups import ReentrantCallbackGroup
    from rclpy.executors import MultiThreadedExecutor
    from nav2_msgs.action import NavigateToPose
    rclpy.init(args=[])
    server_node = rclpy.create_node('go2_fake_navigation_server', enable_rosout=False)
    client_node = rclpy.create_node('go2_fake_navigation_client', enable_rosout=False)
    stopping = threading.Event()
    results = []

    def execute(handle):
        x = handle.request.pose.pose.position.x
        until = time.monotonic() + (3 if x == 3 else .15)
        while time.monotonic() < until and not stopping.is_set():
            if handle.is_cancel_requested:
                handle.canceled()
                return NavigateToPose.Result()
            feedback = NavigateToPose.Feedback()
            feedback.distance_remaining = float(max(0, until-time.monotonic()))
            handle.publish_feedback(feedback)
            time.sleep(.02)
        if x == 2 or stopping.is_set():
            handle.abort()
        else:
            handle.succeed()
        return NavigateToPose.Result()

    server = ActionServer(server_node, NavigateToPose, ACTION_NAME, execute,
        goal_callback=lambda goal: GoalResponse.REJECT if goal.pose.pose.position.x < 0 else GoalResponse.ACCEPT,
        cancel_callback=lambda _goal: CancelResponse.ACCEPT, callback_group=ReentrantCallbackGroup())
    adapter = FoxyLoopbackAction(client_node)
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(server_node); executor.add_node(client_node)
    thread = threading.Thread(target=executor.spin, daemon=True); thread.start()

    async def checks():
        deadline = time.monotonic()+5
        while not adapter.client.server_is_ready():
            if time.monotonic() > deadline:
                raise RuntimeError('Fake action server not discovered.')
            await asyncio.sleep(.05)
        point = lambda x: {'name': 'test', 'x': x, 'y': 0., 'yaw': .3}
        handle = await adapter.send_goal(point(1.))
        assert await asyncio.wait_for(handle.wait_result(), 3) == 4
        results.append('successful_arrival')
        handle = await adapter.send_goal(point(2.))
        assert await asyncio.wait_for(handle.wait_result(), 3) == 6
        results.append('navigation_aborted')
        assert await adapter.send_goal(point(-1.)) is None
        results.append('goal_rejected')
        handle = await adapter.send_goal(point(3.))
        assert await asyncio.wait_for(handle.cancel(), 2)
        assert await asyncio.wait_for(handle.wait_result(), 2) == 5
        results.append('cancel_ack_then_terminal_result')
        from patrol_execution import Nav2Navigator
        async def synthetic_stable():
            return True  # Only the fake server; not real robot stop evidence.
        navigator = Nav2Navigator(adapter, settle=synthetic_stable)
        assert await asyncio.wait_for(navigator.go(point(1.)), 3)
        assert await navigator.settle(point(1.))
        assert await navigator.cancel()
        assert await asyncio.wait_for(navigator.go(point(-1.)), 3) is False
        results.append('patrol_adapter_arrival_settle_and_rejection')
        pending = asyncio.create_task(navigator.go(point(3.)))
        await asyncio.sleep(.10)
        assert await asyncio.wait_for(navigator.cancel(), 3)
        assert await asyncio.wait_for(pending, 2) is False
        results.append('patrol_adapter_cancel_waits_terminal')
        assert adapter.feedback
        results.append('typed_foxy_feedback')
        original = os.environ['ROS_DOMAIN_ID']
        try:
            os.environ['ROS_DOMAIN_ID'] = '0'
            try:
                await adapter.send_goal(point(1.))
                raise AssertionError('Live domain was not rejected')
            except RuntimeError as error:
                assert 'restricted' in str(error)
        finally:
            os.environ['ROS_DOMAIN_ID'] = original
        results.append('live_domain_refused_before_goal')

    report = {'ros_domain_id': 77, 'localhost_only': True, 'backend': 'real ROS2 Foxy actions with synthetic server',
              'motion_sent_to_robot': False, 'cmd_vel_publishers_created': False, 'checks': results}
    try:
        asyncio.run(checks())
        report['passed'] = True
    except Exception as error:
        report.update(passed=False, error=repr(error))
    finally:
        stopping.set(); executor.shutdown(timeout_sec=3); thread.join(timeout=3)
        adapter.close(); server.destroy(); client_node.destroy_node(); server_node.destroy_node(); rclpy.shutdown()
        report['owned_executor_stopped'] = not thread.is_alive()
        from go2_platform_write import write
        write(Path(__file__).with_name('patrol_action_validation_20261007.json'), json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))
    return 0 if report.get('passed') and report['owned_executor_stopped'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
