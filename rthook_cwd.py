# rthook_cwd.py
# Runtime hook — PyInstaller executes this before your app starts.
# Sets the working directory to _MEIPASS so relative paths like
# "data/face_landmarker.task" and "icon.ico" resolve correctly
# when running from the bundled .exe / binary.
import os
import sys

if hasattr(sys, "_MEIPASS"):
    os.chdir(sys._MEIPASS)