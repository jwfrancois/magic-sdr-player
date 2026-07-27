"""Solar conditions fetcher.

Fetches the current space-weather data from NOAA's Space Weather Prediction
Center (SWPC) public JSON API. Used to:

  * Show the user the current solar flux (F10.7), sunspot number, A-index,
    K-index, X-ray flux class.
  * Drive the band-conditions estimator (band_conditions.py).

Endpoints used (all public, no API key required):

  * https://services.swpc.noaa.gov/json/wwv.json
      → current solar flux, A-index, K-index, sunspot number, updated every
        24h. Pulled from WWV broadcast.
  * https://services.swpc.noaa.gov/json/goes/primary/xrays-6-hour.json
      → GOES X-ray flux (last 6 hours, 1-min cadence). We extract the most
        recent reading to get the current X-ray class (A/B/C/M/X).
  * https://services.swpc.noaa.gov/json/planetary_k_index_1m.json
      → recent planetary K-index (last 24h, 3-hour cadence).

We use urllib (in stdlib) so we don't add a dependency on requests.
A background thread fetches every 30 minutes and caches the result.
The UI polls the cached result via ``get_current()``.

If the network is unavailable, we gracefully return None for everything
and the UI shows "—".
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

# Endpoints
URL_WWV = "https://services.swpc.noaa.gov/json/wwv.json"
URL_XRAY = "https://services.swpc.noaa.gov/json/goes/primary/xrays-6-hour.json"
URL_KINDEX = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"

# How often to refresh (seconds)
REFRESH_INTERVAL = 30 * 60  # 30 minutes
REQUEST_TIMEOUT = 10  # seconds

# User-Agent — NOAA asks for a descriptive UA
USER_AGENT = "Magic-SDR-Player/1.0 (educational SDR app)"


@dataclass
class SolarConditions:
    """Snapshot of current solar conditions."""
    # F10.7 solar flux index (sfu)
    solar_flux: Optional[float] = None
    # Sunspot number
    sunspot_number: Optional[int] = None
    # Planetary A-index (daily)
    a_index: Optional[float] = None
    # Planetary K-index (3-hourly, 0-9)
    k_index: Optional[int] = None
    # Current X-ray class letter: A, B, C, M, X
    xray_class: Optional[str] = None
    # Current X-ray flux in W/m²
    xray_flux: Optional[float] = None
    # Timestamp of the data (unix)
    timestamp: float = field(default_factory=time.time)
    # Human-readable message from NOAA (e.g. "Solar activity is low")
    message: Optional[str] = None

    @property
    def is_storm(self) -> bool:
        """True if geomagnetic storm conditions (K >= 5)."""
        return self.k_index is not None and self.k_index >= 5

    @property
    def is_quiet(self) -> bool:
        """True if conditions are quiet (K <= 2)."""
        return self.k_index is not None and self.k_index <= 2

    def summary(self) -> str:
        """Short human-readable summary."""
        parts = []
        if self.solar_flux is not None:
            parts.append(f"SFI {self.solar_flux:.0f}")
        if self.sunspot_number is not None:
            parts.append(f"SSN {self.sunspot_number}")
        if self.a_index is not None:
            parts.append(f"A {self.a_index:.0f}")
        if self.k_index is not None:
            parts.append(f"K {self.k_index}")
        if self.xray_class is not None:
            parts.append(f"X-ray {self.xray_class}")
        if not parts:
            return "—"
        return "  ".join(parts)


class SolarFetcher:
    """Background thread that fetches solar data and caches it."""

    def __init__(self, refresh_interval: int = REFRESH_INTERVAL):
        self._refresh_interval = refresh_interval
        self._cache: Optional[SolarConditions] = None
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_fetch_time: float = 0.0
        self._last_error: Optional[str] = None

    def start(self) -> None:
        """Start the background fetcher."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="SolarFetcher")
        self._thread.start()

    def stop(self) -> None:
        """Stop the background fetcher."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        # Fetch immediately on start
        self._fetch_once()
        while not self._stop.wait(self._refresh_interval):
            self._fetch_once()

    def _fetch_once(self) -> None:
        try:
            conditions = self._fetch_all()
            with self._lock:
                self._cache = conditions
                self._last_fetch_time = time.time()
                self._last_error = None
            log.info("Solar conditions updated: %s", conditions.summary())
        except Exception as e:
            with self._lock:
                self._last_error = str(e)
            log.warning("Solar fetch failed: %s", e)

    def _fetch_json(self, url: str) -> Optional[dict]:
        """Fetch a JSON endpoint with timeout + UA. Returns None on failure."""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                data = resp.read()
                return json.loads(data)
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
            log.debug("Failed to fetch %s: %s", url, e)
            return None

    def _fetch_all(self) -> SolarConditions:
        """Fetch all endpoints and merge into a single SolarConditions."""
        cond = SolarConditions()

        # 1. WWV data (solar flux, sunspots, A-index, K-index, message)
        wwv = self._fetch_json(URL_WWV)
        if wwv:
            # The WWV JSON is a single object, not a list
            cond.solar_flux = _safe_float(wwv.get("solarflux"))
            cond.sunspot_number = _safe_int(wwv.get("sunspots"))
            cond.a_index = _safe_float(wwv.get("aindex"))
            cond.k_index = _safe_int(wwv.get("kindex"))
            # The message field has a complex multi-line format; we'll just
            # take the first line.
            msg = wwv.get("message", "")
            if msg:
                cond.message = msg.split("\n")[0][:200]

        # 2. X-ray flux (current reading = last entry)
        xray = self._fetch_json(URL_XRAY)
        if xray and isinstance(xray, list) and len(xray) > 0:
            # Each entry: {"time_tag": "...", "flux": ..., "satellite": ...,
            #               "observed_class": "B5.2", ...}
            # Sort by time_tag and take the latest
            try:
                latest = max(xray, key=lambda e: e.get("time_tag", ""))
                cond.xray_flux = _safe_float(latest.get("flux"))
                cls = latest.get("observed_class") or latest.get("class")
                if cls and isinstance(cls, str) and len(cls) > 0:
                    cond.xray_class = cls[0].upper()  # "B5.2" → "B"
            except (KeyError, ValueError):
                pass

        # 3. K-index (most recent reading — usually already in WWV, but use this as backup)
        if cond.k_index is None:
            kdata = self._fetch_json(URL_KINDEX)
            if kdata and isinstance(kdata, list) and len(kdata) > 0:
                try:
                    latest_k = max(kdata, key=lambda e: e.get("time_tag", ""))
                    cond.k_index = _safe_int(latest_k.get("kp"))
                except (KeyError, ValueError):
                    pass

        return cond

    def get_current(self) -> Optional[SolarConditions]:
        """Return the cached conditions, or None if not yet fetched."""
        with self._lock:
            return self._cache

    def force_refresh(self) -> None:
        """Trigger an immediate refresh (non-blocking)."""
        t = threading.Thread(target=self._fetch_once, daemon=True, name="SolarRefresh")
        t.start()

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    @property
    def last_fetch_time(self) -> float:
        with self._lock:
            return self._last_fetch_time


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return f if f == f else None  # NaN check
    except (ValueError, TypeError):
        return None


def _safe_int(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        f = _safe_float(v)
        return int(f) if f is not None else None
