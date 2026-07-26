"""EQ presets — 16 named profiles for the 10-band HiFi equalizer.

Each preset is a tuple of 10 gains (dB), one per ISO band:
    (31, 62, 125, 250, 500, 1k, 2k, 4k, 8k, 16k) Hz

Presets are inspired by classic audio engineering curves:
  - Flat:        no coloration
  - Loudness:    Fletcher-Munson V-shape (boosted lows + highs)
  - Bass Boost:  +12 dB at 31-62 Hz, gentle taper
  - Treble:      crisp highs, gentle bass cut
  - Vocal:       presence boost at 2-4 kHz, dip at 250 Hz
  - Speech:      narrow midrange boost for news/talk intelligibility
  - Music:       slight V, warm bass + airy highs
  - AM Vintage:  band-limited 200 Hz - 4 kHz (old radio color)
  - FM HiFi:     gentle loudness, de-emphasized sibilance
  - Shortwave:   narrow 300-2400 Hz (SSB/CW clarity)
  - Aviation:    mid-treble boost for ATC voice clarity
  - Cinematic:   sub-bass boost, slight vocal dip, airy top
  - Open Air:    outdoor summer concert feel
  - Tube Warm:   mid-bass bloom, rolled-off highs
  - Crisp Modern: scooped mids, bright top
  - Headphone:   Harman-ish curve for headphone listening

EQ preset operations support saving/loading the user's current EQ
state as a custom "My Preset" that survives app restart.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

# The 10 ISO band frequencies (must match equalizer.EQ_BANDS_HZ)
EQ_BANDS_HZ: Tuple[int, ...] = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)

# Gains in dB, in band order: 31, 62, 125, 250, 500, 1k, 2k, 4k, 8k, 16k
EQ_PRESETS: Dict[str, Tuple[float, ...]] = {
    "Flat":            ( 0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0),

    # Classic Fletcher-Munson loudness contour — boosts extremes so quiet
    # listening sounds fuller. Great for low-volume FM listening.
    "Loudness":        (+8.0, +6.0, +4.0, +1.0, -1.0, -1.0,  0.0, +2.0, +5.0, +7.0),

    # Big bottom end for EDM / hip-hop / talk-radio callers with deep voices.
    "Bass Boost":      (+12.0, +10.0, +7.0, +3.0,  0.0,  0.0,  0.0,  0.0, +1.0, +2.0),

    # Bright, detailed top — useful for cutting through road noise when
    # listening to SDR via speakers.
    "Treble Boost":    (-1.0, -1.0,  0.0,  0.0,  0.0, +1.0, +3.0, +6.0, +9.0, +11.0),

    # Midrange presence boost for voices. Pulls 250 Hz down to reduce
    # "muddiness", lifts 2-4 kHz for intelligibility.
    "Vocal Clarity":   (-1.0, -1.0, -2.0, -3.0, -1.0, +1.0, +4.0, +5.0, +3.0, +1.0),

    # Narrowband speech curve — what news/talk AM radio wants to be.
    # Cuts low rumble and high hiss, lifts the speech band.
    "Speech / News":   (-6.0, -6.0, -4.0, -1.0, +2.0, +4.0, +4.0, +2.0,  0.0, -3.0),

    # Slight V — warm bass + airy top, mids slightly scooped. Good for
    # general music listening.
    "Music (V)":       (+4.0, +3.0, +2.0,  0.0, -1.0, -1.0,  0.0, +1.0, +3.0, +4.0),

    # Old AM tube-radio coloration — band-limited, mid-forward, with a
    # gentle rolloff above 4 kHz. Pair with the AM band for vintage feel.
    "AM Vintage":      (-6.0, -4.0,  0.0, +3.0, +4.0, +4.0, +3.0, +1.0, -3.0, -8.0),

    # Gentle loudness for FM stereo — slight bass, slight top, de-emphasized
    # sibilance around 6-8 kHz (pulls 8k down a hair to tame tape/MPX hiss).
    "FM HiFi":         (+3.0, +2.0, +1.0,  0.0,  0.0,  0.0,  0.0, +1.0,  0.0, +2.0),

    # Narrow 300-2400 Hz — for shortwave SSB/CW clarity. Cuts the lows
    # that just rumble and the highs that just hiss on SW.
    "Shortwave / SSB": (-8.0, -8.0, -6.0, -2.0, +2.0, +3.0, +2.0, -2.0, -8.0, -10.0),

    # Aviation AM voice lives at 300-3000 Hz. Boost the upper mids for
    # ATC clarity over engine noise.
    "Aviation ATC":    (-5.0, -5.0, -3.0,  0.0, +2.0, +4.0, +5.0, +3.0,  0.0, -3.0),

    # Cinematic — sub-bass lift for thunder/soundtracks, slight vocal dip
    # so dialogue doesn't fight the score, airy top for sparkle.
    "Cinematic":       (+10.0, +7.0, +3.0,  0.0, -1.0, -1.0,  0.0, +2.0, +4.0, +6.0),

    # Open-air outdoor sound — slight mid scoop so the music doesn't feel
    # "stuffy" outdoors, with a gentle top-end lift.
    "Open Air":        (+3.0, +2.0,  0.0, -1.0, -1.0,  0.0, +1.0, +2.0, +4.0, +5.0),

    # Warm tube color — mid-bass bloom, highs rolled off gently. Pairs
    # nicely with AM Vintage or as a "warm" FM listen.
    "Tube Warm":       (+2.0, +4.0, +5.0, +3.0, +1.0,  0.0, -1.0, -2.0, -3.0, -5.0),

    # Modern pop curve — scooped mids, bright top, controlled bottom.
    "Crisp Modern":    (+5.0, +3.0, +1.0, -1.0, -2.0, -1.0,  0.0, +2.0, +5.0, +8.0),

    # Harman-curve-ish for headphones — bass shelf, gentle dip in mids,
    # air peak above 10 kHz. Best for headphone listening.
    "Headphone":       (+6.0, +5.0, +3.0, +1.0, -1.0, -1.0, +1.0, +2.0, +4.0, +5.0),
}


def get_preset_names() -> List[str]:
    """Return a sorted list of preset names (Flat first, then alphabetical)."""
    names = list(EQ_PRESETS.keys())
    # Move "Flat" to the front, keep the rest alphabetical
    if "Flat" in names:
        names.remove("Flat")
        names.sort()
        names.insert(0, "Flat")
    else:
        names.sort()
    return names


def get_preset_gains(name: str) -> Tuple[float, ...]:
    """Return the 10 gains for a named preset.

    Raises KeyError if the preset doesn't exist.
    """
    return EQ_PRESETS[name]


def find_closest_preset(current_gains: List[float]) -> str:
    """Return the name of the preset whose gains are closest to the
    current slider values (Euclidean distance). Useful for highlighting
    which preset the user is closest to when they manually adjust sliders.
    """
    if not current_gains:
        return "Flat"
    best_name = "Flat"
    best_dist = float("inf")
    for name, gains in EQ_PRESETS.items():
        dist = sum((a - b) ** 2 for a, b in zip(gains, current_gains))
        if dist < best_dist:
            best_dist = dist
            best_name = name
    return best_name
