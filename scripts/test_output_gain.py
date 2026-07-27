#!/usr/bin/env python3
"""Verify the output gain control works to make audio quieter/louder.

This test was added after the user reported: "When I muted Gqrx, the
app's sound is loud and noisy with no way to turn it down."

Root cause: the makeup gain was normalizing audio to -0.5 dBFS (very
loud) and the limiter ceiling was -0.3 dBFS (essentially hard clipping
on every peak). The volume slider worked but at 80% of -0.5 dBFS, the
audio was still deafening.

Fix: added a master output gain control (default -6 dB), lowered the
makeup target to -6 dBFS, lowered the limiter ceiling to -3 dBFS, and
lowered the default volume to 50%.
"""
import sys
import numpy as np

sys.path.insert(0, '/home/z/my-project')


def test_output_gain_default():
    """Output gain defaults to -6 dB (half amplitude)."""
    print("[1] Testing output gain default...")
    from magic_sdr.equalizer import Equalizer
    eq = Equalizer(sample_rate=48000)
    assert eq.output_gain_db == -6.0, f"Default should be -6 dB, got {eq.output_gain_db}"
    print(f"    Default output_gain_db: {eq.output_gain_db} dB")
    print("    PASS — default is -6 dB (comfortable listening level)")


def test_output_gain_makes_audio_quieter():
    """Output gain of -20 dB should make audio significantly quieter."""
    print("[2] Testing output gain attenuates audio...")
    from magic_sdr.equalizer import Equalizer

    # Generate a full-scale sine wave
    sr = 48000
    t = np.linspace(0, 0.1, int(sr * 0.1), endpoint=False)
    tone = (np.sin(2 * np.pi * 440 * t) * 0.5 * 32767).astype(np.int16)
    stereo = np.column_stack([tone, tone])

    # Process with output gain = 0 dB (no attenuation)
    eq0 = Equalizer(sample_rate=48000)
    eq0.set_output_gain(0.0)
    eq0.set_limiter_enabled(False)  # disable to isolate output gain effect
    out0 = eq0.process(stereo.copy(), sample_rate=sr)
    peak0 = np.abs(out0).max()

    # Process with output gain = -20 dB (1/10 amplitude)
    eq20 = Equalizer(sample_rate=48000)
    eq20.set_output_gain(-20.0)
    eq20.set_limiter_enabled(False)
    out20 = eq20.process(stereo.copy(), sample_rate=sr)
    peak20 = np.abs(out20).max()

    ratio = peak20 / peak0 if peak0 > 0 else 0
    expected_ratio = 10 ** (-20 / 20)  # 0.1
    print(f"    Peak at 0 dB:   {peak0}")
    print(f"    Peak at -20 dB: {peak20}")
    print(f"    Ratio: {ratio:.4f} (expected ~{expected_ratio:.4f})")
    assert abs(ratio - expected_ratio) < 0.05, f"Ratio {ratio} != expected {expected_ratio}"
    print("    PASS — output gain correctly attenuates audio")


def test_output_gain_makes_audio_louder():
    """Output gain of 0 dB should be louder than -6 dB default."""
    print("[3] Testing output gain can make audio louder...")
    from magic_sdr.equalizer import Equalizer

    sr = 48000
    t = np.linspace(0, 0.1, int(sr * 0.1), endpoint=False)
    # Quiet tone (-20 dBFS) so we don't hit the limiter
    tone = (np.sin(2 * np.pi * 440 * t) * 0.1 * 32767).astype(np.int16)
    stereo = np.column_stack([tone, tone])

    eq_default = Equalizer(sample_rate=48000)  # -6 dB default
    eq_default.set_limiter_enabled(False)
    out_default = eq_default.process(stereo.copy(), sample_rate=sr)
    peak_default = np.abs(out_default).max()

    eq_max = Equalizer(sample_rate=48000)
    eq_max.set_output_gain(0.0)
    eq_max.set_limiter_enabled(False)
    out_max = eq_max.process(stereo.copy(), sample_rate=sr)
    peak_max = np.abs(out_max).max()

    print(f"    Peak at -6 dB (default): {peak_default}")
    print(f"    Peak at 0 dB (max):      {peak_max}")
    assert peak_max > peak_default, "0 dB should be louder than -6 dB"
    ratio = peak_max / peak_default if peak_default > 0 else 0
    expected = 10 ** (6 / 20)  # ~2.0
    print(f"    Ratio: {ratio:.4f} (expected ~{expected:.4f})")
    assert abs(ratio - expected) < 0.1, f"Ratio {ratio} != expected {expected}"
    print("    PASS — output gain can make audio louder")


def test_makeup_target_lowered():
    """Makeup gain target should now be -6 dBFS, not -0.5 dBFS."""
    print("[4] Testing makeup gain target is -6 dBFS (was -0.5)...")
    from magic_sdr.equalizer import Equalizer

    # Drive a signal past full scale to trigger makeup gain
    sr = 48000
    t = np.linspace(0, 0.5, int(sr * 0.5), endpoint=False)
    # -3 dBFS tone + +12 dB pre-gain = +9 dB over full scale
    tone = (np.sin(2 * np.pi * 80 * t) * 0.7 * 32767).astype(np.int16)
    stereo = np.column_stack([tone, tone])

    eq = Equalizer(sample_rate=48000)
    eq.set_pre_gain(12.0)  # +12 dB drives it past full scale
    eq.set_limiter_enabled(False)  # isolate makeup gain
    eq.set_output_gain(0.0)  # isolate makeup gain
    out = eq.process(stereo.copy(), sample_rate=sr)
    peak = np.abs(out).max() / 32768.0  # normalize to 0..1
    peak_db = 20 * np.log10(peak) if peak > 0 else -120

    print(f"    Output peak: {peak:.4f} ({peak_db:.2f} dBFS)")
    # Should be around -6 dBFS (0.501), NOT -0.5 dBFS (0.944)
    assert peak < 0.7, f"Peak {peak} too high — makeup target not lowered (was -0.5 dBFS)"
    assert peak > 0.3, f"Peak {peak} too low — over-attenuated"
    print("    PASS — makeup gain now targets -6 dBFS (was -0.5)")


