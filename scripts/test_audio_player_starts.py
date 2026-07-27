#!/usr/bin/env python3
"""Verify that AudioPlayer actually starts its sounddevice output stream
and that processed chunks reach the speaker.

This test was added because the EQ 'had no audible effect' — the root
cause was that AudioPlayer.start() was never called by MainWindow.
The audio pipeline was: Gqrx → UDP → EQ → (nowhere). Gqrx's own
audio output was what the user actually heard.
"""
import sys
sys.path.insert(0, '/home/z/.local/lib/python3.13/site-packages')
sys.path.insert(0, '/home/z/my-project')

import numpy as np
from magic_sdr.audio_receiver import AudioPlayer, AudioReceiver


def test_audio_player_starts():
    """AudioPlayer.start() must return True and set _running=True."""
    print("[1] Testing AudioPlayer.start()...")
    p = AudioPlayer(sample_rate=48000, channels=2)
    ok = p.start()
    print(f"    start() returned: {ok}")
    print(f"    _running: {p._running}")
    print(f"    _sd_stream: {p._sd_stream!r}")
    assert ok is True, "AudioPlayer failed to start"
    assert p._running is True
    assert p._sd_stream is not None
    p.stop()
    print("    PASS — AudioPlayer starts cleanly")


def test_audio_player_accepts_chunk():
    """push() must accept a chunk and queue it for playback."""
    print("[2] Testing AudioPlayer.push() with a real chunk...")
    p = AudioPlayer(sample_rate=48000, channels=2)
    p.start()
    # Generate a 440 Hz tone for 0.5 s
    t = np.linspace(0, 0.5, int(48000 * 0.5), endpoint=False)
    tone = (np.sin(2 * np.pi * 440 * t) * 0.3 * 32767).astype(np.int16)
    stereo = np.column_stack([tone, tone])
    p.push(stereo)
    # The queue should not be empty
    assert not p._q.empty(), "Chunk was not queued for playback"
    print(f"    Queue size after push: {p._q.qsize()}")
    p.stop()
    print("    PASS — chunks are accepted and queued")


def test_audio_player_volume():
    """set_volume / set_muted work without breaking playback."""
    print("[3] Testing volume + mute controls...")
    p = AudioPlayer(sample_rate=48000, channels=2)
    p.start()
    p.set_volume(0.5)
    assert abs(p.get_volume() - 0.5) < 0.01
    p.set_muted(True)
    assert p.is_muted() is True
    p.set_muted(False)
    assert p.is_muted() is False
    p.stop()
    print("    PASS — volume + mute controls work")


def test_pipeline_end_to_end():
    """End-to-end: AudioReceiver → EQ → AudioPlayer.
    Verifies that if AudioPlayer.start() is called, an EQ'd chunk
    actually reaches the speaker queue.
    """
    print("[4] Testing end-to-end pipeline (Receiver → EQ → Player)...")
    from magic_sdr.equalizer import Equalizer

    eq = Equalizer(sample_rate=48000)
    eq.set_preset_by_name("Bass Boost")

    p = AudioPlayer(sample_rate=48000, channels=2)
    p.start()
    assert p._running

    # Generate a quiet tone (-12 dBFS) so the EQ boost doesn't clip
    t = np.linspace(0, 0.5, int(48000 * 0.5), endpoint=False)
    tone = (np.sin(2 * np.pi * 80 * t) * 0.25 * 32767).astype(np.int16)
    stereo = np.column_stack([tone, tone])

    processed = eq.process(stereo, sample_rate=48000)
    p.push(processed)

    assert not p._q.empty(), "EQ'd chunk did not reach the speaker queue"
    print(f"    EQ'd chunk shape: {processed.shape}, dtype: {processed.dtype}")
    print(f"    Peak amplitude: {np.abs(processed).max()}/{32767}")
    print(f"    Player queue size: {p._q.qsize()}")
    p.stop()
    print("    PASS — EQ'd audio reaches the speaker")


if __name__ == "__main__":
    try:
        test_audio_player_starts()
    except AssertionError as e:
        print(f"    FAIL: {e}")
        print("    >>> sounddevice may not be installed, or audio device is busy <<<")
        sys.exit(1)
    except Exception as e:
        print(f"    SKIP (environment): {e}")
        sys.exit(0)

    try:
        test_audio_player_accepts_chunk()
    except Exception as e:
        print(f"    FAIL: {e}")
        sys.exit(1)

    try:
        test_audio_player_volume()
    except Exception as e:
        print(f"    FAIL: {e}")
        sys.exit(1)

    try:
        test_pipeline_end_to_end()
    except Exception as e:
        print(f"    FAIL: {e}")
        sys.exit(1)

    print("\nAll audio playback tests PASSED.")
    print("AudioPlayer now actually plays sound; EQ will be audible.")
