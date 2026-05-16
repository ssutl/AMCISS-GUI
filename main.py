"""
AMCISS GUI — Main Application
=============================
Adaptive Multichannel Induction Sorting System — data-visualisation
interface.

The GUI is a PyQt6 application that runs alongside the AMCISS firmware
on a host PC. It receives 264-byte UDP scan packets from the STM32,
buffers the last N seconds in memory, and presents the data live in
two views:

  * **LDC Traces** — line plot of one or more user-selected channels
    against time, with a per-channel colour legend.
  * **Heatmap** — 2-D image of all 64 channels against distance along
    the belt (derived from time × user-supplied belt velocity), with a
    diverging colour map centred on a captured "no metal" baseline.

A side panel exposes the connection settings, the calibration scale
factor, recording controls and the heatmap baseline recalibration
button. Incoming scans are also forwarded to a :class:`Recorder` so
operators can capture a CSV for offline analysis.

Layout
------
  ┌──────────────┬──────────────────────────────────────────┐
  │  logo        │  LDC selector (2 × DCM grids)            │
  │  settings    ├──────────────────────────────────────────┤
  │  controls    │  Tab 1: LDC traces                       │
  │              │  Tab 2: heatmap (LDC index × distance)   │
  └──────────────┴──────────────────────────────────────────┘
  status bar: connection state, packets, dropped, buffer size
"""

import os
import sys
import traceback

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, QTimer, QElapsedTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSpinBox, QDoubleSpinBox, QComboBox,
    QGroupBox, QLineEdit, QSplitter, QStatusBar, QTabWidget, QGridLayout,
)

from buffer import DataBuffer
from packet import raw_to_uh, SCALE_FACTOR_UH
from recorder import Recorder
from udp_listener import UDPListener, DummyGenerator

# ── Colour scheme (Catppuccin-inspired) ──────────────────────────
pg.setConfigOption('background', '#1e1e2e')
pg.setConfigOption('foreground', '#cdd6f4')

ACCENT  = '#89b4fa'  # blue   — primary highlight
GREEN   = '#a6e3a1'  # success / connected state
RED     = '#f38ba8'  # error / recording state
YELLOW  = '#f9e2af'  # warning
SURFACE = '#313244'  # widget background
BASE    = '#1e1e2e'  # window background
TEXT    = '#cdd6f4'  # primary text

# Global stylesheet applied to QMainWindow at construction time.
STYLE = f"""
QMainWindow, QWidget {{ background: {BASE}; color: {TEXT}; font-family: 'Segoe UI', sans-serif; }}
QGroupBox {{ border: 1px solid {SURFACE}; border-radius: 6px; margin-top: 8px; padding-top: 8px; font-weight: bold; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; color: {ACCENT}; }}
QPushButton {{ background: {SURFACE}; border: 1px solid {ACCENT}; border-radius: 4px;
               padding: 5px 12px; color: {TEXT}; }}
QPushButton:hover {{ background: {ACCENT}; color: {BASE}; }}
QPushButton:pressed {{ background: #7aa2f7; }}
QLabel {{ color: {TEXT}; }}
QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox {{
    background: {SURFACE}; border: 1px solid #45475a; border-radius: 4px;
    padding: 3px; color: {TEXT}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right;
    width: 16px; border-left: 1px solid #45475a; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 16px; border-left: 1px solid #45475a; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-bottom: 5px solid {TEXT}; width: 0; height: 0; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {TEXT}; width: 0; height: 0; }}
QListWidget {{ background: {SURFACE}; border: 1px solid #45475a; border-radius: 4px; }}
QTabWidget::pane {{ border: 1px solid {SURFACE}; }}
QTabBar::tab {{ background: {SURFACE}; padding: 6px 14px; border-radius: 4px 4px 0 0; }}
QTabBar::tab:selected {{ background: {ACCENT}; color: {BASE}; }}
QStatusBar {{ background: {SURFACE}; color: {TEXT}; }}
"""

NUM_LDCS = 64
REFRESH_MS = 100  # default UI refresh interval (ms)


# ─────────────────────────────────────────────────────────────────
# Heatmap view
# ─────────────────────────────────────────────────────────────────

