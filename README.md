# 👁️ EyeTyper

An accessibility-focused on-screen keyboard controlled entirely by eye gaze.  
Built with **MediaPipe**, **OpenCV**, and **PySide6** — no mouse or touch required.

---

## ✨ Features

- **Eye-gaze typing** — look at a key and dwell to select it
- **Multi-step calibration** — 5, 9, or 13 point affine mapping for high accuracy
- **Live camera preview** — see yourself during calibration with face-detected indicator
- **Dwell time control** — adjustable from 400 ms to 2000 ms
- **Session export** — saves every keystroke to `Documents\EyeTyper Exports\` as Excel
- **Welcome guide** — built-in instruction screen on first launch (with "do not show again")

---

## 🖥️ Requirements

| Dependency    | Version                                            |
| ------------- | -------------------------------------------------- |
| Python        | 3.10 or 3.11 (3.12 not yet supported by MediaPipe) |
| PySide6       | `pip install PySide6`                              |
| mediapipe     | `pip install mediapipe`                            |
| opencv-python | `pip install opencv-python`                        |
| numpy         | `pip install numpy`                                |
| openpyxl      | `pip install openpyxl`                             |
| pyinstaller   | `pip install pyinstaller` (build only)             |

Install all at once:

```bash
pip install PySide6 mediapipe opencv-python numpy openpyxl
```

---

## 🚀 Run from source

```bash
git clone https://github.com/YOUR_USERNAME/EyeTyper.git
cd EyeTyper
pip install PySide6 mediapipe opencv-python numpy openpyxl
python main.py
```

On first launch the app will download the MediaPipe face landmarker model (~30 MB) automatically.

---

## 📦 Build the EXE

Requirements: Python 3.10/3.11, all dependencies above, and PyInstaller.

```bash
pip install pyinstaller
build.bat
```

Output:

```
dist\EyeTyper\EyeTyper.exe   ← distribute this whole folder
build\EyeTyper\              ← intermediate files, safe to delete
```

> All build files are kept inside `build\` and `dist\` — delete them freely between versions.

---

## 📁 Project Structure

```
EyeTyper/
├── main.py                        ← entry point
├── icon.ico                       ← app icon
├── EyeTyper.spec                  ← PyInstaller build spec
├── build.bat                      ← one-click build script
├── data/
│   ├── calibration.json           ← saved calibration (auto-created)
│   └── face_landmarker.task       ← MediaPipe model (auto-downloaded)
└── app/
    ├── contracts.py               ← shared dataclasses & protocols
    ├── services/
    │   ├── calibration_store.py
    │   ├── dwell_selector.py
    │   ├── excel_exporter.py
    │   └── session_logger.py
    ├── ui/
    │   ├── calibration_panel.py
    │   ├── keyboard_widget.py
    │   ├── main_window.py
    │   ├── text_output.py
    │   ├── webcam_placeholder.py
    │   └── welcome_dialog.py
    └── vision/
        ├── calibration.py
        ├── camera.py
        ├── face_mesh.py
        ├── mediapipe_tracker.py
        ├── provider.py
        └── smoothing.py
```

---

## 🎯 How to use

1. **Position yourself** — sit 40–70 cm from screen, face fully visible in camera
2. **Calibrate** — click ▶ Calibrate, look at each red dot until the ring fills
3. **Type** — look at any key and hold gaze to select it
4. **Export** — click 📊 Export to save your session to `Documents\EyeTyper Exports\`

> Re-calibrate any time lighting or your position changes for best accuracy.

---

## 📊 Session Export

Typed sessions are saved automatically to:

```
C:\Users\<you>\Documents\EyeTyper Exports\session_YYYYMMDD_HHMMSS.xlsx
```

Each file contains session metadata, typed text, and a full keystroke log with gaze coordinates and dwell times.

---

## 📄 License

MIT License — free to use, modify, and distribute.

---

## 🙏 Acknowledgements

- [MediaPipe](https://mediapipe.dev/) by Google for face landmark detection
- [PySide6](https://doc.qt.io/qtforpython/) for the UI framework
- [OpenCV](https://opencv.org/) for camera access
