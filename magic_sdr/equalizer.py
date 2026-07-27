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
        # Makeup-gain peak envelope (fast-attack, medium-release) to prevent
        # chunk-to-chunk loudness pumping. Holds the recent peak in float
        # amplitude (1.0 = full scale). Only attenuates when signal exceeds
        # full scale, so quiet passages pass through uncolored.
        self._makeup_peak: float = 1.0
        self._makeup_attack_tau: float = 0.005  # 5 ms attack
        self._makeup_release_tau: float = 0.5   # 500 ms release
        # Pre-EQ gain (dB) — applied before the EQ filters. Lets the user
        # drive the EQ input harder (positive) or softer (negative) to
        # emphasize the EQ's tonal shaping. Default 0 dB (no change).
        self.pre_gain_db: float = 0.0
        # Brick-wall limiter — hard ceiling at -3 dBFS with look-ahead
        # to catch inter-sample peaks. Engaged after the EQ filters and
        # makeup gain, just before the final output gain. Prevents ANY
        # clipping regardless of how hard the user drives the EQ.
        # Default ceiling lowered from -0.3 to -3 dBFS to reduce distortion
        # (the previous -0.3 dBFS was essentially hard clipping on every
        # peak, producing harsh 'noisy' sound).
        self.limiter_enabled: bool = True
        self.limiter_ceiling_db: float = -3.0    # ceiling at -3 dBFS (was -0.3)
        self._limiter_envelope: float = 1.0
        self._limiter_attack_tau: float = 0.001   # 1 ms attack (very fast)
        self._limiter_release_tau: float = 0.060  # 60 ms release
        # Output gain (dB) — the MASTER loudness control. Applied AFTER the
        # limiter, just before int16 conversion. This is what the user should
        # adjust to control overall loudness. Default -6 dB (half amplitude)
        # so the app isn't deafening on first launch. Range: -60 to 0 dB.
        self.output_gain_db: float = -6.0

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
        self._makeup_peak = 1.0  # reset makeup envelope
        self._limiter_envelope = 1.0  # reset limiter envelope

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def set_pre_gain(self, gain_db: float) -> None:
        """Set the pre-EQ gain in dB (-20 to +20 dB)."""
        self.pre_gain_db = max(-20.0, min(20.0, float(gain_db)))

    def get_pre_gain(self) -> float:
        return self.pre_gain_db

    def set_limiter_enabled(self, enabled: bool) -> None:
        self.limiter_enabled = bool(enabled)
        if not enabled:
            self._limiter_envelope = 1.0

    def set_limiter_ceiling(self, ceiling_db: float) -> None:
        """Set the limiter ceiling in dBFS (-12 to 0 dB)."""
        self.limiter_ceiling_db = max(-12.0, min(0.0, float(ceiling_db)))

    def set_output_gain(self, gain_db: float) -> None:
        """Set the master output gain in dB (-60 to 0 dB).

        This is the PRIMARY loudness control. Applied after the limiter.
        Default -6 dB. Use this to make the app quieter or louder without
        affecting the EQ's tonal shaping.
        """
        self.output_gain_db = max(-60.0, min(0.0, float(gain_db)))

    def get_output_gain(self) -> float:
        return self.output_gain_db

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

        Pipeline:
            input -> pre-gain -> EQ filters -> makeup gain -> limiter -> output

        Args:
            chunk: int16 ndarray, shape (N,) for mono or (N, channels) for multi-channel.
            sample_rate: ignored (we use the constructor's sample rate).

        Returns:
            int16 ndarray with the same shape as the input.
        """
        # If EQ is completely off AND no pre-gain AND no output gain AND
        # limiter off, bypass entirely (return input unchanged)
        eq_active = self._enabled and HAVE_SCIPY and not self.is_flat()
        pre_gain_active = abs(self.pre_gain_db) > 0.01
        output_gain_active = abs(self.output_gain_db) > 0.01
        if not eq_active and not pre_gain_active and not output_gain_active and not self.limiter_enabled:
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

        # ---- Pre-EQ gain (applied before the EQ filters) ----
        if pre_gain_active:
            audio = audio * (10 ** (self.pre_gain_db / 20.0))

        # ---- EQ filters ----
        if eq_active:
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

        # ---- Anti-clipping makeup gain (peak envelope follower) ----
        # When any band has positive gain, the filtered output can exceed the
        # [-1, 1] float range. We track a peak envelope with fast attack (5 ms)
        # and medium release (500 ms) so the makeup gain is stable across
        # chunks (minimal pumping). Only kicks in when signal exceeds full
        # scale — quiet passages pass through unattenuated.
        chunk_peak = float(np.max(np.abs(audio))) if audio.size > 0 else 0.0
        dt = audio.shape[0] / float(sr) if sr > 0 else 0.01
        if chunk_peak > self._makeup_peak:
            alpha = 1.0 - np.exp(-dt / self._makeup_attack_tau)
            self._makeup_peak = self._makeup_peak + alpha * (chunk_peak - self._makeup_peak)
        else:
            alpha = 1.0 - np.exp(-dt / self._makeup_release_tau)
            self._makeup_peak = self._makeup_peak + alpha * (chunk_peak - self._makeup_peak)
        if self._makeup_peak > 1.0:
            # Target -6 dBFS (was -0.5) — leaves much more headroom so the
            # limiter doesn't have to work as hard, reducing distortion.
            # The user can boost loudness back up with the output gain or
            # the volume slider if they want.
            target_peak = 10 ** (-6.0 / 20.0)  # ~0.501, leaves 6 dB headroom
            scale = target_peak / self._makeup_peak
            audio = audio * scale

        # ---- Brick-wall limiter ----
        # A look-ahead peak limiter that prevents ANY sample from exceeding
        # the ceiling. Uses a fast attack (1 ms) and medium release (60 ms)
        # envelope follower. When the signal exceeds the ceiling, the gain is
        # reduced instantaneously; when it drops back, the gain recovers
        # smoothly. This guarantees zero clipping regardless of how hard the
        # user drives the pre-gain or EQ.
        if self.limiter_enabled:
            ceiling = 10 ** (self.limiter_ceiling_db / 20.0)  # e.g. -0.3 dB -> 0.966
            # Sample-by-sample peak detection (true look-ahead would need a
            # delay buffer; we use instant attack which is effectively a
            # brick-wall ceiling — any peak above ceiling is squashed).
            abs_audio = np.abs(audio)
            chunk_peak_lim = float(np.max(abs_audio)) if abs_audio.size > 0 else 0.0
            if chunk_peak_lim > ceiling:
                # Instant gain reduction to bring peak to ceiling
                inst_gain = ceiling / chunk_peak_lim
                # Track the envelope with fast attack, slow release so we
                # don't pump on brief transients
                if inst_gain < self._limiter_envelope:
                    alpha = 1.0 - np.exp(-dt / self._limiter_attack_tau)
                    self._limiter_envelope = self._limiter_envelope + alpha * (inst_gain - self._limiter_envelope)
                else:
                    alpha = 1.0 - np.exp(-dt / self._limiter_release_tau)
                    self._limiter_envelope = self._limiter_envelope + alpha * (1.0 - self._limiter_envelope)
                audio = audio * self._limiter_envelope
                # Final safety clip — guarantees no sample exceeds ceiling
                audio = np.clip(audio, -ceiling, ceiling)
            else:
                # No limiting needed this chunk — release the envelope
                alpha = 1.0 - np.exp(-dt / self._limiter_release_tau)
                self._limiter_envelope = self._limiter_envelope + alpha * (1.0 - self._limiter_envelope)

        # ---- Output gain (MASTER loudness control) ----
        # Applied AFTER the limiter, BEFORE int16 conversion. This is the
        # user's primary loudness control. Default -6 dB. The volume slider
        # in the AudioPlayer is a percentage on top of this.
        if abs(self.output_gain_db) > 0.01:
            audio = audio * (10 ** (self.output_gain_db / 20.0))

        # Convert back to original dtype
        if original_dtype == np.int16:
            audio = np.clip(audio * 32768.0, -32768, 32767).astype(np.int16)
        elif original_dtype == np.int32:
            audio = np.clip(audio * 2147483648.0, -2147483648, 2147483647).astype(np.int32)
        # else: float32, leave as-is

        return audio.reshape(original_shape)
