#!/usr/bin/env python3
"""Debug pilot-coherent RDS demodulation step by step."""
import sys
sys.path.insert(0, '/home/z/my-project')

import numpy as np
from typing import List
from magic_sdr.rds import RDS_SUBCARRIER_HZ, RDS_BIT_RATE, OFFSET_WORDS, RDSBlockDecoder

SR = 192000

def compute_check_word(data_16: int, offset_word: int) -> int:
    g = 0x5B9
    word = data_16 << 10
    for i in range(16):
        if word & (1 << (25 - i)):
            word ^= g << (25 - i - 10)
    check = (word & 0x3FF) ^ offset_word
    return check

def make_block(data_16: int, offset_name: str) -> List[int]:
    offset = OFFSET_WORDS[offset_name]
    check = compute_check_word(data_16, offset)
    word = (data_16 << 10) | check
    bits = []
    for i in range(25, -1, -1):
        bits.append((word >> i) & 1)
    return bits

# Simple test: 2 bits, bit=0 then bit=1
# Build a short signal with pilot + RDS subcarrier
spb = int(round(SR / RDS_BIT_RATE))
n_bits = 100
total_samples = n_bits * spb + 1000
t = np.arange(total_samples) / SR

# Pilot
pilot = 0.1 * np.cos(2 * np.pi * 19000 * t)

# RDS with known bits: alternating 0,1
test_bits = [0, 1, 0, 1, 0, 1, 0, 1] * (n_bits // 8)
rds_phase = np.zeros(total_samples)
sample_idx = 0
current_phase = 0.0
for bit in test_bits:
    if bit:
        current_phase += np.pi
    end_idx = sample_idx + spb
    if end_idx > total_samples:
        break
    rds_phase[sample_idx:end_idx] = current_phase
    sample_idx = end_idx

rds_sub = 0.05 * np.cos(2 * np.pi * RDS_SUBCARRIER_HZ * t + rds_phase)
mpx = pilot + rds_sub

print(f"Pilot amplitude: {np.max(np.abs(pilot)):.4f}")
print(f"RDS subcarrier amplitude: {np.max(np.abs(rds_sub)):.4f}")

# Now demodulate step by step
from scipy.signal import firwin, lfilter

# 1. Bandpass around 57 kHz
bpf_b = firwin(129, [(RDS_SUBCARRIER_HZ - 2400)/(SR/2), (RDS_SUBCARRIER_HZ + 2400)/(SR/2)], pass_zero=False)
filtered = lfilter(bpf_b, np.array([1.0]), mpx)
print(f"\nAfter BPF: max={np.max(np.abs(filtered)):.4f}")

# 2. Extract pilot
pilot_bpf = firwin(255, [(19000-200)/(SR/2), (19000+200)/(SR/2)], pass_zero=False)
pilot_extracted = lfilter(pilot_bpf, np.array([1.0]), mpx)
print(f"Pilot extracted: max={np.max(np.abs(pilot_extracted)):.4f}")

# 3. Cube the pilot to get 57 kHz reference
pilot_cubed = pilot_extracted ** 3
print(f"Pilot cubed: max={np.max(np.abs(pilot_cubed)):.6f}")

# Check spectrum of pilot_cubed — should have peak at 57 kHz
from numpy.fft import rfft
spec_pc = np.abs(rfft(pilot_cubed[2000:]))
freqs_pc = np.fft.rfftfreq(len(pilot_cubed[2000:]), 1/SR)
peak_idx = np.argmax(spec_pc[1:]) + 1
print(f"Pilot cubed spectrum peak: {freqs_pc[peak_idx]:.0f} Hz (expected 57000)")

# 4. Mix filtered RDS with pilot_cubed
mixed = filtered * pilot_cubed
print(f"\nMixed: max={np.max(np.abs(mixed)):.6f}")

# 5. Lowpass
lp_b = firwin(63, 2400/(SR/2))
baseband = lfilter(lp_b, np.array([1.0]), mixed)
print(f"Baseband: mean={np.mean(baseband):.6f}, range=[{np.min(baseband):.6f}, {np.max(baseband):.6f}]")

# 6. Integrate-and-dump
bits_raw = []
for i in range(n_bits):
    start = i * spb
    end = start + spb
    integral = np.sum(baseband[start:end])
    bits_raw.append(1 if integral > 0 else 0)

print(f"\nTest bits:     {''.join(str(b) for b in test_bits[:30])}")
print(f"Recovered:     {''.join(str(b) for b in bits_raw[:30])}")
matches = sum(1 for a, b in zip(bits_raw[:30], test_bits[:30]) if a == b)
print(f"Matches: {matches}/30")

# Try inverting (phase ambiguity)
inv_bits = [1-b for b in bits_raw]
print(f"Inverted:      {''.join(str(b) for b in inv_bits[:30])}")
matches_inv = sum(1 for a, b in zip(inv_bits[:30], test_bits[:30]) if a == b)
print(f"Inverted matches: {matches_inv}/30")
