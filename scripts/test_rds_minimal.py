#!/usr/bin/env python3
"""Minimal RDS demod test — check if mixing to baseband works."""
import sys
sys.path.insert(0, '/home/z/my-project')

import numpy as np
from magic_sdr.rds import RDS_SUBCARRIER_HZ, RDS_BIT_RATE

SR = 192000

# Generate a simple BPSK signal at 57 kHz with known bits
# Bit pattern: 10101010 (alternating) at 1187.5 bps
bits = [1, 0, 1, 0, 1, 0, 1, 0] * 100  # 800 bits
diff_bits = []
last = 0
for b in bits:
    d = b ^ last
    diff_bits.append(d)
    last = b

samples_per_bit = SR / RDS_BIT_RATE
print(f"Samples per bit: {samples_per_bit}")

# Build signal with continuous phase
signal = []
sample_idx = 0
for bit in diff_bits:
    phase = np.pi if bit else 0
    next_idx = sample_idx + samples_per_bit
    n_samples = int(round(next_idx)) - int(round(sample_idx))
    for s in range(n_samples):
        time_s = (int(round(sample_idx)) + s) / SR
        sample = np.cos(2 * np.pi * RDS_SUBCARRIER_HZ * time_s + phase)
        signal.append(sample)
    sample_idx = next_idx

signal = np.array(signal, dtype=np.float32)
print(f"Signal: {len(signal)} samples")

# Check the spectrum — should have a peak at 57 kHz
from numpy.fft import rfft
spectrum = np.abs(rfft(signal[:8192]))
freqs = np.fft.rfftfreq(8192, 1/SR)
peak_idx = np.argmax(spectrum)
print(f"Spectrum peak at: {freqs[peak_idx]:.1f} Hz (expected 57000)")

# Now mix down
t = np.arange(len(signal)) / SR
mixer = np.cos(2 * np.pi * RDS_SUBCARRIER_HZ * t)
mixed = signal * mixer

# Check mixed spectrum — should have peak at 0 Hz (baseband) and 114 kHz
spectrum_mixed = np.abs(rfft(mixed[:8192]))
peak_idx_mixed = np.argmax(spectrum_mixed)
print(f"After mixing, spectrum peak at: {freqs[peak_idx_mixed]:.1f} Hz (expected ~0)")

# Lowpass to get baseband
from scipy.signal import firwin, lfilter
lp_cutoff = 1500 / (SR / 2)
lp_b = firwin(127, lp_cutoff)
baseband = lfilter(lp_b, np.array([1.0]), mixed)

print(f"\nBaseband range: [{np.min(baseband):.4f}, {np.max(baseband):.4f}]")
print(f"Baseband mean: {np.mean(baseband):.6f}")

# The baseband should be a square wave at 1187.5/2 = 593.75 Hz
# (since we're alternating 1010, the bit transitions happen at 1187.5 Hz,
#  so the fundamental is at half that)
spectrum_bb = np.abs(rfft(baseband[1000:9000]))
freqs_bb = np.fft.rfftfreq(8000, 1/SR)
peak_idx_bb = np.argmax(spectrum_bb[1:]) + 1  # skip DC
print(f"Baseband spectrum peak at: {freqs_bb[peak_idx_bb]:.1f} Hz (expected ~594 Hz for 1010 pattern)")

# Sample the baseband
# Try sampling at every samples_per_bit
print("\nSampling baseband at bit rate:")
for phase in [0, 40, 80, 120, 160]:
    samples = []
    idx = phase
    while idx < len(baseband):
        samples.append(baseband[idx])
        idx += int(samples_per_bit)
    # First 10 samples
    print(f"  phase={phase}: {' '.join(f'{s:+.4f}' for s in samples[:10])}")

# The issue: when we mix cos(2*pi*57k*t + phase) * cos(2*pi*57k*t),
# we get 0.5*cos(phase) + 0.5*cos(2*pi*114k*t + phase)
# The baseband term is 0.5*cos(phase), which is constant for a given phase.
# So for bit=0 (phase=0), baseband = +0.5
# For bit=1 (phase=pi), baseband = -0.5
# This SHOULD work. Let's check what we're actually getting.

print("\n=== Direct bit recovery (no filter) ===")
# Sample the mixed signal (before lowpass) at bit centers
# Use a simple integrator: average the mixed signal over each bit period
bits_recovered = []
idx = 0
bit_num = 0
while idx < len(mixed) and bit_num < 20:
    end_idx = idx + int(samples_per_bit)
    if end_idx > len(mixed):
        break
    bit_avg = np.mean(mixed[idx:end_idx])
    bits_recovered.append(1 if bit_avg > 0 else 0)
    idx = end_idx
    bit_num += 1

# Differential decode
diff_recovered = []
last = 0
for b in bits_recovered:
    d = b ^ last
    diff_recovered.append(d)
    last = b

print(f"  Recovered raw bits:  {''.join(str(b) for b in bits_recovered[:20])}")
print(f"  Recovered diff bits: {''.join(str(b) for b in diff_recovered[:20])}")
print(f"  Expected bits:       {''.join(str(b) for b in bits[:20])}")
