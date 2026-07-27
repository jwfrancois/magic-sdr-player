#!/usr/bin/env python3
"""Diagnose why EQ presets don't affect sound at runtime.

Constructs a MainWindow with the real config, then checks:
1. Is the equalizer enabled?
2. Are the gains actually set to the preset values?
3. Does equalizer.process() actually change the audio?
4. Is the audio_player receiving the processed chunk?
"""
import sys, os, tempfile
sys.path.insert(0, '/home/z/my-project')
os.environ['XDG_RUNTIME_DIR'] = '/tmp/runtime-z'
os.makedirs('/tmp/runtime-z', exist_ok=True)

import numpy as np
from PyQt5.QtWidgets import QApplication
from magic_sdr.config import Config
from magic_sdr.main_window import MainWindow
import magic_sdr.config as cfgmod

app = QApplication(sys.argv)

# Use the REAL config file (so we get the user's saved Bass Boost preset)
print(f"Config file: {cfgmod.CONFIG_FILE}")
config = Config.load()
print(f"Loaded config:")
print(f"  eq_enabled:      {config.eq_enabled}")
print(f"  eq_preset_name:  {config.eq_preset_name}")
print(f"  eq_gains:        {config.eq_gains}")
print(f"  audio_sample_rate: {config.audio_sample_rate}")
print(f"  audio_channels:    {config.audio_channels}")
print()

win = MainWindow(config)

print("=== After MainWindow construction + _apply_config ===")
print(f"  equalizer._enabled:   {win.equalizer._enabled}")
print(f"  equalizer.gains_db:   {win.equalizer.gains_db}")
print(f"  equalizer.is_flat():  {win.equalizer.is_flat()}")
print(f"  eq_enabled_chk:       {win.eq_enabled_chk.isChecked()}")
print(f"  eq_preset_combo:      {win.eq_preset_combo.currentText()}")
print(f"  slider values:        {[s.value() for s in win.eq_sliders]}")
print()

# Now simulate an audio chunk and trace it through
SR = 48000
DURATION = 1.0
N = int(SR * DURATION)
t = np.arange(N) / SR
# Strong 62 Hz tone (Bass Boost should amplify this by +10 dB)
tone = (0.3 * np.sin(2 * np.pi * 62 * t) * 32767).astype(np.int16)

print("=== Simulating audio chunk through _on_audio_chunk ===")
# Measure input energy at 62 Hz
from numpy.fft import rfft
def band_energy(sig, freq, sr=SR):
    n = len(sig)
    spec = np.abs(rfft(sig * np.hanning(n))) ** 2
    freqs = np.arange(n // 2 + 1) * sr / n
    bw = freq * (2 ** (1/6) - 1)
    mask = (freqs >= freq - bw) & (freqs <= freq + bw)
    return float(np.sqrt(np.sum(spec[mask])))

input_float = tone.astype(np.float32) / 32768.0
input_e = band_energy(input_float, 62)
print(f"  Input energy at 62 Hz: {input_e:.2f}")

# Process through the equalizer directly (as _on_audio_chunk does)
processed = win.equalizer.process(tone.copy(), sample_rate=SR)
out_float = processed.astype(np.float32) / 32768.0
out_e = band_energy(out_float, 62)
db_change = 20 * np.log10(out_e / input_e) if input_e > 0 else 0
print(f"  Output energy at 62 Hz: {out_e:.2f}")
print(f"  Change: {db_change:+.2f} dB  (expected ~+10 dB for Bass Boost at 62 Hz)")
print()

# Now check: does the audio_player actually receive the PROCESSED chunk?
# Monkey-patch audio_player.push to capture what it gets
received_chunks = []
original_push = win.audio_player.push
def spy_push(chunk):
    received_chunks.append(chunk.copy())
    return original_push(chunk)
win.audio_player.push = spy_push

# Also monkey-patch to make sure audio_player._running is True so push doesn't bail
win.audio_player._running = True
win.audio_player._muted = False
win.audio_player._volume = 1.0

# Call _on_audio_chunk
win._on_audio_chunk(tone.copy(), SR, 1)

print(f"=== After _on_audio_chunk ===")
print(f"  Chunks received by audio_player: {len(received_chunks)}")
if received_chunks:
    rc = received_chunks[0]
    rc_float = rc.astype(np.float32) / 32768.0
    rc_e = band_energy(rc_float, 62)
    rc_db = 20 * np.log10(rc_e / input_e) if input_e > 0 else 0
    print(f"  Chunk dtype: {rc.dtype}, shape: {rc.shape}")
    print(f"  Energy at 62 Hz in received chunk: {rc_e:.2f}")
    print(f"  Change vs input: {rc_db:+.2f} dB")
    if abs(rc_db) < 0.5:
        print()
        print("  *** PROBLEM CONFIRMED: audio_player received UN-EQ'd audio! ***")
        print("  The EQ is not being applied to the chunk that reaches playback.")
    elif rc_db > 5:
        print()
        print("  >>> OK: audio_player received EQ'd audio (+{:.1f} dB).".format(rc_db))
    else:
        print()
        print(f"  ??? Unexpected: {rc_db:+.2f} dB change (neither flat nor strong)")

# Also check: is _time_travel_replaying True? (would skip audio_player.push)
print()
print(f"  _time_travel_replaying: {win._time_travel_replaying}")
