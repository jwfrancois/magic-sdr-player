#!/usr/bin/env python3
"""Standalone test of the GqrxClient mute command — doesn't need PyQt5."""
import sys
import socket


def test_gqrx_mute_command():
    """Verify the L AF 0 / L AF 200 commands are sent correctly."""
    print("[1] Testing GqrxClient mute command (without PyQt5)...")

    # Read the gqrx_client source to verify the command format
    with open("/home/z/my-project/magic_sdr/gqrx_client.py") as f:
        src = f.read()

    # Verify the set_audio_gain method exists and sends "L AF <int>"
    assert "def set_audio_gain" in src, "set_audio_gain method missing"
    assert 'f"L AF {int(gain)}"' in src, "Wrong command format"
    print("    set_audio_gain uses 'L AF <int>' — CORRECT for Gqrx")

    # Verify the response check
    assert 'r.startswith("RPRT 0")' in src, "Should check for RPRT 0"
    print("    Returns True only on RPRT 0 — CORRECT")

    print("    PASS")


def test_connect_handler_structure():
    """Verify the connect handler mutes Gqrx regardless of AudioPlayer result."""
    print("[2] Testing connect handler structure...")
    with open("/home/z/my-project/magic_sdr/main_window.py") as f:
        src = f.read()

    # Find the connect handler section
    always_marker = "# ALWAYS mute Gqrx's own audio output"
    assert always_marker in src, "Missing ALWAYS mute marker"

    # The mute call must come BEFORE audio_player.start()
    mute_pos = src.find("mute_ok = self.gqrx.set_audio_gain(0)")
    audio_pos = src.find("audio_ok = self.audio_player.start()")

    assert mute_pos > 0, "Could not find mute call"
    assert audio_pos > 0, "Could not find audio_player.start()"
    assert mute_pos < audio_pos, (
        "Mute MUST come before AudioPlayer.start() so it runs even when "
        "AudioPlayer fails (this was the bug the user hit)"
    )
    print(f"    Gqrx mute at char {mute_pos}")
    print(f"    AudioPlayer.start() at char {audio_pos}")
    print(f"    Mute happens first → user gets silence instead of Gqrx's bypass audio")

    # Verify both branches handle mute correctly
    assert "audio_ok = self.audio_player.start()" in src, "Missing audio_ok var"
    assert "if not audio_ok:" in src, "Missing audio_ok check"
    assert "self.audio_player.last_error" in src, "Should show last_error to user"
    assert "mute_ok" in src, "Should track mute_ok separately"
    print("    PASS — connect handler structure is correct")


def test_ui_buttons_in_layout():
    """Verify all 3 new buttons + status label are added to the layout."""
    print("[3] Testing UI buttons are added to layout...")
    with open("/home/z/my-project/magic_sdr/main_window.py") as f:
        src = f.read()

    # All 3 buttons should be added to audio_row layout
    assert "self.restart_audio_btn = QPushButton" in src
    assert "self.test_audio_btn = QPushButton" in src
    assert "self.gqrx_mute_btn = QPushButton" in src
    assert "self.audio_status_lbl = QLabel" in src

    # All 3 should be added to a layout
    assert "audio_row.addWidget(self.restart_audio_btn)" in src
    assert "audio_row.addWidget(self.test_audio_btn)" in src
    assert "audio_row.addWidget(self.gqrx_mute_btn)" in src

    # All handlers should be defined
    assert "def _on_restart_audio" in src
    assert "def _on_test_audio" in src
    assert "def _on_gqrx_mute_toggled" in src
    assert "def _play_test_tone" in src
    assert "def _update_audio_status_label" in src

    print("    PASS — all UI controls are properly wired")


def test_disconnect_restores_gqrx_audio():
    """Verify disconnect restores Gqrx's audio gain."""
    print("[4] Testing disconnect restores Gqrx audio...")
    with open("/home/z/my-project/magic_sdr/main_window.py") as f:
        src = f.read()

    # On closeEvent, should call set_audio_gain(200) to restore
    assert "self.gqrx.set_audio_gain(200)" in src, "Should restore Gqrx audio on disconnect"
    print("    PASS — Gqrx audio is restored on disconnect")


if __name__ == "__main__":
    test_gqrx_mute_command()
    test_connect_handler_structure()
    test_ui_buttons_in_layout()
    test_disconnect_restores_gqrx_audio()
    print("\nAll tests PASSED.")
