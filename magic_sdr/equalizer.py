"""HiFi multi-band equalizer.

A 10-band graphic EQ (31, 62, 125, 250, 500, 1k, 2k, 4k, 8k, 16k Hz) using
scipy.signal's IIR filter design. The EQ processes audio in real time
before playback, so it affects both desktop and web audio.

Implementation notes
--------------------
* We use a single ``scipy.signal.iirfilter`` per band to design a
  peak/notch biquad. Each band's filter is a second-order IIR.
* The filters are applied via ``scipy.signal.lfilter`` on each incoming
  audio chunk. State is carried between chunks so there are no clicks
  at chunk boundaries.
* Latency: ~1 chunk (~10 ms at 480 Hz / 1024-sample chunks). Acceptable.
* When all bands are at 0 dB, we bypass the filter entirely (no CPU).
* A pass-through EQ panel with vertical sliders is provided for the UI.

If scipy is unavailable, the EQ gracefully degrades to pass-through
(no filtering, but the UI still works and shows the controls).
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)

try:
    from scipy.signal import iirfilter, lfilter, lfilter_zi
    HAVE_SCIPY = True
except ImportError:
    HAVE_SCIPY = False
    log.warning("scipy not available — EQ will pass audio through unfiltered.")

# Standard 10-band ISO frequencies (Hz)
EQ_BANDS_HZ: Tuple[int, ...] = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)

# Default gains (dB) — flat
DEFAULT_GAINS_DB: Tuple[float, ...] = (0.0,) * len(EQ_BANDS_HZ)

# Q factor for each band — determines bandwidth. ~1.41 = 2/3 octave.
EQ_Q = 1.41


class Equalizer:
    """10-band graphic equalizer applied to int16 PCM audio chunks.

    Usage:
        eq = Equalizer(sample_rate=48000)
        eq.set_band_gain(2, +3.0)  # +3 dB at 125 Hz
        processed = eq.process(chunk_int16, sample_rate=48000)
        # processed is also int16 PCM
    """

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        self.sample_rate = sample_rate
        self.channels = channels
        self.gains_db: List[float] = list(DEFAULT_GAINS_DB)
        # Per-band filter state (zi) — kept between calls so the filter is
        # continuous across chunk boundaries. Each band has its own state.
        # Shape: channels × 2 (for a 2nd-order filter)
        self._zi: List[Optional[np.ndarray]] = [None] * len(EQ_BANDS_HZ)
        # Cached filter coefficients (recomputed only when gain changes)
        self._b_a: List[Optional[Tuple[np.ndarray, np.ndarray]]] = [None] * len(EQ_BANDS_HZ)
        self._enabled = True

    def set_band_gain(self, band_index: int, gain_db: float) -> None:
        """Set the gain (in dB) of a band."""
        if not (0 <= band_index < len(self.gains_db)):
            raise IndexError(f"band_index {band_index} out of range")
        gain_db = max(-20.0, min(20.0, float(gain_db)))
        if abs(self.gains_db[band_index] - gain_db) < 0.01:
            return
        self.gains_db[band_index] = gain_db
        # Invalidate the cached filter for this band
        self._b_a[band_index] = None
        # Reset state for this band (changing the filter changes the optimal zi)
        self._zi[band_index] = None

    def get_band_gain(self, band_index: int) -> float:
        return self.gains_db[band_index]

    def set_all_gains(self, gains_db: List[float]) -> None:
        """Set all band gains at once."""
        if len(gains_db) != len(self.gains_db):
            raise ValueError(f"Need {len(self.gains_db)} gains, got {len(gains_db)}")
        for i, g in enumerate(gains_db):
            self.set_band_gain(i, g)

    def reset(self) -> None:
        """Reset all gains to 0 dB (flat)."""
        for i in range(len(self.gains_db)):
            self.set_band_gain(i, 0.0)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def is_flat(self) -> bool:
        """Return True if all bands are at 0 dB."""
        return all(abs(g) < 0.01 for g in self.gains_db)

    def _design_filter(self, band_index: int) -> Tuple[np.ndarray, np.ndarray]:
        """Design a peaking EQ filter for the given band."""
        from scipy.signal import iirfilter
        freq_hz = EQ_BANDS_HZ[band_index]
        gain_db = self.gains_db[band_index]
        # Peaking EQ design via bilinear transform of analog RLC peaker.
        # We use scipy.signal.iirfilter with a peak design — but scipy doesn't
        # have a direct "peaking EQ" filter, so we use iirfilter with btype='band'
        # and a narrow bandwidth. This gives a notch/peak response.
        # A better approach is the standard RBJ biquad peaking EQ:
        #   https://www.musicdsp.org/en/latest/Filters/197-rbj-audio-eq-cookbook.html
        A = 10 ** (gain_db / 40.0)  # gain linear, half because peaking EQ
        w0 = 2 * np.pi * freq_hz / self.sample_rate
        if w0 >= np.pi:  # frequency above Nyquist
            return np.array([1.0, 0.0, 0.0]), np.array([1.0, 0.0, 0.0])
        cos_w0 = np.cos(w0)
        sin_w0 = np.sin(w0)
        alpha = sin_w0 / (2 * EQ_Q)
        b0 = 1 + alpha * A
        b1 = -2 * cos_w0
        b2 = 1 - alpha * A
        a0 = 1 + alpha / A
        a1 = -2 * cos_w0
        a2 = 1 - alpha / A
        b = np.array([b0, b1, b2]) / a0
        a = np.array([1.0, a1 / a0, a2 / a0])
        return b, a

    def process(self, chunk: np.ndarray, sample_rate: Optional[int] = None) -> np.ndarray:
        """Apply the EQ to an audio chunk.

        Args:
            chunk: int16 ndarray, shape (N,) for mono or (N, channels) for multi-channel.
            sample_rate: ignored (we use the constructor's sample rate).

        Returns:
            int16 ndarray with the same shape as the input.
        """
        if not self._enabled or not HAVE_SCIPY or self.is_flat():
            return chunk
        sr = sample_rate or self.sample_rate
        if sr != self.sample_rate:
            # Sample rate changed — invalidate all cached filters and states
            self.sample_rate = sr
            self._b_a = [None] * len(EQ_BANDS_HZ)
            self._zi = [None] * len(EQ_BANDS_HZ)

        # Convert to float32 for processing
        original_shape = chunk.shape
        original_dtype = chunk.dtype
        if chunk.dtype == np.int16:
            audio = chunk.astype(np.float32) / 32768.0
        elif chunk.dtype == np.int32:
            audio = chunk.astype(np.float32) / 2147483648.0
        elif chunk.dtype == np.float32:
            audio = chunk.copy()
        else:
            audio = chunk.astype(np.float32)

        # Handle multi-channel: process each channel separately
        if audio.ndim == 1:
            audio = audio[:, np.newaxis]
            squeeze_back = True
        else:
            squeeze_back = False

        n_channels = audio.shape[1]

        for band_idx in range(len(EQ_BANDS_HZ)):
            if abs(self.gains_db[band_idx]) < 0.01:
                continue  # skip bands at 0 dB
            # Design filter if needed
            if self._b_a[band_idx] is None:
                self._b_a[band_idx] = self._design_filter(band_idx)
                # Reset state because filter coeffs changed
                self._zi[band_idx] = None
            b, a = self._b_a[band_idx]
            # Initialize state if needed.
            # NOTE: scipy's lfilter_zi returns the steady-state response to a
            # STEP input (constant 1.0), which is the right initial condition
            # for DC signals but WRONG for zero-centered audio. Using lfilter_zi
            # as the initial state for an oscillating audio signal creates a
            # transient that decays over ~100 ms and artificially reduces the
            # measured gain during that period.
            #
            # For zero-centered audio, the correct initial state is simply
            # zeros — the filter will reach steady-state within a few cycles
            # of the input signal.
            from scipy.signal import lfilter
            if self._zi[band_idx] is None or self._zi[band_idx].shape[1] != n_channels:
                self._zi[band_idx] = np.zeros((2, n_channels), dtype=np.float64)
            # Process each channel
            for ch in range(n_channels):
                filtered, new_zi = lfilter(
                    b, a, audio[:, ch], zi=self._zi[band_idx][:, ch]
                )
                audio[:, ch] = filtered
                self._zi[band_idx][:, ch] = new_zi

        if squeeze_back:
            audio = audio[:, 0]

        # Convert back to original dtype
        if original_dtype == np.int16:
            audio = np.clip(audio * 32768.0, -32768, 32767).astype(np.int16)
        elif original_dtype == np.int32:
            audio = np.clip(audio * 2147483648.0, -2147483648, 2147483647).astype(np.int32)
        # else: float32, leave as-is

        return audio.reshape(original_shape)
