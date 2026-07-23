from __future__ import annotations


FRAME_HEADER = 0x7B
FRAME_TAIL = 0x7D
FRAME_SIZE = 24


def decode_voltage_frame(frame: bytes) -> float | None:
    """Decode a validated 24-byte Wheeltec telemetry frame."""
    if len(frame) != FRAME_SIZE or frame[0] != FRAME_HEADER or frame[-1] != FRAME_TAIL:
        return None
    checksum = 0
    for value in frame[:22]:
        checksum ^= value
    if checksum != frame[22]:
        return None
    millivolts = (frame[20] << 8) | frame[21]
    return millivolts / 1000.0
