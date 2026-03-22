"""
MediaPipeTracker — iris gaze provider.

Improvements over original:
  • SHOW_DEBUG_LANDMARKS flag (Part 1) — set False to hide all dots
  • Richer landmark extraction + relative iris features (Part 2)
  • Head-pose compensation via nose + eye-corner reference (Part 3)
  • Feature-vector extraction for regression calibration (Part 4)
  • Regression model (Ridge / SVR / MLP) for gaze mapping (Part 5)
  • EMA smoothing + confidence gate + dead-zone (Part 6)

contracts.py interface is fully preserved — GazePoint and GazeProvider
protocol are unchanged.
"""
from __future__ import annotations

import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from app.contracts import GazePoint
from app.vision.smoothing import OneEuroFilter

# ═══════════════════════════════════════════════════════════════════════════════
# PART 1 — debug-landmark visibility
# Set False in production to hide all landmark dots from the camera feed.
# ═══════════════════════════════════════════════════════════════════════════════
SHOW_DEBUG_LANDMARKS: bool = True

# ── landmark indices ──────────────────────────────────────────────────────────
LEFT_IRIS  = 468
RIGHT_IRIS = 473

LEFT_EYE_OUTER  = 33
LEFT_EYE_INNER  = 133
LEFT_EYE_TOP    = 159
LEFT_EYE_BOT    = 145

RIGHT_EYE_OUTER = 362
RIGHT_EYE_INNER = 263
RIGHT_EYE_TOP   = 386
RIGHT_EYE_BOT   = 374

NOSE_TIP = 4   # Part 3 head reference

FACE_OVAL = [
    10,338,297,332,284,251,389,356,454,323,361,288,
    397,365,379,378,400,377,152,148,176,149,150,136,
    172,58,132,93,234,127,162,21,54,103,67,109,10,
]

_MODEL_URL  = ("https://storage.googleapis.com/mediapipe-models/"
               "face_landmarker/face_landmarker/float16/latest/face_landmarker.task")
_MODEL_PATH = Path("data/face_landmarker.task")

# ── distance zones ────────────────────────────────────────────────────────────
DIST_CLOSE  = 0.55
DIST_NEAR   = 0.40
DIST_MID    = 0.25
DIST_FAR    = 0.15

AMP_CLOSE   = 3.0
AMP_NEAR    = 4.0
AMP_MID     = 5.5
AMP_FAR     = 7.0
AMP_VERYFAR = 9.0

# ═══════════════════════════════════════════════════════════════════════════════
# PART 6 — smoothing / confidence / dead-zone constants
# ═══════════════════════════════════════════════════════════════════════════════
EMA_ALPHA       = 0.30   # 0=frozen, 1=raw — 0.25-0.35 suits 30 fps
CONFIDENCE_GATE = 0.4    # below this -> freeze last stable point
DEAD_ZONE       = 0.004  # normalised; skip micro-tremor updates


# ─────────────────────────────────────────────────────────────────────────────
# private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_model() -> str:
    _MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _MODEL_PATH.exists():
        print("Downloading MediaPipe face landmarker model (~30 MB)...")
        urllib.request.urlretrieve(_MODEL_URL, _MODEL_PATH)
        print("Download complete.")
    return str(_MODEL_PATH)


def _find_camera() -> int:
    for i in range(6):
        for backend in (cv2.CAP_DSHOW, cv2.CAP_ANY):
            cap = cv2.VideoCapture(i, backend)
            if cap.isOpened():
                for _ in range(5):
                    ok, frame = cap.read()
                    if ok and frame is not None and frame.mean() > 1.0:
                        cap.release()
                        return i
                cap.release()
    return 0


def _get_screen_size() -> tuple[int, int]:
    try:
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen:
            g = screen.geometry()
            return g.width(), g.height()
    except Exception:
        pass
    try:
        import ctypes
        u32 = ctypes.windll.user32  # type: ignore[attr-defined]
        u32.SetProcessDPIAware()
        return u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)
    except Exception:
        pass
    return 1920, 1080


def _distance_zone(face_h_ratio: float) -> tuple[float, str]:
    if face_h_ratio > DIST_CLOSE:
        return AMP_CLOSE,   "Very Close"
    elif face_h_ratio > DIST_NEAR:
        return AMP_NEAR,    "Near"
    elif face_h_ratio > DIST_MID:
        return AMP_MID,     "Normal"
    elif face_h_ratio > DIST_FAR:
        return AMP_FAR,     "Far"
    else:
        return AMP_VERYFAR, "Very Far"


