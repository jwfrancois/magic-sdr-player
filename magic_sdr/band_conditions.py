"""HF band conditions estimator.

Estimates the propagation conditions on each HF ham band based on the
current solar conditions (solar flux, K-index, X-ray class) and the
time of day at the user's location.

This is a heuristic estimator — not a substitute for a real propagation
prediction tool like VOACAP or ITURHF. But it gives the user a quick
"what bands are open right now" view.

Bands covered:
  160m (1.8 MHz)   — night only, low K-index
  80m  (3.5 MHz)   — day: short; night: long
  60m  (5.3 MHz)   — day/night transition band
  40m  (7.0 MHz)   — day: regional; night: DX
  30m  (10.1 MHz)  — day: regional/DX; night: regional
  20m  (14.0 MHz)  — day: DX; night: short (the workhorse band)
  17m  (18.1 MHz)  — daytime DX
  15m  (21.0 MHz)  — daytime DX, needs high solar flux
  12m  (24.9 MHz)  — daytime DX, needs very high solar flux
  10m  (28.0 MHz)  — daytime, needs high solar flux, low K-index

Conditions rating:
  ★★★★★ Excellent — open for DX
  ★★★★☆ Good — open for regional + some DX
  ★★★☆☆ Fair — short-range only
  ★★☆☆☆ Poor — barely usable
  ★☆☆☆☆ Closed — don't bother
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from .solar import SolarConditions


@dataclass
class BandCondition:
    """Condition of a single HF band."""
    band: str                # "20m"
    freq_mhz: float          # 14.0
    rating: int              # 1-5 (stars)
    label: str               # "Excellent", "Good", "Fair", "Poor", "Closed"
    note: str                # human-readable note about why


def _is_daytime() -> bool:
    """Return True if it's daytime at UTC (rough approximation)."""
    # We use UTC hour as a rough proxy. Real propagation depends on the
    # path's midpoint local time, but for a quick estimator this is fine.
    hour = datetime.now(timezone.utc).hour
    return 6 <= hour <= 18


def estimate_band_conditions(solar: Optional[SolarConditions]) -> List[BandCondition]:
    """Estimate conditions on all HF bands.

    Returns a list of BandCondition objects, one per band.
    """
    conditions: List[BandCondition] = []
    day = _is_daytime()

    # Pull solar values
    sfi = solar.solar_flux if solar and solar.solar_flux is not None else 100.0
    k = solar.k_index if solar and solar.k_index is not None else 3
    xray = solar.xray_class if solar and solar.xray_class is not None else "B"

    # K-index penalty: high K = absorption, especially at high latitudes
    # X-ray penalty: M and X class flares cause shortwave blackouts on day side
    xray_penalty = 0
    if xray == "M":
        xray_penalty = 1 if day else 0
    elif xray == "X":
        xray_penalty = 3 if day else 0
    elif xray == "C":
        xray_penalty = 0  # C-class is normal background

    # SFI bonus: high solar flux = better F-layer ionization = better HF
    sfi_bonus = 0
    if sfi >= 200:
        sfi_bonus = 2
    elif sfi >= 150:
        sfi_bonus = 1
    elif sfi < 70:
        sfi_bonus = -1  # low SFI = poor F-layer

    # ---- 160m ----
    if not day and k <= 3:
        r, lbl, note = 4, "Good", "Night band; low noise if K is low"
    elif not day:
        r, lbl, note = 2, "Poor", "Night but K-index too high for low bands"
    else:
        r, lbl, note = 1, "Closed", "160m is a night band"
    r = max(1, r - xray_penalty)
    conditions.append(BandCondition("160m", 1.8, r, lbl, note))

    # ---- 80m ----
    if not day:
        r, lbl, note = 4, "Good", "Night: DX possible; day: regional only"
    else:
        r, lbl, note = 3, "Fair", "Day: regional NVIS contacts; better at night"
    r = max(1, r - max(0, k - 3) - xray_penalty)
    conditions.append(BandCondition("80m", 3.5, r, lbl, note))

    # ---- 60m ----
    r, lbl, note = 3, "Fair", "Transition band; usable day or night"
    r = max(1, r - max(0, k - 4))
    conditions.append(BandCondition("60m", 5.3, r, lbl, note))

    # ---- 40m ----
    if day:
        r, lbl, note = 4, "Good", "Day: regional; night: DX"
    else:
        r, lbl, note = 5, "Excellent", "Night: prime DX band"
    r = max(1, r - max(0, k - 3) - xray_penalty)
    conditions.append(BandCondition("40m", 7.0, r, lbl, note))

    # ---- 30m ----
    r, lbl, note = 4, "Good", "Day: regional+DX; night: regional"
    r = max(1, r - max(0, k - 4) - xray_penalty)
    conditions.append(BandCondition("30m", 10.1, r, lbl, note))

    # ---- 20m ----
    if day:
        r, lbl, note = 5, "Excellent", "Day: prime DX band"
    else:
        r, lbl, note = 3, "Fair", "Night: short-range; closes for DX"
    r = max(1, r - max(0, k - 4) - xray_penalty + sfi_bonus)
    conditions.append(BandCondition("20m", 14.0, r, lbl, note))

    # ---- 17m ----
    if day:
        r, lbl, note = 4 + sfi_bonus, "Good", "Day: DX when SFI is high"
    else:
        r, lbl, note = 2, "Poor", "Usually closes at night"
    r = max(1, min(5, r - max(0, k - 4) - xray_penalty))
    conditions.append(BandCondition("17m", 18.1, r, lbl, note))

    # ---- 15m ----
    if day and sfi >= 120:
        r, lbl, note = 4, "Good", "Day: DX with decent solar flux"
    elif day:
        r, lbl, note = 2, "Poor", f"Day but SFI {sfi:.0f} too low"
    else:
        r, lbl, note = 1, "Closed", "Night: closed"
    r = max(1, r - max(0, k - 4) - xray_penalty + sfi_bonus)
    conditions.append(BandCondition("15m", 21.0, r, lbl, note))

    # ---- 12m ----
    if day and sfi >= 150:
        r, lbl, note = 4, "Good", "Day: DX with high solar flux"
    elif day:
        r, lbl, note = 2, "Poor", f"Day but SFI {sfi:.0f} too low for 12m"
    else:
        r, lbl, note = 1, "Closed", "Night: closed"
    r = max(1, r - max(0, k - 4) - xray_penalty + sfi_bonus)
    conditions.append(BandCondition("12m", 24.9, r, lbl, note))

    # ---- 10m ----
    if day and sfi >= 150 and k <= 3:
        r, lbl, note = 5, "Excellent", "Day: prime band when SFI high + K low"
    elif day and sfi >= 120:
        r, lbl, note = 3, "Fair", "Day: spotty openings"
    elif day:
        r, lbl, note = 2, "Poor", f"Day but SFI {sfi:.0f} too low"
    else:
        r, lbl, note = 1, "Closed", "Night: closed"
    r = max(1, r - max(0, k - 3) - xray_penalty + sfi_bonus)
    conditions.append(BandCondition("10m", 28.0, r, lbl, note))

    return conditions


def rating_to_stars(rating: int) -> str:
    """Convert a 1-5 rating to a star string."""
    return "★" * rating + "☆" * (5 - rating)


def band_color(rating: int) -> str:
    """Return a CSS color string for a rating (1-5)."""
    colors = {
        5: "#3aaa55",  # green
        4: "#88aa55",  # yellow-green
        3: "#cccc44",  # yellow
        2: "#cc8844",  # orange
        1: "#cc4444",  # red
    }
    return colors.get(rating, "#888888")
