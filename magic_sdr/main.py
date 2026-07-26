"""Entry point for Magic SDR Player.

Usage:
    python -m magic_sdr.main
    python /home/z/my-project/magic_sdr/main.py
"""

from __future__ import annotations

import logging
import signal
import sys

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt

from .config import Config


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet down noisy libs
    for name in ("urllib3", "asyncio", "websockets"):
        logging.getLogger(name).setLevel(logging.WARNING)


def main() -> int:
    setup_logging()
    log = logging.getLogger("magic_sdr")

    # Allow Ctrl-C to terminate the Qt app
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    # High-DPI friendly
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("Magic SDR Player")
    app.setOrganizationName("Magic SDR")

    # Apply a dark theme via Qt stylesheet
    app.setStyleSheet(DARK_STYLE)

    config = Config.load()

    # Import here so logging is set up first
    from .main_window import MainWindow
    win = MainWindow(config)
    win.show()

    log.info("Magic SDR Player started")
    return app.exec_()


DARK_STYLE = """
QMainWindow, QWidget { background-color: #0b0f14; color: #e6ecf3; }
QGroupBox { border: 1px solid #2a3447; border-radius: 6px; margin-top: 8px; padding-top: 8px; font-weight: 600; color: #8b96a7; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QPushButton {
    background-color: #1a2230; color: #e6ecf3;
    border: 1px solid #2a3447; border-radius: 4px;
    padding: 6px 12px;
}
QPushButton:hover { background-color: #5cd9ff; color: #0b0f14; }
QPushButton:pressed { background-color: #2a9bcf; }
QPushButton:checked { background-color: #5cd9ff; color: #0b0f14; }
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    background-color: #1a2230; color: #e6ecf3;
    border: 1px solid #2a3447; border-radius: 4px;
    padding: 4px 8px;
}
QComboBox QAbstractItemView { background-color: #1a2230; color: #e6ecf3; selection-background-color: #5cd9ff; selection-color: #0b0f14; }
QSlider::groove:horizontal { border: 1px solid #2a3447; height: 4px; background: #0b0f14; }
QSlider::handle:horizontal { background: #5cd9ff; border: 1px solid #5cd9ff; width: 12px; margin: -5px 0; border-radius: 6px; }
QProgressBar { background-color: #0b0f14; border: 1px solid #2a3447; border-radius: 3px; text-align: center; color: #e6ecf3; }
QProgressBar::chunk { background-color: #5cd9ff; }
QTabWidget::pane { border: 1px solid #2a3447; border-radius: 4px; }
QTabBar::tab { background-color: #1a2230; color: #8b96a7; padding: 6px 12px; border: 1px solid #2a3447; border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px; }
QTabBar::tab:selected { background-color: #0b0f14; color: #5cd9ff; }
QListWidget { background-color: #0b0f14; color: #e6ecf3; border: 1px solid #2a3447; border-radius: 4px; }
QListWidget::item:selected { background-color: #5cd9ff; color: #0b0f14; }
QListWidget::item:hover { background-color: #1a2230; }
QStatusBar { background-color: #1a2230; color: #8b96a7; }
QLabel { color: #e6ecf3; }
QCheckBox { color: #e6ecf3; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #2a3447; background-color: #1a2230; border-radius: 3px; }
QCheckBox::indicator:checked { background-color: #5cd9ff; border: 1px solid #5cd9ff; }
QSplitter::handle { background-color: #2a3447; }
QSplitter::handle:horizontal { width: 2px; }
"""


if __name__ == "__main__":
    sys.exit(main())