# ═══════════════════════════════════════════════════════════════════════════════
# PART 2 — landmark extraction helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _iris_in_eye(
    iris_x: float, iris_y: float,
    outer_x: float, outer_y: float,
    inner_x: float, inner_y: float,
    top_x:   float, top_y:   float,
    bot_x:   float, bot_y:   float,
) -> tuple[float, float]:
    """
    Return iris position normalised within the eye socket (0-1 each axis).

    WHY RELATIVE FEATURES ARE BETTER THAN RAW POSITIONS
    ─────────────────────────────────────────────────────
    Raw iris (x, y) in the camera frame encodes BOTH where the eye is looking
    AND where the head is sitting in the frame.  If you shift your head 2 cm
    to the right, the raw iris x increases by ~20–40 px even though your gaze
    direction is unchanged.

    By computing   nx = (iris_x - outer_x) / (inner_x - outer_x)
    we keep only the *fraction* of the iris travel within the eye socket — which
    is purely eyeball rotation.  A head shift moves the iris and the eye corners
    by the same amount, so after division the fraction is unchanged.

    Result: 0.5, 0.5 means the iris is centred → looking straight ahead.
    """
    eye_w = inner_x - outer_x
    eye_h = bot_y   - top_y
    if abs(eye_w) < 1e-6 or abs(eye_h) < 1e-6:
        return 0.5, 0.5
    nx = (iris_x - outer_x) / eye_w
    ny = (iris_y - top_y)   / eye_h
    return float(np.clip(nx, 0.0, 1.0)), float(np.clip(ny, 0.0, 1.0))


def _eye_openness(
    top_x: float, top_y: float,
    bot_x: float, bot_y: float,
    outer_x: float, outer_y: float,
    inner_x: float, inner_y: float,
) -> float:
    """Eye aspect ratio: vertical gap / horizontal span.  ~0.25 open, ~0 closed."""
    vert  = abs(bot_y - top_y)
    horiz = abs(inner_x - outer_x)
    return float(vert / horiz) if horiz > 1e-6 else 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# PART 3 — head-movement compensation
# ═══════════════════════════════════════════════════════════════════════════════

class _HeadReference:
    """
    Records a neutral head pose and returns per-frame offset vectors,
    with slow EMA re-anchoring to prevent long-session drift.

    THE FIX vs original
    ───────────────────
    Original recorded frame-0 as neutral and never updated it.  After
    10+ minutes of use the accumulated offset caused a constant gaze bias.

    Now the reference slowly follows the current position (alpha=0.02),
    so sustained head shifts over many minutes are absorbed silently, but
    rapid movements (< ~50 frames) still produce a valid offset for
    head-compensation.

    MATH
    ────
    offset    = mean(current_i - reference_i)   for i in {nose, leye, reye}
    reference += alpha * (current - reference)   ← slow re-anchor each frame
    """

    # Seconds of face data to average before committing neutral pose.
    # During this window the user should sit still and look forward.
    NEUTRAL_CAPTURE_S = 2.0
    # After neutral is set, how quickly the reference re-anchors to absorb
    # long-session head drift. alpha=0.005 means ~200 frames (≈6s) to absorb
    # a sustained shift — fast enough to handle fatigue, slow enough not to
    # corrupt the short-term offset signal.
    REANCHOR_ALPHA = 0.005

    def __init__(self, fps: float = 30.0) -> None:
        self._fps = fps
        # Accumulation buffer during neutral-capture window
        self._capture_buf: list[tuple] = []   # list of (nose,leye,reye)
        self._capture_done: bool       = False
        self._frames_needed: int       = int(self.NEUTRAL_CAPTURE_S * fps)

        self._nose_ref: Optional[tuple[float, float]] = None
        self._leye_ref: Optional[tuple[float, float]] = None
        self._reye_ref: Optional[tuple[float, float]] = None

    def reset(self) -> None:
        """Re-run neutral capture — call this when user triggers re-center."""
        self._capture_buf  = []
        self._capture_done = False
        self._nose_ref = self._leye_ref = self._reye_ref = None

    @property
    def is_neutral_set(self) -> bool:
        """True once the 2-second neutral capture is complete."""
        return self._capture_done

    def update(
        self,
        nose: tuple[float, float],
        leye: tuple[float, float],
        reye: tuple[float, float],
    ) -> tuple[float, float]:
        """Feed current-frame reference points; return (offset_x, offset_y)."""

        # ── Phase 1: neutral-capture window (first 2 seconds) ────────────────
        # Accumulate frames, then average them into the neutral reference.
        # This is more robust than using frame-0, which may catch the user
        # mid-movement as the app opens.
        if not self._capture_done:
            self._capture_buf.append((nose, leye, reye))
            if len(self._capture_buf) >= self._frames_needed:
                # Average all captured frames into the neutral reference
                n = len(self._capture_buf)
                self._nose_ref = (
                    sum(f[0][0] for f in self._capture_buf) / n,
                    sum(f[0][1] for f in self._capture_buf) / n,
                )
                self._leye_ref = (
                    sum(f[1][0] for f in self._capture_buf) / n,
                    sum(f[1][1] for f in self._capture_buf) / n,
                )
                self._reye_ref = (
                    sum(f[2][0] for f in self._capture_buf) / n,
                    sum(f[2][1] for f in self._capture_buf) / n,
                )
                self._capture_done = True
                self._capture_buf  = []   # free memory
                print(f"[HeadRef] Neutral pose captured from {n} frames.")
            return 0.0, 0.0   # return zero offset during capture window

        # ── Phase 2: normal operation — compute offset from neutral ───────────
        dx = ((nose[0] - self._nose_ref[0]) +
              (leye[0] - self._leye_ref[0]) +
              (reye[0] - self._reye_ref[0])) / 3.0
        dy = ((nose[1] - self._nose_ref[1]) +
              (leye[1] - self._leye_ref[1]) +
              (reye[1] - self._reye_ref[1])) / 3.0

        # ── Slow re-anchor: absorbs long-session postural drift ───────────────
        # REANCHOR_ALPHA = 0.005 → reference catches up over ~200 frames (≈6s).
        # Fast enough to absorb fatigue-induced head drift over minutes.
        # Slow enough that deliberate head movements (< ~50 frames) still
        # produce a valid offset for navigation.
        a = self.REANCHOR_ALPHA
        self._nose_ref = (
            self._nose_ref[0] + a * (nose[0] - self._nose_ref[0]),
            self._nose_ref[1] + a * (nose[1] - self._nose_ref[1]),
        )
        self._leye_ref = (
            self._leye_ref[0] + a * (leye[0] - self._leye_ref[0]),
            self._leye_ref[1] + a * (leye[1] - self._leye_ref[1]),
        )
        self._reye_ref = (
            self._reye_ref[0] + a * (reye[0] - self._reye_ref[0]),
            self._reye_ref[1] + a * (reye[1] - self._reye_ref[1]),
        )

        return float(dx), float(dy)


