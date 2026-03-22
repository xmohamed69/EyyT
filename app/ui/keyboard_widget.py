"""
keyboard_widget.py — Windows 11 touch-keyboard style, 5 rows.

Row layout
──────────
  Row 0  [numbers]   1  2  3  4  5  6  7  8  9  0   ⌫
  Row 1  [qwerty]    q  w  e  r  t  y  u  i  o  p
  Row 2  [asdf]       a  s  d  f  g  h  j  k  l      ↵
  Row 3  [zxcv]     ↑  z  x  c  v  b  n  m  ,  .  ↑
  Row 4  [action]   @  #  !     SPACE      :  ;  _  CLR

Grid: 11 columns × 5 rows
Special keys: BKSP ENTER LSHIFT RSHIFT SPACE CLEAR
              and all symbol keys in row 4

set_layout(dict) still works for language switching — when an external
layout is loaded its own grid_cols/grid_rows are used instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient,
    QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import QApplication, QWidget


# ─────────────────────────────────────────────────────────────────────────────
#  Data model
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class _Key:
    label:   str
    face:    str
    row:     int
    col:     float
    span:    float
    special: bool   = False
    num_row: bool   = False   # True → number/symbol row (slightly smaller font)
    rect:    QRectF = field(default_factory=QRectF)


# ─────────────────────────────────────────────────────────────────────────────
#  Default 5-row QWERTY layout
#  Format: (label, face, col, span)
#  Grid columns = 11, grid rows = 5
# ─────────────────────────────────────────────────────────────────────────────
_GC_DEFAULT = 11.0
_GR_DEFAULT = 5

_DEFAULT_ROWS: list[list[tuple]] = [

    # ── Row 0 — numbers + backspace ──────────────────────────────────────────
    # 10 number keys × 1 unit = 10, BKSP = 1 unit → total 11
    [("NUM1","1", 0.0, 1.0), ("NUM2","2", 1.0, 1.0), ("NUM3","3", 2.0, 1.0),
     ("NUM4","4", 3.0, 1.0), ("NUM5","5", 4.0, 1.0), ("NUM6","6", 5.0, 1.0),
     ("NUM7","7", 6.0, 1.0), ("NUM8","8", 7.0, 1.0), ("NUM9","9", 8.0, 1.0),
     ("NUM0","0", 9.0, 1.0),
     ("BKSP","⌫",10.0, 1.0)],

    # ── Row 1 — Q…P (no offset, 10 keys, cols 0-9, col 10 empty) ─────────────
    [("Q","q", 0.0,1.0),("W","w",1.0,1.0),("E","e",2.0,1.0),
     ("R","r", 3.0,1.0),("T","t",4.0,1.0),("Y","y",5.0,1.0),
     ("U","u", 6.0,1.0),("I","i",7.0,1.0),("O","o",8.0,1.0),
     ("P","p", 9.0,2.0)],   # P spans 2 to fill right edge

    # ── Row 2 — A…L + Enter ──────────────────────────────────────────────────
    # 9 keys with 0.5 offset, ENTER fills right
    [("A","a", 0.5,1.0),("S","s",1.5,1.0),("D","d",2.5,1.0),
     ("F","f", 3.5,1.0),("G","g",4.5,1.0),("H","h",5.5,1.0),
     ("J","j", 6.5,1.0),("K","k",7.5,1.0),("L","l",8.5,1.0),
     ("ENTER","↵",9.5,1.5)],

    # ── Row 3 — Shift + Z…M + , . + Shift ────────────────────────────────────
    [("LSHIFT","↑", 0.0,1.5),
     ("Z","z", 1.5,1.0),("X","x",2.5,1.0),("C","c",3.5,1.0),
     ("V","v", 4.5,1.0),("B","b",5.5,1.0),("N","n",6.5,1.0),
     ("M","m", 7.5,1.0),
     ("COMMA",",",8.5,0.75),("DOT",".",9.25,0.75),
     ("RSHIFT","↑",10.0,1.0)],

    # ── Row 4 — symbols + space + clear ──────────────────────────────────────
    # @(1) #(1) !(1) SPACE(4.5) :(1) ;(1) _(1) CLR(1.5) = 11
    [("SYM_AT",  "@", 0.0, 1.0),
     ("SYM_HASH","#", 1.0, 1.0),
     ("SYM_EXCL","!", 2.0, 1.0),
     ("SPACE",   " ", 3.0, 4.5),
     ("SYM_COLON",":",7.5, 1.0),
     ("SYM_SEMI", ";",8.5, 1.0),
     ("SYM_UNDER","_",9.5, 0.75),
     ("CLEAR",  "CLR",10.25,0.75)],
]

_DEFAULT_SPEC: set[str] = {
    "BKSP", "ENTER", "LSHIFT", "RSHIFT", "SPACE", "CLEAR", "COPY",
    "COMMA", "DOT",
    "NUM1","NUM2","NUM3","NUM4","NUM5",
    "NUM6","NUM7","NUM8","NUM9","NUM0",
    "SYM_AT","SYM_HASH","SYM_EXCL",
    "SYM_COLON","SYM_SEMI","SYM_UNDER",
}

# Which rows are "number/symbol" rows → slightly smaller label font
_NUM_ROWS: set[int] = {0, 4}


# ─────────────────────────────────────────────────────────────────────────────
#  Palette  — Win11 touch keyboard
# ─────────────────────────────────────────────────────────────────────────────
_PLATE      = QColor( 28,  28,  32)   # keyboard background
_KEY_LETTER = QColor( 70,  70,  76)   # normal letter key
_KEY_NUM    = QColor( 58,  58,  64)   # number row key (slightly darker)
_KEY_SPEC   = QColor( 48,  48,  54)   # action/special key
_KEY_HOV    = QColor( 92,  94, 106)   # hovered
_KEY_FL     = QColor(  0, 145, 115)   # flash (teal)
_KEY_COPY   = QColor( 20,  80, 160)   # COPY key — distinct blue
_KEY_COPY_FL= QColor( 60, 180, 255)   # COPY key flash — bright cyan

_TXT_LETTER = QColor(245, 245, 250)   # letter label
_TXT_NUM    = QColor(220, 225, 240)   # number/symbol label
_TXT_SPEC   = QColor(185, 190, 210)   # special key label
_TXT_HOV    = QColor(255, 255, 255)
_TXT_FL     = QColor(255, 255, 255)

_DW_L       = QColor(  0, 165, 130,  55)
_DW_R       = QColor(  0, 215, 170, 135)


# ─────────────────────────────────────────────────────────────────────────────
class KeyboardWidget(QWidget):
    key_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(200)

        self._gc: float      = _GC_DEFAULT
        self._gr: int        = _GR_DEFAULT
        self._keys:          list[_Key]    = []
        self._specials:      set[str]      = set(_DEFAULT_SPEC)
        self._hover_label:   Optional[str] = None
        self._dwell_pct:     float         = 0.0
        self._flash_label:   Optional[str] = None
        self._font_face:     str           = "Segoe UI"

        # Magnetic cursor state — tracks the currently snapped key
        # and requires a 'breakout' movement to switch to a different key.
        self._magnet_label:    Optional[str]   = None   # currently snapped key
        self._magnet_strength: float           = 0.35   # snap radius (fraction of key size)
        self._breakout_factor: float           = 1.6    # must travel this × snap_radius to escape

        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.setInterval(160)
        self._flash_timer.timeout.connect(self._end_flash)

        self._load_rows(_DEFAULT_ROWS, _GC_DEFAULT, _GR_DEFAULT, _DEFAULT_SPEC)

    # ── Loading ───────────────────────────────────────────────────────────────

    def _load_rows(self, rows: list, gc: float, gr: int, specials: set) -> None:
        self._gc       = gc
        self._gr       = gr
        self._specials = set(specials)
        self._keys     = []
        for ri, row in enumerate(rows):
            for label, face, col, span in row:
                self._keys.append(_Key(
                    label   = label,
                    face    = face,
                    row     = ri,
                    col     = col,
                    span    = span,
                    special = label in specials,
                    num_row = ri in _NUM_ROWS,
                ))
        self._recalc()
        self.update()

    def set_layout(self, layout: dict) -> None:
        """Load a layout dict from app/ui/layouts.py."""
        try:
            rows      = layout["rows"]
            gc        = float(layout.get("grid_cols", _GC_DEFAULT))
            gr        = int(  layout.get("grid_rows", _GR_DEFAULT))
            specials  = set(  layout.get("specials",  _DEFAULT_SPEC))
            self._font_face = layout.get("font_face", "Segoe UI")
            self._load_rows(rows, gc, gr, specials)
        except Exception:
            import traceback; traceback.print_exc()
            self._font_face = "Segoe UI"
            self._load_rows(_DEFAULT_ROWS, _GC_DEFAULT, _GR_DEFAULT, _DEFAULT_SPEC)

    # ── Geometry ──────────────────────────────────────────────────────────────

    def _recalc(self) -> None:
        if self._gc <= 0 or self._gr <= 0:
            return
        GAP = 3
        MX  = 5
        MY  = 3
        W   = max(1, self.width()  - 2 * MX)
        H   = max(1, self.height() - 2 * MY)
        cu  = W / self._gc
        ru  = H / self._gr
        for k in self._keys:
            k.rect = QRectF(
                MX + k.col * cu + GAP * 0.5,
                MY + k.row * ru + GAP * 0.5,
                k.span * cu - GAP,
                ru          - GAP,
            )

    # ── Public API ────────────────────────────────────────────────────────────

    def get_key_at_gaze(self, gx: float, gy: float) -> Optional[str]:
        scr = QApplication.primaryScreen()
        if not scr:
            return None
        g  = scr.geometry()
        # Use float QPointF — avoids the integer truncation that caused
        # keys to be skipped when the cursor landed between two pixel cols.
        # mapFromGlobal accepts QPoint (int), so we round rather than truncate.
        from PySide6.QtCore import QPoint as _QPoint
        lp = QPointF(self.mapFromGlobal(
            _QPoint(round(gx * g.width()), round(gy * g.height()))
        ))
        for k in self._keys:
            if k.rect.contains(lp):
                return k.label
        return None

    def set_highlight(self, label: Optional[str], progress: float) -> None:
        if label != self._hover_label or abs(progress - self._dwell_pct) > 0.005:
            self._hover_label = label
            self._dwell_pct   = progress
            self.update()

    def flash_key(self, label: str) -> None:
        self._flash_label = label
        self._flash_timer.start()
        self.update()

    def get_key_at_pixel(self, px: float, py: float) -> Optional[str]:
        """
        Return the key label at widget-local pixel coordinates (px, py).
 
        This is used by the keyboard-calibration path in main_window._tick()
        where the tracker's map_to_kb() already converts iris coords into
        keyboard widget pixel space — no screen mapping needed.
 
        px, py : pixel position within THIS widget (not screen coords).
                 e.g. (120, 45) for the Q key on a 1280-wide keyboard.
        """
        pt = QPointF(px, py)
        for k in self._keys:
            if k.rect.contains(pt):
                return k.label
        return None
 

    # ── Qt ────────────────────────────────────────────────────────────────────

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._recalc()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QBrush(_PLATE))

        if not self._keys:
            p.end(); return

        # Scale fonts to key height
        kh       = max(1.0, (self.height() - 6) / self._gr)
        lbl_pt   = max(9,  int(kh * 0.40))   # letter keys
        num_pt   = max(8,  int(kh * 0.34))   # number/symbol row
        sp_pt    = max(7,  int(kh * 0.28))   # action keys (ENTER, SPACE…)

        f_lbl = QFont(self._font_face, lbl_pt, QFont.Weight.Medium)
        f_num = QFont(self._font_face, num_pt, QFont.Weight.Normal)
        f_sp  = QFont(self._font_face, sp_pt,  QFont.Weight.Normal)

        for k in self._keys:
            if k.num_row:
                fn = f_num
            elif k.special:
                fn = f_sp
            else:
                fn = f_lbl
            self._draw_key(p, k, fn)
        p.end()

    def _draw_key(self, p: QPainter, k: _Key, font: QFont) -> None:
        r     = k.rect
        rad   = 5.0
        hover = k.label == self._hover_label
        flash = k.label == self._flash_label

        path = QPainterPath()
        path.addRoundedRect(r, rad, rad)

        # ── fill ──────────────────────────────────────────────────────────────
        is_copy = (k.label == 'COPY')
        if flash and is_copy:
            fill = _KEY_COPY_FL
        elif flash:
            fill = _KEY_FL
        elif hover and is_copy:
            fill = _KEY_COPY
        elif hover:
            fill = _KEY_HOV
        elif is_copy:
            fill = _KEY_COPY
        elif k.special:
            fill = _KEY_SPEC
        elif k.num_row:
            fill = _KEY_NUM
        else:
            fill = _KEY_LETTER
        p.fillPath(path, QBrush(fill))

        # ── dwell sweep ───────────────────────────────────────────────────────
        if hover and self._dwell_pct > 0 and not flash:
            dg = QLinearGradient(r.topLeft(), r.topRight())
            dg.setColorAt(0.0, _DW_L)
            dg.setColorAt(1.0, _DW_R)
            cr = QRectF(r.x(), r.y(), r.width() * self._dwell_pct, r.height())
            p.save()
            p.setClipPath(path)
            p.setClipRect(cr, Qt.ClipOperation.IntersectClip)
            p.fillPath(path, QBrush(dg))
            p.restore()

        # ── top-edge shine ────────────────────────────────────────────────────
        p.setPen(QPen(QColor(255, 255, 255, 16), 1.0))
        p.drawLine(QPointF(r.left() + rad, r.top() + 0.5),
                   QPointF(r.right() - rad, r.top() + 0.5))

        # ── label ─────────────────────────────────────────────────────────────
        if flash:
            tc = _TXT_FL
        elif hover:
            tc = _TXT_HOV
        elif k.label == 'COPY':
            tc = QColor(100, 200, 255)   # bright cyan label, always visible
        elif k.special:
            tc = _TXT_SPEC
        elif k.num_row:
            tc = _TXT_NUM
        else:
            tc = _TXT_LETTER

        p.setPen(tc)
        p.setFont(font)

        if k.label == "SPACE":
            # space bar → white pill indicator
            pw = min(r.width() * 0.25, 46.0)
            ph = 3.0
            p.setBrush(QColor(255, 255, 255, 40 if not hover else 90))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(
                QRectF(r.center().x() - pw / 2,
                       r.center().y() - ph / 2, pw, ph), 2, 2)
        else:
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, k.face)

    def _end_flash(self) -> None:
        self._flash_label = None
        self.update()