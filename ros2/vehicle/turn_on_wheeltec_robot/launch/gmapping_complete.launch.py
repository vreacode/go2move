from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, SetEnvironmentVariable
from launch.substitutions import LaunchConfiguration
from launch.launch_description_sources import PythonLaunchDescriptionSource
from ament_index_python.packages import get_package_share_directory
from pathlib import Path
import os
import launch_ros.actions
import launch

def generate_launch_description():
    use_sim_time = LaunchConfiguration('use_sim_time', default='false')
    with_rviz    = LaunchConfiguration('with_rviz',   default='true')
    odom_frame   = LaunchConfiguration('odom_frame',  default='odom')      # 如果你的里程计帧叫 odom_combined，这里改成它
    imu_topic    = LaunchConfiguration('imu_topic',   default='/imu/data') # Yesense IMU 实际话题，下面也会覆盖到 EKF

    bringup_dir = get_package_share_directory('turn_on_wheeltec_robot')
    launch_dir  = os.path.join(bringup_dir, 'launch')
    cfg_dir     = os.path.join(bringup_dir, 'config')

    # ========== 0) Yesense IMU ==========
    # 直接启动 Yesense IMU 节点，避免包路径问题
    yesense_imu_node = launch_ros.actions.Node(
        package='yesense_std_ros2', 
        executable='yesense_node_publisher', 
        name='yesense_imu_node',
        output='screen',
        parameters=[{
            'device_path': '/dev/ttyUSB0',
            'baudrate': 115200,
            'frame_id': 'imu_link',
            'publish_rate': 100.0
        }]
    )

    # ========== 1) 底盘串口（轮速里程计） ==========
    base_serial = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(launch_dir, 'base_serial.launch.py'))
    )

    # ========== 2) URDF / TF ==========
    # 使用现有的 robot_mode_description.launch.py 来加载 URDF
    flagship_type = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(launch_dir, 'robot_mode_description.launch.py')),
        launch_arguments={'top_akm_dl': 'true'}.items(),
    )
    
    # base_footprint -> base_link 静态 TF（根据注释，top_akm_dl 的 z 轴偏移应该是 0.11744）
    base_to_link = launch_ros.actions.Node(
        package='tf2_ros', executable='static_transform_publisher', name='base_to_link',
        arguments=['0','0','0.11744', '0','0','0', 'base_footprint','base_link']
    )
    
    # base_footprint -> gyro_link
    base_to_gyro = launch_ros.actions.Node(
        package='tf2_ros', executable='static_transform_publisher', name='base_to_gyro',
        arguments=['0','0','0', '0','0','0', 'base_footprint','gyro_link']
    )

    # ========== 3) Hesai 驱动（3D 点云） ==========
    hesai_driver = launch_ros.actions.Node(
        namespace='hesai_ros_driver',
        package='hesai_ros_driver', executable='hesai_ros_driver_node', output='screen'
        # 若驱动支持 frame_id 参数，最好设为 laser_link：parameters=[{'frame_id':'laser_link'}]
    )

    # 点云 -> 激光：切一层薄片，得到 /scan（gmapping 用）
    pcl2scan = launch_ros.actions.Node(
        package='pointcloud_to_laserscan', executable='pointcloud_to_laserscan_node', name='pcl2scan',
        remappings=[('cloud_in','/hesai_ros_driver/points'), ('scan','/scan')],
        parameters=[{
            'target_frame':        'laser_link',
            'transform_tolerance': 0.01,
            'min_height':         -0.06,    # 先用 ±6cm，干净又稳定
            'max_height':          0.06,
            'range_min':           0.15,
            'range_max':          30.0,
            'scan_time':           0.1,
            'use_inf':             True
        }]
    )

    # ========== 4) EKF：里程计 + IMU 融合（发布 odom->base_link） ==========
    ekf_cfg = os.path.join(cfg_dir, 'ekf_2d_gmapping.yaml')
    ekf_node = launch_ros.actions.Node(
        package='robot_localization', executable='ekf_node', name='ekf_2d',
        output='screen',
        parameters=[ekf_cfg, {
            'use_sim_time': use_sim_time,
            'odom_frame':   odom_frame,     # 允许用参数改名（odom / odom_combined）
            'imu0':         imu_topic       # 允许用参数指定 Yesense 话题
        }]
    )

    # ========== 5) GMapping（发布 map->odom） ==========
    gmapping = launch_ros.actions.Node(
        package='slam_gmapping', executable='slam_gmapping', output='screen',
        parameters=[{
            'use_sim_time':  use_sim_time,
            'base_frame':    'base_link',
            'map_frame':     'map',
            'odom_frame':    odom_frame,     # 与 EKF 保持一致！

            # 稳定静止、移动才更新
            'srr': 0.02, 'srt': 0.02, 'str': 0.02, 'stt': 0.02,
            'linearUpdate':  0.8,     # m
            'angularUpdate': 0.8,     # rad
            'temporalUpdate': -1.0,   # 不按时间触发

            'resampleThreshold': 0.5,
            'particles': 30,

            # 室内常用
            'maxUrange': 15.0, 'maxRange': 60.0,
            'xmin': -12.0, 'ymin': -12.0, 'xmax': 12.0, 'ymax': 12.0,
            'delta': 0.05,
            'lstep': 0.05, 'astep': 0.05, 'iterations': 5, 'lsigma': 0.075, 'ogain': 3.0, 'lskip': 0
        }],
        remappings=[('scan','/scan')]
    )

    # ========== 6) RViz ==========
    rviz2 = launch_ros.actions.Node(
        condition=launch.conditions.IfCondition(with_rviz),
        package='rviz2', executable='rviz2',
        output='screen'
    )

    return LaunchDescription([
        DeclareLaunchArgument('use_sim_time', default_value='false'),
        DeclareLaunchArgument('with_rviz',   default_value='true'),
        DeclareLaunchArgument('odom_frame',  default_value='odom'),
        DeclareLaunchArgument('imu_topic',   default_value='/imu/data'),
        SetEnvironmentVariable('RCUTILS_LOGGING_BUFFERED_STREAM', '1'),

        # 传感器
        yesense_imu_node, hesai_driver, pcl2scan,

        # 底盘 / TF / URDF
        base_serial, base_to_link, base_to_gyro, flagship_type,

        # 融合 + SLAM + 可视化
        ekf_node, gmapping, rviz2,
    ])
