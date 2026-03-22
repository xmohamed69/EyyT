"""
firebase_client.py
Handles all Firebase Firestore operations for EyeTyper.

Collections:
  calibrations  — stores calibration profiles keyed by hardware fingerprint
  screen_frames — stores display metadata for calibration matching

No login required — users are identified by an anonymous hardware fingerprint
built from screen resolution + DPI + platform info (no personal data).

Install:
    pip install firebase-admin
    Download serviceAccountKey.json from Firebase Console →
    Project Settings → Service Accounts → Generate new private key
    Place it at:  data/firebase_key.json
"""
from __future__ import annotations

import hashlib
import json
import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.services.platform_utils import get_cpu_id, get_screen_size

# Firebase
try:
    import firebase_admin
    from firebase_admin import credentials, firestore
    _FIREBASE_OK = True
except ImportError:
    _FIREBASE_OK = False

# Screen info
try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QScreen
    _QT_OK = True
except ImportError:
    _QT_OK = False

# ── config ────────────────────────────────────────────────────────────────────
_KEY_PATH    = Path("data/firebase_key.json")
_PROJECT_ID  = "eye-tracker-b0f76"

_FIREBASE_CONFIG = {
    "apiKey":            "AIzaSyBkdjVntDWXRWfeUi7SlG2XpYFxZ51qzpI",
    "authDomain":        "eye-tracker-b0f76.firebaseapp.com",
    "databaseURL":       "https://eye-tracker-b0f76-default-rtdb.firebaseio.com",
    "projectId":         _PROJECT_ID,
    "storageBucket":     "eye-tracker-b0f76.firebasestorage.app",
    "messagingSenderId": "46961998144",
    "appId":             "1:46961998144:web:f42181a2bc29a09d1f22a5",
}


