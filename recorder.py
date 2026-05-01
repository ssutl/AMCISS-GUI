"""
AMCISS Data Recorder
====================
Records LDC readings to CSV for offline signal processing.

Buffers all packets in memory during recording, then writes
everything to CSV when recording stops. This avoids file I/O
on the receive thread, which previously caused missed samples.

CSV format:
  timestamp_ms, seq,
  ldc0_raw .. ldc63_raw,
  ldc0_uh  .. ldc63_uh,
  rp0_raw  .. rp63_raw

Each row = one UDP packet (one full scan of all 64 channels).
"""

import csv
import threading
import numpy as np
from datetime import datetime
from pathlib import Path
from packet import raw_to_uh

NUM_LDCS = 64


class Recorder:
    def __init__(self, output_dir: str = '.'):
        self._lock = threading.Lock()
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._recording = False
        self._sample_count = 0
        self.current_filename = None
        self._buffer = []  # in-memory packet buffer

    def start(self) -> str:
        """Start recording. Returns the filename that will be written to."""
        with self._lock:
            if self._recording:
                return self.current_filename

            timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            filename = self._output_dir / f'amciss_{timestamp}.csv'
            self.current_filename = str(filename)
            self._buffer = []
            self._recording = True
            self._sample_count = 0
            print(f'[Recorder] Started (buffering to memory)')
            return self.current_filename

    def write(self, seq: int, timestamp_ms: int,
              l_readings: np.ndarray, rp_readings: np.ndarray):
        """Buffer one packet in memory. Call from any thread."""
        with self._lock:
            if not self._recording:
                return
            self._buffer.append((seq, timestamp_ms,
                                 l_readings.copy(), rp_readings.copy()))
            self._sample_count += 1

    def stop(self) -> int:
        """Stop recording, flush buffer to CSV. Returns total sample count."""
        with self._lock:
            if not self._recording:
                return 0
            self._recording = False
            buf = self._buffer
            self._buffer = []
            count = self._sample_count

        # Write to CSV outside the lock so we don't block incoming packets
        self._flush_to_csv(buf)
        print(f'[Recorder] Stopped. {count} samples saved to {self.current_filename}')
        return count

    def _flush_to_csv(self, buf: list):
        """Write all buffered packets to CSV in one go."""
        if not buf:
            return
        with open(self.current_filename, 'w', newline='') as f:
            writer = csv.writer(f)

            l_raw_headers = [f'ldc{i}_raw' for i in range(NUM_LDCS)]
            l_uh_headers  = [f'ldc{i}_uh'  for i in range(NUM_LDCS)]
            rp_raw_headers = [f'rp{i}_raw' for i in range(NUM_LDCS)]
            writer.writerow(
                ['timestamp_ms', 'seq'] + l_raw_headers + l_uh_headers + rp_raw_headers
            )

            for seq, timestamp_ms, l_readings, rp_readings in buf:
                uh = raw_to_uh(l_readings)
                row = (
                    [timestamp_ms, seq]
                    + l_readings.tolist()
                    + [f'{v:.4f}' for v in uh.tolist()]
                    + rp_readings.tolist()
                )
                writer.writerow(row)

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def sample_count(self) -> int:
        return self._sample_count
