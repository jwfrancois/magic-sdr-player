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
from PyQt5.QtGui import QFont, QIcon, QColor, QPalette
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QTabWidget,
    QLabel, QPushButton, QComboBox, QSlider, QLineEdit, QSpinBox, QDoubleSpinBox,
    QListWidget, QListWidgetItem, QProgressBar, QStatusBar, QMessageBox,
    QGroupBox, QFormLayout, QCheckBox, QFileDialog, QSplitter, QFrame,
    QApplication, QStyle
)

from .gqrx_client import GqrxClient, MODULATIONS
from .audio_receiver import AudioReceiver, AudioPlayer
from .spectrum import SpectrumReceiver, WaterfallWidget
from .band_scanner import BandScanner, DiscoveredStation
from .bookmark_manager import BookmarkManager, Bookmark
from .recording_manager import RecordingManager
from .ai_tagger import AITagger
from .web_server import WebServer
from .band_presets import BANDS, BANDS_BY_NAME, band_for_frequency, lookup_known, guess_modulation
from .config import Config

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
        self.bookmarks = BookmarkManager()
        self.recordings = RecordingManager()
        self.scanner = BandScanner(self.gqrx)
        self.scanner.threshold_db = config.scan_threshold_db
        self.scanner.dwell_s = config.scan_dwell_s
        self.ai_tagger = AITagger()
        self.ai_tagger.enabled = config.ai_tagging_enabled
        self.scanner.ai_tagger = self.ai_tagger if self.ai_tagger.enabled else None

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

        # Apply initial state
        self._apply_config()

    # ----------------------------- UI construction -----------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

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

        # Frequency dial
        self.dial = FrequencyDial()
        self.dial.tune_requested.connect(self._tune_to)
        left_layout.addWidget(self.dial)

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

        # Signal level bar (using a QProgressBar as a meter)
        self.signal_bar = QProgressBar()
        self.signal_bar.setRange(-100, 0)
        self.signal_bar.setFormat("%v dB")
        self.signal_bar.setValue(-100)
        ctrl_layout.addRow("Signal:", self.signal_bar)

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

        left_layout.addStretch(1)
        splitter.addWidget(left)

        # ----- RIGHT: waterfall + tabs -----
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.waterfall = WaterfallWidget()
        self.waterfall.tune_requested.connect(self._tune_to)
        right_layout.addWidget(self.waterfall, stretch=2)

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
        self.tabs.addTab(set_widget, "Settings")

        splitter.addWidget(right)
        splitter.setSizes([400, 800])

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready. Click 'Connect' to attach to Gqrx.")

    # ----------------------------- signal wiring -----------------------------
    def _wire_signals(self) -> None:
        # Gqrx signals
        self.gqrx.connected.connect(self._on_gqrx_connected)
        self.gqrx.disconnected.connect(self._on_gqrx_disconnected)
        self.gqrx.frequency_changed.connect(self._on_freq_changed)
        self.gqrx.modulation_changed.connect(self._on_mod_changed)
        self.gqrx.signal_level.connect(self._on_signal_level)
        self.gqrx.error.connect(self._on_gqrx_error)

        # Audio → player + recording
        self.audio_receiver.chunk_ready.connect(self._on_audio_chunk)

        # Spectrum → waterfall
        self.spectrum_receiver.spectrum_ready.connect(
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

    def _apply_config(self) -> None:
        self.dial.set_frequency(self.config.last_frequency_hz)
        self.mod_combo.setCurrentText(self.config.last_modulation)
        # Apply initial band context to waterfall
        self._update_band_context(self.config.last_frequency_hz)
        # Auto-start web server if enabled
        if self.config.remote_access_enabled:
            QTimer.singleShot(500, lambda: self.web_btn.click())

    # ----------------------------- handlers -----------------------------
    def _on_connect_clicked(self) -> None:
        if self.gqrx.is_connected():
            self.gqrx.disconnect()
            self.diag_timer.stop()
            self._update_diagnostic_banner()  # hide banner
        else:
            ok = self.gqrx.connect()
            if not ok:
                QMessageBox.warning(
                    self, "Cannot connect to Gqrx",
                    f"Could not connect to Gqrx at {self.config.gqrx_host}:{self.config.gqrx_port}.\n\n"
                    "Make sure Gqrx is running and remote control is enabled:\n"
                    "  Tools → Remote control settings → Enable remote control\n\n"
                    "Also enable (REQUIRED for audio + waterfall):\n"
                    "  • Audio UDP stream → UDP port 7355\n"
                    "  • Spectrum UDP stream → UDP port 7357\n\n"
                    "See QUICKSTART.md for screenshots and step-by-step instructions."
                )
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
        """Update the top banner based on UDP stream health.

        The banner appears when Gqrx is connected but the user hasn't enabled
        the UDP audio / spectrum streams inside Gqrx's settings — the single
        most common cause of "0 stations found" and a black waterfall.
        """
        if not self.gqrx.is_connected():
            self.diag_banner.setVisible(False)
            return
        audio_ok = self.audio_receiver.is_streaming(max_age_s=3.0)
        spec_ok = self.spectrum_receiver.is_streaming(max_age_s=3.0)
        audio_count = self.audio_receiver.packet_count()
        spec_count = self.spectrum_receiver.packet_count()

        if audio_ok and spec_ok:
            self.diag_banner.setVisible(False)
            return

        # Build a clear, actionable message
        parts = ["<b>⚠ Gqrx streams not configured</b> — this is why the waterfall "
                 "is black and the scanner finds 0 stations. Gqrx's TCP control "
                 "works, but it is NOT sending UDP audio / spectrum data to Magic SDR."]
        parts.append("")
        parts.append(f"Audio UDP (port 7355): {'OK — ' + str(audio_count) + ' pkts' if audio_ok else 'NOT receiving'}")
        parts.append(f"Spectrum UDP (port 7357): {'OK — ' + str(spec_count) + ' pkts' if spec_ok else 'NOT receiving'}")
        parts.append("")
        parts.append("<b>Fix in Gqrx:</b>")
        parts.append("  1. Tools → Remote control settings → check 'Enable remote control'")
        parts.append("  2. Same dialog → Audio UDP → set host 127.0.0.1, port 7355, click Start")
        parts.append("  3. Same dialog → Spectrum UDP → set host 127.0.0.1, port 7357, click Start")
        parts.append("  4. In Gqrx's main window, press the Play button ▶ to start the receiver")
        parts.append("  5. Set Gqrx's RF Gain to ~40 dB (Hardware opts → RF gain slider)")
        parts.append("")
        parts.append("Then click 🩺 Diagnose to verify, and ▶ Scan again.")
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
        if self.audio_receiver.is_running():
            cnt = self.audio_receiver.packet_count()
            age = self.audio_receiver.last_packet_age_s()
            if self.audio_receiver.is_streaming(max_age_s=2.0):
                lines.append(f"  ✓ Streaming — {cnt} packets received, last {age:.2f}s ago")
            elif cnt > 0:
                lines.append(f"  ⚠ Was streaming ({cnt} packets) but stale ({age:.1f}s ago)")
                lines.append("    → Gqrx may have stopped, or receiver paused.")
            else:
                lines.append("  ✗ NOT receiving any audio packets since connect.")
                lines.append("    → In Gqrx: Tools → Remote control settings →")
                lines.append("      Audio UDP stream → host 127.0.0.1, port 7355, click Start.")
        else:
            lines.append("  ✗ Audio receiver not running (not connected to Gqrx).")
        lines.append("")

        # 3. UDP spectrum
        lines.append("── UDP spectrum stream (port 7357) ──")
        if self.spectrum_receiver.is_running():
            cnt = self.spectrum_receiver.packet_count()
            age = self.spectrum_receiver.last_packet_age_s()
            if self.spectrum_receiver.is_streaming(max_age_s=2.0):
                lines.append(f"  ✓ Streaming — {cnt} packets received, last {age:.2f}s ago")
            elif cnt > 0:
                lines.append(f"  ⚠ Was streaming ({cnt} packets) but stale ({age:.1f}s ago)")
            else:
                lines.append("  ✗ NOT receiving any spectrum packets since connect.")
                lines.append("    → In Gqrx: Tools → Remote control settings →")
                lines.append("      Spectrum UDP stream → host 127.0.0.1, port 7357, click Start.")
        else:
            lines.append("  ✗ Spectrum receiver not running (not connected to Gqrx).")
        lines.append("")

        # 4. Signal level + gain
        if self.gqrx.is_connected():
            lines.append("── Signal level + RF gain ──")
            lvl = self.gqrx.get_signal_level_robust(n_samples=3, interval_s=0.05)
            if lvl is None:
                lines.append("  ✗ Gqrx did not return a signal level — try `l STRENGTH` manually.")
            else:
                lines.append(f"  Signal level: {lvl:.1f} dBFS")
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

        # 5. Recommendations
        lines.append("── What to do ──")
        if not self.gqrx.is_connected():
            lines.append("  1. Open Gqrx")
            lines.append("  2. Tools → Remote control settings → Enable remote control")
            lines.append("  3. Click Connect in Magic SDR")
        else:
            if not self.audio_receiver.is_streaming(max_age_s=2.0):
                lines.append("  • Enable Audio UDP stream in Gqrx (port 7355)")
            if not self.spectrum_receiver.is_streaming(max_age_s=2.0):
                lines.append("  • Enable Spectrum UDP stream in Gqrx (port 7357)")
            if self.config.gain_db < 1.0:
                lines.append("  • Increase RF Gain to ~40 dB (it was 0)")
            if lvl is not None and lvl < -90:
                lines.append("  • Check antenna is plugged in")
                lines.append("  • Press Play ▶ in Gqrx to start the receiver")
            if (self.audio_receiver.is_streaming(max_age_s=2.0)
                    and self.spectrum_receiver.is_streaming(max_age_s=2.0)
                    and lvl is not None and lvl > -75):
                lines.append("  ✓ Everything looks healthy — try scanning again.")
        lines.append("")
        lines.append("See QUICKSTART.md for step-by-step Gqrx setup with screenshots.")
        return "\n".join(lines)

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
        # Also feed the recording manager
        if self.recordings.is_recording:
            # will be picked up on next audio chunk
            pass

    def _on_audio_chunk(self, chunk: np.ndarray, sample_rate: int, channels: int) -> None:
        # Playback
        self.audio_player.push(chunk)
        # Recording
        if self.recordings.is_recording:
            lvl = self.gqrx.get_signal_level() or -120.0
            self.recordings.write_chunk(chunk, sample_rate, channels, signal_level_db=lvl)

    def _tune_to(self, freq_hz: int) -> None:
        if not self.gqrx.is_connected():
            self.status.showMessage("Not connected to Gqrx", 2000)
            return
        b = band_for_frequency(freq_hz)
        mod = b.modulation if b else "FM"
        if self.mod_combo.currentText() != mod:
            self.gqrx.set_modulation(mod)
        self.gqrx.set_frequency(freq_hz)

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
                                              QQt.QueuedConnection,
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
            # Check if streams are healthy — if not, the cause is upstream
            # (Gqrx not streaming UDP), not the scanner threshold.
            audio_ok = self.audio_receiver.is_streaming(max_age_s=3.0)
            spec_ok = self.spectrum_receiver.is_streaming(max_age_s=3.0)
            if not audio_ok or not spec_ok:
                self.scan_live_label.setText(
                    "0 stations found — Gqrx is NOT streaming UDP audio/spectrum "
                    "to Magic SDR. Click 🩺 Diagnose for the exact fix."
                )
                self.scan_live_label.setStyleSheet(
                    "color: #ff8a8a; font-family: monospace; padding: 2px;"
                )
            else:
                self.scan_live_label.setText(
                    "0 stations found — streams are healthy. Try 🔬 Test Sweep to "
                    "see all signal levels, or check antenna / try a different band."
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
        super().closeEvent(event)
