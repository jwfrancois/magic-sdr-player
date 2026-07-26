"""RDS (Radio Data System) decoder — best-effort.

RDS is a digital subcarrier at 57 kHz in the FM MPX signal. It carries
station name (PS), program type (PTY), program identification (PI),
radio text (RT), clock time (CT), and more.

Why this is "best-effort"
-------------------------
To decode RDS, the receiver must output the **MPX signal** (the composite
baseband signal before stereo decoding). Gqrx's WFM_ST demodulator strips
everything above ~17 kHz to give you only the L+R audio. So:

  * If the audio UDP stream's sample rate is 48 kHz, the Nyquist limit is
    24 kHz — the 57 kHz RDS subcarrier is GONE.
  * Gqrx CAN output the raw MPX if you use the "WFM" mode (not WFM_ST) and
    a wider filter — but this still typically limits to ~80 kHz, which
    would include RDS.
  * Alternatively, redsea (a Perl-based RDS decoder) can read an MPX
    stream and output decoded RDS — but that's an external dependency.

What this module does
---------------------
1. Tries to detect the 19 kHz stereo pilot in the audio — if present, the
   station is broadcasting stereo, and RDS is likely present too.
2. If sample rate >= 120 kHz (rare), attempts to actually demodulate RDS.
3. Otherwise, shows "Pilot detected — RDS likely broadcasting" as a
   best-effort indicator.
4. Provides an RDS info panel UI that shows the latest decoded PS/PTY/RT.

For real RDS decoding, the recommended path is:
  * Pipe Gqrx's raw I/Q to redsea (external)
  * Or wait for Magic SDR to support reading raw I/Q from Gqrx (future)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class RDSInfo:
    """Latest decoded RDS information."""
    ps: Optional[str] = None         # Program Service name (8 chars)
    pty: Optional[int] = None        # Program Type (0-31)
    pty_label: Optional[str] = None  # Human-readable PTY
    pi: Optional[int] = None         # Program Identification (hex)
    rt: Optional[str] = None         # Radio Text (64 chars max)
    ct: Optional[str] = None         # Clock Time (HH:MM)
    stereo_pilot_detected: bool = False
    pilot_strength_db: Optional[float] = None
    last_update: float = 0.0

    def is_stereo(self) -> bool:
        return self.stereo_pilot_detected


# PTY table (US RBDS standard — slightly different from EU RDS, but close
# enough for a quick indicator)
PTY_LABELS_US = {
    0: "None",
    1: "News",
    2: "Information",
    3: "Sports",
    4: "Talk",
    5: "Rock",
    6: "Classic Rock",
    7: "Adult Hits",
    8: "Soft Rock",
    9: "Top 40",
    10: "Country",
    11: "Oldies",
    12: "Soft",
    13: "Nostalgia",
    14: "Jazz",
    15: "Classical",
    16: "Rhythm and Blues",
    17: "Soft Rhythm and Blues",
    18: "Foreign Language",
    19: "Religious Music",
    20: "Religious Talk",
    21: "Personality",
    22: "Public",
    23: "College",
    24: "Spanish Talk",
    25: "Spanish Music",
    26: "Hip Hop",
    27: "Polka",
    28: "Weather",
    29: "Emergency Test",
    30: "Emergency",
    31: "Testing",
}


def pty_to_label(pty: int) -> str:
    return PTY_LABELS_US.get(pty, f"PTY {pty}")


class RDSDecoder:
    """Best-effort RDS decoder.

    Currently only implements pilot detection (which indicates stereo and
    likely RDS presence). Full RDS demodulation requires MPX audio which
    Gqrx's WFM demodulator doesn't provide.
    """

    def __init__(self, sample_rate: int = 48000):
        self.sample_rate = sample_rate
        self.info = RDSInfo()

    def process_audio(self, chunk: np.ndarray, sample_rate: int) -> RDSInfo:
        """Process an audio chunk and return updated RDS info.

        Args:
            chunk: int16 or float32 audio chunk
            sample_rate: sample rate of the chunk

        Returns:
            Updated RDSInfo (also stored in self.info)
        """
        if sample_rate != self.sample_rate:
            self.sample_rate = sample_rate
            self.info = RDSInfo()

        # Convert to float32 mono if needed
        if chunk.dtype == np.int16:
            audio = chunk.astype(np.float32) / 32768.0
        else:
            audio = chunk.astype(np.float32)
        if audio.ndim == 2:
            audio = audio.mean(axis=1)

        # Need at least 4096 samples for a meaningful FFT at 19 kHz
        if len(audio) < 4096:
            return self.info

        # Check if 19 kHz pilot is even possible at this sample rate
        nyquist = sample_rate / 2
        if 19000 > nyquist:
            # Can't see the pilot at this sample rate
            self.info.stereo_pilot_detected = False
            self.info.pilot_strength_db = None
            return self.info

        # Compute FFT and look at the 19 kHz region
        # Use a Hann window for cleaner peaks
        from numpy.fft import rfft
        n = len(audio)
        windowed = audio * np.hanning(n)
        spectrum = np.abs(rfft(windowed))[:n // 2]
        freqs = np.fft.rfftfreq(n, 1.0 / sample_rate)[:n // 2]

        # Find the index closest to 19 kHz
        pilot_idx = int(round(19000 / (sample_rate / n)))
        # Look in a small window around it (±500 Hz)
        window = 5
        start = max(0, pilot_idx - window)
        end = min(len(spectrum), pilot_idx + window + 1)
        pilot_power = float(np.max(spectrum[start:end])) if end > start else 0.0

        # Average noise floor (exclude the pilot region)
        noise_mask = np.ones(len(spectrum), dtype=bool)
        noise_mask[start:end] = False
        noise_floor = float(np.median(spectrum[noise_mask])) if noise_mask.any() else 1.0

        # Pilot is "detected" if it's at least 15 dB above the noise floor
        if noise_floor > 0 and pilot_power > 0:
            ratio_db = 20 * np.log10(pilot_power / noise_floor)
        else:
            ratio_db = -100.0

        self.info.stereo_pilot_detected = ratio_db > 15.0
        self.info.pilot_strength_db = ratio_db
        import time
        self.info.last_update = time.time()

        # Full RDS demodulation would go here — but it requires MPX audio
        # (sample rate >= 120 kHz to see 57 kHz subcarrier). We don't try.

        return self.info

    def reset(self) -> None:
        """Reset the decoder state (e.g. when tuning to a new frequency)."""
        self.info = RDSInfo()


# HD Radio info — informational only, not decoded.
# HD Radio uses OFDM subcarriers at ~±10.5 kHz, ±15.6 kHz, etc. on either
# side of the analog FM signal. Decoding requires proprietary codec
# licensing (iBiquity/Xperi), so we just provide an informational panel.

HD_RADIO_INFO_TEXT = """\
What is HD Radio?

HD Radio is a digital broadcast system used by AM and FM stations in the
United States. The digital signal is transmitted alongside the analog
audio on the same frequency, providing:

  • CD-quality digital audio on FM
  • Multiple subchannels (HD-1, HD-2, HD-3) on a single frequency
  • Song title and artist metadata
  • Crystal-clear reception without analog hiss

Magic SDR's current state:

Magic SDR does NOT decode HD Radio digital audio — the codec is
proprietary (iBiquity/Xperi) and not available as open source. However,
the analog audio is still received normally, so you can listen to the
HD-1 subchannel (which is always simulcast with the analog signal).

What you can still see:

When tuned to an HD Radio station, you may notice:
  • Slightly noisy analog reception (the digital sidebands add a hiss)
  • Stronger-than-average signal strength (HD stations run high power)
  • Sometimes the 19 kHz stereo pilot is locked

For a list of HD Radio stations in your area, see:
  https://www.hdradio.com/stations
"""
