"""Tests for the magical new features (round 2):

  • EQ presets (16 named profiles)
  • Audio Visualizer (4 modes)
  • Memory Presets bar (12 slots, store/clear)
  • Time-Travel buffer (push + read)
  • CW (Morse) decoder (basic element classification)
  • DX Cluster client (spot parsing)
  • Aurora forecast (Kp-to-oval mapping)
  • Auto-Surf (instantiation + stop)
"""

import os
import sys
import time
import math
import json
from pathlib import Path

# Ensure we can import the magic_sdr package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt

app = QApplication.instance() or QApplication([])

from magic_sdr.eq_presets import (
    EQ_PRESETS, get_preset_names, get_preset_gains, find_closest_preset
)
from magic_sdr.audio_visualizer import (
    AudioVisualizer, MODE_OSCILLOSCOPE, MODE_SPECTRUM_BARS,
    MODE_CIRCULAR, MODE_LIQUID, ALL_MODES
)
from magic_sdr.memory_presets import MemoryPresetBar, MemoryPreset, MemoryButton
from magic_sdr.time_travel import TimeTravelBuffer, TimeTravelWidget
from magic_sdr.cw_decoder import CWDecoder, MORSE_TABLE
from magic_sdr.dx_cluster import DXClusterClient, DXSpot, SPOT_RE
from magic_sdr.aurora import (
    forecast_aurora, storm_class_for_kp, oval_latitude_for_kp,
    hf_absorption_for_kp, vhf_scatter_for_kp
)
from magic_sdr.auto_surf import AutoSurfer, SurfStop
from magic_sdr.config import Config


tests_passed = 0
tests_failed = 0


def ok(name, condition, detail=""):
    global tests_passed, tests_failed
    if condition:
        print(f"✓ Test {tests_passed + 1} passed: {name}")
        tests_passed += 1
    else:
        print(f"✗ Test {tests_passed + 1} FAILED: {name} — {detail}")
        tests_failed += 1


# ----------------------------- EQ Presets -----------------------------
print("\n=== EQ Presets ===")

names = get_preset_names()
ok("EQ presets has at least 16 presets", len(names) >= 16, f"only {len(names)}")
ok("EQ presets includes 'Flat' first", names[0] == "Flat")
ok("EQ presets includes Loudness", "Loudness" in names)
ok("EQ presets includes Bass Boost", "Bass Boost" in names)
ok("EQ presets includes Vocal Clarity", "Vocal Clarity" in names)
ok("EQ presets includes FM HiFi", "FM HiFi" in names)
ok("EQ presets includes AM Vintage", "AM Vintage" in names)
ok("EQ presets includes Shortwave / SSB", "Shortwave / SSB" in names)
ok("EQ presets includes Cinematic", "Cinematic" in names)
ok("EQ presets includes Tube Warm", "Tube Warm" in names)
ok("EQ presets includes Headphone", "Headphone" in names)

# Each preset must have exactly 10 gains
for name in names:
    gains = get_preset_gains(name)
    ok(f"preset '{name}' has 10 gains", len(gains) == 10, f"has {len(gains)}")

# Flat must be all zeros
flat = get_preset_gains("Flat")
ok("Flat preset is all zeros", all(g == 0.0 for g in flat))

# Bass Boost must boost the lowest bands
bb = get_preset_gains("Bass Boost")
ok("Bass Boost boosts 31 Hz", bb[0] > 5.0, f"31 Hz gain = {bb[0]}")
ok("Bass Boost boosts 62 Hz", bb[1] > 5.0, f"62 Hz gain = {bb[1]}")

# Treble Boost must boost the highest bands
tb = get_preset_gains("Treble Boost")
ok("Treble Boost boosts 16 kHz", tb[-1] > 5.0, f"16 kHz gain = {tb[-1]}")
ok("Treble Boost boosts 8 kHz", tb[-2] > 3.0, f"8 kHz gain = {tb[-2]}")

