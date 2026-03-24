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
import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSpinBox, QDoubleSpinBox, QComboBox,
    QGroupBox, QCheckBox, QLineEdit, QSlider, QSplitter,
    QStatusBar, QTabWidget, QGridLayout, QListWidget, QListWidgetItem
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor

from buffer import DataBuffer
from udp_listener import UDPListener, DummyGenerator

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
    """LDC index (Y) vs time/distance (X) heatmap."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot_widget = pg.PlotWidget(title='Heatmap — LDC vs Time')
        self.plot_widget.setLabel('left', 'LDC Index')
        self.plot_widget.setLabel('bottom', 'Time (s)')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.2)

        self.img = pg.ImageItem()
        self.plot_widget.addItem(self.img)

        # Colour map: low = dark blue, high = bright yellow/white
        cm = pg.colormap.get('CET-L9')
        self.bar = pg.ColorBarItem(values=(0, 15), colorMap=cm, label='Inductance (µH)')
        self.bar.setImageItem(self.img, insert_in=self.plot_widget.getPlotItem())

        layout.addWidget(self.plot_widget)

    def update(self, timestamps_ms: np.ndarray, readings: np.ndarray):
        if readings.size == 0:
            return
        # readings shape: [N, 64], we want [64, N] for image (x=time, y=ldc)
        img_data = readings.T.astype(np.float32)
        t0 = timestamps_ms[0] / 1000.0
        t1 = timestamps_ms[-1] / 1000.0
        dt = (t1 - t0) / max(img_data.shape[1] - 1, 1)
        self.img.setImage(img_data, autoLevels=False)
        self.img.setRect(pg.QtCore.QRectF(t0, 0, t1 - t0 + dt, NUM_LDCS))
        vb = self.plot_widget.getViewBox()
        vb.setRange(xRange=(t0, t1), yRange=(0, NUM_LDCS), padding=0)


class SingleLDCWidget(QWidget):
    """Live trace for one or more selected LDCs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Controls row
        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel('LDC:'))
        self.ldc_spin = QSpinBox()
        self.ldc_spin.setRange(0, NUM_LDCS - 1)
        self.ldc_spin.setValue(0)
        ctrl.addWidget(self.ldc_spin)
        ctrl.addWidget(QLabel('  Multi-select:'))
        self.multi_list = QListWidget()
        self.multi_list.setMaximumHeight(70)
        self.multi_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        for i in range(NUM_LDCS):
            self.multi_list.addItem(QListWidgetItem(f'LDC {i}'))
        ctrl.addWidget(self.multi_list, stretch=1)
        layout.addLayout(ctrl)

        # Plot
        self.plot_widget = pg.PlotWidget(title='LDC Inductance vs Time')
        self.plot_widget.setLabel('left', 'Inductance (µH)')
        self.plot_widget.setLabel('bottom', 'Time (s)')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.addLegend()
        layout.addWidget(self.plot_widget)

        self._curves = {}
        self._colours = [ACCENT, GREEN, RED, YELLOW, '#cba6f7', '#fab387', '#94e2d5']

    def update(self, timestamps_ms: np.ndarray, readings: np.ndarray):
        if readings.size == 0:
            return
        t = timestamps_ms / 1000.0

        # Selected from multi-list
        selected = [i.row() for i in self.multi_list.selectedIndexes()]
        # Always include the spinner LDC
        primary = self.ldc_spin.value()
        indices = list(dict.fromkeys([primary] + selected))  # deduplicated, primary first

        # Remove curves no longer needed
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

        self.dummy_btn = QPushButton('Start Dummy Data')
        self.dummy_btn.setStyleSheet(f'border-color: {YELLOW};')
        layout.addWidget(self.dummy_btn, 4, 0, 1, 2)

        self.connect_btn = QPushButton('Connect UDP')
        self.connect_btn.setStyleSheet(f'border-color: {GREEN};')
        layout.addWidget(self.connect_btn, 5, 0, 1, 2)

        self.clear_btn = QPushButton('Clear Buffer')
        layout.addWidget(self.clear_btn, 6, 0, 1, 2)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('AMCISS — Data Visualisation')
        self.resize(1400, 800)
        self.setStyleSheet(STYLE)

        # Data layer
        self.buffer = DataBuffer(duration_s=60.0)
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

    def _refresh_ui(self):
        timestamps, readings = self.buffer.get_snapshot()
        connected = (self._listener is not None and self._listener.is_alive()) or \
                    (self._dummy is not None and self._dummy.is_alive())
        packets = self._listener.packets_received if self._listener else 0
        if self._dummy:
            packets = self._dummy._seq
        dropped = self.buffer.dropped_packets

        self.status_bar.update_stats(connected, packets, dropped, self.buffer.sample_count)

        if readings.size == 0:
            return

        current_tab = self.tabs.currentIndex()
        if current_tab == 0:
            self.single_ldc.update(timestamps, readings)
        elif current_tab == 1:
            self.heatmap.update(timestamps, readings)

    def closeEvent(self, event):
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
