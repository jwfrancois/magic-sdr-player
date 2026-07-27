"""Tuning knob widget — a circular rotary knob you drag to change frequency.

Inspired by the big VFO knob on a real HF transceiver. Mouse behavior:
  - Click + drag UP/DOWN: coarse tune (default step × drag amount)
  - Click + drag in a circle: rotate the knob (visual only, also tunes)
  - Mouse wheel: fine tune (1 step per click)
  - Right-click: cycle step size (1 Hz / 10 Hz / 100 Hz / 1 kHz / 10 kHz / 100 kHz / 1 MHz)
  - Double-click: reset to center position (no frequency change)

Emits:
  tune_step(int step_hz) — when the knob is rotated by one "click" worth.
                            Positive = up, negative = down.
  step_changed(int step_hz) — when the step size changes (right-click).
"""

from __future__ import annotations

import math
from typing import List

from PyQt5.QtCore import Qt, QPoint, QPointF, pyqtSignal, QRect
from PyQt5.QtGui import (
    QPainter, QColor, QPen, QBrush, QRadialGradient, QFont, QLinearGradient,
    QPolygonF, QPaintEvent, QMouseEvent, QWheelEvent
)
from PyQt5.QtWidgets import QWidget, QSizePolicy


DEFAULT_STEPS_HZ: List[int] = [
    1, 10, 100, 1_000, 10_000, 100_000, 1_000_000
]
DEFAULT_STEP_INDEX = 4  # 10 kHz


class TuningKnob(QWidget):
    """A circular rotary knob widget for VFO-style frequency tuning."""

    tune_step = pyqtSignal(int)        # step_hz (signed)
    step_changed = pyqtSignal(int)     # new step_hz (absolute)

    def __init__(self, parent=None, *, steps_hz: List[int] | None = None,
                 initial_step_index: int = DEFAULT_STEP_INDEX):
        super().__init__(parent)
        self.steps_hz = steps_hz or DEFAULT_STEPS_HZ
        self.step_index = initial_step_index
        # Angle of the knob indicator, in degrees. 0 = pointing up.
        self._angle = 0.0
        self._dragging = False
        self._last_y = 0
        self._last_angle = 0.0
        self.setMinimumSize(140, 140)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Tool tip explaining controls
        self.setToolTip(
            "Tuning knob\n"
            "  Drag up/down: tune (10 kHz/step by default)\n"
            "  Mouse wheel: fine tune\n"
            "  Right-click: change step size\n"
            "  Double-click: reset knob position"
        )

    # ----------------------------- properties -----------------------------
    @property
    def current_step_hz(self) -> int:
        return self.steps_hz[self.step_index]

    # ----------------------------- events -----------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._last_y = event.y()
            self._last_angle = self._angle_from_pos(event.pos())
            event.accept()
        elif event.button() == Qt.RightButton:
            self._cycle_step()
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._dragging = False
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._dragging:
            return
        # Vertical drag = tune by N steps based on delta y
        dy = self._last_y - event.y()  # positive = up
        # Sensitivity: 8 pixels per step
        step_count = int(dy / 8)
        if step_count != 0:
            step_hz = step_count * self.current_step_hz
            self.tune_step.emit(step_hz)
            # Rotate the indicator visually
            self._angle = (self._angle + step_count * 15) % 360
            if self._angle > 180:
                self._angle -= 360
            self.update()
            self._last_y = event.y()
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._angle = 0.0
            self.update()
            event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:
        # Mouse wheel: 1 step per click (fine tune)
        delta = event.angleDelta().y()
        if delta > 0:
            self.tune_step.emit(self.current_step_hz)
            self._angle = (self._angle + 8) % 360
            if self._angle > 180:
                self._angle -= 360
        elif delta < 0:
            self.tune_step.emit(-self.current_step_hz)
            self._angle = (self._angle - 8) % 360
            if self._angle > 180:
                self._angle -= 360
        self.update()
        event.accept()

    # ----------------------------- helpers -----------------------------
    def _cycle_step(self) -> None:
        self.step_index = (self.step_index + 1) % len(self.steps_hz)
        self.step_changed.emit(self.current_step_hz)
        self.update()

    def _angle_from_pos(self, pos: QPoint) -> float:
        """Return angle in degrees from knob center to mouse pos."""
        cx = self.width() / 2
        cy = self.height() / 2
        return math.degrees(math.atan2(pos.y() - cy, pos.x() - cx))

    # ----------------------------- paint -----------------------------
    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        w = self.width()
        h = self.height()
        cx = w / 2
        cy = h / 2
        radius = min(w, h) / 2 - 8

        # Outer ring (dark metallic)
        outer_grad = QRadialGradient(cx - radius * 0.3, cy - radius * 0.3, radius * 1.5)
        outer_grad.setColorAt(0.0, QColor("#4a4f55"))
        outer_grad.setColorAt(0.7, QColor("#2a2e33"))
        outer_grad.setColorAt(1.0, QColor("#1a1d20"))
        p.setBrush(QBrush(outer_grad))
        p.setPen(QPen(QColor("#0a0c0e"), 2))
        p.drawEllipse(QPointF(cx, cy), radius, radius)

        # Tick marks around the perimeter (24 ticks, every 15°)
        p.setPen(QPen(QColor("#888"), 1))
        for i in range(24):
            angle = math.radians(i * 15 - 90)
            r1 = radius - 4
            r2 = radius - (10 if i % 6 == 0 else 6)
            x1 = cx + r1 * math.cos(angle)
            y1 = cy + r1 * math.sin(angle)
            x2 = cx + r2 * math.cos(angle)
            y2 = cy + r2 * math.sin(angle)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # Inner knob (the part that rotates)
        inner_r = radius - 14
        inner_grad = QRadialGradient(cx - inner_r * 0.4, cy - inner_r * 0.4, inner_r * 1.5)
        inner_grad.setColorAt(0.0, QColor("#6a6f75"))
        inner_grad.setColorAt(0.6, QColor("#3a3e43"))
        inner_grad.setColorAt(1.0, QColor("#1a1d20"))
        p.setBrush(QBrush(inner_grad))
        p.setPen(QPen(QColor("#0a0c0e"), 1))
        p.drawEllipse(QPointF(cx, cy), inner_r, inner_r)

        # Indicator pointer (the line that points in the current direction)
        angle_rad = math.radians(self._angle - 90)
        p.setPen(QPen(QColor("#5cd9ff"), 3, Qt.SolidLine, Qt.RoundCap))
        x_end = cx + (inner_r - 6) * math.cos(angle_rad)
        y_end = cy + (inner_r - 6) * math.sin(angle_rad)
        p.drawLine(QPointF(cx, cy), QPointF(x_end, y_end))

        # Center cap
        p.setBrush(QBrush(QColor("#5cd9ff")))
        p.setPen(QPen(QColor("#0a0c0e"), 1))
        p.drawEllipse(QPointF(cx, cy), 4, 4)

        # Step label below
        p.setPen(QPen(QColor("#888")))
        f = QFont("JetBrains Mono", 8)
        p.setFont(f)
        step = self.current_step_hz
        if step >= 1_000_000:
            step_str = f"{step // 1_000_000} MHz"
        elif step >= 1_000:
            step_str = f"{step // 1_000} kHz"
        else:
            step_str = f"{step} Hz"
        p.drawText(
            QRect(0, h - 18, w, 16), Qt.AlignCenter,
            f"Step: {step_str}"
        )
