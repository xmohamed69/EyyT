"""
calibrator_app.py  —  app/ui/calibrator_app.py

First-run scan logic:
  - Checks data/.scan_done flag
  - If missing: opens a real OS terminal window running scan_helper.py
  - scan_helper.py writes TRUE/FALSE to data/.scan_result then exits
  - CalibratorWindow reads the result, shows it in the terminal log
  - User must click START CALIBRATION button — no auto-timer
  - On close, flag file data/.scan_done is written so scan never runs again
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QPushButton, QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from app.ui.calibration_panel import CalibrationPanel
from app.ui.simple_widgets import WebcamPlaceholder
from app.services.stores import load_calib, calib_exists

# ── paths ─────────────────────────────────────────────────────────────────────
_DATA_DIR    = Path("data")
_SCAN_FLAG   = _DATA_DIR / ".scan_done"
_SCAN_RESULT = _DATA_DIR / ".scan_result"

# ── palette ───────────────────────────────────────────────────────────────────
_INK    = "#07090f"
_SURF   = "#0b0d16"
_CARD   = "#0e1020"
_BORDER = "#16203a"
_TEAL   = "#00c8a0"
_MUTED  = "#2a3858"
_TEXT   = "#6070a0"
_BRIGHT = "#c0cce0"
_WARN   = "#c08820"


# ══════════════════════════════════════════════════════════════════════════════
#  scan_helper script — written to a temp file and run in a real terminal
# ══════════════════════════════════════════════════════════════════════════════

_SCAN_SCRIPT = '''\
import sys, platform, subprocess, json
from pathlib import Path

result_path = Path(sys.argv[1])
data_dir    = result_path.parent
data_dir.mkdir(parents=True, exist_ok=True)

print("=" * 50)
print("  EyeTyper — System Scan")
print("=" * 50)
print()

# ── OS ────────────────────────────────────────────────────────────────────────
print(f"[1/5] OS          : {platform.system()} {platform.release()}")

# ── CPU ───────────────────────────────────────────────────────────────────────
cpu = "unknown"
try:
    if platform.system() == "Windows":
        lines = subprocess.check_output("wmic cpu get Name", shell=True,
            stderr=subprocess.DEVNULL).decode().strip().splitlines()
        cpu = lines[-1].strip() if len(lines) > 1 else "unknown"
    elif platform.system() == "Darwin":
        cpu = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"],
            stderr=subprocess.DEVNULL).decode().strip()
    else:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if "model name" in line:
                    cpu = line.split(":")[-1].strip()
                    break
except Exception:
    pass
print(f"[2/5] CPU         : {cpu}")

# ── RAM ───────────────────────────────────────────────────────────────────────
ram = "unknown"
try:
    if platform.system() == "Windows":
        lines = subprocess.check_output(
            "wmic computersystem get TotalPhysicalMemory",
            shell=True, stderr=subprocess.DEVNULL).decode().strip().splitlines()
        ram = f"{int(lines[-1].strip()) // (1024**3)} GB"
except Exception:
    pass
print(f"[3/5] RAM         : {ram}")

# ── Screen ────────────────────────────────────────────────────────────────────
screen_info = "unknown"
try:
    if platform.system() == "Windows":
        import ctypes
        user32 = ctypes.windll.user32
        w = user32.GetSystemMetrics(0)
        h = user32.GetSystemMetrics(1)
        screen_info = f"{w} x {h}"
except Exception:
    pass
print(f"[4/5] Screen      : {screen_info}")

# ── Firebase match ────────────────────────────────────────────────────────────
print("[5/5] Checking Firebase community database...")
matched = False
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from app.services.firebase_client import get_client
    fb = get_client()
    if getattr(fb, "online", False):
        matches = fb.find_matching_calibrations()
        if matches:
            matched = True
            print(f"      Found {len(matches)} matching calibration(s)")
            print(f"      Best error: {matches[0].get('mean_error', '?'):.4f}")
        else:
            print("      No community match for this screen profile")
    else:
        print("      Firebase offline")
except Exception as e:
    print(f"      Firebase error: {e}")

print()
print("=" * 50)
result = "TRUE" if matched else "FALSE"
print(f"  RESULT: {result}")
print("=" * 50)
print()
print("You can close this window.")

result_path.write_text(result, encoding="utf-8")
'''


def _write_scan_helper() -> Path:
    """Write the scan script to a temp .py file and return its path."""
    fd, path = tempfile.mkstemp(suffix=".py", prefix="eyetyper_scan_")
    os.close(fd)
    Path(path).write_text(_SCAN_SCRIPT, encoding="utf-8")
    return Path(path)


def _open_terminal_scan() -> None:
    """
    Open a real OS terminal window that runs the scan script.
    The terminal closes on its own when the script finishes
    (or user closes it). Result is written to _SCAN_RESULT.
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    script = _write_scan_helper()
    result_arg = str(_SCAN_RESULT.resolve())
    cmd = [sys.executable, str(script), result_arg]

    system = platform.system()
    try:
        if system == "Windows":
            # cmd /k keeps window open so user can read output
            subprocess.Popen(
                ["cmd", "/c", "start", "cmd", "/k",
                 sys.executable, str(script), result_arg],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        elif system == "Darwin":
            # write a tiny shell wrapper
            sh = Path(tempfile.mktemp(suffix=".sh"))
            sh.write_text(
                f'#!/bin/bash\n'
                f'"{sys.executable}" "{script}" "{result_arg}"\n'
                f'echo "\nPress Enter to close..."; read\n',
                encoding="utf-8"
            )
            sh.chmod(0o755)
            subprocess.Popen(["open", "-a", "Terminal", str(sh)])
        else:
            # Linux: try common terminals
            for term in ["gnome-terminal", "xterm", "konsole", "xfce4-terminal"]:
                try:
                    if term == "gnome-terminal":
                        subprocess.Popen([term, "--", "bash", "-c",
                            f'"{sys.executable}" "{script}" "{result_arg}"; '
                            f'echo; echo "Press Enter to close..."; read'])
                    else:
                        subprocess.Popen([term, "-e",
                            f'bash -c "{sys.executable} {script} {result_arg}; '
                            f'echo; read -p \'Press Enter to close...\'"'])
                    break
                except FileNotFoundError:
                    continue
    except Exception as e:
        print(f"[scan] Could not open terminal: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  Terminal log widget
# ══════════════════════════════════════════════════════════════════════════════

class _Terminal(QTextEdit):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setStyleSheet(f"""
            QTextEdit {{
                background: #050709;
                color: {_TEAL};
                border: none;
                font-family: Consolas;
                font-size: 11px;
                padding: 10px;
            }}
            QScrollBar:vertical {{
                background: {_SURF}; width: 4px; border-radius: 2px;
            }}
            QScrollBar::handle:vertical {{
                background: {_BORDER}; border-radius: 2px;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

    def log(self, msg: str, level: str = "INFO") -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        colors = {
            "INFO":  _TEAL,
            "OK":    "#00e090",
            "WARN":  _WARN,
            "ERROR": "#c04040",
            "SYS":   "#304080",
            "DATA":  "#506090",
        }
        col = colors.get(level, _TEAL)
        pad = f"[{level:<5}]"
        self.append(
            f'<span style="color:#1a2840">{ts}</span> '
            f'<span style="color:{col};font-weight:600">{pad}</span> '
            f'<span style="color:{col}">{msg}</span>'
        )
        self.ensureCursorVisible()


# ══════════════════════════════════════════════════════════════════════════════
#  Main calibrator window
# ══════════════════════════════════════════════════════════════════════════════

class CalibratorWindow(QMainWindow):

    def __init__(self, tracker, parent=None):
        super().__init__(parent)
        self._tracker       = tracker
        self._scan_matched  = False   # TRUE result from scan
        self._scan_done     = False   # result file was read

        # try Firebase — silent fail
        self._firebase = None
        try:
            from app.services.firebase_client import get_client
            self._firebase = get_client()
        except Exception:
            pass

        self.setWindowTitle("EyeTyper — Calibrator")
        self.setMinimumSize(980, 600)
        self.resize(1100, 660)
        self.setStyleSheet(f"background: {_INK};")

        self._build_ui()
        self._tracker.start()
        self._log_startup()

        # ── first-run scan decision ───────────────────────────────────────────
        if _SCAN_FLAG.exists():
            # not first run — go straight to ready state
            self._log("Scan already done on first run — skipping", "INFO")
            self._set_ready(matched=False, skip_scan=True)
        else:
            # first run — launch terminal, poll for result
            self._log("First run detected — launching system scan...", "SYS")
            self._log(
                "A terminal window will open and run the scan in the background.",
                "INFO"
            )
            _open_terminal_scan()
            self._poll_timer = QTimer(self)
            self._poll_timer.setInterval(800)
            self._poll_timer.timeout.connect(self._poll_scan_result)
            self._poll_timer.start()

    # ── build ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        vbox = QVBoxLayout(root)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        vbox.addWidget(self._build_header())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet(
            f"QSplitter::handle {{ background: {_BORDER}; }}"
        )

        # ── left: camera + controls ───────────────────────────────────────────
        left = QWidget()
        left.setStyleSheet(f"background: {_SURF};")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(14, 14, 14, 14)
        ll.setSpacing(10)

        self._webcam = WebcamPlaceholder(tracker=self._tracker)
        self._webcam.setStyleSheet(
            f"background: {_INK}; border: 1px solid {_BORDER}; border-radius: 6px;"
        )
        self._webcam.setMinimumHeight(200)
        ll.addWidget(self._webcam, 1)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {_BORDER};")
        ll.addWidget(sep)

        # ── scan result box (hidden until scan done) ──────────────────────────
        self._scan_box = QWidget()
        self._scan_box.setStyleSheet(
            f"background: {_CARD}; border: 1px solid {_BORDER}; border-radius: 6px;"
        )
        self._scan_box.hide()
        sb_lay = QVBoxLayout(self._scan_box)
        sb_lay.setContentsMargins(14, 10, 14, 10)
        sb_lay.setSpacing(6)

        sb_hdr = QLabel("SCAN RESULT")
        sb_hdr.setStyleSheet(
            f"color: {_MUTED}; font-size: 9px; letter-spacing: 3px; "
            f"font-family: Consolas; background: transparent; border: none;"
        )
        sb_lay.addWidget(sb_hdr)

        self._scan_result_lbl = QLabel("")
        self._scan_result_lbl.setStyleSheet(
            f"color: {_BRIGHT}; font-size: 11px; font-family: Consolas; "
            f"background: transparent; border: none;"
        )
        self._scan_result_lbl.setWordWrap(True)
        sb_lay.addWidget(self._scan_result_lbl)

        # use match button — visible only when TRUE
        self._btn_use_match = QPushButton("USE MATCHED CALIBRATION")
        self._btn_use_match.hide()
        self._btn_use_match.setStyleSheet(self._btn_style(_TEAL))
        self._btn_use_match.clicked.connect(self._on_use_match)
        sb_lay.addWidget(self._btn_use_match)

        ll.addWidget(self._scan_box)

        # ── calibration panel ─────────────────────────────────────────────────
        self._calib_panel = CalibrationPanel(gaze_provider=self._tracker)
        self._calib_panel.calibration_done.connect(self._on_calib_done)
        self._calib_panel.setStyleSheet(
            f"background: {_INK}; border: 1px solid {_BORDER}; border-radius: 6px;"
        )
        ll.addWidget(self._calib_panel)

        # ── START button ──────────────────────────────────────────────────────
        self._btn_start = QPushButton("START CALIBRATION")
        self._btn_start.setEnabled(False)   # enabled after scan (or on non-first run)
        self._btn_start.setStyleSheet(self._btn_style(_TEAL, large=True))
        self._btn_start.clicked.connect(self._on_start_calibration)
        ll.addWidget(self._btn_start)

        splitter.addWidget(left)

        # ── right: terminal ───────────────────────────────────────────────────
        right = QWidget()
        right.setStyleSheet(f"background: {_INK};")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        th = QWidget()
        th.setFixedHeight(36)
        th.setStyleSheet(
            f"background: {_SURF}; border-bottom: 1px solid {_BORDER};"
        )
        th_lay = QHBoxLayout(th)
        th_lay.setContentsMargins(14, 0, 14, 0)

        tl = QLabel("TERMINAL")
        tl.setStyleSheet(
            f"color: {_MUTED}; font-size: 9px; letter-spacing: 3px; "
            f"font-family: Consolas; background: transparent;"
        )
        th_lay.addWidget(tl)
        th_lay.addStretch()

        btn_clr = QPushButton("CLEAR")
        btn_clr.setFixedSize(50, 22)
        btn_clr.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {_MUTED};
                border: 1px solid {_BORDER}; border-radius: 3px;
                font-size: 8px; letter-spacing: 2px; font-family: Consolas;
            }}
            QPushButton:hover {{ color: {_TEAL}; border-color: {_TEAL}55; }}
        """)
        btn_clr.clicked.connect(lambda: self._term.clear())
        th_lay.addWidget(btn_clr)
        rl.addWidget(th)

        self._term = _Terminal()
        rl.addWidget(self._term)

        splitter.addWidget(right)
        splitter.setSizes([420, 620])
        vbox.addWidget(splitter, 1)

    def _btn_style(self, col: str, large: bool = False) -> str:
        pad  = "10px 20px" if large else "6px 14px"
        size = "11px" if large else "9px"
        return f"""
            QPushButton {{
                background: {col}15;
                color: {col};
                border: 1px solid {col}50;
                border-radius: 5px;
                padding: {pad};
                font-size: {size};
                letter-spacing: 2px;
                font-family: Consolas;
            }}
            QPushButton:hover {{
                background: {col}28;
                border-color: {col};
            }}
            QPushButton:disabled {{
                color: {_MUTED};
                border-color: {_BORDER};
                background: transparent;
            }}
        """

    def _build_header(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(46)
        bar.setStyleSheet(
            f"background: {_SURF}; border-bottom: 1px solid {_BORDER};"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 0, 18, 0)

        logo = QLabel("EyeTyper  /  Calibrator")
        logo.setStyleSheet(
            f"color: {_TEAL}; font-size: 13px; font-family: Consolas; "
            f"letter-spacing: 1px; background: transparent;"
        )
        lay.addWidget(logo)
        lay.addStretch()

        self._badge = QLabel()
        self._refresh_badge()
        lay.addWidget(self._badge)
        return bar

    def _refresh_badge(self):
        data = load_calib()
        if data:
            txt = (f"CALIBRATED  |  step {data['step']}  "
                   f"error {data['mean_error']:.4f}")
            col, bg, brd = _TEAL, f"{_TEAL}12", f"{_TEAL}40"
        else:
            txt = "NOT CALIBRATED"
            col, bg, brd = _WARN, f"{_WARN}12", f"{_WARN}40"
        self._badge.setText(txt)
        self._badge.setStyleSheet(f"""
            color: {col}; background: {bg};
            border: 1px solid {brd}; border-radius: 3px;
            padding: 4px 12px; font-size: 9px;
            letter-spacing: 2px; font-family: Consolas;
        """)

    # ── first-run scan polling ────────────────────────────────────────────────

    @Slot()
    def _poll_scan_result(self):
        """Check every 800ms if the scan terminal has written its result."""
        if not _SCAN_RESULT.exists():
            self._log("Waiting for scan to complete...", "SYS")
            return

        # result file found — read it
        try:
            raw = _SCAN_RESULT.read_text(encoding="utf-8").strip().upper()
        except Exception:
            return

        self._poll_timer.stop()
        matched = raw == "TRUE"
        self._scan_matched = matched
        self._scan_done    = True

        # clean up result file
        try:
            _SCAN_RESULT.unlink()
        except Exception:
            pass

        self._log(f"Scan result: {raw}", "OK" if matched else "INFO")
        self._set_ready(matched=matched, skip_scan=False)

    def _set_ready(self, matched: bool, skip_scan: bool):
        """Called after scan completes (or on non-first run)."""
        if not skip_scan:
            self._scan_box.show()
            if matched:
                self._scan_result_lbl.setText(
                    "Community match found for your screen profile.\n"
                    "You can use it or run your own calibration."
                )
                self._scan_result_lbl.setStyleSheet(
                    f"color: {_TEAL}; font-size: 11px; font-family: Consolas; "
                    f"background: transparent; border: none;"
                )
                self._btn_use_match.show()
                self._log("Firebase matched calibration available", "OK")
            else:
                self._scan_result_lbl.setText(
                    "No community match found.\n"
                    "Click START CALIBRATION to calibrate manually."
                )
                self._log("No Firebase match — manual calibration required", "WARN")

        self._btn_start.setEnabled(True)
        self._log("Ready — press START CALIBRATION to begin", "INFO")

    # ── log helpers ───────────────────────────────────────────────────────────

    def _log(self, msg: str, level: str = "INFO"):
        self._term.log(msg, level)

    def _log_startup(self):
        self._log("Calibrator started", "SYS")
        self._log(
            f"Python {sys.version.split()[0]}  "
            f"Platform: {platform.system()} {platform.release()}", "SYS"
        )
        self._log("Camera + MediaPipe tracker started", "OK")
        fb = ("connected"
              if self._firebase and getattr(self._firebase, "online", False)
              else "offline")
        self._log(f"Firebase {fb}", "OK" if fb == "connected" else "WARN")
        data = load_calib()
        if data:
            self._log(
                f"Existing calibration:  step={data['step']}  "
                f"error={data['mean_error']:.4f}  "
                f"pts={len(data['screen_points'])}", "INFO"
            )
        else:
            self._log("No calibration found", "WARN")
        self._log("─" * 46, "SYS")

    # ── button handlers ───────────────────────────────────────────────────────

    @Slot()
    def _on_start_calibration(self):
        self._btn_start.setEnabled(False)
        self._btn_start.setText("CALIBRATING...")
        self._log("Starting calibration...", "SYS")
        # Hide this window so the overlay has the full screen
        self.hide()
        from app.ui.calibration_panel import CalibrationOverlay
        self._cal_overlay = CalibrationOverlay(
            gaze_provider=self._tracker, parent=None)
        self._cal_overlay.finished.connect(self._on_overlay_finished)
        self._cal_overlay.show()
        self._cal_overlay.raise_()
        self._cal_overlay.activateWindow()

    @Slot(bool, int, float, list, list)
    def _on_overlay_finished(self, accepted, step_idx, mean_err,
                              screen_pts, gaze_pts):
        # Show the window again
        self.show()
        self.raise_()
        if screen_pts and gaze_pts:
            from app.services.stores import save_calib
            save_calib(step=step_idx+1, mean_error=mean_err,
                 screen_points=screen_pts, gaze_points=gaze_pts)
            self._tracker.load_calibration(screen_pts, gaze_pts)
            self._calib_panel._refresh_status()
        self._on_calib_done(accepted)

    @Slot()
    def _on_use_match(self):
        self._log("Loading matched calibration from Firebase...", "SYS")
        try:
            matches = self._firebase.find_matching_calibrations()
            if not matches:
                self._log("No matches returned — run manual calibration", "WARN")
                return
            best = matches[0]
            from app.services.stores import save_calib
            save_calib(
                step=best.get("step", 1),
                mean_error=best.get("mean_error", 0.0),
                screen_points=best["screen_points"],
                gaze_points=best["gaze_points"],
            )
            self._tracker.load_calibration(
                best["screen_points"], best["gaze_points"]
            )
            self._log(
                f"Matched calibration loaded  "
                f"error={best.get('mean_error', 0):.4f}", "OK"
            )
            self._refresh_badge()
        except Exception as e:
            self._log(f"Failed to load match: {e}", "ERROR")

    # ── calibration result ────────────────────────────────────────────────────

    @Slot(bool)
    def _on_calib_done(self, accepted: bool):
        data = load_calib()
        n    = len(data["screen_points"]) if data else 0
        err  = data["mean_error"] if data else 1.0
        step = data["step"] if data else 0

        self._log("─" * 46, "SYS")
        self._log(f"Calibration finished  step={step}", "SYS")
        self._log(f"  Points: {n}  Mean error: {err:.4f}", "DATA")
        self._log(
            f"  Result: {'ACCEPTED' if accepted else 'REJECTED'}",
            "OK" if accepted else "WARN"
        )

        if accepted and data:
            self._tracker.load_calibration(
                data["screen_points"], data["gaze_points"]
            )
            self._log("Calibration loaded into tracker", "OK")

            if self._firebase and getattr(self._firebase, "online", False):
                try:
                    ok = self._firebase.upload_calibration(
                        screen_pts=data["screen_points"],
                        gaze_pts=data["gaze_points"],
                        mean_error=err,
                        step=step,
                    )
                    self._log(
                        "Uploaded to community DB" if ok else "Upload failed",
                        "OK" if ok else "WARN"
                    )
                except Exception as e:
                    self._log(f"Firebase error: {e}", "WARN")

        # re-enable start button for re-calibration
        self._btn_start.setEnabled(True)
        self._btn_start.setText("START CALIBRATION")
        self._refresh_badge()
        self._log("─" * 46, "SYS")

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def closeEvent(self, ev):
        # write the first-run flag so scan never runs again
        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            _SCAN_FLAG.write_text("done", encoding="utf-8")
        except Exception:
            pass
        try:
            self._webcam.stop()
        except Exception:
            pass
        try:
            self._tracker.stop()
        except Exception:
            pass
        super().closeEvent(ev)