class HeatmapWidget(QWidget):
    """
    2-D heatmap of LDC readings.

    Axes:
        X — LDC index (0..63), i.e. position across the belt.
        Y — distance along the belt in metres, derived from elapsed
            time × user-supplied belt velocity.

    A per-coil baseline is captured automatically from the first
    samples that arrive, and subsequent frames are rendered as
    deviations from that baseline through a diverging colour map. The
    operator can force a fresh baseline with :meth:`recalibrate` (for
    example, after physically clearing the belt).
    """

    # Number of recent samples used when snapshotting the baseline.
    # ~0.6 s at 50 Hz — long enough to median-out per-channel noise,
    # short enough that the first rock can appear shortly afterwards.
    BASELINE_SNAPSHOT_SAMPLES = 30

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.velocity_ms = 2.0  # m/s default; updated from settings panel.

        # Per-view per-coil baselines (shape [64]); keyed by view label
        # ("L" or "RP") so the two views maintain independent baselines.
        # ``None``/missing until the first auto-snapshot.
        self._baselines: dict[str, np.ndarray] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot_widget = pg.PlotWidget(title='Heatmap — LDC Position vs Distance')
        self.plot_widget.setLabel('bottom', 'LDC Index (position across belt)')
        self.plot_widget.setLabel('left', 'Distance (m)')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.2)

        self.img = pg.ImageItem()
        self.plot_widget.addItem(self.img)

        # Diverging colour map: negative deltas → blue, near-zero → neutral,
        # positive deltas → red. This makes "metal present" pop out
        # symmetrically in either polarity (L spikes positive, RP dips negative).
        cm = pg.colormap.get('CET-D1')
        self.bar = pg.ColorBarItem(values=(-1, 1), colorMap=cm, label='Δ Inductance (µH)')
        self.bar.setImageItem(self.img, insert_in=self.plot_widget.getPlotItem())

        layout.addWidget(self.plot_widget)

    def recalibrate(self) -> None:
        """Drop cached baselines so the next refresh re-snapshots from live data."""
        self._baselines.clear()

    def refresh_plot(self, timestamps_ms: np.ndarray, readings: np.ndarray,
                     cbar_label: str | None = None, view_key: str = 'L') -> None:
        """
        Redraw the heatmap from the latest snapshot.

        Args:
            timestamps_ms: 1-D array of sample timestamps in milliseconds.
            readings: 2-D array of shape ``(N, 64)`` — one row per scan.
            cbar_label: Label suffix for the colour bar; defaults to "".
            view_key: ``"L"`` or ``"RP"`` — selects which baseline cache
                to consult, so the two views never share calibration.
        """
        if readings.size == 0:
            return

        timestamps_ms = np.asarray(timestamps_ms, dtype=np.float64)
        readings = np.asarray(readings, dtype=np.float32)

        # Lazily capture the per-coil baseline once enough samples have
        # arrived. We use the *most recent* N samples (assumed to be
        # "no metal" at the moment of calibration).
        baseline = self._baselines.get(view_key)
        if baseline is None and readings.shape[0] >= self.BASELINE_SNAPSHOT_SAMPLES:
            tail = readings[-self.BASELINE_SNAPSHOT_SAMPLES:]
            baseline = np.median(tail, axis=0).astype(np.float32)
            self._baselines[view_key] = baseline

        if baseline is not None:
            delta = readings - baseline
            display_label = f'Δ {cbar_label}' if cbar_label else 'Δ'
        else:
            # Pre-baseline: fall back to raw readings so the user sees
            # something rather than a blank pane.
            delta = readings
            display_label = cbar_label or ''

        # ``pg.ImageItem`` uses column-major ``data[x, y]`` indexing:
        #   x → LDC index (column across belt)
        #   y → sample / distance along belt
        img_data = delta.T

        elapsed_s = (timestamps_ms - timestamps_ms[0]) / 1000.0
        total_distance = float(elapsed_s[-1] * self.velocity_ms)  # metres

        # Pick a symmetric colour range centred on zero once we have a
        # baseline, so "no change" maps to the neutral midpoint of the
        # diverging colour map. Use the 98th percentile of |delta| to
        # ignore stray outliers.
        if baseline is not None:
            mag = float(np.nanpercentile(np.abs(img_data), 98))
            if mag <= 0:
                mag = 1.0
            lo, hi = -mag, mag
        else:
            lo = float(np.nanpercentile(img_data, 2))
            hi = float(np.nanpercentile(img_data, 98))
            if hi <= lo:
                hi = lo + 1.0

        # ColorBarItem owns the colour levels; tell the image not to
        # auto-level so the two stay in sync.
        self.img.setImage(img_data, autoLevels=False)
        self.bar.setLevels(low=lo, high=hi)
        self.img.setRect(pg.QtCore.QRectF(-0.5, 0, NUM_LDCS, max(total_distance, 0.01)))
        vb = self.plot_widget.getViewBox()
        vb.setRange(
            xRange=(-0.5, NUM_LDCS - 0.5),
            yRange=(0, max(total_distance, 0.1)),
            padding=0,
        )
        self.bar.setLabel('right', display_label)


