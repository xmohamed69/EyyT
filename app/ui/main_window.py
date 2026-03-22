"""
app/ui/main_window.py  —  EyeTyper keyboard window (head navigation mode).

Layout (top → bottom)
──────────────────────
  [1] Toolbar   — ✕  HIDE  |  ⚙  |  LANG  |  DWELL slider  |
                   🎯 CALIBRATE  |  💾  |  👤 CAL  |  ● status  |  debug
  [2] Sep
  [3] Suggestions
  [4] Sep
  [5] Input field
  [6] Sep
  [7] Keyboard  (fills remaining height)

Navigation
──────────
  Head tilt left/right → moves keyboard focus LEFT/RIGHT one key.
  Head tilt up/down    → moves focus UP/DOWN one row.
  Dwell still          → selects the focused key.
  👤 CAL button        → re-calibrates head neutral position.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow,
    QPushButton, QSlider, QVBoxLayout, QWidget,
)

from app.contracts import GazePoint
from app.services.stores import load_settings, save_settings, kb_calib_exists, reset_kb_calib
from app.services.text_services import SessionLog, suggest as word_suggest
from app.services.dwell_selector import DwellSelector
from app.ui.keyboard_widget import KeyboardWidget
from app.ui.panels import SuggestionPanel, SettingsPanel, _LANG_ENTRIES
from app.ui.simple_widgets import TextOutput
from app.ui.keyboard_calibration import KeyboardCalibrationOverlay
from app.vision.head_navigator import HeadNavigator, HeadNavSettings, HeadCommand
from app.ui.keyboard_focus import KeyboardFocusController

_TICK_MS = 30

_LANGS = [
    ("EN",  "QWERTY", "en",  False),
    ("AR",  "عربي",   "ar",  True),
]


# ─────────────────────────────────────────────────────────────────────────────
#  Style helpers
# ─────────────────────────────────────────────────────────────────────────────
_TB_BTN = """
QPushButton {{
    background: {bg};   color: {fg};
    border: 1px solid {bd}; border-radius: 4px;
    padding: 1px {pw}px; font-size: 9px;
    font-family: Consolas; letter-spacing: 0.5px;
    min-width: {mw}px; max-height: 20px;
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

    def show_message(self, msg: str, ms: int = 2200) -> None:
        self.setText(msg); self.adjustSize()
        if self.parent():
            pw, ph = self.parent().width(), self.parent().height()
            w = max(self.width(), 250)
            self.setGeometry((pw - w) // 2, ph - self.height() - 12,
                             w, self.height())
        self.show(); self.raise_(); self._t.start(ms)


# ─────────────────────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):

    def __init__(
        self,
        gaze_provider,
        tracker=None,
        cam_window=None,
        mock_mode: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("EyeTyper")
        self._gaze       = gaze_provider
        self._tracker    = tracker
        self._cam_window = cam_window

        # keyboard settings
        self._cfg        = load_settings()
        self._dwell      = DwellSelector(dwell_time_ms=self._cfg["dwell_ms"])
        self._session    = SessionLog()
        self._kb_enabled = True   # always on; head tracks regardless of eye calib
        self._lang_idx   = self._cfg.get("lang_idx", 0) % len(_LANGS)
        self._lang_code  = _LANGS[self._lang_idx][2]

        self._setup_window()
        self._build_ui()
        self._position_window()
        self._apply_kb_state()
        self._apply_settings(self._cfg, emit=False)

        # ── head navigation ───────────────────────────────────────────────────
        self._head_nav = HeadNavigator(HeadNavSettings(
            max_range = self._cfg.get("head_max_range", 0.06),
            dead_zone = self._cfg.get("head_dead_zone", 0.010),
            ema_alpha = self._cfg.get("head_ema_alpha", 0.18),
            v_scale   = self._cfg.get("head_v_scale",   1.2),
        ))
        # build focus grid from the keyboard widget (built in _build_ui above)
        self._focus_ctrl: Optional[KeyboardFocusController] = None
        self._rebuild_focus_ctrl()

        # auto-calibrate on the first valid nose frame
        self._head_needs_calib: bool = True

        # ── tick timer ────────────────────────────────────────────────────────
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._gaze.start()
        self._timer.start()

    # ── window ────────────────────────────────────────────────────────────────

    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint  |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )

    def _position_window(self) -> None:
        scr = QApplication.primaryScreen()
        if not scr:
            self.resize(1280, 540); return
        avail = scr.availableGeometry()
        win_h = avail.height() // 2
        self.setGeometry(avail.left(), avail.bottom() - win_h,
                         avail.width(), win_h)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QWidget()
        root.setStyleSheet("background:#18181c;")
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        acc = QWidget(); acc.setFixedHeight(1)
        acc.setStyleSheet("background:#00c8a018;")
        outer.addWidget(acc)

        inner = QVBoxLayout()
        inner.setContentsMargins(8, 3, 8, 4)
        inner.setSpacing(0)
        outer.addLayout(inner)

        # ── TOOLBAR ───────────────────────────────────────────────────────────
        tb = QHBoxLayout()
        tb.setSpacing(5)
        tb.setContentsMargins(0, 2, 0, 2)

        self._btn_close = QPushButton("✕")
        self._btn_close.setFixedSize(20, 20)
        self._btn_close.setStyleSheet(
            "QPushButton{background:#180c10;color:#3c1822;border:1px solid #22101a;"
            "border-radius:4px;font-size:10px;}"
            "QPushButton:hover{background:#2c0c18;color:#d02840;border-color:#420c1e;}"
            "QPushButton:pressed{background:#100608;}")
        self._btn_close.clicked.connect(QApplication.quit)
        tb.addWidget(self._btn_close)

        self._btn_hide = _tb_btn("HIDE", mw=32)
        self._btn_hide.clicked.connect(self.hide)
        tb.addWidget(self._btn_hide)

        tb.addWidget(_vline())

        self._btn_settings = _tb_btn("⚙", mw=18)
        self._btn_settings.setToolTip("Open keyboard settings")
        self._btn_settings.clicked.connect(self._on_settings)
        tb.addWidget(self._btn_settings)

        tb.addWidget(_vline())

        lang_label = _LANGS[self._lang_idx][0]
        self._btn_lang = _tb_btn(lang_label, mw=26)
        self._btn_lang.clicked.connect(self._on_lang_cycle)
        tb.addWidget(self._btn_lang)

        tb.addWidget(_vline())

        dw_lbl = QLabel("DWELL")
        dw_lbl.setStyleSheet(
            "color:#1e2030;font-size:8px;letter-spacing:1px;font-family:Consolas;")
        tb.addWidget(dw_lbl)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(300, 2000)
        self._slider.setValue(self._cfg["dwell_ms"])
        self._slider.setFixedWidth(80); self._slider.setFixedHeight(16)
        self._slider.setStyleSheet("""
            QSlider::groove:horizontal{height:2px;background:#111118;border-radius:1px;}
            QSlider::handle:horizontal{width:10px;height:10px;margin:-4px 0;
                background:#00c8a0;border-radius:5px;}
            QSlider::sub-page:horizontal{background:#00c8a040;border-radius:1px;}
        """)
        self._slider.valueChanged.connect(self._on_dwell_changed)
        tb.addWidget(self._slider)
        self._dwell_val = QLabel(f"{self._cfg['dwell_ms']}ms")
        self._dwell_val.setStyleSheet(
            "color:#222234;font-size:8px;font-family:Consolas;min-width:34px;")
        tb.addWidget(self._dwell_val)

        tb.addWidget(_vline())

        self._btn_calib = _tb_btn("🎯 CALIBRATE", mw=80,
                                   bg="#0e1f0e", fg="#00c8a0", bd="#1a3a1a",
                                   hbg="#162a16", hfg="#00e0b0", hbd="#00c8a040")
        self._btn_calib.setToolTip("Run keyboard-fitted eye calibration")
        self._btn_calib.clicked.connect(self._on_calibrate)
        tb.addWidget(self._btn_calib)

        tb.addWidget(_vline())

        # 👤 head neutral calibration button
        self._btn_head_calib = _tb_btn("👤 CAL", mw=52,
                                        bg="#0e160e", fg="#40a060", bd="#1a2a1a",
                                        hbg="#162216", hfg="#60c080", hbd="#40a06040")
        self._btn_head_calib.setToolTip(
            "Calibrate head neutral position\n"
            "Look straight at the screen then click")
        self._btn_head_calib.clicked.connect(self._on_head_calib)
        tb.addWidget(self._btn_head_calib)

        tb.addWidget(_vline())

        self._btn_export = _tb_btn("💾", mw=22)
        self._btn_export.setToolTip("Export session to .txt")
        self._btn_export.clicked.connect(self._on_export)
        tb.addWidget(self._btn_export)

        tb.addWidget(_vline())

        self._dot = QLabel("●")
        self._dot.setStyleSheet("color:#1a1c28;font-size:8px;")
        tb.addWidget(self._dot)
        self._status = QLabel("No calibration")
        self._status.setStyleSheet("color:#2a2c3a;font-size:9px;font-family:Consolas;")
        tb.addWidget(self._status)

        tb.addWidget(_vline())

        # head nav debug label — shows live offset + command in toolbar
        self._head_dbg = QLabel("👤 —")
        self._head_dbg.setStyleSheet(
            "color:#304060;font-size:8px;font-family:Consolas;min-width:160px;")
        tb.addWidget(self._head_dbg)

        tb.addStretch(1)
        inner.addLayout(tb)
        inner.addWidget(_hline())

        # ── SUGGESTION BAR ────────────────────────────────────────────────────
        self._suggestion_panel = SuggestionPanel()
        self._suggestion_panel.setFixedHeight(36)
        self._suggestion_panel.word_selected.connect(self._on_suggestion_selected)
        inner.addWidget(self._suggestion_panel)
        inner.addWidget(_hline())

        # ── TEXT OUTPUT ───────────────────────────────────────────────────────
        self._text = TextOutput()
        self._text.setFixedHeight(52)
        self._text.setStyleSheet("""
            QTextEdit {
                background: #0e0e12; color: #d0d8f0;
                border: 1px solid #1e1e28; border-radius: 4px;
                padding: 4px 10px; font-family: Consolas;
                font-size: 15px; selection-background-color: #00c8a030;
            }
        """)
        inner.addWidget(self._text)
        inner.addSpacing(2)
        inner.addWidget(_hline())

        # ── KEYBOARD ──────────────────────────────────────────────────────────
        self._kb = KeyboardWidget()
        self._kb.setMinimumHeight(160)
        inner.addWidget(self._kb, 1)

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
        self._overlay.setParent(self._kb); self._overlay.hide()

        self._settings_panel = SettingsPanel(root)
        self._settings_panel.settings_changed.connect(self._on_settings_changed)

        self._kb_cal: Optional[KeyboardCalibrationOverlay] = None
        self._toast = _Toast(root)

    # ── state helpers ─────────────────────────────────────────────────────────

    def _apply_kb_state(self) -> None:
        if self._kb_enabled:
            self._dot.setStyleSheet("color:#00c8a0;font-size:8px;")
            self._status.setText("Ready")
            self._status.setStyleSheet("color:#2c4436;font-size:9px;font-family:Consolas;")
            if hasattr(self, "_overlay"): self._overlay.hide()
        else:
            self._dot.setStyleSheet("color:#4c3010;font-size:8px;")
            self._status.setText("Calibrate first")
            self._status.setStyleSheet("color:#4c3c20;font-size:9px;font-family:Consolas;")
            if hasattr(self, "_overlay"): self._overlay.show()

    def _apply_settings(self, cfg: dict, emit: bool = True) -> None:
        dwell = cfg.get("dwell_ms", 800)
        self._dwell.dwell_time_ms = dwell
        self._slider.setValue(dwell)
        self._dwell_val.setText(f"{dwell}ms")

        lang_idx = cfg.get("lang_idx", 0)
        if lang_idx != self._lang_idx:
            self._switch_lang(lang_idx)

        suggestions_on = cfg.get("suggestions_on", True)
        self._suggestion_panel.setVisible(suggestions_on)

        debug = cfg.get("debug_landmarks", True)
        try:
            import app.vision.mediapipe_tracker as _mt
            _mt.SHOW_DEBUG_LANDMARKS = debug
        except Exception:
            pass

        cam_on = cfg.get("floating_camera", True)
        if self._cam_window is not None:
            self._cam_window.setVisible(cam_on)

    def _switch_lang(self, idx: int) -> None:
        label, layout_key, lang_code, rtl = _LANGS[idx % len(_LANGS)]
        self._btn_lang.setText(label)
        self._lang_idx  = idx
        self._lang_code = lang_code
        from app.ui.layouts import LAYOUTS, SPECIAL_LABELS
        layout = LAYOUTS.get(layout_key)
        if layout is not None:
            lyt = dict(layout)
            lyt["specials"] = SPECIAL_LABELS
            self._kb.set_layout(lyt)
        try:
            self._text.set_rtl(rtl)
        except Exception:
            pass
        # rebuild focus grid for new layout
        self._rebuild_focus_ctrl()
        self._dwell.reset()

    def _rebuild_focus_ctrl(self) -> None:
        """Build/rebuild KeyboardFocusController from the current keyboard layout."""
        rtl = _LANGS[self._lang_idx][3] if self._lang_idx < len(_LANGS) else False
        try:
            self._focus_ctrl = KeyboardFocusController.from_keyboard_widget(
                self._kb, rtl=rtl)
        except Exception as e:
            print(f"[HeadNav] focus grid build failed: {e}")
            self._focus_ctrl = None

    # ── resize ────────────────────────────────────────────────────────────────

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        if hasattr(self, "_overlay") and hasattr(self, "_kb"):
            self._overlay.setGeometry(self._kb.rect())

    # ── head navigation helpers ────────────────────────────────────────────────

    @Slot()
    def _on_head_calib(self) -> None:
        """Manually calibrate head neutral — look straight at screen first."""
        if hasattr(self._tracker, "get_nose_position"):
            nose = self._tracker.get_nose_position()
            if nose is None:
                self._toast.show_message("👤 No face detected — try again", ms=1500)
                return
            nx, ny = nose
            self._head_nav.calibrate(nx, ny)
            self._head_needs_calib = False
            self._toast.show_message("👤 Head neutral calibrated")
        else:
            self._toast.show_message("👤 Head tracking not available")

    # ── tick ─────────────────────────────────────────────────────────────────

    @Slot()
    def _tick(self) -> None:
        if self._focus_ctrl is None:
            return

        # ── face-presence check — MUST come first ─────────────────────────────
        # get_nose_position() returns None when no face is detected.
        # When face is absent: clear highlight, reset dwell, stop everything.
        nose = None
        if hasattr(self._tracker, "get_nose_position"):
            nose = self._tracker.get_nose_position()   # None or (x, y)

        if nose is None:
            # No face → freeze highlight off, reset dwell, show status
            self._kb.set_highlight(None, 0.0)
            self._dwell.reset()
            self._head_dbg.setText("👤 NO FACE")
            return

        nx, ny = nose

        # ── auto-calibrate head neutral on first real nose frame ───────────────
        if self._head_needs_calib:
            self._head_nav.calibrate(nx, ny)
            self._head_needs_calib = False
            print(f"[HeadNav] Calibrated neutral: ({nx:.4f}, {ny:.4f})")

        # ── continuous head tracking ───────────────────────────────────────────
        hx, hy, dbg = self._head_nav.get_position(nx, ny)

        # update focused key — resets dwell whenever key changes
        prev_label = self._focus_ctrl.focused_label
        self._focus_ctrl.focus_at(hx, hy)
        new_label  = self._focus_ctrl.focused_label

        if new_label != prev_label:
            self._dwell.reset()

        # live debug in toolbar
        cal_sym = "✓" if dbg.calibrated else "?"
        self._head_dbg.setText(
            f"👤{cal_sym} "
            f"dx={dbg.smooth_dx:+.3f} dy={dbg.smooth_dy:+.3f} "
            f"hx={hx:.2f} hy={hy:.2f} [{new_label}]"
        )

        # ── keyboard gate — dwell/select only when kb enabled ─────────────────
        if not self._kb_enabled:
            self._kb.set_highlight(new_label, 0.0)
            return

        # ── dwell on focused key ───────────────────────────────────────────────
        result_key, prog = self._dwell.update(new_label)
        self._kb.set_highlight(new_label, prog)

        if result_key:
            self._kb.flash_key(result_key)
            self._text.append_key(result_key)
            self._session.log_key(result_key, 0.5, 0.5,
                                  int(self._dwell.dwell_time_ms))
            self._dot.setStyleSheet("color:#00c8a0;font-size:8px;")
            QTimer.singleShot(260, lambda: self._dot.setStyleSheet(
                "color:#1a1c28;font-size:8px;"))
            self._update_suggestions()

    # ── suggestions ───────────────────────────────────────────────────────────

    def _update_suggestions(self) -> None:
        if not self._cfg.get("suggestions_on", True):
            return
        try:
            text = self._text.toPlainText()
            last = text.split()[-1] if text.split() else ""
            if not last:
                self._suggestion_panel.clear_suggestions(); return
            words = word_suggest(last, self._lang_code, n=3)
            self._suggestion_panel.set_words(words, source="local")
        except Exception:
            pass

    # ── slots ─────────────────────────────────────────────────────────────────

    @Slot()
    def _on_calibrate(self) -> None:
        if self._kb_cal is not None and self._kb_cal.isVisible():
            return
        self._timer.stop()
        self._kb_cal = KeyboardCalibrationOverlay(
            kb_widget=self._kb,
            tracker=self._tracker,
            parent=self._kb,
        )
        self._kb_cal.finished.connect(self._on_kb_calib_done)
        self._kb_cal.cancelled.connect(self._on_kb_calib_cancelled)
        self._kb_cal.begin()

    @Slot(float)
    def _on_kb_calib_done(self, mean_err: float) -> None:
        self._timer.start()
        self._kb_enabled = True
        self._apply_kb_state()
        err_px  = mean_err * max(self._kb.width(), 1)
        quality = "Excellent" if err_px < 15 else "Good" if err_px < 30 else "Fair"
        self._toast.show_message(
            f"✅  Keyboard calibrated!  Error: {err_px:.1f} px  ({quality})",
            ms=3000)
        # rebuild focus grid in case layout changed during calibration
        self._rebuild_focus_ctrl()

    @Slot()
    def _on_kb_calib_cancelled(self) -> None:
        self._timer.start()
        self._toast.show_message("⚠  Calibration cancelled", ms=1500)

    @Slot()
    def _on_settings(self) -> None:
        if self._settings_panel.isVisible():
            self._settings_panel.hide_panel()
        else:
            self._settings_panel.show_panel()

    @Slot(dict)
    def _on_settings_changed(self, cfg: dict) -> None:
        self._cfg = cfg
        self._apply_settings(cfg, emit=False)

    @Slot()
    def _on_lang_cycle(self) -> None:
        new_idx = (self._lang_idx + 1) % len(_LANGS)
        self._switch_lang(new_idx)
        self._cfg["lang_idx"]   = new_idx
        self._cfg["layout_key"] = _LANGS[new_idx][1]
        self._cfg["lang_code"]  = _LANGS[new_idx][2]
        save_settings(self._cfg)
        self._toast.show_message(f"⌨  Layout: {_LANGS[new_idx][0]}")

    @Slot(int)
    def _on_dwell_changed(self, val: int) -> None:
        self._dwell.dwell_time_ms = val
        self._dwell_val.setText(f"{val}ms")
        self._cfg["dwell_ms"] = val
        save_settings(self._cfg)

    @Slot()
    def _on_export(self) -> None:
        if not getattr(self._session, "events", None):
            self._toast.show_message("💾  Nothing to export yet"); return
        try:
            from app.services.text_services import export_session
            path = export_session(self._session)
            self._toast.show_message(f"💾  Saved: {path.name}")
        except Exception as e:
            self._toast.show_message(f"Export error: {e}")

    @Slot(str)
    def _on_suggestion_selected(self, word: str) -> None:
        try:
            cursor = self._text.textCursor()
            plain  = self._text.toPlainText()
            words  = plain.split(" ")
            if not words: return
            last_len = len(words[-1])
            if last_len:
                cursor.movePosition(cursor.MoveOperation.Left,
                                     cursor.MoveMode.KeepAnchor, last_len)
            cursor.insertText(word + " ")
            self._text.setTextCursor(cursor)
            self._suggestion_panel.clear_suggestions()
        except Exception:
            pass

    # ── external wiring ───────────────────────────────────────────────────────

    def set_camera_window(self, cam_window) -> None:
        self._cam_window = cam_window
        cam_on = self._cfg.get("floating_camera", True)
        cam_window.setVisible(cam_on)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def closeEvent(self, ev) -> None:
        self._timer.stop()
        try:
            self._gaze.stop()
        except Exception:
            pass
        super().closeEvent(ev)