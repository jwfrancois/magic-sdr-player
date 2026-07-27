"""Test the new feature modules: clock, tuning_knob, s_meter, equalizer,
solar, band_conditions, rds, Baltimore presets.

This is a comprehensive test that verifies each new module works correctly
in isolation, plus an integration test that verifies the whole MainWindow
works with all the new widgets.
"""

import os
import sys
import tempfile
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, '/home/z/my-project')


def test_clock_widget():
    """Test 1: Clock widget instantiates and shows time."""
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from magic_sdr.clock import ClockWidget
    w = ClockWidget()
    # Force a tick
    w._tick()
    # UTC label should have a time (HH:MM:SS format)
    utc = w.utc_label.text()
    local = w.local_label.text()
    assert len(utc) == 8 and utc.count(":") == 2, f"Bad UTC time: {utc!r}"
    assert len(local) == 8 and local.count(":") == 2, f"Bad local time: {local!r}"
    # Date label should have something
    assert len(w.date_label.text()) > 5
    w.stop()
    print("✓ Test 1 passed: ClockWidget works")


def test_tuning_knob():
    """Test 2: Tuning knob emits step signals."""
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from magic_sdr.tuning_knob import TuningKnob
    w = TuningKnob()
    # Default step should be 10 kHz (index 4)
    assert w.current_step_hz == 10000, f"Default step wrong: {w.current_step_hz}"
    # Capture step emissions
    emitted = []
    w.tune_step.connect(lambda s: emitted.append(s))
    # Simulate wheel up (positive step)
    from PyQt5.QtGui import QWheelEvent
    from PyQt5.QtCore import QPoint, Qt
    # Wheel events are tricky to fake; test the cycle step instead
    # There are 7 steps in DEFAULT_STEPS_HZ (1, 10, 100, 1k, 10k, 100k, 1M)
    # Cycling 7 times returns to start.
    w._cycle_step()  # index 4 → 5: 10 kHz → 100 kHz
    assert w.current_step_hz == 100000, f"After cycle: {w.current_step_hz}"
    w._cycle_step()  # index 5 → 6: 100 kHz → 1 MHz
    assert w.current_step_hz == 1000000, f"After 2 cycles: {w.current_step_hz}"
    # Cycle 5 more times to return to index 4 (10 kHz) — total 7 cycles
    for _ in range(5):
        w._cycle_step()
    assert w.current_step_hz == 10000, f"After full cycle (7): {w.current_step_hz}"
    print("✓ Test 2 passed: TuningKnob step cycling works")


def test_s_meter():
    """Test 3: S-meter converts dBFS to needle angle correctly."""
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from magic_sdr.s_meter import SMeterWidget
    w = SMeterWidget()
    # S1 (-94 dBFS) should map to -90 degrees
    angle_s1 = w._dbfs_to_angle(-94)
    assert abs(angle_s1 - (-90)) < 1, f"S1 angle wrong: {angle_s1}"
    # S9 (-40 dBFS) should map to ~0 degrees (vertical)
    angle_s9 = w._dbfs_to_angle(-40)
    assert abs(angle_s9) < 1, f"S9 angle wrong: {angle_s9}"
    # S9+40 (0 dBFS) should map to +90 degrees
    angle_max = w._dbfs_to_angle(0)
    assert abs(angle_max - 90) < 1, f"S9+40 angle wrong: {angle_max}"
    # Set level and verify target
    w.set_level(-50.0)
    assert w._target_level == -50.0
    # None = no signal (drops to S1)
    w.set_level(None)
    assert w._target_level is None
    print("✓ Test 3 passed: SMeterWidget dBFS-to-angle mapping works")


