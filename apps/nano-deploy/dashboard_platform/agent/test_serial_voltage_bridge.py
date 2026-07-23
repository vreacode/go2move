import unittest

from .serial_voltage_protocol import decode_voltage_frame


class SerialVoltageFrameTests(unittest.TestCase):
    @staticmethod
    def frame(voltage: float) -> bytes:
        frame = bytearray(24)
        frame[0] = 0x7B
        millivolts = int(voltage * 1000)
        frame[20] = (millivolts >> 8) & 0xFF
        frame[21] = millivolts & 0xFF
        frame[22] = 0
        for value in frame[:22]:
            frame[22] ^= value
        frame[23] = 0x7D
        return bytes(frame)

    def test_decodes_voltage_from_valid_frame(self):
        self.assertEqual(decode_voltage_frame(self.frame(22.6)), 22.6)

    def test_rejects_bad_checksum(self):
        frame = bytearray(self.frame(22.6))
        frame[10] ^= 0x01
        self.assertIsNone(decode_voltage_frame(bytes(frame)))


if __name__ == "__main__":
    unittest.main()
