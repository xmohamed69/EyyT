"""
Calibration overlay — single window.
Flow:
  1. Camera feed fills window
  2. Click Start → 3-second countdown on camera
  3. Dots appear one by one on top of camera feed
  4. Click Next per dot → iris sampled → next dot
"""
from __future__ import annotations

import math
from typing import Optional

import cv2
import numpy as np

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from app.services.stores import load, save, reset, exists

MARGIN    = 0.10
_C, _F    = MARGIN, 1 - MARGIN
SAMPLE_MS = 800
TICK_MS   = 30
COUNTDOWN = 3   # seconds

POINTS_5 = [(_C,_C),(_F,_C),(_C,_F),(_F,_F),(0.5,0.5)]
POINTS_9 = [
    (_C,_C),(0.5,_C),(_F,_C),
    (_C,0.5),(0.5,0.5),(_F,0.5),
    (_C,_F),(0.5,_F),(_F,_F),
]
POINTS_13 = [
    (_C,_C),(0.5,_C),(_F,_C),
    (_C,0.35),(_F,0.35),
    (_C,0.5),(0.5,0.5),(_F,0.5),
    (_C,0.65),(_F,0.65),
    (_C,_F),(0.5,_F),(_F,_F),
]
STEPS = [
    (POINTS_5,  0.08, "Step 1/3 — Quick (5 pts)"),
    (POINTS_9,  0.06, "Step 2/3 — Normal (9 pts)"),
    (POINTS_13, None, "Step 3/3 — Full (13 pts)"),
]

# states
_ST_IDLE      = "idle"       # waiting for Start click
_ST_COUNTDOWN = "countdown"  # 3-2-1 on screen
_ST_DOTS      = "dots"       # dots visible, user clicks Next
_ST_SAMPLING  = "sampling"   # collecting iris data
_ST_DONE      = "done"       # finished


