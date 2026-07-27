#!/usr/bin/env python3
"""Test EQ on realistic-level audio (not near full scale).

Real FM audio from Gqrx is typically around -12 to -6 dBFS, not 0 dBFS.
The EQ should produce clearly audible tonal changes without excessive
makeup gain on these signals.
"""
import sys
sys.path.insert(0, '/home/z/my-project')

import numpy as np
from magic_sdr.equalizer import Equalizer, EQ_BANDS_HZ
from magic_sdr.eq_presets import EQ_PRESETS

SR = 48000
DURATION = 1.0
N = int(SR * DURATION)
t = np.arange(N) / SR

# Realistic FM level: -12 dBFS peak (0.25 amplitude)
sig = 0.25 * (0.7 * np.sin(2 * np.pi * 62 * t) + 0.5 * np.sin(2 * np.pi * 1000 * t))
chunk = (sig * 32767).astype(np.int16)

print(f"Realistic-level input (-12 dBFS peak):")
print(f"  max={chunk.max()}, min={chunk.min()}, RMS={np.sqrt(np.mean(chunk.astype(np.float32)**2)):.0f}")
print()

for name in ["Flat", "Bass Boost", "Treble Boost", "Vocal Clarity", "Loudness"]:
    eq = Equalizer(sample_rate=SR, channels=1)
    gains = EQ_PRESETS[name]
    for j, g in enumerate(gains):
        eq.set_band_gain(j, float(g))
    out = eq.process(chunk.copy(), sample_rate=SR)
    
    clipped = np.sum((out >= 32767) | (out <= -32768))
    pct = 100.0 * clipped / len(out)
    
    # Measure 62 Hz and 1 kHz energy
    from numpy.fft import rfft
    def band_e(sig_arr, freq):
        n = len(sig_arr)
        spec = np.abs(rfft(sig_arr * np.hanning(n))) ** 2
        freqs = np.arange(n // 2 + 1) * SR / n
        bw = freq * (2 ** (1/6) - 1)
        mask = (freqs >= freq - bw) & (freqs <= freq + bw)
        return float(np.sqrt(np.sum(spec[mask])))
    
    in_float = chunk.astype(np.float32) / 32768.0
    out_float = out.astype(np.float32) / 32768.0
    bass_in = band_e(in_float, 62)
    bass_out = band_e(out_float, 62)
    mid_in = band_e(in_float, 1000)
    mid_out = band_e(out_float, 1000)
    
    bass_db = 20 * np.log10(bass_out / bass_in) if bass_in > 0 else 0
    mid_db = 20 * np.log10(mid_out / mid_in) if mid_in > 0 else 0
    
    print(f"{name:15s}: clip={pct:5.2f}%  bass(62Hz)={bass_db:+6.2f}dB  mid(1kHz)={mid_db:+6.2f}dB  "
          f"bass/mid ratio={bass_db-mid_db:+6.2f}dB")

print()
print("Expected on -12 dBFS signal:")
print("  Bass Boost:  bass +9 to +12 dB,  mid ~0 dB,  ratio ~+10 to +12 dB")
print("  Treble Boost: bass ~-1 dB,       mid ~+1 dB,  ratio ~-2 dB")
print("  Vocal Clarity: bass ~-2 dB,      mid ~+1 dB,  ratio ~-3 dB")
print("  Loudness:     bass +5 dB,        mid ~-1 dB,  ratio ~+6 dB")
print()
print("If clipping is 0% and the ratios match, the EQ is now AUDIBLE.")
