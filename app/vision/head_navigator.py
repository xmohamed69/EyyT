"""
app/vision/head_navigator.py
─────────────────────────────────────────────────────────────────────────────
Head position → continuous normalised (x, y) output for keyboard targeting.

WHY THE OLD DISCRETE COMMAND APPROACH FAILED
─────────────────────────────────────────────
The previous version fired a single LEFT/RIGHT/UP/DOWN command then locked
out further commands until the head "returned to centre".  This felt stuck
because:
  1. return_zone was smaller than dead_zone (mathematically impossible to clear)
  2. Even when fixed, the EMA kept a memory of the tilt so the smoothed value
     took many frames to drop back below return_zone
  3. The user had to fully return to neutral AND wait for cooldown before the
     next key — unnatural and slow

THE NEW APPROACH: CONTINUOUS POSITION MAPPING
──────────────────────────────────────────────
Instead of discrete fire-and-wait, we output a continuous (x, y) value in
[0, 1] that represents WHERE the head is pointing relative to a defined range.

    neutral_x, neutral_y  = calibrated centre position (nose at rest)
    max_range             = how far the head needs to move to reach edge (0 or 1)

    raw_dx = nose_x - neutral_x
    raw_dy = nose_y - neutral_y

    smooth_dx = EMA(raw_dx)          ← kills jitter
    smooth_dy = EMA(raw_dy)

    head_x = clamp(0.5 + smooth_dx / max_range, 0, 1)
    head_y = clamp(0.5 + smooth_dy / max_range, 0, 1)

This (head_x, head_y) is then used to pick a key from the keyboard grid by
mapping x → column fraction, y → row fraction — exactly like a cursor.

The result: as you tilt your head right, focus continuously moves right
across keys.  As you tilt back to centre, focus returns to centre keys.
No cooldown, no return gate, no stuck state.

A small dead_zone around centre (head_x near 0.5) is kept to prevent jitter
on the middle keys when the head is at rest.
"""
from __future__ import annotations

import time
from app.vision.smoothing import OneEuroFilter
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional


# Keep HeadCommand for backward compatibility — not used internally anymore
class HeadCommand(Enum):
    NONE  = auto()
    LEFT  = auto()
    RIGHT = auto()
    UP    = auto()
    DOWN  = auto()


@dataclass
class HeadNavSettings:
    """
    Parameters for continuous head tracking.

    max_range   : nose offset (in MediaPipe normalised units) that maps to
                  the far edge of the keyboard (head_x=0 or head_x=1).
                  0.06 means a 6% nose shift covers the full keyboard width.
                  Decrease to make it more sensitive, increase for less.

    dead_zone   : zone around centre (head_x ≈ 0.5) where output is frozen
                  to prevent jitter on centre keys while head is at rest.
                  0.010 in normalised units = ~1% of camera frame width.

    ema_alpha   : EMA smoothing weight.
                  0.18 = smooth and stable, small lag.
                  0.30 = faster response, slightly more jitter.
                  Lower → smoother. Higher → more responsive.

    v_scale     : vertical scaling relative to horizontal.
                  1.2 = vertical movement is amplified 20% vs horizontal.
                  Useful because vertical head range is usually smaller.
    """
    max_range:      float = 0.06
    dead_zone:      float = 0.010
    ema_alpha:      float = 0.18   # kept for compat; OEF is used instead
    v_scale:        float = 1.2
    oef_min_cutoff: float = 0.8    # OEF: smoothing at rest (Hz)
    oef_beta:       float = 0.25   # OEF: responsiveness during fast moves
    # Adaptive bounds: remap output from [0,1] to [out_lo, out_hi].
    # Ensures full keyboard coverage even with limited head range.
    # 0.1–0.9 means a 20° tilt covers 100% of the keyboard.
    out_lo:         float = 0.10
    out_hi:         float = 0.90
    # legacy fields kept for API compat — unused in continuous mode
    h_threshold:  float = 0.018
    v_threshold:  float = 0.014
    cooldown_ms:  int   = 0
    return_zone:  float = 0.012
    nod_select:   bool  = False


@dataclass
class HeadNavDebug:
    raw_dx:         float = 0.0
    raw_dy:         float = 0.0
    smooth_dx:      float = 0.0
    smooth_dy:      float = 0.0
    head_x:         float = 0.5   # continuous output x (0=left, 1=right)
    head_y:         float = 0.5   # continuous output y (0=top,  1=bottom)
    command:        HeadCommand = HeadCommand.NONE   # legacy compat
    calibrated:     bool = False
    waiting_return: bool = False   # always False in continuous mode


