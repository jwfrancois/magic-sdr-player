#!/usr/bin/env python3
"""Debug the RDS demodulation — check if bits are being extracted correctly."""
import sys
sys.path.insert(0, '/home/z/my-project')

import numpy as np
from typing import List
from magic_sdr.rds import RDS_SUBCARRIER_HZ, RDS_BIT_RATE, OFFSET_WORDS

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
    type_code = 0
    version = 0
    word_b = (type_code << 12) | (version << 11) | (0 << 10) | (1 << 9) | (pty << 5) | ps_addr
    block_b = make_block(word_b, "B")
    block_c = make_block(0, "C")
    c1 = ord(ps_chars[0]) if len(ps_chars) > 0 else 0x20
    c2 = ord(ps_chars[1]) if len(ps_chars) > 1 else 0x20
    word_d = (c1 << 8) | c2
    block_d = make_block(word_d, "D")
    return block_a + block_b + block_c + block_d

# Build the original bits
pi = 0xABCD
pty = 9
ps_full = "MAGIC98 "
all_bits = []
for addr in range(4):
    chars = ps_full[addr*2:addr*2+2]
    group_bits = make_group(pi, pty, addr, chars)
    all_bits.extend(group_bits)

print(f"Original bits (first 26, block A): {''.join(str(b) for b in all_bits[:26])}")
print(f"Original bits (first 104, group 0): {''.join(str(b) for b in all_bits[:104])}")

# Differential encoding (as done in the test signal generator)
diff_bits = []
last = 0
for b in all_bits:
    d = b ^ last
    diff_bits.append(d)
    last = b

print(f"Differentially encoded bits (first 26): {''.join(str(b) for b in diff_bits[:26])}")

# Now generate the BPSK signal
samples_per_bit = SR / RDS_BIT_RATE
print(f"Samples per bit: {samples_per_bit}")

# Build the signal
signal = []
t_global = 0
for bit in diff_bits:
    phase = np.pi if bit else 0
    n_samples = int(round(samples_per_bit * (t_global + 1))) - int(round(samples_per_bit * t_global))
    for s in range(n_samples):
        time_s = t_global / RDS_BIT_RATE + s / SR
        sample = np.cos(2 * np.pi * RDS_SUBCARRIER_HZ * time_s + phase)
        signal.append(sample)
    t_global += 1

signal = np.array(signal, dtype=np.float32) * 0.1
print(f"Signal length: {len(signal)} samples")

# Now run the demodulation manually
from scipy.signal import firwin, lfilter

# Bandpass around 57 kHz
numtaps = 129
cutoff_low = (RDS_SUBCARRIER_HZ - 2400) / (SR / 2)
cutoff_high = (RDS_SUBCARRIER_HZ + 2400) / (SR / 2)
bpf_b = firwin(numtaps, [cutoff_low, cutoff_high], pass_zero=False)
bpf_a = np.array([1.0])
bpf_zi = np.zeros(numtaps - 1)
filtered, bpf_zi = lfilter(bpf_b, bpf_a, signal, zi=bpf_zi)

# Mix down
t = np.arange(len(filtered)) / SR
mixer = np.cos(2 * np.pi * RDS_SUBCARRIER_HZ * t)
mixed = filtered * mixer

# Lowpass
lp_cutoff = 2400 / (SR / 2)
lp_b = firwin(63, lp_cutoff)
lp_a = np.array([1.0])
lp_zi = np.zeros(62)
baseband, lp_zi = lfilter(lp_b, lp_a, mixed, zi=lp_zi)

print(f"\nBaseband signal: mean={np.mean(baseband):.6f}, std={np.std(baseband):.6f}")
print(f"Baseband range: [{np.min(baseband):.6f}, {np.max(baseband):.6f}]")

# Sample at bit centers
bits_out = []
last_bit = 0
bit_phase = 0
for i in range(len(baseband)):
    bit_phase += 1.0
    if bit_phase >= samples_per_bit:
        bit_phase -= samples_per_bit
        bit = 1 if baseband[i] > 0 else 0
        diff_bit = bit ^ last_bit
        last_bit = bit
        bits_out.append(diff_bit)

print(f"\nDecoded bits (first 26): {''.join(str(b) for b in bits_out[:26])}")
print(f"Expected (original):     {''.join(str(b) for b in all_bits[:26])}")
matches = sum(1 for a, b in zip(bits_out[:104], all_bits[:104]) if a == b)
print(f"\nBit matches (first 104): {matches}/104")
print(f"Bit accuracy: {100*matches/104:.1f}%")

# Check where the first mismatches are
print("\nFirst 104 bits comparison:")
for i in range(104):
    if bits_out[i] != all_bits[i]:
        print(f"  Mismatch at bit {i}: decoded={bits_out[i]}, expected={all_bits[i]}")
        if i > 20:
            print("  ... (stopping after 20 mismatches)")
            break
