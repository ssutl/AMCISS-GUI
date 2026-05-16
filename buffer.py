"""
DataBuffer
==========
Thread-safe ring buffer holding the most recent N seconds of LDC scans.

The buffer is shared between:

  * the UDP listener thread (or dummy generator), which calls
    :meth:`DataBuffer.push` for each incoming packet, and
  * the Qt UI thread, which calls :meth:`DataBuffer.get_snapshot` on
    every refresh tick to draw the plots and heatmap.

A single ``threading.Lock`` serialises all access to the underlying
``deque``. The recorder is invoked outside the lock to avoid blocking
the UDP receive thread on disk-free CSV buffering.
"""

import threading
from collections import deque

import numpy as np

NUM_LDCS = 64


class DataBuffer:
    """Fixed-duration ring buffer of ``(timestamp_ms, L, RP)`` scans."""

    def __init__(self, duration_s: float = 60.0, sample_rate_hz: float = 2200.0):
        """
        Args:
            duration_s: How many seconds of history to retain. Older
                samples are discarded automatically by the underlying
                bounded ``deque``.
            sample_rate_hz: Expected packet rate from the firmware.
                Used only to size the deque — actual incoming rate may
                vary and the buffer still works correctly.
        """
        self._lock = threading.Lock()
        self.duration_s = duration_s
        self.sample_rate_hz = sample_rate_hz
        self._max_samples = int(duration_s * sample_rate_hz)
        # Each entry is a tuple: (timestamp_ms, l_readings[64], rp_readings[64])
        self._buffer: deque = deque(maxlen=self._max_samples)

        # Packet-loss bookkeeping. ``_last_seq`` lets us detect gaps in
        # the wrap-around 16-bit sequence number from the firmware.
        self.dropped_packets = 0
        self._last_seq: int | None = None

    # ── Producer side (UDP listener thread) ──────────────────────

    def push(self, seq: int, timestamp_ms: int,
             l_readings: np.ndarray, rp_readings: np.ndarray) -> None:
        """
        Append one scan to the buffer and update drop statistics.

        Called from the UDP listener thread for every valid packet.
        Sequence-number gaps are accumulated into ``dropped_packets``
        (modulo the 16-bit wrap).

        If a :class:`Recorder` has been injected as ``self._recorder``
        and is currently recording, the same scan is forwarded to it.
        The recorder call is made outside the lock so a slow recorder
        cannot stall packet ingestion.
        """
        with self._lock:
            if self._last_seq is not None:
                gap = (seq - self._last_seq) & 0xFFFF
                if gap > 1:
                    self.dropped_packets += gap - 1
            self._last_seq = seq
            self._buffer.append((timestamp_ms, l_readings.copy(), rp_readings.copy()))

        recorder = getattr(self, '_recorder', None)
        if recorder and recorder.is_recording:
            recorder.write(seq, timestamp_ms, l_readings, rp_readings)

    # ── Consumer side (UI thread) ────────────────────────────────

    def get_snapshot(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Return a copy of the full buffer as three aligned numpy arrays.

        Returns:
            timestamps_ms: ``float64`` array, shape ``(N,)``.
            l_readings: raw uint16 inductance values, shape ``(N, 64)``.
            rp_readings: raw uint16 RP values, shape ``(N, 64)``.

        When the buffer is empty, returns empty arrays with the
        expected column count so callers can index without special
        casing.
        """
        with self._lock:
            if not self._buffer:
                empty = np.empty((0, NUM_LDCS), dtype=np.float32)
                return np.array([]), empty, empty.copy()
            timestamps  = np.array([t  for t, _, __ in self._buffer], dtype=np.float64)
            l_readings  = np.stack([l  for _, l, __ in self._buffer])
            rp_readings = np.stack([rp for _, __, rp in self._buffer])
            return timestamps, l_readings, rp_readings

    def get_ldc_trace(self, ldc_index: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Return ``(timestamps_ms, l_values, rp_values)`` for a single
        channel. Convenience wrapper around :meth:`get_snapshot`.
        """
        timestamps, l_readings, rp_readings = self.get_snapshot()
        if l_readings.size == 0:
            return timestamps, np.array([]), np.array([])
        return timestamps, l_readings[:, ldc_index], rp_readings[:, ldc_index]

    # ── Configuration & lifecycle ────────────────────────────────

    def set_duration(self, duration_s: float) -> None:
        """Resize the ring buffer to a new retention window."""
        with self._lock:
            self.duration_s = duration_s
            self._max_samples = int(duration_s * self.sample_rate_hz)
            self._buffer = deque(self._buffer, maxlen=self._max_samples)

    def clear(self) -> None:
        """Drop all retained samples and reset drop statistics."""
        with self._lock:
            self._buffer.clear()
            self._last_seq = None
            self.dropped_packets = 0

    @property
    def sample_count(self) -> int:
        """Number of scans currently held in the buffer."""
        with self._lock:
            return len(self._buffer)
