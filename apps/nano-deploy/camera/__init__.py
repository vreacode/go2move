"""Camera integrations used by the board-side deployment."""

from .siyi_zt6 import (
    CMD_GET_ATTITUDE,
    CMD_GET_FIRMWARE,
    CMD_GET_HARDWARE_ID,
    CMD_GET_STATUS,
    CMD_TURN,
    Attitude,
    FirmwareInfo,
    GimbalStatus,
    SiyiPacket,
    SiyiProtocolError,
    SiyiZt6Client,
    build_packet,
    parse_packet,
)

__all__ = [
    "Attitude",
    "CMD_GET_ATTITUDE",
    "CMD_GET_FIRMWARE",
    "CMD_GET_HARDWARE_ID",
    "CMD_GET_STATUS",
    "CMD_TURN",
    "FirmwareInfo",
    "GimbalStatus",
    "SiyiPacket",
    "SiyiProtocolError",
    "SiyiZt6Client",
    "build_packet",
    "parse_packet",
]
