"""
main_window.py  —  EyeTyper keyboard window.
Frameless, always-on-top, taskbar-aware.

Layout (top → bottom)
──────────────────────
  [1] Toolbar   30 px  ✕  HIDE  |  EN  |  DWELL ──●── ms  |  MOUSE Alt+S  |  💾  ● Ready
  [2] Sep        1 px
  [3] Suggest   28 px
  [4] Sep        1 px
  [5] Input     46 px  — clean Consolas text field, no blue border
  [6] Sep        1 px
  [7] Keyboard   ∞     — fills all remaining height
"""
from __future__ import annotations

import platform
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow,
    QPushButton, QSlider, QVBoxLayout, QWidget,
)

from app.contracts import GazePoint
from app.services.stores import calib_exists
from app.services.dwell_selector import DwellSelector
from app.services.text_services import SessionLog
from app.ui.keyboard_widget import KeyboardWidget
from app.ui.panels import SuggestionPanel
from app.ui.simple_widgets import TextOutput
from app.services.hotkey_listener import make_listener

_TICK_MS   = 30
_WIN_H     = 400   # total window height — keyboard gets ~280 px

_HOTKEY    = "alt+s"
_HOTKEY_UI = "⌥S" if platform.system() == "Darwin" else "Alt+S"

_LANGS = [
    ("EN",  "QWERTY", False),
    ("FR",  "AZERTY", False),
    ("AR",  "عربي",   True),
]


# ─────────────────────────────────────────────────────────────────────────────
#  Style helpers
# ─────────────────────────────────────────────────────────────────────────────
_TB_BTN = """
QPushButton {{
    background: {bg};
    color: {fg};
    border: 1px solid {bd};
    border-radius: 4px;
    padding: 1px {pw}px;
    font-size: 9px;
    font-family: Consolas;
    letter-spacing: 0.5px;
    min-width: {mw}px;
    max-height: 20px;
}}
QPushButton:hover   {{ background:{hbg}; color:{hfg}; border-color:{hbd}; }}
QPushButton:pressed {{ background:#060608; }}
QPushButton:checked {{ background:{cbg}; color:{cfg}; border-color:{cbd}; }}
"""

def _tb_btn(label, checkable=False,
            bg="#111116", fg="#404860", bd="#1a1a22",
            hbg="#18181f", hfg="#7080a0", hbd="#222230",
            cbg="#06140f", cfg="#00c8a0", cbd="#00c8a028",
            pw=8, mw=0):
    b = QPushButton(label)
    b.setCheckable(checkable)
    b.setStyleSheet(_TB_BTN.format(**locals()))
    return b


def _vline():
    w = QWidget(); w.setFixedSize(1, 14)
    w.setStyleSheet("background:#1c1c26;")
    return w


def _hline():
    w = QWidget(); w.setFixedHeight(1)
    w.setStyleSheet("background:#111118;")
    return w


