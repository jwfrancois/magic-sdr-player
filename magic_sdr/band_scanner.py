"""Auto-discovery band scanner.

The BandScanner sweeps a target band at a given step size, dwells briefly on
each frequency, measures the signal level (via Gqrx's signal level command),
and reports frequencies that exceed a configurable threshold.

For each active frequency found, it:
  1. Looks it up in the known-channels table for that band (auto-label).
  2. If unknown, optionally invokes the AI tagger to classify it.
  3. Optionally records a few seconds for later review.

Runs in a background thread so the GUI stays responsive. Emits Qt signals
as it discovers stations and when the scan completes.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional, List

from PyQt5.QtCore import QObject, pyqtSignal

from .gqrx_client import GqrxClient
from .band_presets import Band, BANDS, BANDS_BY_NAME, lookup_known, guess_modulation

log = logging.getLogger(__name__)


class DiscoveredStation:
    """A station found by the scanner."""
    def __init__(self, freq_hz: int, level_db: float, band: Band,
                 label: Optional[str] = None, ai_tag: Optional[str] = None,
                 modulation: Optional[str] = None):
        self.freq_hz = freq_hz
        self.level_db = level_db
        self.band = band
        self.label = label or lookup_known(freq_hz)
        self.ai_tag = ai_tag
        self.modulation = modulation or band.modulation
        self.discovered_at = time.time()

    def to_dict(self) -> dict:
        return {
            "freq_hz": self.freq_hz,
            "freq_mhz": self.freq_hz / 1e6,
            "level_db": round(self.level_db, 1),
            "band": self.band.name,
            "label": self.label,
            "ai_tag": self.ai_tag,
            "modulation": self.modulation,
            "discovered_at": self.discovered_at,
        }


class BandScanner(QObject):
    """Background scanner that finds active frequencies in a band."""

    # Signals
    scan_started = pyqtSignal(str)            # band name
    scan_progress = pyqtSignal(float)         # 0..1
    scan_progress_freq = pyqtSignal(int)      # current freq being checked
    station_found = pyqtSignal(object)        # DiscoveredStation
    scan_finished = pyqtSignal(str, list)     # band name, list[DiscoveredStation]
    scan_error = pyqtSignal(str)

    def __init__(self, gqrx: GqrxClient, parent=None):
        super().__init__(parent)
        self.gqrx = gqrx
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._running = False
        self.threshold_db = -45.0
        self.dwell_s = 0.25
        self.ai_tagger = None  # injected later, optional

    def is_running(self) -> bool:
        return self._running

    def stop(self) -> None:
        self._stop.set()

    def scan_band(self, band: Band) -> bool:
        """Start scanning the given band. Returns False if already scanning."""
        if self._running:
            return False
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._scan_loop, args=(band,), daemon=True, name="BandScanner"
        )
        self._thread.start()
        return True

    def scan_band_by_name(self, band_name: str) -> bool:
        band = BANDS_BY_NAME.get(band_name)
        if not band:
            self.scan_error.emit(f"Unknown band: {band_name}")
            return False
        return self.scan_band(band)

    def scan_all_bands(self) -> bool:
        """Scan every supported band, one after another."""
        if self._running:
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._scan_all_loop, daemon=True,
                                        name="BandScannerAll")
        self._thread.start()
        return True

    def _scan_all_loop(self) -> None:
        for band in BANDS:
            if self._stop.is_set():
                break
            self._scan_loop(band)
            if self._stop.is_set():
                break
            time.sleep(0.5)  # brief pause between bands

    def _scan_loop(self, band: Band) -> None:
        self._running = True
        self.scan_started.emit(band.name)
        found: List[DiscoveredStation] = []
        try:
            if not self.gqrx.is_connected():
                self.scan_error.emit("Gqrx not connected")
                return
            start_hz = int(band.start_mhz * 1e6)
            end_hz = int(band.end_mhz * 1e6)
            step_hz = int(band.step_khz * 1e3)
            n_steps = max(1, (end_hz - start_hz) // step_hz + 1)
            # Set modulation once for the whole band
            self.gqrx.set_modulation(band.modulation)
            time.sleep(0.1)
            for i, f in enumerate(range(start_hz, end_hz + 1, step_hz)):
                if self._stop.is_set():
                    break
                self.scan_progress_freq.emit(f)
                self.scan_progress.emit(i / n_steps)
                if not self.gqrx.set_frequency(f):
                    continue
                time.sleep(self.dwell_s)
                lvl = self.gqrx.get_signal_level()
                if lvl is None:
                    continue
                if lvl >= self.threshold_db:
                    st = DiscoveredStation(freq_hz=f, level_db=lvl, band=band,
                                           modulation=band.modulation)
                    # Optionally AI-tag
                    if self.ai_tagger is not None:
                        try:
                            st.ai_tag = self.ai_tagger.classify_sync(f, band)
                        except Exception as e:
                            log.debug("AI tagger failed for %d: %s", f, e)
                    self.station_found.emit(st)
                    found.append(st)
            self.scan_progress.emit(1.0)
            self.scan_finished.emit(band.name, found)
        except Exception as e:
            log.exception("Scan failed for %s", band.name)
            self.scan_error.emit(f"Scan failed: {e}")
        finally:
            self._running = False
