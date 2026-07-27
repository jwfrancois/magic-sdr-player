#!/usr/bin/env python3
"""Improved RDS demodulation test with proper bit timing recovery.

Uses zero-crossing based bit synchronization to align the sampling
points to the actual bit centers in the BPSK signal.
"""
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

def make_group(pi: int, pty: int, ps_addr: int, ps_chars: str) -> List[int]:
    block_a = make_block(pi, "A")
    word_b = (0 << 12) | (0 << 11) | (0 << 10) | (1 << 9) | (pty << 5) | ps_addr
    block_b = make_block(word_b, "B")
    block_c = make_block(0, "C")
    c1 = ord(ps_chars[0]) if len(ps_chars) > 0 else 0x20
    c2 = ord(ps_chars[1]) if len(ps_chars) > 1 else 0x20
    word_d = (c1 << 8) | c2
    block_d = make_block(word_d, "D")
    return block_a + block_b + block_c + block_d

# Build original bits
pi = 0xABCD
pty = 9
ps_full = "MAGIC98 "
all_bits = []
for addr in range(4):
    chars = ps_full[addr*2:addr*2+2]
    all_bits.extend(make_group(pi, pty, addr, chars))
all_bits = all_bits * 8  # repeat

# Differential encode
diff_bits = []
last = 0
for b in all_bits:
    d = b ^ last
    diff_bits.append(d)
    last = b

# Generate BPSK signal — use continuous time, sample at exact intervals
samples_per_bit = SR / RDS_BIT_RATE
print(f"Samples per bit: {samples_per_bit}")

# Build signal sample by sample
signal = []
sample_idx = 0
for bit in diff_bits:
    phase = np.pi if bit else 0
    # Number of samples for this bit
    next_idx = sample_idx + samples_per_bit
    n_samples = int(round(next_idx)) - int(round(sample_idx))
    for s in range(n_samples):
        time_s = (int(round(sample_idx)) + s) / SR
        # BPSK: cos(2*pi*f*t + phase)
        sample = np.cos(2 * np.pi * RDS_SUBCARRIER_HZ * time_s + phase)
        signal.append(sample)
    sample_idx = next_idx

signal = np.array(signal, dtype=np.float32) * 0.1
print(f"Signal: {len(signal)} samples, {len(signal)/SR:.2f}s")

# Add pilot
t = np.arange(len(signal)) / SR
pilot = 0.3 * np.sin(2 * np.pi * 19000 * t)
signal = signal + pilot.astype(np.float32)

# Add noise
np.random.seed(42)
signal = signal + np.random.randn(len(signal)).astype(np.float32) * 0.02

# Demodulate
from scipy.signal import firwin, lfilter

# Bandpass around 57 kHz
numtaps = 257  # longer filter for tighter passband
cutoff_low = (RDS_SUBCARRIER_HZ - 2400) / (SR / 2)
cutoff_high = (RDS_SUBCARRIER_HZ + 2400) / (SR / 2)
bpf_b = firwin(numtaps, [cutoff_low, cutoff_high], pass_zero=False)
bpf_a = np.array([1.0])
filtered = lfilter(bpf_b, bpf_a, signal)

# Mix down to baseband
t = np.arange(len(filtered)) / SR
mixer = np.cos(2 * np.pi * RDS_SUBCARRIER_HZ * t)
mixed = filtered * mixer

# Lowpass — use a tighter filter
lp_cutoff = 1500 / (SR / 2)  # just above bit rate
lp_b = firwin(127, lp_cutoff)
lp_a = np.array([1.0])
baseband = lfilter(lp_b, lp_a, mixed)

# The baseband should now be the BPSK signal at baseband.
# Each bit is samples_per_bit samples long.
# The sign of the baseband at the bit center gives the bit value.
# But there's a phase ambiguity (the mixer phase is arbitrary), so we
# use DIFFERENTIAL decoding: XOR consecutive bits.

# Strategy: sample the baseband at regular intervals of samples_per_bit.
# But we need to find the right phase (which sample is the "center" of a bit).
# We try all possible phases and pick the one that gives the best block sync.

print(f"\nBaseband stats: mean={np.mean(baseband):.6f}, std={np.std(baseband):.6f}")
print(f"Baseband range: [{np.min(baseband):.6f}, {np.max(baseband):.6f}]")

# Try different sampling phases
best_phase = 0
best_score = -1
best_bits = []
for phase_offset in range(int(samples_per_bit)):
    # Sample at phase_offset, phase_offset + spb, phase_offset + 2*spb, ...
    bits_raw = []
    idx = phase_offset
    while idx < len(baseband):
        bits_raw.append(1 if baseband[idx] > 0 else 0)
        idx += int(samples_per_bit)
    # Differential decode
    diff_decoded = []
    last = 0
    for b in bits_raw:
        d = b ^ last
        diff_decoded.append(d)
        last = b
    # Score: count how many valid block syndromes we find
    decoder = RDSBlockDecoder()
    score = 0
    for start in range(0, len(diff_decoded) - 26, 1):
        block = diff_decoded[start:start+26]
        syndrome = decoder._compute_syndrome(block)
        if syndrome in (OFFSET_WORDS["A"], OFFSET_WORDS["B"],
                        OFFSET_WORDS["C"], OFFSET_WORDS["C'"],
                        OFFSET_WORDS["D"]):
            score += 1
    if score > best_score:
        best_score = score
        best_phase = phase_offset
        best_bits = diff_decoded

print(f"\nBest phase offset: {best_phase} (score: {best_score})")
print(f"Best bits (first 52): {''.join(str(b) for b in best_bits[:52])}")
print(f"Expected   (first 52): {''.join(str(b) for b in all_bits[:52])}")

# Check accuracy
matches = sum(1 for a, b in zip(best_bits[:104], all_bits[:104]) if a == b)
print(f"\nBit matches (first 104): {matches}/104 ({100*matches/104:.1f}%)")

# Now feed the best bits to the block decoder
decoder = RDSBlockDecoder()
groups = decoder.push_bits(best_bits[:500])
print(f"\nGroups decoded: {len(groups)}")
for g in groups[:3]:
    print(f"  type={g['type']}, pi=0x{g['pi']:04X}, pty={g['pty']}, word_d=0x{g['word_d']:04X}")
