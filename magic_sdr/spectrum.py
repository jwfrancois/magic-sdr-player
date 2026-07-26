"""UDP spectrum receiver + waterfall widget for Gqrx's spectrum stream.

Gqrx 2.14+ can stream FFT/spectrum data over UDP. Configure in Gqrx via:
  Tools → Remote control settings → Spectrum UDP stream → enable
  UDP host: 127.0.0.1, UDP port: 7357
  Format: raw float32 complex (or magnitude, depending on Gqrx version)

Packet format (Gqrx 2.15+):
  The spectrum is sent as raw IEEE-754 float32 little-endian magnitude values
  (dBFS) with no header. Each packet contains the full FFT (typically 256–2048
  bins). A new packet is sent ~20 times per second.

We also support computing a fallback "audio spectrum" from the audio stream —
useful for older Gqrx versions or when the user hasn't enabled the spectrum
server. The fallback gives a 0–24 kHz spectrum (audio band) instead of the
full RF band, but it still makes for a nice visualization.
"""

from __future__ import annotations

import socket
import threading
import logging
import time
import struct
from typing import Optional, List

import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal, Qt, QTimer
from PyQt5.QtGui import QColor, QImage, QPainter
from PyQt5.QtWidgets import QWidget, QVBoxLayout
import pyqtgraph as pg

log = logging.getLogger(__name__)


