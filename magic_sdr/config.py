"""Runtime configuration loader/saver.

Persists user preferences (last frequency, gain, volume, recent bookmarks, etc.)
to /home/z/my-project/config.json so they survive app restarts.
"""

import json
import os
from dataclasses import dataclass, asdict, field
from typing import Optional, List

from . import CONFIG_FILE


@dataclass
class MemoryPresetData:
    """One memory preset slot, persisted across restarts."""
    freq_hz: int = 0
    modulation: str = ""
    label: str = ""
    stored_at: float = 0.0


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
    # Gqrx's `l STRENGTH` returns dBFS (decibels relative to full scale),
    # not dBm. Typical values:
    #   -30 to -50 dBFS: very strong local FM broadcast
    #   -50 to -65 dBFS: normal FM station, ATC, NOAA
    #   -65 to -80 dBFS: weak but audible stations
    #   -80 to -120 dBFS: noise floor
    # So -80 dB is a good "is there a signal here?" cutoff.
    scan_threshold_db: float = -80.0
    scan_dwell_s: float = 0.5              # time to settle on each frequency while scanning

    # Web access
    remote_access_enabled: bool = True      # launch the embedded web server
    allow_remote_connections: bool = True   # listen on 0.0.0.0 (false = 127.0.0.1 only)

    # AI tagging
    ai_tagging_enabled: bool = True

    # UI
    waterfall_speed: float = 1.0            # multiplier
    window_width: int = 1400
    window_height: int = 900

    # ---- New magical features ----
    # EQ preset state — name of currently-selected preset, or "Custom" if user
    # has manually adjusted sliders since loading a preset.
    eq_preset_name: str = "Flat"
    # EQ gains (dB) per band — 10 values. Persisted so the EQ state survives
    # app restarts.
    eq_gains: List[float] = field(default_factory=lambda: [0.0] * 10)
    eq_enabled: bool = True

    # Audio visualizer mode — "Oscilloscope", "Spectrum Bars", "Circular", "Liquid Light"
    visualizer_mode: str = "Oscilloscope"

    # Memory presets — list of MemoryPresetData dicts (12 slots).
    # We store as list-of-dicts for JSON compatibility.
    memory_presets: List[dict] = field(default_factory=lambda: [None] * 12)

    # CW decoder state
    cw_decoder_enabled: bool = True

    # DX cluster state
    dx_cluster_enabled: bool = True

    # Night vision mode (red theme for dark adaptation)
    night_vision: bool = False

    # Time-travel buffer state — was the user in replay mode when they closed?
    time_travel_live: bool = True

    # Observer latitude for aurora forecast (Baltimore, MD by default)
    observer_latitude: float = 50.0  # magnetic latitude (~geographic 39° + offset)

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
