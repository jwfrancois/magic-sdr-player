"""Gqrx remote control client.

Gqrx listens on TCP port 7356 for line-based ASCII commands (the "Gqrx Remote
Protocol", a subset of the Hamlib NET rigctl protocol). Each command is a single
line terminated by `\\n`. Most commands return `RPRT 0\\n` on success or a value.

Documented at: https://gqrx.dk/doc/remote-control

Key commands used here:
  F <freq>          Set frequency in Hz (without decimal point)
  f                 Get frequency in Hz
  M <mod>           Set modulation (OFF RAW AM FM WFM WFM_ST WFM_ST_OIRT
                                LSB USB CW CWL CWU)
  m                 Get modulation string
  L SQL <sql>       Set squelch level in dB (absolute)
  L SQLGAIN <g>     Set squelch gain
  L AF <g>          Set audio gain (0..1000)
  L RF <g>          Set RF gain
  L AGC <mode>      Set AGC mode (OFF SLOW MEDIUM FAST)
  l <level>         Get level value (e.g. `l SQL` returns current squelch)
  U DSP <sel>       Set/get demodulator options
  u <sel>           Get current value of an option
  AOS               Start audio recording (Gqrx's own recorder)
  LOS               Stop audio recording
  record_start      Start audio recording (alt)
  record_stop       Stop audio recording (alt)
  dump_state        Returns a multi-line state dump (rig caps)
  q                 Close connection (we don't use this; we just disconnect)
  \\chk_vfo          No-op compatibility check (always returns 0)
  \\chk_power        Returns 1 if power on, else 0

This module wraps all of that in a clean Python class with timeouts, reconnect,
and Qt-friendly signals (so the GUI can react to async events).
"""

from __future__ import annotations

import socket
import threading
import time
import logging
from typing import Optional, Callable

from PyQt5.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)


# All modulations Gqrx understands, mapped to short labels used in the UI.
MODULATIONS = [
    "OFF",         # Receiver off
    "RAW",         # Raw I/Q passthrough
    "AM",          # Amplitude modulation
    "FM",          # Narrow FM (12.5 kHz)
    "WFM",         # Wide FM (mono)
    "WFM_ST",      # Wide FM (stereo) — for FM broadcast
    "WFM_ST_OIRT", # Wide FM (OIRT stereo, 65.8–74 MHz Eastern Europe)
    "LSB",         # Lower sideband
    "USB",         # Upper sideband
    "CW",          # Continuous wave (CWU)
    "CWL",         # CW (LSB-side)
    "CWU",         # CW (USB-side)
]


