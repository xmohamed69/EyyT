"""
layouts.py
Keyboard layout definitions for EyeTyper.

Each layout is a dict with:
  name        — display name
  rows        — list of rows, each row is list of (label, face, col, span)
  grid_cols   — total grid column units
  grid_rows   — number of rows
  rtl         — True for right-to-left languages
  font_face   — preferred font for key labels
  lang_code   — BCP-47 language code for spell-checker / AI corrector
  specials    — set of special key labels

Place at: app/ui/layouts.py
"""
from __future__ import annotations

# ── shared specials row ───────────────────────────────────────────────────────
# (label, face, col, span)
_SPECIALS_EN = [
    ("SPACE", "␣  SPACE", 1.5, 3.5),
    ("ENTER", "↵  ENTER", 5.5, 2.0),
    ("CLEAR", "✕  CLR",   8.0, 1.0),
    ("COPY",  "⎘  COPY",  9.0, 1.0),
]

_SPECIALS_AR = [
    ("SPACE", "مسافة",    1.5, 3.5),
    ("ENTER", "↵  دخول",  5.5, 2.0),
    ("CLEAR", "✕  مسح",   8.0, 1.0),
    ("COPY",  "⎘  نسخ",   9.0, 1.0),
]

SPECIAL_LABELS = {"SPACE", "ENTER", "CLEAR", "BKSP", "COPY"}

# ══════════════════════════════════════════════════════════════════════════════
#  QWERTY  (English)
# ══════════════════════════════════════════════════════════════════════════════
QWERTY: dict = {
    "name":      "QWERTY",
    "lang_code": "en-US",
    "rtl":       False,
    "font_face": "Consolas",
    "grid_cols": 10.0,
    "grid_rows": 4,
    "rows": [
        # row 0
        [("Q","Q",0,1),("W","W",1,1),("E","E",2,1),("R","R",3,1),("T","T",4,1),
         ("Y","Y",5,1),("U","U",6,1),("I","I",7,1),("O","O",8,1),("P","P",9,1)],
        # row 1
        [("A","A",.5,1),("S","S",1.5,1),("D","D",2.5,1),("F","F",3.5,1),
         ("G","G",4.5,1),("H","H",5.5,1),("J","J",6.5,1),
         ("K","K",7.5,1),("L","L",8.5,1)],
        # row 2
        [("Z","Z",1,1),("X","X",2,1),("C","C",3,1),("V","V",4,1),
         ("B","B",5,1),("N","N",6,1),("M","M",7,1),("BKSP","⌫",8,2)],
        # row 3
        _SPECIALS_EN,
    ],
}

# ══════════════════════════════════════════════════════════════════════════════
#  ARABIC  (RTL)
# ══════════════════════════════════════════════════════════════════════════════
# Standard Arabic keyboard layout (simplified phone-style, 4 rows)
# Labels are the Arabic characters; faces are the same.
# RTL = True — text output and key order read right to left.
ARABIC: dict = {
    "name":      "عربي",
    "lang_code": "ar",
    "rtl":       True,
    "font_face": "Arial",      # Arial has good Arabic support on Windows
    "grid_cols": 10.0,
    "grid_rows": 4,
    "rows": [
        # row 0 — ض ص ث ق ف غ ع ه خ ح
        [("ض","ض",0,1),("ص","ص",1,1),("ث","ث",2,1),("ق","ق",3,1),
         ("ف","ف",4,1),("غ","غ",5,1),("ع","ع",6,1),("ه","ه",7,1),
         ("خ","خ",8,1),("ح","ح",9,1)],
        # row 1 — ش س ي ب ل ا ت ن م ك
        [("ش","ش",.5,1),("س","س",1.5,1),("ي","ي",2.5,1),("ب","ب",3.5,1),
         ("ل","ل",4.5,1),("ا","ا",5.5,1),("ت","ت",6.5,1),
         ("ن","ن",7.5,1),("م","م",8.5,1)],
        # row 2 — ئ ء ؤ ر لا ى ة و ز ⌫
        [("ئ","ئ",0,1),("ء","ء",1,1),("ؤ","ؤ",2,1),("ر","ر",3,1),
         ("لا","لا",4,1),("ى","ى",5,1),("ة","ة",6,1),
         ("و","و",7,1),("ز","ز",8,1),("BKSP","⌫",9,1)],
        # row 3
        _SPECIALS_AR,
    ],
}

# ── registry ──────────────────────────────────────────────────────────────────
LAYOUTS: dict[str, dict] = {
    "QWERTY": QWERTY,
    "عربي":   ARABIC,
}

LAYOUT_ORDER = ["QWERTY", "عربي"]