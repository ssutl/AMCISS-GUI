"""
AMCISS Packet Definition
========================
Packet structure (138 bytes total) — sent by main PCB to GUI PC:

  [0xAA, 0xBB]         Magic header         (2 bytes)
  [seq_num: uint16]    Sequence number       (2 bytes)
  [timestamp: uint32]  ms since boot         (4 bytes)
  [ldc[0..63]: uint16] 64 raw L register val (128 bytes)
  [crc16: uint16]      CRC16 checksum        (2 bytes)
  Total: 138 bytes

Raw L values are the 16-bit register output of the LDC1101.
Conversion to inductance is done in the GUI using a calibration factor.

C struct for firmware reference:
---------------------------------
#pragma pack(push, 1)
typedef struct {
    uint8_t  magic[2];        // 0xAA, 0xBB
    uint16_t seq;             // sequence number, wraps at 65535
    uint32_t timestamp_ms;    // ms since device boot (HAL_GetTick())
    uint16_t ldc[64];         // raw L register values, LDC 0..63
    uint16_t crc;             // CRC16 of all bytes before this field
} AMCISS_Packet_t;
#pragma pack(pop)
---------------------------------
"""

import struct
import numpy as np

MAGIC = b'\xAA\xBB'
NUM_LDCS = 64
PACKET_SIZE = 138  # 2 + 2 + 4 + 128 + 2

HEADER_FMT = '<2sHI'        # magic(2s) seq(H) timestamp(I)
HEADER_SIZE = struct.calcsize(HEADER_FMT)   # 8 bytes
DATA_FMT = f'<{NUM_LDCS}H'  # 64 x uint16
DATA_SIZE = struct.calcsize(DATA_FMT)       # 128 bytes

# Calibration: raw uint16 -> inductance (µH)
# Set this once measured against a known reference coil.
# raw=0 -> 0 µH, raw=65535 -> SCALE_FACTOR µH
SCALE_FACTOR_UH = 50.0  # placeholder — update after calibration


def raw_to_uh(raw: np.ndarray) -> np.ndarray:
    """Convert raw uint16 LDC register values to inductance in µH."""
    return (raw.astype(np.float32) / 65535.0) * SCALE_FACTOR_UH


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
    """
    Encode a packet from raw uint16 readings array (64 values).
    readings: np.ndarray of uint16, shape (64,)
    """
    header = struct.pack(HEADER_FMT, MAGIC, seq & 0xFFFF, timestamp_ms & 0xFFFFFFFF)
    data = struct.pack(DATA_FMT, *readings.astype(np.uint16).tolist())
    payload = header + data
    crc = struct.pack('<H', crc16(payload))
    return payload + crc


def decode_packet(raw: bytes):
    """
    Decode a raw UDP packet.
    Returns (seq, timestamp_ms, raw_readings_ndarray[64]) or None if invalid.
    raw_readings are uint16 — call raw_to_uh() to convert to µH.
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
    readings = np.array(
        struct.unpack_from(DATA_FMT, raw, HEADER_SIZE),
        dtype=np.uint16
    )
    return seq, timestamp_ms, readings