# ══════════════════════════════════════════════════════════════════════════════
class FirebaseClient:
    """
    Thread-safe Firebase client.
    All methods fail silently if Firebase is unavailable —
    the app works fully offline without Firebase.
    """

    def __init__(self) -> None:
        self._db = None
        self._fingerprint: Optional[str] = None
        self._screen_info: Optional[dict] = None
        self._init_firebase()

    # ── init ──────────────────────────────────────────────────────────────────

    def _init_firebase(self) -> None:
        if not _FIREBASE_OK:
            print("[Firebase] firebase-admin not installed — offline mode")
            return
        if not _KEY_PATH.exists():
            print(f"[Firebase] Key not found at {_KEY_PATH} — offline mode")
            return
        try:
            if not firebase_admin._apps:
                cred = credentials.Certificate(str(_KEY_PATH))
                firebase_admin.initialize_app(cred, {
                    "projectId": _PROJECT_ID,
                })
            self._db = firestore.client()
            print("[Firebase] Connected ✓")
        except Exception as e:
            print(f"[Firebase] Init failed: {e}")
            self._db = None

    @property
    def online(self) -> bool:
        return self._db is not None

    # ── fingerprint ───────────────────────────────────────────────────────────

    def get_fingerprint(self) -> str:
        """
        Anonymous hardware fingerprint — reproducible on the same machine.
        Built from: screen resolution + DPI + OS platform + CPU info.
        Contains NO personal data.
        """
        if self._fingerprint:
            return self._fingerprint

        parts: list[str] = []
        parts.append(platform.system())
        parts.append(platform.machine())

        # CPU ID — cross-platform
        from app.services.platform_utils import get_cpu_id
        parts.append(get_cpu_id())

        # Screen info
        info = self.get_screen_info()
        parts.append(f"{info.get('width', 0)}x{info.get('height', 0)}")
        parts.append(str(info.get('dpi', 96)))

        raw = "|".join(parts)
        self._fingerprint = hashlib.sha256(raw.encode()).hexdigest()[:20]
        return self._fingerprint

    def get_screen_info(self) -> dict:
        """Collect display metadata for calibration matching."""
        if self._screen_info:
            return self._screen_info

        info: dict = {
            "platform":    platform.system(),
            "os_version":  platform.version()[:60],
            "width":       1920,
            "height":      1080,
            "dpi":         96,
            "refresh_hz":  60,
            "scale_factor": 1.0,
        }

        if _QT_OK:
            try:
                scr: QScreen = QApplication.primaryScreen()
                geo  = scr.geometry()
                info["width"]        = geo.width()
                info["height"]       = geo.height()
                info["dpi"]          = round(scr.logicalDotsPerInch())
                info["refresh_hz"]   = round(scr.refreshRate())
                info["scale_factor"] = round(scr.devicePixelRatio(), 2)
            except Exception:
                pass

        self._screen_info = info
        return info

    # ── calibration upload ────────────────────────────────────────────────────

    def upload_calibration(
        self,
        screen_pts: list,
        gaze_pts:   list,
        mean_error: float,
        step:       int,
    ) -> bool:
        """
        Upload calibration to Firestore.
        Document ID = hardware fingerprint (one doc per machine, overwritten).
        Returns True on success.
        """
        if not self.online:
            return False
        try:
            doc = {
                "fingerprint":   self.get_fingerprint(),
                "screen_info":   self.get_screen_info(),
                "screen_points": screen_pts,
                "gaze_points":   gaze_pts,
                "mean_error":    round(mean_error, 6),
                "step":          step,
                "uploaded_at":   datetime.utcnow().isoformat(),
                "platform":      platform.system(),
            }
            self._db.collection("calibrations") \
                    .document(self.get_fingerprint()) \
                    .set(doc)
            print(f"[Firebase] Calibration uploaded (error={mean_error:.4f})")
            return True
        except Exception as e:
            print(f"[Firebase] Upload failed: {e}")
            return False

    # ── calibration matching ──────────────────────────────────────────────────

    def find_matching_calibrations(
        self,
        tolerance_px: int = 50,
        max_results:  int = 5,
    ) -> list[dict]:
        """
        Find calibrations from other users with the same screen profile.
        Matches on: width, height, dpi (within tolerance).
        Returns list of calibration dicts sorted by mean_error ascending.
        """
        if not self.online:
            return []
        try:
            my_info   = self.get_screen_info()
            my_fp     = self.get_fingerprint()
            my_w      = my_info["width"]
            my_h      = my_info["height"]
            my_dpi    = my_info["dpi"]

            docs = self._db.collection("calibrations").stream()
            matches = []
            for doc in docs:
                d = doc.to_dict()
                if d.get("fingerprint") == my_fp:
                    continue   # skip own calibration
                si = d.get("screen_info", {})
                if (abs(si.get("width",  0) - my_w)   <= tolerance_px and
                    abs(si.get("height", 0) - my_h)   <= tolerance_px and
                    abs(si.get("dpi",    0) - my_dpi) <= 10):
                    matches.append(d)

            matches.sort(key=lambda x: x.get("mean_error", 1.0))
            print(f"[Firebase] Found {len(matches)} matching calibration(s)")
            return matches[:max_results]
        except Exception as e:
            print(f"[Firebase] Match search failed: {e}")
            return []

    # ── screen frame log ──────────────────────────────────────────────────────

    def log_screen_frame(self) -> bool:
        """
        Log this machine's screen profile to the screen_frames collection.
        Used to build the global dataset for calibration matching.
        One document per fingerprint, updated on each launch.
        """
        if not self.online:
            return False
        try:
            doc = {
                "fingerprint": self.get_fingerprint(),
                "screen_info": self.get_screen_info(),
                "last_seen":   datetime.utcnow().isoformat(),
                "platform":    platform.system(),
            }
            self._db.collection("screen_frames") \
                    .document(self.get_fingerprint()) \
                    .set(doc, merge=True)
            print("[Firebase] Screen frame logged ✓")
            return True
        except Exception as e:
            print(f"[Firebase] Screen frame log failed: {e}")
            return False

    # ── best community calibration ────────────────────────────────────────────

    def get_best_community_calibration(self) -> Optional[dict]:
        """
        Returns the best (lowest error) community calibration
        matching this machine's screen, or None if nothing found.
        """
        matches = self.find_matching_calibrations()
        return matches[0] if matches else None


# ── singleton ─────────────────────────────────────────────────────────────────
_client: Optional[FirebaseClient] = None


def get_client() -> FirebaseClient:
    global _client
    if _client is None:
        _client = FirebaseClient()
    return _client