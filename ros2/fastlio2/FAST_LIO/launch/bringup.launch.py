from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from ament_index_python.packages import get_package_share_directory
import os


def generate_launch_description():

    # 1) 雷达驱动 lslidar_c16
    lslidar_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(
                get_package_share_directory('lslidar_driver'),
                'launch',
                'lslidar_c16_launch.py'
            )
        )
    )

    # 2) Yesense IMU 驱动
    yesense_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(
                get_package_share_directory('yesense_std_ros2'),
                'launch',
                'yesense_node.launch.py'
            )
        )
    )

    # 3) 雷达 tf：base_link -> laser_link
    # 手动命令等价于：
    # ros2 run tf2_ros static_transform_publisher 0 -0.13 0.465 0 0 0 base_link laser_link
    static_tf_laser = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='static_tf_laser',
        arguments=['0', '-0.13', '0.465', '0', '0', '0', 'base_link', 'laser_link'],
        output='screen'
    )

    # 4) IMU tf：base_link -> gyro_link
    # 手动命令等价于：
    # ros2 run tf2_ros static_transform_publisher 0 -0.25 0.42 0 0 0 base_link gyro_link
    static_tf_imu = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='static_tf_imu',
        arguments=['0', '-0.25', '0.42', '0', '0', '0', 'base_link', 'gyro_link'],
        output='screen'
    )

    # 5) base_link 坐标变换：body -> base_link
    # 手动命令等价于：
    # ros2 run tf2_ros static_transform_publisher 0 0 0 0 0 0 body base_link
    static_tf_base = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='static_tf_base',
        arguments=['0', '0', '0', '0', '0', '0', 'body', 'base_link'],
        output='screen'
    )

    # 6) FAST_LIO 建图节点
    fastlio_mapping = Node(
        package='fast_lio',
        executable='fastlio_mapping',
        name='laser_mapping',
        output='screen',
        parameters=[
            os.path.join(
                get_package_share_directory('fast_lio'),
                'config',
                'lsc16.yaml'
            )
        ]
    )

    return LaunchDescription([
        lslidar_launch,
        yesense_launch,
        static_tf_laser,
        static_tf_imu,
        static_tf_base,
        fastlio_mapping,
    ])

