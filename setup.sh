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
# On Debian/Ubuntu we need python3-venv to create virtualenvs (PEP 668)
if command -v apt-get >/dev/null && ! python3 -c "import venv" 2>/dev/null; then
    NEED_SYS+=(python3-venv)
fi
# And python3-pip is required for the venv to have pip bootstrapped
if command -v apt-get >/dev/null && ! dpkg -s python3-pip >/dev/null 2>&1; then
    NEED_SYS+=(python3-pip)
fi

if [ ${#NEED_SYS[@]} -gt 0 ]; then
    echo ""
    echo "Installing system packages: ${NEED_SYS[*]}"
    if command -v apt-get >/dev/null; then
        sudo apt-get update
        sudo apt-get install -y "${NEED_SYS[@]}"
    elif command -v dnf >/dev/null; then
        sudo dnf install -y "${NEED_SYS[@]/python3-venv/python3-devel}"
    elif command -v pacman >/dev/null; then
        sudo pacman -S --noconfirm "${NEED_SYS[@]/portaudio19-dev/portaudio}" "${NEED_SYS[@]/python3-venv/python-virtualenv}"
    else
        echo "Could not detect package manager. Please install: ${NEED_SYS[*]}"
        exit 1
    fi
fi

# --- Pick the Python interpreter ---
# Honor $PYTHON if set; otherwise default to system python3.
PY="${PYTHON:-python3}"
if [ ! -x "$(command -v "$PY")" ]; then
    echo "Python 3 not found. Install python3 and try again."
    exit 1
fi

# --- Create (or reuse) a project-local virtualenv ---
# This sidesteps PEP 668 (Debian/Ubuntu's externally-managed-environment
# protection) so we don't need --break-system-packages.
VENV_DIR="$(pwd)/.venv"
if [ ! -x "$VENV_DIR/bin/python3" ]; then
    echo ""
    echo "Creating virtualenv at $VENV_DIR …"
    if ! "$PY" -m venv "$VENV_DIR" 2>&1; then
        echo ""
        echo "⚠ venv creation failed. Falling back to system Python with --break-system-packages."
        echo "  (This is safe on a personal machine but may conflict with apt-managed packages.)"
        USE_SYSTEM_PY=1
    else
        USE_SYSTEM_PY=0
    fi
else
    echo ""
    echo "Reusing existing virtualenv at $VENV_DIR"
    USE_SYSTEM_PY=0
fi

if [ "$USE_SYSTEM_PY" = "0" ]; then
    PY="$VENV_DIR/bin/python3"
fi

echo ""
echo "Using Python: $($PY --version 2>&1) at $(command -v "$PY")"
echo "Installing Python packages…"

# Upgrade pip first
$PY -m pip install --upgrade pip

# Install requirements — inside venv no flags needed; outside, use --break-system-packages
if [ "$USE_SYSTEM_PY" = "1" ]; then
    $PY -m pip install --break-system-packages -r requirements.txt
else
    $PY -m pip install -r requirements.txt
fi

# --- Verify all critical imports actually work ---
echo ""
echo "Verifying Python imports…"
MISSING=()
for pkg in PyQt5 pyqtgraph sounddevice fastapi uvicorn jinja2 aiofiles numpy matplotlib; do
    if $PY -c "import $pkg" 2>/dev/null; then
        echo "  OK  $pkg"
    else
        echo "  MISSING  $pkg"
        MISSING+=($pkg)
    fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
    echo ""
    echo "⚠ The following packages failed to import: ${MISSING[*]}"
    echo "  Attempting to install them explicitly…"
    $PY -m pip install ${USE_SYSTEM_PY:+--break-system-packages} "${MISSING[@]}"
    # Re-verify
    STILL_MISSING=()
    for pkg in "${MISSING[@]}"; do
        if $PY -c "import $pkg" 2>/dev/null; then
            echo "  OK  $pkg (after retry)"
        else
            echo "  STILL MISSING  $pkg"
            STILL_MISSING+=($pkg)
        fi
    done
    if [ ${#STILL_MISSING[@]} -gt 0 ]; then
        echo ""
        echo "ERROR: Could not install: ${STILL_MISSING[*]}"
        echo "Please install them manually with:"
        echo "  $PY -m pip install ${STILL_MISSING[*]}"
        exit 1
    fi
fi

# --- Node packages for AI helper ---
if command -v npm >/dev/null; then
    echo ""
    echo "Installing Node packages for AI tagging…"
    cd scripts
    if [ ! -d node_modules ] || [ ! -f node_modules/z-ai-web-dev-sdk/package.json ]; then
        npm init -y >/dev/null
        npm install z-ai-web-dev-sdk
    fi
    cd ..
else
    echo ""
    echo "⚠ npm not found — skipping AI tagger setup. The app will still run;"
    echo "  AI tagging will return 'unknown' for every signal."
    echo "  To enable: install Node.js + run 'cd scripts && npm install z-ai-web-dev-sdk'"
fi

# --- Bookmarks default ---
if [ ! -f bookmarks.json ]; then
    echo '{"seeded": true}' > bookmarks.json
fi

# --- Make run.sh's job easier: write a .python-version-style hint ---
cat > .python-used <<EOF
$PY
EOF

echo ""
echo "=== Setup complete ==="
echo ""
echo "Python interpreter used:  $PY"
echo "Virtualenv location:      $VENV_DIR"
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
