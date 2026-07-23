import unittest

from .config import AgentSettings
from .logs import LogBuffer
from .sensors import SensorBridgeManager


class SensorSelectionTests(unittest.TestCase):
    def test_hesai_profile_sources_workspace_and_selects_hesai_topic(self):
        settings = AgentSettings(
            _env_file=None,
            lidar_primary_source="hesai_xt",
            hesai_ros2_setup_bash="/home/unitree/hesai_ros2_ws/install/setup.bash",
            ros2_lidar_setup_bash="",
            ros2_imu_setup_bash="",
            hesai_point_topic="/hesai_xt/points",
            lidar_point_topic="/c16/lslidar_point_cloud",
        )
        manager = SensorBridgeManager(settings, LogBuffer(20))

        self.assertEqual(manager._setup_commands(), ["source /home/unitree/hesai_ros2_ws/install/setup.bash"])
        command = manager._bridge_command()
        self.assertIn("--lidar-topic /hesai_xt/points", command)
        self.assertNotIn("/c16/lslidar_point_cloud", command)

    def test_generic_profile_keeps_legacy_topic_and_setup(self):
        settings = AgentSettings(
            _env_file=None,
            lidar_primary_source="generic",
            ros2_lidar_setup_bash="/opt/ros/foxy/setup.bash",
            ros2_imu_setup_bash="",
            hesai_ros2_setup_bash="/home/unitree/hesai_ros2_ws/install/setup.bash",
            lidar_point_topic="/legacy/lidar_points",
        )
        manager = SensorBridgeManager(settings, LogBuffer(20))

        self.assertEqual(manager._setup_commands(), ["source /opt/ros/foxy/setup.bash"])
        self.assertIn("--lidar-topic /legacy/lidar_points", manager._bridge_command())


if __name__ == "__main__":
    unittest.main()
