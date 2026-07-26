# Magic SDR Player — Quick Start

A desktop app that turns your **RTL-SDR V3 + Gqrx** into a magical streaming
radio with auto-discovery, waterfall, recordings, AI tagging, and a remote web
UI you can listen to from your phone.

---

## 1. Requirements

- Linux (Ubuntu/Debian, Fedora, or Arch)
- RTL-SDR V3 dongle plugged into a USB port
- Gqrx SDR (installed separately)
- Python 3.10+
- Node.js 18+ (optional — only for AI signal tagging)

---

## 2. Unzip

```bash
unzip magic_sdr_player.zip
cd magic_sdr_player
```

The folder can live anywhere — `~/magic_sdr_player`, `~/Desktop/magic_sdr_player`,
`/opt/magic_sdr_player`, whatever you like. All paths inside the project are
relative, so it's fully portable.

---

## 3. Run setup (installs dependencies)

```bash
./setup.sh
```

This will:
1. Install system packages via apt/dnf/pacman if missing: `gqrx`, `portaudio19-dev`, `rtl-sdr`, `nodejs`
2. Install a udev rule so you can access the dongle as a non-root user (will prompt for sudo password)
3. Install Python packages from `requirements.txt` (PyQt5, pyqtgraph, sounddevice, FastAPI, etc.)
4. Install the `z-ai-web-dev-sdk` Node package for AI tagging
5. Seed `bookmarks.json` with 88 default known channels

If you'd rather use a Python virtualenv, activate it before running setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
./setup.sh
```

The setup script will detect the active virtualenv and install into it.

---

## 4. Plug in the dongle & launch Gqrx

```bash
gqrx &
```

In Gqrx, configure:

**Device settings** (gear icon, top-left):
- Device: `RTL-SDR` → your dongle
- Sample rate: `2.4 MS/s`
- For HF/shortwave: Direct sampling = `Q-branch`

**Tools → Remote control settings**:
- ☑ Enable remote control — TCP port `7356`
- ☑ Enable audio UDP stream — host `127.0.0.1`, port `7355`, 48 kHz, stereo, 16-bit PCM
- ☑ Enable spectrum UDP stream — host `127.0.0.1`, port `7357`

Click the green ▶ in Gqrx to start receiving.

---

## 5. Launch Magic SDR Player

```bash
./run.sh
```

The desktop window opens. Click **Connect** to attach to Gqrx. You should see:
- Frequency display (defaults to 96.9 MHz)
- Live waterfall with click-to-tune
- Bookmark list (88 known channels pre-loaded)
- A **Remote Access** widget showing the URL `http://0.0.0.0:8000`

---

## 6. Listen from your phone

Find your computer's IP on the local network:

```bash
hostname -I    # e.g. 192.168.1.42
```

Open `http://192.168.1.42:8000` on your phone's browser (must be on the same Wi-Fi). Click anywhere on the page once to enable audio playback. You now have live SDR audio on your phone, with its own waterfall and tuner controls.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Cannot connect to Gqrx at 127.0.0.1:7356` | Gqrx isn't running, or remote control isn't enabled. Recheck step 4. |
| No audio in desktop app | Audio UDP stream not enabled in Gqrx. Port 7355, 48 kHz, stereo, 16-bit PCM. |
| Waterfall is blank | Spectrum UDP stream not enabled. Port 7357. Older Gqrx versions may lack this option. |
| No audio in browser | Browsers need a user gesture. Click anywhere on the page once. |
| `Permission denied: /dev/bus/usb/...` | udev rule didn't apply. Unplug & replug the dongle, or reboot. |
| AI tags say "unavailable" | Run `cd scripts && npm install z-ai-web-dev-sdk` manually. |
| HF/shortwave is silent | In Gqrx Device settings, set Direct sampling = Q-branch (RTL-SDR V3 only). |

---

## What you get

- **6 bands**: FM broadcast, Airband, NOAA Weather, 2m Ham, Marine VHF, Shortwave
- **88 pre-loaded bookmarks**: NOAA WX-1..7, all marine channels, ATC freqs, ham calling freqs, WWV time stations, international SW broadcasters
- **Auto-discovery scanner**: sweeps each band, finds active frequencies, labels them, auto-bookmarks new finds
- **Live waterfall**: spectrum + scrolling waterfall with click-to-tune
- **WAV recording**: one-click recording with companion JSON metadata (freq, mod, level, timestamps)
- **AI signal tagger**: classifies signals as music/talk/weather/aviation/ham/marine/noise/data/unknown + language + one-sentence summary
- **Remote web UI**: full tuner + waterfall + bookmarks + scanner accessible from any browser on your network

See `README.md` for the full architecture diagram and protocol reference.
