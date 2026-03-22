"""
app/ui/panels.py
─────────────────────────────────────────────────────────────────────────────
Merged UI panels module.  Contains three self-contained overlay widgets:

  SettingsPanel    — keyboard settings (dwell, language, toggles)
  MousePanel       — mouse-mode settings (smoothing, sensitivity, dead zone…)
  SuggestionPanel  — gaze-dwell word / emoji suggestion strip

Why these three are together
────────────────────────────
All three share the same dark colour palette, have no cross-dependencies on
vision or calibration modules, and are imported together by main_window.py.
Merging them removes three file lookups at import time and keeps the palette
constants in one place.

CalibrationPanel is intentionally NOT merged here — it depends on cv2, numpy,
and calibration services that would add unnecessary overhead to every importer
of this file.

Public API — unchanged from original separate files:

    from app.ui.panels import SettingsPanel, MousePanel, SuggestionPanel

Each class's signals, public methods, and constructor signatures are
identical to the originals.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal, Slot
from PySide6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient,
    QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from app.services.stores import load_settings, save_settings

# ══════════════════════════════════════════════════════════════════════════════
#  Shared palette
#  (all three panels use the same dark theme — defined once here)
# ══════════════════════════════════════════════════════════════════════════════

_BG      = "#0c0c10"
_CARD    = "#121218"
_BORDER  = "#1e1e2a"
_TEAL    = "#00c8a0"
_TEXT    = "#8090b0"
_BRIGHT  = "#c0cce0"
_MUTED   = "#2a3040"
_MUTED2  = "#252535"   # used by MousePanel section labels

# Shared QColor versions used by SuggestionPanel
_C_BG         = QColor(5, 5, 7)
_C_SEP        = QColor(18, 20, 26)
_C_CHIP_WORD  = QColor(18, 20, 24)
_C_CHIP_EMOJI = QColor(14, 16, 20)
_C_CHIP_HOV   = QColor(10, 28, 24)
_C_BORDER_N   = QColor(28, 32, 42)
_C_BORDER_H   = QColor(0, 160, 128)
_C_TEXT_WORD  = QColor(160, 175, 210)
_C_TEXT_EMOJI = QColor(210, 210, 210)
_C_TEXT_HOV   = QColor(0, 230, 185)
_C_TEXT_EMPTY = QColor(35, 42, 58)
_C_DWELL_A    = QColor(0, 100, 80, 50)
_C_DWELL_B    = QColor(0, 200, 160, 110)
_C_BADGE_AI   = QColor(0, 180, 140)
_C_BADGE_LOC  = QColor(60, 80, 120)

# ── shared helpers ────────────────────────────────────────────────────────────

def _section_label(text: str, muted_color: str = _MUTED) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color:{muted_color};font-size:8px;letter-spacing:2px;"
        "font-family:Consolas;background:transparent;border:none;"
    )
    return lbl


def _separator_widget() -> QWidget:
    sep = QWidget()
    sep.setFixedHeight(1)
    sep.setStyleSheet(f"background:{_BORDER};border:none;")
    return sep


def _toggle_button(label: str) -> QPushButton:
    b = QPushButton(label)
    b.setCheckable(True)
    b.setFixedHeight(26)
    b.setStyleSheet(f"""
        QPushButton {{
            background: #16161e; color: {_TEXT};
            border: 1px solid {_BORDER}; border-radius: 4px;
            font-size: 9px; font-family: Consolas; padding: 0 10px;
        }}
        QPushButton:checked {{
            background: #06140f; color: {_TEAL}; border-color: {_TEAL}40;
        }}
        QPushButton:hover {{ background: #1a1a24; }}
    """)
    return b


def _close_button() -> QPushButton:
    b = QPushButton("✕")
    b.setFixedSize(20, 20)
    b.setStyleSheet(
        "QPushButton{background:#1a0a0e;color:#3c1822;border:1px solid #220e18;"
        "border-radius:4px;font-size:9px;}"
        "QPushButton:hover{color:#d02840;background:#2c0c18;}"
    )
    return b


def _rounded_card_paint(widget: QWidget, bg: str = _BG) -> None:
    """Call from paintEvent to draw a rounded card background."""
    p = QPainter(widget)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, widget.width(), widget.height(), 8, 8)
    p.fillPath(path, QBrush(QColor(bg)))
    p.end()


# ══════════════════════════════════════════════════════════════════════════════
#  SettingsPanel
# ══════════════════════════════════════════════════════════════════════════════

# Language / layout registry
_LANG_ENTRIES: list[tuple[str, str, str, bool]] = [
    ("EN — QWERTY", "QWERTY", "en", False),
    ("AR — عربي",   "عربي",   "ar", True),
]


class SettingsPanel(QWidget):
    """
    Overlay settings panel for keyboard mode.
    Call show_panel() / hide_panel() to toggle.

    Signals
    ───────
    settings_changed(dict)  — emitted on any change; dict matches settings_store keys.
    """

    settings_changed = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cfg = load_settings()
        self.setStyleSheet(
            f"background:{_BG};border:1px solid {_BORDER};border-radius:8px;")
        self.setFixedWidth(320)
        self._build_ui()
        self._load_into_ui()
        self.hide()

    # ── build ─────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(14, 10, 14, 14)
        vbox.setSpacing(8)

        # header
        hdr = QHBoxLayout()
        title = QLabel("⚙  Settings")
        title.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        title.setStyleSheet(f"color:{_BRIGHT};background:transparent;border:none;")
        hdr.addWidget(title)
        hdr.addStretch()
        close = _close_button()
        close.clicked.connect(self.hide_panel)
        hdr.addWidget(close)
        vbox.addLayout(hdr)
        vbox.addWidget(_separator_widget())

        # dwell time
        vbox.addWidget(_section_label("DWELL TIME"))
        dw_row = QHBoxLayout()
        dw_row.setSpacing(8)
        self._dwell_slider = QSlider(Qt.Orientation.Horizontal)
        self._dwell_slider.setRange(300, 2000)
        self._dwell_slider.setFixedHeight(16)
        self._dwell_slider.setStyleSheet("""
            QSlider::groove:horizontal{height:2px;background:#111118;border-radius:1px;}
            QSlider::handle:horizontal{width:12px;height:12px;margin:-5px 0;
                background:#00c8a0;border-radius:6px;}
            QSlider::sub-page:horizontal{background:#00c8a040;border-radius:1px;}
        """)
        self._dwell_slider.valueChanged.connect(self._on_dwell)
        dw_row.addWidget(self._dwell_slider, 1)
        self._dwell_lbl = QLabel("800ms")
        self._dwell_lbl.setFixedWidth(46)
        self._dwell_lbl.setStyleSheet(
            f"color:{_TEXT};font-size:9px;font-family:Consolas;"
            "background:transparent;border:none;")
        dw_row.addWidget(self._dwell_lbl)
        vbox.addLayout(dw_row)
        vbox.addWidget(_separator_widget())

        # language / layout
        vbox.addWidget(_section_label("LANGUAGE & LAYOUT"))
        self._lang_btns: list[QPushButton] = []
        for i, (label, _, _, _) in enumerate(_LANG_ENTRIES):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setFixedHeight(28)
            b.setStyleSheet(f"""
                QPushButton {{
                    background: #16161e; color: {_TEXT};
                    border: 1px solid {_BORDER}; border-radius: 4px;
                    font-size: 10px; font-family: Consolas; text-align: left;
                    padding: 0 10px;
                }}
                QPushButton:checked {{
                    background: #06140f; color: {_TEAL}; border-color: {_TEAL}40;
                }}
                QPushButton:hover {{ background: #1a1a24; }}
            """)
            b.clicked.connect(lambda checked, ix=i: self._on_lang(ix))
            vbox.addWidget(b)
            self._lang_btns.append(b)
        vbox.addWidget(_separator_widget())

        # toggles
        vbox.addWidget(_section_label("OPTIONS"))
        self._btn_suggestions = _toggle_button("💡  Suggestions")
        self._btn_suggestions.toggled.connect(
            lambda v: self._set("suggestions_on", v))
        vbox.addWidget(self._btn_suggestions)

        self._btn_landmarks = _toggle_button("🔵  Debug landmarks")
        self._btn_landmarks.toggled.connect(
            lambda v: self._set("debug_landmarks", v))
        vbox.addWidget(self._btn_landmarks)

        self._btn_camera = _toggle_button("📷  Floating camera")
        self._btn_camera.toggled.connect(
            lambda v: self._set("floating_camera", v))
        vbox.addWidget(self._btn_camera)

    def _load_into_ui(self) -> None:
        self._dwell_slider.setValue(self._cfg.get("dwell_ms", 800))
        self._dwell_lbl.setText(f"{self._cfg.get('dwell_ms', 800)}ms")
        lang_idx = self._cfg.get("lang_idx", 0)
        for i, b in enumerate(self._lang_btns):
            b.setChecked(i == lang_idx)
        self._btn_suggestions.setChecked(self._cfg.get("suggestions_on", True))
        self._btn_landmarks.setChecked(self._cfg.get("debug_landmarks", True))
        self._btn_camera.setChecked(self._cfg.get("floating_camera", True))

    # ── handlers ──────────────────────────────────────────────────────────────

    @Slot(int)
    def _on_dwell(self, val: int) -> None:
        self._dwell_lbl.setText(f"{val}ms")
        self._set("dwell_ms", val)

    def _on_lang(self, idx: int) -> None:
        for i, b in enumerate(self._lang_btns):
            b.setChecked(i == idx)
        _, layout_key, lang_code, _ = _LANG_ENTRIES[idx]
        self._cfg["lang_idx"]   = idx
        self._cfg["layout_key"] = layout_key
        self._cfg["lang_code"]  = lang_code
        self._emit()

    def _set(self, key: str, value: Any) -> None:
        self._cfg[key] = value
        self._emit()

    def _emit(self) -> None:
        save_settings(self._cfg)
        self.settings_changed.emit(dict(self._cfg))

    # ── public API ────────────────────────────────────────────────────────────

    def show_panel(self) -> None:
        if self.parent():
            pw = self.parent().width()
            ph = self.parent().height()
            self.move(pw - self.width() - 8, ph - self.height() - 8)
        self.raise_()
        self.show()

    def hide_panel(self) -> None:
        self.hide()

    def get_lang_entry(self, idx: int) -> tuple[str, str, str, bool]:
        return _LANG_ENTRIES[idx % len(_LANG_ENTRIES)]

    @property
    def lang_entries(self) -> list[tuple[str, str, str, bool]]:
        return list(_LANG_ENTRIES)

    def paintEvent(self, _ev) -> None:
        _rounded_card_paint(self)


# ══════════════════════════════════════════════════════════════════════════════
#  SuggestionPanel
# ══════════════════════════════════════════════════════════════════════════════

_DWELL_MS  = 800
_TICK_MS   = 30
_HEIGHT    = 38
_RAD       = 5
_GAP       = 6
_PAD_X     = 12
_PAD_Y     = 5


class SuggestionPanel(QWidget):
    """
    Horizontal strip of gaze-dwell suggestion chips.
    Word chips replace the last typed word when selected.
    Emoji chips append the emoji character.

    Signals
    ───────
    word_selected(str)  — emitted on dwell-select.
    """

    word_selected = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

        self._words:  list[str] = []
        self._emojis: list[str] = []
        self._all:    list[str] = []
        self._source: str       = ""

        self._chip_rects: list[QRectF]  = []
        self._hover_idx:  Optional[int] = None
        self._dwell_ms:   float         = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ── public API ────────────────────────────────────────────────────────────

    def set_words(self, words: list[str], source: str = "local") -> None:
        self._words  = words[:3]
        self._source = source
        self._rebuild()

    def set_emojis(self, emojis: list[str]) -> None:
        self._emojis = emojis[:3]
        self._rebuild()

    def clear_suggestions(self) -> None:
        self._words  = []
        self._emojis = []
        self._source = ""
        self._rebuild()

    def update_gaze(self, gaze_x: float, gaze_y: float) -> None:
        scr = QApplication.primaryScreen()
        if scr is None or not self._all:
            self._hover_idx = None
            return
        geo = scr.geometry()
        lp  = self.mapFromGlobal(
            QPoint(int(gaze_x * geo.width()), int(gaze_y * geo.height()))
        )
        hit = None
        for i, r in enumerate(self._chip_rects):
            if r.contains(float(lp.x()), float(lp.y())):
                hit = i
                break
        if hit != self._hover_idx:
            self._hover_idx = hit
            self._dwell_ms  = 0.0

    # ── internals ─────────────────────────────────────────────────────────────

    def _rebuild(self) -> None:
        self._all       = self._words + self._emojis
        self._hover_idx = None
        self._dwell_ms  = 0.0
        self.update()

    def _tick(self) -> None:
        if self._hover_idx is None:
            if self._dwell_ms != 0.0:
                self._dwell_ms = 0.0
                self.update()
            return
        self._dwell_ms = min(self._dwell_ms + _TICK_MS, _DWELL_MS)
        if self._dwell_ms >= _DWELL_MS:
            idx = self._hover_idx
            if 0 <= idx < len(self._all):
                self.word_selected.emit(self._all[idx])
            self._dwell_ms  = 0.0
            self._hover_idx = None
        self.update()

    # ── painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(0, 0, W, H, QBrush(_C_BG))
        p.setPen(QPen(_C_SEP, 1))
        p.drawLine(0, 0, W, 0)
        p.drawLine(0, H - 1, W, H - 1)
        if not self._all:
            self._draw_empty(p, W, H)
        else:
            self._draw_chips(p, W, H)
        p.end()

    def _draw_empty(self, p: QPainter, W: int, H: int) -> None:
        p.setPen(_C_TEXT_EMPTY)
        p.setFont(QFont("Consolas", 8))
        p.drawText(QRectF(0, 0, W, H), Qt.AlignmentFlag.AlignCenter,
                   "·  ·  ·   type to see suggestions   ·  ·  ·")

    def _draw_chips(self, p: QPainter, W: int, H: int) -> None:
        n_words = len(self._words)
        f_word  = QFont("Segoe UI", 10, QFont.Weight.Medium)
        f_emoji = QFont("Segoe UI Emoji", 12)
        f_badge = QFont("Consolas", 7, QFont.Weight.Bold)

        x_start = 8.0
        if self._source:
            badge_text = "AI ☁" if self._source == "cloud" else "LOCAL"
            badge_col  = _C_BADGE_AI if self._source == "cloud" else _C_BADGE_LOC
            p.setFont(f_badge)
            p.setPen(badge_col)
            fm  = p.fontMetrics()
            bw  = fm.horizontalAdvance(badge_text) + 10
            bh  = H - _PAD_Y * 2
            br  = QRectF(x_start, float(_PAD_Y), bw, bh)
            bp  = QPainterPath()
            bp.addRoundedRect(br, 3, 3)
            p.fillPath(bp, QBrush(QColor(
                badge_col.red(), badge_col.green(), badge_col.blue(), 22)))
            p.setPen(QPen(QColor(
                badge_col.red(), badge_col.green(), badge_col.blue(), 90), 1))
            p.drawPath(bp)
            p.setPen(badge_col)
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, badge_text)
            x_start += bw + _GAP + 4

        if self._source:
            p.setPen(QPen(QColor(25, 30, 40), 1))
            p.drawLine(QPointF(x_start - _GAP / 2, _PAD_Y + 2),
                       QPointF(x_start - _GAP / 2, H - _PAD_Y - 2))

        self._chip_rects = []
        x = x_start

        for i, text in enumerate(self._all):
            is_emoji = i >= n_words
            is_hover = i == self._hover_idx
            progress = (self._dwell_ms / _DWELL_MS) if is_hover else 0.0

            p.setFont(f_emoji if is_emoji else f_word)
            fm     = p.fontMetrics()
            tw     = fm.horizontalAdvance(text)
            chip_w = tw + _PAD_X * 2
            chip_h = float(H - _PAD_Y * 2)
            chip_y = float(_PAD_Y)
            rect   = QRectF(x, chip_y, chip_w, chip_h)
            self._chip_rects.append(rect)

            path = QPainterPath()
            path.addRoundedRect(rect, _RAD, _RAD)

            # background
            p.fillPath(path, QBrush(
                _C_CHIP_HOV if is_hover else (_C_CHIP_EMOJI if is_emoji else _C_CHIP_WORD)))

            # dwell sweep
            if is_hover and progress > 0:
                dg = QLinearGradient(rect.topLeft(), rect.topRight())
                dg.setColorAt(0, _C_DWELL_A)
                dg.setColorAt(1, _C_DWELL_B)
                fill = QRectF(rect.x(), rect.y(),
                              rect.width() * progress, rect.height())
                p.save()
                p.setClipPath(path)
                p.setClipRect(fill, Qt.ClipOperation.IntersectClip)
                p.fillPath(path, QBrush(dg))
                p.restore()

            # border
            p.setPen(QPen(_C_BORDER_H if is_hover else _C_BORDER_N,
                          1.2 if is_hover else 0.8))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

            # top shine
            shine = QLinearGradient(rect.topLeft(),
                                    QPointF(rect.x(), rect.y() + rect.height() * 0.4))
            shine.setColorAt(0, QColor(255, 255, 255, 8))
            shine.setColorAt(1, QColor(255, 255, 255, 0))
            p.save()
            p.setClipPath(path)
            p.fillRect(rect, QBrush(shine))
            p.restore()

            # label
            p.setFont(f_emoji if is_emoji else f_word)
            p.setPen(_C_TEXT_HOV if is_hover else
                     (_C_TEXT_EMOJI if is_emoji else _C_TEXT_WORD))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

            # separator between word and emoji sections
            if i == n_words - 1 and self._emojis:
                sx = x + chip_w + _GAP / 2
                p.setPen(QPen(QColor(25, 30, 40), 1))
                p.drawLine(QPointF(sx, chip_y + 4),
                           QPointF(sx, chip_y + chip_h - 4))

            x += chip_w + _GAP