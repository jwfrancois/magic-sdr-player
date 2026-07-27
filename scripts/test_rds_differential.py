#!/usr/bin/env python3
"""Test RDS demodulation using the squared-signal approach.

RDS BPSK at 57 kHz: when you square the bandpass-filtered signal,
the BPSK phase modulation disappears (since (+1)^2 = (-1)^2 = 1),
and you get a pure 114 kHz tone. This doesn't directly help decode
the data, but it confirms the subcarrier is present.

For actual data recovery, we use the fact that RDS uses DIFFERENTIAL
encoding. The data is in the PHASE TRANSITIONS of the 57 kHz carrier.
So:
  1. Bandpass filter around 57 kHz
  2. Multiply by a delayed copy of itself (delay = 1 bit period)
     This is a differential demodulator — it produces +1 when two
     consecutive bits are the same, and -1 when they differ.
  3. Lowpass and sample at the bit rate.
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
all_bits = all_bits * 8

# Differential encode (this is what RDS actually transmits)
diff_bits = []
last = 0
for b in all_bits:
    d = b ^ last
    diff_bits.append(d)
    last = b

# Generate BPSK signal — NO phase reset per bit, continuous carrier
# with phase flips at bit boundaries
samples_per_bit = SR / RDS_BIT_RATE
spb_int = int(round(samples_per_bit))
print(f"Samples per bit: {spb_int}")

# Build the signal: continuous 57 kHz carrier, phase flips by pi when
# the differentially-encoded bit is 1
signal = np.zeros(0, dtype=np.float32)
current_phase = 0.0
for bit in diff_bits:
    if bit:
        current_phase += np.pi  # flip phase
    n_samples = spb_int
    t = np.arange(n_samples) / SR
    sig_chunk = np.cos(2 * np.pi * RDS_SUBCARRIER_HZ * t + current_phase)
    signal = np.concatenate([signal, sig_chunk.astype(np.float32)])

signal = signal * 0.1
np.random.seed(42)
signal = signal + np.random.randn(len(signal)).astype(np.float32) * 0.02

print(f"Signal: {len(signal)} samples, {len(signal)/SR:.2f}s")

# Check spectrum
from numpy.fft import rfft
spec = np.abs(rfft(signal[:8192]))
freqs = np.fft.rfftfreq(8192, 1/SR)
peak = freqs[np.argmax(spec)]
print(f"Signal spectrum peak: {peak:.0f} Hz (expected 57000)")

# === Differential demodulation ===
# Multiply signal by a delayed copy of itself (delay = 1 bit period)
from scipy.signal import firwin, lfilter

# Bandpass first to clean up
bpf_b = firwin(129, [(RDS_SUBCARRIER_HZ - 2400)/(SR/2), (RDS_SUBCARRIER_HZ + 2400)/(SR/2)], pass_zero=False)
filtered = lfilter(bpf_b, np.array([1.0]), signal)

# Delay by exactly 1 bit period
delay = spb_int
delayed = np.zeros_like(filtered)
delayed[delay:] = filtered[:-delay]

# Multiply — this is the differential demodulator
demod = filtered * delayed

# The product is now a baseband signal:
#   + amplitude when consecutive bits are the same
#   - amplitude when consecutive bits differ
# (Because cos(a)*cos(a+pi) = -cos^2(a), which is negative)

# Lowpass to get the baseband
lp_b = firwin(63, 2400/(SR/2))
baseband = lfilter(lp_b, np.array([1.0]), demod)

print(f"\nDemod baseband: mean={np.mean(baseband):.4f}, range=[{np.min(baseband):.4f}, {np.max(baseband):.4f}]")

# Sample at bit centers
# Try different phases
best_score = -1
best_phase = 0
best_bits = []
for phase in range(spb_int):
    bits_raw = []
    idx = phase
    while idx < len(baseband):
        bits_raw.append(1 if baseband[idx] > 0 else 0)
        idx += spb_int
    # In differential demod, the output IS the decoded bit (no extra XOR needed)
    # Score: count valid block syndromes
    decoder = RDSBlockDecoder()
    score = 0
    for start in range(0, len(bits_raw) - 26, 1):
        block = bits_raw[start:start+26]
        syndrome = decoder._compute_syndrome(block)
        if syndrome in OFFSET_WORDS.values():
            score += 1
    if score > best_score:
        best_score = score
        best_phase = phase
        best_bits = bits_raw

print(f"\nBest phase: {best_phase}, score: {best_score}")
print(f"Recovered bits (first 52): {''.join(str(b) for b in best_bits[:52])}")
print(f"Expected bits   (first 52): {''.join(str(b) for b in all_bits[:52])}")

matches = sum(1 for a, b in zip(best_bits[:104], all_bits[:104]) if a == b)
print(f"\nBit matches (first 104): {matches}/104 ({100*matches/104:.1f}%)")

# Feed to block decoder
decoder = RDSBlockDecoder()
groups = decoder.push_bits(best_bits[:500])
print(f"\nGroups decoded: {len(groups)}")
for g in groups[:5]:
    print(f"  type={g['type']}, pi=0x{g['pi']:04X}, pty={g['pty']}, word_d=0x{g['word_d']:04X}")
