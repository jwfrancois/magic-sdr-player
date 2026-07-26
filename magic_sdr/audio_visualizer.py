"""Audio Visualizer — animated visualizations driven by the live audio stream.

Four switchable modes, all rendered with QPainter:
  1. OSCILLOSCOPE — classic green-trace oscilloscope with grid + glow
  2. SPECTRUM BARS — rainbow-colored frequency bars, peak-hold markers
  3. CIRCULAR    — spectrum drawn as a polar plot (frequency = angle, magnitude = radius)
  4. LIQUID      — "liquid light" 70s psychedelic visualization: smooth radial blobs
                   driven by FFT magnitude, colors cycle through HSL based on dominant freq

The widget consumes audio chunks via push_audio() (same int16 PCM the
audio player gets). It computes its own FFT internally for the
spectrum-based modes; the oscilloscope just plots the raw waveform.

CPU is bounded — we recompute the FFT at most every 50 ms (20 fps),
reusing the most recent chunk if a new one hasn't arrived.
"""

from __future__ import annotations

import math
import time
from typing import Optional

import numpy as np
from PyQt5.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt5.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QPolygonF, QLinearGradient,
    QRadialGradient, QConicalGradient, QPainterPath
)
from PyQt5.QtWidgets import QWidget, QSizePolicy

# Visualization modes
MODE_OSCILLOSCOPE = "Oscilloscope"
MODE_SPECTRUM_BARS = "Spectrum Bars"
MODE_CIRCULAR = "Circular"
MODE_LIQUID = "Liquid Light"

ALL_MODES = [MODE_OSCILLOSCOPE, MODE_SPECTRUM_BARS, MODE_CIRCULAR, MODE_LIQUID]

# FFT size — must be a power of 2. 1024 gives ~46 Hz resolution at 48 kHz.
FFT_SIZE = 1024


