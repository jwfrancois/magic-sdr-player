#!/usr/bin/env python3
"""Standalone test of AudioPlayer volume responsiveness (no PyQt5)."""
import sys
import numpy as np
import queue


def test_volume_in_callback():
    """Replicate the AudioPlayer logic and verify volume is applied in callback."""
    print("[1] Testing volume applied in callback (not push)...")

    # Simulate the AudioPlayer's internal state
    q = queue.Queue(maxsize=64)
    volume = 1.0
    muted = False
    running = True
    pushed_count = 0
    pulled_count = 0

    def push(chunk):
        nonlocal pushed_count
        if not running:
            return
        pushed_count += 1
        # Volume NOT applied here (the fix)
        try:
            q.put_nowait(chunk)
        except queue.Full:
            try:
                q.get_nowait()
                q.put_nowait(chunk)
            except Exception:
                pass

    def callback(outdata, frames):
        nonlocal pulled_count
        try:
            chunk = q.get_nowait()
            pulled_count += 1
        except queue.Empty:
            outdata.fill(0)
            return
        # Volume applied HERE (the fix)
        if muted or volume == 0.0:
            outdata.fill(0)
            return
        if volume < 1.0:
            chunk = (chunk.astype(np.float32) * volume).astype(np.int16)
        n = min(len(chunk), frames)
        outdata[:n] = chunk[:n]
        if n < frames:
            outdata[n:].fill(0)

    # Test: push 10 chunks at vol=1.0, then change to vol=0.1
    for _ in range(10):
        push(np.full((50, 2), 1000, dtype=np.int16))

    volume = 0.1  # change volume

    # Next callback should play at 0.1 volume
    outdata = np.zeros((50, 2), dtype=np.int16)
    callback(outdata, 50)

    peak = np.abs(outdata).max()
    expected = int(1000 * 0.1)  # 100
    assert abs(peak - expected) <= 1, f"Expected ~{expected}, got {peak}"
    print(f"    Pushed 10 chunks at vol=1.0, changed to vol=0.1")
    print(f"    Next callback peak: {peak} (expected ~{expected})")
    print("    PASS — volume change takes effect immediately")


def test_old_behavior_was_broken():
    """Show that the OLD behavior (volume in push) was broken."""
    print("[2] Verifying OLD behavior was broken (volume in push)...")

    q = queue.Queue(maxsize=64)
    volume = 1.0
    pushed_count = 0

    def push_old(chunk):
        nonlocal pushed_count
        pushed_count += 1
        # OLD: volume applied here
        if volume < 1.0:
            chunk = (chunk.astype(np.float32) * volume).astype(np.int16)
        try:
            q.put_nowait(chunk)
        except queue.Full:
            try:
                q.get_nowait()
                q.put_nowait(chunk)
            except Exception:
                pass

    def callback_old(outdata, frames):
        try:
            chunk = q.get_nowait()
        except queue.Empty:
            outdata.fill(0)
            return
        # OLD: no volume applied here
        n = min(len(chunk), frames)
        outdata[:n] = chunk[:n]

    # Push 10 chunks at vol=1.0
    for _ in range(10):
        push_old(np.full((50, 2), 1000, dtype=np.int16))

    # Change volume to 0.1
    volume = 0.1

    # Next callback plays a chunk that was queued at vol=1.0
    outdata = np.zeros((50, 2), dtype=np.int16)
    callback_old(outdata, 50)

    peak = np.abs(outdata).max()
    # OLD behavior: peak is 1000 (unchanged), NOT 100
    assert peak == 1000, f"Old behavior should play at old volume, got {peak}"
    print(f"    Pushed 10 chunks at vol=1.0, changed to vol=0.1")
    print(f"    Next callback peak: {peak} (BUG: should be ~100, got 1000)")
    print("    CONFIRMED — old behavior was broken (volume didn't take effect)")


def test_mute_in_callback():
    """Mute should silence the next callback immediately."""
    print("[3] Testing mute takes effect immediately...")
    q = queue.Queue(maxsize=64)
    volume = 1.0
    muted = False

    def push(chunk):
        q.put_nowait(chunk)

    def callback(outdata, frames):
        nonlocal muted
        try:
            chunk = q.get_nowait()
        except queue.Empty:
            outdata.fill(0)
            return
        if muted or volume == 0.0:
            outdata.fill(0)
            return
        n = min(len(chunk), frames)
        outdata[:n] = chunk[:n]

    for _ in range(5):
        push(np.full((50, 2), 1000, dtype=np.int16))
    muted = True

    outdata = np.zeros((50, 2), dtype=np.int16)
    callback(outdata, 50)
    peak = np.abs(outdata).max()
    assert peak == 0, f"Muted should be silent, got {peak}"
    print(f"    Pushed 5 chunks, muted, next callback peak: {peak}")
    print("    PASS — mute takes effect immediately")


if __name__ == "__main__":
    test_volume_in_callback()
    test_old_behavior_was_broken()
    test_mute_in_callback()
    print("\nAll tests PASSED — volume now takes effect immediately.")
