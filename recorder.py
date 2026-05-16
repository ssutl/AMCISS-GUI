"""
AMCISS Data Recorder
====================
Captures LDC scans to a CSV file for offline signal-processing work
(MATLAB, Python notebooks, etc.).

Design note — in-memory buffering
---------------------------------
An earlier implementation wrote each packet to disk synchronously on
the UDP receive thread. CSV serialisation plus filesystem flushes
periodically blocked that thread for long enough to drop incoming
packets. The current implementation appends each packet to a list in
memory while recording, then flushes the whole list to CSV on
:meth:`Recorder.stop`. The hot path is now lock-protected list append
only, which is negligible compared to the packet interval.

CSV columns
-----------
    timestamp_ms, seq,
    ldc0_raw .. ldc63_raw,    # raw uint16 L register values
    ldc0_uh  .. ldc63_uh,     # L converted to inductance in µH
    rp0_raw  .. rp63_raw      # raw uint16 RP register values

One row per UDP packet (one full 64-channel scan).
"""

import csv
import threading
from datetime import datetime
from pathlib import Path

import numpy as np

from packet import raw_to_uh

NUM_LDCS = 64


class Recorder:
    """Thread-safe in-memory recorder that flushes to CSV on stop."""

    def __init__(self, output_dir: str = '.'):
        """
        Args:
            output_dir: Directory where CSV files are written. Created
                if it does not exist. Each recording produces a file
                named ``amciss_<YYYY-MM-DD_HH-MM-SS>.csv``.
        """
        self._lock = threading.Lock()
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._recording = False
        self._sample_count = 0
        self.current_filename: str | None = None
        # In-memory packet buffer; flushed to CSV on stop().
        self._buffer: list[tuple[int, int, np.ndarray, np.ndarray]] = []

    # ── Lifecycle ─────────────────────────────────────────────────

    def start(self) -> str:
        """
        Begin a new recording. Returns the absolute path of the CSV
        file that will be produced when recording stops. A no-op if a
        recording is already in progress.
        """
        with self._lock:
            if self._recording:
                return self.current_filename

            timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            filename = self._output_dir / f'amciss_{timestamp}.csv'
            self.current_filename = str(filename)
            self._buffer = []
            self._recording = True
            self._sample_count = 0
            print('[Recorder] Started (buffering to memory)')
            return self.current_filename

    def stop(self) -> int:
        """
        Stop recording and flush the in-memory buffer to disk.

        Returns the number of samples written. The disk write happens
        outside the lock so concurrent ``write()`` calls from the UDP
        thread are not blocked while the CSV is being produced.
        """
        with self._lock:
            if not self._recording:
                return 0
            self._recording = False
            buf = self._buffer
            self._buffer = []
            count = self._sample_count

        self._flush_to_csv(buf)
        print(f'[Recorder] Stopped. {count} samples saved to {self.current_filename}')
        return count

    # ── Hot path (called from UDP receive thread) ────────────────

    def write(self, seq: int, timestamp_ms: int,
              l_readings: np.ndarray, rp_readings: np.ndarray) -> None:
        """
        Buffer one scan. Safe to call from any thread; cheap enough to
        sit on the UDP receive path.
        """
        with self._lock:
            if not self._recording:
                return
            self._buffer.append((seq, timestamp_ms,
                                 l_readings.copy(), rp_readings.copy()))
            self._sample_count += 1

    # ── Persistence ──────────────────────────────────────────────

    def _flush_to_csv(self, buf: list) -> None:
        """Serialise the captured buffer to CSV in one pass."""
        if not buf:
            return
        with open(self.current_filename, 'w', newline='') as f:
            writer = csv.writer(f)

            l_raw_headers  = [f'ldc{i}_raw' for i in range(NUM_LDCS)]
            l_uh_headers   = [f'ldc{i}_uh'  for i in range(NUM_LDCS)]
            rp_raw_headers = [f'rp{i}_raw'  for i in range(NUM_LDCS)]
            writer.writerow(
                ['timestamp_ms', 'seq']
                + l_raw_headers + l_uh_headers + rp_raw_headers
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

    # ── Status accessors ─────────────────────────────────────────

    @property
    def is_recording(self) -> bool:
        """``True`` between :meth:`start` and :meth:`stop`."""
        return self._recording

    @property
    def sample_count(self) -> int:
        """Number of scans captured in the current (or last) recording."""
        return self._sample_count
