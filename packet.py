"""
AMCISS Packet Definition
========================
Packet structure (136 bytes total) — sent by main PCB to GUI PC:

  [0xAA, 0xBB]         Magic header         (2 bytes)
  [seq_num: uint16]    Sequence number       (2 bytes)
  [timestamp: uint32]  ms since boot         (4 bytes)
  [ldc[0..63]: uint16] 64 raw L register val (128 bytes)
  Total: 136 bytes

No CRC — local network, magic header is sufficient for validation.

C struct for firmware reference:
---------------------------------
#pragma pack(push, 1)
typedef struct {
    uint8_t  magic[2];        // 0xAA, 0xBB
    uint16_t seq;             // sequence number, wraps at 65535
    uint32_t timestamp_ms;    // HAL_GetTick()
    uint16_t ldc[64];         // raw L register values, ldc[0..31]=DCM0, ldc[32..63]=DCM1
} AMCISS_Packet_t;            // sizeof = 136
#pragma pack(pop)
---------------------------------
"""

import struct
import numpy as np

MAGIC = b'\xAA\xBB'
NUM_LDCS = 64
PACKET_SIZE = 136  # 2 + 2 + 4 + 128

HEADER_FMT = '<2sHI'        # magic(2s) seq(H) timestamp(I)
HEADER_SIZE = struct.calcsize(HEADER_FMT)   # 8 bytes
DATA_FMT = f'<{NUM_LDCS}H'  # 64 x uint16

# Calibration: raw uint16 -> inductance (µH)
# raw=0 -> 0 µH, raw=65535 -> SCALE_FACTOR_UH µH
# Update after measuring against a known coil.
SCALE_FACTOR_UH = 50.0


def raw_to_uh(raw: np.ndarray) -> np.ndarray:
    """Convert raw uint16 LDC register values to inductance in µH."""
    return (raw.astype(np.float32) / 65535.0) * SCALE_FACTOR_UH


def encode_packet(seq: int, timestamp_ms: int, readings: np.ndarray) -> bytes:
    """Encode a packet. readings: uint16 ndarray shape (64,)"""
    header = struct.pack(HEADER_FMT, MAGIC, seq & 0xFFFF, timestamp_ms & 0xFFFFFFFF)
    data = struct.pack(DATA_FMT, *readings.astype(np.uint16).tolist())
    return header + data


def decode_packet(raw: bytes):
    """
    Decode a raw UDP packet.
    Returns (seq, timestamp_ms, readings_ndarray[64]) or None if invalid.
    readings are raw uint16 — call raw_to_uh() to convert to µH.
    """
    if len(raw) != PACKET_SIZE:
        return None
    magic, seq, timestamp_ms = struct.unpack_from(HEADER_FMT, raw, 0)
    if magic != MAGIC:
        return None
    readings = np.array(
        struct.unpack_from(DATA_FMT, raw, HEADER_SIZE),
        dtype=np.uint16
    )
    return seq, timestamp_ms, readings
