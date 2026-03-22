# specs/EyeTyper-mac.spec
# EyeTyper Keyboard + Camera — macOS build
# Output: dist/EyeTyper.app
# Run from project root: pyinstaller specs/EyeTyper-mac.spec --distpath dist --workpath build

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

mp_datas = collect_data_files("mediapipe")
mp_libs  = collect_dynamic_libs("mediapipe")

_HIDDEN = [
    "mediapipe", "mediapipe.tasks", "mediapipe.tasks.python",
    "mediapipe.tasks.python.vision", "mediapipe.python",
    "mediapipe.python.solutions", "mediapipe.python.solutions.face_mesh",
    "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
    "numpy", "cv2", "pyautogui",
    "matplotlib", "matplotlib.pyplot", "matplotlib.backends.backend_agg",
    "firebase_admin", "spellchecker", "requests",
    "app", "app.contracts",
    "app.services.calibration_store", "app.services.dwell_selector",
    "app.services.txt_exporter", "app.services.session_logger",
    "app.services.mouse_controller", "app.services.ai_corrector",
    "app.services.firebase_client", "app.services.platform_utils",
    "app.ui.calibration_panel", "app.ui.camera_check",
    "app.ui.camera_overlay", "app.ui.keyboard_widget",
    "app.ui.keyboard_window", "app.ui.layouts",
    "app.ui.suggestion_bar", "app.ui.text_output",
    "app.ui.webcam_placeholder", "app.ui.welcome_dialog",
    "app.vision.mediapipe_tracker", "app.vision.calibration",
    "app.vision.camera", "app.vision.face_mesh",
    "app.vision.provider", "app.vision.smoothing",
]

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=mp_libs,
    datas=[*mp_datas, ("icon.ico", ".")],
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
    name="EyeTyper",
    debug=False, strip=False, upx=False,  # upx off on macOS (causes issues)
    console=False,
    icon="icon.ico",
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False,
    name="EyeTyper",
)

app = BUNDLE(
    coll,
    name="EyeTyper.app",
    icon="icon.ico",
    bundle_identifier="com.eyetyper.keyboard",
    info_plist={
        "NSCameraUsageDescription":
            "EyeTyper uses your camera to track your eye gaze.",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleName": "EyeTyper",
        "LSUIElement": False,
    },
)
