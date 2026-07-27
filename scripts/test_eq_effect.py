#!/usr/bin/env python3
"""Test that the EQ actually changes the audio signal.

Generates a multi-tone signal (one tone per EQ band) and runs it through
the Equalizer with a strong preset. Measures the per-band gain change
to confirm the filter is actually affecting the audio.
"""
import sys
sys.path.insert(0, '/home/z/my-project')

import numpy as np
from magic_sdr.equalizer import Equalizer, EQ_BANDS_HZ
from magic_sdr.eq_presets import EQ_PRESETS, get_preset_names

SR = 48000
DURATION = 2.0  # seconds
N = int(SR * DURATION)
t = np.arange(N) / SR

# Build a test signal: sum of sine waves at each EQ band center frequency.
# Each tone has equal amplitude (0.1) so we can measure how the EQ changes
# each band's level.
tones = np.zeros(N, dtype=np.float32)
for f in EQ_BANDS_HZ:
    if f < SR / 2:  # below Nyquist
        tones += 0.1 * np.sin(2 * np.pi * f * t).astype(np.float32)

# Convert to int16 (what the EQ receives from the audio receiver)
chunk_int16 = (tones * 32767).astype(np.int16)

print(f"Test signal: {DURATION}s, {SR} Hz, {len(EQ_BANDS_HZ)} tones at band centers")
print(f"Input RMS (overall): {np.sqrt(np.mean(tones**2)):.4f}")
print()

def band_energy(signal_float, freq_hz, sr=SR, q=1.41):
    """Measure energy around freq_hz using a narrow bandpass (Goertzel-ish)."""
    # Use a windowed FFT and sum energy in a band around freq_hz
    from numpy.fft import rfft
    n = len(signal_float)
    spectrum = np.abs(rfft(signal_float * np.hanning(n))) ** 2
    freqs = np.arange(n // 2 + 1) * sr / n
    # Bandwidth: +/- 1/3 octave
    bw = freq_hz * (2 ** (1.0/6.0) - 1)
    mask = (freqs >= freq_hz - bw) & (freqs <= freq_hz + bw)
    return float(np.sqrt(np.sum(spectrum[mask])))

# Measure baseline (no EQ) per-band energy
print(f"{'Band Hz':>8} | {'Input':>10} | ", end="")
for name in ["Flat", "Bass Boost", "Treble Boost", "Vocal Clarity", "Loudness"]:
    print(f"{name[:12]:>12} | ", end="")
print()
print("-" * 90)

baseline = [band_energy(tones, f) for f in EQ_BANDS_HZ]

for i, f in enumerate(EQ_BANDS_HZ):
    print(f"{f:>8} | {baseline[i]:>10.2f} | ", end="")
    for name in ["Flat", "Bass Boost", "Treble Boost", "Vocal Clarity", "Loudness"]:
        # Fresh EQ for each preset
        eq = Equalizer(sample_rate=SR, channels=1)
        gains = EQ_PRESETS[name]
        for j, g in enumerate(gains):
            eq.set_band_gain(j, float(g))
        out = eq.process(chunk_int16.copy(), sample_rate=SR)
        out_float = out.astype(np.float32) / 32768.0
        e = band_energy(out_float, f)
        # Show ratio in dB
        if baseline[i] > 0:
            db = 20 * np.log10(e / baseline[i])
            print(f"{db:>+10.2f}dB | ", end="")
        else:
            print(f"{'n/a':>12} | ", end="")
    print()

print()
print("Expected: Bass Boost should show +dB at 31-125 Hz, ~0 at mids.")
print("Expected: Treble Boost should show +dB at 4k-16k Hz.")
print("Expected: Flat should show ~0 dB everywhere.")
print()
print("If all values are ~0 dB for ALL presets, the EQ is NOT filtering.")
