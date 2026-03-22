"""
main.py — EyeTyper entry point.

Changes vs original:
  • Passes cam_window to window.set_camera_window() so the Settings panel
    can show/hide it via the floating_camera toggle.
  • Everything else is unchanged.
"""
from __future__ import annotations

import sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette, QIcon
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu

from app.ui.startup_dialogs import require_camera, maybe_show_welcome
from app.vision.mediapipe_tracker import MediaPipeTracker
from app.services.stores import load_calib
from app.ui.main_window import MainWindow
from app.ui.camera_widget import CameraWindow


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


def _setup_tray(app: QApplication, window: MainWindow) -> QSystemTrayIcon:
    tray = QSystemTrayIcon(app)
    try:
        tray.setIcon(QIcon("icon.ico"))
    except Exception:
        pass
    tray.setToolTip("EyeTyper")

    menu = QMenu()
    menu.setStyleSheet("""
        QMenu {
            background: #0e1018; color: #c0cce0;
            border: 1px solid #1a2030; border-radius: 6px;
            padding: 4px; font-family: Consolas; font-size: 11px;
        }
        QMenu::item { padding: 7px 20px; border-radius: 4px; }
        QMenu::item:selected { background: #161c2c; }
        QMenu::separator { height: 1px; background: #1a2030; margin: 4px 0; }
    """)

    show_action = menu.addAction("Show")
    hide_action = menu.addAction("Hide")
    menu.addSeparator()
    quit_action = menu.addAction("Quit")

    show_action.triggered.connect(window.show)
    hide_action.triggered.connect(window.hide)
    quit_action.triggered.connect(app.quit)

    tray.setContextMenu(menu)

    def _on_tray_click(reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            window.setVisible(not window.isVisible())

    tray.activated.connect(_on_tray_click)
    tray.show()
    return tray


def main() -> None:
    QApplication.setQuitOnLastWindowClosed(False)

    app = QApplication(sys.argv)
    app.setApplicationName("EyeTyper")
    app.setStyle("Fusion")
    _dark_palette(app)

    try:
        app.setWindowIcon(QIcon("icon.ico"))
    except Exception:
        pass

    camera_index = require_camera()
    maybe_show_welcome()

    tracker = MediaPipeTracker(camera_index=camera_index)

    data = load_calib()
    if data and data.get("screen_points") and data.get("gaze_points"):
        tracker.load_calibration(data["screen_points"], data["gaze_points"])

    window = MainWindow(gaze_provider=tracker, tracker=tracker)
    window.show()

    # Floating camera feed — top-right corner, separate window
    cam_window = CameraWindow(tracker=tracker)
    # Let MainWindow control show/hide via settings
    window.set_camera_window(cam_window)

    tray = _setup_tray(app, window)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()