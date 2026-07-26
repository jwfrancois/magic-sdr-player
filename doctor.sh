#!/usr/bin/env bash
# Magic SDR Player — doctor
#
# Diagnoses and auto-fixes common installation problems.
# Run this whenever run.sh fails to start the app.

set -e
cd "$(dirname "$0")"
SCRIPT_DIR="$(pwd)"

echo "=== Magic SDR Player — doctor ==="
echo ""

# --- 1. System Python ---
echo "1. Checking system Python…"
if command -v python3 >/dev/null; then
    SYS_PY_VER=$(python3 --version 2>&1)
    echo "   ✓ python3 found: $SYS_PY_VER at $(command -v python3)"
else
    echo "   ✗ python3 not found"
    echo "     Fix: sudo apt-get install -y python3 python3-venv python3-pip"
    exit 1
fi

# --- 2. python3-venv module ---
echo ""
echo "2. Checking python3 venv module…"
if python3 -c "import venv" 2>/dev/null; then
    echo "   ✓ venv module available"
else
    echo "   ✗ venv module missing"
    echo "     Fix: sudo apt-get install -y python3-venv"
    read -p "   Install now? [y/N] " -n 1 -r
    echo
    [[ $REPLY =~ ^[Yy]$ ]] && sudo apt-get install -y python3-venv
fi

# --- 3. pip availability ---
echo ""
echo "3. Checking pip…"
if python3 -m pip --version >/dev/null 2>&1; then
    echo "   ✓ pip available in system python3"
elif [ -x "$SCRIPT_DIR/.venv/bin/python3" ] && "$SCRIPT_DIR/.venv/bin/python3" -m pip --version >/dev/null 2>&1; then
    echo "   ✓ pip available in .venv"
else
    echo "   ✗ pip not available"
    echo "     Fix: sudo apt-get install -y python3-pip"
    read -p "   Install now? [y/N] " -n 1 -r
    echo
    [[ $REPLY =~ ^[Yy]$ ]] && sudo apt-get install -y python3-pip
fi

# --- 4. Project venv ---
echo ""
echo "4. Checking project venv at $SCRIPT_DIR/.venv …"
if [ -x "$SCRIPT_DIR/.venv/bin/python3" ]; then
    VENV_VER=$("$SCRIPT_DIR/.venv/bin/python3" --version 2>&1)
    echo "   ✓ venv exists: $VENV_VER"
    PY="$SCRIPT_DIR/.venv/bin/python3"
else
    echo "   ✗ venv missing"
    echo "     Creating it now…"
    if python3 -m venv "$SCRIPT_DIR/.venv"; then
        echo "   ✓ venv created"
        PY="$SCRIPT_DIR/.venv/bin/python3"
        "$PY" -m pip install --upgrade pip
    else
        echo "   ✗ venv creation failed"
        echo "     Try: sudo apt-get install -y python3-venv"
        exit 1
    fi
fi

# --- 5. Critical Python packages ---
echo ""
echo "5. Checking Python packages in $PY …"
MISSING=()
for pkg in PyQt5 pyqtgraph sounddevice fastapi uvicorn jinja2 aiofiles numpy matplotlib; do
    if "$PY" -c "import $pkg" 2>/dev/null; then
        echo "   ✓ $pkg"
    else
        echo "   ✗ $pkg"
        MISSING+=($pkg)
    fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
    echo ""
    echo "   Installing missing packages: ${MISSING[*]}"
    "$PY" -m pip install -r requirements.txt
    # Re-check
    STILL_MISSING=()
    for pkg in "${MISSING[@]}"; do
        if "$PY" -c "import $pkg" 2>/dev/null; then
            echo "   ✓ $pkg (installed)"
        else
            echo "   ✗ $pkg (still missing)"
            STILL_MISSING+=($pkg)
        fi
    done
    if [ ${#STILL_MISSING[@]} -gt 0 ]; then
        echo ""
        echo "   ERROR: Could not install: ${STILL_MISSING[*]}"
        echo "   Try manually: $PY -m pip install ${STILL_MISSING[*]}"
        exit 1
    fi
fi

# --- 6. Bookmarks file ---
echo ""
echo "6. Checking bookmarks.json…"
if [ -f "$SCRIPT_DIR/bookmarks.json" ]; then
    echo "   ✓ bookmarks.json exists"
else
    echo "   ✗ bookmarks.json missing — creating with default seed"
    echo '{"seeded": true}' > "$SCRIPT_DIR/bookmarks.json"
fi

# --- 7. AI helper (optional) ---
echo ""
echo "7. Checking AI tagger (optional)…"
if [ -d "$SCRIPT_DIR/scripts/node_modules/z-ai-web-dev-sdk" ]; then
    echo "   ✓ z-ai-web-dev-sdk installed"
elif command -v npm >/dev/null; then
    echo "   ⚠ z-ai-web-dev-sdk not installed — installing now"
    cd "$SCRIPT_DIR/scripts"
    [ -f package.json ] || npm init -y >/dev/null
    npm install z-ai-web-dev-sdk
    cd "$SCRIPT_DIR"
else
    echo "   ⚠ npm not available — AI tagging will be disabled"
    echo "     Fix: sudo apt-get install -y nodejs npm"
fi

# --- 8. Gqrx (informational only) ---
echo ""
echo "8. Checking Gqrx (informational)…"
if command -v gqrx >/dev/null; then
    echo "   ✓ gqrx found at $(command -v gqrx)"
else
    echo "   ⚠ gqrx not installed"
    echo "     Fix: sudo apt-get install -y gqrx"
fi
if pgrep -x gqrx >/dev/null; then
    echo "   ✓ gqrx is currently running"
else
    echo "   ⚠ gqrx is not running — start it with 'gqrx &' before launching Magic SDR"
fi

# --- Save the verified Python path ---
echo "$PY" > "$SCRIPT_DIR/.python-used"

echo ""
echo "=== Doctor complete ==="
echo ""
echo "Verified Python: $PY"
echo ""
echo "You should now be able to run:"
echo "  ./run.sh"
echo ""