def test_equalizer_basic():
    """Test 4: Equalizer passes through flat audio unchanged and boosts when set."""
    import numpy as np
    from magic_sdr.equalizer import Equalizer, EQ_BANDS_HZ
    eq = Equalizer(sample_rate=48000, channels=2)
    # Generate a longer test signal: 1 kHz sine wave at -20 dB (amplitude 0.1)
    # so the +10 dB boost doesn't clip when converted back to int16.
    # (Real audio has lots of headroom; a full-scale sine would clip.)
    sr = 48000
    t = np.linspace(0, 1.0, sr, endpoint=False)
    sine = (np.sin(2 * np.pi * 1000 * t) * 3276).astype(np.int16)  # amplitude 3276 ≈ -20 dBFS
    chunk = np.column_stack([sine, sine])  # stereo
    # With flat EQ (all 0 dB), output should equal input
    out = eq.process(chunk, sample_rate=sr)
    assert np.array_equal(out, chunk), "Flat EQ should be a no-op"
    # Now set a band and verify output differs
    eq.set_band_gain(5, +10.0)  # +10 dB at 1 kHz (the test signal's freq)
    out2 = eq.process(chunk, sample_rate=sr)
    assert not np.array_equal(out2, chunk), "EQ with +10dB should change audio"
    # Verify the +10 dB boost actually amplifies (approximately).
    # We skip the first 5000 samples (~100 ms) to avoid the IIR filter's
    # startup transient and measure only the steady-state response.
    rms_in = np.sqrt(np.mean(sine[5000:].astype(np.float32) ** 2))
    rms_out = np.sqrt(np.mean(out2[5000:, 0].astype(np.float32) ** 2))
    ratio_db = 20 * np.log10(rms_out / rms_in)
    assert ratio_db > 5, f"+10 dB EQ should boost signal, got {ratio_db:.1f} dB"
    # Reset should make it flat again
    eq.reset()
    out3 = eq.process(chunk, sample_rate=sr)
    assert np.array_equal(out3, chunk), "After reset EQ should be no-op"
    # Disable should also be no-op
    eq.set_band_gain(5, +10.0)
    eq.set_enabled(False)
    out4 = eq.process(chunk, sample_rate=sr)
    assert np.array_equal(out4, chunk), "Disabled EQ should be no-op"
    print(f"✓ Test 4 passed: Equalizer works (flat = no-op, +10dB = +{ratio_db:.1f}dB boost)")


def test_equalizer_clamping():
    """Test 5: EQ gain is clamped to ±20 dB."""
    from magic_sdr.equalizer import Equalizer
    eq = Equalizer(sample_rate=48000, channels=2)
    eq.set_band_gain(0, +50.0)  # request +50
    assert eq.get_band_gain(0) == 20.0, f"Should clamp to +20, got {eq.get_band_gain(0)}"
    eq.set_band_gain(0, -50.0)  # request -50
    assert eq.get_band_gain(0) == -20.0, f"Should clamp to -20, got {eq.get_band_gain(0)}"
    print("✓ Test 5 passed: EQ gain clamped to ±20 dB")


def test_solar_fetcher_offline():
    """Test 6: SolarFetcher gracefully handles offline (no network) state."""
    from magic_sdr.solar import SolarFetcher, SolarConditions
    sf = SolarFetcher()
    # Without starting, get_current should return None
    assert sf.get_current() is None
    # A fresh SolarConditions should have all None fields
    cond = SolarConditions()
    assert cond.solar_flux is None
    assert cond.k_index is None
    assert cond.summary() == "—"
    assert not cond.is_storm
    assert not cond.is_quiet
    print("✓ Test 6 passed: SolarFetcher offline state is graceful")


def test_solar_conditions_summary():
    """Test 7: SolarConditions.summary() formats correctly."""
    from magic_sdr.solar import SolarConditions
    cond = SolarConditions(
        solar_flux=142.5,
        sunspot_number=87,
        a_index=12.0,
        k_index=4,
        xray_class="C",
    )
    s = cond.summary()
    assert "SFI 142" in s or "SFI 143" in s, f"SFI in summary: {s}"
    assert "SSN 87" in s
    assert "A 12" in s
    assert "K 4" in s
    assert "X-ray C" in s
    # K=4 is "active" (not storm, not quiet)
    assert not cond.is_storm
    assert not cond.is_quiet
    # K=5 is storm
    cond.k_index = 5
    assert cond.is_storm
    # K=2 is quiet
    cond.k_index = 2
    assert cond.is_quiet
    print("✓ Test 7 passed: SolarConditions.summary() formats correctly")


