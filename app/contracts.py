"""
Shared contracts (dataclasses + Protocol) for the eye-tracking app.

All modules import from here — never cross-import between services/vision.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

import numpy as np


# ── Value objects ─────────────────────────────────────────────────────────────

@dataclass
class GazePoint:
    """A single gaze sample in normalised screen coordinates (0-1)."""
    x_norm:     float
    y_norm:     float
    confidence: float   # 0.0 = no face, 0.3 = nose fallback, 1.0 = iris
    timestamp:  float


@dataclass
class CalibrationProfile:
    """
    Affine calibration result persisted to data/calibration.json.

    Fields match the calibration_store.py schema so load() -> CalibrationProfile
    round-trips without conversion.
    """
    calibrated_at:  str
    step:           int                        # 1, 2, or 3
    mean_error:     float
    screen_points:  list                       # [[x,y], ...]
    gaze_points:    list                       # [[x,y], ...]


# ── Provider protocol ─────────────────────────────────────────────────────────

class GazeProvider(Protocol):
    """
    Minimal interface that every gaze backend must satisfy.
    MediaPipeTracker is the current concrete implementation.
    """

    def start(self) -> None:
        """Open the camera and begin the background capture loop."""
        ...

    def stop(self) -> None:
        """Stop the loop and release the camera."""
        ...

    def get_gaze(self) -> Optional[GazePoint]:
        """Return the latest gaze point, or None if unavailable."""
        ...

    def get_latest_frame(self) -> Optional[np.ndarray]:
        """Return the latest annotated BGR frame, or None."""
        ...

    def load_calibration(
        self,
        screen_pts: list,
        gaze_pts:   list,
    ) -> None:
        """Apply a previously saved calibration (affine mapping)."""
        ...