# ═══════════════════════════════════════════════════════════════════════════════
# PART 5 — regression gaze model
# ═══════════════════════════════════════════════════════════════════════════════

class _RegressionGazeModel:
    """
    Scikit-learn regressor pair: one for screen_x, one for screen_y.

    FULL PIPELINE
    ─────────────
    Calibration:
      1. Show 13 dots, collect N frames per dot.
      2. Per frame → build_feature() → 8-float vector.
      3. Average N vectors → 1 sample per dot → 13 samples total.
      4. fit(features, screen_x_list, screen_y_list)
      5. Saved to data/gaze_model.joblib via save().

    Runtime:
      1. Each frame → build_feature() → predict() → (screen_x, screen_y).
      2. Apply EMA + dead-zone → final GazePoint.

    Model choice:
      MODEL_TYPE = "ridge"  — Ridge regression. Fast, robust with few samples.
                              Best for < ~30 calibration points.
      MODEL_TYPE = "svr"    — RBF-kernel SVR. More flexible, handles
                              nonlinear gaze distortions. Good from ~13 pts up.
      MODEL_TYPE = "mlp"    — Small MLP. Best accuracy when you have 30+ points
                              and want to capture complex nonlinearities.
    """

    MODEL_TYPE: str = "ridge"  # "ridge" | "svr" | "mlp"

    def __init__(self) -> None:
        self._model_x = None
        self._model_y = None
        self._fitted  = False

    # ── Part 4: feature vector ────────────────────────────────────────────────

    @staticmethod
    def build_feature(
        l_nx: float, l_ny: float,
        r_nx: float, r_ny: float,
        head_dx: float, head_dy: float,
        l_ear: float,
        r_ear: float,
    ) -> list[float]:
        """
        8-element feature vector.

        [0] l_nx     — left  iris relative x in eye socket (0=left, 1=right)
        [1] l_ny     — left  iris relative y in eye socket (0=top,  1=bottom)
        [2] r_nx     — right iris relative x
        [3] r_ny     — right iris relative y
        [4] head_dx  — head horizontal offset from neutral (Part 3)
        [5] head_dy  — head vertical   offset from neutral
        [6] l_ear    — left  eye aspect ratio (openness)
        [7] r_ear    — right eye aspect ratio (openness)
        """
        return [l_nx, l_ny, r_nx, r_ny, head_dx, head_dy, l_ear, r_ear]

    # ── model factory ─────────────────────────────────────────────────────────

    @classmethod
    def _make_model(cls):
        from sklearn.linear_model import Ridge
        from sklearn.svm import SVR
        from sklearn.neural_network import MLPRegressor
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler

        if cls.MODEL_TYPE == "svr":
            return Pipeline([
                ("scaler", StandardScaler()),
                ("reg",    SVR(kernel="rbf", C=10.0, epsilon=0.01)),
            ])
        if cls.MODEL_TYPE == "mlp":
            return Pipeline([
                ("scaler", StandardScaler()),
                ("reg",    MLPRegressor(
                    hidden_layer_sizes=(32, 16),
                    activation="relu",
                    max_iter=2000,
                    random_state=0,
                )),
            ])
        # default: ridge
        return Pipeline([
            ("scaler", StandardScaler()),
            ("reg",    Ridge(alpha=1.0)),
        ])

    def fit(
        self,
        features:  list[list[float]],
        screen_x:  list[float],
        screen_y:  list[float],
    ) -> None:
        X  = np.array(features, dtype=np.float64)
        sx = np.array(screen_x, dtype=np.float64)
        sy = np.array(screen_y, dtype=np.float64)
        self._model_x = self._make_model()
        self._model_y = self._make_model()
        self._model_x.fit(X, sx)
        self._model_y.fit(X, sy)
        self._fitted = True

    def predict(self, feature: list[float]) -> tuple[float, float]:
        if not self._fitted:
            # No model fitted yet — return screen centre so the cursor starts
            # in a neutral position rather than the top-left corner.
            # feature[0]/[1] are eye-socket-relative values (0–1 within the
            # eye), NOT screen coords, so returning them directly would lock
            # the cursor near the top-left of the screen.
            return 0.5, 0.5
        X  = np.array([feature], dtype=np.float64)
        px = float(self._model_x.predict(X)[0])
        py = float(self._model_y.predict(X)[0])
        return float(np.clip(px, 0.0, 1.0)), float(np.clip(py, 0.0, 1.0))

    def save(self, path: Path) -> None:
        import joblib
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"x": self._model_x, "y": self._model_y}, path)

    def load(self, path: Path) -> bool:
        try:
            import joblib
            d = joblib.load(path)
            self._model_x = d["x"]
            self._model_y = d["y"]
            self._fitted  = True
            return True
        except Exception:
            return False

    @property
    def is_fitted(self) -> bool:
        return self._fitted


