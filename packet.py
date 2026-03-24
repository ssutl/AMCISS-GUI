"""
AMCISS Packet Definition
========================
Packet structure (266 bytes total):
  [0xAA, 0xBB]       - Magic header       (2 bytes)
  [seq_num]          - uint16 sequence    (2 bytes)
  [timestamp_ms]     - uint32 ms          (4 bytes)
  [ldc_0..ldc_63]    - 64 × float32 µH   (256 bytes)
  [crc16]            - CRC checksum       (2 bytes)
"""

import struct
import numpy as np

MAGIC = b'\xAA\xBB'
PACKET_SIZE = 266
HEADER_FMT = '<2sHI'   # magic(2s) seq(H) timestamp(I)
HEADER_SIZE = struct.calcsize(HEADER_FMT)  # 8
NUM_LDCS = 64
DATA_FMT = f'<{NUM_LDCS}f'
DATA_SIZE = struct.calcsize(DATA_FMT)      # 256


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def encode_packet(seq: int, timestamp_ms: int, readings: np.ndarray) -> bytes:
    """Encode a packet from readings array (64 floats, µH)."""
    header = struct.pack(HEADER_FMT, MAGIC, seq & 0xFFFF, timestamp_ms & 0xFFFFFFFF)
    data = struct.pack(DATA_FMT, *readings.tolist())
    payload = header + data
    crc = struct.pack('<H', crc16(payload))
    return payload + crc


def decode_packet(raw: bytes):
    """
    Decode a raw UDP packet.
    Returns (seq, timestamp_ms, readings_ndarray) or None if invalid.
    """
    if len(raw) != PACKET_SIZE:
        return None
    magic, seq, timestamp_ms = struct.unpack_from(HEADER_FMT, raw, 0)
    if magic != MAGIC:
        return None
    payload = raw[:-2]
    received_crc = struct.unpack_from('<H', raw, PACKET_SIZE - 2)[0]
    if crc16(payload) != received_crc:
        return None
    readings = np.array(struct.unpack_from(DATA_FMT, raw, HEADER_SIZE), dtype=np.float32)
    return seq, timestamp_ms, readings
