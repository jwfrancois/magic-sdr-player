#!/usr/bin/env python3
"""Test RDS decoder with pilot-coherent demodulation.

Generates a synthetic MPX signal with:
  - 19 kHz pilot tone
  - 57 kHz RDS subcarrier (phase-locked to 3x the pilot)
  - BPSK-modulated RDS data at 1187.5 bps
"""
import sys
sys.path.insert(0, '/home/z/my-project')

import numpy as np
from typing import List
from magic_sdr.rds import RDS_SUBCARRIER_HZ, RDS_BIT_RATE, OFFSET_WORDS, RDSBlockDecoder
from magic_sdr.rds import RDSDecoder

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
all_bits = all_bits * 8  # repeat 8x

print(f"Total bits: {len(all_bits)}")

# Generate the MPX signal
# The 19 kHz pilot: cos(2*pi*19000*t)
# The 57 kHz RDS subcarrier: BPSK modulated, phase-locked to 3x pilot
#   so RDS subcarrier = cos(2*pi*57000*t + phase) where phase is 0 or pi
#   and the 57 kHz is EXACTLY 3x the 19 kHz pilot phase.
# Since 57 = 3*19, if the pilot is cos(2*pi*19000*t), then 3x pilot phase
# is cos(2*pi*57000*t). So the RDS subcarrier should be cos(2*pi*57000*t + bpsk_phase).

samples_per_bit = SR / RDS_BIT_RATE
spb_int = int(round(samples_per_bit))
print(f"Samples per bit: {spb_int}")

# Build signal sample by sample
# We need continuous time across the whole signal
total_samples = int(len(all_bits) * samples_per_bit) + 1000
t = np.arange(total_samples) / SR

# Pilot at 19 kHz
pilot = 0.1 * np.cos(2 * np.pi * 19000 * t)

# RDS subcarrier: BPSK modulated
# For each bit period, the phase is 0 (bit=0) or pi (bit=1)
# Build an array of phase values, one per sample
rds_phase = np.zeros(total_samples)
sample_idx = 0
current_phase = 0.0
for bit in all_bits:
    if bit:
        current_phase += np.pi  # flip phase for bit=1
    end_idx = sample_idx + spb_int
    if end_idx > total_samples:
        break
    rds_phase[sample_idx:end_idx] = current_phase
    sample_idx = end_idx

# RDS subcarrier at 57 kHz, phase-locked to 3x pilot
# Since 57 = 3*19, cos(2*pi*57000*t) = cos(3 * 2*pi*19000*t)
# which is naturally phase-locked to the pilot.
rds_subcarrier = 0.05 * np.cos(2 * np.pi * RDS_SUBCARRIER_HZ * t + rds_phase)

# Combined MPX signal
mpx = pilot + rds_subcarrier

# Add some noise
np.random.seed(42)
mpx = mpx + np.random.randn(len(mpx)) * 0.01

# Convert to int16
chunk_int16 = (mpx * 32767).astype(np.int16)
print(f"Signal: {len(chunk_int16)} samples, {len(chunk_int16)/SR:.2f}s")

# Run through the RDS decoder
print("\nRunning RDS decoder...")
decoder = RDSDecoder(sample_rate=SR)

# Process in chunks
chunk_size = 8192
for i in range(0, len(chunk_int16), chunk_size):
    chunk = chunk_int16[i:i+chunk_size]
    info = decoder.process_audio(chunk, SR)

print("\n=== Results ===")
print(f"  Stereo pilot detected: {info.stereo_pilot_detected}")
print(f"  Pilot strength:        {info.pilot_strength_db}")
print(f"  PI (Program ID):       {('0x%04X' % info.pi) if info.pi else 'None'}")
print(f"  PTY:                   {info.pty} ({info.pty_label})")
print(f"  PS (Station Name):     '{info.ps}'")
print(f"  RT (Radio Text):       '{info.rt}'")
print(f"  Groups decoded:        {info.groups_decoded}")
print(f"  Sync state:            {info.sync_state}")

print(f"\nExpected: PI=0xABCD, PTY=9 (Top 40), PS='MAGIC98 '")
