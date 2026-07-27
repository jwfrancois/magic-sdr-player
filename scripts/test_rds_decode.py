#!/usr/bin/env python3
"""Test the RDS decoder with a synthetic RDS signal.

Generates a fake MPX signal containing a 57 kHz RDS subcarrier with
BPSK-modulated data, runs it through the RDSDecoder, and verifies
that the decoder extracts the correct PS/PTY/PI.
"""
import sys
sys.path.insert(0, '/home/z/my-project')

import numpy as np
from typing import List
from magic_sdr.rds import (
    RDSDecoder, RDSBlockDecoder, RDSGroupInterpreter,
    RDS_SUBCARRIER_HZ, RDS_BIT_RATE, OFFSET_WORDS,
)

# Use a high sample rate so we can see 57 kHz
SR = 192000

def compute_check_word(data_16: int, offset_word: int) -> int:
    """Compute the 10-bit check word for a 16-bit data word + offset."""
    # The check word is the remainder of (data << 10) / g(x) XOR offset
    # g(x) = x^10 + x^8 + x^7 + x^5 + x^4 + x^3 + 1 = 0x5B9 (11 bits)
    g = 0x5B9
    # data is 16 bits; shift left by 10 and divide by g
    word = data_16 << 10
    for i in range(16):
        if word & (1 << (25 - i)):
            word ^= g << (25 - i - 10)
    check = (word & 0x3FF) ^ offset_word
    return check

def make_block(data_16: int, offset_name: str) -> List[int]:
    """Make a 26-bit RDS block from 16-bit data + offset word."""
    offset = OFFSET_WORDS[offset_name]
    check = compute_check_word(data_16, offset)
    word = (data_16 << 10) | check
    bits = []
    for i in range(25, -1, -1):
        bits.append((word >> i) & 1)
    return bits

def make_group(pi: int, pty: int, ps_addr: int, ps_chars: str,
               group_type: str = "0A") -> List[int]:
    """Make a 104-bit RDS group (type 0A with PS data)."""
    # Block A: PI (16 bits)
    block_a = make_block(pi, "A")
    # Block B: group type (4 bits) + version (1 bit) + PTY (5 bits) + ...
    # For type 0A: type_code=0, version=0
    # Word B format: [group_type(4)][version(1)][traffic(1)][music_speech(1)][pty(5)][..(5)]
    type_code = 0
    version = 0  # A
    word_b = (type_code << 12) | (version << 11) | (0 << 10) | (1 << 9) | (pty << 5) | ps_addr
    block_b = make_block(word_b, "B")
    # Block C: for type 0A, this is alternate frequency (we'll use 0)
    block_c = make_block(0, "C")
    # Block D: 2 PS characters
    c1 = ord(ps_chars[0]) if len(ps_chars) > 0 else 0x20
    c2 = ord(ps_chars[1]) if len(ps_chars) > 1 else 0x20
    word_d = (c1 << 8) | c2
    block_d = make_block(word_d, "D")
    return block_a + block_b + block_c + block_d

def bits_to_bpsk_signal(bits: List[int], sample_rate: int) -> np.ndarray:
    """Convert bits to a BPSK-modulated signal at 57 kHz, 1187.5 bps.

    Each bit is differentially encoded (XOR with previous bit).
    """
    # Differential encoding
    diff_bits = []
    last = 0
    for b in bits:
        d = b ^ last
        diff_bits.append(d)
        last = b
    # BPSK: 0 -> 0 phase, 1 -> 180 phase
    samples_per_bit = sample_rate / RDS_BIT_RATE
    signal = []
    t = 0
    for bit in diff_bits:
        phase = np.pi if bit else 0
        n_samples = int(round(samples_per_bit * (t + 1))) - int(round(samples_per_bit * t))
        for s in range(n_samples):
            time_s = t / RDS_BIT_RATE + s / sample_rate
            # BPSK at 57 kHz subcarrier
            sample = np.cos(2 * np.pi * RDS_SUBCARRIER_HZ * time_s + phase)
            signal.append(sample)
        t += 1
    return np.array(signal, dtype=np.float32) * 0.1  # low amplitude

# Build 4 groups to send a complete PS ("MAGIC98 ")
print("Building synthetic RDS groups...")
pi = 0xABCD
pty = 9  # Top 40
ps_full = "MAGIC98 "
all_bits = []
for addr in range(4):
    chars = ps_full[addr*2:addr*2+2]
    group_bits = make_group(pi, pty, addr, chars)
    all_bits.extend(group_bits)

# Repeat the groups several times to help the decoder sync
all_bits = all_bits * 8
print(f"Total bits: {len(all_bits)}")

# Convert to BPSK signal
print("Modulating to BPSK at 57 kHz...")
signal = bits_to_bpsk_signal(all_bits, SR)
print(f"Signal: {len(signal)} samples, {len(signal)/SR:.2f}s")

# Add some noise
np.random.seed(42)
noise = np.random.randn(len(signal)).astype(np.float32) * 0.02
signal_with_noise = signal + noise

# Convert to int16
chunk_int16 = (signal_with_noise * 32767).astype(np.int16)

# Add a 19 kHz pilot so pilot detection works
t = np.arange(len(chunk_int16)) / SR
pilot = (0.3 * np.sin(2 * np.pi * 19000 * t) * 32767).astype(np.int16)
chunk_int16 = np.clip(chunk_int16 + pilot, -32768, 32767).astype(np.int16)

# Run through the decoder
print("Running RDS decoder...")
decoder = RDSDecoder(sample_rate=SR)

# Process in chunks of 4096 samples
chunk_size = 4096
for i in range(0, len(chunk_int16), chunk_size):
    chunk = chunk_int16[i:i+chunk_size]
    info = decoder.process_audio(chunk, SR)

print()
print("=== Results ===")
print(f"  Stereo pilot detected: {info.stereo_pilot_detected}")
print(f"  Pilot strength:        {info.pilot_strength_db}")
print(f"  PI (Program ID):       {('0x%04X' % info.pi) if info.pi else 'None'}")
print(f"  PTY:                   {info.pty} ({info.pty_label})")
print(f"  PS (Station Name):     '{info.ps}'")
print(f"  RT (Radio Text):       '{info.rt}'")
print(f"  Groups decoded:        {info.groups_decoded}")
print(f"  Sync state:            {info.sync_state}")

# Expected: PI=0xABCD, PTY=9 (Top 40), PS="MAGIC98 "
print()
print("Expected: PI=0xABCD, PTY=9 (Top 40), PS='MAGIC98 '")
if info.ps == "MAGIC98 ":
    print("✓ PS decoded correctly!")
elif info.ps is not None:
    print(f"✗ PS decoded but wrong: '{info.ps}'")
else:
    print("✗ PS NOT decoded")

if info.pi == 0xABCD:
    print("✓ PI decoded correctly!")
elif info.pi is not None:
    print(f"✗ PI decoded but wrong: 0x{info.pi:04X}")
else:
    print("✗ PI NOT decoded")

if info.pty == 9:
    print("✓ PTY decoded correctly!")
elif info.pty is not None:
    print(f"✗ PTY decoded but wrong: {info.pty}")
else:
    print("✗ PTY NOT decoded")
