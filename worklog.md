---
Task ID: magic-sdr-player-1.0
Agent: main (Super Z)
Task: Build a "magical streaming player" desktop app on top of Gqrx SDR for an RTL-SDR V3 dongle on Linux. Features: auto-discover, waterfall, recordings, bookmarks, AI tags, remote access. Bands: FM broadcast, Airband, NOAA weather, 2m ham, Marine VHF, Shortwave. Scope: Deluxe. Gqrx already installed.

Work Log:
- Asked user 7 clarifying questions (app shape, platform, magic features, bands, Gqrx state, scope, output). Got: Desktop app, Linux, all 6 magic features, all 6 bands, Gqrx already installed, Deluxe scope, code project.
- Installed Python dependencies: PyQt5, pyqtgraph, sounddevice (PyAudio couldn't build due to missing portaudio.h; switched to sounddevice which bundles portaudio), uvicorn, jinja2, aiofiles. Verified all import.
- Built project structure:
  - magic_sdr/__init__.py — package metadata + network ports (7356 Gqrx TCP, 7355 audio UDP, 7357 spectrum UDP, 8000 web)
  - magic_sdr/config.py — JSON-backed Config dataclass with all user prefs
  - magic_sdr/band_presets.py — 6 Band NamedTuples with start/end MHz, default modulation, scan step, and known-channel dictionaries. 88 known channels total (NOAA WX-1..7, marine Ch 1-28, ATC freqs, ham calling freqs, WWV time stations, international SW broadcasters).
  - magic_sdr/gqrx_client.py — GqrxClient (QObject) with TCP connect, set/get frequency, set/get modulation, set squelch/gain, get signal level, AOS/LOS recording. Background poller emits Qt signals every 500 ms.
  - magic_sdr/audio_receiver.py — AudioReceiver (QObject) binds UDP port 7355, parses int16 PCM, emits chunk_ready signal. AudioPlayer wraps sounddevice.RawOutputStream with volume/mute and a lock-free queue for low-latency playback.
  - magic_sdr/spectrum.py — SpectrumReceiver (QObject) binds UDP port 7357, parses float32 magnitude (or complex I/Q), emits spectrum_ready. WaterfallWidget (QWidget) renders live spectrum (pyqtgraph line plot) + scrolling waterfall (pyqtgraph ImageItem with turbo colormap), supports click-to-tune.
  - magic_sdr/band_scanner.py — BandScanner (QObject) sweeps a band at step_kHz intervals, dwells, checks signal level vs threshold, emits station_found for each active frequency. Optionally invokes AITagger. DiscoveredStation dataclass with label/ai_tag/modulation.
  - magic_sdr/bookmark_manager.py — BookmarkManager (QObject) with JSON persistence, CRUD, search, band-filtering. Seeds 88 defaults on first run.
  - magic_sdr/recording_manager.py — RecordingManager (QObject) writes WAV + companion JSON metadata (freq, mod, band, label, ai_tag, start/end, sample rate, channels, peak/avg level). Supports scheduled recordings via background threads.
  - magic_sdr/ai_tagger.py — AITagger (QObject) computes 5 audio features (spectral centroid, ZCR, RMS, dominant freq, bandwidth) from raw PCM via numpy FFT, then invokes a Node helper (scripts/ai_helper.js) that calls z-ai-web-dev-sdk to classify as music/talk/weather/aviation/ham/marine/noise/data/unknown + language + summary. Gracefully degrades if Node SDK unavailable.
  - scripts/ai_helper.js — Node helper using z-ai-web-dev-sdk. Reads JSON from stdin, returns JSON classification on stdout. Includes a strict system prompt asking for valid JSON only.
  - magic_sdr/web_server.py — FastAPI app with REST endpoints (/api/tune, /api/modulation, /api/volume, /api/mute, /api/record, /api/bookmarks, /api/scan, /api/recordings, /api/ai/tag/{freq}, /api/bands) + 3 WebSockets (/ws/audio streams base64-encoded int16 PCM, /ws/spectrum streams JSON {center_hz, span_hz, bins}, /ws/events polls state). WebServer class wraps uvicorn.Server in a daemon thread.
  - web/templates/index.html + web/static/style.css + web/static/app.js — Remote web UI matching the desktop dark theme. Canvas-based waterfall (turbo colormap, click-to-tune), WebSocket audio decoded via Web Audio API, band tabs, scanner progress, recordings list with download links.
  - magic_sdr/main_window.py — MainWindow (QMainWindow) with FrequencyDial widget, modulation/gain/squelch/volume/mute controls, recording button, Gqrx connection button, web server toggle, waterfall + 4 tabs (Bookmarks / Auto-Discover / Recordings / Settings). Wires all Qt signals between components.
  - magic_sdr/main.py — Entry point with logging setup, dark Qt stylesheet, Ctrl-C handling, high-DPI attributes.
- Wrote setup.sh (installs system + Python + Node deps, udev rule for non-root dongle access), run.sh (launcher with Gqrx-running check), requirements.txt.
- Wrote README.md with full architecture diagram, feature table, supported bands, setup steps, Gqrx configuration, troubleshooting, project layout, and protocol reference.
- Wrote scripts/check_imports.py — verifies all 13 modules import cleanly. PASSED.
- Wrote scripts/functional_test.py — 8 functional tests covering band_presets lookups, Config load/save, GqrxClient against a mock TCP server (full F/f/M/m/l STRENGTH/AOS/LOS roundtrip), BookmarkManager CRUD + seeding (88 defaults), AudioReceiver UDP packet roundtrip, RecordingManager WAV+JSON metadata write, AITagger graceful-failure path, WaterfallWidget instantiation, MainWindow instantiation in offscreen Qt mode, WebServer factory.
- Fixed two bugs found by functional tests:
  1. Recording class had private `_frames_written` but was accessed as `frames_written` by RecordingManager and tests — added a @property.
  2. WaterfallWidget test used QCoreApplication instead of QApplication (QWidgets require QApplication) — fixed.

Stage Summary:
- 13 Python modules + 1 Node helper + 3 web files + 2 shell scripts + README + 2 test scripts all written and passing.
- All 8 functional tests pass: band lookup, config persistence, Gqrx TCP roundtrip via mock server, bookmark CRUD + 88 default presets seeded, audio UDP roundtrip, WAV+JSON recording write, AI tagger graceful failure, waterfall + main window + web server factory instantiation.
- Architecture: GqrxClient (TCP 7356) + AudioReceiver (UDP 7355) + SpectrumReceiver (UDP 7357) feed a PyQt5 desktop UI (FrequencyDial + WaterfallWidget + 4 tabs) and an embedded FastAPI web server (port 8000) that streams audio/spectrum/state over WebSockets to a browser-side canvas waterfall + Web Audio API player.
- 88 default bookmarks seeded (NOAA WX-1..7, marine Ch 1/2/3/4/5/6/8/9/10/11/16/17/18/20/22/24/26/28 + duplex freqs, ATC emergency/unicom/ground/approach/departure/clearance, ham calling + repeater outputs, WWV time stations 2.5/5/10/15/20 MHz, all 9 HF ham bands, CB Ch 19, international SW broadcasters).
- AI tagger uses local FFT feature extraction (numpy) + z-ai-web-dev-sdk LLM call to classify signals as music/talk/weather/aviation/ham/marine/noise/data/unknown.
- Deluxe scope delivered: all 6 magic features (auto-discover, waterfall, recordings, bookmarks, AI tags, remote access) + all 6 bands (FM broadcast, airband, NOAA, 2m ham, marine VHF, shortwave) + scheduled recordings + click-to-tune + dark theme + remote web UI.
- Project saved at /home/z/my-project/. To run on the user's actual Linux machine: ./setup.sh then ./run.sh.