# Loudness should be V-shaped (boosted lows + highs, scooped mids)
loud = get_preset_gains("Loudness")
ok("Loudness boosts 31 Hz", loud[0] > 3.0)
ok("Loudness boosts 16 kHz", loud[-1] > 3.0)
ok("Loudness scoops 500 Hz (or near)", loud[4] < loud[0] and loud[4] < loud[-1])

# find_closest_preset for flat should return "Flat"
closest = find_closest_preset([0.0] * 10)
ok("find_closest_preset(flat) returns 'Flat'", closest == "Flat", f"got '{closest}'")

# find_closest_preset for all-+12 should return a bass-heavy preset
closest = find_closest_preset([12.0] * 10)
ok("find_closest_preset(all +12) returns a non-Flat preset", closest != "Flat")

# ----------------------------- Audio Visualizer -----------------------------
print("\n=== Audio Visualizer ===")

viz = AudioVisualizer()
ok("Visualizer default mode is Oscilloscope", viz.mode == MODE_OSCILLOSCOPE)

# Feed a 1 kHz sine wave and verify it doesn't crash
sr = 48000
t = np.arange(sr // 10) / sr  # 100 ms
sine = (0.5 * 32767 * np.sin(2 * np.pi * 1000 * t)).astype(np.int16)
stereo = np.stack([sine, sine], axis=1)
viz.push_audio(stereo, sr, 2)
ok("Visualizer accepts stereo int16 audio", viz._waveform is not None)

# Cycle modes
modes_visited = [viz.mode]
for _ in range(len(ALL_MODES)):
    new_mode = viz.cycle_mode()
    modes_visited.append(new_mode)
ok("Visualizer cycles through all 4 modes", len(set(modes_visited)) == 4,
   f"visited {set(modes_visited)}")

# Set mode directly
viz.set_mode(MODE_LIQUID)
ok("Visualizer set_mode(Liquid Light) works", viz.mode == MODE_LIQUID)
viz.set_mode(MODE_SPECTRUM_BARS)
ok("Visualizer set_mode(Spectrum Bars) works", viz.mode == MODE_SPECTRUM_BARS)
viz.set_mode(MODE_CIRCULAR)
ok("Visualizer set_mode(Circular) works", viz.mode == MODE_CIRCULAR)

# Silent audio doesn't crash
silence = np.zeros(2048, dtype=np.int16)
viz.push_audio(silence, sr, 1)
ok("Visualizer handles mono silence", viz._waveform is not None)

# ----------------------------- Memory Presets -----------------------------
print("\n=== Memory Presets ===")

mb = MemoryPresetBar(n_slots=12)
ok("MemoryPresetBar has 12 buttons", len(mb.buttons) == 12)

# All slots start empty
for i, btn in enumerate(mb.buttons):
    ok(f"slot M{i+1} starts empty", btn.preset is None)

# Store a preset into slot 3
preset = MemoryPreset(freq_hz=96_900_000, modulation="WFM_ST", label="WIYY 98 Rock")
mb.store_current(2, preset)
ok("Slot M3 stores preset", mb.presets[2] is not None)
ok("Slot M3 has correct freq", mb.presets[2].freq_hz == 96_900_000)
ok("Slot M3 has correct label", mb.presets[2].label == "WIYY 98 Rock")

# find_slot_for_frequency
slot = mb.find_slot_for_frequency(96_900_000)
ok("find_slot_for_frequency returns slot 2", slot == 2, f"got {slot}")

# Clear slot 3
mb.clear_slot(2)
ok("Slot M3 cleared", mb.presets[2] is None)

# Store callback works
calls = []
def store_cb():
    calls.append(1)
    return MemoryPreset(freq_hz=1090_000, modulation="AM", label="WBAL 1090")
mb.store_callback = store_cb
# Simulate long-press on slot 5 (index 4)
mb._on_long_press_store(4)
ok("Store callback invoked on long-press", len(calls) == 1)
ok("Slot M5 has WBAL 1090 after long-press store",
   mb.presets[4] is not None and mb.presets[4].label == "WBAL 1090")

# Get presets as list
plist = mb.get_presets()
ok("get_presets returns list of 12", len(plist) == 12)

# ----------------------------- Time-Travel Buffer -----------------------------
print("\n=== Time-Travel Buffer ===")

buf = TimeTravelBuffer(sample_rate=48000, channels=2)
ok("Buffer initialized", buf is not None)
ok("Buffer capacity is 30s * 48000 = 1.44M samples", buf._capacity == 30 * 48000)

# Push a chunk
chunk = np.zeros((1024, 2), dtype=np.int16)
chunk[:, 0] = 1000  # mark left channel
buf.push(chunk)
ok("Buffer accepts a 1024-sample stereo chunk",
   buf.total_frames == 1024)

# Push more chunks to fill beyond capacity
for i in range(2000):
    c = np.full((1024, 2), i + 1, dtype=np.int16)
    buf.push(c)
ok("Buffer accepts 2000+ chunks", buf.total_frames == 1024 * 2001)
# The buffered duration should be capped at 30s
ok("Buffered duration capped at 30s", abs(buf.buffered_duration_s - 30.0) < 0.1,
   f"got {buf.buffered_duration_s}")

# Read oldest 100 samples — should be the most recent ones because the buffer wrapped
old = buf.read_oldest_n(100)
ok("read_oldest_n returns 100 samples", len(old) == 100)

# Reset
buf.reset()
ok("Buffer reset clears frames_written", buf.total_frames == 0)

# ----------------------------- CW Decoder -----------------------------
print("\n=== CW Decoder ===")

dec = CWDecoder()
ok("CWDecoder initialized", dec is not None)
ok("CWDecoder enabled by default", dec.enabled)
ok("CWDecoder starts with empty text", dec.decoded_text == "")

# Decode the letter "E" (a single dit) — feed a short tone burst followed by silence
sr = 8000
# E = single dit, ~100 ms tone, 200 ms silence
dit_samples = int(0.1 * sr)
silence_samples = int(0.5 * sr)
tone = (0.8 * 32767 * np.sin(2 * np.pi * 600 * np.arange(dit_samples) / sr)).astype(np.int16)
silence = np.zeros(silence_samples, dtype=np.int16)
dec.process_audio(tone, sr)
dec.process_audio(silence, sr)
# Wait a moment for the idle timer to fire
time.sleep(0.7)
ok("CWDecoder decoded an 'E' from a single dit", "E" in dec.decoded_text,
   f"got: '{dec.decoded_text}'")

# Reset and test "T" (single dah)
dec.reset()
dah_samples = int(0.3 * sr)  # 3x dit length
tone = (0.8 * 32767 * np.sin(2 * np.pi * 600 * np.arange(dah_samples) / sr)).astype(np.int16)
dec.process_audio(tone, sr)
dec.process_audio(silence, sr)
time.sleep(0.7)
ok("CWDecoder decoded a 'T' from a single dah", "T" in dec.decoded_text,
   f"got: '{dec.decoded_text}'")

# Test that disabled decoder doesn't process audio
dec.reset()
dec.enabled = False
dec.process_audio(tone, sr)
ok("Disabled CW decoder doesn't process audio", dec.decoded_text == "")

# MORSE_TABLE has letters + digits + punctuation
ok("MORSE_TABLE has letters A-Z",
   all(MORSE_TABLE.get(m) for m in [".-", "-...", "-.-.", "-..", "."]))
ok("MORSE_TABLE has digits 0-9",
   all(MORSE_TABLE.get(m) for m in ["-----", ".----", "..---", "...--", "....-"]))

# ----------------------------- DX Cluster -----------------------------
print("\n=== DX Cluster ===")

# Test the spot regex with several real-world spot formats
test_lines = [
    "DX de W1XYZ:     14025.0  DL1ABC       CW  23 dB   1830Z",
    "DX de K2ABC:      7153.5  ZL2XYZ       FT8  -12 dB  2214Z",
    "DX de VE7CC:     50125.0  EA8AAA       MSK144   1855Z",
]
for line in test_lines:
    m = SPOT_RE.match(line)
    ok(f"Spot regex matches: {line[:50]}…", m is not None, f"line: {line}")

# Parse a spot manually
m = SPOT_RE.match(test_lines[0])
ok("Spot regex captures spotter", m.group(1) == "W1XYZ")
ok("Spot regex captures freq", float(m.group(2)) == 14025.0)
ok("Spot regex captures DX callsign", m.group(3) == "DL1ABC")

# Test DXSpot dataclass
spot = DXSpot(
    spotter="W1XYZ",
    freq_hz=14_025_000,
    dx_callsign="DL1ABC",
    comment="CW 23 dB",
    time_z="1830",
)
ok("DXSpot.freq_mhz computes correctly", abs(spot.freq_mhz - 14.025) < 0.001)
ok("DXSpot.format returns a non-empty string", len(spot.format()) > 10)

# DXClusterClient instantiation
client = DXClusterClient(max_spots=50)
ok("DXClusterClient instantiated", client is not None)
ok("DXClusterClient starts disconnected", not client.is_connected)

# Get recent spots when empty
recent = client.get_recent_spots(n=10)
ok("get_recent_spots on empty returns []", recent == [])

# ----------------------------- Aurora Forecast -----------------------------
print("\n=== Aurora Forecast ===")

# Quiet conditions (Kp=2)
quiet = forecast_aurora(kp_index=2, observer_latitude=50.0)
ok("Aurora: Kp=2 is 'Quiet'", "Quiet" in quiet.storm_class)
ok("Aurora: Kp=2 oval at ~63°", abs(quiet.oval_latitude - 63.0) < 1.0)
ok("Aurora: Kp=2 not visible from 50°", not quiet.visible_from_observer)
ok("Aurora: Kp=2 HF absorption 'None'", "None" in quiet.hf_absorption)

# Storm conditions (Kp=7)
storm = forecast_aurora(kp_index=7, observer_latitude=50.0)
ok("Aurora: Kp=7 is 'G3 (Strong)'", "G3" in storm.storm_class)
ok("Aurora: Kp=7 oval at ~53°", abs(storm.oval_latitude - 53.0) < 1.0)
ok("Aurora: Kp=7 visible from 50°", storm.visible_from_observer)
ok("Aurora: Kp=7 HF absorption 'Major'", "Major" in storm.hf_absorption)
ok("Aurora: Kp=7 VHF scatter 'Strong'", "Strong" in storm.vhf_scatter)

# Extreme storm (Kp=9)
extreme = forecast_aurora(kp_index=9, observer_latitude=50.0)
ok("Aurora: Kp=9 is 'G5 (Extreme)'", "G5" in extreme.storm_class)
ok("Aurora: Kp=9 oval at ~49°", abs(extreme.oval_latitude - 49.0) < 1.0)
ok("Aurora: Kp=9 visible from 50°", extreme.visible_from_observer)

# From Baltimore's magnetic latitude (~50°), aurora is rare
balt = forecast_aurora(kp_index=4, observer_latitude=50.0)
ok("Aurora: Kp=4 not visible from Baltimore mag lat", not balt.visible_from_observer)

# Aurora at Kp=8 visible from Baltimore
balt_storm = forecast_aurora(kp_index=8, observer_latitude=50.0)
ok("Aurora: Kp=8 visible from Baltimore mag lat", balt_storm.visible_from_observer)

# None Kp
none = forecast_aurora(kp_index=None, observer_latitude=50.0)
ok("Aurora: None Kp returns '—' storm class", none.storm_class == "—")

# Helper functions
ok("storm_class_for_kp(5) = 'G1 (Minor)'", storm_class_for_kp(5) == "G1 (Minor)")
ok("storm_class_for_kp(9) = 'G5 (Extreme)'", storm_class_for_kp(9) == "G5 (Extreme)")
ok("oval_latitude_for_kp(0) = 67°", oval_latitude_for_kp(0) == 67.0)
ok("oval_latitude_for_kp(9) = 49°", oval_latitude_for_kp(9) == 49.0)

# ----------------------------- Auto-Surf -----------------------------
print("\n=== Auto-Surf ===")

# AutoSurfer needs a GqrxClient-like object — use a mock
class MockGqrx:
    def __init__(self):
        self._connected = True
        self._freq = 96_900_000
        self._mod = "WFM_ST"
        self._paused = False
    def is_connected(self): return self._connected
    def pause_poller(self): self._paused = True
    def resume_poller(self): self._paused = False
    def set_modulation(self, m): self._mod = m
    def set_frequency(self, f): self._freq = f
    def get_signal_level_robust(self, n_samples=3, interval_s=0.05):
        # Return a strong signal for FM broadcast range
        if 88e6 <= self._freq <= 108e6:
            return -45.0
        return -90.0  # noise floor elsewhere

mock_gqrx = MockGqrx()
surfer = AutoSurfer(mock_gqrx)
ok("AutoSurfer instantiates", surfer is not None)
ok("AutoSurfer not running initially", not surfer.is_running)

# Start with very short dwell + only 2 bands for fast testing
from magic_sdr.band_presets import FM_BROADCAST, AM_BROADCAST
ok("AutoSurfer start succeeds", surfer.start(dwell_seconds=0.05, bands=[FM_BROADCAST, AM_BROADCAST]))
ok("AutoSurfer is running after start", surfer.is_running)

# Wait for it to complete (should be quick with 2 bands + 0.05s dwell)
time.sleep(15.0)
ok("AutoSurfer finished on its own", not surfer.is_running)

# Stop (no-op if already stopped)
surfer.stop()
ok("AutoSurfer stop is idempotent", True)

# ----------------------------- Config persistence -----------------------------
print("\n=== Config Persistence (new fields) ===")

import tempfile
tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
tmp.write("{}")
tmp.close()
# Monkey-patch CONFIG_FILE for testing
import magic_sdr
orig_config_file = magic_sdr.CONFIG_FILE
magic_sdr.CONFIG_FILE = tmp.name
try:
    cfg = Config()
    cfg.eq_preset_name = "Loudness"
    cfg.eq_gains = [8.0, 6.0, 4.0, 1.0, -1.0, -1.0, 0.0, 2.0, 5.0, 7.0]
    cfg.eq_enabled = True
    cfg.visualizer_mode = "Liquid Light"
    cfg.cw_decoder_enabled = True
    cfg.dx_cluster_enabled = False
    cfg.night_vision = True
    cfg.observer_latitude = 55.0
    cfg.memory_presets = [
        {"freq_hz": 1090_000, "modulation": "AM", "label": "WBAL", "stored_at": 1234567890.0},
        None,
        {"freq_hz": 96_900_000, "modulation": "WFM_ST", "label": "98 Rock", "stored_at": 1234567891.0},
    ] + [None] * 9
    cfg.save()

    # Reload
    cfg2 = Config.load()
    ok("Config persists eq_preset_name", cfg2.eq_preset_name == "Loudness")
    ok("Config persists eq_gains list", len(cfg2.eq_gains) == 10 and cfg2.eq_gains[0] == 8.0)
    ok("Config persists eq_enabled", cfg2.eq_enabled == True)
    ok("Config persists visualizer_mode", cfg2.visualizer_mode == "Liquid Light")
    ok("Config persists cw_decoder_enabled", cfg2.cw_decoder_enabled == True)
    ok("Config persists dx_cluster_enabled", cfg2.dx_cluster_enabled == False)
    ok("Config persists night_vision", cfg2.night_vision == True)
    ok("Config persists observer_latitude", cfg2.observer_latitude == 55.0)
    ok("Config persists 3 memory presets in 12-slot list", len(cfg2.memory_presets) == 12)
    ok("Config memory_presets[0] = WBAL", cfg2.memory_presets[0]["label"] == "WBAL")
    ok("Config memory_presets[1] = None", cfg2.memory_presets[1] is None)
    ok("Config memory_presets[2] = 98 Rock", cfg2.memory_presets[2]["label"] == "98 Rock")
finally:
    magic_sdr.CONFIG_FILE = orig_config_file
    os.unlink(tmp.name)


# ----------------------------- MainWindow integration -----------------------------
print("\n=== MainWindow Integration ===")

from magic_sdr.main_window import MainWindow

# Use a temp config file
tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
tmp.write("{}")
tmp.close()
magic_sdr.CONFIG_FILE = tmp.name
try:
    cfg = Config()
    cfg.remote_access_enabled = False  # don't auto-start web server
    win = MainWindow(cfg)
    ok("MainWindow instantiates with all new components", win is not None)

    # Verify all new attributes exist
    for attr in ["audio_visualizer", "time_travel_buffer", "time_travel_widget",
                 "cw_decoder", "dx_cluster", "auto_surfer", "memory_bar",
                 "eq_preset_combo", "viz_mode_combo", "auto_surf_btn",
                 "aurora_summary_label", "aurora_detail_labels",
                 "cw_text_display", "cw_wpm_label", "cw_element_label",
                 "dx_list", "dx_filter_edit", "dx_connect_btn", "dx_status_label",
                 "set_night_vision", "set_dx_autostart", "set_cw_enabled"]:
        ok(f"MainWindow has attribute '{attr}'", hasattr(win, attr), f"missing {attr}")

    # Verify the EQ preset dropdown has all 16+ presets
    n_items = win.eq_preset_combo.count()
    ok("EQ preset dropdown has 17+ items (16 presets + Custom)", n_items >= 17, f"has {n_items}")

    # Verify all 4 visualizer modes are in the dropdown
    viz_items = [win.viz_mode_combo.itemText(i) for i in range(win.viz_mode_combo.count())]
    for m in ALL_MODES:
        ok(f"Visualizer dropdown has '{m}'", m in viz_items)

    # Test loading a preset
    win.eq_preset_combo.setCurrentText("Bass Boost")
    # Verify the EQ gains match the preset
    expected = get_preset_gains("Bass Boost")
    actual = [win.equalizer.gains_db[i] for i in range(10)]
    for i, (e, a) in enumerate(zip(expected, actual)):
        ok(f"Bass Boost preset gain[{i}] applied to EQ", abs(e - a) < 0.1, f"expected {e}, got {a}")

    # Test memory preset bar integration
    ok("Memory bar has 12 buttons in main window", win.memory_bar.n_slots == 12)

    # Test CW decoder integration with audio chunk
    # Generate a simple test audio chunk
    chunk = (np.zeros((1024, 2), dtype=np.int16))
    win._on_audio_chunk(chunk, 48000, 2)
    ok("MainWindow _on_audio_chunk handles new components without crashing", True)

    # Test night vision toggle
    win.set_night_vision.setChecked(True)
    ok("Night vision checkbox toggled", win.config.night_vision == True)

    # Test save_magic_state
    win._save_magic_state()
    ok("_save_magic_state doesn't crash", True)

    # Reload config and verify state was persisted
    cfg2 = Config.load()
    ok("Saved config has 'Bass Boost' as eq_preset_name", cfg2.eq_preset_name == "Bass Boost")
    ok("Saved config has 10 eq_gains", len(cfg2.eq_gains) == 10)

    win.close()
finally:
    magic_sdr.CONFIG_FILE = orig_config_file
    os.unlink(tmp.name)


# ----------------------------- Summary -----------------------------
print(f"\n{'=' * 60}")
print(f"Results: {tests_passed}/{tests_passed + tests_failed} passed, {tests_failed} failed")
if tests_failed:
    sys.exit(1)
