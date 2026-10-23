import os
from pathlib import Path
import xml.etree.ElementTree as ET
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction, IncludeLaunchDescription, TimerAction
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def setup(context):
    share=Path(get_package_share_directory('go2_bringup'))
    def env(role):return {'CYCLONEDDS_URI':str(share/'config/dds'/ (role+'.xml')),'RMW_IMPLEMENTATION':'rmw_cyclonedds_cpp'}
    description=(share/'urdf/go2.urdf').read_text().replace('package://go2_bringup/','file://'+str(share)+'/')
    nodes=[
      Node(package='hesai_ros_driver',executable='hesai_ros_driver_node',name='hesai_ros_driver_node',
           parameters=[{'config_path':'/home/unitree/hesai_ros2_ws/src/hesai_ros_driver/config/config.yaml'}],additional_env=env('lidar'),output='log',respawn=True,respawn_delay=3.),
      Node(package='go2_bringup',executable='state_bridge',name='state_bridge',parameters=[str(share/'config/platform.yaml')],additional_env=env('state'),output='screen',respawn=True,respawn_delay=3.),
      Node(package='robot_state_publisher',executable='robot_state_publisher',name='robot_state_publisher',parameters=[{'robot_description':description,'publish_frequency':30.0}],additional_env=env('model'),output='log'),
      Node(package='pointcloud_to_laserscan',executable='pointcloud_to_laserscan_node',name='pointcloud_to_laserscan',parameters=[str(share/'config/scan.yaml')],remappings=[('cloud_in','/lidar_points'),('scan','/scan')],additional_env=env('scan'),output='screen'),
      Node(package='slam_toolbox',executable='async_slam_toolbox_node',name='slam_toolbox',parameters=[str(share/'config/slam.yaml')],additional_env=env('slam'),condition=IfCondition(LaunchConfiguration('slam')),output='screen'),
      Node(package='rviz2',executable='rviz2',name='rviz',arguments=['-d',str(share/'rviz/platform.rviz')],additional_env=env('rviz'),condition=IfCondition(LaunchConfiguration('rviz')),output='log'),
      IncludeLaunchDescription(PythonLaunchDescriptionSource(str(share/'launch/nav2_preview.launch.py')),condition=IfCondition(LaunchConfiguration('nav2_preview'))),
      IncludeLaunchDescription(PythonLaunchDescriptionSource(str(share/'launch/d435i.launch.py')),condition=IfCondition(LaunchConfiguration('camera')))
    ]
    return [TimerAction(period=float(i)*2.0, actions=[node]) for i,node in enumerate(nodes)]
def generate_launch_description():
    return LaunchDescription([DeclareLaunchArgument('camera',default_value='false'),DeclareLaunchArgument('slam',default_value='true'),DeclareLaunchArgument('rviz',default_value='true'),DeclareLaunchArgument('nav2_preview',default_value='false'),OpaqueFunction(function=setup)])
