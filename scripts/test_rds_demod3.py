#!/usr/bin/env python3
"""Test the integrate-and-dump demodulation directly."""
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

# Differential encode
diff_bits = []
last = 0
for b in all_bits:
    d = b ^ last
    diff_bits.append(d)
    last = b

# Generate BPSK signal
samples_per_bit = SR / RDS_BIT_RATE
spb_int = int(round(samples_per_bit))
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

signal = np.array(signal, dtype=np.float32) * 0.1
np.random.seed(42)
signal = signal + np.random.randn(len(signal)).astype(np.float32) * 0.02

# Demodulate using integrate-and-dump
from scipy.signal import firwin, lfilter

# Bandpass
bpf_b = firwin(129, [(RDS_SUBCARRIER_HZ - 2400)/(SR/2), (RDS_SUBCARRIER_HZ + 2400)/(SR/2)], pass_zero=False)
filtered = lfilter(bpf_b, np.array([1.0]), signal)

# Mix down
t = np.arange(len(filtered)) / SR
mixed = filtered * np.cos(2 * np.pi * RDS_SUBCARRIER_HZ * t)

# Lowpass
lp_b = firwin(63, 2400/(SR/2))
baseband = lfilter(lp_b, np.array([1.0]), mixed)

# Integrate-and-dump
print(f"Samples per bit: {spb_int}")
bits_raw = []
for i in range(len(baseband) // spb_int):
    integral = np.sum(baseband[i*spb_int:(i+1)*spb_int])
    bits_raw.append(1 if integral > 0 else 0)

# Differential decode
diff_recovered = []
last = 0
for b in bits_raw:
    d = b ^ last
    diff_recovered.append(d)
    last = b

print(f"Recovered bits (first 52): {''.join(str(b) for b in diff_recovered[:52])}")
print(f"Expected bits   (first 52): {''.join(str(b) for b in all_bits[:52])}")

matches = sum(1 for a, b in zip(diff_recovered[:104], all_bits[:104]) if a == b)
print(f"\nBit matches (first 104): {matches}/104 ({100*matches/104:.1f}%)")

# Now search for block sync — try all bit offsets
print("\n=== Searching for block sync ===")
decoder = RDSBlockDecoder()
best_offset = -1
best_count = 0
for offset in range(26):
    count = 0
    for start in range(offset, len(diff_recovered) - 26, 26):
        block = diff_recovered[start:start+26]
        syndrome = decoder._compute_syndrome(block)
        if syndrome in OFFSET_WORDS.values():
            count += 1
    if count > best_count:
        best_count = count
        best_offset = offset

print(f"Best bit offset: {best_offset} (found {best_count} valid blocks)")

# Decode from the best offset
aligned_bits = diff_recovered[best_offset:]
# Feed to block decoder
decoder2 = RDSBlockDecoder()
groups = decoder2.push_bits(aligned_bits[:500])
print(f"Groups decoded: {len(groups)}")
for g in groups[:5]:
    print(f"  type={g['type']}, pi=0x{g['pi']:04X}, pty={g['pty']}, word_d=0x{g['word_d']:04X}")
