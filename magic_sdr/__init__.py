"""Magic SDR Player — a magical streaming player for RTL-SDR V3 built on top of Gqrx."""

__version__ = "1.0.0"
__app_name__ = "Magic SDR Player"
__author__ = "Magic SDR"

# Default network ports (must match Gqrx → Tools → Remote control settings)
GQRX_REMOTE_HOST = "127.0.0.1"
GQRX_REMOTE_PORT = 7356       # Gqrx TCP remote control
GQRX_AUDIO_PORT = 7355        # Gqrx UDP audio stream (set in Gqrx → Audio UDP)
GQRX_SPECTRUM_PORT = 7357     # Gqrx UDP spectrum stream (if enabled)

# Web UI
WEB_HOST = "0.0.0.0"
WEB_PORT = 8000

# Filesystem
APP_DIR = "/home/z/my-project"
RECORDINGS_DIR = f"{APP_DIR}/recordings"
BOOKMARKS_FILE = f"{APP_DIR}/bookmarks.json"
CONFIG_FILE = f"{APP_DIR}/config.json"
