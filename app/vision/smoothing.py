"""
app/vision/smoothing.py
────────────────────────
Gaze smoothing filters.

Filters provided
────────────────
OneEuroFilter   — velocity-adaptive filter used by Snapchat/AR industry.
ExponentialSmoother — simple EMA kept for backward compatibility.
GatedSmoother   — confidence gate + dead-zone, now uses OneEuroFilter.
"""
from __future__ import annotations
import math
from typing import Optional


class _ScalarOneEuro:
    """One Euro Filter for a single scalar. Internal — use OneEuroFilter."""

    def __init__(self, fps=30.0, min_cutoff=1.0, beta=0.15, d_cutoff=1.0):
        self._fps        = max(fps, 1.0)
        self._min_cutoff = min_cutoff
        self._beta       = beta
        self._d_cutoff   = d_cutoff
        self._x:  Optional[float] = None
        self._dx: float           = 0.0

    @staticmethod
    def _alpha(cutoff: float, fps: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        te  = 1.0 / fps
        return 1.0 / (1.0 + tau / te)

    def update(self, x: float) -> float:
        if self._x is None:
            self._x = x; self._dx = 0.0; return x
        dx_raw   = (x - self._x) * self._fps
        a_d      = self._alpha(self._d_cutoff, self._fps)
        self._dx = self._dx + a_d * (dx_raw - self._dx)
        fc       = self._min_cutoff + self._beta * abs(self._dx)
        a_s      = self._alpha(fc, self._fps)
        self._x  = self._x + a_s * (x - self._x)
        return self._x

    def reset(self):
        self._x = None; self._dx = 0.0


class OneEuroFilter:
    """
    Velocity-adaptive low-pass filter for 2-D gaze / head positions.

    At rest (fixation): high smoothing → no tremor.
    During saccade:     low smoothing  → no lag.

    Parameters
    ----------
    fps        : webcam fps (default 30)
    min_cutoff : smoothing at rest in Hz — lower = smoother (default 1.0)
    beta       : speed responsiveness — higher = snappier (default 0.15)
    d_cutoff   : derivative filter Hz — leave at 1.0
    """

    def __init__(self, fps=30.0, min_cutoff=1.0, beta=0.15, d_cutoff=1.0):
        self._fx = _ScalarOneEuro(fps, min_cutoff, beta, d_cutoff)
        self._fy = _ScalarOneEuro(fps, min_cutoff, beta, d_cutoff)

    def smooth(self, x: float, y: float) -> tuple[float, float]:
        return self._fx.update(x), self._fy.update(y)

    def reset(self):
        self._fx.reset(); self._fy.reset()

    @property
    def has_value(self) -> bool:
        return self._fx._x is not None

    @property
    def min_cutoff(self) -> float:
        return self._fx._min_cutoff

    @min_cutoff.setter
    def min_cutoff(self, v: float):
        self._fx._min_cutoff = max(0.1, float(v))
        self._fy._min_cutoff = max(0.1, float(v))

    @property
    def beta(self) -> float:
        return self._fx._beta

    @beta.setter
    def beta(self, v: float):
        self._fx._beta = max(0.0, float(v))
        self._fy._beta = max(0.0, float(v))


class ExponentialSmoother:
    """Fixed-alpha EMA — kept for backward compatibility. Prefer OneEuroFilter."""

    def __init__(self, alpha: float = 0.30) -> None:
        if not 0 < alpha <= 1:
            raise ValueError(f"alpha must be in (0, 1], got {alpha}")
        self._a = alpha
        self._x: Optional[float] = None
        self._y: Optional[float] = None

    def smooth(self, x: float, y: float) -> tuple[float, float]:
        if self._x is None:
            self._x, self._y = x, y
        else:
            self._x += self._a * (x - self._x)
            self._y += self._a * (y - self._y)  # type: ignore[operator]
        return self._x, self._y  # type: ignore[return-value]

    def reset(self):
        self._x = self._y = None

    @property
    def has_value(self) -> bool:
        return self._x is not None


class GatedSmoother:
    """
    Confidence gate + dead-zone + One Euro Filter.

    Parameters
    ----------
    fps            : webcam fps
    min_cutoff     : OEF rest smoothing Hz
    beta           : OEF speed responsiveness
    conf_threshold : min confidence to accept update
    dead_zone      : min normalised Δ to pass dead-zone
    """

    def __init__(
        self,
        fps:            float = 30.0,
        min_cutoff:     float = 1.0,
        beta:           float = 0.15,
        conf_threshold: float = 0.4,
        dead_zone:      float = 0.003,
    ) -> None:
        self._oef  = OneEuroFilter(fps=fps, min_cutoff=min_cutoff, beta=beta)
        self._conf = conf_threshold
        self._dz   = dead_zone
        self._last_x: Optional[float] = None
        self._last_y: Optional[float] = None

    def update(self, x: float, y: float, confidence: float = 1.0) -> tuple[float, float]:
        if confidence < self._conf:
            if self._last_x is not None:
                return self._last_x, self._last_y  # type: ignore[return-value]
        if self._last_x is not None:
            if (abs(x - self._last_x) < self._dz and
                    abs(y - self._last_y) < self._dz):  # type: ignore[operator]
                return self._last_x, self._last_y  # type: ignore[return-value]
        sx, sy = self._oef.smooth(x, y)
        self._last_x, self._last_y = sx, sy
        return sx, sy

    def reset(self):
        self._oef.reset()
        self._last_x = self._last_y = None