def test_band_conditions_estimator():
    """Test 8: band_conditions.estimate_band_conditions returns 10 bands."""
    from magic_sdr.band_conditions import estimate_band_conditions, rating_to_stars, band_color
    from magic_sdr.solar import SolarConditions
    cond = SolarConditions(solar_flux=150, k_index=2, xray_class="B")
    bands = estimate_band_conditions(cond)
    # Should have all 10 HF bands
    assert len(bands) == 10, f"Expected 10 bands, got {len(bands)}"
    band_names = [bc.band for bc in bands]
    assert "160m" in band_names
    assert "80m" in band_names
    assert "20m" in band_names
    assert "10m" in band_names
    # Each band's rating should be 1-5
    for bc in bands:
        assert 1 <= bc.rating <= 5
        assert bc.label in ["Excellent", "Good", "Fair", "Poor", "Closed"]
    # Stars should be 5 chars total (★☆)
    stars = rating_to_stars(3)
    assert len(stars) == 5
    assert stars.count("★") == 3
    assert stars.count("☆") == 2
    # Colors
    assert band_color(5) == "#3aaa55"  # green
    assert band_color(1) == "#cc4444"  # red
    # With a bad solar storm, conditions should be worse
    cond_storm = SolarConditions(solar_flux=80, k_index=7, xray_class="X")
    bands_storm = estimate_band_conditions(cond_storm)
    # Average rating should be lower than the quiet case
    avg_quiet = sum(bc.rating for bc in bands) / len(bands)
    avg_storm = sum(bc.rating for bc in bands_storm) / len(bands_storm)
    assert avg_storm < avg_quiet, f"Storm should be worse: quiet={avg_quiet}, storm={avg_storm}"
    print(f"✓ Test 8 passed: 10 bands estimated, storm={avg_storm:.1f} < quiet={avg_quiet:.1f}")


def test_rds_decoder_no_pilot_at_low_sr():
    """Test 9: RDS decoder returns no pilot at low sample rates (no 19 kHz visible)."""
    import numpy as np
    from magic_sdr.rds import RDSDecoder
    # At 32 kHz sample rate, Nyquist is 16 kHz — can't see 19 kHz pilot
    dec = RDSDecoder(sample_rate=32000)
    # Generate silence
    chunk = np.zeros(8192, dtype=np.int16)
    info = dec.process_audio(chunk, 32000)
    assert not info.stereo_pilot_detected, "Should not detect pilot at 32 kHz SR"
    assert info.pilot_strength_db is None, "Pilot strength should be None at low SR"
    print("✓ Test 9 passed: RDS decoder correctly handles low sample rate")


