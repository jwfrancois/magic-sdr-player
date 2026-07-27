#!/usr/bin/env python3
"""Minimal test: is the BPSK signal generation correct?"""
import sys
sys.path.insert(0, '/home/z/my-project')

import numpy as np
from magic_sdr.rds import RDS_SUBCARRIER_HZ, RDS_BIT_RATE

SR = 192000

# Generate a simple 57 kHz carrier
t = np.arange(1000) / SR
carrier = np.cos(2 * np.pi * RDS_SUBCARRIER_HZ * t)
print(f"57 kHz carrier, first 5 samples: {carrier[:5]}")
print(f"Period: {1/RDS_SUBCARRIER_HZ*1e6:.2f} us = {SR/RDS_SUBCARRIER_HZ:.2f} samples")

# Now: differential demod of a pure carrier (no modulation)
# signal * delayed_signal should be ~constant positive
delay = int(SR / RDS_BIT_RATE)  # 1 bit period
print(f"Delay: {delay} samples")

# Longer carrier
t = np.arange(2000) / SR
sig = np.cos(2 * np.pi * RDS_SUBCARRIER_HZ * t)
delayed = np.zeros_like(sig)
delayed[delay:] = sig[:-delay]
product = sig * delayed
print(f"\nPure carrier demod: mean={np.mean(product[delay:]):.4f} (should be ~+0.5)")

# Now flip the phase halfway through (simulating a bit transition)
sig2 = np.cos(2 * np.pi * RDS_SUBCARRIER_HZ * t)
sig2[1000:] = np.cos(2 * np.pi * RDS_SUBCARRIER_HZ * t[1000:] + np.pi)
delayed2 = np.zeros_like(sig2)
delayed2[delay:] = sig2[:-delay]
product2 = sig2 * delayed2
print(f"Phase-flip demod: mean={np.mean(product2[delay:1000]):.4f} (before flip, ~+0.5)")
print(f"Phase-flip demod: mean={np.mean(product2[1000+delay:]):.4f} (after flip, ~-0.5)")
print(f"Phase-flip demod: mean={np.mean(product2[1000:1000+delay]):.4f} (during transition)")

# The key question: what's the 57 kHz period in samples?
period_samples = SR / RDS_SUBCARRIER_HZ
print(f"\n57 kHz period: {period_samples:.2f} samples")
print(f"Delay (1 bit): {delay} samples")
print(f"Delay / period: {delay / period_samples:.4f}")
print(f"Is delay an integer multiple of period? {delay % period_samples:.4f}")

# If the delay is NOT an integer multiple of the carrier period,
# the differential demod will not work! The carrier phase will be wrong.
# 57 kHz period = 192000/57000 = 3.368 samples (not integer!)
# So we can't do a simple sample-delay demod.
# We need either:
#   1. A PLL to track the carrier phase, or
#   2. Mix down to baseband first (which is what I tried before)

print("\n=== Conclusion ===")
print(f"57 kHz period is {period_samples:.3f} samples (non-integer)")
print("Simple delay-line demod won't work. Must mix down to baseband.")
print("The mix-down approach should work if the filters are right.")

# Let's try the mix-down approach with NO filters (clean signal)
print("\n=== Mix-down with NO filters ===")
bits_test = [0, 1, 0, 1, 0, 1, 0, 1] * 10  # 80 bits
# Differential encode
diff = []
last = 0
for b in bits_test:
    d = b ^ last
    diff.append(d)
    last = b

samples_per_bit = SR / RDS_BIT_RATE
sig3 = np.array([], dtype=np.float32)
phase = 0.0
for bit in diff:
    if bit:
        phase += np.pi
    n = int(samples_per_bit)
    t = np.arange(n) / SR
    chunk = np.cos(2 * np.pi * RDS_SUBCARRIER_HZ * t + phase)
    sig3 = np.concatenate([sig3, chunk.astype(np.float32)])

# Mix down
t = np.arange(len(sig3)) / SR
mixed = sig3 * np.cos(2 * np.pi * RDS_SUBCARRIER_HZ * t)
# The product should be 0.5*cos(phase_diff) + 0.5*cos(2*57k*t + ...)
# The baseband part is 0.5*cos(phase_diff) = +0.5 for same phase, -0.5 for flipped

# Average over each bit period (integrate-and-dump)
spb = int(samples_per_bit)
bits_recovered = []
for i in range(len(mixed) // spb):
    integral = np.mean(mixed[i*spb:(i+1)*spb])
    bits_recovered.append(1 if integral < 0 else 0)  # negative = phase flipped = bit 1

print(f"Test bits:          {''.join(str(b) for b in bits_test[:20])}")
print(f"Diff-encoded:      {''.join(str(b) for b in diff[:20])}")
print(f"Recovered (inverted): {''.join(str(b) for b in bits_recovered[:20])}")
# Note: the demod gives us the DIFFERENTIAL bits directly. We need to
# un-differential them: original = recovered XOR previous recovered
undiff = []
last = 0
for b in bits_recovered:
    d = b ^ last
    undiff.append(d)
    last = b
print(f"Un-diff'd:          {''.join(str(b) for b in undiff[:20])}")
matches = sum(1 for a, b in zip(undiff[:20], bits_test[:20]) if a == b)
print(f"Matches: {matches}/20")
