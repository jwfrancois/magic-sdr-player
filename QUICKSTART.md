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
1. Install system packages via apt/dnf/pacman if missing: `gqrx`, `portaudio19-dev`, `rtl-sdr`, `nodejs`, `python3-venv`, `python3-pip`
2. Install a udev rule so you can access the dongle as a non-root user (will prompt for sudo password)
3. **Create a project-local virtualenv at `.venv/`** — this sidesteps Debian/Ubuntu's PEP 668 "externally-managed-environment" protection so `pip install` just works without `--break-system-packages`
4. Install Python packages from `requirements.txt` into the venv (PyQt5, pyqtgraph, sounddevice, FastAPI, etc.)
5. Install the `z-ai-web-dev-sdk` Node package for AI tagging
6. Seed `bookmarks.json` with 88 default known channels

The script writes the chosen Python path to `.python-used` so `run.sh` can find it automatically next time.

**If you prefer to use your own virtualenv** (e.g. conda, pyenv, or a manually created venv elsewhere), activate it first:

```bash
source /path/to/your/venv/bin/activate
./setup.sh
```

The setup script will detect the active virtualenv via `$VIRTUAL_ENV` and install into it instead of creating a new one.

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
- **RF Gain: set to ~40 dB** (NOT 0 — gain 0 means the receiver is deaf)

**Tools → Remote control settings** (this dialog has THREE separate sections, all required):
- ☑ Enable remote control — TCP port `7356`
- ☑ Enable audio UDP stream — host `127.0.0.1`, port `7355`, 48 kHz, stereo, 16-bit PCM
- ☑ Enable spectrum UDP stream — host `127.0.0.1`, port `7357`

> ⚠ **Critical**: All three streams must be enabled. The TCP control connection
> (port 7356) is what Magic SDR uses to *command* Gqrx (set frequency, set
> modulation, read signal level). The UDP audio stream (port 7355) is what
> Magic SDR uses to *hear* what Gqrx receives. The UDP spectrum stream
> (port 7357) is what Magic SDR uses to *draw the waterfall*. If you only
> enable TCP, you'll see "Connected" but the waterfall will be black and the
> scanner will find 0 stations.

Click the green ▶ in Gqrx to start receiving. You should see Gqrx's own
waterfall come alive with signals — if Gqrx's own waterfall is black, Magic
SDR's will be too.

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

If the waterfall is black or a scan finds 0 stations, click the **🩺 Diagnose**
button next to Connect — it will tell you exactly which of the three streams
isn't working and what to fix.

---

## 6. Listen from your phone

Find your computer's IP on the local network:

```bash
hostname -I    # e.g. 192.168.1.42
```

Open `http://192.168.1.42:8000` on your phone's browser (must be on the same Wi-Fi). Click anywhere on the page once to enable audio playback. You now have live SDR audio on your phone, with its own waterfall and tuner controls.

---

## Troubleshooting

### "0 stations found" / waterfall is black

This is the #1 issue. It means Magic SDR can command Gqrx (TCP control works)
but is not receiving any UDP audio/spectrum data. Click **🩺 Diagnose** for an
exact diagnosis. Common causes, in order of frequency:

1. **Audio UDP stream not enabled in Gqrx** → Tools → Remote control settings →
   Audio UDP stream → host `127.0.0.1`, port `7355`, click Start.
2. **Spectrum UDP stream not enabled in Gqrx** → same dialog → Spectrum UDP
   stream → host `127.0.0.1`, port `7357`, click Start.
3. **Gqrx receiver is paused** → press the green ▶ button in Gqrx's main
   window so it actually starts receiving.
4. **RF Gain is 0** → in Gqrx Device settings, set RF Gain to ~40 dB.
   (Magic SDR tries to set this automatically on connect, but if you reset
   it to 0 manually the receiver becomes deaf.)
5. **Antenna not connected** → RTL-SDR V3 needs an antenna plugged into the
   SMA connector to receive anything.
6. **Tuned to a dead frequency** → 108.0 MHz is the top edge of the FM band
   and often has nothing. Try 96.9 MHz, 98.5 MHz, or 101.1 MHz.

### Other symptoms

| Symptom | Fix |
|---|---|
| `Cannot connect to Gqrx at 127.0.0.1:7356` | Gqrx isn't running, or remote control isn't enabled. Recheck step 4. |
| No audio in desktop app | Audio UDP stream not enabled in Gqrx. Port 7355, 48 kHz, stereo, 16-bit PCM. |
| Waterfall is blank | Spectrum UDP stream not enabled. Port 7357. Click 🩺 Diagnose to verify. |
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
