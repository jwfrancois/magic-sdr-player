"""Magic SDR Player — a magical streaming player for RTL-SDR V3 built on top of Gqrx."""

__version__ = "1.0.0"
__app_name__ = "Magic SDR Player"
__author__ = "Magic SDR"

import os

# Default network ports (must match Gqrx → Tools → Remote control settings)
GQRX_REMOTE_HOST = "127.0.0.1"
GQRX_REMOTE_PORT = 7356       # Gqrx TCP remote control
GQRX_AUDIO_PORT = 7355        # Gqrx UDP audio stream (set in Gqrx → Audio UDP)
GQRX_SPECTRUM_PORT = 7357     # Gqrx UDP spectrum stream (if enabled)

# Web UI
WEB_HOST = "0.0.0.0"
WEB_PORT = 8000

# Filesystem — resolved relative to the project root, NOT hardcoded.
# This makes the project fully portable: copy the folder anywhere and run.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = _PROJECT_ROOT
RECORDINGS_DIR = os.path.join(APP_DIR, "recordings")
BOOKMARKS_FILE = os.path.join(APP_DIR, "bookmarks.json")
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
