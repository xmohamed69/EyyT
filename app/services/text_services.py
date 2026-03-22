"""
app/services/text_services.py
─────────────────────────────────────────────────────────────────────────────
Merged text-pipeline module.  Contains three cooperating services:

  SessionLog / SessionEvent  — in-memory keystroke logger
  export_session()           — writes session to ~/Documents/EyeTyper Exports/
  suggest()                  — offline prefix-based word suggestions (EN/FR/AR)

Why these three are together
────────────────────────────
They form a natural pipeline:
    keystrokes → SessionLog → export_session() → .txt file
    keystrokes → suggest()  → suggestion chips

All three are pure Python (no Qt, no cv2, no network, no OS calls).
session_logger was already a direct dependency of txt_exporter.
word_suggest has no dependencies at all.
Merging them eliminates one inter-module import and keeps all text-handling
logic in one place.

Public API — identical to the originals:

    from app.services.text_services import (
        SessionEvent, SessionLog,   # keystroke logger
        export_session,             # .txt exporter
        suggest, LANGS,             # word suggestions
        copy_to_clipboard,          # copy typed text to OS clipboard
    )
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


# ══════════════════════════════════════════════════════════════════════════════
#  SessionLogger
#  Originally: app/services/session_logger.py
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SessionEvent:
    timestamp: float
    key:       str
    gaze_x:    float
    gaze_y:    float
    dwell_ms:  int


@dataclass
class SessionLog:
    start_time: float             = field(default_factory=time.time)
    events:     list[SessionEvent] = field(default_factory=list)

    def log_key(
        self,
        key:      str,
        gaze_x:   float,
        gaze_y:   float,
        dwell_ms: int,
    ) -> None:
        self.events.append(SessionEvent(
            timestamp=time.time(),
            key=key,
            gaze_x=round(gaze_x, 4),
            gaze_y=round(gaze_y, 4),
            dwell_ms=dwell_ms,
        ))

    @property
    def typed_text(self) -> str:
        """Reconstruct what the user actually typed."""
        buf: list[str] = []
        for ev in self.events:
            if ev.key == "BKSP":
                if buf: buf.pop()
            elif ev.key == "SPACE":
                buf.append(" ")
            elif ev.key == "ENTER":
                buf.append("\n")
            elif ev.key == "CLEAR":
                buf.clear()
            else:
                buf.append(ev.key.lower())
        return "".join(buf)

    def clear(self) -> None:
        self.events.clear()
        self.start_time = time.time()


# ══════════════════════════════════════════════════════════════════════════════
#  TxtExporter
#  Originally: app/services/txt_exporter.py
# ══════════════════════════════════════════════════════════════════════════════

_EXPORTS_DIR = Path.home() / "Documents" / "EyeTyper Exports"


def export_session(session: SessionLog) -> Path:
    """
    Write session to a .txt file in ~/Documents/EyeTyper Exports/.
    Returns the file path.
    """
    _EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = _EXPORTS_DIR / f"session_{ts}.txt"

    start_dt = datetime.fromtimestamp(session.start_time)
    duration = (
        (session.events[-1].timestamp - session.start_time)
        if session.events else 0.0
    )

    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("  EyeTyper — Session Report")
    lines.append("=" * 60)
    lines.append(f"  Date      : {start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"  Duration  : {duration:.1f} s")
    lines.append(f"  Keystrokes: {len(session.events)}")
    lines.append("")
    lines.append("  Typed text:")
    lines.append("  " + "-" * 40)
    for line in session.typed_text.splitlines():
        lines.append(f"  {line}")
    lines.append("")
    lines.append("=" * 60)
    lines.append("  Keystroke log")
    lines.append("=" * 60)
    lines.append(
        f"  {'#':<5} {'Time':<12} {'Key':<10} {'Gaze X':<10} {'Gaze Y':<10} {'Dwell ms'}")
    lines.append("  " + "-" * 56)

    for i, ev in enumerate(session.events, 1):
        t = datetime.fromtimestamp(ev.timestamp).strftime("%H:%M:%S.%f")[:-3]
        lines.append(
            f"  {i:<5} {t:<12} {ev.key:<10} "
            f"{ev.gaze_x:<10.4f} {ev.gaze_y:<10.4f} {ev.dwell_ms}"
        )

    lines.append("")
    lines.append("=" * 60)

    filepath.write_text("\n".join(lines), encoding="utf-8")
    return filepath


def copy_to_clipboard(text: str) -> bool:
    """
    Copy *text* to the OS clipboard.  Returns True on success.

    Strategy (in priority order, no extra dependencies):
    1. Qt QApplication.clipboard() — works if a QApplication exists.
       This is always true when called from main_window._on_copy().
    2. ctypes WinAPI SetClipboardData — Windows-only zero-dep fallback.
    3. subprocess xclip / xdotool — Linux fallback.
    4. subprocess pbcopy — macOS fallback.

    The Qt path is almost always taken in this app, so the fallbacks
    are safety nets only.
    """
    if not text:
        return False

    # ── Qt clipboard (primary path) ───────────────────────────────────────
    try:
        from PySide6.QtWidgets import QApplication
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(text)
            return True
    except Exception:
        pass

    # ── Windows ctypes fallback ───────────────────────────────────────────
    try:
        import ctypes
        import ctypes.wintypes
        GMEM_MOVEABLE = 0x0002
        encoded = text.encode('utf-16-le') + b'\x00\x00'
        h = ctypes.windll.kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
        ptr = ctypes.windll.kernel32.GlobalLock(h)
        ctypes.memmove(ptr, encoded, len(encoded))
        ctypes.windll.kernel32.GlobalUnlock(h)
        ctypes.windll.user32.OpenClipboard(None)
        ctypes.windll.user32.EmptyClipboard()
        CF_UNICODETEXT = 13
        ctypes.windll.user32.SetClipboardData(CF_UNICODETEXT, h)
        ctypes.windll.user32.CloseClipboard()
        return True
    except Exception:
        pass

    # ── Linux xclip ───────────────────────────────────────────────────────
    try:
        import subprocess
        p = subprocess.Popen(
            ['xclip', '-selection', 'clipboard'],
            stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        p.communicate(text.encode('utf-8'))
        return p.returncode == 0
    except Exception:
        pass

    # ── macOS pbcopy ──────────────────────────────────────────────────────
    try:
        import subprocess
        p = subprocess.Popen(
            ['pbcopy'],
            stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        p.communicate(text.encode('utf-8'))
        return p.returncode == 0
    except Exception:
        pass

    return False


# ══════════════════════════════════════════════════════════════════════════════
#  WordSuggest
#  Originally: app/services/word_suggest.py
# ══════════════════════════════════════════════════════════════════════════════

_EN: list[str] = [
    "the","be","to","of","and","a","in","that","have","it",
    "for","not","on","with","he","as","you","do","at","this",
    "but","his","by","from","they","we","say","her","she","or",
    "an","will","my","one","all","would","there","their","what",
    "so","up","out","if","about","who","get","which","go","me",
    "when","make","can","like","time","no","just","him","know",
    "take","people","into","year","your","good","some","could",
    "them","see","other","than","then","now","look","only","come",
    "its","over","think","also","back","after","use","two","how",
    "our","work","first","well","way","even","new","want","because",
    "any","these","give","day","most","us","great","between","need",
    "large","often","hand","high","place","hold","real","life","few",
    "hello","help","here","home","have","happy","hard","head","hear",
    "heart","heat","heavy","hope","hour","house","human","hurt",
    "idea","image","important","include","increase","information",
    "inside","instead","interest","into","issue","item",
    "just","keep","kind","know","knowledge",
    "last","late","later","learn","leave","less","level","life",
    "light","like","line","list","little","live","long","look","love",
    "made","make","many","may","mean","meet","mind","miss","more",
    "morning","move","much","must","myself",
    "name","near","need","never","next","night","nothing","now",
    "number","often","only","open","order","other","over","own",
    "part","past","people","person","place","plan","play","point",
    "possible","power","present","problem","program","public","put",
    "question","quick","quite",
    "rather","read","ready","real","really","reason","right","room",
    "run","said","same","school","second","seem","set","should",
    "show","since","small","something","sometimes","soon","sorry",
    "speak","start","state","still","stop","story","study","such",
    "sure","system","take","talk","tell","than","thank","thing",
    "think","those","though","through","today","together","told",
    "tonight","true","try","turn","under","until","upon","used",
    "very","view","wait","walk","want","watch","water","while",
    "whole","why","within","without","wonder","word","world","write",
    "year","young","your",
]

_FR: list[str] = [
    "le","la","les","de","du","des","un","une","et","en",
    "à","au","aux","que","qui","dans","je","il","elle","nous",
    "vous","ils","elles","est","son","sa","ses","sur","par","avec",
    "pour","ce","cette","ces","mais","ou","donc","or","ni","car",
    "plus","très","bien","aussi","tout","tous","toute","toutes",
    "mon","ma","mes","ton","ta","tes","lui","leur","leurs",
    "même","autre","autres","grand","grande","petit","petite",
    "bon","bonne","nouveau","nouvelle","premier","première",
    "après","avant","comme","depuis","pendant","alors","encore",
    "bonjour","bonsoir","merci","pardon","s'il","vous","plaît",
    "comment","quand","pourquoi","parce","que","oui","non",
    "avoir","être","faire","aller","venir","voir","savoir","pouvoir",
    "vouloir","devoir","mettre","prendre","donner","parler",
    "aimer","trouver","croire","dire","penser","regarder","appeler",
    "homme","femme","enfant","monde","vie","temps","main","jour",
    "nuit","maison","pays","ville","travail","heure","eau","tête",
    "chose","fois","façon","question","problème","exemple","point",
    "état","groupe","moment","droit","école","programme","projet",
    "seulement","toujours","jamais","souvent","peut-être","maintenant",
    "ici","là","beaucoup","peu","assez","trop","longtemps",
    "alors","ainsi","cependant","donc","pourtant","néanmoins",
]

_AR: list[str] = [
    "في","من","إلى","على","أن","هو","هي","ما","لا","كان",
    "قد","مع","عن","هذا","هذه","التي","الذي","كل","وكان","وقد",
    "أو","ولا","ولم","وأن","وما","وهو","وهي","وإن","وكل",
    "يكون","يكن","يمكن","يجب","يريد","يعرف","يقول","يرى",
    "كيف","متى","أين","لماذا","ماذا","من","هل","نعم","لا",
    "شكرا","مرحبا","صباح","مساء","الخير","النور","السلام",
    "عليكم","ورحمة","الله","بركاته",
    "كتاب","كتابة","كتب","كلمة","كلام","جملة",
    "يوم","ليلة","صباح","مساء","ساعة","دقيقة","وقت","زمن",
    "بيت","مدرسة","عمل","طريق","مدينة","دولة","عالم","ناس",
    "رجل","امرأة","طفل","أب","أم","أخ","أخت","صديق","صديقة",
    "جيد","جيدة","كبير","كبيرة","صغير","صغيرة","جميل","جميلة",
    "مهم","مهمة","سهل","سهلة","صعب","صعبة","جديد","جديدة",
    "أريد","أعرف","أقول","أذهب","أجيء","أرى","أحب","أفعل",
    "لكن","لأن","حتى","بعد","قبل","مثل","فقط","أيضا","دائما",
    "أحيانا","الآن","هنا","هناك","كثير","قليل","جدا",
    "واحد","اثنان","ثلاثة","أربعة","خمسة","ستة","سبعة","ثمانية",
    "تسعة","عشرة","مئة","ألف",
]

LANGS: dict[str, list[str]] = {
    "en": _EN,
    "fr": _FR,
    "ar": _AR,
}


def suggest(prefix: str, lang: str = "en", n: int = 3) -> list[str]:
    """
    Return up to *n* words from *lang* that start with *prefix*.

    Parameters
    ----------
    prefix : str   — partial word typed so far. Case-insensitive for Latin.
    lang   : str   — "en", "fr", or "ar".
    n      : int   — max suggestions to return.
    """
    if not prefix:
        return []
    wordlist = LANGS.get(lang.lower(), _EN)
    if lang.lower() == "ar":
        return [w for w in wordlist if w.startswith(prefix)][:n]
    p = prefix.lower()
    return [w for w in wordlist if w.startswith(p)][:n]