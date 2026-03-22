"""
MockGazeProvider — satisfies the GazeProvider protocol for UI testing.

Modes
-----
mouse : gaze point tracks the Windows cursor (best for manual testing)
auto  : gaze sweeps a Lissajous pattern across the screen
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import math
import time
from typing import Optional

from app.contracts import GazePoint


class MockGazeProvider:
    """Implements GazeProvider protocol using the system cursor or sine math."""

    def __init__(self, mode: str = "mouse") -> None:
        if mode not in ("mouse", "auto"):
            raise ValueError(f"Unknown mode '{mode}'; use 'mouse' or 'auto'")
        self._mode = mode
        self._running: bool = False
        self._t0: float = 0.0

        # Cache primary-monitor pixel dimensions (physical)
        _user32 = ctypes.windll.user32
        _user32.SetProcessDPIAware()           # ensure unscaled metrics
        self._scr_w: int = _user32.GetSystemMetrics(0)
        self._scr_h: int = _user32.GetSystemMetrics(1)

    # ── GazeProvider protocol ────────────────────────────────────

    def start(self) -> None:
        self._running = True
        self._t0 = time.time()

    def stop(self) -> None:
        self._running = False

    def get_gaze(self) -> Optional[GazePoint]:
        if not self._running:
            return None

        now = time.time()

        if self._mode == "mouse":
            pt = ctypes.wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            x_norm = pt.x / self._scr_w
            y_norm = pt.y / self._scr_h
        else:
            t = now - self._t0
            x_norm = 0.5 + 0.4 * math.sin(0.31 * t)
            y_norm = 0.5 + 0.35 * math.sin(0.47 * t + 0.7)

        return GazePoint(x_norm=x_norm, y_norm=y_norm, confidence=1.0, timestamp=now)

    # ── Extra (non-protocol) ─────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        if value not in ("mouse", "auto"):
            raise ValueError(f"Unknown mode '{value}'")
        self._mode = value

    # ── GazeProvider protocol stubs (no real camera in mock mode) ────────────

    def get_latest_frame(self):
        """No camera in mock mode — return None so UI handles it gracefully."""
        return None

    def load_calibration(self, screen_pts: list, gaze_pts: list) -> None:
        """No-op: mock provider does not use calibration."""
        pass