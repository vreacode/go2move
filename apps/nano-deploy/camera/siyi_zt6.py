"""Dependency-free SIYI ZT6 UDP SDK client.

The ZT6 manual documents a little-endian packet format with a CRC-16/CCITT
checksum (polynomial 0x1021, initial value 0, no final XOR).  Status queries
are read-only; motion commands are bounded by the callers that expose them.
"""

from __future__ import annotations

import json
import socket
import struct
from dataclasses import dataclass
from typing import Any

STX = b"\x55\x66"
DEFAULT_PORT = 37260

CTRL_NEED_ACK = 0x01
CTRL_ACK_PACKET = 0x02

CMD_GET_FIRMWARE = 0x01
CMD_GET_HARDWARE_ID = 0x02
CMD_TURN = 0x07
CMD_CENTER = 0x08
CMD_GET_STATUS = 0x0A
CMD_GET_ATTITUDE = 0x0D


class SiyiProtocolError(ValueError):
    """Raised when a SIYI packet is malformed or fails CRC validation."""


@dataclass(frozen=True)
class SiyiPacket:
    control: int
    data_length: int
    sequence: int
    command_id: int
    data: bytes
    crc: int

    @property
    def is_ack(self) -> bool:
        return bool(self.control & CTRL_ACK_PACKET)


def crc16(data: bytes) -> int:
    """Calculate the CRC used by the SIYI SDK."""

    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def build_packet(
    command_id: int,
    data: bytes = b"",
    *,
    sequence: int = 0,
    need_ack: bool = True,
) -> bytes:
    """Build a SIYI SDK packet with a little-endian CRC trailer."""

    if not 0 <= command_id <= 0xFF:
        raise ValueError("command_id must be in [0, 255]")
    if not 0 <= sequence <= 0xFFFF:
        raise ValueError("sequence must be in [0, 65535]")
    if len(data) > 0xFFFF:
        raise ValueError("data is too long for a SIYI packet")

    control = CTRL_NEED_ACK if need_ack else 0
    body = STX + struct.pack("<BHHB", control, len(data), sequence, command_id) + data
    return body + struct.pack("<H", crc16(body))


def parse_packet(packet: bytes) -> SiyiPacket:
    """Validate and decode one complete SIYI UDP packet."""

    if len(packet) < 10:
        raise SiyiProtocolError(f"packet is too short: {len(packet)} bytes")
    if packet[:2] != STX:
        raise SiyiProtocolError(f"invalid STX: {packet[:2].hex(' ')}")

    control = packet[2]
    data_length, sequence, command_id = struct.unpack_from("<HHB", packet, 3)
    expected_length = 10 + data_length
    if len(packet) != expected_length:
        raise SiyiProtocolError(
            f"invalid packet length: expected {expected_length}, got {len(packet)}"
        )

    expected_crc = int.from_bytes(packet[-2:], "little")
    actual_crc = crc16(packet[:-2])
    if actual_crc != expected_crc:
        raise SiyiProtocolError(
            f"CRC mismatch: expected 0x{expected_crc:04x}, calculated 0x{actual_crc:04x}"
        )

    return SiyiPacket(
        control=control,
        data_length=data_length,
        sequence=sequence,
        command_id=command_id,
        data=packet[8:-2],
        crc=expected_crc,
    )


def _version_from_word(value: int) -> str:
    """Decode the manual's uint32 version format, ignoring its high byte."""

    return f"{(value >> 16) & 0xFF}.{(value >> 8) & 0xFF}.{value & 0xFF}"


@dataclass(frozen=True)
class FirmwareInfo:
    code_board_version: int
    gimbal_version: int
    zoom_version: int

    @classmethod
    def from_payload(cls, data: bytes) -> "FirmwareInfo":
        if len(data) < 12:
            raise SiyiProtocolError(f"firmware payload is too short: {len(data)} bytes")
        return cls(*struct.unpack_from("<III", data))

    def as_dict(self) -> dict[str, Any]:
        return {
            "code_board_raw": f"0x{self.code_board_version:08x}",
            "code_board": _version_from_word(self.code_board_version),
            "gimbal_raw": f"0x{self.gimbal_version:08x}",
            "gimbal": _version_from_word(self.gimbal_version),
            "zoom_raw": f"0x{self.zoom_version:08x}",
            "zoom": _version_from_word(self.zoom_version),
        }


