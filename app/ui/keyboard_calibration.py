"""
app/ui/keyboard_calibration.py
────────────────────────────────
Keyboard-fitted calibration overlay.

Shows calibration dots DIRECTLY on the keyboard widget in two phases:

  Phase 1 — Key dots (9 specific keys: corners, edges, centre)
      Q  T  P         top row
      G               home row centre
      Z  M            bottom row corners
      SPACE  BKSP     action keys

  Phase 2 — Grid pass (6 points spread across the keyboard area)
      fills gaps between the key dots for better affine fit accuracy

For each point:
  • A red glowing dot appears on the key / grid position
  • Instruction label says "Look at the dot — click Next ▶"
  • User clicks Next → 40 iris samples are collected
  • Averaged → one (gaze_point → keyboard_pixel) pair
  • Repeat for all points

After all points:
  • cv2.estimateAffine2D fits gaze coords → keyboard pixel coords
  • Saved to data/kb_calibration.json  AND  data/calibration.json
    (both so the existing load path still works)
  • Signal finished(mean_error) is emitted
  • Overlay hides itself

Why this is better than the old approach
─────────────────────────────────────────
The old calibration mapped iris → FULL SCREEN normalised coords.
The keyboard only occupies the bottom 50% of the screen, so any
full-screen calibration error is magnified by 2× in the keyboard area.

This calibration maps iris → keyboard widget pixel coords directly.
The target points are the actual key centres — there is no intermediate
normalised screen step that can drift.  After calibration, every gaze
sample is transformed into widget-local coordinates and checked against
key rectangles, which eliminates the screen-size mismatch entirely.

Coordinate system
─────────────────
gaze input:  (eye_x, eye_y) — normalised iris-in-eye ratios from tracker
             typically in range [0.35, 0.65] for normal gaze
target:      (px, py)       — pixel coordinates WITHIN the keyboard widget
             e.g. (120, 45) for the Q key on a 1280-wide keyboard

The fitted affine matrix M maps:
    [px, py] ≈ M · [eye_x, eye_y, 1]

At runtime in main_window._tick():
    raw = tracker.get_gaze()          # eye_x, eye_y in [0,1]
    kb_px, kb_py = tracker.map_to_kb(raw.x_norm, raw.y_norm)
    label = kb.get_key_at_pixel(kb_px, kb_py)
"""
from __future__ import annotations

import math
import time
from typing import Optional

import cv2
import numpy as np

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QPainter, QPainterPath, QPen, QBrush,
)
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from app.services.stores import save_kb_calib

# ── calibration point definitions ────────────────────────────────────────────
# Each entry is (label_on_keyboard, description)
# The pixel position is resolved at runtime from KeyboardWidget.key_rect()
_PHASE1_KEYS = [
    ("Q",    "top-left corner"),
    ("T",    "top centre"),
    ("P",    "top-right corner"),
    ("G",    "home row centre"),
    ("Z",    "bottom-left"),
    ("M",    "bottom-right"),
    ("BKSP", "backspace"),
    ("SPACE","space bar"),
]

# Phase 2 grid: normalised positions within the keyboard widget (x, y in 0-1)
_PHASE2_GRID = [
    (0.15, 0.20),   # upper-left area
    (0.85, 0.20),   # upper-right area
    (0.15, 0.75),   # lower-left area
    (0.85, 0.75),   # lower-right area
    (0.50, 0.20),   # top centre
    (0.50, 0.75),   # bottom centre
]

_SAMPLES_PER_POINT = 40
_TICK_MS           = 30
_DOT_RADIUS        = 18
_RING_RADIUS       = 28

# ── hands-free auto-sampling ──────────────────────────────────────────────────
# After the dot appears, wait _SETTLE_MS for the user to fixate, then
# automatically start collecting _SAMPLES_PER_POINT frames.
# No Next click required.
_SETTLE_MS         = 1200   # ms to wait before auto-sampling starts
_AUTO_SAMPLE       = True   # set False to revert to manual Next-click mode


