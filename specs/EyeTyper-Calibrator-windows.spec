# specs/EyeTyper-Calibrator-windows.spec
# EyeTyper Calibrator — Windows build
# Output: dist/EyeTyperCalibrator/EyeTyperCalibrator.exe

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# ── project root = one level up from this spec file ──────────────────────────
_ROOT = Path(SPECPATH).parent

_py_dll = Path(r"C:\Users\admin\AppData\Local\Programs\Python\Python312\python312.dll")
if not _py_dll.exists():
    for _c in [
        Path(sys.executable).parent / "python312.dll",
        Path(r"C:\Windows\System32\python312.dll"),
    ]:
        if _c.exists():
            _py_dll = _c
            break

_extra_bins = [(str(_py_dll), ".")] if _py_dll.exists() else []

mp_datas = collect_data_files("mediapipe")
mp_libs  = collect_dynamic_libs("mediapipe")

_HIDDEN = [
    "mediapipe", "mediapipe.tasks", "mediapipe.tasks.python",
    "mediapipe.tasks.python.vision", "mediapipe.python",
    "mediapipe.python.solutions", "mediapipe.python.solutions.face_mesh",
    "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
    "numpy", "cv2",
    "matplotlib", "matplotlib.pyplot", "matplotlib.backends.backend_agg",
    "firebase_admin", "requests",
    "app", "app.contracts",
    "app.services.stores", "app.services.platform_utils",
    "app.services.firebase_client",
    "app.ui.calibration_panel", "app.ui.calibrator_app",
    "app.ui.camera_widget", "app.ui.simple_widgets",
    "app.ui.startup_dialogs",
    "app.vision.mediapipe_tracker", "app.vision.calibration",
    "app.vision.camera", "app.vision.face_mesh",
    "app.vision.provider", "app.vision.smoothing",
]

block_cipher = None

a = Analysis(
    [str(_ROOT / "calibrator.py")],
    pathex=[str(_ROOT)],
    binaries=mp_libs + _extra_bins,
    datas=[*mp_datas, (str(_ROOT / "icon2.ico"), ".")],
    hiddenimports=_HIDDEN,
    hookspath=[],
    runtime_hooks=[str(_ROOT / "rthook_cwd.py")],
    excludes=["tkinter", "scipy", "pandas", "IPython", "jupyter"],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="EyeTyperCalibrator",
    debug=False, strip=False, upx=True,
    console=False,
    icon=str(_ROOT / "icon2.ico"),
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True,
    name="EyeTyperCalibrator",
)
