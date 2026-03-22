"""
app/ui/camera_widgets.py
─────────────────────────────────────────────────────────────────────────────
Merged camera feed widgets.  Contains two always-on-top floating windows:

  CameraWindow   — top-RIGHT corner, 200×140, used by main_window.py
  CameraOverlay  — top-LEFT  corner, 240×175, used by calibrator / debug

Why these two are together
──────────────────────────
Both widgets share identical structure:
  • Frameless / always-on-top / translucent QWidget
  • cv2 frame grab via tracker.get_latest_frame()
  • Green/red face-detected border + status dot
  • Draggable, double-click collapse/expand
  • Same paintEvent patterns (_paint_expanded / _paint_collapsed)

Merging removes ~120 lines of duplication and keeps all shared painting
helpers in one place.

camera_check.py is intentionally NOT merged here — it is a blocking startup
dialog, imported by both main.py and calibrator.py before any tracker exists.

Public API — unchanged:

    from app.ui.camera_widgets import CameraWindow, CameraOverlay
"""
from __future__ import annotations

import numpy as np
import cv2

from PySide6.QtCore import Qt, QPoint, QRect, QTimer
from PySide6.QtGui import (
    QBrush, QColor, QFont, QImage,
    QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import QApplication, QWidget


# ── Shared paint helper ───────────────────────────────────────────────────────

def _paint_status_bar(
    p: QPainter,
    bar_y: int,
    W: int,
    bar_h: int,
    face_ok: bool,
    hint_text: str = "dbl-click ▲",
    bg: QColor = QColor(5, 6, 10, 210),
) -> None:
    """Draw the bottom status bar shared by both widgets."""
    p.fillRect(2, bar_y, W - 4, bar_h, bg)

    dot = QColor(0, 210, 145) if face_ok else QColor(210, 55, 55)
    p.setBrush(dot)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(9, bar_y + (bar_h - 9) // 2, 9, 9)

    p.setPen(dot)
    p.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
    p.drawText(24, bar_y + bar_h - 5, "FACE OK" if face_ok else "NO FACE")

    p.setPen(QColor(35, 45, 65))
    p.setFont(QFont("Consolas", 6))
    p.drawText(W - 58, bar_y + bar_h - 5, hint_text)


def _paint_collapsed_dot(p: QPainter, W: int, H: int, face_ok: bool) -> None:
    """Draw the collapsed dot shared by both widgets."""
    col = QColor(0, 200, 140, 230) if face_ok else QColor(190, 45, 45, 230)
    p.setBrush(col)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(3, 3, W - 6, H - 6)
    p.setPen(QColor(255, 255, 255, 200))
    p.setFont(QFont("Segoe UI Emoji", 14))
    p.drawText(0, 0, W, H, Qt.AlignmentFlag.AlignCenter, "👁")


def _frame_to_pixmap(frame: np.ndarray) -> QPixmap:
    """Convert a BGR numpy frame to a QPixmap."""
    rgb = np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    h, w, _ = rgb.shape
    return QPixmap.fromImage(
        QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
    )


# ══════════════════════════════════════════════════════════════════════════════
#  CameraWindow
#  Top-RIGHT corner  •  200×140 expanded  •  40×40 collapsed
#  Used by: main_window.py  (floating camera toggle in settings)
# ══════════════════════════════════════════════════════════════════════════════

_CW_W_EXP = 200
_CW_H_EXP = 140
_CW_W_COL = 40
_CW_H_COL = 40
_CW_RAD   = 10
_CW_PAD   = 12
_CW_TICK  = 40      # ~25 fps


class CameraWindow(QWidget):
    """
    Floating always-on-top camera feed — top-right corner.

    tracker must implement:
        get_latest_frame() → np.ndarray | None
        get_gaze()         → object | None
    """

    def __init__(self, tracker, parent=None) -> None:
        super().__init__(parent)
        self._tracker   = tracker
        self._pixmap    = None
        self._face_ok   = False
        self._collapsed = False
        self._drag_pos  = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint  |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(_CW_W_EXP, _CW_H_EXP)
        self._snap_to_corner()

        self._timer = QTimer(self)
        self._timer.setInterval(_CW_TICK)
        self._timer.timeout.connect(self._grab)
        self._timer.start()

    def _snap_to_corner(self) -> None:
        scr = QApplication.primaryScreen()
        if scr is None:
            self.move(100, 100)
            return
        geo = scr.availableGeometry()
        self.move(geo.right() - self.width() - _CW_PAD,
                  geo.top()   + _CW_PAD)

    def _grab(self) -> None:
        try:
            frame         = self._tracker.get_latest_frame()
            gp            = self._tracker.get_gaze()
            self._face_ok = gp is not None
        except Exception:
            self._face_ok = False
            frame = None

        if frame is not None and not self._collapsed:
            self._pixmap = _frame_to_pixmap(frame)
        self.update()

    # ── painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        if self._collapsed:
            _paint_collapsed_dot(p, W, H, self._face_ok)
        else:
            self._paint_expanded(p, W, H)
        p.end()

    def _paint_expanded(self, p: QPainter, W: int, H: int) -> None:
        BAR_H  = 24
        FEED_H = H - BAR_H

        card = QPainterPath()
        card.addRoundedRect(0, 0, W, H, _CW_RAD, _CW_RAD)
        p.fillPath(card, QBrush(QColor(8, 8, 12, 235)))

        border = QColor(0, 200, 140) if self._face_ok else QColor(190, 45, 45)
        p.setPen(QPen(border, 1.5))
        p.drawPath(card)

        if self._pixmap:
            scaled = self._pixmap.scaled(
                W - 4, FEED_H - 4,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            ox   = (W - scaled.width()) // 2
            clip = QPainterPath()
            clip.addRoundedRect(2, 2, W - 4, FEED_H, _CW_RAD - 2, _CW_RAD - 2)
            p.save()
            p.setClipPath(clip)
            p.drawPixmap(ox, 2, scaled)
            p.restore()
        else:
            p.setPen(QColor(40, 50, 70))
            p.setFont(QFont("Consolas", 8))
            p.drawText(0, 0, W, FEED_H,
                       Qt.AlignmentFlag.AlignCenter, "Waiting for camera…")

        _paint_status_bar(p, H - BAR_H, W, BAR_H, self._face_ok)

    # ── interaction ───────────────────────────────────────────────────────────

    def mouseDoubleClickEvent(self, _ev) -> None:
        self._collapsed = not self._collapsed
        self.setFixedSize(
            (_CW_W_COL, _CW_H_COL) if self._collapsed
            else (_CW_W_EXP, _CW_H_EXP)
        )
        self.update()

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (ev.globalPosition().toPoint()
                              - self.frameGeometry().topLeft())

    def mouseMoveEvent(self, ev) -> None:
        if self._drag_pos and ev.buttons() == Qt.MouseButton.LeftButton:
            self.move(ev.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, _ev) -> None:
        self._drag_pos = None

    def stop(self) -> None:
        self._timer.stop()

    def closeEvent(self, ev) -> None:
        self.stop()
        super().closeEvent(ev)


# ══════════════════════════════════════════════════════════════════════════════
#  CameraOverlay
#  Top-LEFT corner  •  240×175 expanded  •  48×48 collapsed
#  Used by: calibrator, debug tools
# ══════════════════════════════════════════════════════════════════════════════

_CO_W     = 240
_CO_H     = 175
_CO_W_COL = 48
_CO_H_COL = 48
_CO_MAR   = 12
_CO_TICK  = 33      # ~30 fps
_CO_RAD   = 10


class CameraOverlay(QWidget):
    """
    Floating always-on-top camera feed — top-left corner.

    tracker must implement:
        get_latest_frame() → np.ndarray | None
        get_gaze()         → object with .confidence  (or None)
    """

    def __init__(self, tracker, parent=None) -> None:
        super().__init__(parent)
        self._tracker   = tracker
        self._pixmap    = None
        self._face_ok   = False
        self._collapsed = False
        self._drag_pos  = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint  |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(_CO_W, _CO_H)
        self.move(_CO_MAR, _CO_MAR)

        self._timer = QTimer(self)
        self._timer.setInterval(_CO_TICK)
        self._timer.timeout.connect(self._grab)
        self._timer.start()

    def _grab(self) -> None:
        frame         = self._tracker.get_latest_frame()
        gp            = self._tracker.get_gaze()
        self._face_ok = gp is not None and gp.confidence > 0.3

        if frame is not None and not self._collapsed:
            self._pixmap = _frame_to_pixmap(frame)
        self.update()

    # ── painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        if self._collapsed:
            _paint_collapsed_dot(p, W, H, self._face_ok)
        else:
            self._paint_expanded(p, W, H)
        p.end()

    def _paint_expanded(self, p: QPainter, W: int, H: int) -> None:
        BAR_H  = 28
        FEED_H = H - BAR_H

        path = QPainterPath()
        path.addRoundedRect(0, 0, W, H, _CO_RAD, _CO_RAD)
        p.fillPath(path, QBrush(QColor(10, 14, 24, 230)))

        border = QColor(0, 200, 140) if self._face_ok else QColor(200, 50, 50)
        p.setPen(QPen(border, 1.5))
        p.drawPath(path)

        if self._pixmap:
            scaled = self._pixmap.scaled(
                W - 4, FEED_H - 4,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            ox   = (W - scaled.width()) // 2
            clip = QPainterPath()
            clip.addRoundedRect(2, 2, W - 4, FEED_H, _CO_RAD - 2, _CO_RAD - 2)
            p.save()
            p.setClipPath(clip)
            p.drawPixmap(ox, 2, scaled)
            p.restore()
        else:
            p.setPen(QColor(50, 70, 100))
            p.setFont(QFont("Consolas", 9))
            p.drawText(QRect(0, 0, W, FEED_H),
                       Qt.AlignmentFlag.AlignCenter, "Waiting for camera…")

        _paint_status_bar(
            p, H - BAR_H, W, BAR_H, self._face_ok,
            bg=QColor(8, 12, 20, 200),
        )

    # ── interaction ───────────────────────────────────────────────────────────

    def mouseDoubleClickEvent(self, _ev) -> None:
        self._collapsed = not self._collapsed
        self.setFixedSize(
            (_CO_W_COL, _CO_H_COL) if self._collapsed
            else (_CO_W, _CO_H)
        )
        self.update()

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (ev.globalPosition().toPoint()
                              - self.frameGeometry().topLeft())

    def mouseMoveEvent(self, ev) -> None:
        if self._drag_pos and ev.buttons() == Qt.MouseButton.LeftButton:
            self.move(ev.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, _ev) -> None:
        self._drag_pos = None

    def stop(self) -> None:
        self._timer.stop()