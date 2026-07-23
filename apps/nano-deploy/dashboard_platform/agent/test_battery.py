import time
import unittest

from .battery import BatteryManager
from .config import AgentSettings
from .logs import LogBuffer


class BatteryConversionTests(unittest.TestCase):
    def test_default_pack_calibration(self):
        settings = AgentSettings(_env_file=None)
        self.assertEqual(settings.battery_empty_voltage, 20.0)
        self.assertEqual(settings.battery_full_voltage, 22.6)
        self.assertEqual(settings.battery_filter_window, 5)
        self.assertEqual(settings.battery_low_confirm_samples, 3)

    def test_voltage_conversion_uses_safe_lower_bound(self):
        settings = AgentSettings(_env_file=None, battery_empty_voltage=20.0, battery_full_voltage=22.6)
        manager = BatteryManager(settings, LogBuffer(10))

        self.assertEqual(BatteryManager.percentage(22.6, 20.0, 22.6), 100.0)
        self.assertEqual(BatteryManager.percentage(21.3, 20.0, 22.6), 50.0)
        self.assertEqual(BatteryManager.percentage(20.0, 20.0, 22.6), 0.0)
        self.assertEqual(BatteryManager.percentage(19.5, 20.0, 22.6), 0.0)

    def test_single_voltage_spike_does_not_change_filtered_level(self):
        settings = AgentSettings(_env_file=None, battery_empty_voltage=20.0, battery_full_voltage=22.6, battery_filter_window=5)
        manager = BatteryManager(settings, LogBuffer(10))

        for voltage in (22.6, 22.6, 22.6, 22.6, 22.6):
            manager._record_voltage(voltage, time.time())
        manager._record_voltage(19.5, time.time())

        self.assertEqual(manager.state.voltage, 22.6)
        self.assertFalse(manager.status().low)

    def test_low_voltage_requires_consecutive_samples(self):
        settings = AgentSettings(
            _env_file=None,
            battery_empty_voltage=20.0,
            battery_full_voltage=22.6,
            battery_low_voltage=20.0,
            battery_low_percentage=0.0,
            battery_low_confirm_samples=3,
        )
        manager = BatteryManager(settings, LogBuffer(10))

        manager._record_voltage(19.9, time.time())
        manager._record_voltage(20.1, time.time())
        self.assertFalse(manager.status().low)
        manager._record_voltage(19.9, time.time())
        manager._record_voltage(19.9, time.time())
        manager._record_voltage(19.9, time.time())
        self.assertTrue(manager.status().low)

    def test_percentage_is_not_invented_without_calibration(self):
        self.assertIsNone(BatteryManager.percentage(24.0, 0.0, 0.0))

    def test_percentage_is_clamped_after_calibration(self):
        self.assertEqual(BatteryManager.percentage(20.0, 20.0, 22.6), 0.0)
        self.assertEqual(BatteryManager.percentage(21.3, 20.0, 22.6), 50.0)
        self.assertEqual(BatteryManager.percentage(26.0, 20.0, 22.6), 100.0)


if __name__ == "__main__":
    unittest.main()