# ─────────────────────────────────────────────────────────────────────────────
#  Toast
# ─────────────────────────────────────────────────────────────────────────────
class _Toast(QLabel):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFont(QFont("Consolas", 9))
        self.setStyleSheet(
            "background:#050d09; color:#00c8a0;"
            "border:1px solid #00c8a028; border-radius:5px; padding:4px 14px;")
        self.hide()
        self._t = QTimer(self); self._t.setSingleShot(True)
        self._t.timeout.connect(self.hide)

    def show_message(self, msg):
        self.setText(msg); self.adjustSize()
        if self.parent():
            pw, ph = self.parent().width(), self.parent().height()
            w = max(self.width(), 250)
            self.setGeometry((pw-w)//2, ph-self.height()-12, w, self.height())
        self.show(); self.raise_(); self._t.start(2200)


# ─────────────────────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):

    def __init__(self, gaze_provider, tracker=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("EyeTyper")
        self._gaze       = gaze_provider
        self._tracker    = tracker
        self._dwell      = DwellSelector(dwell_time_ms=800)
        self._session    = SessionLog()
        self._kb_enabled = calib_exists()
        self._mouse_mode = False
        self._lang_idx   = 0

        self._setup_window()
        self._build_ui()
        self._position_window()
        self._apply_kb_state()

        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._gaze.start()
        self._timer.start()

        self._hotkey = make_listener(hotkey=_HOTKEY, parent=self)
        self._hotkey.triggered.connect(self._on_hotkey)
        self._hotkey.start()

    # ── Window setup ──────────────────────────────────────────────────────────

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint  |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )

    def _position_window(self):
        scr = QApplication.primaryScreen()
        if not scr:
            self.resize(1280, _WIN_H); return
        avail = scr.availableGeometry()
        self.setGeometry(avail.left(), avail.bottom() - _WIN_H,
                         avail.width(), _WIN_H)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        root.setStyleSheet("background:#18181c;")
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # thin top accent
        acc = QWidget(); acc.setFixedHeight(1)
        acc.setStyleSheet("background:#00c8a015;")
        outer.addWidget(acc)

        inner = QVBoxLayout()
        inner.setContentsMargins(8, 3, 8, 4)
        inner.setSpacing(0)
        outer.addLayout(inner)

        # ── TOOLBAR ───────────────────────────────────────────────────────────
        tb = QHBoxLayout()
        tb.setSpacing(5)
        tb.setContentsMargins(0, 2, 0, 2)

        # close ✕
        self._btn_close = QPushButton("✕")
        self._btn_close.setFixedSize(20, 20)
        self._btn_close.setStyleSheet(
            "QPushButton{background:#180c10;color:#3c1822;border:1px solid #22101a;"
            "border-radius:4px;font-size:10px;}"
            "QPushButton:hover{background:#2c0c18;color:#d02840;border-color:#420c1e;}"
            "QPushButton:pressed{background:#100608;}")
        self._btn_close.clicked.connect(QApplication.quit)
        tb.addWidget(self._btn_close)

        # HIDE
        self._btn_hide = _tb_btn("HIDE", mw=32)
        self._btn_hide.clicked.connect(self.hide)
        tb.addWidget(self._btn_hide)

        tb.addWidget(_vline())

        # language cycler
        self._btn_lang = _tb_btn("EN", mw=26)
        self._btn_lang.setToolTip("Cycle keyboard language")
        self._btn_lang.clicked.connect(self._on_lang_cycle)
        tb.addWidget(self._btn_lang)

        tb.addWidget(_vline())

        # dwell
        dw_lbl = QLabel("DWELL")
        dw_lbl.setStyleSheet(
            "color:#1e2030;font-size:8px;letter-spacing:1px;font-family:Consolas;")
        tb.addWidget(dw_lbl)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(300, 2000)
        self._slider.setValue(800)
        self._slider.setFixedWidth(80)
        self._slider.setFixedHeight(16)
        self._slider.setStyleSheet("""
            QSlider::groove:horizontal{height:2px;background:#111118;border-radius:1px;}
            QSlider::handle:horizontal{width:10px;height:10px;margin:-4px 0;
                background:#00c8a0;border-radius:5px;}
            QSlider::sub-page:horizontal{background:#00c8a0;border-radius:1px;}
        """)
        self._slider.valueChanged.connect(self._on_dwell_changed)
        tb.addWidget(self._slider)

        self._dwell_val = QLabel("800ms")
        self._dwell_val.setStyleSheet(
            "color:#222234;font-size:8px;font-family:Consolas;min-width:32px;")
        tb.addWidget(self._dwell_val)

        tb.addWidget(_vline())

        # mouse toggle
        self._btn_mouse = _tb_btn(f"MOUSE  {_HOTKEY_UI}", checkable=True, mw=80)
        self._btn_mouse.setToolTip(
            f"Toggle keyboard ↔ mouse\nGlobal shortcut: {_HOTKEY_UI}")
        self._btn_mouse.toggled.connect(self._on_mouse_toggled)
        tb.addWidget(self._btn_mouse)

        tb.addWidget(_vline())

        # export
        self._btn_export = _tb_btn("💾", mw=22)
        self._btn_export.setToolTip("Export session")
        self._btn_export.clicked.connect(self._on_export)
        tb.addWidget(self._btn_export)

        tb.addWidget(_vline())

        # status dot + text — right side, NO stretch before it so it stays compact
        self._dot = QLabel("●")
        self._dot.setStyleSheet("color:#1a1c28;font-size:8px;")
        tb.addWidget(self._dot)

        self._status = QLabel("No calibration")
        self._status.setStyleSheet(
            "color:#2a2c3a;font-size:9px;font-family:Consolas;")
        tb.addWidget(self._status)

        # push everything left, empty right space
        tb.addStretch(1)

        inner.addLayout(tb)

        # ── SEP ───────────────────────────────────────────────────────────────
        inner.addWidget(_hline())

        # ── SUGGESTION BAR ────────────────────────────────────────────────────
        self._suggestion_panel = SuggestionPanel()
        self._suggestion_panel.setFixedHeight(28)
        self._suggestion_panel.word_selected.connect(self._on_suggestion_selected)
        inner.addWidget(self._suggestion_panel)

        inner.addWidget(_hline())

        # ── INPUT FIELD ───────────────────────────────────────────────────────
        self._text = TextOutput()
        self._text.setFixedHeight(46)
        self._text.setStyleSheet("""
            QTextEdit {
                background: #0e0e12;
                color: #d0d8f0;
                border: 1px solid #1e1e28;
                border-radius: 4px;
                padding: 4px 10px;
                font-family: Consolas;
                font-size: 15px;
                selection-background-color: #00c8a030;
            }
        """)
        inner.addWidget(self._text)

        inner.addSpacing(2)
        inner.addWidget(_hline())

        # ── KEYBOARD ──────────────────────────────────────────────────────────
        self._kb = KeyboardWidget()
        self._kb.setMinimumHeight(200)
        inner.addWidget(self._kb, 1)

        # calibration overlay
        self._overlay = QLabel(
            "CALIBRATION REQUIRED  —  Run EyeTyper Calibrator first")
        self._overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._overlay.setStyleSheet("""
            QLabel {
                color: #706030; background: rgba(4,4,8,220);
                border: 1px solid #1c1808; border-radius:6px;
                font-size:12px; font-family:Consolas;
                letter-spacing:1px; padding:14px;
            }
        """)
        self._overlay.setParent(self._kb)
        self._overlay.hide()

        # toast
        self._toast = _Toast(root)

    # ── State ─────────────────────────────────────────────────────────────────

    def _apply_kb_state(self):
        if self._kb_enabled:
            self._dot.setStyleSheet("color:#00c8a0;font-size:8px;")
            self._status.setText("Ready")
            self._status.setStyleSheet(
                "color:#2c4436;font-size:9px;font-family:Consolas;")
            if hasattr(self, '_overlay'): self._overlay.hide()
        else:
            self._dot.setStyleSheet("color:#4c3010;font-size:8px;")
            self._status.setText("Calibrate first")
            self._status.setStyleSheet(
                "color:#4c3c20;font-size:9px;font-family:Consolas;")
            if hasattr(self, '_overlay'): self._overlay.show()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if hasattr(self, '_overlay') and hasattr(self, '_kb'):
            self._overlay.setGeometry(self._kb.rect())

    # ── Gaze tick ─────────────────────────────────────────────────────────────

    @Slot()
    def _tick(self):
        try:
            gp: Optional[GazePoint] = self._gaze.get_gaze()
        except Exception:
            return

        if self._mouse_mode and gp is not None:
            try:
                from app.services.platform_utils import move_cursor
                scr = QApplication.primaryScreen()
                if scr:
                    g = scr.geometry()
                    move_cursor(int(gp.x_norm * g.width()),
                                int(gp.y_norm * g.height()))
            except Exception:
                pass
            return

        if not self._kb_enabled:
            return

        if gp is None:
            self._kb.set_highlight(None, 0.0)
            self._dwell.reset()
            return

        self._suggestion_panel.update_gaze(gp.x_norm, gp.y_norm)

        # ── Magnetic cursor hit-test ──────────────────────────────────────
        # get_key_with_magnet() uses float pixel coords (no int truncation)
        # and applies hysteresis so the cursor stays on a key until the
        # head moves past the breakout threshold — prevents key-skipping.
        scr = __import__('PySide6.QtWidgets', fromlist=['QApplication'])\
              .QApplication.primaryScreen()
        if scr:
            g     = scr.geometry()
            # Use float multiply — no round/int — for sub-pixel precision
            gx_px = gp.x_norm * g.width()
            gy_px = gp.y_norm * g.height()
            # Convert screen pixel → widget-local float pixel
            from PySide6.QtCore import QPoint as _QPoint
            wpt   = self._kb.mapFromGlobal(_QPoint(round(gx_px), round(gy_px)))
            label = self._kb.get_key_with_magnet(float(wpt.x()), float(wpt.y()))
        else:
            label = self._kb.get_key_at_gaze(gp.x_norm, gp.y_norm)

        result, progress = self._dwell.update(label)
        self._kb.set_highlight(label, progress)

        if result:
            self._kb.flash_key(result)
            self._kb.reset_magnet()   # clear snap so next key starts fresh
            self._text.append_key(result)
            self._session.log_key(result, gp)
            self._dot.setStyleSheet("color:#00c8a0;font-size:8px;")
            QTimer.singleShot(260, lambda: self._dot.setStyleSheet(
                "color:#1a1c28;font-size:8px;"))
            self._update_suggestions()

    def _update_suggestions(self):
        try:
            text = self._text.toPlainText()
            last = text.split()[-1] if text.split() else ""
            if not last:
                self._suggestion_panel.clear_suggestions(); return
            # plug in your autocorrect service here
            self._suggestion_panel.set_words([], source="local")
        except Exception:
            pass

    # ── Slots ─────────────────────────────────────────────────────────────────

    @Slot()
    def _on_hotkey(self):
        new = not self._mouse_mode
        self._btn_mouse.setChecked(new)
        self._toast.show_message(
            f"🖱  Mouse mode ON  ({_HOTKEY_UI})" if new else
            f"⌨  Keyboard mode ON  ({_HOTKEY_UI})")
        if not self.isVisible(): self.show()
        self.raise_()

    @Slot(bool)
    def _on_mouse_toggled(self, checked: bool):
        self._mouse_mode = checked
        self._dwell.reset()
        self._kb.set_highlight(None, 0.0)
        if checked:
            self._status.setText("Mouse mode")
            self._status.setStyleSheet(
                "color:#00c8a0;font-size:9px;font-family:Consolas;")
        else:
            self._status.setText("Ready" if self._kb_enabled else "Calibrate first")
            col = "#2c4436" if self._kb_enabled else "#4c3c20"
            self._status.setStyleSheet(
                f"color:{col};font-size:9px;font-family:Consolas;")

    @Slot()
    def _on_lang_cycle(self):
        self._lang_idx = (self._lang_idx + 1) % len(_LANGS)
        label, layout_key, rtl = _LANGS[self._lang_idx]
        self._btn_lang.setText(label)
        try:
            from app.ui.layouts import LAYOUTS
            layout = LAYOUTS.get(layout_key)
            if layout and hasattr(self._kb, 'set_layout'):
                self._kb.set_layout(layout)
        except Exception:
            pass
        try:
            self._text.set_rtl(rtl)
        except Exception:
            pass
        self._toast.show_message(f"⌨  Layout: {label}")

    @Slot(int)
    def _on_dwell_changed(self, val: int):
        self._dwell.dwell_time_ms = val
        self._dwell_val.setText(f"{val}ms")

    @Slot()
    def _on_export(self):
        if not getattr(self._session, 'events', None):
            self._toast.show_message("💾  Nothing to export yet"); return
        try:
            from app.services.text_services import export_session
            path = export_session(self._session)
            self._toast.show_message(f"💾  Saved: {path.name}")
        except Exception as e:
            self._toast.show_message(f"Export error: {e}")

    @Slot(str)
    def _on_suggestion_selected(self, word: str):
        try:
            cursor = self._text.textCursor()
            words  = self._text.toPlainText().split(" ")
            if not words: return
            cursor.movePosition(cursor.MoveOperation.Left,
                                 cursor.MoveMode.KeepAnchor, len(words[-1]))
            cursor.insertText(word + " ")
            self._text.setTextCursor(cursor)
            self._suggestion_panel.clear_suggestions()
        except Exception:
            pass

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def closeEvent(self, ev):
        self._timer.stop()
        self._hotkey.stop()
        try: self._gaze.stop()
        except Exception: pass
        super().closeEvent(ev)