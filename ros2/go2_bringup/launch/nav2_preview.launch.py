from pathlib import Path
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import TimerAction, DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
def generate_launch_description():
 share=Path(get_package_share_directory('go2_bringup'));params=LaunchConfiguration('nav2_params_file')
 def node(package,executable,name,role):
  return Node(package=package,executable=executable,name=name,parameters=[params],
   remappings=[('cmd_vel','/nav2/cmd_vel_preview')],
   additional_env={'CYCLONEDDS_URI':str(share/'config/dds'/(role+'.xml')),'RMW_IMPLEMENTATION':'rmw_cyclonedds_cpp'},output='screen')
 nodes=[
  node('nav2_planner','planner_server','planner_server','planner'),
  node('nav2_controller','controller_server','controller_server','controller'),
  node('nav2_bt_navigator','bt_navigator','bt_navigator','navigator'),
  node('nav2_lifecycle_manager','lifecycle_manager','lifecycle_manager_navigation','lifecycle')]
 return LaunchDescription([DeclareLaunchArgument('nav2_params_file',default_value=str(share/'config/nav2_preview.yaml'))]+[TimerAction(period=float(i)*2.0,actions=[n]) for i,n in enumerate(nodes)])
