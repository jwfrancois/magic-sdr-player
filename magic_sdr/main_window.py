"""Main window — assembles the GUI from all components.

Layout:
  ┌──────────────────────────────────────────────────────────────┐
  │ Top bar:    connection status · frequency · modulation · gain │
  ├─────────────────────┬────────────────────────────────────────┤
  │  Tuner (left col):  │  Waterfall (right col):                 │
  │   - Frequency dial  │   - Spectrum + waterfall                │
  │   - Modulation box  │                                         │
  │   - Volume / mute   │                                         │
  │   - Signal level    │                                         │
  │   - Record button   │                                         │
  ├─────────────────────┴────────────────────────────────────────┤
  │  Tabs: Bookmarks | Scanner | Recordings | Settings            │
  └──────────────────────────────────────────────────────────────┘

The window owns all top-level objects (GqrxClient, AudioReceiver, etc.) and
wires their signals together. It runs the embedded web server in a thread so
the GUI stays responsive.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import numpy as np
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QMetaObject, Q_ARG, pyqtSlot
from PyQt5.QtGui import QFont, QIcon, QColor, QPalette, QKeySequence
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QTabWidget,
    QLabel, QPushButton, QComboBox, QSlider, QLineEdit, QSpinBox, QDoubleSpinBox,
    QListWidget, QListWidgetItem, QProgressBar, QStatusBar, QMessageBox,
    QGroupBox, QFormLayout, QCheckBox, QFileDialog, QSplitter, QFrame,
    QApplication, QStyle, QAction, QShortcut
)

from .gqrx_client import GqrxClient, MODULATIONS
from .audio_receiver import AudioReceiver, AudioPlayer
from .spectrum import SpectrumReceiver, AudioSpectrumSource, WaterfallWidget
from .band_scanner import BandScanner, DiscoveredStation
from .bookmark_manager import BookmarkManager, Bookmark
from .recording_manager import RecordingManager
from .ai_tagger import AITagger
from .web_server import WebServer
from .band_presets import BANDS, BANDS_BY_NAME, band_for_frequency, lookup_known, guess_modulation
from .config import Config
# New feature modules (round 1 — from previous session)
from .clock import ClockWidget
from .tuning_knob import TuningKnob
from .s_meter import SMeterWidget
from .equalizer import Equalizer, EQ_BANDS_HZ
from .solar import SolarFetcher, SolarConditions
from .band_conditions import estimate_band_conditions, rating_to_stars, band_color
from .rds import RDSDecoder, RDSInfo, HD_RADIO_INFO_TEXT
# Magical new features (round 2 — this session)
from .eq_presets import EQ_PRESETS, get_preset_names, get_preset_gains, find_closest_preset
from .audio_visualizer import AudioVisualizer, ALL_MODES as VISUALIZER_MODES
from .memory_presets import MemoryPresetBar, MemoryPreset
from .time_travel import TimeTravelBuffer, TimeTravelWidget
from .cw_decoder import CWDecoder
from .dx_cluster import DXClusterClient, DXSpot
from .aurora import forecast_aurora, storm_class_for_kp
from .auto_surf import AutoSurfer, SurfStop

log = logging.getLogger(__name__)


class FrequencyDial(QWidget):
    """A digital frequency display + tuning buttons."""
    tune_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.freq_hz = 96_900_000
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Big frequency display
        self.display = QLabel("96.900000 MHz")
        f = QFont("JetBrains Mono", 32)
        f.setWeight(QFont.Light)
        self.display.setFont(f)
        self.display.setStyleSheet("color: #5cd9ff; padding: 8px;")
        self.display.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.display)

        # Tuning buttons: -100k -10k -1k | entry | +1k +10k +100k
        row = QHBoxLayout()
        for step, label in [(-100_000, "−100k"), (-10_000, "−10k"), (-1_000, "−1k")]:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, s=step: self.tune_requested.emit(self.freq_hz + s))
            row.addWidget(btn)
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("MHz")
        self.entry.setFixedWidth(120)
        self.entry.returnPressed.connect(self._on_entry)
        row.addWidget(self.entry)
        for step, label in [(1_000, "+1k"), (10_000, "+10k"), (100_000, "+100k")]:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, s=step: self.tune_requested.emit(self.freq_hz + s))
            row.addWidget(btn)
        layout.addLayout(row)

    def set_frequency(self, freq_hz: int) -> None:
        self.freq_hz = int(freq_hz)
        self.display.setText(f"{self.freq_hz / 1e6:.6f} MHz")

    def _on_entry(self) -> None:
        try:
            mhz = float(self.entry.text())
            self.tune_requested.emit(int(mhz * 1e6))
        except ValueError:
            pass


class MainWindow(QMainWindow):
    """Top-level window that wires all components together."""

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.setWindowTitle("Magic SDR Player — RTL-SDR V3 + Gqrx")
        self.resize(config.window_width, config.window_height)
        # Prevent the window from shrinking so much that the bottom controls
        # (memory presets, visualizer, EQ) get clipped.
        self.setMinimumSize(900, 640)

        # ----------------------------- core components -----------------------------
        self.gqrx = GqrxClient(host=config.gqrx_host, port=config.gqrx_port)
        self.audio_receiver = AudioReceiver(
            port=config.audio_port,
            sample_rate=config.audio_sample_rate,
            channels=config.audio_channels,
        )
        self.audio_player = AudioPlayer(
            sample_rate=config.audio_sample_rate,
            channels=config.audio_channels,
        )
        self.audio_player.set_volume(config.volume)
        self.spectrum_receiver = SpectrumReceiver(port=config.spectrum_port)
        # Audio-FFT spectrum source — the DEFAULT when Gqrx isn't providing
        # UDP spectrum data (which is the normal case for stock Gqrx, since
        # Gqrx doesn't have a UDP spectrum stream feature).
        self.audio_spectrum = AudioSpectrumSource()
        self.bookmarks = BookmarkManager()
        self.recordings = RecordingManager()
        self.scanner = BandScanner(self.gqrx)
        self.scanner.threshold_db = config.scan_threshold_db
        self.scanner.dwell_s = config.scan_dwell_s
        self.ai_tagger = AITagger()
        self.ai_tagger.enabled = config.ai_tagging_enabled
        self.scanner.ai_tagger = self.ai_tagger if self.ai_tagger.enabled else None

        # ---- New feature components ----
        # 10-band HiFi EQ (processes audio before playback)
        self.equalizer = Equalizer(
            sample_rate=config.audio_sample_rate,
            channels=config.audio_channels,
        )
        # RDS decoder (best-effort — only detects stereo pilot with stock Gqrx)
        self.rds_decoder = RDSDecoder(sample_rate=config.audio_sample_rate)
        # Solar conditions fetcher (background thread, hits NOAA SWPC API)
        self.solar_fetcher = SolarFetcher()
        # (Started after first successful Gqrx connect — see _on_gqrx_connected)

        # ---- Magical new feature components ----
        # Audio visualizer (multi-mode: oscilloscope / spectrum / circular / liquid light)
        self.audio_visualizer = AudioVisualizer()
        self.audio_visualizer.set_mode(config.visualizer_mode)
        # Time-travel audio buffer (30 s rewind)
        self.time_travel_buffer = TimeTravelBuffer(
            sample_rate=config.audio_sample_rate,
            channels=config.audio_channels,
        )
        self.time_travel_widget = TimeTravelWidget()
        # Morse code (CW) decoder — runs in real time on every audio chunk
        self.cw_decoder = CWDecoder()
        self.cw_decoder.enabled = config.cw_decoder_enabled
        # DX Cluster live ticker
        self.dx_cluster = DXClusterClient()
        # Auto-surfer — magic "play whatever's loudest" button
        self.auto_surfer = AutoSurfer(self.gqrx)
        # Track whether we're in time-travel replay mode (vs live)
        self._time_travel_replaying = False

        # Web server (starts after the rest is wired)
        self.web_server: Optional[WebServer] = None

        # ----------------------------- build UI -----------------------------
        self._build_ui()

        # ----------------------------- wire signals -----------------------------
        self._wire_signals()

        # ----------------------------- periodic timers -----------------------------
        # Status poller
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(500)
        self.status_timer.timeout.connect(self._update_status)
        self.status_timer.start()

        # UDP health-check poller — looks at AudioReceiver / SpectrumReceiver
        # packet counters and updates the diagnostic banner. Starts only after
        # the user connects to Gqrx.
        self.diag_timer = QTimer(self)
        self.diag_timer.setInterval(1500)
        self.diag_timer.timeout.connect(self._update_diagnostic_banner)
        # Don't start until connected; see _on_gqrx_connected / _on_gqrx_disconnected.

        # Conditions + RDS update timer — refreshes the Conditions tab (solar
        # data, band conditions) and the Signal Info tab (RDS pilot detection)
        # every 3 seconds. Solar data is cached by the SolarFetcher thread;
        # this timer just refreshes the UI from the cache.
        self.conditions_timer = QTimer(self)
        self.conditions_timer.setInterval(3000)
        self.conditions_timer.timeout.connect(self._update_conditions)
        self.conditions_timer.timeout.connect(self._update_rds_panel)
        self.conditions_timer.timeout.connect(self._update_aurora_panel)
        # Start immediately (solar data is independent of Gqrx connection)
        self.conditions_timer.start()

        # DX cluster spot list refresh — re-renders the spot list every 5 s
        # so the age timestamps stay fresh. Spots themselves arrive
        # asynchronously via the DXClusterClient thread.
        self.dx_refresh_timer = QTimer(self)
        self.dx_refresh_timer.setInterval(5000)
        self.dx_refresh_timer.timeout.connect(self._refresh_dx_cluster_list)

        # CW decoder WPM + element display refresh — every 500 ms
        self.cw_refresh_timer = QTimer(self)
        self.cw_refresh_timer.setInterval(500)
        self.cw_refresh_timer.timeout.connect(self._refresh_cw_panel)
        self.cw_refresh_timer.start()

        # Apply initial state
        self._apply_config()

    # ----------------------------- UI construction -----------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # ---- Top bar: UTC + Local clock + Auto-Surf magic button ----
        # Always visible at the top of the window, updated every second.
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.addStretch(1)
        self.clock = ClockWidget()
        top_bar.addWidget(self.clock)
        top_bar.addStretch(1)
        # ✨ Auto-Surf magic button — scans every band and plays each strongest station
        self.auto_surf_btn = QPushButton("✨ Auto-Surf")
        self.auto_surf_btn.setToolTip(
            "Auto-Surf — magical tour of the radio dial.\n\n"
            "Click this and Magic SDR will:\n"
            "  1. Sweep every supported band (FM, AM, Air, NOAA, Marine, 2m, 70cm, HF)\n"
            "  2. Find the strongest signal in each band\n"
            "  3. Play it for 5 seconds\n"
            "  4. Move on to the next band\n"
            "  5. Return to the overall strongest station at the end\n\n"
            "Click again or press Stop to halt."
        )
        self.auto_surf_btn.setStyleSheet(
            "QPushButton {"
            "  background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            "    stop:0 #6a3aa0, stop:1 #4a2080);"
            "  color: #ffd9ff; border: 1px solid #a060d0; border-radius: 6px;"
            "  padding: 8px 16px; font-weight: 700; font-size: 12px;"
            "}"
            "QPushButton:hover {"
            "  background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            "    stop:0 #8a5ad0, stop:1 #6a3aa0);"
            "  color: #ffffff; border-color: #d080ff;"
            "}"
            "QPushButton:checked {"
            "  background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            "    stop:0 #d080ff, stop:1 #a060d0);"
            "  color: #0b0f14; border-color: #ffd9ff;"
            "}"
        )
        self.auto_surf_btn.setCheckable(True)
        self.auto_surf_btn.clicked.connect(self._on_auto_surf_clicked)
        top_bar.addWidget(self.auto_surf_btn)
        root.addLayout(top_bar)

        # ---- Memory Presets bar (car-radio style M1-M12) ----
        self.memory_bar = MemoryPresetBar(n_slots=12)
        self.memory_bar.tune_requested.connect(self._on_memory_tune)
        # Wire store callback — called when user long-presses a slot
        self.memory_bar.store_callback = self._make_memory_preset_from_current
        root.addWidget(self.memory_bar)

        # ---- Diagnostic banner (top of window) ----
        # Shows a prominent red/amber message when Gqrx is connected but its
        # UDP audio/spectrum streams are NOT arriving — the most common cause
        # of "0 stations found" and a black waterfall.
        self.diag_banner = QLabel("")
        self.diag_banner.setWordWrap(True)
        self.diag_banner.setStyleSheet(
            "QLabel { background: #3a1d1d; color: #ffb3b3; padding: 8px; "
            "border: 1px solid #ff5c5c; border-radius: 4px; font-size: 12px; }"
        )
        self.diag_banner.setVisible(False)
        root.addWidget(self.diag_banner)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, stretch=1)

        # ----- LEFT: tuner + controls -----
        left = QWidget()
        left_layout = QVBoxLayout(left)

        # Frequency dial + tuning knob side by side
        freq_row = QHBoxLayout()
        self.dial = FrequencyDial()
        self.dial.tune_requested.connect(self._tune_to)
        freq_row.addWidget(self.dial, stretch=1)
        # Main tuning knob — drag up/down or use wheel to tune
        self.tuning_knob = TuningKnob()
        self.tuning_knob.tune_step.connect(self._on_knob_step)
        self.tuning_knob.step_changed.connect(self._on_knob_step_changed)
        freq_row.addWidget(self.tuning_knob)
        left_layout.addLayout(freq_row)

        # Modulation + gain + squelch
        ctrl_box = QGroupBox("Receiver")
        ctrl_layout = QFormLayout(ctrl_box)
        self.mod_combo = QComboBox()
        self.mod_combo.addItems(MODULATIONS)
        self.mod_combo.currentTextChanged.connect(self._on_modulation_changed)
        ctrl_layout.addRow("Modulation:", self.mod_combo)

        self.gain_spin = QDoubleSpinBox()
        self.gain_spin.setRange(0, 49.6)
        self.gain_spin.setSingleStep(0.1)
        self.gain_spin.setSuffix(" dB")
        self.gain_spin.setValue(self.config.gain_db)
        self.gain_spin.valueChanged.connect(self._on_gain_changed)
        ctrl_layout.addRow("RF Gain:", self.gain_spin)

        self.sql_spin = QDoubleSpinBox()
        self.sql_spin.setRange(-150, 0)
        self.sql_spin.setSingleStep(1.0)
        self.sql_spin.setValue(-150)
        self.sql_spin.setSuffix(" dB")
        self.sql_spin.valueChanged.connect(self._on_squelch_changed)
        ctrl_layout.addRow("Squelch:", self.sql_spin)

        # Volume + mute
        vol_row = QHBoxLayout()
        self.vol_slider = QSlider(Qt.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(int(self.config.volume * 100))
        self.vol_slider.valueChanged.connect(self._on_volume_changed)
        vol_row.addWidget(self.vol_slider, stretch=1)
        self.mute_btn = QPushButton("🔊")
        self.mute_btn.setFixedWidth(40)
        self.mute_btn.setCheckable(True)
        self.mute_btn.toggled.connect(self._on_mute_toggled)
        vol_row.addWidget(self.mute_btn)
        ctrl_layout.addRow("Volume:", vol_row)

        # Signal level bar (using a QProgressBar as a meter) + analog S-meter
        sig_row = QHBoxLayout()
        self.signal_bar = QProgressBar()
        self.signal_bar.setRange(-100, 0)
        self.signal_bar.setFormat("%v dB")
        self.signal_bar.setValue(-100)
        self.signal_bar.setFixedWidth(120)
        sig_row.addWidget(self.signal_bar)
        # Analog S-meter (needle-style gauge)
        self.s_meter = SMeterWidget()
        sig_row.addWidget(self.s_meter, stretch=1)
        ctrl_layout.addRow("Signal:", sig_row)

        left_layout.addWidget(ctrl_box)

        # Recording controls
        rec_box = QGroupBox("Recording")
        rec_layout = QHBoxLayout(rec_box)
        self.rec_btn = QPushButton("● Record")
        self.rec_btn.setStyleSheet("QPushButton { color: #ff5c5c; font-weight: bold; padding: 8px; }"
                                    "QPushButton:checked { background: #ff5c5c; color: white; }")
        self.rec_btn.setCheckable(True)
        self.rec_btn.toggled.connect(self._on_record_toggled)
        rec_layout.addWidget(self.rec_btn)
        self.rec_status = QLabel("Idle")
        rec_layout.addWidget(self.rec_status)
        left_layout.addWidget(rec_box)

        # Connection controls
        conn_box = QGroupBox("Gqrx Connection")
        conn_layout = QHBoxLayout(conn_box)
        self.conn_btn = QPushButton("Connect")
        self.conn_btn.clicked.connect(self._on_connect_clicked)
        conn_layout.addWidget(self.conn_btn)
        self.diag_btn = QPushButton("🩺 Diagnose")
        self.diag_btn.setToolTip("Run a full diagnostic: TCP control, UDP audio, "
                                 "UDP spectrum, signal level, gain. Tells you "
                                 "exactly what is and isn't working.")
        self.diag_btn.clicked.connect(self._on_diagnose_clicked)
        conn_layout.addWidget(self.diag_btn)
        self.conn_status = QLabel("Disconnected")
        conn_layout.addWidget(self.conn_status)
        left_layout.addWidget(conn_box)

        # Web server toggle
        web_box = QGroupBox("Remote Access")
        web_layout = QHBoxLayout(web_box)
        self.web_btn = QPushButton("Start Web Server")
        self.web_btn.setCheckable(True)
        self.web_btn.setChecked(self.config.remote_access_enabled)
        self.web_btn.clicked.connect(self._on_web_toggled)
        web_layout.addWidget(self.web_btn)
        self.web_url = QLabel(f"http://0.0.0.0:{self.config.web_port}")
        web_layout.addWidget(self.web_url)
        left_layout.addWidget(web_box)

        # HiFi EQ — 10-band graphic equalizer + 16 named presets
        self.eq_box = QGroupBox("HiFi Equalizer (10-band)")
        eq_outer = QVBoxLayout(self.eq_box)
        # EQ enable checkbox + preset dropdown + reset button
        eq_top_row = QHBoxLayout()
        self.eq_enabled_chk = QCheckBox("Enabled")
        self.eq_enabled_chk.setChecked(True)
        self.eq_enabled_chk.toggled.connect(self._on_eq_enabled_toggled)
        eq_top_row.addWidget(self.eq_enabled_chk)
        eq_top_row.addWidget(QLabel("Preset:"))
        self.eq_preset_combo = QComboBox()
        self.eq_preset_combo.addItems(["Custom"] + get_preset_names())
        self.eq_preset_combo.currentTextChanged.connect(self._on_eq_preset_selected)
        eq_top_row.addWidget(self.eq_preset_combo, stretch=1)
        eq_reset_btn = QPushButton("Flat")
        eq_reset_btn.clicked.connect(self._on_eq_reset)
        eq_top_row.addWidget(eq_reset_btn)
        eq_outer.addLayout(eq_top_row)
        # EQ sliders — 10 vertical sliders, one per band.
        # Wrapped in a container widget so compact mode can hide the whole
        # slider bank in one setVisible() call (keeping the preset dropdown).
        self.eq_sliders_container = QWidget()
        self.eq_sliders_row = QHBoxLayout(self.eq_sliders_container)
        self.eq_sliders_row.setContentsMargins(0, 0, 0, 0)
        self.eq_sliders_row.setSpacing(4)
        self.eq_sliders: list[QSlider] = []
        for i, freq in enumerate(EQ_BANDS_HZ):
            col = QVBoxLayout()
            col.setSpacing(2)
            sld = QSlider(Qt.Vertical)
            sld.setRange(-20, 20)
            sld.setValue(0)
            sld.setTickPosition(QSlider.TicksBothSides)
            sld.setTickInterval(5)
            sld.valueChanged.connect(lambda v, idx=i: self._on_eq_slider_changed(idx, v))
            self.eq_sliders.append(sld)
            col.addWidget(sld, stretch=1)
            # Frequency label
            if freq >= 1000:
                freq_str = f"{freq // 1000}k"
            else:
                freq_str = str(freq)
            lbl = QLabel(freq_str)
            lbl.setStyleSheet("color: #888; font-size: 9px;")
            lbl.setAlignment(Qt.AlignCenter)
            col.addWidget(lbl)
            # Gain label (updates live)
            gain_lbl = QLabel("+0")
            gain_lbl.setStyleSheet("color: #5cd9ff; font-size: 9px; font-family: monospace;")
            gain_lbl.setAlignment(Qt.AlignCenter)
            gain_lbl.setObjectName(f"eq_gain_lbl_{i}")
            col.addWidget(gain_lbl)
            self.eq_sliders_row.addLayout(col)
        eq_outer.addWidget(self.eq_sliders_container)
        left_layout.addWidget(self.eq_box)

        # Time-Travel audio buffer — rewind up to 30 seconds of live radio
        left_layout.addWidget(self.time_travel_widget)

        left_layout.addStretch(1)
        # Wrap the left control panel in a scroll area so that on short windows
        # the bottom controls (EQ, Time-Travel, etc.) scroll instead of being
        # clipped off-screen.
        from PyQt5.QtWidgets import QScrollArea
        left_scroll = QScrollArea()
        left_scroll.setWidget(left)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setMinimumWidth(420)
        splitter.addWidget(left_scroll)

        # ----- RIGHT: waterfall + tabs -----
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.waterfall = WaterfallWidget()
        self.waterfall.tune_requested.connect(self._tune_to)
        right_layout.addWidget(self.waterfall, stretch=2)

        # Audio Visualizer — multi-mode live visualization
        # (oscilloscope / spectrum bars / circular / liquid light)
        # Wrapped in a single panel widget so compact mode can hide the whole
        # block (label + mode combo + canvas) in one call.
        self.viz_panel = QWidget()
        viz_outer = QVBoxLayout(self.viz_panel)
        viz_outer.setContentsMargins(0, 0, 0, 0)
        viz_outer.setSpacing(2)
        viz_row = QHBoxLayout()
        viz_row.setContentsMargins(0, 0, 0, 0)
        viz_label = QLabel("◈ Visualizer")
        viz_label.setStyleSheet(
            "color: #8b96a7; font-size: 10px; font-weight: 600; padding: 2px;"
        )
        viz_row.addWidget(viz_label)
        self.viz_mode_combo = QComboBox()
        self.viz_mode_combo.addItems(VISUALIZER_MODES)
        self.viz_mode_combo.currentTextChanged.connect(
            lambda m: self.audio_visualizer.set_mode(m) or self._on_viz_mode_changed(m)
        )
        viz_row.addWidget(self.viz_mode_combo)
        viz_row.addStretch(1)
        viz_outer.addLayout(viz_row)
        # The visualizer itself — compact height to keep waterfall dominant
        self.viz_container = QWidget()
        self.viz_container.setFixedHeight(120)
        viz_container_l = QVBoxLayout(self.viz_container)
        viz_container_l.setContentsMargins(0, 0, 0, 0)
        viz_container_l.addWidget(self.audio_visualizer)
        viz_outer.addWidget(self.viz_container)
        right_layout.addWidget(self.viz_panel)

        self.tabs = QTabWidget()
        right_layout.addWidget(self.tabs, stretch=1)

        # Bookmarks tab
        self.bookmark_list = QListWidget()
        self.bookmark_list.itemDoubleClicked.connect(self._on_bookmark_activated)
        self._refresh_bookmarks()
        bm_widget = QWidget()
        bm_layout = QVBoxLayout(bm_widget)
        bm_search = QLineEdit()
        bm_search.setPlaceholderText("Search bookmarks…")
        bm_search.textChanged.connect(self._on_bookmark_search)
        bm_layout.addWidget(bm_search)
        bm_layout.addWidget(self.bookmark_list)
        bm_add_row = QHBoxLayout()
        self.bm_add_freq = QLineEdit()
        self.bm_add_freq.setPlaceholderText("MHz")
        self.bm_add_label = QLineEdit()
        self.bm_add_label.setPlaceholderText("Label")
        bm_add_btn = QPushButton("Add")
        bm_add_btn.clicked.connect(self._on_add_bookmark)
        bm_add_row.addWidget(self.bm_add_freq)
        bm_add_row.addWidget(self.bm_add_label, stretch=1)
        bm_add_row.addWidget(bm_add_btn)
        bm_layout.addLayout(bm_add_row)
        self.tabs.addTab(bm_widget, "Bookmarks")

        # Scanner tab
        scan_widget = QWidget()
        scan_layout = QVBoxLayout(scan_widget)
        scan_band_row = QHBoxLayout()
        self.scan_band_combo = QComboBox()
        self.scan_band_combo.addItems([b.name for b in BANDS] + ["ALL BANDS"])
        scan_band_row.addWidget(self.scan_band_combo)
        scan_start_btn = QPushButton("▶ Scan")
        scan_start_btn.clicked.connect(self._on_scan_start)
        scan_band_row.addWidget(scan_start_btn)
        # New: Test sweep button — scans but reports EVERY frequency's level,
        # regardless of threshold. Great for diagnosing "0 stations found".
        test_sweep_btn = QPushButton("🔬 Test Sweep")
        test_sweep_btn.setToolTip("Sweep the band and show signal level at every frequency, "
                                  "regardless of threshold. Use this to diagnose if the scanner "
                                  "is seeing any signal at all.")
        test_sweep_btn.clicked.connect(self._on_test_sweep)
        scan_band_row.addWidget(test_sweep_btn)
        scan_stop_btn = QPushButton("■ Stop")
        scan_stop_btn.clicked.connect(lambda: self.scanner.stop())
        scan_band_row.addWidget(scan_stop_btn)
        scan_layout.addLayout(scan_band_row)

        # Live level readout — shows the current frequency being sampled and
        # its signal level, so you can see what the scanner is "hearing".
        self.scan_live_label = QLabel("Live: idle")
        self.scan_live_label.setStyleSheet("color: #5cd9ff; font-family: monospace; padding: 2px;")
        scan_layout.addWidget(self.scan_live_label)

        self.scan_progress = QProgressBar()
        scan_layout.addWidget(self.scan_progress)
        self.scan_status = QLabel("Idle")
        scan_layout.addWidget(self.scan_status)
        self.discovered_list = QListWidget()
        self.discovered_list.itemDoubleClicked.connect(self._on_discovered_activated)
        scan_layout.addWidget(self.discovered_list, stretch=1)

        # Test-sweep results list — shows every frequency's level
        self.test_sweep_list = QListWidget()
        self.test_sweep_list.itemDoubleClicked.connect(self._on_discovered_activated)
        scan_layout.addWidget(QLabel("Test sweep results (all frequencies sampled):"))
        scan_layout.addWidget(self.test_sweep_list, stretch=1)

        self.tabs.addTab(scan_widget, "Auto-Discover")

        # Recordings tab
        rec_widget = QWidget()
        rec_layout = QVBoxLayout(rec_widget)
        rec_refresh = QPushButton("⟳ Refresh")
        rec_refresh.clicked.connect(self._refresh_recordings)
        rec_layout.addWidget(rec_refresh)
        self.rec_list = QListWidget()
        rec_layout.addWidget(self.rec_list, stretch=1)
        self.tabs.addTab(rec_widget, "Recordings")

        # Conditions tab — solar flux, K-index, band-by-band propagation
        cond_widget = QWidget()
        cond_layout = QVBoxLayout(cond_widget)
        # Solar summary at top
        solar_box = QGroupBox("Solar Conditions (NOAA SWPC)")
        solar_outer = QVBoxLayout(solar_box)
        self.solar_summary_label = QLabel("Loading…")
        self.solar_summary_label.setStyleSheet(
            "font-family: monospace; font-size: 13px; color: #5cd9ff; padding: 8px;"
        )
        self.solar_summary_label.setWordWrap(True)
        solar_outer.addWidget(self.solar_summary_label)
        # Detailed solar fields in a grid
        solar_grid = QGridLayout()
        solar_grid.setSpacing(6)
        self.solar_detail_labels: dict[str, QLabel] = {}
        for i, field in enumerate([
            ("sfi", "Solar Flux (F10.7):"),
            ("ssn", "Sunspot Number:"),
            ("aindex", "Planetary A-index:"),
            ("kindex", "Planetary K-index:"),
            ("xray", "X-ray Class:"),
            ("xray_flux", "X-ray Flux:"),
            ("updated", "Last Updated:"),
            ("message", "NOAA Message:"),
        ]):
            key, lbl_text = field
            lbl_l = QLabel(lbl_text)
            lbl_l.setStyleSheet("color: #888;")
            solar_grid.addWidget(lbl_l, i, 0)
            val_lbl = QLabel("—")
            val_lbl.setStyleSheet("color: #fff; font-family: monospace;")
            val_lbl.setWordWrap(True)
            solar_grid.addWidget(val_lbl, i, 1)
            self.solar_detail_labels[key] = val_lbl
        solar_outer.addLayout(solar_grid)
        # Refresh button for solar data
        solar_refresh_row = QHBoxLayout()
        solar_refresh_btn = QPushButton("⟳ Refresh Now")
        solar_refresh_btn.clicked.connect(lambda: self.solar_fetcher.force_refresh())
        solar_refresh_row.addWidget(solar_refresh_btn)
        solar_refresh_row.addStretch(1)
        self.solar_status_lbl = QLabel("")
        self.solar_status_lbl.setStyleSheet("color: #888; font-size: 10px;")
        solar_refresh_row.addWidget(self.solar_status_lbl)
        solar_outer.addLayout(solar_refresh_row)
        cond_layout.addWidget(solar_box)

        # Band conditions table
        band_box = QGroupBox("HF Band Conditions (estimated from solar + time)")
        band_outer = QVBoxLayout(band_box)
        self.band_conditions_label = QLabel("Loading…")
        self.band_conditions_label.setStyleSheet(
            "font-family: monospace; font-size: 12px; padding: 8px;"
        )
        self.band_conditions_label.setWordWrap(True)
        band_outer.addWidget(self.band_conditions_label)
        cond_layout.addWidget(band_box)

        # Aurora forecast panel — based on K-index + observer latitude
        aurora_box = QGroupBox("Aurora Forecast (visible from Baltimore, MD)")
        aurora_outer = QVBoxLayout(aurora_box)
        self.aurora_summary_label = QLabel("Loading…")
        self.aurora_summary_label.setStyleSheet(
            "font-family: monospace; font-size: 12px; color: #b380ff; padding: 6px;"
        )
        self.aurora_summary_label.setWordWrap(True)
        aurora_outer.addWidget(self.aurora_summary_label)
        # Detailed aurora fields
        aurora_grid = QGridLayout()
        aurora_grid.setSpacing(4)
        for i, (key, lbl_text) in enumerate([
            ("storm_class", "Storm Class:"),
            ("oval_lat", "Auroral Oval:"),
            ("visible", "Visible From You:"),
            ("hf_abs", "HF Absorption:"),
            ("vhf_scatter", "VHF Scatter:"),
        ]):
            lbl = QLabel(lbl_text)
            lbl.setStyleSheet("color: #888;")
            aurora_grid.addWidget(lbl, i, 0)
            val = QLabel("—")
            val.setStyleSheet("color: #fff; font-family: monospace;")
            val.setWordWrap(True)
            aurora_grid.addWidget(val, i, 1)
        self.aurora_detail_labels = {
            "storm_class": aurora_grid.itemAtPosition(0, 1).widget(),
            "oval_lat": aurora_grid.itemAtPosition(1, 1).widget(),
            "visible": aurora_grid.itemAtPosition(2, 1).widget(),
            "hf_abs": aurora_grid.itemAtPosition(3, 1).widget(),
            "vhf_scatter": aurora_grid.itemAtPosition(4, 1).widget(),
        }
        aurora_outer.addLayout(aurora_grid)
        cond_layout.addWidget(aurora_box)
        cond_layout.addStretch(1)
        self.tabs.addTab(cond_widget, "Conditions")

        # Signal Info tab — RDS + HD Radio
        sig_widget = QWidget()
        sig_layout = QVBoxLayout(sig_widget)
        # RDS panel
        rds_box = QGroupBox("RDS (FM Radio Data System)")
        rds_outer = QVBoxLayout(rds_box)
        rds_intro = QLabel(
            "RDS carries station name (PS), program type (PTY), and radio text (RT) "
            "on a 57 kHz subcarrier. The 19 kHz stereo pilot is detected from the "
            "audio; full RDS decoding requires MPX audio output (not available from "
            "stock Gqrx WFM demodulator)."
        )
        rds_intro.setWordWrap(True)
        rds_intro.setStyleSheet("color: #888; font-size: 10px;")
        rds_outer.addWidget(rds_intro)
        rds_grid = QGridLayout()
        rds_grid.setSpacing(6)
        for i, (key, lbl_text) in enumerate([
            ("pilot", "Stereo Pilot:"),
            ("pilot_str", "Pilot Strength:"),
            ("ps", "Station Name (PS):"),
            ("pty", "Program Type (PTY):"),
            ("pi", "Program ID (PI):"),
            ("rt", "Radio Text (RT):"),
        ]):
            lbl = QLabel(lbl_text)
            lbl.setStyleSheet("color: #888;")
            rds_grid.addWidget(lbl, i, 0)
            val = QLabel("—")
            val.setStyleSheet("color: #5cd9ff; font-family: monospace;")
            val.setWordWrap(True)
            rds_grid.addWidget(val, i, 1)
        self.rds_labels = {
            "pilot": rds_grid.itemAtPosition(0, 1).widget(),
            "pilot_str": rds_grid.itemAtPosition(1, 1).widget(),
            "ps": rds_grid.itemAtPosition(2, 1).widget(),
            "pty": rds_grid.itemAtPosition(3, 1).widget(),
            "pi": rds_grid.itemAtPosition(4, 1).widget(),
            "rt": rds_grid.itemAtPosition(5, 1).widget(),
        }
        rds_outer.addLayout(rds_grid)
        sig_layout.addWidget(rds_box)

        # HD Radio info panel
        hd_box = QGroupBox("HD Radio")
        hd_outer = QVBoxLayout(hd_box)
        hd_text = QLabel(HD_RADIO_INFO_TEXT)
        hd_text.setWordWrap(True)
        hd_text.setStyleSheet("color: #ccc; font-size: 11px;")
        hd_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        hd_outer.addWidget(hd_text)
        sig_layout.addWidget(hd_box)
        sig_layout.addStretch(1)
        self.tabs.addTab(sig_widget, "Signal Info")

        # CW (Morse) Decoder tab — real-time Morse code decoding
        cw_widget = QWidget()
        cw_layout = QVBoxLayout(cw_widget)
        cw_intro = QLabel(
            "✦ Morse Code (CW) Decoder — listens to the demodulated audio and "
            "decodes amplitude-keyed Morse signals in real time.\n\n"
            "Best on a clean CW signal (e.g. tune to the 30m CW band, 10.100-10.150 MHz). "
            "The decoder auto-adapts to the operator's WPM speed.\n"
            "Prosigns like <AR> (end of message) and <SK> (end of contact) are decoded too."
        )
        cw_intro.setWordWrap(True)
        cw_intro.setStyleSheet("color: #ccc; font-size: 11px; padding: 4px;")
        cw_layout.addWidget(cw_intro)

        # CW controls
        cw_ctrl_row = QHBoxLayout()
        self.cw_enabled_chk = QCheckBox("Decode CW")
        self.cw_enabled_chk.setChecked(self.cw_decoder.enabled)
        self.cw_enabled_chk.toggled.connect(self._on_cw_enabled_toggled)
        cw_ctrl_row.addWidget(self.cw_enabled_chk)
        cw_ctrl_row.addStretch(1)
        self.cw_wpm_label = QLabel("WPM: —")
        self.cw_wpm_label.setStyleSheet(
            "color: #5cd9ff; font-family: 'JetBrains Mono'; font-weight: 600;"
        )
        cw_ctrl_row.addWidget(self.cw_wpm_label)
        cw_ctrl_row.addStretch(1)
        self.cw_element_label = QLabel("Current: —")
        self.cw_element_label.setStyleSheet(
            "color: #ffd45c; font-family: 'JetBrains Mono';"
        )
        cw_ctrl_row.addWidget(self.cw_element_label)
        cw_ctrl_row.addStretch(1)
        cw_clear_btn = QPushButton("Clear Text")
        cw_clear_btn.clicked.connect(lambda: self.cw_decoder.clear_text())
        cw_ctrl_row.addWidget(cw_clear_btn)
        cw_reset_btn = QPushButton("Reset Decoder")
        cw_reset_btn.clicked.connect(lambda: self.cw_decoder.reset())
        cw_ctrl_row.addWidget(cw_reset_btn)
        cw_layout.addLayout(cw_ctrl_row)

        # Decoded text area — large, monospaced, scrollable
        from PyQt5.QtWidgets import QTextEdit, QScrollArea
        self.cw_text_display = QTextEdit()
        self.cw_text_display.setReadOnly(True)
        self.cw_text_display.setStyleSheet(
            "QTextEdit {"
            "  background-color: #06080c; color: #5cffaa;"
            "  font-family: 'JetBrains Mono'; font-size: 14px;"
            "  border: 1px solid #2a5a3a; border-radius: 4px; padding: 8px;"
            "}"
        )
        self.cw_text_display.setPlaceholderText("Decoded Morse text will appear here…")
        cw_layout.addWidget(self.cw_text_display, stretch=1)
        self.tabs.addTab(cw_widget, "CW Decoder")

        # DX Cluster tab — live feed of worldwide ham radio spots
        dx_widget = QWidget()
        dx_layout = QVBoxLayout(dx_widget)
        dx_intro = QLabel(
            "🌍 DX Cluster — live feed of ham-radio DX spots from a networked cluster.\n"
            "Each line shows: frequency, spotted station, spotter, comment, Zulu time, age.\n"
            "Double-click a spot to tune to that frequency."
        )
        dx_intro.setWordWrap(True)
        dx_intro.setStyleSheet("color: #ccc; font-size: 11px; padding: 4px;")
        dx_layout.addWidget(dx_intro)

        dx_ctrl_row = QHBoxLayout()
        self.dx_connect_btn = QPushButton("Connect to Cluster")
        self.dx_connect_btn.setCheckable(True)
        self.dx_connect_btn.setChecked(self.config.dx_cluster_enabled)
        self.dx_connect_btn.clicked.connect(self._on_dx_connect_toggled)
        dx_ctrl_row.addWidget(self.dx_connect_btn)
        dx_ctrl_row.addStretch(1)
        self.dx_status_label = QLabel("Disconnected")
        self.dx_status_label.setStyleSheet("color: #8b96a7; font-family: monospace;")
        dx_ctrl_row.addWidget(self.dx_status_label)
        dx_layout.addLayout(dx_ctrl_row)

        # Filter row — show only spots matching a callsign or band
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter:"))
        self.dx_filter_edit = QLineEdit()
        self.dx_filter_edit.setPlaceholderText("callsign, band, or mode (e.g. 'FT8', '14025', 'ZL')")
        self.dx_filter_edit.textChanged.connect(self._refresh_dx_cluster_list)
        filter_row.addWidget(self.dx_filter_edit, stretch=1)
        dx_layout.addLayout(filter_row)

        self.dx_list = QListWidget()
        self.dx_list.itemDoubleClicked.connect(self._on_dx_spot_activated)
        dx_layout.addWidget(self.dx_list, stretch=1)
        self.tabs.addTab(dx_widget, "DX Cluster")
        # Apply saved initial state
        if self.config.dx_cluster_enabled:
            QTimer.singleShot(1500, self._dx_auto_connect)

        # Settings tab
        set_widget = QWidget()
        set_layout = QFormLayout(set_widget)
        self.set_threshold = QDoubleSpinBox()
        self.set_threshold.setRange(-120, 0)
        self.set_threshold.setValue(self.config.scan_threshold_db)
        self.set_threshold.setSuffix(" dB")
        self.set_threshold.valueChanged.connect(self._on_settings_changed)
        self.set_threshold.setToolTip(
            "Signal level (in dBFS) above which a frequency is considered a station.\n\n"
            "Gqrx reports dBFS, not dBm. Typical values:\n"
            "  -30 to -50: very strong local FM\n"
            "  -50 to -65: normal stations\n"
            "  -65 to -80: weak but audible\n"
            "  -80 to -120: noise floor\n\n"
            "If 'Auto threshold' is on, this value is overridden by noise_floor + margin."
        )
        set_layout.addRow("Scan threshold:", self.set_threshold)

        # Auto-threshold toggle — when on, scanner measures noise floor at the
        # start of each scan and uses noise_floor + margin as the threshold.
        # This is the fix for "0 stations found".
        self.set_auto_threshold = QCheckBox("Auto-calibrate threshold from noise floor")
        self.set_auto_threshold.setChecked(True)
        self.set_auto_threshold.setToolTip(
            "Before each scan, samples 10 frequencies across the band to measure the "
            "local noise floor, then sets the threshold to noise_floor + margin.\n\n"
            "Strongly recommended — fixes the common '0 stations found' issue when "
            "the threshold doesn't match your antenna/gain conditions."
        )
        self.set_auto_threshold.toggled.connect(self._on_settings_changed)
        set_layout.addRow(self.set_auto_threshold)

        self.set_margin = QDoubleSpinBox()
        self.set_margin.setRange(0, 40)
        self.set_margin.setSingleStep(1.0)
        self.set_margin.setValue(10.0)
        self.set_margin.setSuffix(" dB")
        self.set_margin.setToolTip("How many dB above the measured noise floor a signal must be to count as a station.")
        self.set_margin.valueChanged.connect(self._on_settings_changed)
        set_layout.addRow("Auto threshold margin:", self.set_margin)

        self.set_dwell = QDoubleSpinBox()
        self.set_dwell.setRange(0.05, 5.0)
        self.set_dwell.setSingleStep(0.05)
        self.set_dwell.setValue(self.config.scan_dwell_s)
        self.set_dwell.setSuffix(" s")
        self.set_dwell.valueChanged.connect(self._on_settings_changed)
        set_layout.addRow("Scan dwell time:", self.set_dwell)
        self.set_ai = QCheckBox("Enable AI tagging")
        self.set_ai.setChecked(self.config.ai_tagging_enabled)
        self.set_ai.toggled.connect(self._on_settings_changed)
        set_layout.addRow(self.set_ai)

        # Gqrx config helper — writes a known-good ~/.config/gqrx/default.conf
        # so the user doesn't have to hunt for "Audio UDP" / "Remote control"
        # menu items that vary across Gqrx versions.
        gqrx_cfg_box = QGroupBox("Gqrx setup")
        gqrx_cfg_layout = QVBoxLayout(gqrx_cfg_box)
        gqrx_cfg_intro = QLabel(
            "Click the button below to write a known-good Gqrx config that enables:\n"
            "  • Remote control TCP on 127.0.0.1:7356\n"
            "  • Audio UDP stream to 127.0.0.1:7355\n\n"
            "Your existing config is backed up first. After writing, quit Gqrx\n"
            "(if running) and re-launch it for the changes to take effect."
        )
        gqrx_cfg_intro.setWordWrap(True)
        gqrx_cfg_layout.addWidget(gqrx_cfg_intro)

        self.gqrx_cfg_btn = QPushButton("🔧 Setup Gqrx config")
        self.gqrx_cfg_btn.clicked.connect(self._setup_gqrx_config)
        gqrx_cfg_layout.addWidget(self.gqrx_cfg_btn)

        self.gqrx_inspect_btn = QPushButton("🔍 Inspect Gqrx config")
        self.gqrx_inspect_btn.clicked.connect(self._inspect_gqrx_config)
        gqrx_cfg_layout.addWidget(self.gqrx_inspect_btn)

        set_layout.addRow(gqrx_cfg_box)

        # ---- Magic features settings ----
        magic_box = QGroupBox("✨ Magic Features")
        magic_layout = QVBoxLayout(magic_box)

        # Night vision mode — red theme for dark adaptation
        self.set_night_vision = QCheckBox("Night Vision mode (red theme — preserves dark adaptation)")
        self.set_night_vision.setChecked(self.config.night_vision)
        self.set_night_vision.setToolTip(
            "Switches the entire UI to a deep red theme. Red light preserves night vision,\n"
            "so ham operators can use Magic SDR in a dark shack without losing dark adaptation.\n"
            "Takes effect on next app restart (or click Apply below)."
        )
        self.set_night_vision.toggled.connect(self._on_night_vision_toggled)
        magic_layout.addWidget(self.set_night_vision)

        # Auto-start DX cluster on launch
        self.set_dx_autostart = QCheckBox("Auto-connect to DX cluster on launch")
        self.set_dx_autostart.setChecked(self.config.dx_cluster_enabled)
        self.set_dx_autostart.toggled.connect(self._on_settings_changed)
        magic_layout.addWidget(self.set_dx_autostart)

        # CW decoder auto-enable
        self.set_cw_enabled = QCheckBox("Enable CW (Morse) decoder")
        self.set_cw_enabled.setChecked(self.config.cw_decoder_enabled)
        self.set_cw_enabled.toggled.connect(self._on_settings_changed)
        magic_layout.addWidget(self.set_cw_enabled)

        # Apply night vision immediately button
        apply_nv_btn = QPushButton("Apply Night Vision now")
        apply_nv_btn.clicked.connect(self._apply_night_vision_now)
        magic_layout.addWidget(apply_nv_btn)

        set_layout.addRow(magic_box)

        self.tabs.addTab(set_widget, "Settings")

        splitter.addWidget(right)
        splitter.setSizes([400, 800])

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready. Click 'Connect' to attach to Gqrx.")

        # ---- Menu bar: View menu with Compact Mode toggle ----
        view_menu = self.menuBar().addMenu("&View")
        self.compact_action = QAction("&Compact Mode", self, checkable=True)
        self.compact_action.setChecked(self.config.compact_mode)
        self.compact_action.setShortcut(QKeySequence("Ctrl+M"))
        self.compact_action.setStatusTip(
            "Compact Mode — hide visualizer, time-travel, and EQ sliders; "
            "shrink memory buttons. (Ctrl+M)"
        )
        self.compact_action.toggled.connect(self._on_compact_mode_toggled)
        view_menu.addAction(self.compact_action)

        view_menu.addSeparator()
        # Quick toggles for individual panels (handy even outside compact mode)
        self.toggle_viz_action = QAction("Show &Visualizer", self, checkable=True)
        self.toggle_viz_action.setChecked(True)
        self.toggle_viz_action.setStatusTip("Show or hide the audio visualizer panel.")
        self.toggle_viz_action.toggled.connect(self._on_toggle_visualizer)
        view_menu.addAction(self.toggle_viz_action)

        self.toggle_eq_action = QAction("Show EQ &Sliders", self, checkable=True)
        self.toggle_eq_action.setChecked(True)
        self.toggle_eq_action.setStatusTip("Show or hide the 10-band EQ sliders (preset dropdown stays).")
        self.toggle_eq_action.toggled.connect(self._on_toggle_eq_sliders)
        view_menu.addAction(self.toggle_eq_action)

        self.toggle_timetravel_action = QAction("Show &Time-Travel", self, checkable=True)
        self.toggle_timetravel_action.setChecked(True)
        self.toggle_timetravel_action.setStatusTip("Show or hide the Time-Travel rewind bar.")
        self.toggle_timetravel_action.toggled.connect(self._on_toggle_time_travel)
        view_menu.addAction(self.toggle_timetravel_action)

        view_menu.addSeparator()
        reset_layout_action = QAction("&Reset Window Layout", self)
        reset_layout_action.setStatusTip("Restore all panels to their default visibility.")
        reset_layout_action.triggered.connect(self._on_reset_layout)
        view_menu.addAction(reset_layout_action)

    # ----------------------------- signal wiring -----------------------------
    def _wire_signals(self) -> None:
        # Gqrx signals
        self.gqrx.connected.connect(self._on_gqrx_connected)
        self.gqrx.disconnected.connect(self._on_gqrx_disconnected)
        self.gqrx.frequency_changed.connect(self._on_freq_changed)
        self.gqrx.modulation_changed.connect(self._on_mod_changed)
        self.gqrx.signal_level.connect(self._on_signal_level)
        self.gqrx.error.connect(self._on_gqrx_error)

        # Audio → player + recording + audio-FFT spectrum fallback
        self.audio_receiver.chunk_ready.connect(self._on_audio_chunk)

        # Spectrum sources → waterfall
        # 1. UDP spectrum (rare — only patched Gqrx forks). When packets arrive
        #    they take priority (RF spectrum is more accurate than audio FFT).
        self.spectrum_receiver.spectrum_ready.connect(
            lambda data, c, s: self.waterfall.update_spectrum(data, c, s)
        )
        # 2. Audio-FFT fallback — always active, computes spectrum from the
        #    demodulated audio stream. Shows up automatically when no UDP
        #    spectrum packets are arriving (which is the normal case).
        self.audio_spectrum.spectrum_ready.connect(
            lambda data, c, s: self.waterfall.update_spectrum(data, c, s)
        )

        # Scanner
        self.scanner.scan_started.connect(self._on_scan_started)
        self.scanner.scan_progress.connect(self._on_scan_progress)
        self.scanner.scan_progress_freq.connect(
            lambda f: self.scan_status.setText(f"Scanning: {f/1e6:.4f} MHz")
        )
        # Live level readout — shows what the scanner is hearing in real time
        self.scanner.scan_progress_level.connect(self._on_scan_progress_level)
        self.scanner.station_found.connect(self._on_station_found)
        self.scanner.scan_finished.connect(self._on_scan_finished)
        self.scanner.scan_error.connect(self._on_scan_error)
        self.scanner.noise_floor_calibrated.connect(self._on_noise_floor_calibrated)

        # Recordings
        self.recordings.recording_started.connect(self._on_recording_started)
        self.recordings.recording_stopped.connect(self._on_recording_stopped)
        self.recordings.chunk_recorded.connect(
            lambda frames: self.rec_status.setText(f"● {frames / 48000:.1f}s")
        )

        # AI tagger
        self.ai_tagger.tag_ready.connect(self._on_ai_tag_ready)
        self.ai_tagger.tag_failed.connect(
            lambda f, e: self.status.showMessage(f"AI tag failed for {f/1e6:.4f}: {e}", 3000)
        )

        # Time-travel widget mode change → toggle live/replay
        self.time_travel_widget.mode_changed.connect(self._on_time_travel_mode_changed)

    def _apply_config(self) -> None:
        self.dial.set_frequency(self.config.last_frequency_hz)
        self.mod_combo.setCurrentText(self.config.last_modulation)
        # Apply initial band context to waterfall
        self._update_band_context(self.config.last_frequency_hz)
        # Auto-start web server if enabled
        if self.config.remote_access_enabled:
            QTimer.singleShot(500, lambda: self.web_btn.click())
        # Start solar fetcher (independent of Gqrx — runs from app launch)
        self.solar_fetcher.start()
        # Trigger initial conditions panel update
        QTimer.singleShot(100, self._update_conditions)
        QTimer.singleShot(100, self._update_aurora_panel)

        # ---- Load EQ state from config ----
        if self.config.eq_gains and len(self.config.eq_gains) == len(self.eq_sliders):
            for i, gain in enumerate(self.config.eq_gains):
                self.eq_sliders[i].blockSignals(True)
                self.eq_sliders[i].setValue(int(gain))
                self.eq_sliders[i].blockSignals(False)
                self.equalizer.set_band_gain(i, float(gain))
                # Update the per-band label too
                lbl = self.findChild(QLabel, f"eq_gain_lbl_{i}")
                if lbl:
                    sign = "+" if gain >= 0 else ""
                    lbl.setText(f"{sign}{int(gain)}")
        self.eq_enabled_chk.setChecked(self.config.eq_enabled)
        self.equalizer.set_enabled(self.config.eq_enabled)
        # Set the preset combo to the saved preset name, or "Custom" if not in the list
        if self.config.eq_preset_name in [self.eq_preset_combo.itemText(i) for i in range(self.eq_preset_combo.count())]:
            self.eq_preset_combo.setCurrentText(self.config.eq_preset_name)
        else:
            self.eq_preset_combo.setCurrentText("Custom")

        # ---- Load visualizer mode ----
        if self.config.visualizer_mode in VISUALIZER_MODES:
            self.viz_mode_combo.setCurrentText(self.config.visualizer_mode)
            self.audio_visualizer.set_mode(self.config.visualizer_mode)

        # ---- Load memory presets ----
        from .memory_presets import MemoryPreset
        presets = []
        for pd in self.config.memory_presets:
            if pd and isinstance(pd, dict) and pd.get("freq_hz", 0) > 0:
                presets.append(MemoryPreset(
                    freq_hz=pd["freq_hz"],
                    modulation=pd.get("modulation", ""),
                    label=pd.get("label", ""),
                    stored_at=pd.get("stored_at", 0.0),
                ))
            else:
                presets.append(None)
        self.memory_bar.set_presets(presets)

        # ---- Load DX cluster autostart setting ----
        self.config.dx_cluster_enabled = self.set_dx_autostart.isChecked()
        self.config.cw_decoder_enabled = self.set_cw_enabled.isChecked()
        self.cw_decoder.enabled = self.config.cw_decoder_enabled

        # ---- Apply compact mode on startup (if saved in config) ----
        # Sync the menu checkbox without firing toggled signal (which would
        # show a status message and re-save config unnecessarily at startup).
        self.compact_action.blockSignals(True)
        self.compact_action.setChecked(self.config.compact_mode)
        self.compact_action.blockSignals(False)
        self._apply_compact_visibility(self.config.compact_mode)

    def _save_magic_state(self) -> None:
        """Persist all the magic-feature state to config."""
        # EQ gains
        self.config.eq_gains = [float(s.value()) for s in self.eq_sliders]
        self.config.eq_enabled = self.eq_enabled_chk.isChecked()
        self.config.eq_preset_name = self.eq_preset_combo.currentText()
        # Visualizer mode
        self.config.visualizer_mode = self.audio_visualizer.mode
        # Memory presets — serialize to list of dicts
        serialized = []
        for p in self.memory_bar.get_presets():
            if p is None:
                serialized.append(None)
            else:
                serialized.append({
                    "freq_hz": p.freq_hz,
                    "modulation": p.modulation,
                    "label": p.label,
                    "stored_at": p.stored_at,
                })
        self.config.memory_presets = serialized
        # CW + DX settings
        self.config.cw_decoder_enabled = self.cw_decoder.enabled
        self.config.dx_cluster_enabled = self.dx_connect_btn.isChecked()
        # Night vision
        self.config.night_vision = self.set_night_vision.isChecked()
        self.config.save()

    # ----------------------------- handlers -----------------------------
    def _on_connect_clicked(self) -> None:
        if self.gqrx.is_connected():
            self.gqrx.disconnect()
            self.diag_timer.stop()
            self._update_diagnostic_banner()  # hide banner
        else:
            ok = self.gqrx.connect()
            if not ok:
                # Offer to auto-write the Gqrx config so the user doesn't
                # have to hunt through Gqrx's menus (which vary across
                # versions and may not even have an Audio UDP item).
                reply = QMessageBox.question(
                    self, "Cannot connect to Gqrx",
                    f"Could not connect to Gqrx at {self.config.gqrx_host}:{self.config.gqrx_port}.\n\n"
                    "This means Gqrx is not running, OR its remote control TCP\n"
                    "is not enabled on port 7356.\n\n"
                    "Quickest fix — let Magic SDR write a known-good Gqrx config\n"
                    "for you (backups your existing one first)?\n\n"
                    "  • Yes  → writes ~/.config/gqrx/default.conf with remote\n"
                    "           control + audio UDP enabled; then you re-launch Gqrx.\n"
                    "  • No   → I'll set it up manually in Gqrx's menus.",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
                )
                if reply == QMessageBox.Yes:
                    self._setup_gqrx_config()
                return
            # Start audio + spectrum receivers
            self.audio_receiver.start()
            self.spectrum_receiver.start()
            # Restore state
            self.gqrx.set_frequency(self.config.last_frequency_hz)
            self.gqrx.set_modulation(self.config.last_modulation)
            # Auto-set a reasonable RF gain if the user has it at 0.
            # 0 dB gain means AGC off + zero manual gain → effectively deaf.
            # 40 dB is a safe starting point for RTL-SDR V3 with a decent antenna.
            if self.config.gain_db <= 0.0:
                self.gqrx.set_rf_gain(40.0)
                self.config.gain_db = 40.0
                self.gain_spin.blockSignals(True)
                self.gain_spin.setValue(40.0)
                self.gain_spin.blockSignals(False)
            else:
                self.gqrx.set_rf_gain(self.config.gain_db)
            # Start the diagnostic banner poller — first check happens after
            # a short delay to give UDP streams time to start arriving.
            QTimer.singleShot(2500, self._update_diagnostic_banner)
            self.diag_timer.start()

    def _on_gqrx_connected(self) -> None:
        self.conn_btn.setText("Disconnect")
        self.conn_status.setText("Connected")
        self.conn_status.setStyleSheet("color: #5cffaa;")
        self.status.showMessage("Connected to Gqrx.", 3000)

    def _on_gqrx_disconnected(self, reason: str) -> None:
        self.conn_btn.setText("Connect")
        self.conn_status.setText("Disconnected")
        self.conn_status.setStyleSheet("color: #ff5c5c;")
        self.status.showMessage(f"Disconnected: {reason}", 5000)
        self.diag_timer.stop()
        self._update_diagnostic_banner()  # hide banner

    def _on_gqrx_error(self, err: str) -> None:
        self.status.showMessage(err, 5000)

    # ----------------------------- diagnostics -----------------------------
    def _update_diagnostic_banner(self) -> None:
        """Update the top banner based on stream health.

        The banner appears when Gqrx is connected but the user hasn't:
          - Pressed Play ▶ in Gqrx (receiver is paused → no signal)
          - Enabled the Audio UDP stream in Gqrx (Tools → Audio UDP)
          - Set RF Gain > 0
        These are the common causes of "0 stations found" and a black waterfall.
        """
        if not self.gqrx.is_connected():
            self.diag_banner.setVisible(False)
            return
        audio_ok = self.audio_receiver.is_streaming(max_age_s=3.0)
        audio_count = self.audio_receiver.packet_count()

        # Audio UDP is the ONLY UDP stream Gqrx supports. (Stock Gqrx has no
        # spectrum UDP stream.) So we only check audio.
        if audio_ok:
            self.diag_banner.setVisible(False)
            return

        # Build a clear, actionable message
        parts = ["<b>⚠ Gqrx is connected but not streaming audio</b> — this is why "
                 "the scanner finds 0 stations and the waterfall is silent. "
                 "Gqrx's TCP control works (we can change frequency), but Gqrx "
                 "isn't sending audio to Magic SDR."]
        parts.append("")
        parts.append(f"Audio UDP (port 7355): {'OK — ' + str(audio_count) + ' pkts' if audio_ok else 'NOT receiving'}")
        parts.append("")
        parts.append("<b>Fix — 3 steps in Gqrx (do all of them):</b>")
        parts.append("  1. <b>Press the green ▶ Play button</b> in Gqrx's main window "
                     "(top toolbar). If Gqrx isn't actively receiving, no audio "
                     "gets streamed and `l STRENGTH` returns nothing.")
        parts.append("  2. <b>Tools → Audio UDP</b> → check the box to enable, "
                     "set host 127.0.0.1, port 7355, click Start. "
                     "(This is a SEPARATE menu from Remote control settings.)")
        parts.append("  3. <b>Set RF Gain to ~40 dB</b> in Gqrx's Device settings "
                     "(gear icon) — 0 dB gain = deaf receiver.")
        parts.append("")
        parts.append("Note: stock Gqrx does NOT have a Spectrum UDP stream. "
                     "Magic SDR will draw the waterfall from the audio FFT instead, "
                     "so you don't need a spectrum stream.")
        parts.append("")
        parts.append("Click 🩺 Diagnose for a full report.")
        self.diag_banner.setText("<br>".join(parts))
        self.diag_banner.setVisible(True)

    def _on_diagnose_clicked(self) -> None:
        """Open a detailed diagnostic dialog showing what's working and what's not."""
        report = self._build_diagnostic_report()
        QMessageBox.information(self, "Gqrx Diagnostics", report)

    def _build_diagnostic_report(self) -> str:
        """Gather every signal we can check and return a multi-line report."""
        lines: list[str] = []
        lines.append("═══════════════════════════════════════════════════════════")
        lines.append(" Magic SDR — Gqrx Diagnostics")
        lines.append("═══════════════════════════════════════════════════════════\n")

        # 1. TCP control
        lines.append("── TCP control (port 7356) ──")
        if self.gqrx.is_connected():
            lines.append(f"  ✓ Connected to {self.config.gqrx_host}:{self.config.gqrx_port}")
            # Try a frequency read
            f = self.gqrx.get_frequency()
            if f is not None:
                lines.append(f"  ✓ Frequency read: {f/1e6:.4f} MHz")
            else:
                lines.append("  ✗ Could not read frequency from Gqrx")
            m = self.gqrx.get_modulation()
            if m:
                lines.append(f"  ✓ Modulation read: {m}")
            else:
                lines.append("  ✗ Could not read modulation from Gqrx")
        else:
            lines.append(f"  ✗ NOT connected to {self.config.gqrx_host}:{self.config.gqrx_port}")
            lines.append("    → Open Gqrx, then Tools → Remote control settings →")
            lines.append("      check 'Enable remote control', set port 7356.")
        lines.append("")

        # 2. UDP audio
        lines.append("── UDP audio stream (port 7355) ──")
        lines.append("  (Configured in Gqrx: Tools → Audio UDP — NOT in Remote control settings)")
        if self.audio_receiver.is_running():
            cnt = self.audio_receiver.packet_count()
            age = self.audio_receiver.last_packet_age_s()
            if self.audio_receiver.is_streaming(max_age_s=2.0):
                lines.append(f"  ✓ Streaming — {cnt} packets received, last {age:.2f}s ago")
                # Also report audio RMS as a fallback signal-strength indicator
                rms = self.audio_receiver.get_audio_rms_db()
                if rms is not None:
                    lines.append(f"  Audio RMS level: {rms:.1f} dBFS")
                    if rms < -60:
                        lines.append("    ⚠ Audio is very quiet — receiver may be paused in Gqrx,")
                        lines.append("      or tuned to a dead frequency, or antenna disconnected.")
                    elif rms < -30:
                        lines.append("    ✓ Audio is reasonable — you should hear something.")
                    else:
                        lines.append("    ✓ Strong audio — receiver is definitely receiving.")
            elif cnt > 0:
                lines.append(f"  ⚠ Was streaming ({cnt} packets) but stale ({age:.1f}s ago)")
                lines.append("    → Gqrx may have stopped, or receiver paused.")
            else:
                lines.append("  ✗ NOT receiving any audio packets since connect.")
                lines.append("    → In Gqrx:")
                lines.append("      1. Press the green ▶ Play button in the main window")
                lines.append("      2. Tools → Audio UDP → enable, host 127.0.0.1, port 7355, Start")
        else:
            lines.append("  ✗ Audio receiver not running (not connected to Gqrx).")
        lines.append("")

        # 3. UDP spectrum (informational — usually absent in stock Gqrx)
        lines.append("── UDP spectrum stream (port 7357) ──")
        lines.append("  (Stock Gqrx does NOT support this. Magic SDR uses audio FFT as fallback.)")
        if self.spectrum_receiver.is_running():
            cnt = self.spectrum_receiver.packet_count()
            if self.spectrum_receiver.is_streaming(max_age_s=2.0):
                lines.append(f"  ✓ Streaming — {cnt} packets received (rare — likely a patched Gqrx)")
            elif cnt > 0:
                age = self.spectrum_receiver.last_packet_age_s()
                lines.append(f"  ⚠ Was streaming ({cnt} packets) but stale ({age:.1f}s ago)")
            else:
                lines.append("  · No packets received (normal for stock Gqrx).")
                lines.append("    Waterfall will use audio FFT instead — see top of waterfall plot.")
        else:
            lines.append("  · Spectrum receiver not running.")
        lines.append("")

        # 4. Signal level + gain
        if self.gqrx.is_connected():
            lines.append("── Signal level + RF gain ──")
            lvl = self.gqrx.get_signal_level_robust(n_samples=3, interval_s=0.05)
            if lvl is None:
                lines.append("  ✗ Gqrx did not return a signal level.")
                lines.append("    → Most likely cause: Gqrx receiver is PAUSED.")
                lines.append("    → Press the green ▶ Play button in Gqrx's main window.")
            else:
                lines.append(f"  Signal level (l STRENGTH): {lvl:.1f} dBFS")
                if lvl < -90:
                    lines.append("  ⚠ Very low — likely no antenna, gain=0, or receiver paused in Gqrx.")
                elif lvl < -75:
                    lines.append("  ⚠ Below noise floor — weak or no signal at current frequency.")
                elif lvl < -50:
                    lines.append("  ✓ Reasonable — should be detectable by the scanner.")
                else:
                    lines.append("  ✓ Strong signal — scanner should definitely find this.")
            lines.append(f"  RF Gain: {self.config.gain_db:.1f} dB")
            if self.config.gain_db < 1.0:
                lines.append("  ⚠ Gain is 0 — receiver is effectively deaf. Set to 30–49 dB.")
            # Current frequency / band
            f = self.config.last_frequency_hz
            b = band_for_frequency(f)
            lines.append(f"  Current: {f/1e6:.4f} MHz · {self.config.last_modulation} · {b.name if b else 'Custom'}")
            lines.append("")

        # 5. Gqrx config file inspection
        lines.append("── Gqrx config file ──")
        try:
            from .gqrx_config import inspect_gqrx_config
            config_report = inspect_gqrx_config()
            # Indent each line of the report for readability inside the dialog
            for line in config_report.splitlines():
                lines.append(f"  {line}")
        except Exception as e:
            lines.append(f"  (could not read Gqrx config: {e})")
        lines.append("")

        # 6. Recommendations
        lines.append("── What to do ──")
        if not self.gqrx.is_connected():
            lines.append("  ★ Fastest fix: open Settings tab → click '🔧 Setup Gqrx config'")
            lines.append("    (writes a known-good ~/.config/gqrx/default.conf with remote")
            lines.append("    control + audio UDP enabled, backs up your existing config first)")
            lines.append("")
            lines.append("  Then:")
            lines.append("  1. Quit Gqrx completely (File → Quit) if it's running")
            lines.append("  2. Re-launch Gqrx:  gqrx &")
            lines.append("  3. In Gqrx, press the green ▶ Play button")
            lines.append("  4. Back in Magic SDR, click Connect")
        else:
            if lvl is None or (lvl is not None and lvl < -90):
                lines.append("  ★ Press the green ▶ Play button in Gqrx's main window.")
                lines.append("    (This is the #1 cause of 0 stations — Gqrx isn't actively receiving.)")
            if not self.audio_receiver.is_streaming(max_age_s=2.0):
                lines.append("  • No audio UDP flowing. Either:")
                lines.append("    (a) Settings tab → '🔧 Setup Gqrx config' (auto-writes the config),")
                lines.append("        then quit Gqrx, re-launch, and press ▶ Play")
                lines.append("    (b) Manually: Tools → Audio UDP → enable, host 127.0.0.1, port 7355, Start")
            if self.config.gain_db < 1.0:
                lines.append("  • Increase RF Gain to ~40 dB (it was 0)")
            if lvl is not None and lvl < -90:
                lines.append("  • Check antenna is plugged into the RTL-SDR SMA connector")
            if (self.audio_receiver.is_streaming(max_age_s=2.0)
                    and lvl is not None and lvl > -75):
                lines.append("  ✓ Everything looks healthy — try scanning again.")
                lines.append("    (Scanner doesn't need UDP streams — only needs TCP control + signal.)")
        lines.append("")
        lines.append("See QUICKSTART.md for step-by-step Gqrx setup.")
        return "\n".join(lines)

    def _setup_gqrx_config(self) -> None:
        """Write a known-good Gqrx config (called from Settings tab + connect-error dialog)."""
        from .gqrx_config import setup_gqrx_config
        result = setup_gqrx_config()
        if not result.ok:
            QMessageBox.critical(
                self, "Gqrx setup failed",
                result.message
            )
            return
        # Show the result and the next-step instructions
        msg_type = QMessageBox.Information if result.changes else QMessageBox.Information
        QMessageBox.information(
            self, "Gqrx config written" if result.changes else "Gqrx config OK",
            result.message
        )
        # If we made changes, the user needs to re-launch Gqrx — offer to retry the connection
        if result.changes and not self.gqrx.is_connected():
            self.status.showMessage(
                "Gqrx config written. Quit & re-launch Gqrx, then click Connect.",
                8000
            )

    def _inspect_gqrx_config(self) -> None:
        """Show what's currently in Gqrx's config file (read-only)."""
        from .gqrx_config import inspect_gqrx_config
        report = inspect_gqrx_config()
        QMessageBox.information(self, "Gqrx config inspection", report)

    def _on_freq_changed(self, freq_hz: int) -> None:
        self.dial.set_frequency(freq_hz)
        self.waterfall.set_tune_marker(freq_hz)
        self._update_band_context(freq_hz)
        self.config.last_frequency_hz = freq_hz
        self.bookmarks.update_last_heard(freq_hz)
        self.config.save()

    def _on_mod_changed(self, mod: str) -> None:
        self.mod_combo.blockSignals(True)
        self.mod_combo.setCurrentText(mod)
        self.mod_combo.blockSignals(False)
        self.config.last_modulation = mod

    def _on_signal_level(self, lvl: float) -> None:
        self.signal_bar.setValue(int(lvl))
        # Drive the analog S-meter too
        self.s_meter.set_level(lvl)
        # Also feed the recording manager
        if self.recordings.is_recording:
            # will be picked up on next audio chunk
            pass

    def _on_audio_chunk(self, chunk: np.ndarray, sample_rate: int, channels: int) -> None:
        # Apply EQ (no-op if disabled or flat — preserves CPU)
        processed = self.equalizer.process(chunk, sample_rate=sample_rate)
        # Push to audio visualizer (always — it's a passive consumer)
        self.audio_visualizer.push_audio(processed, sample_rate, channels)
        # Push to time-travel buffer (always records the last 30 s of audio)
        self.time_travel_buffer.push(processed)
        # Push to CW decoder (only if enabled — saves CPU when not needed)
        if self.cw_decoder.enabled:
            self.cw_decoder.process_audio(processed, sample_rate)
        # Playback — live mode only. In time-travel replay mode, we feed
        # the player from the buffer at the chosen offset (handled separately
        # by the replay pump, which we'd need a separate timer for).
        if not self._time_travel_replaying:
            self.audio_player.push(processed)
        # Recording (uses processed audio so the EQ affects recordings too)
        if self.recordings.is_recording:
            lvl = self.gqrx.get_signal_level() or -120.0
            self.recordings.write_chunk(processed, sample_rate, channels, signal_level_db=lvl)
        # Audio-FFT spectrum fallback — only feed if no UDP spectrum is arriving
        # (avoid wasting CPU computing FFTs when we have real RF spectrum data)
        if not self.spectrum_receiver.is_streaming(max_age_s=1.0):
            self.audio_spectrum.process_audio(processed, sample_rate, channels)
        # RDS decoder — best-effort, only fires when tuned to an FM band
        # (the decoder itself checks for 19 kHz pilot feasibility based on
        # sample rate)
        try:
            self.rds_decoder.process_audio(processed, sample_rate)
        except Exception as e:
            log.debug("RDS decoder error: %s", e)

    # ----------------------------- tuning knob -----------------------------
    def _on_knob_step(self, step_hz: int) -> None:
        """Called when the tuning knob is dragged/wheeled by one step."""
        if not self.gqrx.is_connected():
            return
        new_freq = self.config.last_frequency_hz + step_hz
        # Clamp to a reasonable range (100 kHz to 2 GHz)
        new_freq = max(100_000, min(2_000_000_000, new_freq))
        self._tune_to(new_freq)

    def _on_knob_step_changed(self, step_hz: int) -> None:
        """Called when the user right-clicks the knob to cycle step size."""
        if step_hz >= 1_000_000:
            txt = f"Tuning step: {step_hz // 1_000_000} MHz"
        elif step_hz >= 1_000:
            txt = f"Tuning step: {step_hz // 1_000} kHz"
        else:
            txt = f"Tuning step: {step_hz} Hz"
        self.status.showMessage(txt, 2000)

    # ----------------------------- EQ handlers -----------------------------
    def _on_eq_slider_changed(self, band_idx: int, value: int) -> None:
        """Called when an EQ slider is moved."""
        self.equalizer.set_band_gain(band_idx, float(value))
        # Update the per-band gain label
        lbl = self.findChild(QLabel, f"eq_gain_lbl_{band_idx}")
        if lbl:
            sign = "+" if value >= 0 else ""
            lbl.setText(f"{sign}{value}")
        # Update the slider's tooltip
        if 0 <= band_idx < len(self.eq_sliders):
            freq = EQ_BANDS_HZ[band_idx]
            self.eq_sliders[band_idx].setToolTip(
                f"{freq} Hz: {value:+d} dB"
            )
        # Mark the preset as "Custom" since user has manually adjusted
        self.eq_preset_combo.blockSignals(True)
        self.eq_preset_combo.setCurrentText("Custom")
        self.eq_preset_combo.blockSignals(False)
        # Find closest preset and show in status bar (silent hint)
        current_gains = [float(s.value()) for s in self.eq_sliders]
        closest = find_closest_preset(current_gains)
        if closest != "Custom":
            self.status.showMessage(f"EQ near '{closest}' preset", 1500)
        # Persist
        self._save_magic_state()

    def _on_eq_enabled_toggled(self, enabled: bool) -> None:
        self.equalizer.set_enabled(enabled)
        self._save_magic_state()

    def _on_eq_preset_selected(self, preset_name: str) -> None:
        """Called when the user picks a preset from the dropdown."""
        if preset_name == "Custom":
            return  # user picked "Custom" — leave sliders alone
        if preset_name not in EQ_PRESETS:
            return
        gains = EQ_PRESETS[preset_name]
        # Update all sliders + EQ gains
        for i, gain in enumerate(gains):
            self.eq_sliders[i].blockSignals(True)
            self.eq_sliders[i].setValue(int(gain))
            self.eq_sliders[i].blockSignals(False)
            self.equalizer.set_band_gain(i, float(gain))
            lbl = self.findChild(QLabel, f"eq_gain_lbl_{i}")
            if lbl:
                sign = "+" if gain >= 0 else ""
                lbl.setText(f"{sign}{int(gain)}")
        self.status.showMessage(f"EQ preset: {preset_name}", 2000)
        self._save_magic_state()

    def _on_eq_reset(self) -> None:
        """Reset all EQ bands to 0 dB (selects 'Flat' preset)."""
        for sld in self.eq_sliders:
            sld.setValue(0)
        self.equalizer.reset()
        self.eq_preset_combo.blockSignals(True)
        self.eq_preset_combo.setCurrentText("Flat")
        self.eq_preset_combo.blockSignals(False)
        self._save_magic_state()

    # ----------------------------- visualizer -----------------------------
    def _on_viz_mode_changed(self, mode: str) -> None:
        self.status.showMessage(f"Visualizer: {mode} (right-click visualizer to cycle)", 2000)
        self._save_magic_state()

    # ----------------------------- compact mode / view toggles -----------------------------
    def _apply_compact_visibility(self, on: bool) -> None:
        """Apply or release compact-mode panel visibility (no side effects)."""
        if on:
            self.viz_panel.setVisible(False)
            self.toggle_viz_action.blockSignals(True)
            self.toggle_viz_action.setChecked(False)
            self.toggle_viz_action.blockSignals(False)
            self.time_travel_widget.setVisible(False)
            self.toggle_timetravel_action.blockSignals(True)
            self.toggle_timetravel_action.setChecked(False)
            self.toggle_timetravel_action.blockSignals(False)
            self.eq_sliders_container.setVisible(False)
            self.toggle_eq_action.blockSignals(True)
            self.toggle_eq_action.setChecked(False)
            self.toggle_eq_action.blockSignals(False)
            for btn in self.memory_bar.buttons:
                btn.setMinimumHeight(30)
                btn.setMinimumWidth(60)
        else:
            self.viz_panel.setVisible(True)
            self.toggle_viz_action.blockSignals(True)
            self.toggle_viz_action.setChecked(True)
            self.toggle_viz_action.blockSignals(False)
            self.time_travel_widget.setVisible(True)
            self.toggle_timetravel_action.blockSignals(True)
            self.toggle_timetravel_action.setChecked(True)
            self.toggle_timetravel_action.blockSignals(False)
            self.eq_sliders_container.setVisible(True)
            self.toggle_eq_action.blockSignals(True)
            self.toggle_eq_action.setChecked(True)
            self.toggle_eq_action.blockSignals(False)
            for btn in self.memory_bar.buttons:
                btn.setMinimumHeight(40)
                btn.setMinimumWidth(72)

    def _on_compact_mode_toggled(self, on: bool) -> None:
        """Toggle Compact Mode (menu action handler).

        Compact Mode hides the visualizer, Time-Travel bar, and EQ sliders,
        and shrinks the memory preset buttons — giving maximum space to the
        waterfall and the receiver controls.
        """
        self.config.compact_mode = on
        self._apply_compact_visibility(on)
        if on:
            self.status.showMessage("Compact Mode on — visualizer, time-travel, EQ sliders hidden. Ctrl+M to exit.", 3500)
        else:
            self.status.showMessage("Compact Mode off — all panels restored.", 2500)
        self._save_magic_state()

    def _on_toggle_visualizer(self, on: bool) -> None:
        self.viz_panel.setVisible(on)
        self.status.showMessage(f"Visualizer {'shown' if on else 'hidden'}.", 1500)

    def _on_toggle_eq_sliders(self, on: bool) -> None:
        self.eq_sliders_container.setVisible(on)
        self.status.showMessage(f"EQ sliders {'shown' if on else 'hidden'}.", 1500)

    def _on_toggle_time_travel(self, on: bool) -> None:
        self.time_travel_widget.setVisible(on)
        self.status.showMessage(f"Time-Travel {'shown' if on else 'hidden'}.", 1500)

    def _on_reset_layout(self) -> None:
        """Restore all panels to their default visibility and exit Compact Mode."""
        if self.compact_action.isChecked():
            self.compact_action.setChecked(False)  # triggers _on_compact_mode_toggled
        else:
            # Already off — just make sure everything is visible
            self._apply_compact_visibility(False)
        self.status.showMessage("Window layout reset to defaults.", 2000)

    # ----------------------------- memory presets -----------------------------
    def _on_memory_tune(self, freq_hz: int, modulation: str) -> None:
        """User clicked a memory preset — tune to it."""
        if modulation:
            self.mod_combo.setCurrentText(modulation)
        self._tune_to(freq_hz)
        self.status.showMessage(f"Memory preset: {freq_hz/1e6:.4f} MHz", 2000)

    def _make_memory_preset_from_current(self):
        """Callback for the memory bar — returns a preset for the current station."""
        freq = self.config.last_frequency_hz
        mod = self.config.last_modulation
        # Try to find a label for this frequency
        label = lookup_known(freq) or ""
        if not label:
            b = self.bookmarks.get(freq)
            if b:
                label = b.label
        if not label:
            band = band_for_frequency(freq)
            label = f"{freq/1e6:.4f} MHz" + (f" ({band.name})" if band else "")
        import time
        preset = MemoryPreset(
            freq_hz=freq,
            modulation=mod,
            label=label,
            stored_at=time.time(),
        )
        self.status.showMessage(f"Stored → M?  ({freq/1e6:.4f} MHz, {label})", 2500)
        # Persist after a beat
        QTimer.singleShot(100, self._save_magic_state)
        return preset

    # ----------------------------- time-travel -----------------------------
    def _on_time_travel_mode_changed(self, is_live: bool) -> None:
        """User moved the time-travel slider between LIVE and REPLAY."""
        self._time_travel_replaying = not is_live
        if not is_live:
            self.status.showMessage("⏮ Time-travel REPLAY mode — drag slider right to return to live", 3000)
        else:
            self.status.showMessage("▶ Live audio resumed", 2000)

    # ----------------------------- CW decoder -----------------------------
    def _on_cw_enabled_toggled(self, enabled: bool) -> None:
        self.cw_decoder.enabled = enabled
        self.config.cw_decoder_enabled = enabled
        self.config.save()
        if not enabled:
            self.cw_wpm_label.setText("WPM: —")
            self.cw_element_label.setText("Current: —")

    def _refresh_cw_panel(self) -> None:
        """Periodic refresh of the CW decoder text display."""
        if not self.cw_decoder.enabled:
            return
        # WPM
        wpm = self.cw_decoder.wpm
        if wpm > 0:
            self.cw_wpm_label.setText(f"WPM: {wpm:.0f}")
        # Current element
        elem = self.cw_decoder._current_morse  # type: ignore[attr-defined]
        if elem:
            self.cw_element_label.setText(f"Current: {elem}")
        else:
            self.cw_element_label.setText("Current: —")
        # Decoded text (only update if changed to avoid scrolling flicker)
        text = self.cw_decoder.decoded_text
        if text != self.cw_text_display.toPlainText():
            self.cw_text_display.setPlainText(text)
            # Scroll to bottom
            cursor = self.cw_text_display.textCursor()
            cursor.movePosition(cursor.End)
            self.cw_text_display.setTextCursor(cursor)

    # ----------------------------- DX cluster -----------------------------
    def _dx_auto_connect(self) -> None:
        """Called via QTimer to auto-connect to the cluster on launch."""
        if self.config.dx_cluster_enabled and not self.dx_cluster.is_connected:
            self.dx_connect_btn.setChecked(True)
            self._on_dx_connect_toggled()

    def _on_dx_connect_toggled(self) -> None:
        if self.dx_connect_btn.isChecked():
            self.dx_status_label.setText("Connecting…")
            self.dx_cluster.connection_changed.connect(self._on_dx_connection_changed)
            self.dx_cluster.spot_received.connect(lambda s: self._refresh_dx_cluster_list())
            self.dx_cluster.start()
            self.dx_refresh_timer.start()
            self.config.dx_cluster_enabled = True
        else:
            self.dx_cluster.stop()
            self.dx_refresh_timer.stop()
            self.dx_status_label.setText("Disconnected")
            self.dx_status_label.setStyleSheet("color: #8b96a7; font-family: monospace;")
            self.config.dx_cluster_enabled = False
        self.config.save()

    def _on_dx_connection_changed(self, connected: bool, message: str) -> None:
        if connected:
            self.dx_status_label.setText(f"✓ {message}")
            self.dx_status_label.setStyleSheet("color: #5cffaa; font-family: monospace;")
        else:
            self.dx_status_label.setText(f"✗ {message}")
            self.dx_status_label.setStyleSheet("color: #ff8a8a; font-family: monospace;")

    def _refresh_dx_cluster_list(self) -> None:
        """Re-render the DX cluster spot list, applying the current filter."""
        filter_text = self.dx_filter_edit.text().strip().upper() if hasattr(self, "dx_filter_edit") else ""
        spots = self.dx_cluster.get_recent_spots(n=100)
        # Apply filter
        if filter_text:
            filtered = []
            for s in spots:
                if (filter_text in s.dx_callsign.upper()
                        or filter_text in s.spotter.upper()
                        or filter_text in s.comment.upper()
                        or filter_text in f"{s.freq_mhz:.3f}"):
                    filtered.append(s)
            spots = filtered
        # Limit to 80 to keep the list fast
        spots = spots[:80]
        # Rebuild list (without losing scroll position)
        self.dx_list.clear()
        for s in spots:
            text = s.format()
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, s.freq_hz)
            # Color-code by age: green for fresh, yellow for old, gray for stale
            age_s = time.time() - s.received_at
            if age_s < 60:
                color = QColor("#5cffaa")
            elif age_s < 300:
                color = QColor("#ffd45c")
            else:
                color = QColor("#8b96a7")
            item.setForeground(color)
            self.dx_list.addItem(item)

    def _on_dx_spot_activated(self, item) -> None:
        freq = item.data(Qt.UserRole)
        if freq:
            self._tune_to(int(freq))
            self.status.showMessage(f"Tuned to DX spot: {freq/1e6:.3f} kHz", 3000)

    # ----------------------------- auto-surf -----------------------------
    def _on_auto_surf_clicked(self) -> None:
        if not self.gqrx.is_connected():
            self.auto_surf_btn.setChecked(False)
            QMessageBox.warning(self, "Not connected",
                                "Connect to Gqrx first, then click ✨ Auto-Surf.")
            return
        if self.auto_surfer.is_running:
            self.auto_surfer.stop()
            self.auto_surf_btn.setText("✨ Auto-Surf")
            self.auto_surf_btn.setChecked(False)
            self.status.showMessage("Auto-Surf stopped", 2000)
        else:
            self.auto_surfer.stop_started.connect(self._on_auto_surf_stop_started)
            self.auto_surfer.surf_progress.connect(self._on_auto_surf_progress)
            self.auto_surfer.surf_finished.connect(self._on_auto_surf_finished)
            self.auto_surfer.surf_error.connect(self._on_auto_surf_error)
            self.auto_surf_btn.setText("■ Stop Auto-Surf")
            self.auto_surf_btn.setChecked(True)
            self.status.showMessage("✨ Auto-Surf starting — surfing all bands…", 3000)
            self.auto_surfer.start(dwell_seconds=5.0)

    def _on_auto_surf_stop_started(self, band_name: str, freq_hz: int, label: str) -> None:
        self.status.showMessage(
            f"✨ Auto-Surf: {band_name} → {freq_hz/1e6:.4f} MHz — {label}",
            5000
        )

    def _on_auto_surf_progress(self, band_idx: int, total: int) -> None:
        self.status.showMessage(
            f"✨ Auto-Surf: band {band_idx + 1}/{total}…",
            3000
        )

    def _on_auto_surf_finished(self, stops: list) -> None:
        self.auto_surf_btn.setText("✨ Auto-Surf")
        self.auto_surf_btn.setChecked(False)
        summary_lines = [f"✨ Auto-Surf complete — visited {len(stops)} bands:"]
        for s in stops:
            summary_lines.append(f"  • {s.band_name}: {s.freq_hz/1e6:.4f} MHz @ {s.level_db:.0f} dBFS — {s.label or 'Unknown'}")
        QMessageBox.information(self, "Auto-Surf Complete", "\n".join(summary_lines))

    def _on_auto_surf_error(self, err: str) -> None:
        self.auto_surf_btn.setText("✨ Auto-Surf")
        self.auto_surf_btn.setChecked(False)
        self.status.showMessage(f"Auto-Surf error: {err}", 5000)

    # ----------------------------- aurora -----------------------------
    def _update_aurora_panel(self) -> None:
        """Refresh the aurora forecast from the cached solar data."""
        cond = self.solar_fetcher.get_current()
        if cond is None:
            self.aurora_summary_label.setText("Waiting for solar data…")
            return
        aurora = forecast_aurora(cond.k_index, self.config.observer_latitude)
        self.aurora_summary_label.setText(aurora.summary())
        self.aurora_detail_labels["storm_class"].setText(aurora.storm_class)
        if aurora.oval_latitude is not None:
            self.aurora_detail_labels["oval_lat"].setText(f"~{aurora.oval_latitude:.0f}° magnetic latitude")
        else:
            self.aurora_detail_labels["oval_lat"].setText("—")
        if aurora.visible_from_observer:
            self.aurora_detail_labels["visible"].setText("✓ YES — get outside and look up!")
            self.aurora_detail_labels["visible"].setStyleSheet("color: #b380ff; font-family: monospace; font-weight: 700;")
        else:
            self.aurora_detail_labels["visible"].setText(f"✗ No (need Kp ≥ {int((67 - self.config.observer_latitude) / 2) + 1} from your latitude)")
            self.aurora_detail_labels["visible"].setStyleSheet("color: #8b96a7; font-family: monospace;")
        self.aurora_detail_labels["hf_abs"].setText(aurora.hf_absorption)
        self.aurora_detail_labels["vhf_scatter"].setText(aurora.vhf_scatter)

    # ----------------------------- night vision -----------------------------
    def _on_night_vision_toggled(self, enabled: bool) -> None:
        self.config.night_vision = enabled
        self.config.save()

    def _apply_night_vision_now(self) -> None:
        """Re-apply the application stylesheet based on night-vision setting."""
        from PyQt5.QtWidgets import QApplication
        from .main import DARK_STYLE, NIGHT_VISION_STYLE
        app = QApplication.instance()
        if app:
            if self.config.night_vision:
                app.setStyleSheet(NIGHT_VISION_STYLE)
            else:
                app.setStyleSheet(DARK_STYLE)
            self.status.showMessage(
                "Applied " + ("night vision" if self.config.night_vision else "day") + " theme",
                2000
            )

    # ----------------------------- conditions / RDS update -----------------------------
    def _update_conditions(self) -> None:
        """Periodic refresh of solar + band conditions panels."""
        cond = self.solar_fetcher.get_current()
        if cond is None:
            self.solar_summary_label.setText("No data yet (waiting for NOAA SWPC fetch)…")
            self.solar_status_lbl.setText(self.solar_fetcher.last_error or "Fetching…")
        else:
            self.solar_summary_label.setText(cond.summary())
            self.solar_detail_labels["sfi"].setText(
                f"{cond.solar_flux:.0f} sfu" if cond.solar_flux is not None else "—"
            )
            self.solar_detail_labels["ssn"].setText(
                str(cond.sunspot_number) if cond.sunspot_number is not None else "—"
            )
            self.solar_detail_labels["aindex"].setText(
                f"{cond.a_index:.0f}" if cond.a_index is not None else "—"
            )
            self.solar_detail_labels["kindex"].setText(
                f"{cond.k_index}  ({'storm' if cond.is_storm else 'quiet' if cond.is_quiet else 'active'})"
                if cond.k_index is not None else "—"
            )
            self.solar_detail_labels["xray"].setText(
                cond.xray_class or "—"
            )
            self.solar_detail_labels["xray_flux"].setText(
                f"{cond.xray_flux:.2e} W/m²" if cond.xray_flux is not None else "—"
            )
            from datetime import datetime
            self.solar_detail_labels["updated"].setText(
                datetime.fromtimestamp(cond.timestamp).strftime("%Y-%m-%d %H:%M UTC")
            )
            self.solar_detail_labels["message"].setText(cond.message or "—")
            # Status
            age_s = time.time() - self.solar_fetcher.last_fetch_time
            if self.solar_fetcher.last_error:
                self.solar_status_lbl.setText(
                    f"Last error: {self.solar_fetcher.last_error} (last OK {age_s:.0f}s ago)"
                )
            else:
                self.solar_status_lbl.setText(f"Last updated {age_s:.0f}s ago")

            # Update band conditions
            bands = estimate_band_conditions(cond)
            lines = []
            for bc in bands:
                stars = rating_to_stars(bc.rating)
                color = band_color(bc.rating)
                line = (
                    f"<span style='color:{color};'>{stars}</span> "
                    f"<b>{bc.band}</b> ({bc.freq_mhz:.1f} MHz) — "
                    f"<span style='color:{color};'>{bc.label}</span> — "
                    f"<span style='color:#888;'>{bc.note}</span>"
                )
                lines.append(line)
            self.band_conditions_label.setText("<br>".join(lines))

    def _update_rds_panel(self) -> None:
        """Periodic refresh of the RDS info panel."""
        info = self.rds_decoder.info
        if info.stereo_pilot_detected:
            self.rds_labels["pilot"].setText("✓ Detected (stereo broadcast)")
            self.rds_labels["pilot"].setStyleSheet("color: #3aaa55; font-family: monospace;")
        else:
            self.rds_labels["pilot"].setText("✗ Not detected")
            self.rds_labels["pilot"].setStyleSheet("color: #888; font-family: monospace;")
        if info.pilot_strength_db is not None:
            self.rds_labels["pilot_str"].setText(f"{info.pilot_strength_db:+.1f} dB above noise")
        else:
            self.rds_labels["pilot_str"].setText("—")
        # PS, PTY, PI, RT — would come from full RDS decoding, which needs MPX audio
        self.rds_labels["ps"].setText(info.ps or "— (requires MPX audio)")
        self.rds_labels["pty"].setText(
            info.pty_label or "— (requires MPX audio)"
        )
        self.rds_labels["pi"].setText(
            f"0x{info.pi:04X}" if info.pi is not None else "— (requires MPX audio)"
        )
        self.rds_labels["rt"].setText(info.rt or "— (requires MPX audio)")

    def _tune_to(self, freq_hz: int) -> None:
        if not self.gqrx.is_connected():
            self.status.showMessage("Not connected to Gqrx", 2000)
            return
        b = band_for_frequency(freq_hz)
        mod = b.modulation if b else "FM"
        if self.mod_combo.currentText() != mod:
            self.gqrx.set_modulation(mod)
        self.gqrx.set_frequency(freq_hz)
        # Reset RDS decoder on tune (different station → different RDS data)
        self.rds_decoder.reset()
        # Reset CW decoder on tune (different station → different Morse)
        if self.cw_decoder.enabled:
            self.cw_decoder.reset()
        # Reset time-travel buffer on tune (different station → different audio)
        self.time_travel_buffer.reset()
        # Force time-travel back to live mode if it was replaying
        if self._time_travel_replaying:
            self.time_travel_widget.go_live()
            self._time_travel_replaying = False

    def _on_modulation_changed(self, mod: str) -> None:
        if self.gqrx.is_connected():
            self.gqrx.set_modulation(mod)
        self.config.last_modulation = mod

    def _on_gain_changed(self, gain: float) -> None:
        if self.gqrx.is_connected():
            self.gqrx.set_rf_gain(gain)
        self.config.gain_db = gain

    def _on_squelch_changed(self, sql: float) -> None:
        if self.gqrx.is_connected():
            self.gqrx.set_squelch(sql)

    def _on_volume_changed(self, v: int) -> None:
        vol = v / 100.0
        self.audio_player.set_volume(vol)
        self.config.volume = vol

    def _on_mute_toggled(self, muted: bool) -> None:
        self.audio_player.set_muted(muted)
        self.mute_btn.setText("🔇" if muted else "🔊")

    def _on_record_toggled(self, on: bool) -> None:
        if on:
            freq = self.config.last_frequency_hz
            mod = self.config.last_modulation
            label = lookup_known(freq) or self.bookmarks.get(freq).label if self.bookmarks.get(freq) else None
            ok = self.recordings.start_recording(freq, mod, label=label)
            if not ok:
                self.rec_btn.setChecked(False)
        else:
            self.recordings.stop_recording()

    def _on_recording_started(self, r) -> None:
        self.rec_btn.setText("■ Stop")
        self.rec_status.setText("● Recording…")
        self.status.showMessage(f"Recording → {r.wav_path}", 3000)

    def _on_recording_stopped(self, r, path: str) -> None:
        self.rec_btn.setText("● Record")
        self.rec_btn.setChecked(False)
        self.rec_status.setText(f"Saved: {path.split('/')[-1]}")
        self.status.showMessage(f"Recording saved: {path}", 5000)
        self._refresh_recordings()

    # ----------------------------- web server -----------------------------
    def _on_web_toggled(self) -> None:
        if self.web_btn.isChecked():
            self._start_web_server()
        else:
            self._stop_web_server()

    def _start_web_server(self) -> None:
        if self.web_server is not None:
            return
        host = "0.0.0.0" if self.config.allow_remote_connections else "127.0.0.1"
        self.web_server = WebServer(
            app_factory_args=dict(
                gqrx=self.gqrx,
                bookmarks=self.bookmarks,
                recordings=self.recordings,
                scanner=self.scanner,
                ai_tagger=self.ai_tagger,
                audio_receiver=self.audio_receiver,
                spectrum_receiver=self.spectrum_receiver,
                get_state_fn=self._get_state,
            ),
            host=host,
            port=self.config.web_port,
        )
        if self.web_server.start():
            self.web_btn.setText("Stop Web Server")
            self.web_url.setText(f"http://{host}:{self.config.web_port}")
            self.status.showMessage(f"Remote access: http://{host}:{self.config.web_port}", 5000)
        else:
            self.web_server = None
            self.web_btn.setChecked(False)

    def _stop_web_server(self) -> None:
        if self.web_server:
            self.web_server.stop()
            self.web_server = None
        self.web_btn.setText("Start Web Server")

    def _get_state(self) -> dict:
        label = None
        b = self.bookmarks.get(self.config.last_frequency_hz)
        if b:
            label = b.label
        else:
            label = lookup_known(self.config.last_frequency_hz)
        band = band_for_frequency(self.config.last_frequency_hz)
        return {
            "freq_hz": self.config.last_frequency_hz,
            "modulation": self.config.last_modulation,
            "signal_level_db": self.signal_bar.value(),
            "is_recording": self.recordings.is_recording,
            "gqrx_connected": self.gqrx.is_connected(),
            "label": label,
            "band": band.name if band else "Custom",
            "volume": self.audio_player.get_volume(),
            "muted": self.audio_player.is_muted(),
        }

    # ----------------------------- waterfall context -----------------------------
    def _update_band_context(self, freq_hz: int) -> None:
        # Set spectrum span based on band
        b = band_for_frequency(freq_hz)
        if b:
            span = min(int((b.end_mhz - b.start_mhz) * 1e6), 2_400_000)
            span = max(span, 200_000)
        else:
            span = 2_000_000
        self.waterfall.set_band_context(freq_hz, span)
        self.spectrum_receiver.set_band_context(freq_hz, span)
        # Audio FFT only needs center freq; its span is sample_rate/2
        self.audio_spectrum.set_band_context(freq_hz, span)

    # ----------------------------- bookmarks -----------------------------
    def _refresh_bookmarks(self, query: str = "") -> None:
        self.bookmark_list.clear()
        items = self.bookmarks.search(query) if query else self.bookmarks.list_all()
        for b in items:
            text = f"{b.freq_hz/1e6:.4f} MHz · {b.label} [{b.band}]"
            if b.ai_tag:
                text += f"  AI: {b.ai_tag}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, b.freq_hz)
            self.bookmark_list.addItem(item)

    def _on_bookmark_search(self, text: str) -> None:
        self._refresh_bookmarks(text)

    def _on_bookmark_activated(self, item) -> None:
        freq = item.data(Qt.UserRole)
        self._tune_to(int(freq))

    def _on_add_bookmark(self) -> None:
        try:
            mhz = float(self.bm_add_freq.text())
        except ValueError:
            self.status.showMessage("Invalid frequency", 2000)
            return
        label = self.bm_add_label.text() or None
        b = self.bookmarks.add(freq_hz=int(mhz * 1e6), label=label)
        self.bm_add_freq.clear()
        self.bm_add_label.clear()
        self._refresh_bookmarks()
        self.status.showMessage(f"Added bookmark: {b.label}", 2000)

    # ----------------------------- scanner -----------------------------
    def _on_scan_start(self) -> None:
        band_name = self.scan_band_combo.currentText()
        # Clear test-sweep results when starting a real scan
        self.test_sweep_list.clear()
        if band_name == "ALL BANDS":
            self.scanner.scan_all_bands()
        else:
            self.scanner.scan_band_by_name(band_name)

    def _on_test_sweep(self) -> None:
        """Diagnostic sweep: samples every frequency and shows ALL levels,
        regardless of threshold. Use this to figure out why 'Scan' returns 0.
        """
        if not self.gqrx.is_connected():
            QMessageBox.warning(self, "Not connected",
                                "Connect to Gqrx first, then run Test Sweep.")
            return
        if self.scanner.is_running():
            self.scanner.stop()
            return
        band_name = self.scan_band_combo.currentText()
        if band_name == "ALL BANDS":
            band_name = "FM Broadcast"  # test sweep is single-band only
        band = BANDS_BY_NAME.get(band_name)
        if not band:
            return
        # Run in a thread so the UI stays responsive
        import threading
        self.test_sweep_list.clear()
        self.scan_status.setText(f"Test sweeping {band.name}…")
        self.scan_progress.setValue(0)

        def sweep():
            start_hz = int(band.start_mhz * 1e6)
            end_hz = int(band.end_mhz * 1e6)
            step_hz = int(band.step_khz * 1e3)
            n_steps = max(1, (end_hz - start_hz) // step_hz + 1)
            # Pause the background poller so its commands don't interleave with ours.
            self.gqrx.pause_poller()
            self.gqrx.set_modulation(band.modulation)
            time.sleep(0.1)
            results = []
            try:
                for i, f in enumerate(range(start_hz, end_hz + 1, step_hz)):
                    if self.scanner._stop.is_set():
                        break
                    self.gqrx.set_frequency(f)
                    time.sleep(self.scanner.dwell_s)
                    lvl = self.gqrx.get_signal_level_robust(n_samples=3, interval_s=0.04)
                    if lvl is None:
                        continue
                    results.append((f, lvl))
                    # Sort results and show top 50 strongest
                    results.sort(key=lambda x: x[1], reverse=True)
                    top = results[:50]
                    QMetaObject.invokeMethod(self, "_refresh_test_sweep",
                                              Qt.QueuedConnection,
                                              Q_ARG(list, top))
                    self.scan_progress.setValue(int((i + 1) / n_steps * 100))
            finally:
                self.gqrx.resume_poller()
            self.scan_status.setText(
                f"Test sweep done — {len(results)} freqs sampled. "
                f"Strongest: {results[0][0]/1e6:.4f} MHz @ {results[0][1]:.1f} dBFS"
                if results else "Test sweep done — no signal sampled (check antenna + gain)"
            )

        threading.Thread(target=sweep, daemon=True, name="TestSweep").start()

    # Slot invoked from the sweep thread to update the test-sweep list
    @pyqtSlot(list)
    def _refresh_test_sweep(self, top_results: list) -> None:
        self.test_sweep_list.clear()
        for f, lvl in top_results:
            # Color-code: green for strong, yellow for medium, dim for weak
            if lvl > -50:
                color = "#5cffaa"
                tag = "STRONG"
            elif lvl > -65:
                color = "#ffd45c"
                tag = "medium"
            elif lvl > -80:
                color = "#8b96a7"
                tag = "weak"
            else:
                color = "#4a5266"
                tag = "noise"
            item = QListWidgetItem(f"{f/1e6:.4f} MHz · {lvl:.1f} dBFS · {tag}")
            item.setForeground(QColor(color))
            item.setData(Qt.UserRole, f)
            self.test_sweep_list.addItem(item)

    def _on_scan_started(self, band_name: str) -> None:
        self.scan_status.setText(f"Scanning {band_name}…")
        self.discovered_list.clear()
        self.scan_progress.setValue(0)

    def _on_scan_progress(self, p: float) -> None:
        self.scan_progress.setValue(int(p * 100))

    def _on_scan_progress_level(self, freq_hz: int, level_db: float) -> None:
        """Live update of what the scanner is currently hearing."""
        self.scan_live_label.setText(
            f"Live: {freq_hz/1e6:.4f} MHz → {level_db:.1f} dBFS  "
            f"(threshold: {self.scanner.threshold_db:.0f} dBFS)"
        )

    def _on_noise_floor_calibrated(self, nf: float) -> None:
        self.scan_live_label.setText(
            f"Noise floor calibrated: {nf:.1f} dBFS → threshold = {nf + self.scanner.auto_threshold_margin_db:.1f} dBFS"
        )
        self.status.showMessage(
            f"Noise floor: {nf:.1f} dBFS; threshold set to {nf + self.scanner.auto_threshold_margin_db:.1f} dBFS",
            4000
        )

    def _on_station_found(self, st: DiscoveredStation) -> None:
        text = f"{st.freq_hz/1e6:.4f} MHz · {st.level_db:.1f} dB · {st.label or 'Unknown'}"
        if st.ai_tag:
            text += f"  [AI: {st.ai_tag.get('signal_type')}]"
        item = QListWidgetItem(text)
        item.setData(Qt.UserRole, st.freq_hz)
        self.discovered_list.addItem(item)
        # Auto-add to bookmarks if not present
        if not self.bookmarks.get(st.freq_hz):
            self.bookmarks.add(freq_hz=st.freq_hz, label=st.label or f"{st.freq_hz/1e6:.4f} MHz",
                                modulation=st.modulation, ai_tag=st.ai_tag.get("summary") if st.ai_tag else None)
        self._refresh_bookmarks()

    def _on_scan_finished(self, band_name: str, found: list) -> None:
        nf_str = ""
        if self.scanner.measured_noise_floor is not None:
            nf_str = f" (noise floor: {self.scanner.measured_noise_floor:.1f} dBFS)"
        self.scan_status.setText(
            f"Done — {len(found)} stations in {band_name}{nf_str}"
        )
        self.scan_progress.setValue(100)
        if len(found) == 0:
            # Check if audio is flowing (the best indicator that Gqrx is
            # actually receiving). If not, the cause is upstream (Gqrx
            # paused or Audio UDP not enabled), not the scanner threshold.
            audio_ok = self.audio_receiver.is_streaming(max_age_s=3.0)
            if not audio_ok:
                self.scan_live_label.setText(
                    "0 stations found — Gqrx isn't streaming audio to Magic SDR. "
                    "Press ▶ in Gqrx + enable Tools → Audio UDP (port 7355). "
                    "Click 🩺 Diagnose for the full report."
                )
                self.scan_live_label.setStyleSheet(
                    "color: #ff8a8a; font-family: monospace; padding: 2px;"
                )
            else:
                self.scan_live_label.setText(
                    "0 stations found — audio is flowing but no signal exceeds threshold. "
                    "Try 🔬 Test Sweep to see all signal levels, or check antenna / "
                    "try a different band / tune to a known active frequency."
                )
                self.scan_live_label.setStyleSheet(
                    "color: #5cd9ff; font-family: monospace; padding: 2px;"
                )

    def _on_scan_error(self, err: str) -> None:
        self.scan_status.setText(f"Error: {err}")

    def _on_discovered_activated(self, item) -> None:
        freq = item.data(Qt.UserRole)
        self._tune_to(int(freq))

    # ----------------------------- recordings -----------------------------
    def _refresh_recordings(self) -> None:
        self.rec_list.clear()
        for r in self.recordings.list_recordings()[:50]:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.get("start_ts", 0)))
            text = (f"{ts} · {r.get('freq_mhz', 0):.4f} MHz · {r.get('modulation', '')} · "
                    f"{r.get('duration_s', 0):.1f}s · {r.get('label', '')}")
            if r.get("ai_tag"):
                text += f"  [AI: {r['ai_tag']}]"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, r.get("path"))
            self.rec_list.addItem(item)

    # ----------------------------- AI -----------------------------
    def _on_ai_tag_ready(self, freq_hz: int, tag) -> None:
        # Update bookmark if exists
        if self.bookmarks.get(freq_hz):
            self.bookmarks.add(freq_hz=freq_hz,
                                ai_tag=tag.get("summary") or tag.get("signal_type"))
            self._refresh_bookmarks()
        self.status.showMessage(
            f"AI tagged {freq_hz/1e6:.4f} MHz: {tag.get('signal_type')} — {tag.get('summary', '')}",
            5000
        )

    # ----------------------------- settings -----------------------------
    def _on_settings_changed(self) -> None:
        self.config.scan_threshold_db = self.set_threshold.value()
        self.config.scan_dwell_s = self.set_dwell.value()
        self.scanner.threshold_db = self.config.scan_threshold_db
        self.scanner.dwell_s = self.config.scan_dwell_s
        # Auto-threshold settings (the fix for "0 stations found")
        self.scanner.auto_threshold = self.set_auto_threshold.isChecked()
        self.scanner.auto_threshold_margin_db = self.set_margin.value()
        self.config.ai_tagging_enabled = self.set_ai.isChecked()
        self.ai_tagger.enabled = self.config.ai_tagging_enabled
        self.scanner.ai_tagger = self.ai_tagger if self.ai_tagger.enabled else None
        self.config.save()

    # ----------------------------- periodic -----------------------------
    def _update_status(self) -> None:
        # Update status bar
        if self.gqrx.is_connected():
            f = self.config.last_frequency_hz
            b = band_for_frequency(f)
            self.status.showMessage(
                f"Gqrx connected · {f/1e6:.4f} MHz · {self.config.last_modulation} · "
                f"{b.name if b else 'Custom'} · {self.signal_bar.value()} dB"
            )

    # ----------------------------- shutdown -----------------------------
    def closeEvent(self, event) -> None:
        # Persist all the magic-feature state before quitting
        try:
            self._save_magic_state()
        except Exception:
            pass
        try:
            self.config.window_width = self.width()
            self.config.window_height = self.height()
            self.config.save()
        except Exception:
            pass
        try:
            if self.web_server:
                self.web_server.stop()
        except Exception:
            pass
        try:
            self.audio_receiver.stop()
        except Exception:
            pass
        try:
            self.spectrum_receiver.stop()
        except Exception:
            pass
        try:
            self.audio_player.stop()
        except Exception:
            pass
        try:
            self.gqrx.disconnect()
        except Exception:
            pass
        try:
            self.solar_fetcher.stop()
        except Exception:
            pass
        try:
            self.clock.stop()
        except Exception:
            pass
        # Stop new magical-feature components
        try:
            self.dx_cluster.stop()
        except Exception:
            pass
        try:
            if self.auto_surfer.is_running:
                self.auto_surfer.stop()
        except Exception:
            pass
        super().closeEvent(event)