class CalibrationOverlay(QWidget):

    finished = Signal(bool, int, float, list, list)

    def __init__(self, gaze_provider, step_index=0, best_error=None, parent=None):
        super().__init__(parent)

        # state vars
        self._gaze       = gaze_provider
        self._step_idx   = step_index
        self._best_err   = best_error
        self._state      = _ST_IDLE
        self._countdown  = COUNTDOWN * 1000   # ms remaining
        self._pt_idx     = 0
        self._screen_pts: list[tuple[float,float]] = []
        self._gaze_pts:   list[tuple[float,float]] = []
        self._samples:    list[tuple[float,float]] = []
        self._sample_ms  = 0.0
        self._t          = 0.0
        self._cam_pixmap: Optional[QPixmap] = None

        points, threshold, label = STEPS[step_index]
        self._points    = list(points)
        self._threshold = threshold
        self._label     = label

        # window
        self.setWindowTitle("Calibration")
        self.setWindowFlags(Qt.WindowType.Window)

        # bottom bar — must be built BEFORE showMaximized() so that the
        # resizeEvent → _place_bar() finds self._bar on the first resize.
        self._build_bar()
        self._place_bar()   # force correct position before first paint

        self.showMaximized()

        # main timer — drives camera + countdown + animations
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        # sample timer
        self._stimer = QTimer(self)
        self._stimer.setInterval(TICK_MS)
        self._stimer.timeout.connect(self._sample_tick)

    # ── bar ───────────────────────────────────────────────────────────────────
    def _build_bar(self):
        self._bar = QWidget(self)
        self._bar.setFixedHeight(72)
        self._bar.setStyleSheet("background:rgba(0,0,0,210);")

        h = QHBoxLayout(self._bar)
        h.setContentsMargins(24, 0, 24, 0)
        h.setSpacing(14)

        self._lbl = QLabel("Click  ▶ Start  when your face is visible")
        self._lbl.setFont(QFont("Segoe UI", 13))
        self._lbl.setStyleSheet("color:#ddd;background:transparent;")
        h.addWidget(self._lbl, stretch=1)

        S = ("QPushButton{padding:9px 22px;border-radius:5px;"
             "font-size:13px;color:white;border:none;}"
             "QPushButton:disabled{background:#333;color:#555;}")

        self._btn_start = QPushButton("▶  Start")
        self._btn_start.setStyleSheet(
            S+"QPushButton{background:#1a6a3a;}"
             "QPushButton:hover{background:#2a9a4a;}")
        self._btn_start.clicked.connect(self._on_start)
        h.addWidget(self._btn_start)

        self._btn_next = QPushButton("Next  ▶")
        self._btn_next.setStyleSheet(
            S+"QPushButton{background:#1a3a6a;}"
             "QPushButton:hover{background:#2a5a9a;}")
        self._btn_next.clicked.connect(self._on_next)
        self._btn_next.setVisible(False)
        h.addWidget(self._btn_next)

        self._btn_cancel = QPushButton("✕  Cancel")
        self._btn_cancel.setStyleSheet(
            S+"QPushButton{background:#6a1a1a;}"
             "QPushButton:hover{background:#9a2a2a;}")
        self._btn_cancel.clicked.connect(self._on_cancel)
        h.addWidget(self._btn_cancel)

        self._place_bar()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._place_bar()

    def _place_bar(self):
        if hasattr(self, "_bar"):
            self._bar.setGeometry(0, self.height()-72, self.width(), 72)
            self._bar.raise_()

    # ── main tick ─────────────────────────────────────────────────────────────
    def _tick(self):
        self._t += TICK_MS / 1000.0

        # update camera frame
        if hasattr(self._gaze, "get_latest_frame"):
            frame = self._gaze.get_latest_frame()
            if frame is not None:
                rgb = np.ascontiguousarray(
                    cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                h, w, _ = rgb.shape
                self._cam_pixmap = QPixmap.fromImage(
                    QImage(rgb.data, w, h, w*3,
                           QImage.Format.Format_RGB888).copy()
                )

        # countdown tick
        if self._state == _ST_COUNTDOWN:
            self._countdown -= TICK_MS
            if self._countdown <= 0:
                print('[CAL] Countdown done — showing dot 1')
                self._state = _ST_DOTS
                self._btn_next.setVisible(True)
                self._set_label(
                    f"Look at dot 1/{len(self._points)}"
                    " — click  Next ▶  when steady")

        self.update()

    # ── paint ─────────────────────────────────────────────────────────────────
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        # ── layer 1: camera feed (clipped above bar) ────────────────────────
        BAR_H = 72
        cam_h = H - BAR_H
        # dark bar background
        p.fillRect(0, cam_h, W, BAR_H, QColor(0, 0, 0, 220))
        if self._cam_pixmap:
            scaled = self._cam_pixmap.scaled(
                W, cam_h,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            ox = (W      - scaled.width())  // 2
            oy = (cam_h  - scaled.height()) // 2
            # clip so camera never draws over the bar
            p.setClipRect(0, 0, W, cam_h)
            p.drawPixmap(ox, oy, scaled)
            p.setClipping(False)
        else:
            p.fillRect(0, 0, W, cam_h, QColor(10,10,10))
            p.setPen(QColor(120,120,120))
            p.setFont(QFont("Segoe UI", 16))
            p.drawText(QRectF(0, 0, W, cam_h),
                       Qt.AlignmentFlag.AlignCenter,
                       "Waiting for camera…")

        # ── layer 2: countdown ────────────────────────────────────────────────
        if self._state == _ST_COUNTDOWN:
            p.fillRect(0, 0, W, cam_h, QColor(0,0,0,120))
            secs = math.ceil(self._countdown / 1000)
            p.setFont(QFont("Segoe UI", 140, QFont.Weight.Bold))
            p.setPen(QColor(255,255,255,220))
            p.drawText(QRectF(0, cam_h*0.1, W, cam_h*0.6),
                       Qt.AlignmentFlag.AlignCenter, str(secs))
            p.setFont(QFont("Segoe UI", 18))
            p.setPen(QColor(200,200,200,200))
            p.drawText(QRectF(0, cam_h*0.65, W, cam_h*0.15),
                       Qt.AlignmentFlag.AlignCenter,
                       "Get ready — look at each dot and click Next")

        # ── layer 3: dots on camera ───────────────────────────────────────────
        elif self._state in (_ST_DOTS, _ST_SAMPLING):
            self._paint_dots(p, W, cam_h)

        p.end()

    def _paint_dots(self, p: QPainter, W: int, H: int):
        total = len(self._points)
        if self._pt_idx >= total:
            return

        nx, ny = self._points[self._pt_idx]
        cx = int(nx * W)
        cy = int(ny * H)

        # subtle dark overlay
        p.fillRect(0, 0, W, H, QColor(0,0,0,60))

        # completed dots
        for i, (px_n, py_n) in enumerate(self._points):
            px_c, py_c = int(px_n*W), int(py_n*H)
            if i < self._pt_idx:
                p.setBrush(QColor(40,210,80,230))
                p.setPen(QPen(QColor(255,255,255,180), 2))
                p.drawEllipse(QPointF(px_c,py_c), 10, 10)
                p.setBrush(QColor(255,255,255))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QPointF(px_c,py_c), 3, 3)
            elif i > self._pt_idx:
                p.setBrush(QColor(255,255,255,50))
                p.setPen(QPen(QColor(255,255,255,90), 1))
                p.drawEllipse(QPointF(px_c,py_c), 8, 8)

        # spotlight
        SPOT = 85
        outer = QPainterPath()
        outer.addRect(QRectF(0,0,W,H))
        inner = QPainterPath()
        inner.addEllipse(QPointF(cx,cy), SPOT, SPOT)
        p.fillPath(outer-inner, QColor(0,0,0,80))

        # sampling ring
        RING = 52
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(60,60,60,180), 5))
        p.drawEllipse(QPointF(cx,cy), RING, RING)
        if self._state == _ST_SAMPLING and self._sample_ms > 0:
            prog = min(self._sample_ms / SAMPLE_MS, 1.0)
            pen2 = QPen(QColor(50,220,255), 5)
            pen2.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen2)
            p.drawArc(QRectF(cx-RING,cy-RING,RING*2,RING*2),
                      90*16, int(-prog*360*16))

        # glowing red dot
        pulse = 15 + int(4*math.sin(self._t*6))
        for r, a in ((pulse+20,25),(pulse+10,55),(pulse,180)):
            p.setBrush(QColor(220,40,40,a))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(cx,cy), r, r)
        p.setBrush(QColor(255,255,255))
        p.drawEllipse(QPointF(cx,cy), 5, 5)

        # crosshair
        p.setPen(QPen(QColor(255,255,255,130), 1))
        p.drawLine(cx-(pulse+14), cy, cx+(pulse+14), cy)
        p.drawLine(cx, cy-(pulse+14), cx, cy+(pulse+14))

        # counter label
        p.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        p.setPen(QColor(255,255,255,220))
        p.drawText(QRectF(cx-60, cy+RING+10, 120, 26),
                   Qt.AlignmentFlag.AlignCenter,
                   f"{self._pt_idx+1} / {total}")

    # ── button handlers ───────────────────────────────────────────────────────
    def _on_start(self):
        print('[CAL] Start clicked — entering countdown')
        self._state = _ST_COUNTDOWN
        self._countdown = COUNTDOWN * 1000
        self._btn_start.setVisible(False)
        self._set_label("Get ready…")

    def _on_next(self):
        print(f'[CAL] Next clicked — state={self._state}')
        if self._state != _ST_DOTS:
            return
        self._state     = _ST_SAMPLING
        self._sample_ms = 0.0
        self._samples   = []
        self._btn_next.setEnabled(False)
        self._set_label("Sampling… keep looking at the dot")
        self._stimer.start()

    def _on_cancel(self):
        print(f'[CAL] CANCEL called — state={self._state} pts={len(self._screen_pts)}')
        self._timer.stop()
        self._stimer.stop()
        self.close()
        self.finished.emit(False, self._step_idx, 1.0, [], [])

    def _set_label(self, txt):
        if hasattr(self, "_lbl"):
            self._lbl.setText(txt)

    # ── sample tick ───────────────────────────────────────────────────────────
    def _sample_tick(self):
        self._sample_ms += TICK_MS
        gp = self._gaze.get_gaze()
        if gp is not None and gp.confidence > 0.4:
            self._samples.append((gp.x_norm, gp.y_norm))
        self.update()
        if self._sample_ms >= SAMPLE_MS:
            self._stimer.stop()
            self._commit_point()

    def _commit_point(self):
        print(f'[CAL] Point {self._pt_idx+1} committed — samples={len(self._samples)}')
        sx, sy = self._points[self._pt_idx]
        if self._samples:
            xs  = sorted(s[0] for s in self._samples)
            ys  = sorted(s[1] for s in self._samples)
            mid = len(xs)//2
            gx, gy = xs[mid], ys[mid]
        else:
            gx, gy = sx, sy
        self._screen_pts.append((sx, sy))
        self._gaze_pts.append((gx, gy))
        self._pt_idx += 1
        self._samples = []
        total = len(self._points)
        if self._pt_idx >= total:
            self._state = _ST_DONE
            self._finish()
        else:
            self._state = _ST_DOTS
            self._btn_next.setEnabled(True)
            self._set_label(
                f"Look at dot {self._pt_idx+1}/{total}"
                " — click  Next ▶  when steady")

    # ── finish ────────────────────────────────────────────────────────────────
    def _finish(self):
        self._timer.stop()
        self._stimer.stop()
        errors = [
            math.sqrt((gx-sx)**2+(gy-sy)**2)
            for (sx,sy),(gx,gy) in zip(self._screen_pts, self._gaze_pts)
        ]
        mean_err = sum(errors)/len(errors) if errors else 1.0
        if self._best_err is None or mean_err < self._best_err:
            self._best_err = mean_err
        next_step = self._step_idx + 1
        accepted  = (
            (self._threshold is not None and mean_err <= self._threshold)
            or next_step >= len(STEPS)
        )
        next_idx = (next_step if next_step < len(STEPS) else None) \
                   if not accepted else None
        r = _ResultOverlay(mean_err, accepted, next_idx, self)
        r.accept_clicked.connect(lambda: self._emit(True, mean_err))
        r.retry_clicked.connect(self._retry)
        r.next_clicked.connect(self._next_step)
        r.setGeometry(self.rect())
        r.show()

    def _emit(self, accepted, error):
        self.close()
        self.finished.emit(accepted, self._step_idx, error,
                           list(self._screen_pts), list(self._gaze_pts))

    def _retry(self):
        self.close()
        self._ov = CalibrationOverlay(
            self._gaze, self._step_idx, self._best_err, parent=None)
        self._ov.finished.connect(self.finished)
        self._ov.show()

    def _next_step(self):
        self.close()
        self._ov = CalibrationOverlay(
            self._gaze, self._step_idx+1, self._best_err, parent=None)
        self._ov.finished.connect(self.finished)
        self._ov.show()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self._on_cancel()


