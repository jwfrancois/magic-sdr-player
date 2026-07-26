"""Memory Presets — car-radio style instant-tune buttons (M1–M12).

Like the preset buttons on a car radio:
  • Click a button → instantly tunes to that stored frequency
  • Long-press a button (800 ms) → stores the current frequency into that slot
  • Right-click a button → clears that slot
  • Empty slots show "—" and are inert

Up to 12 slots. State persists across app restarts via the user's config.

Each preset stores: frequency (Hz), modulation, label, last-heard timestamp.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QPoint
from PyQt5.QtGui import QFont, QColor, QMouseEvent
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QLabel,
    QSizePolicy, QFrame
)


@dataclass
class MemoryPreset:
    freq_hz: int
    modulation: str
    label: str = ""
    stored_at: float = field(default_factory=time.time)


class MemoryButton(QPushButton):
    """A single memory preset button.

    Emits:
      clicked_tune(idx) — short click, tune to stored freq
      long_press_store(idx) — held 800 ms, store current freq into this slot
      right_click_clear(idx) — right-click, clear this slot
    """

    clicked_tune = pyqtSignal(int)
    long_press_store = pyqtSignal(int)
    right_click_clear = pyqtSignal(int)

    HOLD_MS = 800

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self.index = index
        self.preset: Optional[MemoryPreset] = None
        self.setMinimumHeight(54)
        self.setMinimumWidth(96)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # We manage our own click detection because we want a long-press
        # gesture distinct from a normal click.
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(self.HOLD_MS)
        self._hold_timer.timeout.connect(self._on_hold_complete)
        self._hold_fired = False
        self._update_appearance()

    def set_preset(self, preset: Optional[MemoryPreset]) -> None:
        self.preset = preset
        self._update_appearance()

    def _update_appearance(self) -> None:
        if self.preset is None:
            self.setText(f"M{self.index + 1}\n—")
            self.setStyleSheet(self._empty_style())
            self.setToolTip(f"Memory M{self.index + 1} (empty)\n  Long-press to store current freq\n  Right-click to clear")
        else:
            freq_mhz = self.preset.freq_hz / 1e6
            if freq_mhz >= 100:
                freq_str = f"{freq_mhz:.1f}"
            elif freq_mhz >= 1:
                freq_str = f"{freq_mhz:.3f}"
            else:
                freq_str = f"{freq_mhz * 1000:.0f}k"
            label = self.preset.label[:14] if self.preset.label else ""
            self.setText(f"M{self.index + 1}\n{freq_str}\n{label}")
            self.setStyleSheet(self._filled_style())
            self.setToolTip(
                f"Memory M{self.index + 1}\n"
                f"  {freq_mhz:.4f} MHz · {self.preset.modulation}\n"
                f"  {self.preset.label or '(no label)'}\n"
                f"  Stored: {time.strftime('%Y-%m-%d %H:%M', time.localtime(self.preset.stored_at))}\n"
                f"  Click: tune · Long-press: overwrite · Right-click: clear"
            )

    def _empty_style(self) -> str:
        return (
            "QPushButton {"
            "  background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            "    stop:0 #141a24, stop:1 #0c1018);"
            "  color: #4a5266;"
            "  border: 1px solid #2a3447; border-radius: 6px;"
            "  padding: 6px; font-family: 'JetBrains Mono'; font-size: 10px;"
            "  text-align: center;"
            "}"
            "QPushButton:hover { border-color: #3a4458; color: #6a7280; }"
        )

    def _filled_style(self) -> str:
        return (
            "QPushButton {"
            "  background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            "    stop:0 #1e2a3a, stop:1 #16202e);"
            "  color: #5cd9ff;"
            "  border: 1px solid #3a5a7a; border-radius: 6px;"
            "  padding: 6px; font-family: 'JetBrains Mono'; font-size: 10px;"
            "  text-align: center; font-weight: 600;"
            "}"
            "QPushButton:hover {"
            "  background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            "    stop:0 #2a3a4e, stop:1 #1e2a3e);"
            "  border-color: #5cd9ff;"
            "  color: #ffffff;"
            "}"
            "QPushButton:pressed { background: #5cd9ff; color: #0b0f14; }"
        )

    # ----------------------------- events -----------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._hold_fired = False
            self._hold_timer.start()
            event.accept()
        elif event.button() == Qt.RightButton:
            self.right_click_clear.emit(self.index)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._hold_timer.stop()
            if not self._hold_fired:
                # Short click — tune to stored freq (or do nothing if empty)
                self.clicked_tune.emit(self.index)
            event.accept()

    def _on_hold_complete(self) -> None:
        self._hold_fired = True
        self.long_press_store.emit(self.index)


class MemoryPresetBar(QWidget):
    """A horizontal bar of 12 memory buttons + a caption row.

    Args:
        n_slots: number of memory slots (default 12)
        on_tune: callback(freq_hz, modulation) when a preset is clicked
        on_store: callback(slot_index) -> MemoryPreset, called when a slot is
                  long-pressed to ask the parent for the current station info
    """

    tune_requested = pyqtSignal(int, str)  # freq_hz, modulation

    def __init__(self, n_slots: int = 12, parent=None):
        super().__init__(parent)
        self.n_slots = n_slots
        self.presets: List[Optional[MemoryPreset]] = [None] * n_slots
        # Callback the parent sets — returns a MemoryPreset to store
        self.store_callback: Optional[Callable[[], MemoryPreset]] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # Caption
        caption = QLabel("◉ Memory Presets")
        caption.setStyleSheet(
            "color: #8b96a7; font-size: 10px; font-weight: 600; "
            "padding: 2px 4px;"
        )
        caption.setToolTip(
            "Memory Presets — like car radio buttons.\n"
            "  Click: tune to stored frequency\n"
            "  Long-press (hold 0.8s): store current station\n"
            "  Right-click: clear slot"
        )
        layout.addWidget(caption)

        # Button row
        btn_row = QGridLayout()
        btn_row.setSpacing(3)
        self.buttons: List[MemoryButton] = []
        # 2 rows of 6 buttons
        cols = 6
        for i in range(n_slots):
            btn = MemoryButton(i)
            btn.clicked_tune.connect(self._on_clicked_tune)
            btn.long_press_store.connect(self._on_long_press_store)
            btn.right_click_clear.connect(self._on_right_click_clear)
            r, c = i // cols, i % cols
            btn_row.addWidget(btn, r, c)
            self.buttons.append(btn)
        layout.addLayout(btn_row)

    # ----------------------------- public API -----------------------------
    def set_presets(self, presets: List[Optional[MemoryPreset]]) -> None:
        """Replace all presets. List length must equal n_slots."""
        if len(presets) != self.n_slots:
            presets = (presets + [None] * self.n_slots)[:self.n_slots]
        self.presets = presets
        for i, p in enumerate(presets):
            self.buttons[i].set_preset(p)

    def get_presets(self) -> List[Optional[MemoryPreset]]:
        return list(self.presets)

    def store_current(self, slot_idx: int, preset: MemoryPreset) -> None:
        """Store a preset into a specific slot (0-indexed)."""
        if 0 <= slot_idx < self.n_slots:
            self.presets[slot_idx] = preset
            self.buttons[slot_idx].set_preset(preset)

    def clear_slot(self, slot_idx: int) -> None:
        if 0 <= slot_idx < self.n_slots:
            self.presets[slot_idx] = None
            self.buttons[slot_idx].set_preset(None)

    def find_slot_for_frequency(self, freq_hz: int) -> Optional[int]:
        """Return the slot index that contains freq_hz, or None."""
        for i, p in enumerate(self.presets):
            if p and p.freq_hz == freq_hz:
                return i
        return None

    # ----------------------------- handlers -----------------------------
    def _on_clicked_tune(self, idx: int) -> None:
        p = self.presets[idx]
        if p is not None:
            self.tune_requested.emit(p.freq_hz, p.modulation)

    def _on_long_press_store(self, idx: int) -> None:
        if self.store_callback is None:
            return
        preset = self.store_callback()
        if preset is not None:
            self.store_current(idx, preset)

    def _on_right_click_clear(self, idx: int) -> None:
        self.clear_slot(idx)