class KeyboardCalibrationOverlay(QWidget):
    """
    Semi-transparent overlay drawn on top of the keyboard widget.

    Signals
    ───────
    finished(float)   — emitted when calibration completes; arg = mean error
    cancelled()       — emitted if user cancels
    """

    finished  = Signal(float)   # mean_error
    cancelled = Signal()

    def __init__(self, kb_widget: QWidget, tracker, parent=None) -> None:
        super().__init__(parent)
        self._kb     = kb_widget
        self._tracker = tracker

        # calibration state
        self._phase:       int   = 1          # 1 or 2
        self._pt_idx:      int   = 0
        self._gaze_pts:    list  = []         # collected (eye_x, eye_y)
        self._kb_pts:      list  = []         # matching (px, py) in kb coords
        self._samples:     list  = []         # iris samples for current point
        self._collecting:  bool  = False
        self._t:           float = 0.0
        # hands-free: ms elapsed since dot appeared (for settle timer)
        self._settle_elapsed_ms: float = 0.0

        # current dot position in widget coords
        self._dot_x: float = 0.0
        self._dot_y: float = 0.0

        # all phase-1 points resolved at show-time
        self._phase1_points: list[tuple[float, float, str]] = []  # (x, y, desc)
        self._phase2_points: list[tuple[float, float, str]] = []

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setStyleSheet("background: transparent;")

        # bottom bar
        self._build_bar()
        self._update_bar()

        # animation timer
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)

    # ── bar ───────────────────────────────────────────────────────────────────

    def _build_bar(self) -> None:
        self._bar = QWidget(self)
        self._bar.setStyleSheet("background:rgba(0,0,0,200);")
        self._bar.setFixedHeight(52)

        h = QHBoxLayout(self._bar)
        h.setContentsMargins(16, 0, 16, 0)
        h.setSpacing(10)

        self._lbl_instr = QLabel()
        self._lbl_instr.setFont(QFont("Consolas", 11))
        self._lbl_instr.setStyleSheet("color:#c0cce0;background:transparent;")
        h.addWidget(self._lbl_instr, 1)

        S = ("QPushButton{padding:7px 18px;border-radius:4px;font-size:11px;"
             "color:white;border:none;}")

        self._btn_next = QPushButton("Next  ▶")
        self._btn_next.setStyleSheet(
            S + "QPushButton{background:#1a3a6a;}"
                "QPushButton:hover{background:#2a5a9a;}"
                "QPushButton:disabled{background:#222;color:#555;}")
        self._btn_next.clicked.connect(self._on_next)
        h.addWidget(self._btn_next)

        self._btn_cancel = QPushButton("✕  Cancel")
        self._btn_cancel.setStyleSheet(
            S + "QPushButton{background:#6a1a1a;}"
                "QPushButton:hover{background:#9a2a2a;}")
        self._btn_cancel.clicked.connect(self._on_cancel)
        h.addWidget(self._btn_cancel)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        if hasattr(self, "_bar"):
            self._bar.setGeometry(0, self.height() - 52, self.width(), 52)
            self._bar.raise_()

    # ── public API ────────────────────────────────────────────────────────────

    def begin(self) -> None:
        """Resolve key positions and start calibration."""
        self._resolve_points()
        self.resize(self._kb.size())
        self.move(0, 0)
        self.raise_()
        self.show()
        self._timer.start()
        self._go_to_point()

    def _resolve_points(self) -> None:
        """Convert key labels and grid fractions to widget pixel coordinates."""
        self._phase1_points = []
        for label, desc in _PHASE1_KEYS:
            rect = self._get_key_rect(label)
            if rect is not None:
                cx = rect.center().x()
                cy = rect.center().y()
                self._phase1_points.append((cx, cy, desc))

        W, H_full = self._kb.width(), self._kb.height()
        H = H_full - 52   # exclude bar area
        self._phase2_points = [
            (x * W, y * H, f"grid {i+1}")
            for i, (x, y) in enumerate(_PHASE2_GRID)
        ]

    def _get_key_rect(self, label: str) -> Optional[QRectF]:
        """Ask the keyboard widget for a named key's bounding rect."""
        try:
            # KeyboardWidget stores _keys list with .label and .rect
            for k in self._kb._keys:
                if k.label == label:
                    return k.rect
        except Exception:
            pass
        return None

    # ── calibration flow ──────────────────────────────────────────────────────

    def _current_points(self) -> list[tuple[float, float, str]]:
        return self._phase1_points if self._phase == 1 else self._phase2_points

    def _total_points(self) -> int:
        return len(self._phase1_points) + len(self._phase2_points)

    def _global_idx(self) -> int:
        if self._phase == 1:
            return self._pt_idx
        return len(self._phase1_points) + self._pt_idx

    def _go_to_point(self) -> None:
        pts = self._current_points()
        if self._pt_idx >= len(pts):
            # advance phase or finish
            if self._phase == 1:
                self._phase = 2
                self._pt_idx = 0
                self._go_to_point()
            else:
                self._finish()
            return

        x, y, desc = pts[self._pt_idx]
        self._dot_x    = x
        self._dot_y    = y
        self._samples  = []
        self._collecting = False
        self._settle_elapsed_ms = 0.0   # reset hands-free settle timer
        self._btn_next.setEnabled(True)
        self._btn_next.setText("Next  ▶")
        self._update_bar()
        self.update()

    def _update_bar(self) -> None:
        pts   = self._current_points()
        total = self._total_points()
        g_idx = self._global_idx() + 1
        phase_lbl = "Phase 1/2 — Keys" if self._phase == 1 else "Phase 2/2 — Grid"

        if not hasattr(self, "_lbl_instr"):
            return

        if self._collecting:
            n = len(self._samples)
            self._lbl_instr.setText(
                f"[{phase_lbl}]  Point {g_idx}/{total}  —  "
                f"Sampling… {n}/{_SAMPLES_PER_POINT}  keep looking at the dot")
            self._btn_next.setEnabled(False)
        else:
            if pts and self._pt_idx < len(pts):
                _, _, desc = pts[self._pt_idx]
                if _AUTO_SAMPLE:
                    remain = max(0, _SETTLE_MS - self._settle_elapsed_ms) / 1000.0
                    self._lbl_instr.setText(
                        f"[{phase_lbl}]  Point {g_idx}/{total}  —  "
                        f"Look at the  {desc}  dot  — auto-sampling in {remain:.1f}s")
                else:
                    self._lbl_instr.setText(
                        f"[{phase_lbl}]  Point {g_idx}/{total}  —  "
                        f"Look at the  {desc}  dot,  then click  Next ▶")
            else:
                self._lbl_instr.setText("Finishing…")

    def _tick(self) -> None:
        self._t += _TICK_MS / 1000.0

        if not self._collecting:
            # ── hands-free: auto-start sampling after settle delay ────────────
            if _AUTO_SAMPLE:
                self._settle_elapsed_ms += _TICK_MS
                self._update_bar()   # refresh countdown display
                if self._settle_elapsed_ms >= _SETTLE_MS:
                    # auto-trigger — same as clicking Next
                    self._collecting = True
                    self._btn_next.setEnabled(False)
                    self._btn_next.setText("Sampling…")
                    self._update_bar()
                    print(f"[Cal] Auto-sampling point {self._global_idx() + 1}"
                          f"/{self._total_points()}")
        else:
            # ── collect iris samples ──────────────────────────────────────────
            gp = self._tracker.get_gaze()
            feat = None
            try:
                feat = self._tracker.get_last_feature()
            except Exception:
                pass

            if gp is not None and gp.confidence > 0.4 and feat and len(feat) >= 4:
                eye_x = (feat[0] + feat[2]) / 2.0
                eye_y = (feat[1] + feat[3]) / 2.0
                self._samples.append((eye_x, eye_y))

            self._update_bar()

            if len(self._samples) >= _SAMPLES_PER_POINT:
                self._commit_point()

        self.update()

    def _on_next(self) -> None:
        if self._collecting:
            return
        self._collecting = True
        self._btn_next.setEnabled(False)
        self._btn_next.setText("Sampling…")
        self._update_bar()

    def _commit_point(self) -> None:
        self._collecting = False

        # median of samples for robustness
        arr = np.array(self._samples, dtype=np.float64)
        mid = len(arr) // 2
        eye_x = float(np.sort(arr[:, 0])[mid])
        eye_y = float(np.sort(arr[:, 1])[mid])

        self._gaze_pts.append((eye_x, eye_y))
        self._kb_pts.append((self._dot_x, self._dot_y))

        self._pt_idx += 1
        self._go_to_point()

    def _on_cancel(self) -> None:
        self._timer.stop()
        self.hide()
        self.cancelled.emit()

    def _finish(self) -> None:
        self._timer.stop()

        if len(self._gaze_pts) < 4:
            self._on_cancel()
            return

        src = np.array(self._gaze_pts,  dtype=np.float32)
        dst = np.array(self._kb_pts,    dtype=np.float32)
        M, inliers = cv2.estimateAffine2D(src, dst, method=cv2.RANSAC)

        if M is None:
            self._on_cancel()
            return

        # compute mean reprojection error in pixels
        n      = len(src)
        errors = []
        for i in range(n):
            pt  = np.array([[[src[i, 0], src[i, 1]]]], dtype=np.float32)
            out = cv2.transform(pt, M)
            dx  = out[0, 0, 0] - dst[i, 0]
            dy  = out[0, 0, 1] - dst[i, 1]
            errors.append(math.sqrt(dx * dx + dy * dy))
        mean_err_px = sum(errors) / len(errors)

        # normalise error to [0,1] relative to keyboard width for the signal
        mean_err_norm = mean_err_px / max(self._kb.width(), 1)

        # persist
        save_kb_calib(
            matrix=M.tolist(),
            kb_width=self._kb.width(),
            kb_height=self._kb.height(),
            mean_error_px=mean_err_px,
            gaze_points=self._gaze_pts,
            kb_points=self._kb_pts,
        )

        # load into tracker
        try:
            self._tracker.load_kb_calibration(M)
        except Exception:
            pass

        self.hide()
        self.finished.emit(mean_err_norm)

    # ── painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W = self.width()
        H = self.height() - 52   # above bar

        # semi-transparent dark overlay — dimmer over the whole keyboard
        p.fillRect(0, 0, W, H, QColor(0, 0, 0, 140))

        cx, cy = self._dot_x, self._dot_y

        # spotlight: cut a bright circle around the target dot
        outer = QPainterPath()
        outer.addRect(QRectF(0, 0, W, H))
        inner = QPainterPath()
        inner.addEllipse(QPointF(cx, cy), 60, 60)
        p.fillPath(outer - inner, QColor(0, 0, 0, 80))

        # progress ring (fills as samples are collected)
        if self._collecting and _SAMPLES_PER_POINT > 0:
            prog = min(1.0, len(self._samples) / _SAMPLES_PER_POINT)
            ring_pen = QPen(QColor(0, 200, 160), 4)
            ring_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(ring_pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(
                QRectF(cx - _RING_RADIUS, cy - _RING_RADIUS,
                       _RING_RADIUS * 2, _RING_RADIUS * 2),
                90 * 16,
                int(-prog * 360 * 16),
            )

        # glowing red dot with pulse animation
        pulse = _DOT_RADIUS + int(3 * math.sin(self._t * 6))
        for r, a in ((pulse + 16, 20), (pulse + 8, 50), (pulse, 160)):
            p.setBrush(QColor(220, 40, 40, a))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(cx, cy), r, r)
        # bright white centre
        p.setBrush(QColor(255, 255, 255, 230))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), 4, 4)

        # crosshair
        p.setPen(QPen(QColor(255, 255, 255, 80), 1))
        p.drawLine(QPointF(cx - pulse - 10, cy), QPointF(cx + pulse + 10, cy))
        p.drawLine(QPointF(cx, cy - pulse - 10), QPointF(cx, cy + pulse + 10))

        # counter label below dot
        g_idx  = self._global_idx() + 1
        total  = self._total_points()
        p.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
        p.setPen(QColor(255, 255, 255, 200))
        p.drawText(
            QRectF(cx - 50, cy + _RING_RADIUS + 6, 100, 22),
            Qt.AlignmentFlag.AlignCenter,
            f"{g_idx} / {total}",
        )

        p.end()