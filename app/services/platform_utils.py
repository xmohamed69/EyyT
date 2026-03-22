"""
platform_utils.py
Single source of truth for all platform-specific behaviour in EyeTyper.
Never use sys.platform or ctypes.windll directly in other files — use this.

Place at: app/services/platform_utils.py
"""
from __future__ import annotations

import sys
import platform
import subprocess
from typing import Optional

# ── platform flags ────────────────────────────────────────────────────────────
IS_WINDOWS = sys.platform == "win32"
IS_MAC     = sys.platform == "darwin"
IS_LINUX   = sys.platform.startswith("linux")

PLATFORM_NAME = (
    "Windows" if IS_WINDOWS else
    "macOS"   if IS_MAC     else
    "Linux"
)


# ── screen size ───────────────────────────────────────────────────────────────

def get_screen_size() -> tuple[int, int]:
    """Return (width, height) of primary screen — cross-platform."""
    try:
        from PySide6.QtWidgets import QApplication
        scr = QApplication.primaryScreen()
        if scr:
            g = scr.geometry()
            return g.width(), g.height()
    except Exception:
        pass

    if IS_WINDOWS:
        try:
            import ctypes
            u32 = ctypes.windll.user32
            u32.SetProcessDPIAware()
            return u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)
        except Exception:
            pass

    if IS_MAC:
        try:
            out = subprocess.check_output(
                ["system_profiler", "SPDisplaysDataType"],
                stderr=subprocess.DEVNULL
            ).decode()
            for line in out.splitlines():
                if "Resolution" in line:
                    parts = line.strip().split()
                    idx = parts.index("x")
                    return int(parts[idx - 1]), int(parts[idx + 1])
        except Exception:
            pass

    if IS_LINUX:
        try:
            import re
            out = subprocess.check_output(
                ["xrandr", "--current"], stderr=subprocess.DEVNULL
            ).decode()
            for line in out.splitlines():
                if " connected" in line:
                    m = re.search(r"(\d+)x(\d+)\+", line)
                    if m:
                        return int(m.group(1)), int(m.group(2))
        except Exception:
            pass

    return 1920, 1080


# ── CPU fingerprint ───────────────────────────────────────────────────────────

def get_cpu_id() -> str:
    """Anonymous CPU identifier — no personal data."""
    if IS_WINDOWS:
        try:
            out = subprocess.check_output(
                "wmic cpu get ProcessorId",
                shell=True, stderr=subprocess.DEVNULL
            ).decode().strip().split()
            return out[-1] if out else "unknown"
        except Exception:
            pass

    if IS_MAC:
        try:
            out = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                stderr=subprocess.DEVNULL
            ).decode().strip()
            return out[:40]
        except Exception:
            pass

    if IS_LINUX:
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        return line.split(":")[-1].strip()[:40]
        except Exception:
            pass

    return f"unknown_{platform.machine()}"


# ── camera backends ───────────────────────────────────────────────────────────

def get_camera_backends() -> list:
    """Return ordered list of cv2 backends to try."""
    import cv2
    if IS_WINDOWS:
        return [cv2.CAP_DSHOW, cv2.CAP_ANY]
    if IS_MAC:
        return [cv2.CAP_AVFOUNDATION, cv2.CAP_ANY]
    return [cv2.CAP_V4L2, cv2.CAP_ANY]


# ── cursor control ────────────────────────────────────────────────────────────

def move_cursor(x: int, y: int) -> None:
    """Move OS cursor to (x, y) in screen pixels — cross-platform."""
    try:
        import pyautogui
        pyautogui.moveTo(x, y, _pause=False)
        return
    except ImportError:
        pass

    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.user32.SetCursorPos(x, y)
            return
        except Exception:
            pass

    # macOS / Linux fallback
    try:
        subprocess.run(
            ["xdotool", "mousemove", str(x), str(y)],
            check=False, capture_output=True
        )
    except Exception:
        pass


def click_cursor(x: int, y: int) -> None:
    """Perform a left click at (x, y) — cross-platform."""
    try:
        import pyautogui
        pyautogui.click(x, y, _pause=False)
        return
    except ImportError:
        pass

    if IS_WINDOWS:
        try:
            import ctypes
            LEFTDOWN = 0x0002
            LEFTUP   = 0x0004
            u32 = ctypes.windll.user32
            u32.SetCursorPos(x, y)
            u32.mouse_event(LEFTDOWN, 0, 0, 0, 0)
            u32.mouse_event(LEFTUP,   0, 0, 0, 0)
            return
        except Exception:
            pass

    try:
        subprocess.run(
            ["xdotool", "mousemove", str(x), str(y), "click", "1"],
            check=False, capture_output=True
        )
    except Exception:
        pass


# ── tray support ──────────────────────────────────────────────────────────────

def tray_supported() -> bool:
    """Check if system tray is available on this platform."""
    try:
        from PySide6.QtWidgets import QSystemTrayIcon
        return QSystemTrayIcon.isSystemTrayAvailable()
    except Exception:
        return False


# ── data directory ────────────────────────────────────────────────────────────

def get_data_dir():
    """
    Return the Path where runtime data lives.
    Frozen EXE/app: next to the executable.
    Source: project root / data.
    """
    from pathlib import Path
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "data"
    return Path("data")