class HeadNavigator:
    """
    Converts per-frame nose position into a continuous (head_x, head_y) pair.

    head_x and head_y are in [0, 1]:
        0.5, 0.5  = head at neutral (looking straight ahead)
        0.0, 0.5  = head fully tilted left
        1.0, 0.5  = head fully tilted right
        0.5, 0.0  = head tilted up
        0.5, 1.0  = head tilted down

    Usage
    -----
    nav = HeadNavigator()
    nav.calibrate(nose_x, nose_y)         # call once at startup

    every frame:
        hx, hy, dbg = nav.get_position(nose_x, nose_y)
        # hx, hy → pass to KeyboardFocusController.focus_at(hx, hy)
    """

    def __init__(self, settings: Optional[HeadNavSettings] = None) -> None:
        self._s = settings or HeadNavSettings()
        self._neutral_x: Optional[float] = None
        self._neutral_y: Optional[float] = None
        # One Euro Filter replaces fixed EMA.
        # Instantiated here; recreated when settings change.
        self._oef = OneEuroFilter(
            fps=30.0,
            min_cutoff=self._s.oef_min_cutoff,
            beta=self._s.oef_beta,
        )
        self._sx: float = 0.0
        self._sy: float = 0.0

    def calibrate(self, nose_x: float, nose_y: float) -> None:
        """Record current nose position as the neutral centre."""
        self._neutral_x = nose_x
        self._neutral_y = nose_y
        self._sx = 0.0
        self._sy = 0.0
        self._oef.reset()

    def is_calibrated(self) -> bool:
        return self._neutral_x is not None

    def reset_calibration(self) -> None:
        self._neutral_x = None
        self._neutral_y = None
        self._sx = self._sy = 0.0
        self._oef.reset()

    @property
    def settings(self) -> HeadNavSettings:
        return self._s

    @settings.setter
    def settings(self, s: HeadNavSettings) -> None:
        self._s = s
        self._oef = OneEuroFilter(
            fps=30.0,
            min_cutoff=s.oef_min_cutoff,
            beta=s.oef_beta,
        )

    def get_position(
        self,
        nose_x: float,
        nose_y: float,
    ) -> tuple[float, float, HeadNavDebug]:
        """
        Feed current nose position; return (head_x, head_y, debug).

        head_x, head_y are continuous values in [0, 1].
        0.5, 0.5 = neutral / looking straight ahead.
        """
        dbg = HeadNavDebug()

        if not self.is_calibrated():
            return 0.5, 0.5, dbg

        dbg.calibrated = True

        # raw offset from neutral
        raw_dx = nose_x - self._neutral_x  # type: ignore[operator]
        raw_dy = nose_y - self._neutral_y  # type: ignore[operator]
        dbg.raw_dx = raw_dx
        dbg.raw_dy = raw_dy

        # ── One Euro Filter (velocity-adaptive smoothing) ────────────────
        # Heavy smoothing at rest → no jitter on held keys.
        # Light smoothing during fast movement → no lag when scanning.
        # Operates on the raw delta values (float precision preserved).
        smooth_dx, smooth_dy = self._oef.smooth(raw_dx, raw_dy)
        self._sx = smooth_dx
        self._sy = smooth_dy
        dbg.smooth_dx = self._sx
        dbg.smooth_dy = self._sy

        # ── dead zone: freeze output near neutral ─────────────────────────
        sx = self._sx if abs(self._sx) > self._s.dead_zone else 0.0
        sy = self._sy if abs(self._sy) > self._s.dead_zone else 0.0

        # ── map offset → raw [0, 1] position ─────────────────────────────
        r = self._s.max_range
        raw_x = 0.5 + sx / r
        raw_y = 0.5 + (sy * self._s.v_scale) / r
        raw_x = max(0.0, min(1.0, raw_x))
        raw_y = max(0.0, min(1.0, raw_y))

        # ── adaptive bounds remap: [0,1] → [out_lo, out_hi] ──────────────
        # This is the '0.1–0.9 remapping' fix.
        # Even if the user's head only moves 60% of the theoretical range,
        # the output is stretched so 0.0 and 1.0 are always reachable.
        # out_lo=0.10, out_hi=0.90 means the raw [0.1,0.9] band maps to
        # the full [0.0,1.0] output, giving extra travel at both edges.
        lo, hi = self._s.out_lo, self._s.out_hi
        span = hi - lo
        if span > 0:
            head_x = (raw_x - lo) / span
            head_y = (raw_y - lo) / span
        else:
            head_x, head_y = raw_x, raw_y
        head_x = max(0.0, min(1.0, head_x))
        head_y = max(0.0, min(1.0, head_y))

        dbg.head_x = head_x
        dbg.head_y = head_y

        return head_x, head_y, dbg

    # ── legacy compatibility shim ─────────────────────────────────────────────
    # Old code called nav.update(nx, ny) → (HeadCommand, HeadNavDebug).
    # Keep this so any other callers don't break.

    def update(
        self,
        nose_x: float,
        nose_y: float,
    ) -> tuple[HeadCommand, HeadNavDebug]:
        """Legacy shim — returns NONE command always; use get_position() instead."""
        hx, hy, dbg = self.get_position(nose_x, nose_y)
        return HeadCommand.NONE, dbg