def test_rds_decoder_detects_pilot():
    """Test 10: RDS decoder detects a synthetic 19 kHz pilot tone."""
    import numpy as np
    from magic_sdr.rds import RDSDecoder
    # At 48 kHz sample rate, 19 kHz is well within Nyquist (24 kHz)
    dec = RDSDecoder(sample_rate=48000)
    sr = 48000
    t = np.linspace(0, 0.5, sr // 2, endpoint=False)
    # Strong 19 kHz pilot + low-frequency audio
    pilot = np.sin(2 * np.pi * 19000 * t) * 0.2
    audio = np.sin(2 * np.pi * 1000 * t) * 0.5 + pilot
    chunk = (audio * 32767).astype(np.int16)
    info = dec.process_audio(chunk, sr)
    assert info.stereo_pilot_detected, "Should detect 19 kHz pilot tone"
    assert info.pilot_strength_db is not None
    assert info.pilot_strength_db > 15.0, f"Pilot strength should be >15 dB, got {info.pilot_strength_db:.1f}"
    print(f"✓ Test 10 passed: RDS decoder detects pilot ({info.pilot_strength_db:.1f} dB above noise)")


def test_rds_decoder_silence():
    """Test 11: RDS decoder doesn't false-trigger on silence."""
    import numpy as np
    from magic_sdr.rds import RDSDecoder
    dec = RDSDecoder(sample_rate=48000)
    chunk = np.zeros(8192, dtype=np.int16)
    info = dec.process_audio(chunk, 48000)
    assert not info.stereo_pilot_detected, "Silence should not trigger pilot detection"
    print("✓ Test 11 passed: RDS decoder doesn't false-trigger on silence")


def test_baltimore_presets():
    """Test 12: Baltimore AM/FM presets are present and labeled correctly."""
    from magic_sdr.band_presets import FM_BROADCAST, AM_BROADCAST, BANDS, BANDS_BY_NAME
    # FM
    assert "AM Broadcast" in BANDS_BY_NAME
    # Check key Baltimore FM stations
    assert 97900000 in FM_BROADCAST.known, "WIYY 97.9 missing"
    assert "WIYY" in FM_BROADCAST.known[97900000]
    assert 1090000 in AM_BROADCAST.known, "WBAL 1090 missing"
    assert "WBAL" in AM_BROADCAST.known[1090000]
    # Count Baltimore FM stations (should be ~15)
    assert len(FM_BROADCAST.known) >= 15, f"FM stations: {len(FM_BROADCAST.known)}"
    # Count Baltimore AM stations (should be ~9)
    assert len(AM_BROADCAST.known) >= 9, f"AM stations: {len(AM_BROADCAST.known)}"
    # All bands should be in BANDS list
    assert FM_BROADCAST in BANDS
    assert AM_BROADCAST in BANDS
    print(f"✓ Test 12 passed: Baltimore presets present ({len(FM_BROADCAST.known)} FM, {len(AM_BROADCAST.known)} AM)")


def test_main_window_full_integration():
    """Test 13: MainWindow instantiates with ALL new features."""
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from magic_sdr.main_window import MainWindow
    from magic_sdr.config import Config
    cfg = Config()
    mw = MainWindow(config=cfg)
    # All new attributes must exist
    for attr in ['clock', 'tuning_knob', 's_meter', 'equalizer', 'eq_sliders',
                 'eq_enabled_chk', 'rds_decoder', 'solar_fetcher',
                 'solar_summary_label', 'solar_detail_labels',
                 'band_conditions_label', 'rds_labels', 'conditions_timer']:
        assert hasattr(mw, attr), f"Missing attribute: {attr}"
    # Tabs
    tab_names = [mw.tabs.tabText(i) for i in range(mw.tabs.count())]
    assert 'Conditions' in tab_names
    assert 'Signal Info' in tab_names
    # EQ sliders count
    assert len(mw.eq_sliders) == 10
    # EQ functionality
    mw.eq_sliders[3].setValue(8)
    assert mw.equalizer.get_band_gain(3) == 8.0
    # Test EQ reset
    mw._on_eq_reset()
    for s in mw.eq_sliders:
        assert s.value() == 0
    # Test conditions update doesn't crash
    mw._update_conditions()
    # Test RDS update doesn't crash
    mw._update_rds_panel()
    # Test knob step handler
    mw._on_knob_step(5000)  # +5 kHz
    # Test signal level drives S-meter
    mw._on_signal_level(-50.0)
    assert mw.s_meter._target_level == -50.0
    print("✓ Test 13 passed: Full MainWindow integration with all new features")
    # Stop the solar fetcher to clean up
    mw.solar_fetcher.stop()


def main():
    print("Running new features tests...\n")
    tests = [
        test_clock_widget,
        test_tuning_knob,
        test_s_meter,
        test_equalizer_basic,
        test_equalizer_clamping,
        test_solar_fetcher_offline,
        test_solar_conditions_summary,
        test_band_conditions_estimator,
        test_rds_decoder_no_pilot_at_low_sr,
        test_rds_decoder_detects_pilot,
        test_rds_decoder_silence,
        test_baltimore_presets,
        test_main_window_full_integration,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"✗ {t.__name__} FAILED: {e}")
            import traceback
            traceback.print_exc()
    print(f"\n{'='*60}")
    print(f"Results: {passed}/{len(tests)} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
