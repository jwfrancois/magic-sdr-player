#!/usr/bin/env python3
"""Test the RDS block decoder's syndrome computation and sync search.

This tests the BLOCK DECODER in isolation (no demodulation) by feeding
it the exact known bits. If this works, the block decoder is correct
and the issue is only in the demodulation.
"""
import sys
sys.path.insert(0, '/home/z/my-project')

from typing import List
from magic_sdr.rds import (
    RDSBlockDecoder, RDSGroupInterpreter, OFFSET_WORDS, RDS_BLOCK_BITS,
)

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

# Build a group with known data
pi = 0xABCD
pty = 9  # Top 40
ps_full = "MAGIC98 "

# Block A: PI
block_a = make_block(pi, "A")
# Block B: type 0A, PTY 9
word_b = (0 << 12) | (0 << 11) | (0 << 10) | (1 << 9) | (pty << 5) | 0  # addr 0
block_b = make_block(word_b, "B")
# Block C: AF
block_c = make_block(0, "C")
# Block D: 2 PS chars
c1, c2 = ord('M'), ord('A')
word_d = (c1 << 8) | c2
block_d = make_block(word_d, "D")

group_bits = block_a + block_b + block_c + block_d
print(f"Group bits ({len(group_bits)} bits): {''.join(str(b) for b in group_bits[:52])}...")

# Test 1: Feed the exact bits to the block decoder
print("\n=== Test 1: Feed exact aligned bits ===")
decoder = RDSBlockDecoder()
groups = decoder.push_bits(group_bits)
print(f"Groups decoded: {len(groups)}")
if groups:
    g = groups[0]
    print(f"  type={g['type']}, pi=0x{g['pi']:04X}, pty={g['pty']}, word_d=0x{g['word_d']:04X}")
    print(f"  PS chars: {chr(g['word_d'] >> 8)}{chr(g['word_d'] & 0xFF)}")

# Test 2: Feed bits with a random offset (simulates misalignment)
print("\n=== Test 2: Feed bits with 7-bit offset ===")
decoder2 = RDSBlockDecoder()
# Prepend 7 random bits
offset_bits = [0, 1, 1, 0, 1, 0, 0] + group_bits + group_bits + group_bits
groups2 = decoder2.push_bits(offset_bits)
print(f"Groups decoded: {len(groups2)}")
for g in groups2:
    print(f"  type={g['type']}, pi=0x{g['pi']:04X}, pty={g['pty']}, word_d=0x{g['word_d']:04X}")

# Test 3: Feed multiple groups
print("\n=== Test 3: Feed 4 groups (complete PS) ===")
all_bits = []
for addr in range(4):
    chars = ps_full[addr*2:addr*2+2]
    block_a = make_block(pi, "A")
    word_b = (0 << 12) | (0 << 11) | (0 << 10) | (1 << 9) | (pty << 5) | addr
    block_b = make_block(word_b, "B")
    block_c = make_block(0, "C")
    c1, c2 = ord(chars[0]), ord(chars[1])
    word_d = (c1 << 8) | c2
    block_d = make_block(word_d, "D")
    all_bits.extend(block_a + block_b + block_c + block_d)

decoder3 = RDSBlockDecoder()
groups3 = decoder3.push_bits(all_bits)
print(f"Groups decoded: {len(groups3)}")
interpreter = RDSGroupInterpreter()
for g in groups3:
    changes = interpreter.process_group(g)
    print(f"  type={g['type']}, pi=0x{g['pi']:04X}, pty={g['pty']}, word_d=0x{g['word_d']:04X}, changes={list(changes.keys())}")
print(f"\nFinal PS: '{interpreter.ps_complete}'")
print(f"Final PI: 0x{interpreter.last_pi:04X}" if interpreter.last_pi else "Final PI: None")
print(f"Final PTY: {interpreter.last_pty} ({interpreter.last_pty})" if interpreter.last_pty else "Final PTY: None")

# Expected: PS="MAGIC98 ", PI=0xABCD, PTY=9
print(f"\nExpected: PS='MAGIC98 ', PI=0xABCD, PTY=9")