_REGRESSION_MODEL_PATH = Path("data/gaze_model.joblib")

# ═══════════════════════════════════════════════════════════════════════════════
# Head-pose navigator
# ═══════════════════════════════════════════════════════════════════════════════

class _HeadPoseNavigator:
    """
    Converts raw landmark deltas into keyboard-navigable (x, y) in [0, 1].

    Pipeline per frame
    ──────────────────
    head_dx/head_dy  (raw offset from _HeadReference, normalised camera units)
        │
        ▼
    1. Dead zone  — suppress tremor below HEAD_DZ_NORM threshold.
       Output is exactly 0.5 (centre) while user is still.

    2. Parabolic sensitivity curve  (HEAD_POWER = 0.5)
       f(t) = t^0.5  (square root = ease-out parabola)
       This is the core of the Snapchat "snap" feel:
         • Small tilts (t near 0) → f(t) grows fast → responsive near centre
         • Larger tilts (t near 1) → f(t) grows slowly → controlled at edges
       Concretely at HEAD_RANGE = 0.055 (≈ 2-3 inch nose movement):
         • 0.5-inch tilt → 30% of cursor travel (fast start)
         • 1.5-inch tilt → 52% of cursor travel
         • 2.5-inch tilt → 67% of cursor travel
         • 3-inch tilt   → 74% of cursor travel
       This means the centre keys (A-G, H-L) are reached with tiny movements,
       while edge keys (Q, P, SPACE) are reachable without straining.

    3. Edge snapping  — if cursor is within EDGE_SNAP_ZONE of 0.0 or 1.0,
       snap it all the way to the edge. Eliminates the "almost there" feeling
       where keys at the extreme edge are unreachable without perfect accuracy.
       Snap zone = 10% of each edge (cursor > 0.90 → snap to 1.0, etc.)

    4. NOTE: no OEF here — the main tracker OEF in _loop() handles smoothing.
       Having two OEFs in series adds lag without benefit.

    Tunable constants (class-level, change before creating instance):
        HEAD_RANGE     : normalised units for full edge sweep (default 0.055)
        HEAD_DZ_NORM   : dead-zone radius (default 0.006)
        HEAD_POWER     : curve exponent <1=ease-out, >1=ease-in (default 0.5)
        EDGE_SNAP_ZONE : fraction near edge that triggers snap (default 0.10)
    """

    # ±5.5% of camera frame = full keyboard sweep.
    # At 60cm from webcam, 2-3 inches nose movement ≈ 5-6% frame width.
    # This means a 2.5-inch tilt reaches ~74% of keyboard width (generous).
    HEAD_RANGE     = 0.055

    # Dead-zone: ignore wobble below ±0.6% of frame.
    # At 60cm this ≈ 3-4mm — below the threshold of intentional movement.
    HEAD_DZ_NORM   = 0.006

    # Square-root curve: fast start near centre, controlled near edges.
    # 0.5 = parabolic ease-out. Use 0.4 for even more aggressive acceleration.
    HEAD_POWER     = 0.5

    # Snap-to-edge zone: within 10% of edge → snap to 0.0 or 1.0.
    # This makes Space, Q, P, BKSP reliably reachable without pixel-perfect aim.
    EDGE_SNAP_ZONE = 0.10

    def __init__(self) -> None:
        pass   # no internal filter — smoothing handled by main OEF

    def compute(self, head_dx: float, head_dy: float) -> tuple[float, float]:
        """
        head_dx, head_dy : raw offset from _HeadReference.update()
        Returns (x_norm, y_norm) in [0, 1], centre = 0.5.
        """
        import numpy as np

        def _map(delta: float) -> float:
            # ── 1. dead zone ──────────────────────────────────────────────────
            if abs(delta) < self.HEAD_DZ_NORM:
                return 0.5

            # ── 2. rescale: dead-zone edge → 0.0, HEAD_RANGE → 1.0 ───────────
            sign      = 1.0 if delta > 0 else -1.0
            magnitude = (abs(delta) - self.HEAD_DZ_NORM) / (
                         self.HEAD_RANGE - self.HEAD_DZ_NORM)
            magnitude = min(1.0, magnitude)   # hard clamp at range boundary

            # ── 3. parabolic ease-out (square-root curve) ─────────────────────
            # f(t) = t^0.5 — fast initial movement, controlled near edges.
            # This is the "snap" feel: small tilt = large cursor response
            # near centre, diminishing returns as you approach the edge key.
            curved = magnitude ** self.HEAD_POWER

            # ── 4. map to [0, 1] around 0.5 ──────────────────────────────────
            pos = 0.5 + sign * curved * 0.5

            # ── 5. edge snapping ──────────────────────────────────────────────
            # Within EDGE_SNAP_ZONE of either edge → snap to that edge.
            # Makes the outermost keys (Q, P, Space, BKSP) always reachable.
            if pos <= self.EDGE_SNAP_ZONE:
                return 0.0
            if pos >= (1.0 - self.EDGE_SNAP_ZONE):
                return 1.0

            return float(pos)

        raw_x = _map(head_dx)
        raw_y = _map(head_dy)
        return float(np.clip(raw_x, 0.0, 1.0)), float(np.clip(raw_y, 0.0, 1.0))

    def reset(self) -> None:
        pass   # stateless — nothing to reset

