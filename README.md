# Magic SDR Player

A magical streaming player for your **RTL-SDR V3** dongle, built on top of **Gqrx**.

Magic SDR Player wraps Gqrx's remote-control protocol and adds a polished desktop UI + a remote web UI (for listening from your phone/laptop), an auto-discovery band scanner, a waterfall display, an AI signal tagger, and a recording scheduler.

```
                ┌─────────────────────────────┐
                │   Magic SDR Player (PyQt5)  │
                │  ┌───────────────────────┐  │
   RTL-SDR V3 ─►│  │  GqrxClient (TCP)     │  │
   (USB)        │  │  AudioReceiver (UDP)  │  │──► Speakers (sounddevice)
                │  │  SpectrumReceiver(UDP)│  │──► Waterfall (pyqtgraph)
                │  │  BandScanner          │  │──► Auto-discovered stations
                │  │  BookmarkManager      │  │──► bookmarks.json (88 presets)
                │  │  RecordingManager     │  │──► recordings/*.wav + .json
                │  │  AITagger (Node+GLM)  │  │──► signal_type / language / summary
                │  │  WebServer (FastAPI)  │  │──► http://0.0.0.0:8000
                │  └───────────────────────┘  │     └─ remote web UI (HTML/CSS/JS)
                └─────────────────────────────┘
```

## ✦ What it does

| Feature | Description |
|---|---|
| **Tune** | Click-to-tune on the waterfall, type a frequency, or use ±1k/±10k/±100k buttons. Modulation auto-selects based on band (WFM_ST for FM broadcast, AM for airband, NBFM for ham/marine, AM for shortwave). |
| **Waterfall** | Live spectrum + scrolling waterfall with the tuned-frequency marker. Turbo colormap, peak-hold on the spectrum line. |
| **Auto-discover** | One-click band scanner that sweeps each band, finds active frequencies above a configurable threshold, and labels them. Found stations are auto-added to your bookmarks. |
| **Bookmarks** | Curated library of 88 known channels seeded on first run (NOAA WX-1 through 7, all 7 marine channels, ATC frequencies, ham calling frequencies, WWV time stations, international SW broadcasters, etc.). Add your own from the UI or web. |
| **Recordings** | One-click WAV recording with companion JSON metadata (frequency, modulation, band, label, AI tag, peak/avg signal level, timestamps). Files organized by date. |
| **AI tags** | Each discovered station can be classified by GLM (via z-ai-web-dev-sdk) into one of `music / talk / weather / aviation / ham / marine / noise / data / unknown`, plus a language guess and a one-sentence summary. The classification uses audio features (spectral centroid, ZCR, RMS, dominant frequency, bandwidth) computed locally and sent to the LLM. |
| **Remote access** | Embedded FastAPI web server on port 8000. Open the URL on your phone or laptop on the same Wi-Fi to listen from anywhere in the house. The web UI streams audio via WebSocket and renders its own waterfall on a canvas. |

## ✦ Supported bands

| Band | Range | Modulation | Notable channels |
|---|---|---|---|
| FM Broadcast | 88–108 MHz | WFM_ST | Local stations (auto-discovered) |
| Aviation (Airband) | 118–137 MHz | AM | 121.5 (Guard), 122.75 (air-to-air), 122.8 (Unicom), common approach/departure freqs |
| NOAA Weather Radio | 162.400–162.550 MHz | WFM | All 7 NOAA WX channels |
| 2m Amateur (Ham) | 144–148 MHz | FM | 146.520 (national simplex), 145.800 (ISS voice), 144.100 (SSB/CW calling) |
| Marine VHF | 156–162 MHz | FM | Ch 16 distress, Ch 6/9/22, all international channels |
| Shortwave (HF) | 0.5–30 MHz | AM | All HF ham bands, WWV time stations (2.5/5/10/15/20 MHz), CB Ch 19, international SW broadcasters |

For the shortwave band, configure RTL-SDR V3's **Q-branch direct sampling** in Gqrx → Device settings (no upconverter needed).

## ✦ Requirements

