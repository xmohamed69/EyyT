"""
app/ui/startup_dialogs.py
─────────────────────────────────────────────────────────────────────────────
Merged startup dialogs module.  Contains two blocking startup dialogs:

  maybe_show_welcome() / WelcomeDialog  — first-run instructions dialog
  require_camera()                      — camera detection gate

Why these two are together
──────────────────────────
Both run before MainWindow is created, both are modal/blocking, and neither
has any runtime state after they complete. They are the only two UI
components that main.py and calibrator.py import before building their
main windows.

Merging them keeps all "pre-flight" startup UI in one file and removes two
import lookups at startup.

Public API — identical to the originals:

    from app.ui.startup_dialogs import maybe_show_welcome, require_camera
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient,
    QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)


# ══════════════════════════════════════════════════════════════════════════════
#  WelcomeDialog
#  Originally: app/ui/welcome_dialog.py
# ══════════════════════════════════════════════════════════════════════════════

_PREF_PATH = Path.home() / ".eyetracker" / "welcome_shown.json"


def _is_suppressed() -> bool:
    try:
        return json.loads(_PREF_PATH.read_text()).get("suppress", False)
    except Exception:
        return False


def _set_suppressed(value: bool) -> None:
    _PREF_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PREF_PATH.write_text(json.dumps({"suppress": value}))


def maybe_show_welcome() -> None:
    """Show the welcome dialog unless the user has suppressed it."""
    if _is_suppressed():
        return
    WelcomeDialog().exec()


_STEPS = [
    ("🪑", "Position yourself",
     "Sit 40–70 cm from your screen in a well-lit room. "
     "Make sure your face is fully visible in the camera — "
     "the preview box on the calibration screen turns green when you're detected."),
    ("🎯", "Run calibration",
     "Click ▶ Calibrate. A full-screen overlay will appear with a 3-second "
     "countdown, then 5 red dots will appear one by one. "
     "Look directly at each dot and hold your gaze steady until the ring fills. "
     "Keep your head still — only move your eyes."),
    ("✅", "Check accuracy",
     "After all dots, a result screen shows your mean error score. "
     "Below 0.08 is great. If accuracy is low, click Retry. "
     "You can also advance to 9- or 13-point calibration for better precision."),
    ("⌨️", "Use the keyboard",
     "Once calibrated, the on-screen keyboard unlocks. "
     "Look at a key and hold your gaze on it — the blue dwell ring fills up "
     "and the key is selected when it completes. "
     "Adjust dwell time with the slider (400–2000 ms) to suit your comfort."),
    ("💡", "Tips for best results",
     "• Avoid strong back-lighting (e.g. window behind you).\n"
     "• Re-calibrate if you move your chair or change lighting.\n"
     "• Use the Reset button to clear a bad calibration and start fresh.\n"
     "• Export your session to Excel with the Export button."),
]


class WelcomeDialog(QDialog):

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Eye-Tracking Keyboard — Welcome")
        self.setModal(True)
        self.setFixedSize(640, 620)
        self.setStyleSheet("""
            QDialog { background-color: #0f0f13; }
            QScrollArea, QWidget#scroll_content { background-color: transparent; border: none; }
            QScrollBar:vertical { background: #1a1a22; width: 6px; border-radius: 3px; }
            QScrollBar::handle:vertical { background: #3a3a55; border-radius: 3px; min-height: 20px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            QCheckBox { color: #888; font-size: 11px; spacing: 6px; }
            QCheckBox::indicator { width: 15px; height: 15px; border-radius: 3px;
                border: 1px solid #444; background: #1a1a22; }
            QCheckBox::indicator:checked { background: #4472C4; border-color: #4472C4; }
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(_WelcomeHeader())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("scroll_content")
        steps_lay = QVBoxLayout(content)
        steps_lay.setContentsMargins(28, 20, 28, 20)
        steps_lay.setSpacing(12)
        for i, (icon, title, body) in enumerate(_STEPS):
            steps_lay.addWidget(_StepCard(i + 1, icon, title, body))
        steps_lay.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)

        footer = QWidget()
        footer.setStyleSheet("background:#0f0f13; border-top: 1px solid #222;")
        footer.setFixedHeight(64)
        foot_lay = QHBoxLayout(footer)
        foot_lay.setContentsMargins(28, 0, 28, 0)
        self._no_show = QCheckBox("Do not show again")
        foot_lay.addWidget(self._no_show)
        foot_lay.addStretch()

        btn = QPushButton("Got it — Let's Start  ▶")
        btn.setFixedHeight(38)
        btn.setMinimumWidth(190)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #1a3a6a,stop:1 #2a5a9a);
                color: white; border: none; border-radius: 6px;
                font-size: 13px; font-weight: 600; padding: 0 20px; letter-spacing: 0.3px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #2a4a8a,stop:1 #3a6aba);
            }
        """)
        btn.clicked.connect(self._accept)
        foot_lay.addWidget(btn)
        outer.addWidget(footer)

    def _accept(self) -> None:
        if self._no_show.isChecked():
            _set_suppressed(True)
        self.accept()


class _WelcomeHeader(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(110)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 18, 28, 14)
        lay.setSpacing(4)
        title = QLabel("  Eye-Tracking Keyboard")
        title.setFont(QFont("Segoe UI", 22, QFont.Weight.Bold))
        title.setStyleSheet("color: #e8e8f0; background: transparent;")
        lay.addWidget(title)
        sub = QLabel("Read this quick guide before you start — it only takes a minute.")
        sub.setFont(QFont("Segoe UI", 10))
        sub.setStyleSheet("color: #6a6a88; background: transparent;")
        lay.addWidget(sub)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0.0, QColor(18, 18, 32))
        grad.setColorAt(1.0, QColor(10, 22, 45))
        p.fillRect(self.rect(), QBrush(grad))
        p.setPen(QColor(40, 80, 160, 180))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        p.end()


class _StepCard(QWidget):
    def __init__(self, number: int, icon: str, title: str,
                 body: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        row.setAlignment(Qt.AlignmentFlag.AlignTop)

        badge_col = QVBoxLayout()
        badge_col.setSpacing(4)
        badge_col.setAlignment(Qt.AlignmentFlag.AlignTop)
        badge = QLabel(str(number))
        badge.setFixedSize(28, 28)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        badge.setStyleSheet(
            "background:#1a3a6a;color:#7aadee;border-radius:14px;"
            "border:1px solid #2a5a9a;")
        badge_col.addWidget(badge)
        icon_lbl = QLabel(icon)
        icon_lbl.setFixedSize(28, 28)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setFont(QFont("Segoe UI", 15))
        icon_lbl.setStyleSheet("background: transparent;")
        badge_col.addWidget(icon_lbl)
        row.addLayout(badge_col)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        text_col.setAlignment(Qt.AlignmentFlag.AlignTop)
        t = QLabel(title)
        t.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        t.setStyleSheet("color: #d0d8f0; background: transparent;")
        text_col.addWidget(t)
        b = QLabel(body)
        b.setFont(QFont("Segoe UI", 10))
        b.setWordWrap(True)
        b.setStyleSheet("color: #7a8099; background: transparent;")
        text_col.addWidget(b)
        row.addLayout(text_col, stretch=1)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 8, 8)
        p.fillPath(path, QBrush(QColor(20, 20, 30)))
        p.setPen(QColor(35, 35, 55))
        p.drawPath(path)
        p.end()

    def sizeHint(self):
        sh = super().sizeHint()
        sh.setHeight(max(sh.height(), 80))
        return sh


# ══════════════════════════════════════════════════════════════════════════════
#  CameraCheck (require_camera)
#  Originally: app/ui/camera_check.py
# ══════════════════════════════════════════════════════════════════════════════

_PROBE_INDICES = list(range(6))


def _probe_cameras() -> int:
    from app.services.platform_utils import get_camera_backends
    backends = get_camera_backends()
    for i in _PROBE_INDICES:
        for backend in backends:
            cap = cv2.VideoCapture(i, backend)
            if not cap.isOpened():
                cap.release()
                continue
            for _ in range(5):
                ok, frame = cap.read()
                if ok and frame is not None and frame.mean() > 1.0:
                    cap.release()
                    return i
            cap.release()
    return -1


def require_camera() -> int:
    """Probe cameras and return the working index. Shows a blocking dialog if none found."""
    idx = _probe_cameras()
    if idx >= 0:
        return idx
    dlg = _NoCameraDialog()
    while True:
        result = dlg.exec()
        if result == QDialog.DialogCode.Accepted:
            idx = _probe_cameras()
            if idx >= 0:
                return idx
            dlg._set_status("Still no camera detected. Check connections and retry.")
        else:
            QApplication.quit()
            sys.exit(0)


class _NoCameraDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("EyeTyper — Camera Required")
        self.setModal(True)
        self.setFixedSize(520, 340)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.CustomizeWindowHint |
            Qt.WindowType.WindowTitleHint)
        self.setStyleSheet(
            "QDialog{background:#080c14;} QLabel{background:transparent;border:none;}")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(_CameraHeaderStripe())

        body = QWidget()
        body.setStyleSheet("background:#0d1320;")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(36, 24, 36, 24)
        bl.setSpacing(10)
        self._status = QLabel(
            "Please connect a USB webcam or enable your built-in camera,\n"
            "then click Retry.")
        self._status.setFont(QFont("Bahnschrift", 10))
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setStyleSheet("color:#8899bb;")
        self._status.setWordWrap(True)
        bl.addWidget(self._status)
        tips = QLabel(
            "•  Make sure no other app is using the camera\n"
            "•  Try unplugging and replugging the USB camera\n"
            "•  Check Device Manager if the camera is still not found")
        tips.setFont(QFont("Bahnschrift", 9))
        tips.setStyleSheet("color:#445566; margin-top:6px;")
        bl.addWidget(tips)
        lay.addWidget(body, stretch=1)

        foot = QWidget()
        foot.setFixedHeight(64)
        foot.setStyleSheet("background:#080c14; border-top:1px solid #1a2540;")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(36, 0, 36, 0)
        fl.setSpacing(12)
        fl.addStretch()

        btn_quit = QPushButton("Quit")
        btn_quit.setFixedSize(110, 36)
        btn_quit.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_quit.setStyleSheet(
            "QPushButton{background:#1a1f2e;color:#556688;border:1px solid #2a3450;"
            "border-radius:5px;font-family:Bahnschrift;font-size:11px;}"
            "QPushButton:hover{background:#222840;color:#8899bb;}")
        btn_quit.clicked.connect(self.reject)
        fl.addWidget(btn_quit)

        btn_retry = QPushButton("🔄  Retry")
        btn_retry.setFixedSize(130, 36)
        btn_retry.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_retry.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #0a3060,stop:1 #0a4a80);color:#00ccff;border:1px solid #0a5090;"
            "border-radius:5px;font-family:Bahnschrift;font-size:11px;font-weight:bold;}"
            "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #0a4080,stop:1 #0a60a0);}")
        btn_retry.clicked.connect(self.accept)
        fl.addWidget(btn_retry)
        lay.addWidget(foot)

    def _set_status(self, msg: str) -> None:
        self._status.setText(msg)
        self._status.setStyleSheet("color:#e07040;")


class _CameraHeaderStripe(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(100)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 16, 36, 12)
        lay.setSpacing(3)
        row = QHBoxLayout()
        row.setSpacing(10)
        icon = QLabel("📷")
        icon.setFont(QFont("Segoe UI Emoji", 22))
        icon.setStyleSheet("color:#00ccff;")
        row.addWidget(icon)
        t = QLabel("No Camera Detected")
        t.setFont(QFont("Bahnschrift", 16, QFont.Weight.Bold))
        t.setStyleSheet("color:#e8eeff; letter-spacing:0.5px;")
        row.addWidget(t)
        row.addStretch()
        lay.addLayout(row)
        s = QLabel("EyeTyper requires a webcam to track your gaze.")
        s.setFont(QFont("Bahnschrift", 9))
        s.setStyleSheet("color:#445577;")
        lay.addWidget(s)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        grad = QLinearGradient(0, 0, self.width(), 0)
        grad.setColorAt(0.0, QColor(10, 20, 50))
        grad.setColorAt(1.0, QColor(8, 12, 20))
        p.fillRect(self.rect(), QBrush(grad))
        p.setPen(QPen(QColor(0, 120, 180, 120), 1))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        p.end()