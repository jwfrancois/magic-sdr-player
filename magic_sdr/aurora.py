"""Aurora forecast — estimates aurora visibility from current K-index + latitude.

Aurora visible from a given latitude depends on the planetary K-index (Kp):
  • Kp = 3 → visible from magnetic latitude ~67° (normal auroral oval)
  • Kp = 5 (G1 storm) → visible down to ~62° (e.g. southern Canada)
  • Kp = 6 (G2 storm) → visible down to ~56° (e.g. Seattle, UK)
  • Kp = 7 (G3 storm) → visible down to ~50° (e.g. Pacific NW, central Europe)
  • Kp = 8 (G4 storm) → visible down to ~45° (e.g. Baltimore at 39° is on the edge)
  • Kp = 9 (G5 storm) → visible down to ~40° or lower (Baltimore can see aurora!)

Aurora matters for radio because:
  • Aurora absorbs HF radio (D-layer ionization) — HF can disappear entirely.
  • Aurora reflects VHF (50-500 MHz) — causes "auroral scatter" / "auroral
    echo" modes that hams chase on 6m and 2m. Voice sounds "raspy" / whispery.
  • Aurora indicates CME activity, which correlates with solar flares.

Baltimore, MD is at geographic latitude 39.3°N. Its *magnetic* latitude
(in the geomagnetic coordinate system used for aurora) is closer to ~50°N
because the magnetic north pole is in northern Canada.

For UI: we report:
  • Aurora oval latitude (the equatorward edge of the auroral oval)
  • Whether aurora is "likely visible" from a given observer latitude
  • Aurora's effect on HF (absorption) and VHF (scatter potential)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class AuroraForecast:
    """Aurora forecast based on the current K-index."""
    kp_index: Optional[int]                  # planetary K-index (0-9)
    oval_latitude: Optional[float]           # equatorward edge of auroral oval
    observer_latitude: float                 # observer's magnetic latitude
    visible_from_observer: bool              # is aurora overhead-or-visible?
    storm_class: str                         # "Quiet", "G1", "G2", ..., "G5"
    hf_absorption: str                       # effect on HF: "None", "Minor", "Major", "Blackout"
    vhf_scatter: str                         # effect on VHF: "None", "Possible", "Strong"

    def summary(self) -> str:
        if self.kp_index is None:
            return "—"
        parts = [
            f"Kp {self.kp_index} ({self.storm_class})",
            f"oval at {self.oval_latitude:.0f}°" if self.oval_latitude else "oval position unknown",
        ]
        if self.visible_from_observer:
            parts.append("✓ VISIBLE FROM YOU")
        else:
            parts.append(f"not visible from {self.observer_latitude:.0f}°")
        return "  ".join(parts)


def storm_class_for_kp(kp: Optional[int]) -> str:
    """Return the G-scale storm class for a Kp index."""
    if kp is None:
        return "—"
    if kp <= 4:
        return "Quiet"
    if kp == 5:
        return "G1 (Minor)"
    if kp == 6:
        return "G2 (Moderate)"
    if kp == 7:
        return "G3 (Strong)"
    if kp == 8:
        return "G4 (Severe)"
    return "G5 (Extreme)"


def oval_latitude_for_kp(kp: Optional[int]) -> Optional[float]:
    """Return the equatorward edge latitude of the auroral oval for a Kp index.

    Based on the standard NOAA ovation model approximation:
      oval_lat ≈ 67 - 2*kp  (very rough, but good enough for a status display)

    So:
      Kp=0 → 67°, Kp=3 → 61°, Kp=5 → 57°, Kp=7 → 53°, Kp=9 → 49°
    """
    if kp is None:
        return None
    return max(40.0, 67.0 - 2.0 * kp)


def hf_absorption_for_kp(kp: Optional[int]) -> str:
    """Effect of geomagnetic activity on HF propagation."""
    if kp is None:
        return "—"
    if kp <= 3:
        return "None — normal HF"
    if kp <= 4:
        return "Minor — slight attenuation at high latitudes"
    if kp <= 5:
        return "Moderate — high-latitude paths degraded"
    if kp <= 6:
        return "Significant — HF blackouts at high latitudes"
    if kp <= 7:
        return "Major — widespread HF absorption"
    return "Blackout — HF may be unusable for hours"


def vhf_scatter_for_kp(kp: Optional[int]) -> str:
    """Effect of aurora on VHF (the positive side — auroral scatter is a mode!)."""
    if kp is None:
        return "—"
    if kp <= 4:
        return "None"
    if kp <= 5:
        return "Possible — 6m/2m auroral scatter at high latitudes"
    if kp <= 6:
        return "Likely — 6m/2m auroral scatter, raspy signals"
    if kp <= 7:
        return "Strong — 6m/2m/70cm auroral scatter possible"
    return "Excellent — major auroral event, multi-band VHF scatter"


def forecast_aurora(kp_index: Optional[int], observer_latitude: float = 50.0) -> AuroraForecast:
    """Generate an aurora forecast.

    Args:
        kp_index: current planetary K-index (0-9), or None
        observer_latitude: observer's MAGNETIC latitude (default ~50° for Baltimore MD)

    Returns:
        AuroraForecast dataclass
    """
    oval_lat = oval_latitude_for_kp(kp_index)
    # Aurora is visible if the observer's latitude is at or poleward of the
    # oval's equatorward edge, minus a 5° buffer for the fact that aurora
    # can be seen on the horizon from ~5° equatorward of the oval edge.
    visible = (oval_lat is not None) and (observer_latitude >= oval_lat - 5.0)
    return AuroraForecast(
        kp_index=kp_index,
        oval_latitude=oval_lat,
        observer_latitude=observer_latitude,
        visible_from_observer=visible,
        storm_class=storm_class_for_kp(kp_index),
        hf_absorption=hf_absorption_for_kp(kp_index),
        vhf_scatter=vhf_scatter_for_kp(kp_index),
    )
