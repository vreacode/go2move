import unittest

from siyi_zt6 import (
    CMD_GET_ATTITUDE,
    CMD_GET_FIRMWARE,
    CMD_GET_HARDWARE_ID,
    CMD_GET_STATUS,
    CMD_CENTER,
    CMD_TURN,
    Attitude,
    FirmwareInfo,
    GimbalStatus,
    SiyiProtocolError,
    build_packet,
    parse_packet,
)


class SiyiZt6ProtocolTests(unittest.TestCase):
    def test_status_request_matches_manual_example(self):
        self.assertEqual(
            build_packet(CMD_GET_STATUS),
            bytes.fromhex("55 66 01 00 00 00 00 0a 0f 75"),
        )

    def test_query_packets_match_manual_examples(self):
        self.assertEqual(
            build_packet(CMD_GET_HARDWARE_ID),
            bytes.fromhex("55 66 01 00 00 00 00 02 07 f4"),
        )
        self.assertEqual(
            build_packet(CMD_GET_FIRMWARE),
            bytes.fromhex("55 66 01 00 00 00 00 01 64 c4"),
        )
        self.assertEqual(
            build_packet(CMD_GET_ATTITUDE),
            bytes.fromhex("55 66 01 00 00 00 00 0d e8 05"),
        )
        self.assertEqual(
            build_packet(CMD_TURN, bytes((100, 100))),
            bytes.fromhex("55 66 01 02 00 00 00 07 64 64 3d cf"),
        )
        self.assertEqual(
            build_packet(CMD_CENTER, b"\x01"),
            bytes.fromhex("55 66 01 01 00 00 00 08 01 d1 12"),
        )

    def test_turn_packet_accepts_signed_speeds(self):
        self.assertEqual(
            build_packet(CMD_TURN, bytes((246, 0))),
            build_packet(CMD_TURN, bytes.fromhex("f6 00")),
        )

    def test_status_response_is_decoded(self):
        packet = parse_packet(
            bytes.fromhex("55 66 02 08 00 01 00 0a 00 00 00 02 01 01 00 00 ec 29")
        )
        status = GimbalStatus.from_payload(packet.data)
        self.assertTrue(packet.is_ack)
        self.assertEqual(status.hdr_enabled, 0)
        self.assertEqual(status.record_state_name, "no_tf_card")
        self.assertEqual(status.motion_mode_name, "follow")
        self.assertEqual(status.mounting_direction_name, "normal")
        self.assertEqual(status.video_output_name, "hdmi")

    def test_hardware_and_firmware_responses_are_decoded(self):
        hardware = parse_packet(
            bytes.fromhex(
                "55 66 02 0c 00 01 00 02 "
                "38 33 30 32 32 30 39 31 32 35 00 00 87 84"
            )
        )
        self.assertEqual(hardware.data.rstrip(b"\x00").decode("ascii"), "8302209125")

        firmware = FirmwareInfo.from_payload(
            bytes.fromhex("09 01 00 82 03 02 00 83 00 00 00 00")
        )
        self.assertEqual(firmware.as_dict()["code_board"], "0.1.9")
        self.assertEqual(firmware.as_dict()["gimbal"], "0.2.3")
        self.assertEqual(firmware.as_dict()["zoom"], "0.0.0")

    def test_attitude_response_is_decoded(self):
        packet = parse_packet(
            bytes.fromhex(
                "55 66 02 0c 00 03 00 0d "
                "00 00 00 00 00 00 fd ff fd ff ff ff 12 1d"
            )
        )
        attitude = Attitude.from_payload(packet.data)
        self.assertEqual(attitude.angles_deg, (0.0, 0.0, 0.0))
        self.assertEqual(attitude.angular_velocity_raw, (-3, -3, -1))

    def test_bad_crc_is_rejected(self):
        packet = bytearray(build_packet(CMD_GET_STATUS))
        packet[-1] ^= 0x01
        with self.assertRaises(SiyiProtocolError):
            parse_packet(bytes(packet))


if __name__ == "__main__":
    unittest.main()
