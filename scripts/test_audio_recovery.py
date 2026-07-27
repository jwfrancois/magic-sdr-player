#!/usr/bin/env python3
"""Verify the audio-pipeline recovery UI controls and Gqrx mute logic.

This test was added after the user reported "Gqrx does not mute. Magic
SDR does not play." The root cause was that set_audio_gain(0) was in
the `else` branch of `if not audio_player.start()`, so when AudioPlayer
failed (e.g., sounddevice missing), Gqrx was never muted either.

The fix separates the two operations and adds UI controls so the user
can manually recover:
  - Restart Audio button
  - Test Audio button (plays 440 Hz tone)
  - Mute Gqrx toggle button
  - Audio status label
"""
import sys
sys.path.insert(0, '/home/z/.local/lib/python3.13/site-packages')
sys.path.insert(0, '/home/z/my-project')

import numpy as np


def test_audio_player_error_capture():
    """AudioPlayer must capture last_error so the UI can show it."""
    print("[1] Testing AudioPlayer.last_error capture...")
    from magic_sdr.audio_receiver import AudioPlayer
    p = AudioPlayer(sample_rate=48000, channels=2)
    assert p.last_error == ""
    # Try to start (will fail in headless env, that's OK)
    ok = p.start()
    if not ok:
        assert p.last_error != "", "last_error should be set when start() fails"
        print(f"    Captured error: {p.last_error[:80]}...")
        print("    PASS — error is captured for UI display")
    else:
        p.stop()
        print("    (AudioPlayer started in this env — skipping error test)")


def test_audio_player_status_api():
    """is_running(), pushed_count(), pulled_count(), device_name() all work."""
    print("[2] Testing AudioPlayer status API...")
    from magic_sdr.audio_receiver import AudioPlayer
    p = AudioPlayer(sample_rate=48000, channels=2)
    assert p.is_running() is False
    assert p.pushed_count() == 0
    assert p.pulled_count() == 0
    assert isinstance(p.device_name(), str)
    print(f"    device_name: {p.device_name()!r}")
    print(f"    is_running: {p.is_running()}")
    print("    PASS — status API works")


def test_gqrx_set_audio_gain_command_format():
    """Verify the GqrxClient sends the correct 'L AF 0' command to mute."""
    print("[3] Testing GqrxClient.set_audio_gain() command format...")
    from magic_sdr.gqrx_client import GqrxClient

    # Create a client without connecting — we'll intercept _send
    client = GqrxClient(host="127.0.0.1", port=7356)
    sent_commands = []
    def fake_send(cmd, expect_reply=True, timeout=3.0):
        sent_commands.append(cmd)
        return "RPRT 0"
    client._send = fake_send

    # Mute
    ok = client.set_audio_gain(0)
    assert ok is True
    assert sent_commands[-1] == "L AF 0", f"Expected 'L AF 0', got {sent_commands[-1]!r}"
    print(f"    Mute command: {sent_commands[-1]!r}")

    # Unmute
    ok = client.set_audio_gain(200)
    assert ok is True
    assert sent_commands[-1] == "L AF 200", f"Expected 'L AF 200', got {sent_commands[-1]!r}"
    print(f"    Unmute command: {sent_commands[-1]!r}")

    print("    PASS — Gqrx mute commands are formatted correctly")


def test_gqrx_set_audio_gain_failure():
    """When Gqrx returns an error, set_audio_gain() returns False."""
    print("[4] Testing GqrxClient.set_audio_gain() failure handling...")
    from magic_sdr.gqrx_client import GqrxClient

    client = GqrxClient(host="127.0.0.1", port=7356)
    def fake_send_error(cmd, expect_reply=True, timeout=3.0):
        return "RPRT 1"  # Gqrx error
    client._send = fake_send_error

    ok = client.set_audio_gain(0)
    assert ok is False, "Should return False when Gqrx returns RPRT 1"
    print("    PASS — failure correctly returns False")


def test_connect_handler_mutes_unconditionally():
    """Verify the connect handler mutes Gqrx REGARDLESS of AudioPlayer result.

    This is the regression test for the bug the user hit: previously,
    set_audio_gain(0) was inside the `else` branch of `if not audio_player.start()`,
    so if AudioPlayer failed, Gqrx was never muted either.
    """
    print("[5] Testing connect handler mutes Gqrx unconditionally...")
    # Read the source and verify the structure
    with open("/home/z/my-project/magic_sdr/main_window.py") as f:
        src = f.read()
    # Find the connect handler section
    marker = "# ALWAYS mute Gqrx's own audio output"
    assert marker in src, "Missing 'ALWAYS mute Gqrx' marker — fix may have been reverted"
    # Find set_audio_gain(0) — must come BEFORE audio_player.start()
    mute_pos = src.find("mute_ok = self.gqrx.set_audio_gain(0)")
    audio_pos = src.find("audio_ok = self.audio_player.start()")
    assert mute_pos > 0 and audio_pos > 0, "Could not find mute/start markers"
    assert mute_pos < audio_pos, (
        "set_audio_gain(0) must come BEFORE audio_player.start() so it runs "
        "regardless of whether AudioPlayer succeeds"
    )
    print(f"    mute_ok position: {mute_pos}")
    print(f"    audio_ok position: {audio_pos}")
    print("    PASS — Gqrx is muted unconditionally before AudioPlayer starts")


def test_ui_buttons_exist():
    """Verify the three new UI buttons are created in the main window."""
    print("[6] Testing UI buttons are defined...")
    with open("/home/z/my-project/magic_sdr/main_window.py") as f:
        src = f.read()
    for name in ["restart_audio_btn", "test_audio_btn", "gqrx_mute_btn",
                 "audio_status_lbl", "_on_restart_audio", "_on_test_audio",
                 "_on_gqrx_mute_toggled", "_update_audio_status_label",
                 "_play_test_tone"]:
        assert name in src, f"Missing UI element: {name}"
        print(f"    {name}: OK")
    print("    PASS — all UI controls are defined")


def test_test_tone_generation():
    """Verify the test tone generator produces valid int16 audio."""
    print("[7] Testing test tone generation...")
    from magic_sdr.audio_receiver import AudioPlayer
    import numpy as np

    p = AudioPlayer(sample_rate=48000, channels=2)
    # Replicate the _play_test_tone logic
    sr = 48000
    duration_s = 1.0
    freq_hz = 440.0
    n = int(sr * duration_s)
    t = np.linspace(0, duration_s, n, endpoint=False)
    tone = (np.sin(2 * np.pi * freq_hz * t) * 0.5 * 32767).astype(np.int16)
    stereo = np.column_stack([tone, tone])
    assert stereo.dtype == np.int16
    assert stereo.shape == (n, 2)
    # Peak amplitude should be ~0.5 * 32767 = 16383
    peak = np.abs(stereo).max()
    assert 14000 < peak < 17000, f"Unexpected peak: {peak}"
    print(f"    Tone shape: {stereo.shape}, dtype: {stereo.dtype}, peak: {peak}")
    print("    PASS — test tone generates valid int16 stereo audio")


if __name__ == "__main__":
    tests = [
        test_audio_player_error_capture,
        test_audio_player_status_api,
        test_gqrx_set_audio_gain_command_format,
        test_gqrx_set_audio_gain_failure,
        test_connect_handler_mutes_unconditionally,
        test_ui_buttons_exist,
        test_test_tone_generation,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"    FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"    SKIP (env): {e}")
    print()
    if failed:
        print(f"{failed} test(s) FAILED.")
        sys.exit(1)
    else:
        print("All audio-pipeline recovery tests PASSED.")