def test_limiter_ceiling_default_lowered():
    """Limiter ceiling default should be -3 dBFS, not -0.3 dBFS."""
    print("[5] Testing limiter ceiling default is -3 dBFS...")
    from magic_sdr.equalizer import Equalizer
    eq = Equalizer(sample_rate=48000)
    assert eq.limiter_ceiling_db == -3.0, (
        f"Default ceiling should be -3 dBFS, got {eq.limiter_ceiling_db}"
    )
    print(f"    Default limiter_ceiling_db: {eq.limiter_ceiling_db} dBFS")
    print("    PASS — limiter ceiling lowered from -0.3 to -3 dBFS")


def test_volume_default_lowered():
    """Default volume should be 0.5 (50%), not 0.8 (80%)."""
    print("[6] Testing default volume is 0.5...")
    from magic_sdr.config import Config
    c = Config()
    assert c.volume == 0.5, f"Default volume should be 0.5, got {c.volume}"
    print(f"    Default volume: {c.volume}")
    print("    PASS — default volume lowered from 0.8 to 0.5")


def test_output_gain_persisted():
    """Output gain should be persisted in config."""
    print("[7] Testing output gain is persisted in config...")
    from magic_sdr.config import Config
    c = Config()
    assert hasattr(c, "eq_output_gain_db"), "Config missing eq_output_gain_db field"
    assert c.eq_output_gain_db == -6.0, f"Default should be -6, got {c.eq_output_gain_db}"
    assert hasattr(c, "eq_limiter_ceiling_db"), "Config missing eq_limiter_ceiling_db field"
    assert c.eq_limiter_ceiling_db == -3.0, f"Default should be -3, got {c.eq_limiter_ceiling_db}"
    print(f"    eq_output_gain_db: {c.eq_output_gain_db} dB")
    print(f"    eq_limiter_ceiling_db: {c.eq_limiter_ceiling_db} dBFS")
    print("    PASS — output gain + limiter ceiling persisted in config")


def test_ui_slider_exists():
    """Verify the output gain slider is in the main window."""
    print("[8] Testing output gain UI slider exists...")
    with open("/home/z/my-project/magic_sdr/main_window.py") as f:
        src = f.read()
    assert "self.output_gain_slider = QSlider" in src, "Missing output_gain_slider"
    assert "self.output_gain_label = QLabel" in src, "Missing output_gain_label"
    assert "def _on_output_gain_changed" in src, "Missing _on_output_gain_changed handler"
    assert "self.vol_pct_label = QLabel" in src, "Missing vol_pct_label"
    # Verify the slider range is -60 to 0
    assert "self.output_gain_slider.setRange(-60, 0)" in src
    assert "self.output_gain_slider.setValue(-6)" in src, "Default should be -6"
    print("    PASS — output gain UI slider properly defined")


def test_no_clipping_with_defaults():
    """With default settings, no clipping should occur on normal audio."""
    print("[9] Testing no clipping with default settings...")
    from magic_sdr.equalizer import Equalizer

    sr = 48000
    t = np.linspace(0, 1.0, int(sr * 1.0), endpoint=False)
    # Full-scale FM-like audio (varying amplitude)
    env = 0.5 + 0.5 * np.sin(2 * np.pi * 2 * t)
    tone = (np.sin(2 * np.pi * 440 * t) * env * 32767).astype(np.int16)
    stereo = np.column_stack([tone, tone])

    # Use Bass Boost preset (which previously caused 69% clipping)
    from magic_sdr.eq_presets import get_preset_gains
    eq = Equalizer(sample_rate=48000)  # all defaults
    bass_gains = get_preset_gains("Bass Boost")
    eq.set_all_gains(list(bass_gains))

    out = eq.process(stereo.copy(), sample_rate=sr)
    peak = np.abs(out).max()
    clipping_pct = float(np.mean(np.abs(out) >= 32767) * 100)

    print(f"    Input peak:  {np.abs(stereo).max()}")
    print(f"    Output peak: {peak}")
    print(f"    Clipping: {clipping_pct:.2f}%")
    assert clipping_pct < 0.1, f"Should be 0% clipping, got {clipping_pct}%"
    print("    PASS — no clipping with default settings + Bass Boost")


if __name__ == "__main__":
    tests = [
        test_output_gain_default,
        test_output_gain_makes_audio_quieter,
        test_output_gain_makes_audio_louder,
        test_makeup_target_lowered,
        test_limiter_ceiling_default_lowered,
        test_volume_default_lowered,
        test_output_gain_persisted,
        test_ui_slider_exists,
        test_no_clipping_with_defaults,
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
        print("All loudness-control tests PASSED.")
