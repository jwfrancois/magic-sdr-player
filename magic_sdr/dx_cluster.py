"""DX Cluster client — live feed of worldwide ham radio DX spots.

A DX cluster is a network of servers (typically accessible via telnet on
port 8000 or 7373) where ham operators post "spots" — announcements of
interesting DX (long-distance) stations they're hearing. Each spot looks
like:

    DX de W1XYZ:     14025.0  DL1ABC       CW  23 dB   18:30Z  <CLUSTER>

This module connects to a DX cluster via plain TCP (no authentication
required for read-only public clusters), parses incoming spot lines,
and exposes them as a deque of recent spots.

The cluster node list (in order, try each until one connects):
  • dxc.w9pa.net:7373      (USA, general-purpose)
  • dxc.ve7cc.net:23       (Canada)
  • nc7j.hrdllc.net:7373   (USA west coast)
  • dxspider.w1nr.net:7373 (USA east coast)

When connected, the cluster sends a steady stream of spots — anywhere
from 1/min to 10/min depending on band conditions and time of day.

Click-to-tune: each spot exposes its frequency; the UI lets the user
click a spot to tune there.
"""

from __future__ import annotations

import logging
import re
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional

from PyQt5.QtCore import QObject, pyqtSignal, QTimer

log = logging.getLogger(__name__)


# DX cluster node list (in priority order)
DX_CLUSTER_NODES = [
    ("dxc.w9pa.net", 7373),
    ("dxc.ve7cc.net", 23),
    ("nc7j.hrdllc.net", 7373),
    ("dxspider.w1nr.net", 7373),
    ("dxc.db0sue.ampr.org", 8000),
]

# Spot line regex — captures spotter, freq, callsign, mode/comment, time
# Example: "DX de W1XYZ:     14025.0  DL1ABC       CW  23 dB   18:30Z"
# Example: "DX de K2ABC:      7153.5   ZL2XYZ       FT8  -12 dB  22:14Z"
SPOT_RE = re.compile(
    r"DX de ([A-Z0-9/]+):\s+"
    r"(\d+\.\d+)\s+"
    r"([A-Z0-9/]+)\s+"
    r"(.*?)\s+"
    r"(\d{4})Z\s*$",
    re.IGNORECASE,
)


@dataclass
class DXSpot:
    spotter: str        # callsign of the spotter
    freq_hz: int        # spotted frequency
    dx_callsign: str    # callsign of the spotted station
    comment: str        # mode, signal, etc.
    time_z: str         # 4-digit Zulu time (HHMM)
    received_at: float = field(default_factory=time.time)  # local timestamp

    @property
    def freq_mhz(self) -> float:
        return self.freq_hz / 1e6

    def format(self) -> str:
        """One-line summary suitable for display in a list."""
        age_s = time.time() - self.received_at
        if age_s < 60:
            age_str = f"{int(age_s)}s ago"
        elif age_s < 3600:
            age_str = f"{int(age_s / 60)}m ago"
        else:
            age_str = f"{int(age_s / 3600)}h ago"
        return (
            f"{self.freq_mhz:8.3f} MHz  {self.dx_callsign:<10}  "
            f"by {self.spotter:<8}  {self.comment[:30]:<30}  "
            f"{self.time_z}Z  {age_str}"
        )


class DXClusterClient(QObject):
    """Connects to a DX cluster, parses spots, exposes them via Qt signals."""

    spot_received = pyqtSignal(object)  # DXSpot
    connection_changed = pyqtSignal(bool, str)  # connected, message

    def __init__(self, max_spots: int = 200, parent=None):
        super().__init__(parent)
        self.max_spots = max_spots
        self.spots: Deque[DXSpot] = deque(maxlen=max_spots)
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._connected = False
        # Callsign for login — public clusters accept any callsign
        # We use a placeholder that's clearly an SDR monitor, not a real ham.
        self.login_callsign = "SDR-MONITOR"

        # Auto-reconnect timer — if disconnected, try to reconnect every 30 s
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setInterval(30_000)
        self._reconnect_timer.timeout.connect(self._try_reconnect)
        self._reconnect_timer.start()

    # ----------------------------- public API -----------------------------
    def start(self) -> None:
        """Start connecting to the cluster (non-blocking)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="DXCluster")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._reconnect_timer.stop()

    @property
    def is_connected(self) -> bool:
        return self._connected

    def get_recent_spots(self, n: int = 50) -> List[DXSpot]:
        """Return the n most recent spots, newest first."""
        return list(self.spots)[-n:][::-1]

    # ----------------------------- internals -----------------------------
    def _try_reconnect(self) -> None:
        if not self._connected and not self._stop.is_set():
            if not (self._thread and self._thread.is_alive()):
                self.start()

    def _run(self) -> None:
        """Worker thread: connect to a cluster, read lines, emit spots."""
        for host, port in DX_CLUSTER_NODES:
            if self._stop.is_set():
                return
            try:
                log.info("DX cluster: connecting to %s:%d…", host, port)
                sock = socket.create_connection((host, port), timeout=15)
                sock.settimeout(5.0)
                self._sock = sock
                self._connected = True
                self.connection_changed.emit(True, f"Connected to {host}:{port}")
                # Send login (callsign)
                try:
                    sock.sendall((self.login_callsign + "\n").encode("ascii", errors="ignore"))
                except Exception:
                    pass
                # Read loop
                buf = b""
                while not self._stop.is_set():
                    try:
                        data = sock.recv(4096)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    if not data:
                        break
                    buf += data
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        try:
                            self._handle_line(line.decode("utf-8", errors="ignore").strip())
                        except Exception as e:
                            log.debug("DX cluster line parse error: %s", e)
                # Disconnected
                self._connected = False
                self.connection_changed.emit(False, f"Disconnected from {host}:{port}")
                try:
                    sock.close()
                except Exception:
                    pass
                self._sock = None
                return  # _reconnect_timer will restart us
            except Exception as e:
                log.debug("DX cluster connect to %s:%d failed: %s", host, port, e)
                continue
        # All nodes failed
        self._connected = False
        self.connection_changed.emit(False, "All cluster nodes unreachable — will retry in 30s")

    def _handle_line(self, line: str) -> None:
        """Parse a single cluster line. If it's a spot, emit it."""
        if not line:
            return
        m = SPOT_RE.match(line)
        if m:
            try:
                spotter = m.group(1).upper()
                freq_khz = float(m.group(2))
                freq_hz = int(freq_khz * 1000)  # cluster freqs are in kHz
                dx = m.group(3).upper()
                comment = m.group(4).strip()
                time_z = m.group(5)
                spot = DXSpot(
                    spotter=spotter,
                    freq_hz=freq_hz,
                    dx_callsign=dx,
                    comment=comment,
                    time_z=time_z,
                )
                self.spots.append(spot)
                self.spot_received.emit(spot)
            except Exception as e:
                log.debug("Spot parse failed: %s (line: %s)", e, line)
        # else: cluster status / chat line — ignore for now
