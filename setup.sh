#!/usr/bin/env bash
# Magic SDR Player — setup script for Linux
#
# Installs Python deps + system deps for Gqrx (if missing).
# Run this once before the first launch.

set -e

cd "$(dirname "$0")"

echo "=== Magic SDR Player setup ==="

# --- System packages ---
NEED_SYS=()
if ! command -v gqrx >/dev/null; then NEED_SYS+=(gqrx); fi
if ! ldconfig -p 2>/dev/null | grep -q libportaudio; then NEED_SYS+=(portaudio19-dev); fi
if ! command -v rtl_sdr >/dev/null; then NEED_SYS+=(rtl-sdr); fi
if ! command -v node >/dev/null; then NEED_SYS+=(nodejs); fi

if [ ${#NEED_SYS[@]} -gt 0 ]; then
    echo ""
    echo "Installing system packages: ${NEED_SYS[*]}"
    if command -v apt-get >/dev/null; then
        sudo apt-get update
        sudo apt-get install -y "${NEED_SYS[@]}"
    elif command -v dnf >/dev/null; then
        sudo dnf install -y "${NEED_SYS[@]}"
    elif command -v pacman >/dev/null; then
        # Arch: gqrx, portaudio, rtl-sdr, nodejs
        sudo pacman -S --noconfirm "${NEED_SYS[@]/portaudio19-dev/portaudio}"
    else
        echo "Could not detect package manager. Please install: ${NEED_SYS[*]}"
        exit 1
    fi
fi

# --- udev rules for RTL-SDR V3 (so non-root can access the dongle) ---
UDEV_RULE="/etc/udev/rules.d/rtl-sdr.rules"
if [ ! -f "$UDEV_RULE" ]; then
    echo ""
    echo "Installing udev rule for RTL-SDR V3 (allows non-root access)…"
    echo 'SUBSYSTEMS=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", GROUP="plugdev", MODE="0666"' | sudo tee "$UDEV_RULE" >/dev/null
    sudo udevadm control --reload-rules || true
    sudo udevadm trigger || true
    echo "Done. Unplug and replug the RTL-SDR V3 to apply."
fi

# --- Python packages ---
PY=python3
if [ -x "/home/z/.venv/bin/python3" ]; then PY=/home/z/.venv/bin/python3; fi
echo ""
echo "Installing Python packages via $PY…"
$PY -m pip install --upgrade pip
$PY -m pip install -r requirements.txt

# --- Node packages for AI helper ---
echo ""
echo "Installing Node packages for AI tagging…"
cd scripts
if [ ! -d node_modules ]; then
    npm init -y >/dev/null
    npm install z-ai-web-dev-sdk
fi
cd ..

# --- Bookmarks default ---
if [ ! -f bookmarks.json ]; then
    echo '{"seeded": true}' > bookmarks.json
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Plug in your RTL-SDR V3."
echo "  2. Launch Gqrx:"
echo "       gqrx &"
echo "  3. In Gqrx, configure (Tools → Remote control settings):"
echo "       ☑ Enable remote control            (port 7356)"
echo "       ☑ Enable audio UDP stream          (port 7355, 48 kHz, stereo, S16LE)"
echo "       ☑ Enable spectrum UDP stream       (port 7357)"
echo "  4. Start Magic SDR Player:"
echo "       ./run.sh"
echo ""
echo "Then open http://localhost:8000 on your phone or laptop to listen remotely."
