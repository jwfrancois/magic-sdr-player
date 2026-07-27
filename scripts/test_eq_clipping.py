#!/usr/bin/env python3
"""Check for clipping in the EQ output.

With +12 dB bass boost, a loud bass-heavy signal can exceed int16 range.
When we clip to [-32768, 32767], the waveform flattens — which can make
the EQ sound like it has NO effect (because everything is just clipped
to the same square wave).

This test drives the EQ with a realistic loud signal and checks how
often clipping occurs.
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

# Simulate a loud FM signal — near full-scale int16
# Mix of bass (62 Hz) and mids (1 kHz) at near-full amplitude
sig = 0.7 * np.sin(2 * np.pi * 62 * t) + 0.5 * np.sin(2 * np.pi * 1000 * t)
chunk = (sig * 32767 * 0.9).astype(np.int16)  # near full scale

print(f"Input: max={chunk.max()}, min={chunk.min()}, dtype={chunk.dtype}")
print(f"Input RMS: {np.sqrt(np.mean(chunk.astype(np.float32)**2)):.1f}")
print()

for name in ["Flat", "Bass Boost", "Loudness", "Treble Boost"]:
    eq = Equalizer(sample_rate=SR, channels=1)
    gains = EQ_PRESETS[name]
    for j, g in enumerate(gains):
        eq.set_band_gain(j, float(g))
    out = eq.process(chunk.copy(), sample_rate=SR)
    
    # Count clipped samples
    clipped_high = np.sum(out >= 32767)
    clipped_low = np.sum(out <= -32768)
    clipped = clipped_high + clipped_low
    pct = 100.0 * clipped / len(out)
    
    print(f"{name:15s}: max={out.max():>6}, min={out.min():>6}, "
          f"clipped={clipped:>5}/{len(out)} ({pct:5.2f}%), "
          f"RMS={np.sqrt(np.mean(out.astype(np.float32)**2)):.1f}")

print()
print("If Bass Boost shows >5% clipping, the EQ IS working but the output")
print("is being smashed into square waves — which sounds like distortion,")
print("not a bass boost. The fix is to apply headroom/attenuation before")
print("clipping, OR to scale the output to prevent clipping.")
