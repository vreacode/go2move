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
    with_rviz = LaunchConfiguration('with_rviz', default='true')
    carto_slam = LaunchConfiguration('carto_slam', default='false')
    
    # 声明启动参数
    use_sim_time_arg = DeclareLaunchArgument('use_sim_time', default_value='false')
    with_rviz_arg = DeclareLaunchArgument('with_rviz', default_value='true')
    carto_slam_arg = DeclareLaunchArgument('carto_slam', default_value='false')
    
    # ==== 1) 底盘 + EKF（复用现有配置）====
    wheeltec_robot = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(launch_dir, 'base_serial.launch.py'))
    )
    
    robot_ekf = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(launch_dir, 'wheeltec_ekf.launch.py')),
        launch_arguments={'carto_slam': carto_slam}.items()
    )
    
    # ==== 2) URDF 和 TF 发布（复用现有配置）====
    # base_footprint -> base_link 静态变换（top_akm_dl的z轴偏移）
    base_to_link = launch_ros.actions.Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='base_to_link',
        arguments=['0', '0', '0.11744', '0', '0', '0', 'base_footprint', 'base_link']
    )
    
    # base_footprint -> gyro_link 静态变换
    base_to_gyro = launch_ros.actions.Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='base_to_gyro',
        arguments=['0', '0', '0', '0', '0', '0', 'base_footprint', 'gyro_link']
    )

    # base_link -> laser_link 静态变换（激光雷达位置）
    base_to_laser = launch_ros.actions.Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='base_to_laser',
        arguments=['0.25', '0', '0.15', '0', '0', '0', 'base_link', 'laser_link']
    )
    
    # IMU 滤波节点
    imu_config = Path(get_package_share_directory('turn_on_wheeltec_robot'), 'config', 'imu.yaml')
    imu_filter_node = launch_ros.actions.Node(
        package='imu_filter_madgwick',
        executable='imu_filter_madgwick_node',
        parameters=[imu_config]
    )
    
    # 关节状态发布器
    joint_state_publisher_node = launch_ros.actions.Node(
        package='joint_state_publisher', 
        executable='joint_state_publisher', 
        name='joint_state_publisher'
    )
    
    # 机器人模型描述（使用top_akm_dl配置）
    flagship_type = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(launch_dir, 'robot_mode_description.launch.py')),
        launch_arguments={'top_akm_dl': 'true'}.items()
    )
    
    # ==== 3) Hesai 激光雷达驱动 ====
    hesai_driver = launch_ros.actions.Node(
        namespace='hesai_ros_driver',
        package='hesai_ros_driver', 
        executable='hesai_ros_driver_node', 
        output='screen',
        parameters=[{'use_sim_time': use_sim_time}]
    )
    
    # ==== 4) 点云转激光扫描 ====
    # 将3D点云切片成2D激光扫描数据
    pcl2scan = launch_ros.actions.Node(
        package='pointcloud_to_laserscan', 
        executable='pointcloud_to_laserscan_node', 
        name='pcl2scan',
        remappings=[
            ('cloud_in', '/hesai_ros_driver/points'),
            ('scan', '/scan')
        ],
        parameters=[{
            'target_frame': 'laser_link',           # 输出扫描的坐标系
            'transform_tolerance': 0.01,
            'min_height': -0.15,                    # 高度窗口下限（相对于点云坐标系）
            'max_height': 0.20,                     # 高度窗口上限
            'angle_min': -3.14159,                  # 扫描角度范围
            'angle_max': 3.14159,
            'angle_increment': 0.05,                # 角度增量（约2.87度）
            'scan_time': 0.1,                       # 扫描时间
            'range_min': 0.1,                       # 最小距离
            'range_max': 30.0,                      # 最大距离
            'use_inf': True,                        # 使用无穷大值
            'inf_epsilon': 1.0,
            'use_sim_time': use_sim_time
        }]
    )
    
    # ==== 5) SLAM Toolbox (替代GMapping) ====
    slam_toolbox = launch_ros.actions.Node(
        package='slam_toolbox',
        executable='async_slam_toolbox_node',
        name='slam_toolbox',
        output='screen',
        parameters=[{
            'use_sim_time': use_sim_time,
            'odom_frame': 'odom_combined',          # 里程计坐标系
            'map_frame': 'map',                     # 地图坐标系
            'base_frame': 'base_link',              # 机器人基座坐标系
            'scan_topic': '/scan',                  # 激光扫描话题

            # SLAM模式设置
            'mode': 'mapping',                      # 建图模式

            # 地图参数
            'resolution': 0.05,                     # 地图分辨率（米/像素）
            'max_laser_range': 20.0,                # 激光最大距离
            'min_laser_range': 0.1,                 # 激光最小距离

            # 运动阈值
            'minimum_travel_distance': 0.5,         # 最小移动距离
            'minimum_travel_heading': 0.5,          # 最小转动角度（弧度）

            # 扫描匹配参数
            'use_scan_matching': True,              # 启用扫描匹配
            'use_scan_barycenter': True,            # 使用扫描重心

            # 相关性搜索参数
            'correlation_search_space_dimension': 0.5,
            'correlation_search_space_resolution': 0.01,
            'correlation_search_space_smear_deviation': 0.1,

            # 回环检测参数
            'do_loop_closing': True,                # 启用回环检测
            'loop_search_maximum_distance': 3.0,    # 回环搜索最大距离
            'loop_match_minimum_response_coarse': 0.35,
            'loop_match_minimum_response_fine': 0.45,

            # 其他参数
            'transform_publish_period': 0.02,       # TF发布周期
            'map_update_interval': 5.0,             # 地图更新间隔
            'transform_timeout': 0.2,               # 变换超时
            'tf_buffer_duration': 30.0,             # TF缓冲区持续时间

            # 调试和可视化
            'debug_logging': False,                 # 调试日志
            'enable_interactive_mode': True         # 启用交互模式
        }]
    )
    
    # ==== 6) Yesense IMU 驱动 ====
    yesense_imu = launch_ros.actions.Node(
        package='yesense_std_ros2',
        executable='yesense_node_publisher',
        name='yesense_imu_node',
        output='screen',
        parameters=[{'use_sim_time': use_sim_time}]
    )
    
    # ==== 7) RViz2 可视化 ====
    # 使用Hesai驱动的RViz配置，或创建自定义配置
    rviz_cfg = os.path.join(get_package_share_directory('hesai_ros_driver'), 'rviz', 'rviz2.rviz')
    rviz2 = launch_ros.actions.Node(
        condition=IfCondition(with_rviz),
        package='rviz2', 
        executable='rviz2',
        arguments=['-d', rviz_cfg],
        output='screen'
    )
    
    # 创建启动描述
    ld = LaunchDescription()
    
    # 添加参数声明
    ld.add_action(use_sim_time_arg)
    ld.add_action(with_rviz_arg)
    ld.add_action(carto_slam_arg)
    
    # 设置环境变量
    ld.add_action(SetEnvironmentVariable('RCUTILS_LOGGING_BUFFERED_STREAM', '1'))
    
    # 添加机器人基础组件
    ld.add_action(flagship_type)           # 机器人模型
    ld.add_action(wheeltec_robot)          # 底盘驱动
    ld.add_action(base_to_link)            # TF变换
    ld.add_action(base_to_gyro)            # TF变换
    ld.add_action(base_to_laser)           # TF变换（激光雷达）
    ld.add_action(joint_state_publisher_node)  # 关节状态
    ld.add_action(imu_filter_node)         # IMU滤波
    ld.add_action(robot_ekf)               # 扩展卡尔曼滤波
    
    # 添加传感器和SLAM组件
    ld.add_action(hesai_driver)            # 激光雷达驱动
    ld.add_action(pcl2scan)                # 点云转激光
    ld.add_action(yesense_imu)             # Yesense IMU驱动
    ld.add_action(slam_toolbox)            # SLAM建图
    
    # 添加可视化
    ld.add_action(rviz2)                   # RViz2
    
    return ld