# ═══════════════════════════════════════════════════════════════════════════════
# Main tracker
# ═══════════════════════════════════════════════════════════════════════════════

class MediaPipeTracker:

    def __init__(self, camera_index: int = -1) -> None:
        self._cam_idx = _find_camera() if camera_index == -1 else camera_index
        self._running = False
        self._cap:    Optional[cv2.VideoCapture] = None
        self._thread: Optional[threading.Thread] = None
        self._lock    = threading.Lock()

        # Mirror toggle — flip landmark x coords for cameras that present
        # a non-mirrored view (e.g. DroidCam, some external webcams).
        # True  = flip frame before MediaPipe (default — matches webcam mirror)
        # False = pass frame as-is (for cameras that are already correct)
        self.mirror_input: bool = True

        self._gaze_point:   Optional[GazePoint]  = None
        self._latest_frame: Optional[np.ndarray] = None

        # legacy screen-space affine matrix
        self._calib_matrix: Optional[np.ndarray] = None
        # keyboard-fitted affine matrix (maps iris → kb widget pixels)
        self._kb_matrix:    Optional[np.ndarray] = None

        self._distance_label = "Unknown"
        self._amplification  = AMP_MID

        # Part 3
        self._head_ref = _HeadReference()

        # Part 5
        self._reg_model = _RegressionGazeModel()
        self._reg_model.load(_REGRESSION_MODEL_PATH)  # silent if missing

        # auto-load keyboard calibration if it exists
        try:
            from app.services.stores import load_kb_calib
            _kb_data = load_kb_calib()
            if _kb_data and _kb_data.get('matrix'):
                self.load_kb_calibration(_kb_data['matrix'])
        except Exception:
            pass

        # Part 6 — One Euro Filter (replaces fixed EMA)
        # beta=0.25: more responsive during fast head movements (saccades)
        # min_cutoff=0.8: slightly more aggressive rest smoothing
        self._oef = OneEuroFilter(fps=30.0, min_cutoff=0.8, beta=0.25)
        self._sx: Optional[float] = None
        self._sy: Optional[float] = None

        # Head-pose navigator
        self._head_nav = _HeadPoseNavigator()

        # Iris-head fusion weight (0=pure head, 1=pure iris)
        # Calibrated at runtime: high iris confidence → more iris weight
        self._fusion_iris_weight: float = 0.7

        # Part 4 — last feature vector (exposed for calibration data collection)
        self._last_feature: Optional[list[float]] = None

        # Head navigation
        self._nose_x: Optional[float] = None
        self._nose_y: Optional[float] = None
        self._head_nav_pos: Optional[tuple[float,float]] = None

        self._sw, self._sh = _get_screen_size()

        from mediapipe.tasks import python as mp_tasks
        from mediapipe.tasks.python import vision as mp_vision

        model_path = _ensure_model()
        base_opts  = mp_tasks.BaseOptions(model_asset_path=model_path)
        opts = mp_vision.FaceLandmarkerOptions(
            base_options=base_opts,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=False,
            num_faces=1,
        )
        self._landmarker = mp_vision.FaceLandmarker.create_from_options(opts)

    # ── calibration — contracts.py interface (preserved) ─────────────────────

    def load_calibration(self, screen_pts: list, gaze_pts: list) -> None:
        """
        Load legacy affine calibration (contracts.py / calibration_store).

        If a regression model is already fitted it takes priority at runtime.
        This method is preserved so CalibrationPanel / calibration_store
        work without any modification.
        """
        if len(screen_pts) < 4 or len(gaze_pts) < 4:
            return
        src = np.array(gaze_pts,   dtype=np.float32)
        dst = np.array(screen_pts, dtype=np.float32)
        M, _ = cv2.estimateAffine2D(src, dst, method=cv2.RANSAC)
        if M is not None:
            self._calib_matrix = M
        self._head_ref.reset()
        self._sx = self._sy = None

    def load_kb_calibration(self, matrix) -> None:
        """
        Load a keyboard-fitted affine matrix.

        matrix  : 2×3 numpy array or nested list from cv2.estimateAffine2D
                  maps (eye_x, eye_y) → (kb_px, kb_py)
        """
        import numpy as _np
        if not isinstance(matrix, _np.ndarray):
            matrix = _np.array(matrix, dtype=_np.float32)
        if matrix.shape == (2, 3):
            self._kb_matrix = matrix
            self._head_ref.reset()
            self._head_nav.reset()
            self._oef.reset()
            self._sx = self._sy = None

    def map_to_kb(self, eye_x: float, eye_y: float) -> tuple[float, float]:
        """
        Map normalised iris-in-eye coords to keyboard widget pixel coords.

        Returns widget-local (px, py).  If no kb calibration is loaded,
        returns (eye_x * kb_ref_width, eye_y * kb_ref_height) as a rough
        fallback — still better than nothing.
        """
        if self._kb_matrix is not None:
            pt  = np.array([[[eye_x, eye_y]]], dtype=np.float32)
            out = cv2.transform(pt, self._kb_matrix)
            return float(out[0, 0, 0]), float(out[0, 0, 1])
        # no kb calibration — passthrough scaled to 1280×400 reference
        return eye_x * 1280.0, eye_y * 400.0

        # ── regression calibration — new API ─────────────────────────────────────

    def fit_regression(
        self,
        features:  list[list[float]],
        screen_x:  list[float],
        screen_y:  list[float],
    ) -> None:
        """Fit the regression gaze model from collected calibration data."""
        self._reg_model.fit(features, screen_x, screen_y)
        self._reg_model.save(_REGRESSION_MODEL_PATH)
        self._head_ref.reset()
        self._sx = self._sy = None

    def _map_gaze(
        self,
        ix: float, iy: float,
        feature: list[float],
    ) -> tuple[float, float]:
        """Regression model if fitted; else fall back to legacy affine."""
        if self._reg_model.is_fitted:
            return self._reg_model.predict(feature)
        if self._calib_matrix is not None:
            pt  = np.array([[[ix, iy]]], dtype=np.float32)
            out = cv2.transform(pt, self._calib_matrix)
            return (float(np.clip(out[0, 0, 0], 0.0, 1.0)),
                    float(np.clip(out[0, 0, 1], 0.0, 1.0)))
        return float(np.clip(ix, 0.0, 1.0)), float(np.clip(iy, 0.0, 1.0))

    # ── GazeProvider protocol ─────────────────────────────────────────────────

    def start(self) -> None:
        # Try DSHOW first; if it opens but delivers no frames, fall back to CAP_ANY
        self._cap = cv2.VideoCapture(self._cam_idx, cv2.CAP_DSHOW)
        if not self._cap.isOpened():
            self._cap = cv2.VideoCapture(self._cam_idx)

        # warm-up reads — verify we actually get frames
        got_frame = False
        for _ in range(15):
            ok, frame = self._cap.read()
            if ok and frame is not None and frame.size > 0:
                got_frame = True
                break
            time.sleep(0.02)

        if not got_frame:
            # DSHOW failed silently — retry with CAP_ANY
            print("[Tracker] DSHOW gave no frames, retrying with CAP_ANY…")
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = cv2.VideoCapture(self._cam_idx)
            for _ in range(10):
                self._cap.read()

        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self._cap and self._cap.isOpened():
            self._cap.release()

    def get_gaze(self) -> Optional[GazePoint]:
        with self._lock:
            return self._gaze_point

    def get_latest_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            return self._latest_frame

    def get_distance_info(self) -> tuple[str, float]:
        with self._lock:
            return self._distance_label, self._amplification

    def get_last_feature(self) -> Optional[list[float]]:
        """Return the most recent 8-element feature vector (Part 4 / Part 5)."""
        with self._lock:
            return self._last_feature

    def get_head_nav_pos(self) -> Optional[tuple[float, float]]:
        """Return current head-pose navigator output (x, y) in [0,1]."""
        with self._lock:
            return self._head_nav_pos

    def get_nose_position(self) -> Optional[tuple[float, float]]:
        """
        Return the latest normalised nose-tip (x, y), or None if no face.

        Returns None when no face was detected in the last frame.
        Callers must check for None before using the value.
        """
        with self._lock:
            if self._nose_x is None:
                return None
            return self._nose_x, self._nose_y

    @property
    def neutral_ready(self) -> bool:
        """True once the 2-second neutral pose capture is complete."""
        return self._head_ref.is_neutral_set

    def recenter(self) -> None:
        """
        Re-run neutral head pose capture.
        Call this when the user wants to re-centre the head tracking
        (e.g. after moving their chair, or via a toolbar button).
        The next 2 seconds of face data will be averaged into the new neutral.
        """
        self._head_ref.reset()
        self._head_nav.reset()
        self._oef.reset()
        self._sx = self._sy = None
        print("[Tracker] Re-centering — hold still for 2 seconds...")

    def set_mirror(self, mirrored: bool) -> None:
        """
        Set whether the camera input should be horizontally flipped.

        mirrored=True  (default): flip the frame before landmark detection.
            Use this for laptop webcams and any camera that presents a
            mirror-style image (left on screen = left in real life).

        mirrored=False: pass the frame as-is.
            Use this for DroidCam, some external USB webcams, or any
            camera that already presents a non-mirrored (correct) view.

        Changing this at runtime resets the head reference and OEF so
        the tracker re-anchors immediately in the new orientation.
        """
        if self.mirror_input == mirrored:
            return   # no change
        self.mirror_input = mirrored
        # reset all stateful components so they re-anchor in new orientation
        self._head_ref.reset()
        self._head_nav.reset()
        self._oef.reset()
        self._sx = self._sy = None
        print(f"[Tracker] mirror_input = {mirrored}")

    # ── main loop ─────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        import mediapipe as mp

        consecutive_failures = 0

        while self._running:
            ok, bgr = self._cap.read()
            if not ok or bgr is None:
                consecutive_failures += 1
                if consecutive_failures > 30:
                    # camera disconnected — try to reopen
                    print("[Tracker] Camera lost — attempting reconnect…")
                    try:
                        self._cap.release()
                    except Exception:
                        pass
                    time.sleep(1.0)
                    self._cap = cv2.VideoCapture(self._cam_idx, cv2.CAP_DSHOW)
                    if not self._cap.isOpened():
                        self._cap = cv2.VideoCapture(self._cam_idx)
                    consecutive_failures = 0
                else:
                    time.sleep(0.01)
                continue

            consecutive_failures = 0

            # guard against zero-size or corrupt frames from DSHOW
            if bgr.size == 0 or bgr.ndim != 3 or bgr.shape[2] != 3:
                time.sleep(0.01)
                continue

            try:
                if self.mirror_input:
                    bgr = cv2.flip(bgr, 1)
                fh, fw = bgr.shape[:2]
                rgb    = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

                mp_image = mp.Image(
                    image_format=mp.ImageFormat.SRGB,
                    data=np.ascontiguousarray(rgb),
                )
                results   = self._landmarker.detect(mp_image)
            except Exception as e:
                print(f"[Tracker] Frame processing error: {e}")
                time.sleep(0.01)
                continue

            annotated = bgr.copy()
            gp: Optional[GazePoint] = None
            dist_label = "No face"
            amp        = AMP_MID
            feature: Optional[list[float]] = None   # Part 4

            if results.face_landmarks:
                lms = results.face_landmarks[0]

                # ── distance / bounding box ───────────────────────────────────
                xs = [lms[i].x * fw for i in FACE_OVAL]
                ys = [lms[i].y * fh for i in FACE_OVAL]
                x1, y1 = int(min(xs)), int(min(ys))
                x2, y2 = int(max(xs)), int(max(ys))
                face_h_ratio = (y2 - y1) / fh
                amp, dist_label = _distance_zone(face_h_ratio)

                box_col = (
                    (0, 200, 255)  if dist_label in ("Very Close", "Near")
                    else (0, 255, 0)   if dist_label == "Normal"
                    else (255, 140, 0)
                )
                cv2.rectangle(annotated, (x1, y1), (x2, y2), box_col, 1)
                cv2.putText(
                    annotated, f"{dist_label} {amp:.1f}x",
                    (x1, max(y1 - 6, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, box_col, 1,
                )

                # ── Part 1: face mesh — radius 1, only when debug on ──────────
                if SHOW_DEBUG_LANDMARKS:
                    for lm in lms:
                        cv2.circle(
                            annotated,
                            (int(lm.x * fw), int(lm.y * fh)),
                            1, (0, 110, 0), -1,   # radius 1, dark green
                        )

                if len(lms) > RIGHT_IRIS:
                    # ── Part 2: named landmark extraction ─────────────────────
                    li = lms[LEFT_IRIS];  lx, ly = li.x, li.y
                    ri = lms[RIGHT_IRIS]; rx, ry = ri.x, ri.y

                    nose  = lms[NOSE_TIP]
                    l_out = lms[LEFT_EYE_OUTER];  l_inn = lms[LEFT_EYE_INNER]
                    l_top = lms[LEFT_EYE_TOP];    l_bot = lms[LEFT_EYE_BOT]
                    r_out = lms[RIGHT_EYE_OUTER]; r_inn = lms[RIGHT_EYE_INNER]
                    r_top = lms[RIGHT_EYE_TOP];   r_bot = lms[RIGHT_EYE_BOT]

                    # Part 1: draw ONLY iris centres (radius 2) ─────────────
                    if SHOW_DEBUG_LANDMARKS:
                        for px, py in (
                            (int(lx * fw), int(ly * fh)),
                            (int(rx * fw), int(ry * fh)),
                        ):
                            cv2.circle(annotated, (px, py), 2, (0, 255, 0), -1)
                            cv2.circle(annotated, (px, py), 3, (255, 255, 255), 1)

                    # Part 2: relative iris positions ──────────────────────
                    l_nx, l_ny = _iris_in_eye(
                        lx, ly,
                        l_out.x, l_out.y,
                        l_inn.x, l_inn.y,
                        l_top.x, l_top.y,
                        l_bot.x, l_bot.y,
                    )
                    r_nx, r_ny = _iris_in_eye(
                        rx, ry,
                        r_inn.x, r_inn.y,   # right eye: inner is left in image
                        r_out.x, r_out.y,
                        r_top.x, r_top.y,
                        r_bot.x, r_bot.y,
                    )

                    # Part 2: eye aspect ratio (openness) ──────────────────
                    l_ear = _eye_openness(
                        l_top.x, l_top.y, l_bot.x, l_bot.y,
                        l_out.x, l_out.y, l_inn.x, l_inn.y,
                    )
                    r_ear = _eye_openness(
                        r_top.x, r_top.y, r_bot.x, r_bot.y,
                        r_out.x, r_out.y, r_inn.x, r_inn.y,
                    )

                    # Part 3: head-offset vector ────────────────────────────
                    leye_mid = (
                        (l_out.x + l_inn.x) / 2,
                        (l_out.y + l_inn.y) / 2,
                    )
                    reye_mid = (
                        (r_out.x + r_inn.x) / 2,
                        (r_out.y + r_inn.y) / 2,
                    )
                    head_dx, head_dy = self._head_ref.update(
                        (nose.x, nose.y), leye_mid, reye_mid,
                    )

                    # average both eyes.
                    # l_nx/r_nx are already normalised WITHIN the eye socket,
                    # so head translation is already factored out.
                    # head_dx/head_dy go into the feature vector so the
                    # regression model can handle residual rotation, but must
                    # NOT be subtracted here — doing so corrupts the value and
                    # drives eye_x/eye_y below 0, which clips to 0,0 (top-left).
                    eye_x = (l_nx + r_nx) / 2
                    eye_y = (l_ny + r_ny) / 2

                    # Part 4+5: build feature vector, run regression ────────
                    feature = _RegressionGazeModel.build_feature(
                        l_nx, l_ny, r_nx, r_ny,
                        head_dx, head_dy,
                        l_ear, r_ear,
                    )
                    mapped_x, mapped_y = self._map_gaze(eye_x, eye_y, feature)

                    # NOTE: amplification removed — it corrupted calibration
                    # by blowing mapped values outside [0,1] before clipping.
                    # The affine/regression calibration already handles
                    # the full iris-to-screen mapping; no extra amp needed.
                    cx_c = float(np.clip(mapped_x, 0.0, 1.0))
                    cy_c = float(np.clip(mapped_y, 0.0, 1.0))

                    # ── Iris-Head Fusion ───────────────────────────────────
                    #
                    # THEORY
                    # Iris alone: high precision, small range (~0.3-0.7 in eye)
                    # Head alone: full range, but 50-100ms lag from neck muscles
                    # Fusion:     head provides coarse position, iris provides
                    #             fine offset within a ±0.15 window around it.
                    #
                    # WEIGHT FORMULA
                    # iris_weight scales with iris confidence (EAR > threshold)
                    # If both eyes are wide open → trust iris more (0.75)
                    # If eyes are narrow/squinting → trust head more (0.40)
                    avg_ear    = (l_ear + r_ear) / 2.0
                    iris_w     = 0.75 if avg_ear > 0.20 else 0.40
                    head_x, head_y = self._head_nav.compute(head_dx, head_dy)
                    fused_x = iris_w * cx_c + (1.0 - iris_w) * head_x
                    fused_y = iris_w * cy_c + (1.0 - iris_w) * head_y

                    # ── One Euro Filter (replaces fixed EMA) ───────────────
                    # Velocity-adaptive: heavy smoothing at rest (fixation),
                    # light smoothing during fast movement (saccades).
                    fx, fy         = self._oef.smooth(fused_x, fused_y)
                    self._sx, self._sy = fx, fy
                    # also update head nav output for get_head_nav_pos()
                    self._head_nav_pos = (head_x, head_y)

                    gp = GazePoint(
                        x_norm=float(np.clip(self._sx, 0.0, 1.0)),
                        y_norm=float(np.clip(self._sy, 0.0, 1.0)),  # type: ignore[arg-type]
                        confidence=1.0,
                        timestamp=time.time(),
                    )

                else:
                    dist_label = "No iris"

            else:
                cv2.putText(
                    annotated, "No face detected", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2,
                )
                # Part 6 — confidence gate: emit last valid point at conf=0
                # so the UI can choose to freeze the cursor rather than jump.
                if self._sx is not None:
                    gp = GazePoint(
                        x_norm=float(np.clip(self._sx, 0.0, 1.0)),
                        y_norm=float(np.clip(self._sy, 0.0, 1.0)),  # type: ignore[arg-type]
                        confidence=0.0,
                        timestamp=time.time(),
                    )
                else:
                    self._sx = self._sy = None
                    self._head_nav_pos = None

            with self._lock:
                self._gaze_point     = gp
                self._latest_frame   = annotated
                self._distance_label = dist_label
                self._amplification  = amp
                if feature is not None:
                    self._last_feature = feature
                # store nose for head navigation — None when no face
                if results.face_landmarks:
                    _n = results.face_landmarks[0][NOSE_TIP]
                    self._nose_x = _n.x
                    self._nose_y = _n.y
                else:
                    self._nose_x = None
                    self._nose_y = None