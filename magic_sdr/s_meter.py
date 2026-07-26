"""Analog S-meter widget — a classic needle-style signal meter.

Looks like the S-meter on a 1970s/80s HF receiver:
  - Curved scale from S1 to S9+60dB
  - Tick marks every S-unit + every 10 dB over S9
  - Needle deflects based on dBFS input
  - Color zones: gray (S1-S5), green (S6-S9), yellow (+10 to +30 dB), red (+40 to +60 dB)

The dBFS-to-S-unit mapping is approximate:
  S9 ≈ -73 dBm (HF) — but we work in dBFS, so we map -40 dBFS to S9.
  Each S-unit ≈ 6 dB.
  So S9 + 10 dB ≈ -30 dBFS, S9 + 20 dB ≈ -20 dBFS, etc.
  Below S9: each S-unit down = +6 dBFS weaker.
  S1 ≈ -118 dBm → in our scale ≈ -94 dBFS.
"""

from __future__ import annotations

import math
from typing import Optional

from PyQt5.QtCore import Qt, QPointF, QRectF
from PyQt5.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QPolygonF, QPaintEvent,
    QRadialGradient, QLinearGradient, QConicalGradient
)
from PyQt5.QtWidgets import QWidget, QSizePolicy


# dBFS values for each S-unit boundary. S1 is the weakest, S9+60 is the strongest.
# We map the analog needle across this range.
S_METER_SCALE = [
    # (label, dBFS)
    ("S1", -94),
    ("S3", -82),
    ("S5", -70),
    ("S7", -58),
    ("S9", -40),       # S9 reference
    ("+10", -30),
    ("+20", -20),
    ("+30", -10),
    ("+40", 0),
]


