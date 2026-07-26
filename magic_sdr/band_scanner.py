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

IMPORTANT — understanding Gqrx's signal level values:
  Gqrx's `l STRENGTH` command returns dBFS (decibels relative to full scale),
  NOT dBm. This is a unitless ratio where 0 dBFS = full ADC scale.

  Typical values you'll see:
    -30 to -50 dBFS: very strong local FM broadcast (or AGC limiting)
    -50 to -65 dBFS: normal FM station, ATC, NOAA
    -65 to -80 dBFS: weak but audible stations
    -80 to -120 dBFS: noise floor / no signal

  The default threshold of -80 dB will catch anything above the noise floor.
  If you get 0 stations, your threshold is too strict (or there's no antenna,
  or Gqrx's gain is too low). Use the `calibrate_noise_floor()` method to
  measure the local noise floor and set threshold accordingly.
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
    scan_progress_level = pyqtSignal(int, float)  # freq_hz, level_db (for live diagnostic)
    station_found = pyqtSignal(object)        # DiscoveredStation
    scan_finished = pyqtSignal(str, list)     # band name, list[DiscoveredStation]
    scan_error = pyqtSignal(str)
    noise_floor_calibrated = pyqtSignal(float)  # measured noise floor dBFS

    def __init__(self, gqrx: GqrxClient, parent=None):
        super().__init__(parent)
        self.gqrx = gqrx
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._running = False
        # -80 dBFS catches everything above noise floor; adjust if too noisy.
        self.threshold_db = -80.0
        self.dwell_s = 0.5
        self.ai_tagger = None  # injected later, optional
        # When True, auto-calibrate the noise floor before each scan and set
        # the threshold to noise_floor + 10 dB. Helps a lot when the user
        # hasn't tuned the threshold manually.
        self.auto_threshold = True
        self._measured_noise_floor: Optional[float] = None
        # Min signal delta above noise floor to count as a station
        self.auto_threshold_margin_db = 10.0

    @property
    def measured_noise_floor(self) -> Optional[float]:
        return self._measured_noise_floor

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

    def calibrate_noise_floor(self, band: Optional[Band] = None,
                              n_samples: int = 10) -> Optional[float]:
        """Measure the noise floor in (or near) the given band.

        Picks `n_samples` frequencies evenly spaced across the band, tunes to
        each, reads the signal level, and returns the median. This becomes
        the baseline; stations must be `auto_threshold_margin_db` above it.

        Returns the measured noise floor in dBFS, or None on failure.
        """
        if not self.gqrx.is_connected():
            return None
        if band is None:
            # Default to FM broadcast band (88–108 MHz)
            band = BANDS_BY_NAME.get("FM Broadcast") or BANDS[0]
        try:
            self.gqrx.set_modulation(band.modulation)
            time.sleep(0.1)
            start_hz = int(band.start_mhz * 1e6)
            end_hz = int(band.end_mhz * 1e6)
            if n_samples < 2:
                n_samples = 2
            step = (end_hz - start_hz) // (n_samples - 1) if n_samples > 1 else 0
            levels: List[float] = []
            for i in range(n_samples):
                if self._stop.is_set():
                    break
                f = start_hz + i * step
                if not self.gqrx.set_frequency(f):
                    continue
                time.sleep(max(0.2, self.dwell_s * 0.6))
                lvl = self.gqrx.get_signal_level()
                if lvl is not None:
                    levels.append(lvl)
            if not levels:
                return None
            levels.sort()
            median = levels[len(levels) // 2]
            self._measured_noise_floor = median
            log.info("Noise floor calibrated: %.1f dBFS (from %d samples)", median, len(levels))
            self.noise_floor_calibrated.emit(median)
            return median
        except Exception as e:
            log.warning("Noise floor calibration failed: %s", e)
            return None

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

            # Auto-calibrate noise floor if enabled — this is the key fix
            # for "0 stations found" when the user's threshold is wrong.
            effective_threshold = self.threshold_db
            if self.auto_threshold:
                nf = self.calibrate_noise_floor(band)
                if nf is not None:
                    effective_threshold = nf + self.auto_threshold_margin_db
                    log.info("Auto threshold for %s: %.1f dBFS (noise %.1f + margin %.1f)",
                             band.name, effective_threshold, nf, self.auto_threshold_margin_db)

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
                # Always emit level for live diagnostic — useful even if
                # nothing exceeds threshold, you can see what's coming in.
                self.scan_progress_level.emit(f, lvl)
                if lvl >= effective_threshold:
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
