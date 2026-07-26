"""Auto-Surf — magical "scan all bands, play each strong station for a few seconds".

This is the user's "I just want to listen to whatever's loudest" button.
Press it and the app:

  1. Sweeps every supported band (FM, AM, Airband, NOAA, Marine, 2m, 70cm, HF).
  2. For each band, finds the strongest signal.
  3. Stops there, plays it for `dwell_seconds` (default 5 s).
  4. If the user has clicked the magic button again or pressed Stop, halt.
  5. Otherwise, move on to the next band.
  6. At the end, optionally loop back to the strongest station found overall.

The surf runs in a background thread; the UI updates via Qt signals.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Callable

from PyQt5.QtCore import QObject, pyqtSignal

from .band_presets import BANDS, Band

log = logging.getLogger(__name__)


@dataclass
class SurfStop:
    """A single stop on the auto-surf tour."""
    band_name: str
    freq_hz: int
    level_db: float
    label: Optional[str] = None
    modulation: str = ""


class AutoSurfer(QObject):
    """Background auto-surf scanner.

    Usage:
        surfer = AutoSurfer(gqrx_client, audio_receiver)
        surfer.stop_started.connect(on_stop_started)  # band_name, freq, label
        surfer.surf_progress.connect(on_progress)     # band_index, total_bands
        surfer.surf_finished.connect(on_finished)     # list[SurfStop]
        surfer.start(dwell_seconds=5.0)
    """

    stop_started = pyqtSignal(str, int, str)   # band_name, freq_hz, label
    stop_skipped = pyqtSignal(str, str)        # band_name, reason
    surf_progress = pyqtSignal(int, int)       # band_index, total_bands
    surf_finished = pyqtSignal(list)           # list[SurfStop]
    surf_error = pyqtSignal(str)

    def __init__(self, gqrx_client, parent=None):
        super().__init__(parent)
        self.gqrx = gqrx_client
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._dwell_s: float = 5.0
        self._best_overall: Optional[SurfStop] = None
        self._stops: List[SurfStop] = []

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, dwell_seconds: float = 5.0, bands: Optional[List[Band]] = None) -> bool:
        """Start the auto-surf. Returns True if started, False if already running."""
        if self.is_running:
            return False
        self._stop.clear()
        self._dwell_s = dwell_seconds
        self._bands = bands or list(BANDS)
        self._best_overall = None
        self._stops = []
        self._thread = threading.Thread(target=self._run, daemon=True, name="AutoSurfer")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            self.gqrx.pause_poller()
            total = len(self._bands)
            for i, band in enumerate(self._bands):
                if self._stop.is_set():
                    break
                self.surf_progress.emit(i, total)
                stop = self._surf_band(band)
                if stop is None:
                    self.stop_skipped.emit(band.name, "no signal above -75 dBFS")
                    continue
                self._stops.append(stop)
                # Track best overall
                if (self._best_overall is None
                        or stop.level_db > self._best_overall.level_db):
                    self._best_overall = stop
                # Notify UI we're dwelling on this station
                self.stop_started.emit(stop.band_name, stop.freq_hz,
                                       stop.label or f"{stop.freq_hz/1e6:.4f} MHz")
                # Tune and dwell
                self.gqrx.set_modulation(stop.modulation)
                time.sleep(0.1)
                self.gqrx.set_frequency(stop.freq_hz)
                # Wait the dwell time, checking stop flag every 100 ms
                end = time.time() + self._dwell_s
                while time.time() < end and not self._stop.is_set():
                    time.sleep(0.1)
            self.surf_progress.emit(len(self._bands), len(self._bands))
            # Surf complete — go back to the strongest overall
            if self._best_overall and not self._stop.is_set():
                b = self._best_overall
                self.gqrx.set_modulation(b.modulation)
                time.sleep(0.1)
                self.gqrx.set_frequency(b.freq_hz)
                self.stop_started.emit(b.band_name, b.freq_hz,
                                       (b.label or f"{b.freq_hz/1e6:.4f} MHz") + "  ★ STRONGEST")
            self.surf_finished.emit(self._stops)
        except Exception as e:
            log.exception("Auto-surf error")
            self.surf_error.emit(str(e))
        finally:
            try:
                self.gqrx.resume_poller()
            except Exception:
                pass

    def _surf_band(self, band: Band) -> Optional[SurfStop]:
        """Find the strongest signal in this band. Returns None if no signal."""
        start_hz = int(band.start_mhz * 1e6)
        end_hz = int(band.end_mhz * 1e6)
        step_hz = max(int(band.step_khz * 1e3), 1000)
        # Set modulation first
        try:
            self.gqrx.set_modulation(band.modulation)
            time.sleep(0.1)
        except Exception:
            pass
        best_freq = None
        best_level = -999.0
        # Limit number of samples per band for speed
        max_samples = 60
        n_total = max(1, (end_hz - start_hz) // step_hz + 1)
        if n_total > max_samples:
            # Sample evenly across the band
            samples = [start_hz + int(i * (end_hz - start_hz) / max_samples) for i in range(max_samples)]
        else:
            samples = list(range(start_hz, end_hz + 1, step_hz))
        for f in samples:
            if self._stop.is_set():
                return None
            self.gqrx.set_frequency(f)
            time.sleep(0.08)
            lvl = self.gqrx.get_signal_level_robust(n_samples=2, interval_s=0.03)
            if lvl is None:
                continue
            if lvl > best_level:
                best_level = lvl
                best_freq = f
        if best_freq is None or best_level < -75:
            return None
        # Look up label
        label = band.known.get(best_freq)
        if not label:
            # Try nearby known freqs (within 5 kHz)
            for kf, kn in band.known.items():
                if abs(kf - best_freq) < 5000:
                    label = kn
                    break
        return SurfStop(
            band_name=band.name,
            freq_hz=best_freq,
            level_db=best_level,
            label=label,
            modulation=band.modulation,
        )