# ─────────────────────────────────────────────────────────────────
# LDC selector (which channels appear in the trace plot)
# ─────────────────────────────────────────────────────────────────

class LDCSelectorWidget(QWidget):
    """
    2-DCM grid of toggle buttons showing the physical channel layout.

    DCM 1 (indices 0..31) sits on the left, DCM 2 (indices 32..63) on
    the right. Each DCM is laid out as two rows of sixteen buttons
    (row 1: channels 1–16, row 2: channels 17–32 within that DCM).

    The selection drives :class:`SingleLDCWidget`; the heatmap always
    shows all channels regardless of this selection.
    """

    BTN_STYLE_OFF = (
        f'background: {SURFACE}; border: 1px solid #45475a; border-radius: 2px;'
        f' padding: 1px; color: {TEXT}; font-size: 10px;'
        ' min-width: 22px; max-width: 28px; min-height: 18px;'
    )
    BTN_STYLE_ON = (
        f'background: {ACCENT}; border: 1px solid {ACCENT}; border-radius: 2px;'
        f' padding: 1px; color: {BASE}; font-weight: bold; font-size: 10px;'
        ' min-width: 22px; max-width: 28px; min-height: 18px;'
    )

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── Header row: title + bulk action buttons ──────────────
        header = QHBoxLayout()
        header.addWidget(QLabel('LDC Selection'))
        self.select_all_btn = QPushButton('All')
        self.select_all_btn.setFixedWidth(60)
        self.select_all_btn.clicked.connect(self._select_all)
        self.clear_btn = QPushButton('Clear')
        self.clear_btn.setFixedWidth(60)
        self.clear_btn.clicked.connect(self._clear_all)
        header.addStretch()
        header.addWidget(self.select_all_btn)
        header.addWidget(self.clear_btn)
        layout.addLayout(header)

        # ── Grid: DCM 1 | DCM 2 ──────────────────────────────────
        grid_layout = QHBoxLayout()
        grid_layout.setSpacing(12)

        self._buttons: dict[int, QPushButton] = {}  # 0-based index → button
        self._selected: set[int] = set()

        for dcm_idx, dcm_label in enumerate(['DCM 1', 'DCM 2']):
            dcm_box = QVBoxLayout()
            dcm_box.setSpacing(2)
            lbl = QLabel(dcm_label)
            lbl.setStyleSheet(f'color: {ACCENT}; font-weight: bold; font-size: 11px;')
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            dcm_box.addWidget(lbl)

            grid = QGridLayout()
            grid.setSpacing(2)
            base = dcm_idx * 32  # DCM 1 starts at 0, DCM 2 starts at 32

            for row in range(2):
                for col in range(16):
                    ldc_0based = base + row * 16 + col
                    ldc_display = row * 16 + col + 1  # 1-based label within DCM
                    btn = QPushButton(str(ldc_display))
                    btn.setStyleSheet(self.BTN_STYLE_OFF)
                    btn.setCheckable(True)
                    btn.clicked.connect(lambda checked, idx=ldc_0based: self._toggle(idx))
                    grid.addWidget(btn, row, col)
                    self._buttons[ldc_0based] = btn

            dcm_box.addLayout(grid)
            grid_layout.addLayout(dcm_box)

        layout.addLayout(grid_layout)

        # Start with LDC 1 pre-selected so the trace plot has something
        # to draw before the operator interacts with the grid.
        self._toggle(0)
        self._buttons[0].setChecked(True)

    # ── Internal toggles ─────────────────────────────────────────

    def _toggle(self, idx: int) -> None:
        if idx in self._selected:
            self._selected.discard(idx)
            self._buttons[idx].setStyleSheet(self.BTN_STYLE_OFF)
        else:
            self._selected.add(idx)
            self._buttons[idx].setStyleSheet(self.BTN_STYLE_ON)

    def _select_all(self) -> None:
        for idx, btn in self._buttons.items():
            self._selected.add(idx)
            btn.setChecked(True)
            btn.setStyleSheet(self.BTN_STYLE_ON)

    def _clear_all(self) -> None:
        for idx, btn in self._buttons.items():
            self._selected.discard(idx)
            btn.setChecked(False)
            btn.setStyleSheet(self.BTN_STYLE_OFF)

    # ── Public API ───────────────────────────────────────────────

    def selected_indices(self) -> list[int]:
        """Return the sorted list of currently selected 0-based LDC indices."""
        return sorted(self._selected)