class SpectrumReceiver(QObject):
    """Receives Gqrx's UDP spectrum stream.

    Emits `spectrum_ready(np.ndarray, center_hz, span_hz)` where ndarray is a
    1-D float32 array of dBFS magnitudes.
    """

    spectrum_ready = pyqtSignal(object, int, int)  # data, center_hz, span_hz

    def __init__(self, port: int = 7357, parent=None):
        super().__init__(parent)
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._running = False
        # Default band context (updated by main window when freq changes)
        self.center_hz: int = 96_900_000
        self.span_hz: int = 2_000_000  # Gqrx default FFT width ~2 MHz
        # UDP health tracking — used by UI to detect if Gqrx is streaming spectrum.
        self._packet_count: int = 0
        self._last_packet_time: float = 0.0

    def start(self) -> bool:
        if self._running:
            return True
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
            self._sock.bind(("0.0.0.0", self.port))
            self._sock.settimeout(0.5)
            self._stop.clear()
            self._running = True
            self._packet_count = 0
            self._last_packet_time = 0.0
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name=f"SpectrumRecv:{self.port}")
            self._thread.start()
            log.info("SpectrumReceiver listening on UDP %d", self.port)
            return True
        except Exception as e:
            log.error("Failed to start SpectrumReceiver on port %d: %s", self.port, e)
            self._running = False
            return False

    def stop(self) -> None:
        self._stop.set()
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

    def is_running(self) -> bool:
        return self._running

    # ---- UDP health-check API ----
    def packet_count(self) -> int:
        return self._packet_count

    def last_packet_age_s(self):
        if self._last_packet_time == 0.0:
            return None
        return time.time() - self._last_packet_time

    def is_streaming(self, max_age_s: float = 2.0) -> bool:
        if self._last_packet_time == 0.0:
            return False
        return (time.time() - self._last_packet_time) <= max_age_s

    def set_band_context(self, center_hz: int, span_hz: int) -> None:
        """Inform the receiver of the current RF center/span so we can label
        the spectrum correctly. Gqrx's spectrum stream itself does not include
        this metadata; we infer it from the current frequency + sample rate.
        """
        self.center_hz = center_hz
        self.span_hz = span_hz

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, _addr = self._sock.recvfrom(32768)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                continue
            # Track packet arrival
            self._packet_count += 1
            self._last_packet_time = time.time()
            # Gqrx spectrum stream = float32 little-endian magnitude (dBFS)
            n = len(data) // 4
            if n == 0:
                continue
            try:
                arr = np.frombuffer(data[:n * 4], dtype="<f4").astype(np.float32)
            except Exception as e:
                log.debug("Failed to parse spectrum packet: %s", e)
                continue
            # Some versions send interleaved I/Q float32 — if so, take magnitude.
            if n % 2 == 0 and np.any(arr[:n // 2] > 100):  # not dBFS, looks like raw
                # Treat as complex I/Q: convert to magnitude dBFS
                iq = arr[:n].view(np.complex64) if n % 2 == 0 else None
                if iq is not None:
                    arr = 20.0 * np.log10(np.abs(iq) + 1e-12).astype(np.float32)
            self.spectrum_ready.emit(arr, self.center_hz, self.span_hz)


class WaterfallWidget(QWidget):
    """Combined spectrum + waterfall display.

    Top: live spectrum (line plot of dBFS vs frequency).
    Bottom: scrolling waterfall (time on Y-axis, frequency on X-axis, color = level).

    Click on either plot to tune to that frequency.
    """

    tune_requested = pyqtSignal(int)  # freq_hz

    def __init__(self, parent=None):
        super().__init__(parent)
        self.center_hz: int = 96_900_000
        self.span_hz: int = 2_000_000
        self.waterfall_history_lines = 256
        self.waterfall_height = 256
        self.waterfall_width = 1024
        # image data: rows = time (top = newest), cols = freq
        self._img_data = np.zeros((self.waterfall_height, self.waterfall_width),
                                  dtype=np.uint8)
        # Colormap (gray-black to yellow to red to white, "turbo"-ish)
        self._colormap = self._make_colormap()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        pg.setConfigOption("background", "#0b0f14")
        pg.setConfigOption("foreground", "#d8dde3")

        # Spectrum plot
        self.spectrum_plot = pg.PlotWidget()
        self.spectrum_plot.setMouseEnabled(False, False)
        self.spectrum_plot.hideButtons()
        self.spectrum_plot.showGrid(x=False, y=True, alpha=0.3)
        self.spectrum_plot.setLabel("bottom", "Frequency", units="Hz")
        self.spectrum_plot.setLabel("left", "Level", units="dBFS")
        self.spectrum_curve = self.spectrum_plot.plot(pen=pg.mkPen("#5cd9ff", width=1.5))
        # Marker for current tuned frequency
        self.tune_marker = pg.InfiniteLine(angle=90, pen=pg.mkPen("#ff5c5c", width=1.5))
        self.spectrum_plot.addItem(self.tune_marker)
        layout.addWidget(self.spectrum_plot, stretch=1)

        # Waterfall — using an ImageItem inside a PlotWidget so we can share
        # the same X-axis as the spectrum.
        self.waterfall_plot = pg.PlotWidget()
        self.waterfall_plot.setMouseEnabled(False, False)
        self.waterfall_plot.hideButtons()
        self.waterfall_plot.setLabel("left", "Time", units="")
        self.waterfall_plot.setLabel("bottom", "Frequency", units="Hz")
        # Build an ImageItem; we'll resize data to (waterfall_width,) per row
        self.waterfall_image = pg.ImageItem()
        self.waterfall_image.setLookupTable(self._colormap)
        # Default color range (dBFS)
        self.waterfall_image.setLevels([-80.0, -10.0])
        self.waterfall_plot.addItem(self.waterfall_image)
        # Set the Y-axis to show "now" at top, history going down
        self.waterfall_plot.setYRange(0, self.waterfall_height, padding=0)
        self.waterfall_plot.setXRange(self.center_hz - self.span_hz / 2,
                                       self.center_hz + self.span_hz / 2,
                                       padding=0)
        self.waterfall_plot.getAxis("left").setTicks([])
        self.waterfall_marker = pg.InfiniteLine(angle=90, pen=pg.mkPen("#ff5c5c", width=1.5))
        self.waterfall_plot.addItem(self.waterfall_marker)
        layout.addWidget(self.waterfall_plot, stretch=2)

        # Click-to-tune on either plot
        self.spectrum_plot.scene().sigMouseClicked.connect(self._on_click)
        self.waterfall_plot.scene().sigMouseClicked.connect(self._on_click)

    def _make_colormap(self) -> np.ndarray:
        """Turbo-ish colormap as Nx4 uint8 RGBA."""
        try:
            from matplotlib.pyplot import cm
            cmap = cm.get_cmap("turbo")
            return (cmap(np.linspace(0, 1, 256)) * 255).astype(np.ubyte)
        except Exception:
            # Fallback: simple grayscale→amber
            cmap = np.zeros((256, 4), dtype=np.ubyte)
            for i in range(256):
                t = i / 255.0
                cmap[i] = [int(20 + 235 * t),
                           int(10 + 60 * t),
                           int(40 - 30 * t),
                           255]
            return cmap

    def set_band_context(self, center_hz: int, span_hz: int) -> None:
        self.center_hz = int(center_hz)
        self.span_hz = int(span_hz)
        self.spectrum_plot.setXRange(self.center_hz - self.span_hz / 2,
                                      self.center_hz + self.span_hz / 2, padding=0)
        self.waterfall_plot.setXRange(self.center_hz - self.span_hz / 2,
                                       self.center_hz + self.span_hz / 2, padding=0)
        self.tune_marker.setPos(self.center_hz)
        self.waterfall_marker.setPos(self.center_hz)

    def update_spectrum(self, data: np.ndarray, center_hz: int, span_hz: int) -> None:
        """Update spectrum + waterfall with a new FFT magnitude array (dBFS)."""
        if len(data) == 0:
            return
        if center_hz != self.center_hz or span_hz != self.span_hz:
            self.set_band_context(center_hz, span_hz)
        # X-axis: each bin maps to a frequency
        n = len(data)
        f_start = center_hz - span_hz / 2
        f_end = center_hz + span_hz / 2
        xs = np.linspace(f_start, f_end, n)
        self.spectrum_curve.setData(xs, data)
        # Update waterfall: resample to waterfall_width columns, shift down, add row at top
        col_data = np.interp(np.linspace(0, n - 1, self.waterfall_width),
                             np.arange(n), data)
        # Shift down
        self._img_data[1:] = self._img_data[:-1]
        # Map dBFS to 0..255
        v = np.clip((col_data + 80) / 70, 0, 1)
        self._img_data[0] = (v * 255).astype(np.uint8)
        # Set image — coordinates are (x=left,right, y=bottom,top)
        self.waterfall_image.setImage(self._img_data,
                                       levels=[0, 255],
                                       autoLevels=False)
        # Position the image so x = frequency, y = time (0..waterfall_height)
        self.waterfall_image.setRect(f_start, 0, span_hz, self.waterfall_height)

    def set_tune_marker(self, freq_hz: int) -> None:
        self.tune_marker.setPos(freq_hz)
        self.waterfall_marker.setPos(freq_hz)

    def _on_click(self, event) -> None:
        """Click-to-tune: convert mouse X to a frequency."""
        # Determine which plot was clicked
        vb = event.currentItem
        if vb is None:
            return
        # Walk up to find the ViewBox
        from pyqtgraph import ViewBox
        item = event.currentItem
        while item is not None and not isinstance(item, ViewBox):
            item = getattr(item, "parentItem", lambda: None)()
        if item is None:
            # Try plot items
            for plot in (self.spectrum_plot, self.waterfall_plot):
                try:
                    pos = plot.plotItem.vb.mapSceneToView(event.scenePos())
                    f = int(pos.x())
                    if self.center_hz - self.span_hz / 2 <= f <= self.center_hz + self.span_hz / 2:
                        self.tune_requested.emit(f)
                        return
                except Exception:
                    continue
            return
        try:
            pos = item.mapSceneToView(event.scenePos())
            f = int(pos.x())
            if self.center_hz - self.span_hz / 2 <= f <= self.center_hz + self.span_hz / 2:
                self.tune_requested.emit(f)
        except Exception as e:
            log.debug("click-to-tune failed: %s", e)
