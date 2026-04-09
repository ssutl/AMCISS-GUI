"""
AMCISS Data Recorder
====================
Records LDC readings to CSV for offline signal processing.

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
        self._file = None
        self._writer = None
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._recording = False
        self._sample_count = 0
        self.current_filename = None

    def start(self) -> str:
        """Start recording. Returns the filename being written to."""
        with self._lock:
            if self._recording:
                return self.current_filename

            timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            filename = self._output_dir / f'amciss_{timestamp}.csv'
            self.current_filename = str(filename)
            self._file = open(filename, 'w', newline='')
            self._writer = csv.writer(self._file)

            # Header row
            l_raw_headers = [f'ldc{i}_raw' for i in range(NUM_LDCS)]
            l_uh_headers  = [f'ldc{i}_uh'  for i in range(NUM_LDCS)]
            rp_raw_headers = [f'rp{i}_raw' for i in range(NUM_LDCS)]
            self._writer.writerow(
                ['timestamp_ms', 'seq'] + l_raw_headers + l_uh_headers + rp_raw_headers
            )

            self._recording = True
            self._sample_count = 0
            print(f'[Recorder] Started: {filename}')
            return self.current_filename

    def write(self, seq: int, timestamp_ms: int,
              l_readings: np.ndarray, rp_readings: np.ndarray):
        """Write one packet to CSV. Call from any thread."""
        with self._lock:
            if not self._recording or self._writer is None:
                return
            uh = raw_to_uh(l_readings)
            row = (
                [timestamp_ms, seq]
                + l_readings.tolist()
                + [f'{v:.4f}' for v in uh.tolist()]
                + rp_readings.tolist()
            )
            self._writer.writerow(row)
            self._sample_count += 1
            if self._sample_count % 50 == 0:
                self._file.flush()

    def stop(self) -> int:
        """Stop recording. Returns total sample count."""
        with self._lock:
            if not self._recording:
                return 0
            self._recording = False
            if self._file:
                self._file.flush()
                self._file.close()
                self._file = None
                self._writer = None
            count = self._sample_count
            print(f'[Recorder] Stopped. {count} samples saved to {self.current_filename}')
            return count

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def sample_count(self) -> int:
        return self._sample_count