class GqrxClient(QObject):
    """Thread-safe TCP client for Gqrx's remote control protocol.

    Emits Qt signals on connection state changes and async errors so the GUI
    can react without polling.
    """

    # --- Qt signals (all GUI-safe) ---
    connected = pyqtSignal()
    disconnected = pyqtSignal(str)        # reason
    frequency_changed = pyqtSignal(int)   # new freq Hz (from `f` polling)
    modulation_changed = pyqtSignal(str)  # new modulation (from `m` polling)
    signal_level = pyqtSignal(float)      # current signal level in dB
    error = pyqtSignal(str)

    def __init__(self, host: str = "127.0.0.1", port: int = 7356, parent=None):
        super().__init__(parent)
        self.host = host
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._lock = threading.RLock()
        self._connected = False
        self._stop_poller = threading.Event()
        self._poller: Optional[threading.Thread] = None

    # ----------------------------- connection -----------------------------
    def connect(self, timeout: float = 3.0) -> bool:
        """Open a TCP connection to Gqrx. Returns True on success."""
        with self._lock:
            if self._connected and self._sock:
                return True
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(timeout)
                s.connect((self.host, self.port))
                # Send the compatibility no-op; Gqrx expects this on connect.
                try:
                    s.sendall(b"\\dump_state\n")
                    s.recv(4096)  # discard the state dump
                except socket.timeout:
                    pass
                s.settimeout(5.0)
                self._sock = s
                self._connected = True
                log.info("Connected to Gqrx at %s:%d", self.host, self.port)
                self.connected.emit()
                self._start_poller()
                return True
            except Exception as e:
                self._connected = False
                err = f"Cannot connect to Gqrx at {self.host}:{self.port} — {e}"
                log.warning(err)
                self.error.emit(err)
                return False

    def disconnect(self) -> None:
        with self._lock:
            self._stop_poller.set()
            if self._poller and self._poller.is_alive():
                self._poller.join(timeout=1.0)
            self._poller = None
            self._stop_poller.clear()
            if self._sock:
                try:
                    self._sock.close()
                except Exception:
                    pass
            self._sock = None
            self._connected = False
            self.disconnected.emit("Disconnected by user")

    def is_connected(self) -> bool:
        return self._connected

    # ---- poller control ----
    # The background poller sends f/m/l STRENGTH commands every 500ms on the
    # SAME socket the scanner uses. During a scan, this can interleave with
    # the scanner's F/l STRENGTH commands and corrupt response parsing.
    # The scanner calls pause_poller() before scanning and resume_poller()
    # after, so it has exclusive access to the socket during sweeps.
    def pause_poller(self) -> None:
        """Temporarily stop the background poller (e.g., during scans)."""
        if self._poller and self._poller.is_alive():
            self._stop_poller.set()
            self._poller.join(timeout=1.0)
            self._poller = None

    def resume_poller(self) -> None:
        """Restart the background poller after a pause_poller() call."""
        if not self._poller or not self._poller.is_alive():
            self._stop_poller.clear()
            self._start_poller()

    # ----------------------------- low-level I/O -----------------------------
    def _send(self, cmd: str, expect_reply: bool = True, timeout: float = 3.0) -> Optional[str]:
        """Send a single command and return the reply line (or None).

        Most commands return `RPRT 0` on success. Some (F, M, L, l, f, m, u, U)
        return a value or `RPRT 0`.
        """
        with self._lock:
            if not self._connected or not self._sock:
                return None
            try:
                self._sock.settimeout(timeout)
                self._sock.sendall((cmd + "\n").encode("ascii", errors="replace"))
                if not expect_reply:
                    return None
                # Read until newline
                buf = b""
                while b"\n" not in buf:
                    chunk = self._sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                return buf.decode("ascii", errors="replace").strip()
            except socket.timeout:
                log.warning("Timeout sending command: %s", cmd)
                return None
            except Exception as e:
                log.warning("Error sending command %r: %s", cmd, e)
                self._connected = False
                self.disconnected.emit(f"Connection lost: {e}")
                return None

    # ----------------------------- high-level API -----------------------------
    def set_frequency(self, freq_hz: int) -> bool:
        """Tune the receiver to freq_hz."""
        r = self._send(f"F {int(freq_hz)}")
        return r is not None and (r == "RPRT 0" or r.startswith("RPRT 0"))

    def get_frequency(self) -> Optional[int]:
        r = self._send("f")
        if r and r.isdigit():
            return int(r)
        # Some versions return e.g. "96000000\nRPRT 0"
        if r and r.split()[0].isdigit():
            return int(r.split()[0])
        return None

    def set_modulation(self, mod: str) -> bool:
        if mod not in MODULATIONS:
            log.warning("Unknown modulation: %s", mod)
            return False
        r = self._send(f"M {mod}")
        return r is not None and r.startswith("RPRT 0")

    def get_modulation(self) -> Optional[str]:
        r = self._send("m")
        if r and r != "RPRT 0":
            # Gqrx returns modulation then RPRT 0 on second line
            return r.split("\n")[0].strip()
        return None

    def set_squelch(self, db: float) -> bool:
        r = self._send(f"L SQL {db}")
        return r is not None and r.startswith("RPRT 0")

    def set_audio_gain(self, gain: float) -> bool:
        """gain is 0..1000 (linear)."""
        r = self._send(f"L AF {int(gain)}")
        return r is not None and r.startswith("RPRT 0")

    def set_rf_gain(self, gain_db: float) -> bool:
        """RF gain in dB (0 = AGC)."""
        r = self._send(f"L RF {int(gain_db)}")
        return r is not None and r.startswith("RPRT 0")

    def get_signal_level(self) -> Optional[float]:
        """Read the current signal level in dBFS via `l STRENGTH`.

        Gqrx returns the dBFS value as a single line, e.g. "-72.34".
        However, behavior varies across versions:
          * Some return "RPRT 0" with no number (level not available)
          * Some return an empty line
          * Some return "0" (no signal at all)
          * Some return a float, optionally followed by "RPRT 0" on the next line

        We try to extract the first parseable float from the response.
        Returns None if no number can be parsed (caller should treat as
        "unknown" — NOT as a -100 dB signal).
        """
        import math
        r = self._send("l STRENGTH")
        if r is None:
            return None
        # Multi-line: take the first line that parses as a float
        for line in r.splitlines():
            line = line.strip()
            if not line or line.startswith("RPRT"):
                continue
            try:
                v = float(line)
            except ValueError:
                # Maybe first token is the number
                try:
                    v = float(line.split()[0])
                except (ValueError, IndexError):
                    continue
            # Reject NaN / Inf — Gqrx shouldn't send these but be defensive
            if math.isfinite(v):
                return v
        return None

    def get_signal_level_robust(self, n_samples: int = 3,
                                 interval_s: float = 0.05) -> Optional[float]:
        """Sample the signal level `n_samples` times and return the max.

        Useful for scanning — a single sample can hit a fade, but the max
        of several is a more reliable indicator of carrier presence.
        """
        best: Optional[float] = None
        for _ in range(max(1, n_samples)):
            lvl = self.get_signal_level()
            if lvl is not None and (best is None or lvl > best):
                best = lvl
            if n_samples > 1:
                time.sleep(interval_s)
        return best

    def start_recording(self) -> bool:
        r = self._send("AOS")
        return r is not None and r.startswith("RPRT 0")

    def stop_recording(self) -> bool:
        r = self._send("LOS")
        return r is not None and r.startswith("RPRT 0")

    # ----------------------------- poller thread -----------------------------
    def _start_poller(self) -> None:
        """Background poller: queries freq/mod/level every 500 ms and emits signals."""
        if self._poller and self._poller.is_alive():
            return
        self._stop_poller.clear()
        self._poller = threading.Thread(target=self._poll_loop, daemon=True, name="GqrxPoller")
        self._poller.start()

    def _poll_loop(self) -> None:
        last_freq: Optional[int] = None
        last_mod: Optional[str] = None
        last_level_emit = 0.0
        while not self._stop_poller.is_set() and self._connected:
            try:
                # Frequency
                f = self.get_frequency()
                if f is not None and f != last_freq:
                    last_freq = f
                    self.frequency_changed.emit(f)
                # Modulation
                m = self.get_modulation()
                if m and m != last_mod:
                    last_mod = m
                    self.modulation_changed.emit(m)
                # Signal level — emit at most every ~250 ms to avoid flooding
                lvl = self.get_signal_level()
                if lvl is not None and abs(lvl - last_level_emit) >= 0.5:
                    last_level_emit = lvl
                    self.signal_level.emit(lvl)
            except Exception as e:
                log.warning("Poller error: %s", e)
            self._stop_poller.wait(0.5)