- **Linux** (Ubuntu/Debian tested; Arch/Fedora should work)
- **Gqrx SDR** (already installed — `apt install gqrx` if missing)
- **RTL-SDR V3** dongle plugged into a USB port
- **Python 3.10+** with PyQt5, pyqtgraph, sounddevice, FastAPI, uvicorn, numpy, jinja2, aiofiles, matplotlib
- **Node.js 18+** with the `z-ai-web-dev-sdk` package (for AI tagging only — the app runs without it)
- **PortAudio** system library (for sounddevice)

## ✦ First-time setup

```bash
cd /home/z/my-project
./setup.sh
```

This will:
1. Install system packages (gqrx, portaudio19-dev, rtl-sdr, nodejs) if missing.
2. Install a udev rule so you can access the dongle as a non-root user.
3. Install Python packages from `requirements.txt`.
4. Install `z-ai-web-dev-sdk` for the AI tagger.
5. Seed `bookmarks.json` with the default station library.

## ✦ Configure Gqrx

Open Gqrx and configure:

1. **Device settings** (top-left gear icon):
   - Device: `RTL-SDR` → your dongle
   - Sample rate: 2.4 MS/s
   - For HF (shortwave): Direct sampling = **Q-branch**
   - **RF Gain: ~40 dB** (NOT 0 — 0 dB means the receiver is deaf)

2. **Remote control TCP** — `Tools → Remote control settings`:
   - ☑ **Enable remote control** — TCP port `7356`

3. **Audio UDP stream** — `Tools → Audio UDP` (separate menu, not a tab inside Remote control settings):
   - ☑ Enable — host `127.0.0.1`, port `7355`, 48 kHz, **stereo**, 16-bit signed PCM

4. Press the green ▶ **Play** button in Gqrx's main window to start the receiver.

> 💡 **Tip — don't want to hunt through menus?** Open Magic SDR's Settings tab and click
> `🔧 Setup Gqrx config`. This writes a known-good `~/.config/gqrx/default.conf` (with
> remote control + audio UDP enabled) and backs up your existing config first. Then just
> quit Gqrx and re-launch it — no menu hunting required.
>
> ⚠ **About spectrum UDP**: stock Gqrx has NO spectrum UDP stream option. Magic SDR
> computes a real-time FFT from the audio stream and draws the waterfall from that —
> you don't need a spectrum stream.

## ✦ Run

```bash
cd /home/z/my-project
./run.sh
```

Or directly:

```bash
python3 -m magic_sdr.main
```

The desktop window opens. Click **Connect** to attach to Gqrx. The **Remote Access** widget shows the web URL (default `http://0.0.0.0:8000`) — open it on your phone or laptop.

## ✦ Project layout

```
/home/z/my-project/
├── magic_sdr/                 # Python package
│   ├── __init__.py            # Network ports + paths
│   ├── config.py              # JSON-backed user config
│   ├── band_presets.py        # 6 bands + known-channel tables (88 entries)
│   ├── gqrx_client.py         # TCP client for Gqrx remote control (port 7356)
│   ├── audio_receiver.py      # UDP audio stream receiver (port 7355) + PyAudio/sounddevice player
│   ├── spectrum.py            # UDP spectrum receiver (port 7357) + pyqtgraph waterfall widget
│   ├── band_scanner.py        # Background band-sweeper that auto-discovers stations
│   ├── bookmark_manager.py    # JSON-backed station library (CRUD + search)
│   ├── recording_manager.py   # WAV + JSON metadata recording with scheduling
│   ├── ai_tagger.py           # Audio feature extraction + GLM-based signal classification
│   ├── web_server.py          # FastAPI app: REST + 3 WebSockets (audio, spectrum, events)
│   ├── main_window.py         # PyQt5 main window assembling everything
│   └── main.py                # Entry point + dark Qt stylesheet
├── web/
│   ├── templates/index.html   # Remote web UI
│   └── static/
│       ├── style.css          # Dark theme matching the desktop app
│       └── app.js             # WebSocket audio/spectrum/event handlers, click-to-tune
├── scripts/
│   ├── ai_helper.js           # Node helper invoking z-ai-web-dev-sdk for signal classification
│   ├── check_imports.py       # Sanity check that all modules import cleanly
│   └── functional_test.py     # 8 functional tests (band lookup, Gqrx mock, recording, etc.)
├── recordings/                # WAV + JSON files, organized by date
├── bookmarks.json             # Default = 88 known channels
├── config.json                # User config (frequency, gain, volume, etc.)
├── requirements.txt
├── setup.sh                   # First-time install
├── run.sh                     # Launcher
└── README.md
```

