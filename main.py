"""
AMCISS GUI - Main Application
==============================
Adaptive Multichannel Induction Sorting System
Data Visualisation Interface

Layout:
  - Top bar: connection status, stats, settings
  - Left panel: Single LDC monitor + multi-LDC overlay
  - Right panel: Heatmap (LDC index vs time/distance)
  - Bottom bar: controls
"""

import sys
import traceback
import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSpinBox, QDoubleSpinBox, QComboBox,
    QGroupBox, QLineEdit, QSplitter, QStatusBar, QTabWidget,
    QGridLayout, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor

from buffer import DataBuffer
from udp_listener import UDPListener, DummyGenerator
from packet import raw_to_uh, SCALE_FACTOR_UH
from recorder import Recorder

# ── Colour scheme ──────────────────────────────────────────────
pg.setConfigOption('background', '#1e1e2e')
pg.setConfigOption('foreground', '#cdd6f4')

ACCENT   = '#89b4fa'  # blue
GREEN    = '#a6e3a1'
RED      = '#f38ba8'
YELLOW   = '#f9e2af'
SURFACE  = '#313244'
BASE     = '#1e1e2e'
TEXT     = '#cdd6f4'

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
QListWidget {{ background: {SURFACE}; border: 1px solid #45475a; border-radius: 4px; }}
QTabWidget::pane {{ border: 1px solid {SURFACE}; }}
QTabBar::tab {{ background: {SURFACE}; padding: 6px 14px; border-radius: 4px 4px 0 0; }}
QTabBar::tab:selected {{ background: {ACCENT}; color: {BASE}; }}
QStatusBar {{ background: {SURFACE}; color: {TEXT}; }}
"""

NUM_LDCS = 64
REFRESH_MS = 100  # UI refresh interval (ms)


class HeatmapWidget(QWidget):
    """
    Heatmap: X = LDC index (position across belt, 0..63)
             Y = distance (m), derived from elapsed time x belt velocity
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.velocity_ms = 2.0  # m/s default, updated from settings panel
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot_widget = pg.PlotWidget(title='Heatmap — LDC Position vs Distance')
        self.plot_widget.setLabel('bottom', 'LDC Index (position across belt)')
        self.plot_widget.setLabel('left', 'Distance (m)')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.2)

        self.img = pg.ImageItem()
        self.plot_widget.addItem(self.img)

        # Colour map: low = dark blue, high = bright yellow/white
        cm = pg.colormap.get('CET-L9')
        self.bar = pg.ColorBarItem(values=(0, 50), colorMap=cm, label='Inductance (µH)')
        self.bar.setImageItem(self.img, insert_in=self.plot_widget.getPlotItem())
        self._levels = (0.0, 50.0)

        layout.addWidget(self.plot_widget)

    def refresh_plot(self, timestamps_ms: np.ndarray, readings: np.ndarray):
        if readings.size == 0:
            return
        # readings shape: [N, 64]
        # Image axes: x = LDC index (0..63), y = distance (m)
        # ImageItem expects data[x, y] so transpose: [64, N]
        img_data = readings.T.astype(np.float32)

        # Convert time span to distance
        t0 = timestamps_ms[0] / 1000.0
        t1 = timestamps_ms[-1] / 1000.0
        total_distance = (t1 - t0) * self.velocity_ms  # metres

        # Compute levels from data so the heatmap is always visible
        lo = float(np.percentile(img_data, 2))
        hi = float(np.percentile(img_data, 98))
        if hi <= lo:
            hi = lo + 1.0
        self._levels = (lo, hi)
        self.img.setImage(img_data, autoLevels=False, levels=self._levels)
        self.bar.setLevels((lo, hi))
        # rect(x, y, width, height): x=LDC axis, y=distance axis
        self.img.setRect(pg.QtCore.QRectF(0, 0, NUM_LDCS, max(total_distance, 0.01)))
        vb = self.plot_widget.getViewBox()
        vb.setRange(xRange=(0, NUM_LDCS), yRange=(0, max(total_distance, 0.1)), padding=0)


class LDCGridWidget(QWidget):
    """
    Visual PCB layout — two DCMs side by side, each 2 rows × 16 LDCs.
      DCM0: LDCs  0-15 (top row),  16-31 (bottom row)
      DCM1: LDCs 32-47 (top row),  48-63 (bottom row)
    Click to toggle. Range entry for bulk selection (0-based, e.g. '0-11, 32, 50-55').
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected: set[int] = set()
        self._buttons: list[QPushButton] = [None] * NUM_LDCS

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 2)
        outer.setSpacing(3)

        # ── Two DCM blocks side by side ──────────────────────────
        grids_row = QHBoxLayout()
        grids_row.setSpacing(0)

        for dcm in range(2):
            base = dcm * 32
            block = QVBoxLayout()
            block.setSpacing(2)

            lbl = QLabel(f'DCM{dcm}')
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f'color: {ACCENT}; font-weight: bold; font-size: 10px; padding: 1px;')
            block.addWidget(lbl)

            g = QGridLayout()
            g.setSpacing(2)
            g.setContentsMargins(4, 0, 4, 0)

            for i in range(32):
                ldc_idx = base + i
                row = i // 16
                col = i % 16
                btn = QPushButton(str(ldc_idx))
                btn.setMinimumSize(0, 20)
                btn.setMaximumHeight(24)
                btn.setFont(QFont('Segoe UI', 6))
                btn.setSizePolicy(
                    QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                btn.clicked.connect(
                    lambda checked, idx=ldc_idx: self._toggle(idx))
                g.addWidget(btn, row, col)
                self._buttons[ldc_idx] = btn

            block.addLayout(g)
            grids_row.addLayout(block, stretch=1)

            if dcm == 0:
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.VLine)
                sep.setFixedWidth(2)
                sep.setStyleSheet(f'background: {SURFACE};')
                grids_row.addWidget(sep)

        outer.addLayout(grids_row)

        # ── Range input row ──────────────────────────────────────
        range_row = QHBoxLayout()
        range_row.setSpacing(4)
        range_row.addWidget(QLabel('Select:'))
        self._range_edit = QLineEdit()
        self._range_edit.setPlaceholderText('e.g. 0-11, 32, 50-55')
        self._range_edit.setFixedHeight(24)
        self._range_edit.returnPressed.connect(self._apply_range)
        range_row.addWidget(self._range_edit, stretch=1)

        add_btn = QPushButton('Add')
        add_btn.setFixedWidth(50)
        add_btn.clicked.connect(self._apply_range)
        range_row.addWidget(add_btn)

        clear_btn = QPushButton('Clear')
        clear_btn.setFixedWidth(50)
        clear_btn.clicked.connect(self._clear_all)
        range_row.addWidget(clear_btn)

        outer.addLayout(range_row)
        self.selected.add(0)
        self._update_styles()

    def _toggle(self, idx: int):
        if idx in self.selected:
            self.selected.discard(idx)
        else:
            self.selected.add(idx)
        self._update_styles()

    def _apply_range(self):
        text = self._range_edit.text().strip()
        for part in text.split(','):
            part = part.strip()
            if not part:
                continue
            if '-' in part:
                halves = part.split('-', 1)
                try:
                    a, b = int(halves[0]), int(halves[1])
                    for i in range(min(a, b), max(a, b) + 1):
                        if 0 <= i < NUM_LDCS:
                            self.selected.add(i)
                except ValueError:
                    pass
            else:
                try:
                    i = int(part)
                    if 0 <= i < NUM_LDCS:
                        self.selected.add(i)
                except ValueError:
                    pass
        self._range_edit.clear()
        self._update_styles()

    def _clear_all(self):
        self.selected.clear()
        self._range_edit.clear()
        self._update_styles()

    def _update_styles(self):
        for i, btn in enumerate(self._buttons):
            if btn is None:
                continue
            if i in self.selected:
                btn.setStyleSheet(
                    f'background: {ACCENT}; color: {BASE};'
                    f' border: none; border-radius: 2px;')
            else:
                btn.setStyleSheet(
                    f'background: {SURFACE}; color: {TEXT};'
                    f' border: none; border-radius: 2px;')


class SingleLDCWidget(QWidget):
    """Live trace for LDCs selected via the grid."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.grid = LDCGridWidget()
        layout.addWidget(self.grid)

        self.plot_widget = pg.PlotWidget(title='LDC Inductance vs Time')
        self.plot_widget.setLabel('left', 'Inductance (µH)')
        self.plot_widget.setLabel('bottom', 'Time (s)')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.addLegend()
        layout.addWidget(self.plot_widget)

        self._curves = {}
        self._colours = [ACCENT, GREEN, RED, YELLOW, '#cba6f7', '#fab387', '#94e2d5']

    def refresh_plot(self, timestamps_ms: np.ndarray, readings: np.ndarray):
        if readings.size == 0:
            return
        t = timestamps_ms / 1000.0
        indices = sorted(self.grid.selected)

        for key in list(self._curves.keys()):
            if key not in indices:
                self.plot_widget.removeItem(self._curves.pop(key))

        for idx, ldc_i in enumerate(indices):
            colour = self._colours[idx % len(self._colours)]
            if ldc_i not in self._curves:
                pen = pg.mkPen(color=colour, width=1.5)
                self._curves[ldc_i] = self.plot_widget.plot(
                    pen=pen, name=f'LDC {ldc_i}')
            self._curves[ldc_i].setData(t, readings[:, ldc_i])


class StatusBar(QWidget):
    def __init__(self, parent=None):
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

    def update_stats(self, connected: bool, packets: int, dropped: int, samples: int):
        if connected:
            self.status_dot.setStyleSheet(f'color: {GREEN}; font-size: 14px;')
            self.status_label.setText('Connected')
        else:
            self.status_dot.setStyleSheet(f'color: {RED}; font-size: 14px;')
            self.status_label.setText('Disconnected')
        self.packets_label.setText(f'Packets: {packets}')
        self.dropped_label.setText(f'Dropped: {dropped}')
        self.samples_label.setText(f'Buffer: {samples} samples')


class SettingsPanel(QGroupBox):
    def __init__(self, parent=None):
        super().__init__('Settings', parent)
        layout = QGridLayout(self)

        layout.addWidget(QLabel('UDP Port:'), 0, 0)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(5005)
        layout.addWidget(self.port_spin, 0, 1)

        layout.addWidget(QLabel('Host IP:'), 1, 0)
        self.host_edit = QLineEdit('0.0.0.0')
        layout.addWidget(self.host_edit, 1, 1)

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

        layout.addWidget(QLabel('View:'), 4, 0)
        self.view_combo = QComboBox()
        self.view_combo.addItems(['L — Inductance (µH)', 'RP — Resistance (raw)'])
        layout.addWidget(self.view_combo, 4, 1)

        self.dummy_btn = QPushButton('Start Dummy Data')
        self.dummy_btn.setStyleSheet(f'border-color: {YELLOW};')
        layout.addWidget(self.dummy_btn, 5, 0, 1, 2)

        self.connect_btn = QPushButton('Connect UDP')
        self.connect_btn.setStyleSheet(f'border-color: {GREEN};')
        layout.addWidget(self.connect_btn, 6, 0, 1, 2)

        self.clear_btn = QPushButton('Clear Buffer')
        layout.addWidget(self.clear_btn, 7, 0, 1, 2)

        self.record_btn = QPushButton('⏺  Start Recording')
        self.record_btn.setStyleSheet(f'border-color: {RED};')
        layout.addWidget(self.record_btn, 10, 0, 1, 2)

        self.record_label = QLabel('Not recording')
        self.record_label.setStyleSheet(f'color: #6c7086; font-size: 10px;')
        self.record_label.setWordWrap(True)
        layout.addWidget(self.record_label, 11, 0, 1, 2)

        layout.addWidget(QLabel('Scale factor (µH):'), 8, 0)
        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.1, 10000.0)
        self.scale_spin.setValue(SCALE_FACTOR_UH)
        self.scale_spin.setDecimals(1)
        self.scale_spin.setSingleStep(1.0)
        self.scale_spin.setToolTip('Calibration: raw 65535 = this value in µH')
        layout.addWidget(self.scale_spin, 8, 1)

        layout.addWidget(QLabel('Belt velocity (m/s):'), 9, 0)
        self.velocity_spin = QDoubleSpinBox()
        self.velocity_spin.setRange(0.1, 10.0)
        self.velocity_spin.setValue(2.0)
        self.velocity_spin.setDecimals(2)
        self.velocity_spin.setSingleStep(0.1)
        self.velocity_spin.setToolTip('Used to convert time axis to distance on heatmap')
        layout.addWidget(self.velocity_spin, 9, 1)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('AMCISS — Data Visualisation')
        self.resize(1400, 800)
        self.setStyleSheet(STYLE)

        # Data layer
        self._recorder = Recorder(output_dir='recordings')
        self.buffer = DataBuffer(duration_s=60.0)
        self.buffer._recorder = self._recorder  # inject recorder into buffer
        self._listener = None
        self._dummy = None

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ── Left: settings + single/multi LDC ──
        left = QVBoxLayout()
        self.settings = SettingsPanel()
        left.addWidget(self.settings)
        left.addStretch()
        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setMaximumWidth(240)

        # ── Right: tabs (single LDC | heatmap) ──
        self.tabs = QTabWidget()
        self.single_ldc = SingleLDCWidget()
        self.heatmap = HeatmapWidget()
        self.tabs.addTab(self.single_ldc, '📈  LDC Traces')
        self.tabs.addTab(self.heatmap, '🌡  Heatmap')

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_widget)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(1, 4)
        root.addWidget(splitter)

        # ── Status bar ──
        self.status_bar = StatusBar()
        self.setStatusBar(QStatusBar())
        self.statusBar().addPermanentWidget(self.status_bar, 1)

        # ── Refresh timer ──
        self.timer = QTimer()
        self.timer.setInterval(REFRESH_MS)
        self.timer.timeout.connect(self._refresh_ui)
        self.timer.start()

        # ── Connect signals ──
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

    # ── Slots ──────────────────────────────────────────────────

    def _toggle_udp(self):
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

    def _toggle_dummy(self):
        if self._dummy and self._dummy.is_alive():
            self._dummy.stop()
            self._dummy = None
            self.settings.dummy_btn.setText('Start Dummy Data')
            self.settings.dummy_btn.setStyleSheet(f'border-color: {YELLOW};')
        else:
            self._dummy = DummyGenerator(self.buffer, rate_hz=20.0)
            self._dummy.start()
            self.settings.dummy_btn.setText('Stop Dummy Data')
            self.settings.dummy_btn.setStyleSheet(f'border-color: {RED};')

    def _clear_buffer(self):
        self.buffer.clear()

    def _toggle_recording(self):
        if self._recorder.is_recording:
            count = self._recorder.stop()
            self.settings.record_btn.setText('⏺  Start Recording')
            self.settings.record_btn.setStyleSheet(f'border-color: {RED};')
            self.settings.record_label.setText(f'Saved {count} samples\n{self._recorder.current_filename}')
            self.settings.record_label.setStyleSheet(f'color: {GREEN}; font-size: 10px;')
        else:
            filename = self._recorder.start()
            self.settings.record_btn.setText('⏹  Stop Recording')
            self.settings.record_btn.setStyleSheet(f'background: {RED}; color: white; border-color: {RED};')
            self.settings.record_label.setText(f'Recording...\n{filename}')
            self.settings.record_label.setStyleSheet(f'color: {RED}; font-size: 10px;')

    def _update_scale_factor(self, value: float):
        import packet as pkt
        pkt.SCALE_FACTOR_UH = value

    def _refresh_ui(self):
        timestamps, l_raw, rp_raw = self.buffer.get_snapshot()

        # Update recording sample count label
        if self._recorder.is_recording:
            self.settings.record_label.setText(
                f'Recording... {self._recorder.sample_count} samples\n{self._recorder.current_filename}')
        connected = (self._listener is not None and self._listener.is_alive()) or \
                    (self._dummy is not None and self._dummy.is_alive())
        packets = self._listener.packets_received if self._listener else 0
        if self._dummy:
            packets = self._dummy._seq
        dropped = self.buffer.dropped_packets

        self.status_bar.update_stats(connected, packets, dropped, self.buffer.sample_count)

        if l_raw.size == 0:
            return

        # Select dataset based on view toggle
        view_rp = self.settings.view_combo.currentIndex() == 1
        if view_rp:
            readings = rp_raw.astype(np.float32)
            y_label = 'RP (raw)'
            cbar_label = 'RP (raw)'
        else:
            readings = raw_to_uh(l_raw)
            y_label = 'Inductance (µH)'
            cbar_label = 'Inductance (µH)'

        current_tab = self.tabs.currentIndex()
        try:
            self.single_ldc.plot_widget.setLabel('left', y_label)
            if current_tab == 0:
                self.single_ldc.refresh_plot(timestamps, readings)
            elif current_tab == 1:
                self.heatmap.refresh_plot(timestamps, readings)
        except Exception as e:
            msg = f'[UI] refresh error: {e}'
            print(msg)
            traceback.print_exc()
            self.setWindowTitle(f'AMCISS — ERROR: {e}')

    def closeEvent(self, event):
        if self._recorder.is_recording:
            self._recorder.stop()
        if self._listener:
            self._listener.stop()
        if self._dummy:
            self._dummy.stop()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('AMCISS GUI')
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
