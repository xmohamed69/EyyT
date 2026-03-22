"""
calibrator.py — EyeTyper Calibrator entry point.

Launches the CalibratorWindow which contains the keyboard-fitted
calibration overlay (🎯 CALIBRATE button).
Run this OR use the CALIBRATE button inside main.py directly.
"""
from __future__ import annotations

import sys
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication

from app.ui.startup_dialogs import require_camera
from app.vision.mediapipe_tracker import MediaPipeTracker
from app.services.stores import load_kb_calib
from app.ui.calibrator_app import CalibratorWindow


def _dark_palette(app: QApplication) -> None:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window,          QColor(7,  9, 15))
    p.setColor(QPalette.ColorRole.WindowText,      QColor(200, 215, 240))
    p.setColor(QPalette.ColorRole.Base,            QColor(5,  7, 12))
    p.setColor(QPalette.ColorRole.AlternateBase,   QColor(11, 14, 24))
    p.setColor(QPalette.ColorRole.Text,            QColor(200, 215, 240))
    p.setColor(QPalette.ColorRole.Button,          QColor(14, 18, 32))
    p.setColor(QPalette.ColorRole.ButtonText,      QColor(200, 215, 240))
    p.setColor(QPalette.ColorRole.Highlight,       QColor(0, 180, 160))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    app.setPalette(p)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("EyeTyper Calibrator")
    app.setStyle("Fusion")
    _dark_palette(app)

    try:
        app.setWindowIcon(QIcon("calibrator_icon.ico"))
    except Exception:
        pass

    camera_index = require_camera()

    tracker = MediaPipeTracker(camera_index=camera_index)

    # load existing keyboard calibration so tracker is ready immediately
    kb_data = load_kb_calib()
    if kb_data and kb_data.get("matrix"):
        tracker.load_kb_calibration(kb_data["matrix"])

    window = CalibratorWindow(tracker=tracker)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()