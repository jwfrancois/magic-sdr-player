#!/usr/bin/env bash
# Magic SDR Player — launcher
#
# Starts the Magic SDR Player desktop app. The app connects to a running
# Gqrx instance and exposes a web UI on http://0.0.0.0:8000 for remote
# listening from a phone or laptop.

set -e
cd "$(dirname "$0")"

# Pick the Python interpreter, in order of preference:
#   1. $PYTHON if explicitly set
#   2. The project's .venv created by setup.sh
#   3. An active virtualenv ($VIRTUAL_ENV)
#   4. The path recorded in .python-used (written by setup.sh)
#   5. System python3 as last resort
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -n "$PYTHON" ] && [ -x "$(command -v "$PYTHON")" ]; then
    PY="$PYTHON"
elif [ -x "$SCRIPT_DIR/.venv/bin/python3" ]; then
    PY="$SCRIPT_DIR/.venv/bin/python3"
elif [ -n "$VIRTUAL_ENV" ] && [ -x "$VIRTUAL_ENV/bin/python3" ]; then
    PY="$VIRTUAL_ENV/bin/python3"
elif [ -f "$SCRIPT_DIR/.python-used" ] && [ -x "$(cat "$SCRIPT_DIR/.python-used")" ]; then
    PY="$(cat "$SCRIPT_DIR/.python-used")"
elif command -v python3 >/dev/null; then
    PY="python3"
else
    echo "Python 3 not found. Run ./setup.sh first, or install python3."
    exit 1
fi

echo "Using Python: $PY"

# --- Preflight: verify critical imports ---
MISSING=""
for pkg in PyQt5 pyqtgraph sounddevice fastapi uvicorn numpy; do
    if ! "$PY" -c "import $pkg" 2>/dev/null; then
        MISSING="$MISSING $pkg"
    fi
done

if [ -n "$MISSING" ]; then
    echo ""
    echo "❌ Missing Python packages:$MISSING"
    echo ""
    echo "This usually means setup.sh didn't complete successfully."
    echo "To fix, run one of the following:"
    echo ""
    echo "  Option A — Re-run setup (recommended):"
    echo "    cd \"$SCRIPT_DIR\""
    echo "    ./setup.sh"
    echo ""
    echo "  Option B — Install into the existing venv manually:"
    echo "    cd \"$SCRIPT_DIR\""
    echo "    .venv/bin/python3 -m pip install -r requirements.txt"
    echo ""
    echo "  Option C — If .venv doesn't exist, create it from scratch:"
    echo "    cd \"$SCRIPT_DIR\""
    echo "    sudo apt-get install -y python3-venv python3-pip   # Debian/Ubuntu only"
    echo "    python3 -m venv .venv"
    echo "    .venv/bin/python3 -m pip install -r requirements.txt"
    echo ""
    echo "  Python being used: $PY"
    exit 1
fi

# Quick health-check: is Gqrx running?
if ! pgrep -x gqrx >/dev/null; then
    echo ""
    echo "⚠ Gqrx is not running. Start it first with 'gqrx' and configure:"
    echo "   Tools → Remote control settings:"
    echo "     ☑ Enable remote control (TCP port 7356)"
    echo "     ☑ Enable audio UDP stream (port 7355, 48 kHz stereo S16LE)"
    echo "     ☑ Enable spectrum UDP stream (port 7357)"
    echo "   Also pick your RTL-SDR V3 in Device settings (Direct sampling = Q-branch for HF)."
    echo ""
    read -p "Continue anyway? [y/N] " -n 1 -r
    echo
    [[ $REPLY =~ ^[Yy]$ ]] || exit 0
fi

exec "$PY" -m magic_sdr.main "$@"
