"""Import-only sanity check for all magic_sdr modules.

Runs from /home/z/my-project so the package is importable.
"""
import sys, os
sys.path.insert(0, "/home/z/my-project")

# Use a headless Qt platform so the test runs without an X server
os.environ["QT_QPA_PLATFORM"] = "offscreen"

errors = []

modules = [
    "magic_sdr",
    "magic_sdr.config",
    "magic_sdr.band_presets",
    "magic_sdr.gqrx_client",
    "magic_sdr.audio_receiver",
    "magic_sdr.spectrum",
    "magic_sdr.band_scanner",
    "magic_sdr.bookmark_manager",
    "magic_sdr.recording_manager",
    "magic_sdr.ai_tagger",
    "magic_sdr.web_server",
    "magic_sdr.main_window",
    "magic_sdr.main",
]

for m in modules:
    try:
        __import__(m)
        print(f"  OK  {m}")
    except Exception as e:
        errors.append((m, e))
        print(f"  FAIL {m}: {type(e).__name__}: {e}")

print()
if errors:
    print(f"FAILED: {len(errors)} modules failed to import")
    sys.exit(1)
print("All modules imported cleanly.")
