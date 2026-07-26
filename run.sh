#!/usr/bin/env bash
# Magic SDR Player — launcher
#
# Starts the Magic SDR Player desktop app. The app connects to a running
# Gqrx instance and exposes a web UI on http://0.0.0.0:8000 for remote
# listening from a phone or laptop.

set -e
cd "$(dirname "$0")"

# Pick the right Python
if [ -x "/home/z/.venv/bin/python3" ]; then
    PY=/home/z/.venv/bin/python3
elif command -v python3 >/dev/null; then
    PY=python3
else
    echo "Python 3 not found. Install python3 and try again."
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

exec $PY -m magic_sdr.main "$@"
