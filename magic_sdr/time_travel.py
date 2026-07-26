"""Time-Travel Audio Buffer — a 30-second circular rewind buffer.

Lets the user "rewind" live radio by up to 30 seconds:
  • A circular buffer continuously records the most recent 30 s of audio
    (after EQ processing).
  • A slider lets the user scrub back in time.
  • When the slider is at the right edge (now), live audio plays through
    to the speakers as normal.
  • When the slider is moved back, playback switches to the buffer at the
    chosen offset; the live stream keeps recording into the buffer in the
    background.
  • When the user releases the slider back to "now", playback catches up
    by jumping to live (with a tiny fade to avoid click).

Implementation notes
--------------------
* The buffer is a numpy ring of int16 samples, sized for BUFFER_SECONDS
  at the current sample rate.
* We track write position (write_head) and read position (read_head).
* When "scrubbing", the AudioPlayer is fed from the buffer at the read
  position; otherwise it's fed live (as before).
* This module is a self-contained widget with a slider, label, and a
  "live / replay" indicator.

CPU is bounded — the buffer ops are pure numpy slicing, very cheap.
"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QColor
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSlider, QLabel, QPushButton,
    QSizePolicy, QFrame
)


BUFFER_SECONDS = 30.0  # How much history to keep


class TimeTravelBuffer:
    """A circular audio buffer that keeps the last N seconds of audio.

    Thread-safety: this class is NOT thread-safe; it must be driven from
    the same thread that calls push() and read(). In our case, both run
    on the Qt main thread because audio chunks arrive via Qt signals.
    """

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        self.sample_rate = sample_rate
        self.channels = channels
        n_samples = int(BUFFER_SECONDS * sample_rate)
        # Ring buffer: shape (n_samples, channels) for stereo, (n_samples,) for mono
        if channels > 1:
            self._buf = np.zeros((n_samples, channels), dtype=np.int16)
        else:
            self._buf = np.zeros(n_samples, dtype=np.int16)
        self._capacity = n_samples
        self._write_head = 0
        self._frames_written = 0  # total frames ever written (monotonic)

    def push(self, chunk: np.ndarray) -> None:
        """Append a chunk of audio to the buffer. chunk must be int16."""
        if chunk.dtype != np.int16:
            chunk = chunk.astype(np.int16)
        # Normalize shape
        if self.channels > 1 and chunk.ndim == 1:
            # Up-mix mono to stereo
            chunk = np.stack([chunk, chunk], axis=1)
        elif self.channels == 1 and chunk.ndim == 2:
            chunk = chunk.mean(axis=1).astype(np.int16)
        n = chunk.shape[0]
        if n == 0:
            return
        # Handle wraparound
        end = self._write_head + n
        if end <= self._capacity:
            self._buf[self._write_head:end] = chunk
        else:
            # Split across the wraparound
            first = self._capacity - self._write_head
            self._buf[self._write_head:] = chunk[:first]
            second = n - first
            self._buf[:second] = chunk[first:]
        self._write_head = (self._write_head + n) % self._capacity
        self._frames_written += n

    def read_oldest_n(self, n: int) -> np.ndarray:
        """Return the oldest n samples currently in the buffer."""
        if self._frames_written < n:
            n = self._frames_written
        if n == 0:
            return np.zeros(0, dtype=np.int16) if self.channels == 1 else np.zeros((0, self.channels), dtype=np.int16)
        # The oldest sample is at (write_head - frames_written) mod capacity,
        # but since we only ever keep the last min(frames_written, capacity) samples,
        # the oldest is at (write_head - min(frames_written, capacity)) mod capacity.
        in_buf = min(self._frames_written, self._capacity)
        start = (self._write_head - in_buf) % self._capacity
        return self._read_range(start, n)

    def read_range(self, start_frame: int, n: int) -> np.ndarray:
        """Return n samples starting at absolute frame index `start_frame`.

        start_frame is in absolute frames since the buffer started. We map
        it to the ring buffer position.
        """
        if start_frame < 0:
            start_frame = 0
        end = start_frame + n
        if end > self._frames_written:
            n = self._frames_written - start_frame
            if n <= 0:
                return np.zeros(0, dtype=np.int16) if self.channels == 1 else np.zeros((0, self.channels), dtype=np.int16)
        # Map absolute frame to ring position. We only keep the last
        # self._capacity frames; if start_frame is older than that, clamp.
        oldest_kept = max(0, self._frames_written - self._capacity)
        ring_start = (start_frame - oldest_kept + self._write_head) % self._capacity
        # Actually simpler: relative position
        # offset_from_head = start_frame - self._frames_written
        # ring_start = (self._write_head + offset_from_head) % self._capacity
        ring_start = (self._write_head + (start_frame - self._frames_written)) % self._capacity
        return self._read_range(ring_start, n)

    def _read_range(self, ring_start: int, n: int) -> np.ndarray:
        """Read n samples from the ring starting at ring_start."""
        end = ring_start + n
        if end <= self._capacity:
            return self._buf[ring_start:end].copy()
        else:
            first = self._capacity - ring_start
            out = np.empty_like(self._buf[:n])
            out[:first] = self._buf[ring_start:]
            out[first:] = self._buf[:n - first]
            return out

    @property
    def total_frames(self) -> int:
        """Total frames ever written (monotonic counter)."""
        return self._frames_written

    @property
    def buffered_duration_s(self) -> float:
        """How many seconds of audio are currently in the buffer."""
        return min(self._frames_written, self._capacity) / self.sample_rate

    def reset(self) -> None:
        """Clear the buffer (e.g., when changing stations)."""
        self._buf.fill(0)
        self._write_head = 0
        self._frames_written = 0


class TimeTravelWidget(QWidget):
    """A slider + label that lets the user scrub back through the live audio.

    When the slider is at the right edge, playback is LIVE (pass-through).
    When the slider is anywhere else, playback is REPLAY from the buffer
    at the chosen offset.

    Emits:
      replay_chunk_requested(offset_frames) — when in REPLAY mode, the
        parent should pull a chunk from the buffer at this offset and feed
        it to the audio player. The parent must connect this to a QTimer
        that fires at the audio chunk rate.
      mode_changed(is_live) — when the user moves between LIVE and REPLAY.
    """

    mode_changed = pyqtSignal(bool)  # True = live, False = replay
    seek_requested = pyqtSignal(int)  # absolute frame offset to seek to

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_live = True
        self._replay_offset_frames = 0  # how many frames behind live

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        # Header
        header = QHBoxLayout()
        self.title = QLabel("⏮ Time Travel")
        self.title.setStyleSheet(
            "color: #5cd9ff; font-size: 11px; font-weight: 600;"
        )
        header.addWidget(self.title)
        header.addStretch(1)
        self.mode_label = QLabel("● LIVE")
        self.mode_label.setStyleSheet(
            "color: #5cffaa; font-size: 10px; font-family: monospace; font-weight: 600;"
        )
        header.addWidget(self.mode_label)
        layout.addLayout(header)

        # Slider — 0 to 3000 (representing 0.0 to 30.0 seconds, in 10 ms steps)
        slider_row = QHBoxLayout()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 3000)
        self.slider.setValue(3000)  # at "now"
        self.slider.valueChanged.connect(self._on_slider_changed)
        slider_row.addWidget(self.slider, stretch=1)
        self.time_label = QLabel("00.0s")
        self.time_label.setStyleSheet(
            "color: #e6ecf3; font-family: 'JetBrains Mono'; font-size: 10px;"
        )
        self.time_label.setMinimumWidth(48)
        slider_row.addWidget(self.time_label)
        layout.addLayout(slider_row)

        # Live button — jumps back to live
        self.live_btn = QPushButton("▶ Live")
        self.live_btn.setFixedHeight(24)
        self.live_btn.setStyleSheet(
            "QPushButton { background: #1a2230; color: #5cffaa; border: 1px solid #2a5a3a;"
            "  border-radius: 4px; padding: 2px 8px; font-size: 10px; font-weight: 600; }"
            "QPushButton:hover { background: #2a3a4e; }"
            "QPushButton:disabled { color: #4a5266; border-color: #2a3447; }"
        )
        self.live_btn.clicked.connect(self.go_live)
        layout.addWidget(self.live_btn)

    def is_live(self) -> bool:
        return self._is_live

    def go_live(self) -> None:
        self.slider.setValue(3000)
        self._is_live = True
        self.mode_label.setText("● LIVE")
        self.mode_label.setStyleSheet("color: #5cffaa; font-size: 10px; font-family: monospace; font-weight: 600;")
        self.live_btn.setEnabled(False)
        self.mode_changed.emit(True)

    def _on_slider_changed(self, value: int) -> None:
        # value is in 10 ms units (0 to 3000 = 0 to 30 s)
        seconds_back = value / 100.0
        self.time_label.setText(f"-{seconds_back:04.1f}s")
        if value >= 2995:  # at "now" (with small deadzone)
            if not self._is_live:
                self._is_live = True
                self.mode_label.setText("● LIVE")
                self.mode_label.setStyleSheet(
                    "color: #5cffaa; font-size: 10px; font-family: monospace; font-weight: 600;"
                )
                self.live_btn.setEnabled(False)
                self.mode_changed.emit(True)
        else:
            if self._is_live:
                self._is_live = False
                self.mode_label.setText("⏮ REPLAY")
                self.mode_label.setStyleSheet(
                    "color: #ffd45c; font-size: 10px; font-family: monospace; font-weight: 600;"
                )
                self.live_btn.setEnabled(True)
                self.mode_changed.emit(False)
            self._replay_offset_frames = int(seconds_back * 48000)  # sample_rate known by parent
            # Emit absolute frame to seek to
            self.seek_requested.emit(self._replay_offset_frames)
