"""Spectrum source + waterfall widget for Magic SDR.

There are TWO sources of spectrum data:

1. **UDP spectrum stream** (SpectrumReceiver) — listens on UDP port 7357 for
   incoming FFT magnitude data. NOTE: stock Gqrx does NOT support this; it
   only exists in some patched forks. We keep the receiver for compatibility,
   but most users will not have this.

2. **Audio FFT fallback** (AudioSpectrumSource) — computes a real-time FFT
   from Gqrx's audio UDP stream (port 7355, available in all Gqrx versions
   via Tools → Audio UDP). The result is an audio-band spectrum (0–24 kHz
   for 48 kHz sample rate) centered on the tuned RF frequency. This is what
   most users will see.

The WaterfallWidget accepts spectrum data from either source via
`update_spectrum(data, center_hz, span_hz)`.

Audio FFT waterfall caveats:
  - The X axis represents audio frequencies (0 to sample_rate/2), NOT the
    full RF band. We label the axis as RF frequencies (centered on the tuned
    frequency ± sample_rate/4) for visual continuity, but the actual content
    is the demodulated audio.
  - For AM/FM/WFM signals, you'll see the carrier, sidebands, and audio
    content as peaks.
  - For SSB, you'll see the voice audio spectrum.
  - This is NOT a substitute for an RF spectrum analyzer — it's a
    visualization of what the demodulator is hearing.
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


class AudioSpectrumSource(QObject):
    """Computes a real-time spectrum from demodulated audio chunks.

    This is the DEFAULT spectrum source for Magic SDR, because stock Gqrx
    does not stream RF spectrum data over UDP. We compute an FFT of the
    incoming audio (already demodulated by Gqrx) and present it as a
    "spectrum" centered on the tuned RF frequency.

    The result is an audio-band FFT (0 to sample_rate/2, typically 0–24 kHz).
    On the waterfall, we map this onto an RF frequency range centered on the
    tuned frequency with a span of sample_rate/2. This isn't physically
    accurate (the audio band isn't an RF band), but it gives the user a useful
    visualization of what the demodulator is producing.

    Emits `spectrum_ready(np.ndarray, center_hz, span_hz)` — same signature
    as SpectrumReceiver, so the WaterfallWidget doesn't care which one is
    feeding it.
    """

    spectrum_ready = pyqtSignal(object, int, int)  # data, center_hz, span_hz

    def __init__(self, parent=None):
        super().__init__(parent)
        self.center_hz: int = 96_900_000  # updated when freq changes
        self.span_hz: int = 24_000        # default: 48 kHz sample rate / 2
        self._fft_size: int = 1024
        self._window: Optional[np.ndarray] = None
        # Track how many FFTs we've emitted, for diagnostic purposes
        self._fft_count: int = 0

    def set_band_context(self, center_hz: int, span_hz: int) -> None:
        """Update the center frequency. span_hz is ignored — audio FFT span
        is always sample_rate/2.
        """
        self.center_hz = int(center_hz)

    def process_audio(self, chunk: np.ndarray, sample_rate: int, channels: int) -> None:
        """Compute FFT of an audio chunk and emit as spectrum.

        chunk: int16 ndarray, shape (N,) for mono or (N, 2) for stereo.
        """
        if chunk is None or chunk.size == 0:
            return
        # Convert to mono if stereo
        if channels == 2 and chunk.ndim == 2:
            chunk = chunk.mean(axis=1)
        # Need at least fft_size samples
        n = min(len(chunk), self._fft_size)
        if n < 32:
            return
        # Take the most recent N samples
        samples = chunk[-n:].astype(np.float32) / 32768.0
        # Hann window
        if self._window is None or len(self._window) != n:
            self._window = np.hanning(n).astype(np.float32)
        windowed = samples * self._window
        # rFFT → magnitude in dBFS
        fft = np.fft.rfft(windowed)
        # Normalize by N/2 for amplitude, then convert to dBFS
        mag = np.abs(fft) / (n / 2.0)
        # Avoid log(0)
        mag = np.maximum(mag, 1e-7)
        mag_db = 20.0 * np.log10(mag).astype(np.float32)
        # Update span based on actual sample rate
        span = sample_rate // 2
        self.span_hz = span
        self._fft_count += 1
        self.spectrum_ready.emit(mag_db, self.center_hz, span)

    def fft_count(self) -> int:
        return self._fft_count


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
        # "RF" = real RF spectrum (from UDP spectrum stream, rare);
        # "Audio" = audio-band FFT (default fallback when no UDP spectrum)
        self.mode: str = "RF"
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
        # Mode label (top-right of spectrum plot)
        self.mode_label = pg.TextItem(anchor=(1, 0), color="#8b96a7")
        self.mode_label.setText("RF spectrum (idle)")
        self.spectrum_plot.addItem(self.mode_label)
        self.spectrum_plot.setXRange(self.center_hz - self.span_hz / 2,
                                      self.center_hz + self.span_hz / 2, padding=0)
        self.spectrum_plot.setYRange(-100, 0, padding=0)
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
        # Position the mode label at the top-right corner of the spectrum plot
        self.mode_label.setPos(self.center_hz + self.span_hz / 2 * 0.98, -5)

    def update_spectrum(self, data: np.ndarray, center_hz: int, span_hz: int) -> None:
        """Update spectrum + waterfall with a new FFT magnitude array (dBFS).

        The data source (RF UDP spectrum vs Audio FFT) is auto-detected from
        the span: a span >= 100 kHz means RF spectrum; smaller means audio FFT.
        """
        if len(data) == 0:
            return
        # Auto-detect mode from span. Audio FFT has span = sample_rate/2 (~24 kHz).
        # RF spectrum has span >= 100 kHz typically.
        new_mode = "Audio" if span_hz < 100_000 else "RF"
        if new_mode != self.mode or center_hz != self.center_hz or span_hz != self.span_hz:
            self.mode = new_mode
            self.set_band_context(center_hz, span_hz)
            if self.mode == "Audio":
                self.mode_label.setText(f"Audio FFT · {span_hz/1000:.0f} kHz span")
                self.mode_label.setColor("#ffd45c")
                # Audio FFT levels are typically much lower than RF dBFS; widen range
                self.waterfall_image.setLevels([-90.0, -20.0])
            else:
                self.mode_label.setText(f"RF spectrum · {span_hz/1e6:.2f} MHz span")
                self.mode_label.setColor("#5cffaa")
                self.waterfall_image.setLevels([-80.0, -10.0])
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
        # Map dBFS to 0..255 — use mode-appropriate range
        if self.mode == "Audio":
            v = np.clip((col_data + 90) / 70, 0, 1)
        else:
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
