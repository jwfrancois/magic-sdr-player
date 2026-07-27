"""Magic SDR Player — a magical streaming player for RTL-SDR V3 built on top of Gqrx."""

__version__ = "1.0.0"
__app_name__ = "Magic SDR Player"
__author__ = "Magic SDR"

import os

# Default network ports
# GQRX_REMOTE_PORT: TCP remote control — set in Gqrx: Tools → Remote control settings
# GQRX_AUDIO_PORT:  UDP audio stream — set in Gqrx: Tools → Audio UDP (separate menu!)
# GQRX_SPECTRUM_PORT: UDP spectrum stream — NOT supported by stock Gqrx. We keep
#                     the receiver for compatibility with patched forks, but the
#                     app falls back to audio-FFT waterfall when no data arrives
#                     on this port (which is the normal case).
GQRX_REMOTE_HOST = "127.0.0.1"
GQRX_REMOTE_PORT = 7356       # Gqrx TCP remote control
GQRX_AUDIO_PORT = 7355        # Gqrx UDP audio stream (Tools → Audio UDP)
GQRX_SPECTRUM_PORT = 7357     # Gqrx UDP spectrum stream (rare; not in stock Gqrx)

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
