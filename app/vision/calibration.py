"""
calibration.py — gaze calibration with feature-vector collection.

Part 4 + Part 5 improvements:
  • 13-point calibration grid (was 5-point)
  • Collects full feature vectors per sample (not just raw iris avg)
  • Saves all features to calibration JSON for auditability
  • fit_regression() helper integrates with MediaPipeTracker
  • Legacy AffineCalibrationProfile and run_calibration() preserved
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


# ── local calibration profile (affine — legacy) ───────────────────────────────
@dataclass
class AffineCalibrationProfile:
    """Legacy affine calibration.  Kept for backward compatibility."""
    coeffs_x:   list   # [a, b, c]  sx = a*ix + b*iy + c
    coeffs_y:   list   # [d, e, f]  sy = d*ix + e*iy + f
    created_at: str


CalibrationProfile = AffineCalibrationProfile

# ── Part 4: rich calibration record ──────────────────────────────────────────

@dataclass
class CalibrationSample:
    """
    One calibration sample collected during a 13-point session.

    screen_x / screen_y   : normalised target position (0–1)
    l_iris_rel_x/y        : left  iris position relative to eye socket
    r_iris_rel_x/y        : right iris position relative to eye socket
    head_offset_x/y       : head movement offset (Part 3)
    l_eye_openness        : left  eye aspect ratio
    r_eye_openness        : right eye aspect ratio
    """
    screen_x:        float
    screen_y:        float
    l_iris_rel_x:    float
    l_iris_rel_y:    float
    r_iris_rel_x:    float
    r_iris_rel_y:    float
    head_offset_x:   float
    head_offset_y:   float
    l_eye_openness:  float
    r_eye_openness:  float

    def to_feature(self) -> list[float]:
        """Return the 8-element vector expected by _RegressionGazeModel."""
        return [
            self.l_iris_rel_x, self.l_iris_rel_y,
            self.r_iris_rel_x, self.r_iris_rel_y,
            self.head_offset_x, self.head_offset_y,
            self.l_eye_openness, self.r_eye_openness,
        ]


# ── Part 4: 13-point grid ─────────────────────────────────────────────────────
_M = 0.10          # margin
_F = 1.0 - _M

DEFAULT_TARGETS_13: list[tuple[float, float]] = [
    (_M, _M), (0.5, _M), (_F, _M),          # top row
    (_M, 0.35), (_F, 0.35),                  # upper-mid row
    (_M, 0.5),  (0.5, 0.5), (_F, 0.5),      # middle row
    (_M, 0.65), (_F, 0.65),                  # lower-mid row
    (_M, _F),  (0.5, _F),  (_F, _F),         # bottom row
]

# Legacy 5-point kept for callers that still use it
DEFAULT_TARGETS: list[tuple[float, float]] = [
    (0.5, 0.5),
    (0.1, 0.1), (0.9, 0.1),
    (0.1, 0.9), (0.9, 0.9),
]

_DATA_DIR = Path("data")
_CALIB_JSON = _DATA_DIR / "calibration_features.json"


# ── screen helper ─────────────────────────────────────────────────────────────

def _screen_size() -> tuple[int, int]:
    try:
        import ctypes
        u32 = ctypes.windll.user32  # type: ignore[attr-defined]
        u32.SetProcessDPIAware()
        return u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)
    except Exception:
        return 1920, 1080


# ── legacy affine map (for AffineCalibrationProfile) ─────────────────────────

def map_gaze(
    profile: AffineCalibrationProfile,
    iris_x: float,
    iris_y: float,
) -> tuple[float, float]:
    a, b, c = profile.coeffs_x
    d, e, f = profile.coeffs_y
    sx = a * iris_x + b * iris_y + c
    sy = d * iris_x + e * iris_y + f
    return (
        float(np.clip(sx, 0.0, 1.0)),
        float(np.clip(sy, 0.0, 1.0)),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Part 4: feature-collecting calibration routine
# ═══════════════════════════════════════════════════════════════════════════════

def collect_calibration_samples(
    tracker,                                        # MediaPipeTracker instance
    targets: Optional[list[tuple[float, float]]] = None,
    samples_per_point: int = 30,
    display_widget=None,                            # optional PySide6 widget
) -> list[CalibrationSample]:
    """
    Collect feature-vector samples during an interactive calibration session.

    For each target point:
      1. Show the dot (via display_widget or OpenCV fullscreen).
      2. Collect `samples_per_point` frames of iris data.
      3. Average the features → one CalibrationSample.

    Returns a list of CalibrationSample objects ready for fit_from_samples().

    This function is display-agnostic: pass your existing PySide6 overlay
    widget as `display_widget` and it will call
    ``display_widget.show_target(tx, ty)`` / ``display_widget.hide_target()``
    if those methods exist.  Without a widget it falls back to OpenCV.
    """
    if targets is None:
        targets = DEFAULT_TARGETS_13

    sw, sh = _screen_size()
    samples: list[CalibrationSample] = []

    for idx, (tx, ty) in enumerate(targets):
        # ── show target ───────────────────────────────────────────────────────
        if display_widget is not None and hasattr(display_widget, "show_target"):
            display_widget.show_target(tx, ty, idx, len(targets))
        else:
            _show_opencv_target(sw, sh, tx, ty, idx, len(targets))

        # ── collect frames ────────────────────────────────────────────────────
        raw: list[list[float]] = []
        attempts = 0
        while len(raw) < samples_per_point and attempts < samples_per_point * 5:
            attempts += 1
            gp = tracker.get_gaze()
            if gp is None or gp.confidence < 0.5:
                time.sleep(0.033)
                continue

            # Pull the last feature vector the tracker computed.
            # MediaPipeTracker exposes _last_feature as a public helper below.
            feat = getattr(tracker, "get_last_feature", lambda: None)()
            if feat is None or len(feat) != 8:
                time.sleep(0.033)
                continue
            raw.append(feat)
            time.sleep(0.033)

        # ── average ───────────────────────────────────────────────────────────
        if raw:
            arr = np.array(raw, dtype=np.float64)
            avg = arr.mean(axis=0).tolist()
        else:
            avg = [0.5, 0.5, 0.5, 0.5, 0.0, 0.0, 0.25, 0.25]

        samples.append(CalibrationSample(
            screen_x=tx,       screen_y=ty,
            l_iris_rel_x=avg[0], l_iris_rel_y=avg[1],
            r_iris_rel_x=avg[2], r_iris_rel_y=avg[3],
            head_offset_x=avg[4], head_offset_y=avg[5],
            l_eye_openness=avg[6], r_eye_openness=avg[7],
        ))

    if display_widget is not None and hasattr(display_widget, "hide_target"):
        display_widget.hide_target()
    else:
        cv2.destroyAllWindows()

    return samples


def fit_from_samples(
    tracker,
    samples: list[CalibrationSample],
) -> None:
    """
    Fit the regression model on collected samples and save to disk.

    Calls tracker.fit_regression() which persists data/gaze_model.joblib.
    """
    features = [s.to_feature() for s in samples]
    sx       = [s.screen_x     for s in samples]
    sy       = [s.screen_y     for s in samples]
    tracker.fit_regression(features, sx, sy)
    save_feature_calibration(samples)
    print(f"[CAL] Regression model fitted on {len(samples)} points.")


# ── Part 4: JSON persistence ──────────────────────────────────────────────────

def save_feature_calibration(
    samples: list[CalibrationSample],
    path: Optional[Path] = None,
) -> Path:
    """Save all calibration feature samples to JSON."""
    if path is None:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = _CALIB_JSON
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n_points":   len(samples),
        "samples":    [asdict(s) for s in samples],
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"[CAL] Feature calibration saved to {path}")
    return path


def load_feature_calibration(
    path: Optional[Path] = None,
) -> list[CalibrationSample]:
    if path is None:
        path = _CALIB_JSON
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [CalibrationSample(**s) for s in raw["samples"]]


# ── OpenCV fallback target display ────────────────────────────────────────────

def _show_opencv_target(
    sw: int, sh: int,
    tx: float, ty: float,
    idx: int, total: int,
) -> None:
    canvas = np.zeros((sh, sw, 3), dtype=np.uint8)
    cx, cy = int(tx * sw), int(ty * sh)
    cv2.circle(canvas, (cx, cy), 30, (0, 80, 0), -1)
    cv2.circle(canvas, (cx, cy), 13, (0, 255, 0), -1)
    cv2.circle(canvas, (cx, cy),  4, (255, 255, 255), -1)
    msg = f"Point {idx + 1}/{total} -- look at the dot, capturing in 1s..."
    cv2.putText(canvas, msg, (30, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (200, 200, 200), 2)
    win = "Calibration"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(win, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    cv2.imshow(win, canvas)
    cv2.waitKey(1000)   # 1 s settle time before sampling


def _trimmed_mean(vals: list[float], pct: float = 10.0) -> float:
    arr = np.asarray(vals)
    lo, hi = np.percentile(arr, [pct, 100.0 - pct])
    mask    = (arr >= lo) & (arr <= hi)
    trimmed = arr[mask]
    return float(trimmed.mean()) if len(trimmed) else float(arr.mean())


def _fit_affine(
    pairs: list[tuple[tuple[float, float], tuple[float, float]]],
) -> AffineCalibrationProfile:
    n  = len(pairs)
    A  = np.ones((n, 3), dtype=np.float64)
    bx = np.zeros(n, dtype=np.float64)
    by = np.zeros(n, dtype=np.float64)
    for i, ((sx, sy), (rx, ry)) in enumerate(pairs):
        A[i, 0] = rx
        A[i, 1] = ry
        bx[i]   = sx
        by[i]   = sy
    cx, *_ = np.linalg.lstsq(A, bx, rcond=None)
    cy, *_ = np.linalg.lstsq(A, by, rcond=None)
    return AffineCalibrationProfile(
        coeffs_x=cx.tolist(),
        coeffs_y=cy.tolist(),
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )


def save_calibration(
    profile: AffineCalibrationProfile,
    path: Optional[Path] = None,
) -> Path:
    if path is None:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = _DATA_DIR / "calibration.json"
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(profile), indent=2), encoding="utf-8")
    return path


def load_calibration(path: Optional[Path] = None) -> AffineCalibrationProfile:
    if path is None:
        path = _DATA_DIR / "calibration.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return AffineCalibrationProfile(**data)