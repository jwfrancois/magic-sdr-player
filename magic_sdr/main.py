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

    # Apply night-vision theme if enabled
    if config.night_vision:
        app.setStyleSheet(NIGHT_VISION_STYLE)

    # Import here so logging is set up first
    from .main_window import MainWindow
    win = MainWindow(config)
    win.show()

    log.info("Magic SDR Player started")
    return app.exec_()


DARK_STYLE = """
QMainWindow, QWidget { background-color: #0b0f14; color: #e6ecf3; }
QGroupBox {
    border: 1px solid #2a3447; border-radius: 6px;
    margin-top: 10px; padding-top: 10px;
    font-weight: 600; color: #8b96a7;
    background-color: #0e131a;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 10px; padding: 0 4px;
    color: #5cd9ff;
}
QPushButton {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #1a2230, stop:1 #161c28);
    color: #e6ecf3;
    border: 1px solid #2a3447; border-radius: 4px;
    padding: 6px 12px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #2a9bcf, stop:1 #1e75a5);
    color: #ffffff;
    border-color: #5cd9ff;
}
QPushButton:pressed { background-color: #2a9bcf; color: #ffffff; }
QPushButton:checked {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #5cd9ff, stop:1 #2a9bcf);
    color: #0b0f14;
    border-color: #5cd9ff;
    font-weight: 700;
}
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    background-color: #1a2230; color: #e6ecf3;
    border: 1px solid #2a3447; border-radius: 4px;
    padding: 4px 8px;
    selection-background-color: #5cd9ff;
    selection-color: #0b0f14;
}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {
    border: 1px solid #5cd9ff;
}
QComboBox QAbstractItemView {
    background-color: #1a2230; color: #e6ecf3;
    selection-background-color: #5cd9ff; selection-color: #0b0f14;
    border: 1px solid #2a3447;
}
QSlider::groove:horizontal {
    border: 1px solid #2a3447; height: 4px; background: #0b0f14;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
        stop:0 #5cd9ff, stop:1 #2a9bcf);
    border: 1px solid #5cd9ff; width: 14px; margin: -6px 0; border-radius: 7px;
}
QSlider::handle:horizontal:hover { background: #80e5ff; }
QSlider::groove:vertical {
    border: 1px solid #2a3447; width: 4px; background: #0b0f14;
    border-radius: 2px;
}
QSlider::handle:vertical {
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
        stop:0 #5cd9ff, stop:1 #2a9bcf);
    border: 1px solid #5cd9ff; height: 14px; margin: 0 -6px; border-radius: 7px;
}
QSlider::handle:vertical:hover { background: #80e5ff; }
QProgressBar {
    background-color: #0b0f14; border: 1px solid #2a3447;
    border-radius: 3px; text-align: center; color: #e6ecf3;
}
QProgressBar::chunk {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #2a9bcf, stop:1 #5cd9ff);
    border-radius: 2px;
}
QTabWidget::pane { border: 1px solid #2a3447; border-radius: 4px; }
QTabBar::tab {
    background-color: #1a2230; color: #8b96a7;
    padding: 6px 14px;
    border: 1px solid #2a3447; border-bottom: none;
    border-top-left-radius: 4px; border-top-right-radius: 4px;
}
QTabBar::tab:selected {
    background-color: #0b0f14; color: #5cd9ff;
    border-color: #5cd9ff;
    font-weight: 600;
}
QTabBar::tab:hover:!selected { color: #b0c4d8; }
QListWidget {
    background-color: #0b0f14; color: #e6ecf3;
    border: 1px solid #2a3447; border-radius: 4px;
}
QListWidget::item:selected { background-color: #5cd9ff; color: #0b0f14; }
QListWidget::item:hover { background-color: #1a2230; }
QStatusBar { background-color: #1a2230; color: #8b96a7; }
QStatusBar::item { border: none; }
QLabel { color: #e6ecf3; }
QCheckBox { color: #e6ecf3; spacing: 6px; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid #2a3447; background-color: #1a2230; border-radius: 3px;
}
QCheckBox::indicator:checked {
    background-color: #5cd9ff; border: 1px solid #5cd9ff;
}
QSplitter::handle { background-color: #2a3447; }
QSplitter::handle:horizontal { width: 2px; }
QSplitter::handle:vertical { height: 2px; }
QToolTip {
    background-color: #1a2230; color: #e6ecf3;
    border: 1px solid #5cd9ff; border-radius: 3px;
    padding: 4px;
}
QScrollBar:vertical {
    background: #0b0f14; width: 10px; border: none;
}
QScrollBar::handle:vertical {
    background: #2a3447; border-radius: 5px; min-height: 20px;
}
QScrollBar::handle:vertical:hover { background: #5cd9ff; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    background: #0b0f14; height: 10px; border: none;
}
QScrollBar::handle:horizontal {
    background: #2a3447; border-radius: 5px; min-width: 20px;
}
QScrollBar::handle:horizontal:hover { background: #5cd9ff; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QMenu {
    background-color: #1a2230; color: #e6ecf3;
    border: 1px solid #2a3447; border-radius: 4px;
    padding: 4px;
}
QMenu::item:selected { background-color: #5cd9ff; color: #0b0f14; }
"""

