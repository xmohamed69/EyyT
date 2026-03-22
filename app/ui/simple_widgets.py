"""
app/ui/simple_widgets.py
─────────────────────────────────────────────────────────────────────────────
Merged small UI widgets module.  Contains three self-contained widgets:

  TextOutput         — read-only gaze-typed text field (QTextEdit subclass)
  WebcamPlaceholder  — live camera feed display widget
  SuggestionBar      — horizontal gaze-dwell suggestion chip bar

Why these three are together
────────────────────────────
All three are small, self-contained display widgets:
  - No cross-imports between them
  - No Qt signals that cross into services or vision modules
  - Each is used as a drop-in child widget in main_window / keyboard_window
  - None owns a QMainWindow, dialog, or complex lifecycle

Note: SuggestionBar is the original standalone bar widget. SuggestionPanel
(in panels.py) is the newer floating-overlay version with emoji support.
Both exist and serve slightly different purposes.

Public API — identical to the originals:

    from app.ui.simple_widgets import TextOutput, WebcamPlaceholder, SuggestionBar
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import cv2

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QImage, QLinearGradient,
    QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import QApplication, QSizePolicy, QTextEdit, QWidget


# ══════════════════════════════════════════════════════════════════════════════
#  TextOutput
#  Originally: app/ui/text_output.py
# ══════════════════════════════════════════════════════════════════════════════

class TextOutput(QTextEdit):
    """Read-only text display that accumulates gaze-typed characters."""

    # ── label → character map (all non-trivial keys) ──────────────────────────
    _KEY_MAP: dict[str, str] = {
        "NUM1": "1", "NUM2": "2", "NUM3": "3", "NUM4": "4", "NUM5": "5",
        "NUM6": "6", "NUM7": "7", "NUM8": "8", "NUM9": "9", "NUM0": "0",
        "COMMA":     ",",
        "DOT":       ".",
        "APOS":      "'",
        "QMARK":     "?",
        "SYM_AT":    "@",
        "SYM_HASH":  "#",
        "SYM_EXCL":  "!",
        "SYM_COLON": ":",
        "SYM_SEMI":  ";",
        "SYM_UNDER": "_",
    }

    _NOOP: frozenset[str] = frozenset({
        "LSHIFT", "RSHIFT", "SYM", "CTRL", "EMOJI", "LANG",
    })

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 15))
        self.setPlaceholderText("Gaze-typed text will appear here…")

    def set_rtl(self, rtl: bool) -> None:
        """Switch text direction and font for RTL languages (e.g. Arabic)."""
        if rtl:
            self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            self.setFont(QFont("Arial", 15))
            self.setPlaceholderText("اكتب هنا…")
        else:
            self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
            self.setFont(QFont("Consolas", 15))
            self.setPlaceholderText("Gaze-typed text will appear here…")

    def append_key(self, key: str) -> None:
        """Translate a key label into a text-editing action."""
        if key == "BKSP":
            cur = self.textCursor()
            cur.deletePreviousChar()
            self.setTextCursor(cur)
        elif key == "SPACE":
            self.insertPlainText(" ")
        elif key == "ENTER":
            self.insertPlainText("\n")
        elif key == "CLEAR":
            self.clear()
        elif key == "LEFT":
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.Left)
            self.setTextCursor(cur)
        elif key == "RIGHT":
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.Right)
            self.setTextCursor(cur)
        elif key in self._NOOP:
            pass
        elif key in self._KEY_MAP:
            self.insertPlainText(self._KEY_MAP[key])
        else:
            self.insertPlainText(key.lower())
        self.ensureCursorVisible()


# ══════════════════════════════════════════════════════════════════════════════
#  WebcamPlaceholder
#  Originally: app/ui/webcam_placeholder.py
# ══════════════════════════════════════════════════════════════════════════════

class WebcamPlaceholder(QWidget):
    """
    Webcam widget — displays annotated frames from MediaPipeTracker.
    Falls back to raw OpenCV if no tracker is provided.
    """

    _TICK_MS = 33

    def __init__(
        self,
        tracker=None,
        camera_index: int = -1,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._pixmap  = None
        self._buf     = None
        self._tracker = tracker
        self._cap     = None
        self._error   = None

        self.setMinimumSize(320, 160)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(
            "background-color:#1a1a1a; border:1px solid #444; border-radius:6px;")

        if tracker is None:
            idx = camera_index if camera_index >= 0 else self._find_camera()
            if idx == -1:
                self._error = "No camera found"
            else:
                self._cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
                if not self._cap.isOpened():
                    self._cap = cv2.VideoCapture(idx)
                for _ in range(10):
                    self._cap.read()

        self._timer = QTimer(self)
        self._timer.setInterval(self._TICK_MS)
        self._timer.timeout.connect(self._grab)
        self._timer.start()

    @staticmethod
    def _find_camera() -> int:
        for i in range(6):
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap = cv2.VideoCapture(i)
            if cap.isOpened():
                for _ in range(5):
                    ok, frame = cap.read()
                    if ok and frame is not None and frame.mean() > 1.0:
                        cap.release()
                        return i
                cap.release()
        return -1

    def _grab(self) -> None:
        if self._tracker is not None:
            frame = self._tracker.get_latest_frame()
        elif self._cap is not None:
            ok, frame = self._cap.read()
            if not ok or frame is None:
                return
        else:
            return

        if frame is None:
            return

        self._buf = np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        h, w, _ = self._buf.shape
        self._pixmap = QPixmap.fromImage(
            QImage(self._buf.data, w, h, w * 3,
                   QImage.Format.Format_RGB888).copy()
        )
        self.update()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._error:
            p.setPen(Qt.GlobalColor.red)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._error)
        elif self._pixmap is None:
            p.setPen(Qt.GlobalColor.gray)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Waiting for camera…")
        else:
            scaled = self._pixmap.scaled(
                self.width(), self.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self.width()  - scaled.width())  // 2
            y = (self.height() - scaled.height()) // 2
            p.drawPixmap(x, y, scaled)
        p.end()

    def stop(self) -> None:
        if hasattr(self, "_timer"):
            self._timer.stop()
        if self._cap and self._cap.isOpened():
            self._cap.release()

    def closeEvent(self, ev) -> None:
        self.stop()
        super().closeEvent(ev)


# ══════════════════════════════════════════════════════════════════════════════
#  SuggestionBar
#  Originally: app/ui/suggestion_bar.py
#
#  Note: This is the original chip bar widget (flat, embedded in layout).
#  For the newer floating overlay version with emoji support, see
#  SuggestionPanel in app/ui/panels.py.
# ══════════════════════════════════════════════════════════════════════════════

_DWELL_MS   = 800
_TICK_MS    = 30
_BAR_HEIGHT = 48


class SuggestionBar(QWidget):
    """
    Horizontal bar of gaze-selectable suggestion chips.
    Word chips replace the last typed word.
    Emoji chips append the emoji.
    """

    word_selected = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(_BAR_HEIGHT)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("background: transparent;")

        self._suggestions: list[str]   = []
        self._emojis:      list[str]   = []
        self._all:         list[str]   = []
        self._hover_idx:   Optional[int] = None
        self._dwell_ms:    float       = 0.0
        self._chip_rects:  list[QRectF] = []
        self._source = ""

        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ── public API ────────────────────────────────────────────────────────────

    def set_suggestions(self, words: list[str], source: str = "local") -> None:
        self._suggestions = words[:3]
        self._source      = source
        self._rebuild()

    def set_emojis(self, emojis: list[str]) -> None:
        self._emojis = emojis[:3]
        self._rebuild()

    def clear(self) -> None:
        self._suggestions = []
        self._emojis      = []
        self._source      = ""
        self._rebuild()

    def update_gaze(self, gaze_x: float, gaze_y: float) -> None:
        if not self._all:
            self._hover_idx = None
            return
        scr = QApplication.primaryScreen()
        if scr is None:
            return
        geo = scr.geometry()
        lp  = self.mapFromGlobal(
            QPoint(int(gaze_x * geo.width()), int(gaze_y * geo.height())))
        hit = None
        for i, r in enumerate(self._chip_rects):
            if r.contains(lp.x(), lp.y()):
                hit = i
                break
        self._hover_idx = hit

    # ── internals ─────────────────────────────────────────────────────────────

    def _rebuild(self) -> None:
        self._all       = self._suggestions + self._emojis
        self._hover_idx = None
        self._dwell_ms  = 0.0
        self.update()

    def _tick(self) -> None:
        if self._hover_idx is None:
            self._dwell_ms = 0.0
            return
        self._dwell_ms += _TICK_MS
        if self._dwell_ms >= _DWELL_MS:
            self._select(self._hover_idx)
            self._dwell_ms = 0.0
        self.update()

    def _select(self, idx: int) -> None:
        if 0 <= idx < len(self._all):
            self.word_selected.emit(self._all[idx])

    # ── painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.fillRect(0, 0, W, H, QBrush(QColor(8, 11, 20)))
        p.setPen(QPen(QColor(26, 37, 64), 1))
        p.drawLine(0, 0, W, 0)

        if not self._all:
            p.setPen(QColor(40, 58, 90))
            p.setFont(QFont("Consolas", 8))
            p.drawText(QRectF(0, 0, W, H), Qt.AlignmentFlag.AlignCenter,
                       "— type to see suggestions —")
            p.end()
            return

        if self._source:
            src_col = (QColor(0, 200, 150) if self._source == "cloud"
                       else QColor(80, 120, 180))
            p.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
            p.setPen(src_col)
            badge = "☁ AI" if self._source == "cloud" else "📖 LOCAL"
            p.drawText(QRectF(6, H / 2 - 8, 50, 16),
                       Qt.AlignmentFlag.AlignVCenter, badge)
            x_start = 60.0
        else:
            x_start = 10.0

        PAD_X, PAD_Y, GAP, RAD = 14, 6, 8, 8
        fn_word  = QFont("Segoe UI Semibold", 11, QFont.Weight.DemiBold)
        fn_emoji = QFont("Segoe UI", 14)
        self._chip_rects = []
        x        = x_start
        n_words  = len(self._suggestions)

        for i, text in enumerate(self._all):
            is_emoji = i >= n_words
            is_hover = i == self._hover_idx
            progress = min(1.0, self._dwell_ms / _DWELL_MS) if is_hover else 0.0

            p.setFont(fn_emoji if is_emoji else fn_word)
            fm     = p.fontMetrics()
            tw     = fm.horizontalAdvance(text)
            chip_w = tw + PAD_X * 2
            chip_h = H - PAD_Y * 2
            chip_y = float(PAD_Y)
            rect   = QRectF(x, chip_y, chip_w, chip_h)
            self._chip_rects.append(rect)

            path = QPainterPath()
            path.addRoundedRect(rect, RAD, RAD)

            if is_hover:
                grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
                grad.setColorAt(0, QColor(0, 160, 130, 80))
                grad.setColorAt(1, QColor(0, 200, 160, 120))
                p.fillPath(path, QBrush(grad))
            elif is_emoji:
                p.fillPath(path, QBrush(QColor(20, 28, 45)))
            else:
                p.fillPath(path, QBrush(QColor(14, 20, 35)))

            if is_hover and progress > 0:
                fill_w    = rect.width() * progress
                fill_r    = QRectF(rect.x(), rect.y(), fill_w, rect.height())
                fill_grad = QLinearGradient(rect.topLeft(), rect.topRight())
                fill_grad.setColorAt(0, QColor(0, 200, 160, 100))
                fill_grad.setColorAt(1, QColor(0, 240, 200, 160))
                p.save()
                p.setClipPath(path)
                p.setClipRect(fill_r, Qt.ClipOperation.IntersectClip)
                p.fillPath(path, QBrush(fill_grad))
                p.restore()

            if is_hover:
                p.setPen(QPen(QColor(0, 220, 170), 1.5))
            elif is_emoji:
                p.setPen(QPen(QColor(35, 50, 80), 1.0))
            else:
                p.setPen(QPen(QColor(30, 45, 75), 1.0))
            p.drawPath(path)

            if is_hover:
                p.setPen(QColor(0, 240, 200))
            elif is_emoji:
                p.setPen(QColor(220, 220, 220))
            else:
                p.setPen(QColor(180, 210, 250))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

            x += chip_w + GAP

        p.end()