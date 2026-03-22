"""
app/services/stores.py
─────────────────────────────────────────────────────────────────────────────
Merged persistent-storage module.  Contains two JSON-backed stores:

  settings_store   — keyboard-mode preferences   (data/settings.json)
  calibration_store — gaze calibration data      (data/calibration.json)

Why these three are together
────────────────────────────
All three follow the exact same pattern:
    Path → json.loads → defaults dict → load() / save()
No cross-imports, no Qt, no cv2, no threads — pure stdlib JSON I/O.
Merging removes ~60 lines of boilerplate and keeps all data-layer paths
in one place.

Public API — identical to the originals:

    from app.services.stores import (
        load_settings, save_settings,          # keyboard settings
        load_calib, save_calib, reset_calib, calib_exists,  # calibration
    )

Drop-in aliases are provided at the bottom so existing imports that use
the old module names continue to work without changes.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


# ══════════════════════════════════════════════════════════════════════════════
#  settings_store  —  keyboard-mode preferences
#  Originally: app/services/settings_store.py
# ══════════════════════════════════════════════════════════════════════════════

_SETTINGS_PATH = Path("data/settings.json")

_SETTINGS_DEFAULTS: dict[str, Any] = {
    "dwell_ms":         800,
    "lang_idx":         0,
    "suggestions_on":   True,
    "debug_landmarks":  True,
    "floating_camera":  True,
    "layout_key":       "QWERTY",
    "lang_code":        "en",
}


def load_settings() -> dict[str, Any]:
    """Return keyboard settings dict, filling missing keys with defaults."""
    try:
        raw = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    result = dict(_SETTINGS_DEFAULTS)
    result.update({k: v for k, v in raw.items() if k in _SETTINGS_DEFAULTS})
    return result


def save_settings(cfg: dict[str, Any]) -> None:
    """Persist keyboard settings to disk (only known keys are written)."""
    _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    clean = {k: cfg[k] for k in _SETTINGS_DEFAULTS if k in cfg}
    _SETTINGS_PATH.write_text(
        json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
#  calibration_store  —  gaze calibration data
#  Originally: app/services/calibration_store.py
#
#  Schema
#  ------
#  {
#    "calibrated_at": "2026-03-17 14:32:00",
#    "step": 1,
#    "mean_error": 0.031,
#    "screen_points": [[x,y], ...],
#    "gaze_points":   [[x,y], ...]
#  }
# ══════════════════════════════════════════════════════════════════════════════

_CALIB_PATH = Path("data/calibration.json")


def load_calib() -> Optional[dict]:
    """Return the saved calibration dict, or None if none exists."""
    if not _CALIB_PATH.exists():
        return None
    try:
        return json.loads(_CALIB_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_calib(
    step: int,
    mean_error: float,
    screen_points: list[tuple[float, float]],
    gaze_points:   list[tuple[float, float]],
) -> None:
    """Persist calibration data to disk."""
    _CALIB_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "calibrated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "step":          step,
        "mean_error":    round(mean_error, 6),
        "screen_points": [list(p) for p in screen_points],
        "gaze_points":   [list(p) for p in gaze_points],
    }
    _CALIB_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def reset_calib() -> None:
    """Delete the calibration file."""
    if _CALIB_PATH.exists():
        _CALIB_PATH.unlink()


def calib_exists() -> bool:
    """Return True if a calibration file exists."""
    return _CALIB_PATH.exists()



# ══════════════════════════════════════════════════════════════════════════════
#  kb_calibration_store  —  keyboard-fitted affine calibration
#  Stored separately from the old screen-space calibration.
#
#  Schema
#  ------
#  {
#    "calibrated_at":  "2026-03-21 14:00:00",
#    "kb_width":       1280,
#    "kb_height":      400,
#    "mean_error_px":  8.4,
#    "matrix":         [[a,b,c],[d,e,f]],   # 2×3 affine
#    "gaze_points":    [[x,y], ...],
#    "kb_points":      [[x,y], ...]
#  }
# ══════════════════════════════════════════════════════════════════════════════

_KB_CALIB_PATH = Path("data/kb_calibration.json")


def load_kb_calib() -> Optional[dict]:
    """Return keyboard calibration dict, or None if none exists."""
    if not _KB_CALIB_PATH.exists():
        return None
    try:
        return json.loads(_KB_CALIB_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_kb_calib(
    matrix:        list,
    kb_width:      int,
    kb_height:     int,
    mean_error_px: float,
    gaze_points:   list,
    kb_points:     list,
) -> None:
    """Persist keyboard calibration to disk."""
    _KB_CALIB_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "calibrated_at":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "kb_width":        kb_width,
        "kb_height":       kb_height,
        "mean_error_px":   round(mean_error_px, 4),
        "matrix":          matrix,
        "gaze_points":     [list(p) for p in gaze_points],
        "kb_points":       [list(p) for p in kb_points],
    }
    _KB_CALIB_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def reset_kb_calib() -> None:
    """Delete the keyboard calibration file."""
    if _KB_CALIB_PATH.exists():
        _KB_CALIB_PATH.unlink()


def kb_calib_exists() -> bool:
    """Return True if a keyboard calibration file exists."""
    return _KB_CALIB_PATH.exists()

# ── Drop-in aliases ───────────────────────────────────────────────────────────
# These let existing files that import from the old modules continue to work
# without any changes.  Just update the import line and everything else is
# unchanged.
#
# calibration_store.py used:  load / save / reset / exists
load  = load_calib
save  = save_calib
reset = reset_calib
exists = calib_exists