class AudioVisualizer(QWidget):
    """A multi-mode real-time audio visualizer widget."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = MODE_OSCILLOSCOPE
        self.setMinimumSize(280, 180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setToolTip(
            "Audio Visualizer — right-click to change mode\n"
            "  • Oscilloscope — waveform trace\n"
            "  • Spectrum Bars — rainbow frequency bars\n"
            "  • Circular — polar spectrum plot\n"
            "  • Liquid Light — psychedelic 70s light show"
        )

        # Latest audio data
        self._waveform: Optional[np.ndarray] = None  # mono float32 in [-1, 1]
        self._spectrum: Optional[np.ndarray] = None  # magnitude (linear), length FFT_SIZE/2
        self._sample_rate: int = 48000
        self._channels: int = 2

        # Peak-hold for spectrum bars
        self._peak_hold: Optional[np.ndarray] = None
        self._peak_decay: float = 0.92  # decay rate per frame

        # Liquid light state — phase offsets for each blob
        self._liquid_phase: float = 0.0
        self._dominant_freq_hz: float = 0.0
        self._hue_offset: float = 0.0

        # Repaint timer — 20 fps for smooth animation without burning CPU
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self.update)
        self._timer.start()

        # Track time for liquid light animation
        self._last_t = time.time()

    # ----------------------------- public API -----------------------------
    def push_audio(self, chunk: np.ndarray, sample_rate: int, channels: int) -> None:
        """Feed a new audio chunk. The widget keeps only the most recent one."""
        try:
            self._sample_rate = sample_rate
            self._channels = channels
            # Convert to mono float32 in [-1, 1]
            if chunk.dtype == np.int16:
                audio = chunk.astype(np.float32) / 32768.0
            elif chunk.dtype == np.int32:
                audio = chunk.astype(np.float32) / 2147483648.0
            elif chunk.dtype == np.float32:
                audio = chunk.copy()
            else:
                audio = chunk.astype(np.float32)

            # Down-mix to mono
            if audio.ndim == 2:
                audio = audio.mean(axis=1)

            # Take the most recent FFT_SIZE samples
            if len(audio) >= FFT_SIZE:
                self._waveform = audio[-FFT_SIZE:].copy()
            else:
                # Pad with zeros
                padded = np.zeros(FFT_SIZE, dtype=np.float32)
                padded[:len(audio)] = audio
                self._waveform = padded

            # Compute FFT for the spectrum modes
            windowed = self._waveform * np.hanning(FFT_SIZE)
            fft = np.fft.rfft(windowed)
            mag = np.abs(fft).astype(np.float32)
            # Normalize to [0, 1] roughly — rfft of int16 audio is at most FFT_SIZE
            self._spectrum = mag / (FFT_SIZE * 0.5)

            # Track dominant frequency for the liquid light hue
            if mag.any():
                peak_bin = int(np.argmax(mag[1:]) + 1)  # skip DC
                self._dominant_freq_hz = peak_bin * (sample_rate / 2.0) / (FFT_SIZE // 2)
        except Exception:
            # Never let the visualizer crash the audio path
            pass

    def set_mode(self, mode: str) -> None:
        if mode in ALL_MODES:
            self.mode = mode
            self.update()

    def cycle_mode(self) -> str:
        """Advance to the next mode. Returns the new mode name."""
        idx = ALL_MODES.index(self.mode)
        new_idx = (idx + 1) % len(ALL_MODES)
        self.set_mode(ALL_MODES[new_idx])
        return self.mode

    # ----------------------------- event handlers -----------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            new_mode = self.cycle_mode()
            # Show a transient label via the parent's status bar mechanism
            self.setToolTip(f"Mode: {new_mode}")
        super().mousePressEvent(event)

    # ----------------------------- paint -----------------------------
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        # Background — deep gradient
        bg = QLinearGradient(0, 0, 0, h)
        bg.setColorAt(0, QColor(10, 12, 22))
        bg.setColorAt(1, QColor(6, 8, 14))
        p.fillRect(0, 0, w, h, bg)

        # Mode label in top-left corner
        p.setFont(QFont("JetBrains Mono", 8))
        p.setPen(QColor(120, 130, 150))
        p.drawText(8, 14, f"◈ {self.mode}")

        # Mode-specific drawing
        if self.mode == MODE_OSCILLOSCOPE:
            self._draw_oscilloscope(p, w, h)
        elif self.mode == MODE_SPECTRUM_BARS:
            self._draw_spectrum_bars(p, w, h)
        elif self.mode == MODE_CIRCULAR:
            self._draw_circular(p, w, h)
        elif self.mode == MODE_LIQUID:
            self._draw_liquid(p, w, h)

    # ----------------------------- mode 1: oscilloscope -----------------------------
    def _draw_oscilloscope(self, p: QPainter, w: int, h: int) -> None:
        # Grid
        p.setPen(QPen(QColor(20, 60, 40), 1, Qt.DashLine))
        for i in range(1, 8):
            x = int(i * w / 8)
            p.drawLine(x, 0, x, h)
        for i in range(1, 6):
            y = int(i * h / 6)
            p.drawLine(0, y, w, y)

        # Center line
        p.setPen(QPen(QColor(40, 100, 60), 1))
        p.drawLine(0, h // 2, w, h // 2)

        if self._waveform is None or len(self._waveform) == 0:
            p.setPen(QColor(80, 200, 120))
            p.setFont(QFont("JetBrains Mono", 10))
            p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, "— waiting for audio —")
            return

        # Glow effect — draw the trace 3 times: wide & dim, medium, narrow & bright
        samples = self._waveform[:min(len(self._waveform), 512)]
        n = len(samples)
        path = QPainterPath()
        for i, s in enumerate(samples):
            x = i * (w / max(1, n - 1))
            # Auto-scale: use 4x amplification for typical -12 dBFS audio
            y = h / 2 - float(s) * h * 0.4
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        # Outer glow
        glow_pen = QPen(QColor(60, 220, 130, 60), 6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(glow_pen)
        p.drawPath(path)
        # Mid glow
        p.setPen(QPen(QColor(80, 240, 150, 120), 3))
        p.drawPath(path)
        # Bright core
        p.setPen(QPen(QColor(180, 255, 200, 255), 1.5))
        p.drawPath(path)

    # ----------------------------- mode 2: spectrum bars -----------------------------
    def _draw_spectrum_bars(self, p: QPainter, w: int, h: int) -> None:
        if self._spectrum is None or len(self._spectrum) == 0:
            p.setPen(QColor(120, 130, 150))
            p.setFont(QFont("JetBrains Mono", 10))
            p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, "— waiting for audio —")
            return

        # Logarithmic frequency mapping — show ~32 bars across audible range
        n_bars = 32
        spec = self._spectrum
        n_bins = len(spec)
        # Map bar index to bin range logarithmically (1 to n_bins-1)
        bins_per_bar = []
        for i in range(n_bars):
            start = int(math.pow(n_bins - 1, i / n_bars)) + 1
            end = int(math.pow(n_bins - 1, (i + 1) / n_bars)) + 1
            start = max(1, min(start, n_bins - 1))
            end = max(start + 1, min(end, n_bins))
            bins_per_bar.append((start, end))

        # Compute bar heights (max magnitude in each bin range)
        bar_heights = []
        for s, e in bins_per_bar:
            seg = spec[s:e]
            if len(seg) > 0:
                # Convert to dB
                val = float(np.max(seg))
                db = 20 * math.log10(max(val, 1e-9))
                # Map [-80, 0] dB to [0, 1]
                h_norm = max(0.0, min(1.0, (db + 80) / 80))
            else:
                h_norm = 0.0
            bar_heights.append(h_norm)

        # Peak hold
        if self._peak_hold is None or len(self._peak_hold) != n_bars:
            self._peak_hold = np.zeros(n_bars, dtype=np.float32)
        for i, v in enumerate(bar_heights):
            if v > self._peak_hold[i]:
                self._peak_hold[i] = v
            else:
                self._peak_hold[i] *= self._peak_decay

        # Draw bars with rainbow gradient
        bar_w = w / n_bars
        for i, v in enumerate(bar_heights):
            x = i * bar_w
            bar_h = v * (h - 20)
            y = h - 10 - bar_h
            # Rainbow color: red→yellow→green→cyan→blue→magenta across bars
            hue = (i / n_bars) * 280  # 0-280° (red to violet)
            color = QColor.fromHsv(int(hue), 220, 240)
            # Gradient fill — bright at top, dim at bottom
            grad = QLinearGradient(0, y, 0, h - 10)
            grad.setColorAt(0, color.lighter(140))
            grad.setColorAt(1, color.darker(180))
            p.fillRect(QRectF(x + 1, y, bar_w - 2, bar_h), QBrush(grad))

            # Peak hold marker
            peak_y = h - 10 - self._peak_hold[i] * (h - 20)
            peak_color = color.lighter(180)
            p.setPen(QPen(peak_color, 2))
            p.drawLine(QPointF(x + 1, peak_y), QPointF(x + bar_w - 1, peak_y))

        # Baseline
        p.setPen(QPen(QColor(80, 90, 110), 1))
        p.drawLine(0, h - 10, w, h - 10)

    # ----------------------------- mode 3: circular -----------------------------
    def _draw_circular(self, p: QPainter, w: int, h: int) -> None:
        if self._spectrum is None or len(self._spectrum) == 0:
            p.setPen(QColor(120, 130, 150))
            p.setFont(QFont("JetBrains Mono", 10))
            p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, "— waiting for audio —")
            return

        cx, cy = w / 2, h / 2
        base_r = min(w, h) * 0.18
        max_r = min(w, h) * 0.46

        # Center pulse — driven by RMS
        if self._waveform is not None and len(self._waveform) > 0:
            rms = float(np.sqrt(np.mean(self._waveform ** 2)))
        else:
            rms = 0.0
        pulse_r = base_r * (1.0 + min(1.0, rms * 4.0))
        # Glow
        for i, alpha in enumerate([30, 60, 100]):
            r = pulse_r + i * 4
            grad = QRadialGradient(cx, cy, r)
            grad.setColorAt(0, QColor(90, 200, 255, alpha))
            grad.setColorAt(1, QColor(90, 200, 255, 0))
            p.setBrush(QBrush(grad))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(cx, cy), r, r)

        # Radial spectrum — 64 rays around the circle
        n_rays = 64
        spec = self._spectrum
        n_bins = len(spec)
        # Log-bin
        ray_heights = []
        for i in range(n_rays):
            t = i / n_rays
            bin_idx = int(math.pow(n_bins - 1, t))
            bin_idx = max(1, min(bin_idx, n_bins - 1))
            val = float(spec[bin_idx])
            db = 20 * math.log10(max(val, 1e-9))
            h_norm = max(0.0, min(1.0, (db + 60) / 60))
            ray_heights.append(h_norm)

        # Draw each ray
        for i, v in enumerate(ray_heights):
            angle = (i / n_rays) * 2 * math.pi - math.pi / 2
            r1 = pulse_r + 4
            r2 = pulse_r + 4 + v * (max_r - pulse_r - 4)
            x1 = cx + r1 * math.cos(angle)
            y1 = cy + r1 * math.sin(angle)
            x2 = cx + r2 * math.cos(angle)
            y2 = cy + r2 * math.sin(angle)
            # Color cycles with angle
            hue = int((i / n_rays) * 360 + time.time() * 30) % 360
            color = QColor.fromHsv(hue, 220, 240)
            pen = QPen(color, 2)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # Connecting outer ring — curve through the tips
        path = QPainterPath()
        for i, v in enumerate(ray_heights):
            angle = (i / n_rays) * 2 * math.pi - math.pi / 2
            r2 = pulse_r + 4 + v * (max_r - pulse_r - 4)
            x = cx + r2 * math.cos(angle)
            y = cy + r2 * math.sin(angle)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        path.closeSubpath()
        p.setPen(QPen(QColor(180, 220, 255, 120), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

    # ----------------------------- mode 4: liquid light -----------------------------
    def _draw_liquid(self, p: QPainter, w: int, h: int) -> None:
        """Psychedelic 70s-style liquid light show.

        Several radial "blobs" drift around, each blob's radius and color
        driven by a different FFT band. The result is a smoothly morphing,
        colorful lava-lamp effect that responds to the music.
        """
        # Compute frame time delta for smooth animation
        now = time.time()
        dt = now - self._last_t
        self._last_t = now
        self._liquid_phase += dt
        self._hue_offset = (self._hue_offset + dt * 30) % 360

        # Pick 6 frequency bands spread across the spectrum to drive blobs
        if self._spectrum is None or len(self._spectrum) < 64:
            # No audio yet — still draw idle blobs
            bands = [0.0] * 6
        else:
            spec = self._spectrum
            n_bins = len(spec)
            bands = []
            for i, t in enumerate([0.05, 0.15, 0.3, 0.5, 0.7, 0.9]):
                bin_idx = max(1, min(int(t * n_bins), n_bins - 1))
                val = float(spec[bin_idx])
                db = 20 * math.log10(max(val, 1e-9))
                bands.append(max(0.0, min(1.0, (db + 60) / 60)))

        # 6 blobs orbiting the center at different speeds/radii
        cx, cy = w / 2, h / 2
        for i, intensity in enumerate(bands):
            # Each blob has its own orbit
            speed = 0.3 + i * 0.15
            orbit_r = min(w, h) * (0.15 + i * 0.05)
            phase = self._liquid_phase * speed + i * 1.0472  # 60° offset
            bx = cx + orbit_r * math.cos(phase)
            by = cy + orbit_r * math.sin(phase * 1.3 + i)

            # Blob radius pulses with intensity
            blob_r = (min(w, h) * 0.18) * (0.6 + intensity * 1.8)

            # Color cycles through hues
            hue = int((self._hue_offset + i * 60) % 360)
            color = QColor.fromHsv(hue, 230, 250)

            # Radial gradient — bright in the center, fading to transparent
            grad = QRadialGradient(bx, by, blob_r)
            grad.setColorAt(0, QColor(color.red(), color.green(), color.blue(), 180))
            grad.setColorAt(0.5, QColor(color.red(), color.green(), color.blue(), 80))
            grad.setColorAt(1, QColor(color.red(), color.green(), color.blue(), 0))
            p.setBrush(QBrush(grad))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(bx, by), blob_r, blob_r)

        # Central "core" — pulsing brightness
        if self._waveform is not None and len(self._waveform) > 0:
            rms = float(np.sqrt(np.mean(self._waveform ** 2)))
        else:
            rms = 0.0
        core_r = min(w, h) * 0.05 * (1.0 + rms * 3.0)
        core_grad = QRadialGradient(cx, cy, core_r * 3)
        core_color = QColor.fromHsv(int(self._hue_offset) % 360, 80, 255)
        core_grad.setColorAt(0, QColor(255, 255, 255, 220))
        core_grad.setColorAt(0.3, QColor(core_color.red(), core_color.green(), core_color.blue(), 150))
        core_grad.setColorAt(1, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(core_grad))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx, cy), core_r * 3, core_r * 3)