## ✦ Gqrx remote protocol commands used

Magic SDR Player talks to Gqrx over TCP port 7356 using the Gqrx remote control protocol (a Hamlib NET rigctl subset). Commands actually issued:

| Command | Effect |
|---|---|
| `F <freq_hz>` | Set receiver frequency |
| `f` | Get receiver frequency |
| `M <mod>` | Set modulation (`WFM_ST`, `AM`, `FM`, `USB`, `LSB`, `CWU`, `CWL`, …) |
| `m` | Get modulation |
| `L SQL <db>` | Set squelch level |
| `L RF <db>` | Set RF gain (0 = AGC) |
| `l STRENGTH` | Read signal level (smeter) in dB |
| `AOS` / `LOS` | Start / stop Gqrx's own recorder (not used — we record locally) |

The poller thread queries `f`, `m`, `l STRENGTH` every 500 ms and emits Qt signals to update the GUI.

## ✦ Troubleshooting

**"Cannot connect to Gqrx at 127.0.0.1:7356"**
→ Gqrx isn't running, or its remote control TCP isn't enabled on port 7356.
→ **Quickest fix:** open the Settings tab in Magic SDR → click `🔧 Setup Gqrx config`.
  This writes a known-good `~/.config/gqrx/default.conf` (backing up your existing one first),
  enabling both remote control (TCP 7356) and audio UDP (127.0.0.1:7355). Then quit & re-launch Gqrx.
→ **Manual fix:** in Gqrx, `Tools → Remote control settings → ☑ Enable remote control`.

**No audio in the desktop app**
→ Enable Gqrx's audio UDP stream (host 127.0.0.1, port 7355). This is in `Tools → Audio UDP`
  (a separate menu from Remote control settings). If your Gqrx version doesn't have this menu
  item, use the `🔧 Setup Gqrx config` button in Magic SDR's Settings tab to write the config
  file directly.
→ Make sure your speakers are connected and not muted.

**Waterfall is blank**
→ Press the green ▶ Play button in Gqrx's main window — until Gqrx is actively receiving,
  no audio gets streamed and `l STRENGTH` returns nothing.
→ Also enable Gqrx's audio UDP stream (see above) — Magic SDR draws the waterfall from a
  real-time FFT of the audio. Stock Gqrx has NO spectrum UDP stream; the app falls back to
  audio-FFT automatically when no UDP spectrum data arrives on port 7357.
→ Click `🔍 Inspect Gqrx config` in the Settings tab to verify the config file.

**No audio in the web UI**
→ Browsers require a user gesture before allowing audio. Click anywhere on the page once to enable playback.

**AI tags show "AI classifier unavailable"**
→ Run `cd scripts && npm install z-ai-web-dev-sdk`. AI tagging is optional — the rest of the app works without it.

**HF / shortwave frequencies are silent**
→ In Gqrx Device settings, set "Direct sampling" to "Q-branch" (RTL-SDR V3 feature). Without this, the dongle cannot receive below ~24 MHz.

## ✦ License & credits

Magic SDR Player is open-source, MIT-licensed. Built on top of:
- [Gqrx SDR](https://gqrx.dk/) — the SDR engine
- [rtl-sdr](https://osmocom.org/projects/rtl-sdr) — the dongle driver
- [PyQt5](https://www.riverbankcomputing.com/software/pyqt/) + [pyqtgraph](https://pyqtgraph.org/) — UI + waterfall
- [FastAPI](https://fastapi.tiangolo.com/) + [uvicorn](https://www.uvicorn.org/) — remote web server
- [z-ai-web-dev-sdk](https://www.npmjs.com/package/z-ai-web-dev-sdk) — AI signal classification
