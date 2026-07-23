import os
from pathlib import Path
import launch
from launch.actions import SetEnvironmentVariable, DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, GroupAction,
                            IncludeLaunchDescription, SetEnvironmentVariable)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import PushRosNamespace
import launch_ros.actions
from launch.conditions import IfCondition
from launch.conditions import UnlessCondition

def generate_launch_description():
    # 获取包路径
    bringup_dir = get_package_share_directory('turn_on_wheeltec_robot')
    launch_dir = os.path.join(bringup_dir, 'launch')
    
    # 启动参数
    use_sim_time = LaunchConfiguration('use_sim_time', default='false')
    
    # 声明启动参数
    use_sim_time_arg = DeclareLaunchArgument('use_sim_time', default_value='false')
    
    # ==== 1) 机器人模型描述（使用top_akm_dl配置）====
    flagship_type = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(launch_dir, 'robot_mode_description.launch.py')),
        launch_arguments={'top_akm_dl': 'true'}.items()
    )
    
    # ==== 2) 点云转激光扫描 ====
    # 将3D点云切片成2D激光扫描数据
    pcl2scan = launch_ros.actions.Node(
        package='pointcloud_to_laserscan', 
        executable='pointcloud_to_laserscan_node', 
        name='pcl2scan',
        remappings=[
            ('cloud_in', '/lidar_points'), 
            ('scan', '/scan')
        ],
        parameters=[{
            'target_frame': 'laser_link',           # 输出扫描的坐标系
            'transform_tolerance': 0.01,
            'min_height': -0.15,                    # 高度窗口下限（相对于点云坐标系）
            'max_height': 0.20,                     # 高度窗口上限
            'angle_min': -3.14159,                  # 扫描角度范围
            'angle_max': 3.14159,
            'angle_increment': 0.002,               # 角度增量（约0.11度）
            'scan_time': 0.1,                       # 扫描时间
            'range_min': 0.1,                       # 最小距离
            'range_max': 60.0,                      # 最大距离
            'use_inf': True,                        # 使用无穷大值
            'inf_epsilon': 1.0,
            'use_sim_time': use_sim_time
        }]
    )
    
    # 创建启动描述
    ld = LaunchDescription()
    
    # 添加参数声明
    ld.add_action(use_sim_time_arg)
    
    # 设置环境变量
    ld.add_action(SetEnvironmentVariable('RCUTILS_LOGGING_BUFFERED_STREAM', '1'))
    
    # 添加组件
    ld.add_action(flagship_type)           # 机器人模型
    ld.add_action(pcl2scan)                # 点云转激光
    
    return ld