# ─────────────────────────────────────────────────────────────────
# Single-LDC trace plot
# ─────────────────────────────────────────────────────────────────

class SingleLDCWidget(QWidget):
    """
    Live line plot of the LDC channels currently selected in the
    :class:`LDCSelectorWidget`. Supports two X-axis modes:

      * Time (s) — relative seconds since the oldest sample in the buffer.
      * Sample No. — integer index into the buffer.

    Curves are added and removed dynamically as the selection changes,
    so the legend always matches the active set.
    """

    def __init__(self, ldc_selector: LDCSelectorWidget,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._selector = ldc_selector
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── X-axis mode selector ─────────────────────────────────
        ctrl_row = QHBoxLayout()
        ctrl_row.addWidget(QLabel('X-Axis:'))
        self.xaxis_combo = QComboBox()
        self.xaxis_combo.addItems(['Time (s)', 'Sample No.'])
        self.xaxis_combo.setFixedWidth(120)
        self.xaxis_combo.currentIndexChanged.connect(self._update_xaxis_label)
        ctrl_row.addWidget(self.xaxis_combo)
        ctrl_row.addStretch()
        layout.addLayout(ctrl_row)

        # ── Plot ─────────────────────────────────────────────────
        self.plot_widget = pg.PlotWidget(title='LDC Inductance Trace')
        self.plot_widget.setLabel('left', 'Inductance (µH)')
        self.plot_widget.setLabel('bottom', 'Time (s)')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.addLegend()
        layout.addWidget(self.plot_widget)

        # Cached curves keyed by 0-based LDC index, so we only create
        # one PlotDataItem per channel and update its data each frame.
        self._curves: dict[int, pg.PlotDataItem] = {}
        # Cycled through for the per-channel curve colours.
        self._colours = [ACCENT, GREEN, RED, YELLOW, '#cba6f7', '#fab387', '#94e2d5']

    def _update_xaxis_label(self) -> None:
        if self.xaxis_combo.currentIndex() == 1:
            self.plot_widget.setLabel('bottom', 'Sample No.')
        else:
            self.plot_widget.setLabel('bottom', 'Time (s)')

    def refresh_plot(self, timestamps_ms: np.ndarray, readings: np.ndarray) -> None:
        """
        Redraw the trace for every channel currently selected.

        Args:
            timestamps_ms: 1-D array of sample timestamps in milliseconds.
            readings: 2-D array of shape ``(N, 64)`` — same row order
                as ``timestamps_ms``.
        """
        if readings.size == 0:
            return

        use_samples = self.xaxis_combo.currentIndex() == 1
        if use_samples:
            x = np.arange(len(timestamps_ms))
        else:
            x = (timestamps_ms - timestamps_ms[0]) / 1000.0

        indices = self._selector.selected_indices()

        # Remove curves for channels that are no longer selected so the
        # legend stays in sync.
        for key in list(self._curves.keys()):
            if key not in indices:
                self.plot_widget.removeItem(self._curves.pop(key))

        # Update (or create) a curve for each selected channel.
        for idx, ldc_i in enumerate(indices):
            colour = self._colours[idx % len(self._colours)]
            if ldc_i not in self._curves:
                pen = pg.mkPen(color=colour, width=1.5)
                self._curves[ldc_i] = self.plot_widget.plot(
                    pen=pen, name=f'LDC {ldc_i + 1}')
            self._curves[ldc_i].setData(x, readings[:, ldc_i])


# ─────────────────────────────────────────────────────────────────
# Status bar (bottom of the window)
# ─────────────────────────────────────────────────────────────────

class StatusBar(QWidget):
    """
    Compact strip showing connection state and packet counters. Sits
    inside the QMainWindow's QStatusBar.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)

        self.status_dot = QLabel('●')
        self.status_dot.setStyleSheet(f'color: {RED}; font-size: 14px;')
        layout.addWidget(self.status_dot)

        self.status_label = QLabel('Disconnected')
        layout.addWidget(self.status_label)
        layout.addStretch()

        self.packets_label = QLabel('Packets: 0')
        layout.addWidget(self.packets_label)
        layout.addSpacing(20)

        self.dropped_label = QLabel('Dropped: 0')
        self.dropped_label.setStyleSheet(f'color: {YELLOW};')
        layout.addWidget(self.dropped_label)
        layout.addSpacing(20)

        self.samples_label = QLabel('Buffer: 0 samples')
        layout.addWidget(self.samples_label)

    def update_stats(self, connected: bool, packets: int,
                     dropped: int, samples: int) -> None:
        """Refresh every counter in one call."""
        if connected:
            self.status_dot.setStyleSheet(f'color: {GREEN}; font-size: 14px;')
            self.status_label.setText('Connected')
        else:
            self.status_dot.setStyleSheet(f'color: {RED}; font-size: 14px;')
            self.status_label.setText('Disconnected')
        self.packets_label.setText(f'Packets: {packets}')
        self.dropped_label.setText(f'Dropped: {dropped}')
        self.samples_label.setText(f'Buffer: {samples} samples')


# ─────────────────────────────────────────────────────────────────
# Settings panel (left-hand side of the window)
# ─────────────────────────────────────────────────────────────────

class SettingsPanel(QGroupBox):
    """
    All operator-facing controls in one column: connection, buffer,
    view selection, recording and heatmap calibration. The widgets are
    public attributes — :class:`MainWindow` wires their signals up to
    the relevant slots during construction.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__('Settings', parent)
        layout = QGridLayout(self)

        # ── Network ──────────────────────────────────────────────
        layout.addWidget(QLabel('UDP Port:'), 0, 0)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(5005)
        layout.addWidget(self.port_spin, 0, 1)

        layout.addWidget(QLabel('Host IP:'), 1, 0)
        self.host_edit = QLineEdit('0.0.0.0')
        layout.addWidget(self.host_edit, 1, 1)

        # ── Buffer / refresh ─────────────────────────────────────
        layout.addWidget(QLabel('Buffer (s):'), 2, 0)
        self.buffer_spin = QDoubleSpinBox()
        self.buffer_spin.setRange(5.0, 300.0)
        self.buffer_spin.setValue(60.0)
        self.buffer_spin.setSingleStep(5.0)
        layout.addWidget(self.buffer_spin, 2, 1)

        layout.addWidget(QLabel('Refresh (ms):'), 3, 0)
        self.refresh_spin = QSpinBox()
        self.refresh_spin.setRange(50, 2000)
        self.refresh_spin.setValue(REFRESH_MS)
        layout.addWidget(self.refresh_spin, 3, 1)

        # ── View toggle ──────────────────────────────────────────
        layout.addWidget(QLabel('View:'), 4, 0)
        self.view_combo = QComboBox()
        self.view_combo.addItems(['L — Inductance (µH)', 'RP — Resistance (raw)'])
        layout.addWidget(self.view_combo, 4, 1)

        # ── Connection / dummy / clear ───────────────────────────
        self.dummy_btn = QPushButton('Start Dummy Data')
        self.dummy_btn.setStyleSheet(f'border-color: {YELLOW};')
        layout.addWidget(self.dummy_btn, 5, 0, 1, 2)

        self.connect_btn = QPushButton('Connect UDP')
        self.connect_btn.setStyleSheet(f'border-color: {GREEN};')
        layout.addWidget(self.connect_btn, 6, 0, 1, 2)

        self.clear_btn = QPushButton('Clear Buffer')
        layout.addWidget(self.clear_btn, 7, 0, 1, 2)

        # ── Sliding window (drives plots, heatmap, recording) ────
        layout.addWidget(QLabel('Window (s):'), 8, 0)
        self.window_spin = QDoubleSpinBox()
        self.window_spin.setRange(1.0, 60.0)
        self.window_spin.setValue(10.0)
        self.window_spin.setDecimals(1)
        self.window_spin.setSingleStep(1.0)
        self.window_spin.setToolTip('Time window for plots, heatmap, and recording duration')
        layout.addWidget(self.window_spin, 8, 1)

        # ── Calibration ──────────────────────────────────────────
        layout.addWidget(QLabel('Scale factor (µH):'), 9, 0)
        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.1, 10000.0)
        self.scale_spin.setValue(SCALE_FACTOR_UH)
        self.scale_spin.setDecimals(1)
        self.scale_spin.setSingleStep(1.0)
        self.scale_spin.setToolTip('Calibration: raw 65535 = this value in µH')
        layout.addWidget(self.scale_spin, 9, 1)

        layout.addWidget(QLabel('Belt velocity (m/s):'), 10, 0)
        self.velocity_spin = QDoubleSpinBox()
        self.velocity_spin.setRange(0.1, 10.0)
        self.velocity_spin.setValue(2.0)
        self.velocity_spin.setDecimals(2)
        self.velocity_spin.setSingleStep(0.1)
        self.velocity_spin.setToolTip('Used to convert time axis to distance on heatmap')
        layout.addWidget(self.velocity_spin, 10, 1)

        # ── Recording ────────────────────────────────────────────
        self.record_btn = QPushButton('Start Recording')
        self.record_btn.setStyleSheet(f'border-color: {RED};')
        layout.addWidget(self.record_btn, 11, 0, 1, 2)

        self.record_label = QLabel('Not recording')
        self.record_label.setWordWrap(True)
        self.record_label.setStyleSheet('font-size: 10px;')
        layout.addWidget(self.record_label, 12, 0, 1, 2)

        # ── Heatmap baseline recalibration ───────────────────────
        self.calibrate_btn = QPushButton('Calibrate Heatmap Baseline')
        self.calibrate_btn.setStyleSheet(f'border-color: {ACCENT};')
        self.calibrate_btn.setToolTip(
            'Snapshot current per-coil readings as the "no metal" baseline.\n'
            'The heatmap then shows deviation from this baseline.')
        layout.addWidget(self.calibrate_btn, 13, 0, 1, 2)


