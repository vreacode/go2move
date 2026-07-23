from launch.actions import TimerAction
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch.conditions import IfCondition
from launch.conditions import UnlessCondition
import launch_ros.actions

#def launch(launch_descriptor, argv):
def generate_launch_description():

    # Declare launch arguments (assuming these are used elsewhere if not here)
    akmcar = LaunchConfiguration('akmcar', default='true')
    # Add other launch arguments here if needed, matching the original file

    # Define the wheeltec_robot_node
    wheeltec_robot_node = launch_ros.actions.Node(
        package='turn_on_wheeltec_robot',
        executable='wheeltec_robot_node',
        name='wheeltec_robot', # Added name for clarity, assuming it was missing in the snippet
        output='screen',
        # The base must consume the post-processed command. Nav2 publishes
        # raw commands to /cmd_vel_nav, velocity_smoother publishes /cmd_vel,
        # and collision_monitor gates the final /cmd_vel_safe stream.
        remappings=[('/cmd_vel', '/cmd_vel_safe')],
        parameters=[{'usart_port_name': '/dev/ttyCH343USB0', # 大车串口；驱动实际读取此参数
                    'serial_baud_rate': 115200,
                    'robot_frame_id': 'base_footprint',
                    'odom_frame_id': 'odom_combined',
                    'cmd_vel': 'cmd_vel_safe',
                    'akm_cmd_vel': 'cmd_vel_safe',
                    'product_number': 4,
                    'odom_x_scale': 2.6982,
                    'odom_y_scale': 2.6982,
                    'odom_z_scale_positive': 1.0,
                    'odom_z_scale_negative': 1.0}],
        # Add remapping or other arguments if they were in the original file
        )

    # Wrap the node with a TimerAction to introduce a 5-second delay
    wheeltec_robot_with_delay = TimerAction(
        period=5.0,  # Delay in seconds
        actions=[wheeltec_robot_node]
    )

    # Return the LaunchDescription with the delayed node action
    return LaunchDescription([
        # Include other actions from the original LaunchDescription here if they exist
        # For example:
        # DeclareLaunchArgument(...)
        # StaticTransformPublisher(...)
        # RobotStatePublisher(...)
        # imu_filter_madgwick_node = Node(...) # If these nodes are defined/used in this file

        wheeltec_robot_with_delay, # This is the delayed wheeltec_robot_node
  ])