# ══════════════════════════════════════════════════════════════════════════════
class _ResultOverlay(QWidget):
    accept_clicked = Signal()
    retry_clicked  = Signal()
    next_clicked   = Signal()

    def __init__(self, error, accepted, next_step, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:rgba(5,5,5,215);")
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(18)

        icon  = "✅" if accepted else "❌"
        color = "#60e060" if accepted else "#e06060"
        msg   = (f"Calibration complete!\nMean error: {error:.3f}"
                 if accepted else
                 f"Low accuracy  (error: {error:.3f})")

        for text, size, col in [(icon,52,"#fff"),(msg,15,color)]:
            l = QLabel(text)
            l.setFont(QFont("Segoe UI", size))
            l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            l.setStyleSheet(
                f"color:{col};background:transparent;border:none;")
            lay.addWidget(l)

        S = ("QPushButton{padding:11px 30px;border-radius:6px;"
             "font-size:13px;color:white;border:none;}"
             "QPushButton:hover{opacity:0.85;}")

        if accepted:
            b = QPushButton("✓  Done")
            b.setStyleSheet(S+"QPushButton{background:#2d6a2e;}"
                            "QPushButton:hover{background:#3a8a3b;}")
            b.clicked.connect(self.accept_clicked)
            lay.addWidget(b, alignment=Qt.AlignmentFlag.AlignCenter)
        else:
            b1 = QPushButton("🔄  Retry")
            b1.setStyleSheet(S+"QPushButton{background:#5a3a10;}"
                             "QPushButton:hover{background:#7a5010;}")
            b1.clicked.connect(self.retry_clicked)
            lay.addWidget(b1, alignment=Qt.AlignmentFlag.AlignCenter)
            if next_step is not None:
                b2 = QPushButton(
                    f"➡  More points ({len(STEPS[next_step][0])} pts)")
                b2.setStyleSheet(S+"QPushButton{background:#1a3a6a;}"
                                 "QPushButton:hover{background:#2a5a9a;}")
                b2.clicked.connect(self.next_clicked)
                lay.addWidget(b2, alignment=Qt.AlignmentFlag.AlignCenter)

        b3 = QPushButton("✓  Accept anyway")
        b3.setStyleSheet(S+"QPushButton{background:#333;}"
                         "QPushButton:hover{background:#444;}")
        b3.clicked.connect(self.accept_clicked)
        lay.addWidget(b3, alignment=Qt.AlignmentFlag.AlignCenter)


# ══════════════════════════════════════════════════════════════════════════════
class CalibrationPanel(QWidget):

    calibration_done = Signal(bool)

    def __init__(self, gaze_provider=None, parent=None):
        super().__init__(parent)
        self._gaze    = gaze_provider
        self._overlay = None

        self.setMinimumSize(320, 160)
        self.setStyleSheet(
            "background-color:#1a1a1a;border:1px solid #444;border-radius:6px;"
        )
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(10)

        self._status_lbl = QLabel()
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_lbl.setFont(QFont("Segoe UI", 11))
        self._status_lbl.setStyleSheet("border:none;")
        lay.addWidget(self._status_lbl)

        self._info_lbl = QLabel(
            "Camera shown full screen.\n"
            "Dots appear as overlay — click Next per dot.")
        self._info_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._info_lbl.setFont(QFont("Segoe UI", 9))
        self._info_lbl.setStyleSheet("color:#555;border:none;")
        lay.addWidget(self._info_lbl)

        row = QHBoxLayout()
        row.setSpacing(8)

        self._btn_calib = QPushButton("▶  Calibrate")
        self._btn_calib.setStyleSheet(self._bs("#1a3a6a","#2a5a9a"))
        self._btn_calib.clicked.connect(self._start)
        row.addWidget(self._btn_calib)

        self._btn_reset = QPushButton("🗑  Reset")
        self._btn_reset.setStyleSheet(self._bs("#6a1a1a","#9a2a2a"))
        self._btn_reset.clicked.connect(self._reset)
        row.addWidget(self._btn_reset)

        lay.addLayout(row)
        self._refresh_status()

    @staticmethod
    def _bs(bg, hov):
        return (f"QPushButton{{padding:7px 14px;border-radius:4px;"
                f"background:{bg};color:white;border:none;font-size:11px;}}"
                f"QPushButton:hover{{background:{hov};}}"
                f"QPushButton:disabled{{background:#333;color:#555;}}")

    def set_gaze_provider(self, gaze):
        self._gaze = gaze
        self._btn_calib.setEnabled(True)

    def _refresh_status(self):
        data = load()
        if data:
            self._status_lbl.setText(
                f"✅  Calibrated\n"
                f"Step {data['step']}  •  error {data['mean_error']:.3f}\n"
                f"{data['calibrated_at']}"
            )
            self._status_lbl.setStyleSheet("color:#60e060;border:none;")
            self._btn_reset.setEnabled(True)
        else:
            self._status_lbl.setText("⚠️  Not calibrated")
            self._status_lbl.setStyleSheet("color:#e0c060;border:none;")
            self._btn_reset.setEnabled(False)
        self._btn_calib.setEnabled(self._gaze is not None)

    def _start(self):
        if self._gaze is None:
            return
        self._btn_calib.setEnabled(False)
        self._status_lbl.setText("⏳  Calibrating…")
        self._status_lbl.setStyleSheet("color:#aaa;border:none;")
        self._overlay = CalibrationOverlay(
            gaze_provider=self._gaze, parent=None)
        self._overlay.finished.connect(self._on_finished)
        self._overlay.show()
        self._overlay.raise_()
        self._overlay.activateWindow()

    def _on_finished(self, accepted, step_idx, mean_err, screen_pts, gaze_pts):
        if screen_pts and gaze_pts:
            save(step=step_idx+1, mean_error=mean_err,
                 screen_points=screen_pts, gaze_points=gaze_pts)
            if hasattr(self._gaze, "load_calibration"):
                self._gaze.load_calibration(screen_pts, gaze_pts)
        self._refresh_status()
        self.calibration_done.emit(accepted)

    def _reset(self):
        reset()
        self._refresh_status()
        self.calibration_done.emit(False)