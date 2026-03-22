# specs/EyeTyper-Calibrator-linux.spec
# EyeTyper Calibrator — Linux build
# Output: dist/EyeTyperCalibrator/EyeTyperCalibrator

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

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
    "app.services.calibration_store", "app.services.platform_utils",
    "app.services.firebase_client",
    "app.ui.calibration_panel", "app.ui.calibrator_app",
    "app.ui.camera_check", "app.ui.webcam_placeholder",
    "app.ui.welcome_dialog",
    "app.vision.mediapipe_tracker", "app.vision.calibration",
    "app.vision.camera", "app.vision.face_mesh",
    "app.vision.provider", "app.vision.smoothing",
]

block_cipher = None

a = Analysis(
    ["calibrator.py"],
    pathex=["."],
    binaries=mp_libs,
    datas=[*mp_datas, ("calibrator_icon.ico", ".")],
    hiddenimports=_HIDDEN,
    hookspath=[],
    runtime_hooks=["rthook_cwd.py"],
    excludes=["tkinter", "scipy", "pandas", "IPython", "jupyter"],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="EyeTyperCalibrator",
    debug=False, strip=True,
    upx=True,
    console=False,
    icon="calibrator_icon.ico",
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=True, upx=True,
    name="EyeTyperCalibrator",
)