# ─────────────────────────────────────────────────────────────────
# Top-level window
# ─────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    """
    Top-level application window. Owns the data layer
    (:class:`DataBuffer`, :class:`Recorder`, optional listener and
    dummy threads) and the periodic UI refresh timer.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle('AMCISS — Data Visualisation')
        self.resize(1400, 800)
        self.setStyleSheet(STYLE)

        # ── Data layer ───────────────────────────────────────────
        self._recorder = Recorder(output_dir='recordings')
        self.buffer = DataBuffer(duration_s=60.0, sample_rate_hz=2200.0)
        # Inject the recorder into the buffer so push() can forward to
        # it without DataBuffer needing to know the concrete type.
        self.buffer._recorder = self._recorder
        self._listener: UDPListener | None = None
        self._dummy: DummyGenerator | None = None
        self._recording_elapsed = QElapsedTimer()
        self._recording_active = False

        # ── Central widget ───────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ── Left column: logo + settings panel ───────────────────
        left = QVBoxLayout()

        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 'AMCISS Logo.png')
        logo_label = QLabel()
        logo_pixmap = QPixmap(logo_path)
        logo_label.setPixmap(
            logo_pixmap.scaledToWidth(200, Qt.TransformationMode.SmoothTransformation)
        )
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left.addWidget(logo_label)

        self.settings = SettingsPanel()
        left.addWidget(self.settings)
        left.addStretch()
        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setMaximumWidth(240)

        # ── Right column: LDC selector + tabbed plots ────────────
        right = QVBoxLayout()
        self.ldc_selector = LDCSelectorWidget()
        right.addWidget(self.ldc_selector)

        self.tabs = QTabWidget()
        self.single_ldc = SingleLDCWidget(self.ldc_selector)
        self.heatmap = HeatmapWidget()
        self.tabs.addTab(self.single_ldc, '📈  LDC Traces')
        self.tabs.addTab(self.heatmap, '🌡  Heatmap')
        right.addWidget(self.tabs, stretch=1)

        right_widget = QWidget()
        right_widget.setLayout(right)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(1, 4)
        root.addWidget(splitter)

        # ── Status bar ───────────────────────────────────────────
        self.status_bar = StatusBar()
        self.setStatusBar(QStatusBar())
        self.statusBar().addPermanentWidget(self.status_bar, 1)

        # ── Refresh timer ────────────────────────────────────────
        self.timer = QTimer()
        self.timer.setInterval(REFRESH_MS)
        self.timer.timeout.connect(self._refresh_ui)
        self.timer.start()

        # ── Signal wiring ────────────────────────────────────────
        self.settings.connect_btn.clicked.connect(self._toggle_udp)
        self.settings.dummy_btn.clicked.connect(self._toggle_dummy)
        self.settings.clear_btn.clicked.connect(self._clear_buffer)
        self.settings.buffer_spin.valueChanged.connect(
            lambda v: self.buffer.set_duration(v))
        self.settings.refresh_spin.valueChanged.connect(
            lambda v: self.timer.setInterval(v))
        self.settings.scale_spin.valueChanged.connect(
            self._update_scale_factor)
        self.settings.velocity_spin.valueChanged.connect(
            lambda v: setattr(self.heatmap, 'velocity_ms', v))
        self.settings.record_btn.clicked.connect(self._toggle_recording)
        self.settings.calibrate_btn.clicked.connect(self.heatmap.recalibrate)

    # ─────────────────────────────────────────────────────────────
    # Slots — connection lifecycle
    # ─────────────────────────────────────────────────────────────

    def _toggle_udp(self) -> None:
        """Start or stop the live UDP listener."""
        if self._listener and self._listener.is_alive():
            self._listener.stop()
            self._listener = None
            self.settings.connect_btn.setText('Connect UDP')
            self.settings.connect_btn.setStyleSheet(f'border-color: {GREEN};')
        else:
            host = self.settings.host_edit.text()
            port = self.settings.port_spin.value()
            self._listener = UDPListener(self.buffer, host=host, port=port)
            self._listener.start()
            self.settings.connect_btn.setText('Disconnect UDP')
            self.settings.connect_btn.setStyleSheet(f'border-color: {RED};')

    def _toggle_dummy(self) -> None:
        """Start or stop the synthetic data generator."""
        if self._dummy and self._dummy.is_alive():
            self._dummy.stop()
            self._dummy = None
            self.settings.dummy_btn.setText('Start Dummy Data')
            self.settings.dummy_btn.setStyleSheet(f'border-color: {YELLOW};')
        else:
            self._dummy = DummyGenerator(self.buffer, rate_hz=50.0)
            self._dummy.start()
            self.settings.dummy_btn.setText('Stop Dummy Data')
            self.settings.dummy_btn.setStyleSheet(f'border-color: {RED};')

    def _clear_buffer(self) -> None:
        """Drop all buffered samples and invalidate the heatmap baseline."""
        self.buffer.clear()
        # Stale baselines no longer correspond to incoming data, so
        # force the heatmap to re-snapshot from whatever arrives next.
        self.heatmap.recalibrate()

    # ─────────────────────────────────────────────────────────────
    # Slots — recording
    # ─────────────────────────────────────────────────────────────

    def _toggle_recording(self) -> None:
        """Start or stop a CSV recording."""
        if self._recorder.is_recording:
            self._stop_recording()
        else:
            filename = self._recorder.start()
            self._recording_elapsed.start()
            self._recording_active = True
            self.settings.record_btn.setText('Stop Recording')
            self.settings.record_btn.setStyleSheet(
                f'background: {RED}; color: white; border-color: {RED};')
            self.settings.record_label.setText(f'Recording...\n{filename}')
            self.settings.record_label.setStyleSheet(f'color: {RED}; font-size: 10px;')

    def _stop_recording(self) -> None:
        """Stop the active recording and update the status label."""
        self._recording_active = False
        count = self._recorder.stop()
        self.settings.record_btn.setText('Start Recording')
        self.settings.record_btn.setStyleSheet(f'border-color: {RED};')
        self.settings.record_label.setText(
            f'Saved {count} samples\n{self._recorder.current_filename}')
        self.settings.record_label.setStyleSheet(f'color: {GREEN}; font-size: 10px;')

    # ─────────────────────────────────────────────────────────────
    # Slots — calibration
    # ─────────────────────────────────────────────────────────────

    def _update_scale_factor(self, value: float) -> None:
        """
        Update the µH calibration factor used by :func:`raw_to_uh`.

        The factor lives on the ``packet`` module so every consumer
        (plot, heatmap, recorder) sees the change consistently.
        """
        import packet as pkt
        pkt.SCALE_FACTOR_UH = value

    # ─────────────────────────────────────────────────────────────
    # Refresh loop
    # ─────────────────────────────────────────────────────────────

    @property
    def window_s(self) -> float:
        """Current sliding-window length in seconds (from the settings spin box)."""
        return float(self.settings.window_spin.value())

    def _apply_time_window(self, timestamps: np.ndarray,
                           *arrays: np.ndarray) -> tuple[np.ndarray, ...]:
        """
        Slice ``timestamps`` and each accompanying array to the most
        recent :attr:`window_s` seconds. Returns the trimmed views in
        the same order they were supplied.
        """
        if timestamps.size == 0:
            return (timestamps, *arrays)

        cutoff = timestamps[-1] - self.window_s * 1000.0
        start = int(np.searchsorted(timestamps, cutoff, side='left'))
        return (timestamps[start:], *(array[start:] for array in arrays))

    def _refresh_ui(self) -> None:
        """
        Periodic UI tick: pull the latest snapshot, update counters,
        and redraw both plots. Driven by :attr:`timer`.
        """
        timestamps, l_raw, rp_raw = self.buffer.get_snapshot()

        # ── Recording countdown / auto-stop ──────────────────────
        if self._recording_active and self._recorder.is_recording:
            elapsed_s = self._recording_elapsed.elapsed() / 1000.0
            remaining = max(0.0, self.window_s - elapsed_s)
            self.settings.record_label.setText(
                f'Recording... {self._recorder.sample_count} samples\n'
                f'{remaining:.1f}s remaining')
            if elapsed_s >= self.window_s:
                self._stop_recording()

        # ── Connection/packet stats ──────────────────────────────
        connected = (self._listener is not None and self._listener.is_alive()) or \
                    (self._dummy is not None and self._dummy.is_alive())
        packets = self._listener.packets_received if self._listener else 0
        if self._dummy:
            # Prefer the dummy's seq counter when it's running so the
            # status bar reflects synthesised traffic too.
            packets = self._dummy._seq
        dropped = self.buffer.dropped_packets

        self.status_bar.update_stats(connected, packets, dropped, self.buffer.sample_count)

        if l_raw.size == 0:
            return

        timestamps, l_raw, rp_raw = self._apply_time_window(timestamps, l_raw, rp_raw)

        # ── Pick which dataset feeds the plots (L vs RP) ─────────
        view_rp = self.settings.view_combo.currentIndex() == 1
        if view_rp:
            readings = rp_raw.astype(np.float32)
            y_label = 'RP (raw)'
            cbar_label = 'RP (raw)'
            view_key = 'RP'
        else:
            readings = raw_to_uh(l_raw)
            y_label = 'Inductance (µH)'
            cbar_label = 'Inductance (µH)'
            view_key = 'L'

        # ── Redraw ───────────────────────────────────────────────
        try:
            self.single_ldc.plot_widget.setLabel('left', y_label)
            self.single_ldc.refresh_plot(timestamps, readings)
            self.heatmap.refresh_plot(timestamps, readings, cbar_label, view_key)
        except Exception as e:
            # Surface refresh errors in both the console and the title
            # bar — easier to spot during demos than a silent failure.
            msg = f'[UI] refresh error: {e}'
            print(msg)
            traceback.print_exc()
            self.setWindowTitle(f'AMCISS — ERROR: {e}')

    # ─────────────────────────────────────────────────────────────
    # Window lifecycle
    # ─────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        """Tear down background threads and flush any active recording."""
        if self._recorder.is_recording:
            self._recorder.stop()
        if self._listener:
            self._listener.stop()
        if self._dummy:
            self._dummy.stop()
        super().closeEvent(event)


def main() -> None:
    """Entry point — instantiate the Qt app and show the main window."""
    app = QApplication(sys.argv)
    app.setApplicationName('AMCISS GUI')
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
