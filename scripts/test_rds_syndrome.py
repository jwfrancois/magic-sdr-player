#!/usr/bin/env python3
"""Debug the RDS block decoder syndrome computation."""
import sys
sys.path.insert(0, '/home/z/my-project')

from typing import List
from magic_sdr.rds import RDSBlockDecoder, OFFSET_WORDS, RDS_BLOCK_BITS

def compute_check_word(data_16: int, offset_word: int) -> int:
    """Compute the 10-bit check word for a 16-bit data word + offset."""
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

# Test block A with PI=0xABCD
data_a = 0xABCD
block_a_bits = make_block(data_a, "A")
print(f"Block A bits: {''.join(str(b) for b in block_a_bits)}")
print(f"  data=0x{data_a:04X}, check=0x{compute_check_word(data_a, OFFSET_WORDS['A']):03X}")

# Compute syndrome directly
decoder = RDSBlockDecoder()
syndrome = decoder._compute_syndrome(block_a_bits)
print(f"  Syndrome: 0x{syndrome:03X}")
print(f"  Expected (offset A): 0x{OFFSET_WORDS['A']:03X}")
print(f"  Match: {syndrome == OFFSET_WORDS['A']}")

# Debug: show the raw word
word = 0
for b in block_a_bits:
    word = (word << 1) | b
print(f"  Full 26-bit word: 0x{word:07X} ({word:026b})")
print(f"  Data (top 16):    0x{(word >> 10) & 0xFFFF:04X}")
print(f"  Check (low 10):   0x{word & 0x3FF:03X}")

# Manual syndrome computation
g = 0x5B9
print(f"  Generator poly: 0x{g:03X} ({g:011b})")
rem = word
print(f"  Initial remainder: 0x{rem:07X} ({rem:026b})")
for i in range(16):
    if rem & (1 << (25 - i)):
        shifted_g = g << (25 - i - 10)
        print(f"  Step {i}: bit {25-i} set, XOR with g<<{25-i-10} = 0x{shifted_g:07X}")
        rem ^= shifted_g
        print(f"           rem now: 0x{rem:07X}")
    else:
        print(f"  Step {i}: bit {25-i} not set, skip")
print(f"  Final remainder (syndrome): 0x{rem & 0x3FF:03X}")
print(f"  Offset A: 0x{OFFSET_WORDS['A']:03X}")
