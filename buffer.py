"""
Ring buffer storing the last N seconds of LDC readings.
Thread-safe via a lock.
"""

import threading
import numpy as np
from collections import deque


class DataBuffer:
    def __init__(self, duration_s: float = 60.0, sample_rate_hz: float = 50.0):
        self._lock = threading.Lock()
        self.duration_s = duration_s
        self.sample_rate_hz = sample_rate_hz
        self._max_samples = int(duration_s * sample_rate_hz)
        # deque of (timestamp_ms, readings[64]) tuples
        self._buffer: deque = deque(maxlen=self._max_samples)
        self.dropped_packets = 0
        self._last_seq = None

    def push(self, seq: int, timestamp_ms: int, readings: np.ndarray):
        with self._lock:
            if self._last_seq is not None:
                gap = (seq - self._last_seq) & 0xFFFF
                if gap > 1:
                    self.dropped_packets += gap - 1
            self._last_seq = seq
            self._buffer.append((timestamp_ms, readings.copy()))

    def get_snapshot(self):
        """Returns (timestamps_ms array, readings array [N x 64])."""
        with self._lock:
            if not self._buffer:
                return np.array([]), np.empty((0, 64), dtype=np.float32)
            timestamps = np.array([t for t, _ in self._buffer], dtype=np.float64)
            readings = np.stack([r for _, r in self._buffer])
            return timestamps, readings

    def get_ldc_trace(self, ldc_index: int):
        """Returns (timestamps_ms, values) for a single LDC."""
        timestamps, readings = self.get_snapshot()
        if readings.size == 0:
            return timestamps, np.array([])
        return timestamps, readings[:, ldc_index]

    def set_duration(self, duration_s: float):
        with self._lock:
            self.duration_s = duration_s
            self._max_samples = int(duration_s * self.sample_rate_hz)
            new_buf = deque(self._buffer, maxlen=self._max_samples)
            self._buffer = new_buf

    def clear(self):
        with self._lock:
            self._buffer.clear()
            self._last_seq = None
            self.dropped_packets = 0

    @property
    def sample_count(self):
        with self._lock:
            return len(self._buffer)
