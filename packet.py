"""
AMCISS Packet Definition
========================
Wire format and codec for UDP packets carrying one full LDC scan
(all 64 inductance channels plus the matching RP register values)
from the STM32 firmware to the host GUI.

Wire layout (264 bytes, little-endian, no padding):

    Offset  Size  Field         Notes
      0      2    magic         0xAA, 0xBB — packet dropped if wrong
      2      2    seq           uint16, wraps at 65535
      4      4    timestamp_ms  uint32, HAL_GetTick() on STM32
      8    128    ldc[0..63]    64 raw L  register values  (uint16)
    136    128    rp[0..63]     64 raw RP register values  (uint16)

Channel layout: indices 0..31 belong to DCM0, 32..63 to DCM1.

No CRC is included — packets travel over a local link and the
2-byte magic header is sufficient to discard stray traffic.

Reference C struct (for firmware authors)
-----------------------------------------
    #pragma pack(push, 1)
    typedef struct {
        uint8_t  magic[2];        // 0xAA, 0xBB
        uint16_t seq;             // sequence number, wraps at 65535
        uint32_t timestamp_ms;    // HAL_GetTick()
        uint16_t ldc[64];         // raw L  register values
        uint16_t rp[64];          // raw RP register values
    } AMCISS_Packet_t;            // sizeof = 264
    #pragma pack(pop)
"""

import struct
import numpy as np

# ── Wire constants ───────────────────────────────────────────────
MAGIC = b'\xAA\xBB'
NUM_LDCS = 64
PACKET_SIZE = 264                            # 2 + 2 + 4 + 128 + 128

HEADER_FMT = '<2sHI'                         # magic(2s) seq(H) timestamp(I)
HEADER_SIZE = struct.calcsize(HEADER_FMT)    # 8 bytes
DATA_FMT = f'<{NUM_LDCS}H'                   # 64 x uint16 — used for both L and RP blocks

# ── Calibration ──────────────────────────────────────────────────
# Linear mapping from raw uint16 register value to inductance in µH:
#   raw =     0  →  0           µH
#   raw = 65535  →  SCALE_FACTOR_UH µH
# The host GUI exposes a spin box that mutates this at runtime so the
# operator can recalibrate without a code change.
SCALE_FACTOR_UH = 50.0


def raw_to_uh(raw: np.ndarray) -> np.ndarray:
    """Convert raw uint16 L register values to inductance in µH."""
    return (raw.astype(np.float32) / 65535.0) * SCALE_FACTOR_UH


def encode_packet(seq: int, timestamp_ms: int,
                  l_readings: np.ndarray, rp_readings: np.ndarray) -> bytes:
    """
    Build a 264-byte packet from a seq number, timestamp and two
    64-element uint16 reading arrays. Used by the dummy generator
    so test data exercises the same codec path as real packets.
    """
    header = struct.pack(HEADER_FMT, MAGIC, seq & 0xFFFF, timestamp_ms & 0xFFFFFFFF)
    l_data = struct.pack(DATA_FMT, *l_readings.astype(np.uint16).tolist())
    rp_data = struct.pack(DATA_FMT, *rp_readings.astype(np.uint16).tolist())
    return header + l_data + rp_data


def decode_packet(raw: bytes) -> tuple[int, int, np.ndarray, np.ndarray] | None:
    """
    Decode a raw UDP datagram.

    Returns ``(seq, timestamp_ms, l_readings, rp_readings)`` on success
    or ``None`` if the payload is the wrong size or the magic header
    does not match. Both reading arrays are returned as raw uint16 —
    apply :func:`raw_to_uh` to ``l_readings`` for µH.
    """
    if len(raw) != PACKET_SIZE:
        return None
    magic, seq, timestamp_ms = struct.unpack_from(HEADER_FMT, raw, 0)
    if magic != MAGIC:
        return None
    l_readings = np.array(
        struct.unpack_from(DATA_FMT, raw, HEADER_SIZE),
        dtype=np.uint16,
    )
    rp_readings = np.array(
        struct.unpack_from(DATA_FMT, raw, HEADER_SIZE + NUM_LDCS * 2),
        dtype=np.uint16,
    )
    return seq, timestamp_ms, l_readings, rp_readings
