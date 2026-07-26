"""UDP audio receiver + player for Gqrx's audio stream.

Gqrx can stream its demodulated audio over UDP. Configure it in Gqrx via:
  Tools → Remote control settings → Audio UDP stream → enable
  UDP host: 127.0.0.1, UDP port: 7355
  Sample rate: 48 kHz, Stereo, Format: 16-bit signed PCM

The stream is raw PCM (no header). Each UDP packet contains ~512 samples
(2 channels × 2 bytes = 2048 bytes typically).

AudioReceiver listens on the UDP port, collects packets, and pushes them to:
  - AudioPlayer (sounddevice) for live playback
  - RecordingManager (if a recording is in progress)
  - WebServer (for HTTP/WS streaming to remote clients)
"""

from __future__ import annotations

import socket
import threading
import logging
import time
import queue
from typing import Optional, Callable

import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)


# Type for an audio chunk: (np.ndarray of shape (N,) or (N,2), sample_rate, channels)
AudioChunk = np.ndarray


class AudioReceiver(QObject):
    """Receives raw PCM audio from Gqrx via UDP.

    Emits `chunk_ready(np.ndarray, int, int)` for every UDP packet received.
    The ndarray dtype is int16, shape is (N,) for mono or (N, 2) for stereo.
    """

    chunk_ready = pyqtSignal(object, int, int)  # ndarray, sample_rate, channels

    def __init__(self, port: int = 7355, sample_rate: int = 48000,
                 channels: int = 2, parent=None):
        super().__init__(parent)
        self.port = port
        self.sample_rate = sample_rate
        self.channels = channels
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._running = False
        # UDP health tracking — used by the UI to detect if Gqrx is actually
        # streaming audio. If packet_count stays 0 (or last_packet_age_s gets
        # large), the user has NOT enabled the UDP audio stream in Gqrx.
        self._packet_count: int = 0
        self._last_packet_time: float = 0.0

    def start(self) -> bool:
        if self._running:
            return True
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Larger recv buffer so we don't drop packets on burst
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
            self._sock.bind(("0.0.0.0", self.port))
            self._sock.settimeout(0.5)
            self._stop.clear()
            self._running = True
            self._packet_count = 0
            self._last_packet_time = 0.0
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name=f"AudioRecv:{self.port}")
            self._thread.start()
            log.info("AudioReceiver listening on UDP %d", self.port)
            return True
        except Exception as e:
            log.error("Failed to start AudioReceiver on port %d: %s", self.port, e)
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

    # ---- UDP health-check API (used by MainWindow to detect dead streams) ----
    def packet_count(self) -> int:
        """Total UDP audio packets received since start."""
        return self._packet_count

    def last_packet_age_s(self) -> Optional[float]:
        """Seconds since the last packet arrived, or None if no packets yet."""
        if self._last_packet_time == 0.0:
            return None
        return time.time() - self._last_packet_time

    def is_streaming(self, max_age_s: float = 2.0) -> bool:
        """True iff at least one packet arrived in the last `max_age_s` seconds."""
        if self._last_packet_time == 0.0:
            return False
        return (time.time() - self._last_packet_time) <= max_age_s

    def _loop(self) -> None:
        bytes_per_sample = 2  # int16
        frame_size = bytes_per_sample * self.channels
        while not self._stop.is_set():
            try:
                data, _addr = self._sock.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                continue
            # Track packet arrival for health-check
            self._packet_count += 1
            self._last_packet_time = time.time()
            # Trim to whole frames
            n_full = (len(data) // frame_size) * frame_size
            if n_full == 0:
                continue
            arr = np.frombuffer(data[:n_full], dtype=np.int16)
            if self.channels == 2:
                arr = arr.reshape(-1, 2)
            self.chunk_ready.emit(arr, self.sample_rate, self.channels)


class AudioPlayer:
    """Live PCM playback using sounddevice.

    The chunk_ready signal from AudioReceiver feeds this player. We use a
    lock-free ring buffer (queue.Queue) to decouple producer/consumer.
    """

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        self.sample_rate = sample_rate
        self.channels = channels
        self._sd_stream = None
        self._q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=64)
        self._volume = 0.8
        self._muted = False
        self._running = False

    def start(self) -> bool:
        try:
            import sounddevice as sd
        except Exception as e:
            log.error("sounddevice not available: %s", e)
            return False
        try:
            self._sd_stream = sd.RawOutputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                blocksize=0,
                callback=self._callback,
            )
            self._sd_stream.start()
            self._running = True
            log.info("AudioPlayer started: %d Hz, %d ch", self.sample_rate, self.channels)
            return True
        except Exception as e:
            log.error("Failed to open audio output: %s", e)
            self._running = False
            return False

    def stop(self) -> None:
        self._running = False
        if self._sd_stream:
            try:
                self._sd_stream.stop()
                self._sd_stream.close()
            except Exception:
                pass
            self._sd_stream = None

    def push(self, chunk: np.ndarray) -> None:
        """Accept a chunk (int16) for playback. Drops if buffer is full."""
        if not self._running:
            return
        # Apply volume
        if self._muted or self._volume == 0.0:
            chunk = np.zeros_like(chunk)
        elif self._volume < 1.0:
            chunk = (chunk.astype(np.float32) * self._volume).astype(np.int16)
        try:
            self._q.put_nowait(chunk)
        except queue.Full:
            # Drop oldest to keep latency low
            try:
                self._q.get_nowait()
                self._q.put_nowait(chunk)
            except Exception:
                pass

    def _callback(self, outdata: np.ndarray, frames: int, time_info, status) -> None:
        # sounddevice wants `frames` samples per channel
        try:
            chunk = self._q.get_nowait()
        except queue.Empty:
            outdata.fill(0)
            return
        # Reshape if needed
        if self.channels == 2 and chunk.ndim == 1:
            chunk = np.column_stack([chunk, chunk])
        # Fit to requested frames
        n = min(len(chunk), frames)
        outdata[:n] = chunk[:n]
        if n < frames:
            outdata[n:].fill(0)
        # If chunk was bigger, push remainder back
        if len(chunk) > n:
            try:
                self._q.put_nowait(chunk[n:])
            except queue.Full:
                pass

    def set_volume(self, v: float) -> None:
        self._volume = max(0.0, min(1.0, v))

    def get_volume(self) -> float:
        return self._volume

    def set_muted(self, muted: bool) -> None:
        self._muted = muted

    def is_muted(self) -> bool:
        return self._muted
