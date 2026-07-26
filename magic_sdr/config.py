"""Runtime configuration loader/saver.

Persists user preferences (last frequency, gain, volume, recent bookmarks, etc.)
to /home/z/my-project/config.json so they survive app restarts.
"""

import json
import os
from dataclasses import dataclass, asdict, field
from typing import Optional

from . import CONFIG_FILE


@dataclass
class Config:
    # Gqrx connection
    gqrx_host: str = "127.0.0.1"
    gqrx_port: int = 7356
    audio_port: int = 7355
    spectrum_port: int = 7357

    # Web
    web_host: str = "0.0.0.0"
    web_port: int = 8000

    # SDR defaults
    last_frequency_hz: int = 96_900_000     # 96.9 MHz, a typical FM broadcast freq
    last_modulation: str = "WFM_ST"
    gain_db: float = 0.0                    # 0 = auto (AGC)

    # Audio
    volume: float = 0.8                     # 0.0 – 1.0
    audio_sample_rate: int = 48000
    audio_channels: int = 2                 # Gqrx streams stereo for WFM_ST

    # Scanner
    scan_threshold_db: float = -45.0        # signal level above which a frequency is considered "active"
    scan_dwell_s: float = 0.25              # time to settle on each frequency while scanning

    # Web access
    remote_access_enabled: bool = True      # launch the embedded web server
    allow_remote_connections: bool = True   # listen on 0.0.0.0 (false = 127.0.0.1 only)

    # AI tagging
    ai_tagging_enabled: bool = True

    # UI
    waterfall_speed: float = 1.0            # multiplier
    window_width: int = 1400
    window_height: int = 900

    @classmethod
    def load(cls) -> "Config":
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE) as f:
                    data = json.load(f)
                # Only keep keys we know about (forward-compat)
                known = set(cls.__dataclass_fields__.keys())
                clean = {k: v for k, v in data.items() if k in known}
                return cls(**clean)
            except Exception as e:
                print(f"[config] Failed to load {CONFIG_FILE}: {e}; using defaults")
        return cls()

    def save(self) -> None:
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(asdict(self), f, indent=2)
        except Exception as e:
            print(f"[config] Failed to save {CONFIG_FILE}: {e}")
