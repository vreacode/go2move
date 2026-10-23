#!/usr/bin/env python3
"""Read-only real Foxy map loader check, localhost domain 77; no robot nodes."""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

from foxy_patrol_action import require_isolated_graph


def main():
    require_isolated_graph()
    import rclpy
    import yaml
    from lifecycle_msgs.srv import ChangeState
    from nav2_msgs.srv import LoadMap
    source = Path('/home/unitree/go2_ros2_ws/maps/end-of-day-20261006/map.yaml')
    report = {'read_only_original_map': True, 'motion_sent': False, 'ros_domain_id': 77}
    rclpy.init(args=[])
    node = rclpy.create_node('go2_map_roundtrip_probe', enable_rosout=False)
    child = None

    def call(client, request):
        if not client.wait_for_service(timeout_sec=5):
            raise RuntimeError('Map validation service unavailable: '+client.srv_name)
        future = client.call_async(request)
        rclpy.spin_until_future_complete(node, future, timeout_sec=5)
        if not future.done() or future.exception():
            raise RuntimeError('Map validation service timed out')
        return future.result()

    try:
        with tempfile.TemporaryDirectory(prefix='go2-map-roundtrip-') as folder:
            root = Path(folder)
            params = yaml.safe_load(source.read_text())
            corrected = dict(params, free_thresh=.196)
            safe = root/'map.yaml'; safe.write_text(yaml.safe_dump(corrected))
            with (root/'map_server.log').open('w') as log:
                child = subprocess.Popen(['/opt/ros/foxy/lib/nav2_map_server/map_server', '--ros-args',
                    '-r','__node:=go2_map_validation', '-p','yaml_filename:='+str(safe)],
                    stdout=log, stderr=subprocess.STDOUT)
                try:
                    lifecycle = node.create_client(ChangeState, '/go2_map_validation/change_state')
                    for transition in (1, 3):
                        request = ChangeState.Request(); request.transition.id = transition
                        if not call(lifecycle, request).success:
                            raise RuntimeError('Map server lifecycle transition failed')
                    service_name = next((name for name, kinds in node.get_service_names_and_types()
                        if 'nav2_msgs/srv/LoadMap' in kinds and 'go2_map_validation' in name), '/go2_map_validation/load_map')
                    load = node.create_client(LoadMap, service_name)
                    for label, path in [('original', source), ('corrected', safe)]:
                        request = LoadMap.Request(); request.map_url = str(path)
                        result = call(load, request)
                        if result.result != 0:
                            raise RuntimeError('Map loader rejected '+label)
                        data = list(result.map.data)
                        report[label] = {key: data.count(value) for key, value in [('free', 0), ('occupied', 100), ('unknown', -1)]}
                    report['passed'] = (report['corrected']['free'] == 3832 and report['corrected']['occupied'] == 609
                                        and report['corrected']['unknown'] == 3239)
                finally:
                    if child.poll() is None: child.send_signal(signal.SIGINT)
                    try: child.wait(timeout=5)
                    except subprocess.TimeoutExpired: child.kill(); child.wait()
    except Exception as error:
        report.update(passed=False, error=repr(error))
    finally:
        report['owned_map_server_stopped'] = child is None or child.poll() is not None
        node.destroy_node(); rclpy.shutdown()
        from go2_platform_write import write
        write(Path(__file__).with_name('saved_map_roundtrip_validation_20261007.json'), json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
    return 0 if report.get('passed') and report['owned_map_server_stopped'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