class SMeterWidget(QWidget):
    """An analog S-meter widget with a needle that deflects based on dBFS."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._level_dbfs: Optional[float] = None  # None = no signal
        self._target_level: Optional[float] = None
        self._needle_angle = -90.0  # starts at left (S1)
        self.setMinimumSize(220, 140)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip(
            "Analog S-meter (signal strength)\n"
            "Range: S1 (weak) to S9+40 dB (very strong)\n"
            "S9 ≈ -40 dBFS, each S-unit ≈ 6 dB"
        )

        # Smooth needle animation: we lerp the current angle toward the target.
        # This makes the needle "settle" with a satisfying mechanical feel.
        from PyQt5.QtCore import QTimer
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(33)  # ~30 fps
        self._anim_timer.timeout.connect(self._animate_needle)
        self._anim_timer.start()

    def set_level(self, level_dbfs: Optional[float]) -> None:
        """Update the meter. Pass None to indicate no signal (needle drops to S1)."""
        self._target_level = level_dbfs

    def _animate_needle(self) -> None:
        """Smoothly move the needle toward the target angle."""
        if self._target_level is None:
            target_angle = -90.0  # S1
        else:
            target_angle = self._dbfs_to_angle(self._target_level)
        # Lerp toward target
        delta = target_angle - self._needle_angle
        if abs(delta) < 0.5:
            self._needle_angle = target_angle
        else:
            # Move 15% of the remaining distance per frame → settles in ~0.3 s
            self._needle_angle += delta * 0.15
        self.update()

    def _dbfs_to_angle(self, dbfs: float) -> float:
        """Map a dBFS value to a needle angle in degrees.

        -90° = S1 (left), 0° = vertical (S9), +90° = S9+40 (right).
        """
        # Clamp to scale range
        clamped = max(S_METER_SCALE[0][1], min(S_METER_SCALE[-1][1], dbfs))
        # Linear interp between scale points
        for i in range(len(S_METER_SCALE) - 1):
            (l1, v1), (l2, v2) = S_METER_SCALE[i], S_METER_SCALE[i + 1]
            if v1 <= clamped <= v2:
                t = (clamped - v1) / (v2 - v1) if v2 != v1 else 0
                # Map i=0 → -90°, last → +90°
                angle_low = -90 + i * (180 / (len(S_METER_SCALE) - 1))
                angle_high = -90 + (i + 1) * (180 / (len(S_METER_SCALE) - 1))
                return angle_low + t * (angle_high - angle_low)
        return 90.0

    # ----------------------------- paint -----------------------------
    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        w = self.width()
        h = self.height()

        # Background panel — black with subtle gradient
        bg_grad = QLinearGradient(0, 0, 0, h)
        bg_grad.setColorAt(0.0, QColor("#1a1d20"))
        bg_grad.setColorAt(1.0, QColor("#0a0c0e"))
        p.setBrush(QBrush(bg_grad))
        p.setPen(QPen(QColor("#444"), 1))
        p.drawRoundedRect(QRectF(0, 0, w, h), 6, 6)

        # Meter geometry — a half-circle arc
        margin_x = 16
        margin_top = 16
        margin_bottom = 26
        cx = w / 2
        cy = h - margin_bottom
        radius = min((w - 2 * margin_x) / 2, h - margin_top - margin_bottom)

        # Draw the colored arc segments
        # We draw the arc as a series of pie slices, colored by zone.
        # The arc spans from 180° (left, S1) to 0° (right, S9+40) going over the top.
        # In Qt, angles are 1/16 degree, 0° = 3 o'clock, CCW positive.
        # We need: S1 at 180° (left), S9+40 at 0° (right).
        # That's an arc from 180° to 360° (= 0°) going clockwise = -180° span.
        # In Qt: startAngle=0*16, spanAngle=-180*16 draws the top half.
        # But we need to color zones, so we draw pie slices one at a time.

        # Zone colors (gray/green/yellow/red):
        zones = [
            # (start_label, end_label, color)
            ("S1", "S5", QColor("#666666")),     # gray
            ("S5", "S9", QColor("#3aaa55")),      # green
            ("S9", "+20", QColor("#cccc44")),     # yellow
            ("+20", "+40", QColor("#cc4444")),    # red
        ]
        label_to_angle = {}
        for i, (label, _) in enumerate(S_METER_SCALE):
            label_to_angle[label] = -90 + i * (180 / (len(S_METER_SCALE) - 1))

        # Draw arc band: a thick line following the scale curve
        # Convert to Qt angles (1/16 degree, 0=3 o'clock, CCW positive)
        # Our angle: -90 = left (S1), 0 = up (S9), +90 = right (S9+40)
        # Qt angle for our angle: 180 - our_angle (in degrees), ×16
        def our_to_qt16(angle_deg: float) -> int:
            """Convert our -90..+90 angle to Qt's 1/16-degree system."""
            # Our 0 = up = Qt 90°. Our +90 = right = Qt 0°. Our -90 = left = Qt 180°.
            qt_deg = 90 - angle_deg
            return int(qt_deg * 16)

        # Draw colored arc band: 4 segments
        band_thickness = 10
        inner_r = radius - 8
        outer_r = radius + 2

        p.setPen(Qt.NoPen)
        for start_lbl, end_lbl, color in zones:
            start_a = label_to_angle[start_lbl]
            end_a = label_to_angle[end_lbl]
            # Draw as a thick arc using a custom path
            from PyQt5.QtGui import QPainterPath
            path = QPainterPath()
            # Outer arc
            r1 = outer_r
            r2 = inner_r
            # Convert angles to radians for trig
            sa = math.radians(90 - start_a)  # 0 = right, 90 = up, 180 = left
            ea = math.radians(90 - end_a)
            # Outer arc start to end
            path.moveTo(cx + r1 * math.cos(sa), cy - r1 * math.sin(sa))
            path.arcTo(QRectF(cx - r1, cy - r1, 2 * r1, 2 * r1),
                       90 - start_a, end_a - start_a)
            # Line to inner end
            path.lineTo(cx + r2 * math.cos(ea), cy - r2 * math.sin(ea))
            # Inner arc back
            path.arcTo(QRectF(cx - r2, cy - r2, 2 * r2, 2 * r2),
                       90 - end_a, start_a - end_a)
            path.closeSubpath()
            p.setBrush(QBrush(color.darker(180)))
            p.drawPath(path)

        # Draw tick marks and labels
        p.setPen(QPen(QColor("#cccccc"), 1))
        label_font = QFont("JetBrains Mono", 8)
        p.setFont(label_font)
        for label, _ in S_METER_SCALE:
            angle = label_to_angle[label]
            rad = math.radians(90 - angle)
            # Major tick
            r_out = outer_r + 2
            r_in = outer_r - 4
            p.drawLine(
                QPointF(cx + r_out * math.cos(rad), cy - r_out * math.sin(rad)),
                QPointF(cx + r_in * math.cos(rad), cy - r_in * math.sin(rad))
            )
            # Label just outside the arc
            r_label = r_out + 12
            lx = cx + r_label * math.cos(rad)
            ly = cy - r_label * math.sin(rad)
            p.drawText(QRectF(lx - 16, ly - 8, 32, 14), Qt.AlignCenter, label)

        # Draw minor ticks (every S-unit not labeled)
        p.setPen(QPen(QColor("#888888"), 0.5))
        for i in range(len(S_METER_SCALE) - 1):
            base_angle = -90 + i * (180 / (len(S_METER_SCALE) - 1))
            next_angle = -90 + (i + 1) * (180 / (len(S_METER_SCALE) - 1))
            for j in range(1, 5):
                t = j / 5
                a = base_angle + t * (next_angle - base_angle)
                rad = math.radians(90 - a)
                p.drawLine(
                    QPointF(cx + outer_r * math.cos(rad), cy - outer_r * math.sin(rad)),
                    QPointF(cx + (outer_r - 3) * math.cos(rad), cy - (outer_r - 3) * math.sin(rad))
                )

        # Draw the needle
        needle_angle_rad = math.radians(90 - self._needle_angle)
        needle_length = radius - 14
        p.setPen(QPen(QColor("#ff5c5c"), 2, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(
            QPointF(cx, cy),
            QPointF(cx + needle_length * math.cos(needle_angle_rad),
                    cy - needle_length * math.sin(needle_angle_rad))
        )
        # Needle pivot (small red dot)
        p.setBrush(QBrush(QColor("#ff5c5c")))
        p.setPen(QPen(QColor("#660000"), 1))
        p.drawEllipse(QPointF(cx, cy), 4, 4)

        # Label below
        p.setPen(QPen(QColor("#888888")))
        f = QFont("JetBrains Mono", 9)
        p.setFont(f)
        if self._target_level is None:
            level_text = "—"
        else:
            level_text = f"{self._target_level:+.1f} dBFS"
        p.drawText(QRectF(0, h - 22, w, 16), Qt.AlignCenter, level_text)
