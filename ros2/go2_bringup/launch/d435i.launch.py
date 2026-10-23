import os
from pathlib import Path
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    share = Path(get_package_share_directory('go2_bringup'))
    # Camera-only SDK overlay; preserve the original system SDK and ROS distro.
    sdk = Path('/home/unitree/go2_ros2_ws/deps/realsense-2.51.1-rsusb')
    wrapper = Path('/home/unitree/go2_ros2_ws/realsense_rsusb_install/realsense2_camera')
    executable = wrapper/'lib/realsense2_camera/realsense2_camera_node'
    if executable.is_file():
        driver = {'executable': str(executable)}
        libraries = [str(sdk/'lib'), str(wrapper/'lib')]
    else:
        driver = {'package': 'realsense2_camera', 'executable': 'realsense2_camera_node'}
        libraries = []
    # Reuse only the standalone legacy SDK library, without sourcing ROS Noetic.
    libraries.extend(['/opt/ros/noetic/lib/aarch64-linux-gnu', os.environ.get('LD_LIBRARY_PATH', '')])
    return LaunchDescription([
        DeclareLaunchArgument('config_file', default_value=str(share/'config/d435i.yaml')),
        Node(
        **driver, namespace='camera', name='camera',
        parameters=[LaunchConfiguration('config_file')], output='screen',
        additional_env={'RMW_IMPLEMENTATION': 'rmw_cyclonedds_cpp',
                        'CYCLONEDDS_URI': str(share/'config/dds/camera.xml'),
                        'LD_LIBRARY_PATH': ':'.join(libraries)})])