@dataclass(frozen=True)
class GimbalStatus:
    hdr_enabled: int
    record_state: int
    motion_mode: int
    mounting_direction: int
    video_output: int
    trailing_reserved: bytes = b""

    @classmethod
    def from_payload(cls, data: bytes) -> "GimbalStatus":
        # The manual lists seven fields. Current firmware returns one extra
        # trailing reserved byte, so preserve it rather than discarding it.
        if len(data) < 7:
            raise SiyiProtocolError(f"status payload is too short: {len(data)} bytes")
        return cls(
            hdr_enabled=data[1],
            record_state=data[3],
            motion_mode=data[4],
            mounting_direction=data[5],
            video_output=data[6],
            trailing_reserved=data[7:],
        )

    @property
    def motion_mode_name(self) -> str:
        return {0: "lock", 1: "follow", 2: "fpv"}.get(
            self.motion_mode, f"unknown({self.motion_mode})"
        )

    @property
    def record_state_name(self) -> str:
        return {
            0: "not_recording",
            1: "recording",
            2: "no_tf_card",
            3: "recording_data_loss",
        }.get(self.record_state, f"unknown({self.record_state})")

    @property
    def mounting_direction_name(self) -> str:
        return {1: "normal", 2: "inverted"}.get(
            self.mounting_direction, f"unknown({self.mounting_direction})"
        )

    @property
    def video_output_name(self) -> str:
        return {0: "hdmi", 1: "cvbs"}.get(
            self.video_output, f"unknown({self.video_output})"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "hdr_enabled": bool(self.hdr_enabled),
            "record_state": self.record_state,
            "record_state_name": self.record_state_name,
            "motion_mode": self.motion_mode,
            "motion_mode_name": self.motion_mode_name,
            "mounting_direction": self.mounting_direction,
            "mounting_direction_name": self.mounting_direction_name,
            "video_output": self.video_output,
            "video_output_name": self.video_output_name,
            "trailing_reserved": self.trailing_reserved.hex(" "),
        }


@dataclass(frozen=True)
class Attitude:
    yaw_raw: int
    pitch_raw: int
    roll_raw: int
    yaw_velocity_raw: int
    pitch_velocity_raw: int
    roll_velocity_raw: int

    @classmethod
    def from_payload(cls, data: bytes) -> "Attitude":
        if len(data) < 12:
            raise SiyiProtocolError(f"attitude payload is too short: {len(data)} bytes")
        return cls(*struct.unpack_from("<6h", data))

    @property
    def angles_deg(self) -> tuple[float, float, float]:
        return tuple(value / 10.0 for value in (self.yaw_raw, self.pitch_raw, self.roll_raw))

    @property
    def angular_velocity_raw(self) -> tuple[int, int, int]:
        return (self.yaw_velocity_raw, self.pitch_velocity_raw, self.roll_velocity_raw)

    def as_dict(self) -> dict[str, Any]:
        yaw, pitch, roll = self.angles_deg
        return {
            "yaw_deg": yaw,
            "pitch_deg": pitch,
            "roll_deg": roll,
            "angular_velocity_raw": list(self.angular_velocity_raw),
        }


class SiyiZt6Client:
    """Read-only UDP client for the SIYI ZT6 SDK."""

    def __init__(self, host: str, port: int = DEFAULT_PORT, timeout: float = 2.0) -> None:
        if not host:
            raise ValueError("host must not be empty")
        if not 1 <= port <= 65535:
            raise ValueError("port must be in [1, 65535]")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sequence = 0

    def _request(self, command_id: int, data: bytes = b"") -> SiyiPacket:
        sequence = self._sequence
        self._sequence = (self._sequence + 1) & 0xFFFF
        request = build_packet(command_id, data, sequence=sequence)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(self.timeout)
            sock.sendto(request, (self.host, self.port))
            response, _ = sock.recvfrom(2048)

        packet = parse_packet(response)
        if packet.command_id != command_id:
            raise SiyiProtocolError(
                f"response command mismatch: expected 0x{command_id:02x}, "
                f"got 0x{packet.command_id:02x}"
            )
        if not packet.is_ack:
            raise SiyiProtocolError("response packet is not marked as ACK")
        return packet

    def turn(self, yaw_speed: int, pitch_speed: int) -> bool:
        """Send one bounded velocity command; caller must send zero to stop."""

        for name, value in (("yaw_speed", yaw_speed), ("pitch_speed", pitch_speed)):
            if not isinstance(value, int) or not -100 <= value <= 100:
                raise ValueError(f"{name} must be an integer in [-100, 100]")
        data = struct.pack("<bb", yaw_speed, pitch_speed)
        response = self._request(CMD_TURN, data)
        return bool(response.data and response.data[0] == 1)

    def stop(self) -> bool:
        """Send the documented zero-speed command."""

        return self.turn(0, 0)

    def center(self) -> bool:
        """Trigger the documented yaw-and-pitch center position."""

        response = self._request(CMD_CENTER, b"\x01")
        return bool(response.data and response.data[0] == 1)

    def query_status(self) -> GimbalStatus:
        return GimbalStatus.from_payload(self._request(CMD_GET_STATUS).data)

    def query_hardware_id(self) -> str:
        data = self._request(CMD_GET_HARDWARE_ID).data
        return data.split(b"\x00", 1)[0].decode("ascii", errors="replace")

    def query_firmware(self) -> FirmwareInfo:
        return FirmwareInfo.from_payload(self._request(CMD_GET_FIRMWARE).data)

    def query_attitude(self) -> Attitude:
        return Attitude.from_payload(self._request(CMD_GET_ATTITUDE).data)

    def query_snapshot(self) -> dict[str, Any]:
        """Return a JSON-friendly read-only snapshot of the ZT6 state."""

        return {
            "target": f"{self.host}:{self.port}",
            "hardware_id": self.query_hardware_id(),
            "firmware": self.query_firmware().as_dict(),
            "status": self.query_status().as_dict(),
            "attitude": self.query_attitude().as_dict(),
        }


def snapshot_json(snapshot: dict[str, Any]) -> str:
    """Serialize a snapshot for shell tools and dashboard diagnostics."""

    return json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True)
