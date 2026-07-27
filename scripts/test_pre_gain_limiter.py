#!/usr/bin/env python3
"""Test pre-EQ gain and brick-wall limiter."""
import sys
sys.path.insert(0, '/home/z/my-project')

import numpy as np
from magic_sdr.equalizer import Equalizer

SR = 48000
DURATION = 1.0
N = int(SR * DURATION)
t = np.arange(N) / SR

# Test signal: loud sine at -6 dBFS (0.5 amplitude)
sig = 0.5 * np.sin(2 * np.pi * 440 * t)
chunk = (sig * 32767).astype(np.int16)

print(f"Input: peak={np.abs(chunk).max()} ({20*np.log10(np.abs(chunk).max()/32768):.2f} dBFS)")
print()

# Test 1: Pre-gain boosts the signal, limiter prevents clipping
print("=== Test 1: +12 dB pre-gain, limiter ON ===")
eq = Equalizer(sample_rate=SR, channels=1)
eq.set_pre_gain(12.0)
eq.set_limiter_enabled(True)
out = eq.process(chunk.copy(), sample_rate=SR)
peak = np.abs(out).max()
print(f"  Output peak: {peak} ({20*np.log10(peak/32768):.2f} dBFS)")
print(f"  Clipped samples: {np.sum((out >= 32767) | (out <= -32768))}/{len(out)}")
print(f"  Expected: ~-0.3 dBFS (limiter ceiling), 0 clipped")
print()

# Test 2: +12 dB pre-gain, limiter OFF — should clip heavily
print("=== Test 2: +12 dB pre-gain, limiter OFF ===")
eq2 = Equalizer(sample_rate=SR, channels=1)
eq2.set_pre_gain(12.0)
eq2.set_limiter_enabled(False)
out2 = eq2.process(chunk.copy(), sample_rate=SR)
peak2 = np.abs(out2).max()
clipped2 = np.sum((out2 >= 32767) | (out2 <= -32768))
print(f"  Output peak: {peak2} ({20*np.log10(peak2/32768):.2f} dBFS)")
print(f"  Clipped samples: {clipped2}/{len(out2)} ({100*clipped2/len(out2):.1f}%)")
print(f"  Expected: 32767 (0 dBFS), heavy clipping")
print()

# Test 3: Pre-gain + EQ + limiter all together
print("=== Test 3: +6 dB pre-gain + Bass Boost + limiter ON ===")
eq3 = Equalizer(sample_rate=SR, channels=1)
eq3.set_pre_gain(6.0)
for i, g in enumerate([12, 10, 7, 3, 0, 0, 0, 0, 1, 2]):
    eq3.set_band_gain(i, float(g))
eq3.set_limiter_enabled(True)
out3 = eq3.process(chunk.copy(), sample_rate=SR)
peak3 = np.abs(out3).max()
clipped3 = np.sum((out3 >= 32767) | (out3 <= -32768))
print(f"  Output peak: {peak3} ({20*np.log10(peak3/32768):.2f} dBFS)")
print(f"  Clipped samples: {clipped3}/{len(out3)}")
print(f"  Expected: ~-0.3 dBFS, 0 clipped (limiter catches everything)")
print()

# Test 4: -6 dB pre-gain attenuates
print("=== Test 4: -6 dB pre-gain, flat EQ, limiter ON ===")
eq4 = Equalizer(sample_rate=SR, channels=1)
eq4.set_pre_gain(-6.0)
eq4.set_limiter_enabled(True)
out4 = eq4.process(chunk.copy(), sample_rate=SR)
peak4 = np.abs(out4).max()
print(f"  Output peak: {peak4} ({20*np.log10(peak4/32768):.2f} dBFS)")
print(f"  Expected: ~-12 dBFS (input -6 dBFS + pre-gain -6 dB)")
print()

# Test 5: Limiter ceiling adjustment
print("=== Test 5: Limiter ceiling at -6 dBFS ===")
eq5 = Equalizer(sample_rate=SR, channels=1)
eq5.set_pre_gain(12.0)
eq5.set_limiter_enabled(True)
eq5.set_limiter_ceiling(-6.0)
out5 = eq5.process(chunk.copy(), sample_rate=SR)
peak5 = np.abs(out5).max()
print(f"  Output peak: {peak5} ({20*np.log10(peak5/32768):.2f} dBFS)")
print(f"  Expected: ~-6 dBFS (limiter ceiling)")
print()

print("=== All pre-gain + limiter tests done ===")
