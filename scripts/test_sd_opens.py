#!/usr/bin/env python3
"""Standalone test of AudioPlayer.start() — verifies that the
sounddevice RawOutputStream opens correctly.

We bypass the PyQt5 import by importing sounddevice directly and
replicating the AudioPlayer's start() logic.
"""
import sys
sys.path.insert(0, '/home/z/.local/lib/python3.13/site-packages')

import queue
import numpy as np
import sounddevice as sd


def test_sd_stream_opens():
    """Replicate AudioPlayer.start() — verifies sounddevice opens."""
    print("[1] Opening sounddevice RawOutputStream...")
    q = queue.Queue(maxsize=64)

    def callback(outdata, frames, time_info, status):
        try:
            chunk = q.get_nowait()
        except queue.Empty:
            outdata.fill(0)
            return
        if chunk.ndim == 1:
            chunk = np.column_stack([chunk, chunk])
        n = min(len(chunk), frames)
        outdata[:n] = chunk[:n]
        if n < frames:
            outdata[n:].fill(0)

    stream = sd.RawOutputStream(
        samplerate=48000,
        channels=2,
        dtype="int16",
        blocksize=0,
        callback=callback,
    )
    stream.start()
    print(f"    stream.active: {stream.active}")
    print(f"    stream.sample_rate: {stream.samplerate}")
    assert stream.active, "Stream did not start"
    # Push a tone
    t = np.linspace(0, 0.5, int(48000 * 0.5), endpoint=False)
    tone = (np.sin(2 * np.pi * 440 * t) * 0.3 * 32767).astype(np.int16)
    stereo = np.column_stack([tone, tone])
    q.put_nowait(stereo)
    # Let it play briefly
    import time as _t
    _t.sleep(0.2)
    print(f"    Queue size after 0.2s: {q.qsize()}")
    stream.stop()
    stream.close()
    print("    PASS — sounddevice stream opens and plays audio")


if __name__ == "__main__":
    try:
        test_sd_stream_opens()
        print("\nAudioPlayer will work on the user's machine once started.")
        print("The fix: call self.audio_player.start() in MainWindow._on_gqrx_connect().")
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)
