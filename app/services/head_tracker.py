"""
app/services/head_tracker.py
─────────────────────────────
Head-pose gaze estimator for keyboard control.

Uses landmarks already extracted by MediaPipeTracker — no extra model,
no extra camera read, zero additional CPU cost.

How it works
────────────
We use three reference points that MediaPipe gives us every frame:

    nose tip        lms[4]
    left eye mid    midpoint of lms[33] and lms[133]
    right eye mid   midpoint of lms[362] and lms[263]

From these we derive:

    YAW   (left/right):
        The horizontal position of the nose tip relative to the eye midpoint.
        When you turn your head right, your nose moves right of centre.
        Formula:  yaw = nose.x - eye_mid.x
        At neutral (looking straight): yaw ≈ 0.0

    PITCH (up/down):
        The vertical position of the nose tip relative to the eye midpoint.
        When you tilt your head down, your nose moves below centre.
        Formula:  pitch = nose.y - eye_mid.y
        At neutral (looking straight): pitch ≈ some small positive value
        (nose is always slightly below the eye line)

Both raw values are stored on the first valid frame as the NEUTRAL reference.
All subsequent values are reported as DELTA from that neutral.

The delta is then scaled by a sensitivity factor and mapped to [0, 1] screen
coordinates using a configurable range (how far you need to tilt to reach
the edge of the keyboard).

Output
──────
HeadPose(x_norm, y_norm, confidence)
    x_norm  — 0.0 = left edge, 0.5 = centre, 1.0 = right edge
    y_norm  — 0.0 = top  edge, 0.5 = centre, 1.0 = bottom edge
    confidence — 1.0 if valid, 0.0 if no face this frame
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


# ── tunables ──────────────────────────────────────────────────────────────────
# How many degrees of head tilt map to a full edge-to-edge sweep.
# Smaller = more sensitive (less tilt needed to reach edge).
# Larger  = less sensitive (need bigger tilt).
HEAD_RANGE_X   = 0.08   # normalised landmark units, ≈ ±8% of face width
HEAD_RANGE_Y   = 0.06   # normalised landmark units, ≈ ±6% of face height

# EMA smoothing for head pose (separate from gaze EMA)
HEAD_EMA_ALPHA = 0.25   # 0=frozen, 1=raw — lower than gaze for stability

# Dead-zone: ignore tiny head tremor below this threshold
HEAD_DEAD_ZONE = 0.003  # normalised


@dataclass
class HeadPose:
    """Normalised head-pose position for keyboard targeting."""
    x_norm:     float   # 0.0–1.0  left→right
    y_norm:     float   # 0.0–1.0  top→bottom
    confidence: float   # 1.0 = valid, 0.0 = no face
    yaw_raw:    float   # raw delta from neutral (for debug)
    pitch_raw:  float   # raw delta from neutral (for debug)


class HeadTracker:
    """
    Converts raw MediaPipe landmark dicts into normalised screen positions
    using head yaw and pitch.

    Usage
    ─────
        ht = HeadTracker()

        # call every frame inside MediaPipeTracker._loop():
        pose = ht.update(nose_x, nose_y, leye_mid_x, leye_mid_y,
                          reye_mid_x, reye_mid_y)

        # pose.x_norm / pose.y_norm → drive keyboard highlight
        # pose.confidence           → fallback trigger
    """

    def __init__(
        self,
        range_x:    float = HEAD_RANGE_X,
        range_y:    float = HEAD_RANGE_Y,
        ema_alpha:  float = HEAD_EMA_ALPHA,
        dead_zone:  float = HEAD_DEAD_ZONE,
    ) -> None:
        self._range_x   = range_x
        self._range_y   = range_y
        self._alpha     = ema_alpha
        self._dead_zone = dead_zone

        # neutral reference — set on first valid frame after reset()
        self._neutral_yaw:   Optional[float] = None
        self._neutral_pitch: Optional[float] = None

        # EMA state
        self._sx: Optional[float] = None
        self._sy: Optional[float] = None

    # ── properties (live-adjustable from UI) ──────────────────────────────────

    @property
    def range_x(self) -> float:
        return self._range_x

    @range_x.setter
    def range_x(self, v: float) -> None:
        self._range_x = max(0.01, min(0.30, float(v)))

    @property
    def range_y(self) -> float:
        return self._range_y

    @range_y.setter
    def range_y(self, v: float) -> None:
        self._range_y = max(0.01, min(0.20, float(v)))

    # ── main update ───────────────────────────────────────────────────────────

    def update(
        self,
        nose_x:     float,
        nose_y:     float,
        leye_mid_x: float,
        leye_mid_y: float,
        reye_mid_x: float,
        reye_mid_y: float,
    ) -> HeadPose:
        """
        Feed current-frame landmark positions (normalised 0-1 camera coords).
        Returns HeadPose with x_norm/y_norm in keyboard screen space.
        """
        # eye midpoint = average of left and right eye centres
        eye_mid_x = (leye_mid_x + reye_mid_x) / 2.0
        eye_mid_y = (leye_mid_y + reye_mid_y) / 2.0

        # raw yaw / pitch relative to eye midpoint
        raw_yaw   = nose_x - eye_mid_x   # right = positive
        raw_pitch = nose_y - eye_mid_y   # down  = positive

        # seed neutral on first frame
        if self._neutral_yaw is None:
            self._neutral_yaw   = raw_yaw
            self._neutral_pitch = raw_pitch
            self._sx = 0.5
            self._sy = 0.5
            return HeadPose(
                x_norm=0.5, y_norm=0.5,
                confidence=1.0,
                yaw_raw=0.0, pitch_raw=0.0,
            )

        # delta from neutral
        delta_yaw   = raw_yaw   - self._neutral_yaw
        delta_pitch = raw_pitch - self._neutral_pitch

        # map [-range, +range] → [0, 1]
        # yaw:   turning right (+delta) → x increases (rightward on screen)
        # pitch: tilting down (+delta)  → y increases (downward on screen)
        mapped_x = 0.5 + delta_yaw   / (2.0 * self._range_x)
        mapped_y = 0.5 + delta_pitch / (2.0 * self._range_y)

        mapped_x = float(np.clip(mapped_x, 0.0, 1.0))
        mapped_y = float(np.clip(mapped_y, 0.0, 1.0))

        # dead-zone + EMA
        if self._sx is None:
            self._sx, self._sy = mapped_x, mapped_y
        else:
            if (abs(mapped_x - self._sx) > self._dead_zone or
                    abs(mapped_y - self._sy) > self._dead_zone):   # type: ignore[operator]
                self._sx += self._alpha * (mapped_x - self._sx)
                self._sy += self._alpha * (mapped_y - self._sy)   # type: ignore[operator]

        return HeadPose(
            x_norm=float(np.clip(self._sx, 0.0, 1.0)),
            y_norm=float(np.clip(self._sy, 0.0, 1.0)),  # type: ignore[arg-type]
            confidence=1.0,
            yaw_raw=delta_yaw,
            pitch_raw=delta_pitch,
        )

    def reset(self) -> None:
        """Re-capture neutral on the next frame (call when re-enabling)."""
        self._neutral_yaw   = None
        self._neutral_pitch = None
        self._sx = self._sy = None