# Night-vision red theme — preserves dark adaptation for ham operators
# working at night. Uses deep reds and ambers only.
NIGHT_VISION_STYLE = """
QMainWindow, QWidget { background-color: #0a0204; color: #ffa080; }
QGroupBox {
    border: 1px solid #4a1a0a; border-radius: 6px;
    margin-top: 10px; padding-top: 10px;
    font-weight: 600; color: #c06040;
    background-color: #100406;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 10px; padding: 0 4px;
    color: #ff8060;
}
QPushButton {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #2a0810, stop:1 #1a0408);
    color: #ffa080;
    border: 1px solid #4a1a0a; border-radius: 4px;
    padding: 6px 12px; font-weight: 500;
}
QPushButton:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #6a2010, stop:1 #4a1808);
    color: #ffd0a0; border-color: #ff6040;
}
QPushButton:checked {
    background-color: #c04020;
    color: #ffffff; border-color: #ff6040; font-weight: 700;
}
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    background-color: #1a0408; color: #ffa080;
    border: 1px solid #4a1a0a; border-radius: 4px;
    padding: 4px 8px;
    selection-background-color: #ff6040; selection-color: #0a0204;
}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {
    border: 1px solid #ff6040;
}
QComboBox QAbstractItemView {
    background-color: #1a0408; color: #ffa080;
    selection-background-color: #ff6040; selection-color: #0a0204;
    border: 1px solid #4a1a0a;
}
QSlider::groove:horizontal {
    border: 1px solid #4a1a0a; height: 4px; background: #0a0204;
}
QSlider::handle:horizontal {
    background: #ff6040; border: 1px solid #ff8060;
    width: 14px; margin: -6px 0; border-radius: 7px;
}
QSlider::groove:vertical {
    border: 1px solid #4a1a0a; width: 4px; background: #0a0204;
}
QSlider::handle:vertical {
    background: #ff6040; border: 1px solid #ff8060;
    height: 14px; margin: 0 -6px; border-radius: 7px;
}
QProgressBar {
    background-color: #0a0204; border: 1px solid #4a1a0a;
    border-radius: 3px; text-align: center; color: #ffa080;
}
QProgressBar::chunk { background-color: #ff6040; border-radius: 2px; }
QTabWidget::pane { border: 1px solid #4a1a0a; border-radius: 4px; }
QTabBar::tab {
    background-color: #1a0408; color: #c06040;
    padding: 6px 14px; border: 1px solid #4a1a0a; border-bottom: none;
    border-top-left-radius: 4px; border-top-right-radius: 4px;
}
QTabBar::tab:selected {
    background-color: #0a0204; color: #ff8060;
    border-color: #ff6040; font-weight: 600;
}
QListWidget {
    background-color: #0a0204; color: #ffa080;
    border: 1px solid #4a1a0a; border-radius: 4px;
}
QListWidget::item:selected { background-color: #ff6040; color: #0a0204; }
QListWidget::item:hover { background-color: #2a0810; }
QStatusBar { background-color: #1a0408; color: #c06040; }
QLabel { color: #ffa080; }
QCheckBox { color: #ffa080; spacing: 6px; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid #4a1a0a; background-color: #1a0408; border-radius: 3px;
}
QCheckBox::indicator:checked {
    background-color: #ff6040; border: 1px solid #ff8060;
}
QSplitter::handle { background-color: #4a1a0a; }
QSplitter::handle:horizontal { width: 2px; }
QSplitter::handle:vertical { height: 2px; }
QToolTip {
    background-color: #1a0408; color: #ffa080;
    border: 1px solid #ff6040; border-radius: 3px; padding: 4px;
}
QScrollBar:vertical { background: #0a0204; width: 10px; border: none; }
QScrollBar::handle:vertical {
    background: #4a1a0a; border-radius: 5px; min-height: 20px;
}
QScrollBar::handle:vertical:hover { background: #ff6040; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #0a0204; height: 10px; border: none; }
QScrollBar::handle:horizontal {
    background: #4a1a0a; border-radius: 5px; min-width: 20px;
}
QScrollBar::handle:horizontal:hover { background: #ff6040; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QMenu {
    background-color: #1a0408; color: #ffa080;
    border: 1px solid #4a1a0a; border-radius: 4px; padding: 4px;
}
QMenu::item:selected { background-color: #ff6040; color: #0a0204; }
"""


if __name__ == "__main__":
    sys.exit(main())
