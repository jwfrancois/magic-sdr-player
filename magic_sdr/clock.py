"""UTC + Local clock widget.

A small widget for the top bar that shows both UTC and local time side by
side. UTC matters for shortwave broadcast schedules and ham band plans;
local time matters for everything else.

The widget auto-updates every second via a QTimer.
"""

from __future__ import annotations

from datetime import datetime, timezone

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QLabel, QWidget, QHBoxLayout, QVBoxLayout


class ClockWidget(QWidget):
    """Shows UTC and local time side by side, updated every second."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(12)

        # UTC label
        utc_caption = QLabel("UTC")
        utc_caption.setStyleSheet("color: #888; font-size: 10px;")
        utc_caption.setAlignment(Qt.AlignCenter)
        self.utc_label = QLabel("--:--:--")
        f = QFont("JetBrains Mono", 11)
        f.setWeight(QFont.Medium)
        self.utc_label.setFont(f)
        self.utc_label.setStyleSheet("color: #5cd9ff;")
        self.utc_label.setAlignment(Qt.AlignCenter)

        # Separator
        sep = QLabel("│")
        sep.setStyleSheet("color: #444; font-size: 16px;")
        sep.setAlignment(Qt.AlignCenter)

        # Local label
        local_caption = QLabel("LOCAL")
        local_caption.setStyleSheet("color: #888; font-size: 10px;")
        local_caption.setAlignment(Qt.AlignCenter)
        self.local_label = QLabel("--:--:--")
        self.local_label.setFont(f)
        self.local_label.setStyleSheet("color: #5cd9ff;")
        self.local_label.setAlignment(Qt.AlignCenter)

        # Date label (smaller, below)
        self.date_label = QLabel("---- -- --")
        date_font = QFont("JetBrains Mono", 9)
        self.date_label.setFont(date_font)
        self.date_label.setStyleSheet("color: #888;")
        self.date_label.setAlignment(Qt.AlignCenter)

        # Layout: [UTC caption | UTC time | sep | LOCAL caption | LOCAL time | date]
        col_left = QWidget()
        col_left_l = QVBoxLayout(col_left)
        col_left_l.setContentsMargins(0, 0, 0, 0)
        col_left_l.setSpacing(0)
        col_left_l.addWidget(utc_caption)
        col_left_l.addWidget(self.utc_label)
        layout.addWidget(col_left)

        layout.addWidget(sep)

        col_mid = QWidget()
        col_mid_l = QVBoxLayout(col_mid)
        col_mid_l.setContentsMargins(0, 0, 0, 0)
        col_mid_l.setSpacing(0)
        col_mid_l.addWidget(local_caption)
        col_mid_l.addWidget(self.local_label)
        layout.addWidget(col_mid)

        layout.addWidget(self.date_label)

        # Update every second
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._tick()

    def _tick(self) -> None:
        now = datetime.now(timezone.utc)
        local = datetime.now()
        self.utc_label.setText(now.strftime("%H:%M:%S"))
        self.local_label.setText(local.strftime("%H:%M:%S"))
        self.date_label.setText(local.strftime("%a %Y-%m-%d"))

    def stop(self) -> None:
        """Stop the timer (called on close)."""
        self._timer.stop()
