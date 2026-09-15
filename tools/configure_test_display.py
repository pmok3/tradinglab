"""Configure and verify the hosted Windows desktop before GUI test processes.

No third-party utilities or registry edits. Local invocation is read-only via
--check-only; changing the display is restricted to GitHub Actions runners.
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time
from pathlib import Path
from typing import Protocol


class Display(Protocol):
    def size(self) -> tuple[int, int]: ...
    def resize(self, width: int, height: int) -> None: ...


# Explicit Win32 field widths keep this structure correct even in non-Windows
# unit tests; ctypes.wintypes uses the host C ABI for some types.
class _DevMode(ctypes.Structure):
    _fields_ = [
        ("device_name", ctypes.c_uint16 * 32),
        ("spec_version", ctypes.c_uint16), ("driver_version", ctypes.c_uint16),
        ("size", ctypes.c_uint16), ("driver_extra", ctypes.c_uint16),
        ("fields", ctypes.c_uint32),
        ("position_x", ctypes.c_int32), ("position_y", ctypes.c_int32),
        ("orientation", ctypes.c_uint32), ("fixed_output", ctypes.c_uint32),
        ("color", ctypes.c_int16), ("duplex", ctypes.c_int16),
        ("y_resolution", ctypes.c_int16), ("tt_option", ctypes.c_int16),
        ("collate", ctypes.c_int16), ("form_name", ctypes.c_uint16 * 32),
        ("log_pixels", ctypes.c_uint16), ("bits_per_pixel", ctypes.c_uint32),
        ("width", ctypes.c_uint32), ("height", ctypes.c_uint32),
        ("display_flags", ctypes.c_uint32), ("frequency", ctypes.c_uint32),
        ("icm_method", ctypes.c_uint32), ("icm_intent", ctypes.c_uint32),
        ("media_type", ctypes.c_uint32), ("dither_type", ctypes.c_uint32),
        ("reserved1", ctypes.c_uint32), ("reserved2", ctypes.c_uint32),
        ("panning_width", ctypes.c_uint32), ("panning_height", ctypes.c_uint32),
    ]


class WindowsDisplay:
    def __init__(self) -> None:
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.EnumDisplaySettingsW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_uint32, ctypes.POINTER(_DevMode),
        ]
        self._user32.EnumDisplaySettingsW.restype = ctypes.c_int32
        self._user32.ChangeDisplaySettingsExW.argtypes = [
            ctypes.c_wchar_p, ctypes.POINTER(_DevMode), ctypes.c_void_p,
            ctypes.c_uint32, ctypes.c_void_p,
        ]
        self._user32.ChangeDisplaySettingsExW.restype = ctypes.c_int32
        self._user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        self._user32.GetSystemMetrics.restype = ctypes.c_int

    def size(self) -> tuple[int, int]:
        return self._user32.GetSystemMetrics(0), self._user32.GetSystemMetrics(1)

    def resize(self, width: int, height: int) -> None:
        mode = _DevMode()
        mode.size = ctypes.sizeof(mode)
        if not self._user32.EnumDisplaySettingsW(None, 0xFFFFFFFF, ctypes.byref(mode)):
            raise RuntimeError("Cannot read the runner's primary display mode")
        mode.width, mode.height = width, height
        mode.fields = 0x00080000 | 0x00100000  # DM_PELSWIDTH | DM_PELSHEIGHT
        # Test support before applying; do not request a reboot or persist a mode.
        for flags in (2, 0):  # CDS_TEST, then session-only apply
            result = self._user32.ChangeDisplaySettingsExW(None, ctypes.byref(mode), None, flags, None)
            if result != 0:
                raise RuntimeError(f"Windows rejected {width}x{height} (display result {result})")


def configure_display(
    display: Display, width: int, height: int, *, check_only: bool = False,
) -> tuple[int, int]:
    before = display.size()
    print(f"Windows desktop before setup: {before[0]}x{before[1]}")
    if before != (width, height) and not check_only:
        display.resize(width, height)
        deadline = time.monotonic() + 5
        while display.size() != (width, height) and time.monotonic() < deadline:
            time.sleep(0.1)
    actual = display.size()
    if actual[0] < width or actual[1] < height:
        raise RuntimeError(f"GUI tests require at least {width}x{height}; actual desktop is {actual[0]}x{actual[1]}")
    return actual


def verify_tk_display(width: int, height: int) -> tuple[int, int]:
    import tkinter as tk

    root = tk.Tk()
    try:
        root.withdraw()
        actual = root.winfo_screenwidth(), root.winfo_screenheight()
    finally:
        root.destroy()
    if actual[0] < width or actual[1] < height:
        raise RuntimeError(f"Tk sees only {actual[0]}x{actual[1]}, below the requested {width}x{height}")
    print(f"Tk desktop verified: {actual[0]}x{actual[1]}")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)
    if args.width <= 0 or args.height <= 0:
        parser.error("Display dimensions must be positive")
    if sys.platform != "win32":
        parser.error("This setup is only for the Windows runner desktop")
    if not args.check_only and os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("Display changes are restricted to GitHub Actions; use --check-only locally")
    try:
        configure_display(WindowsDisplay(), args.width, args.height, check_only=args.check_only)
        width, height = verify_tk_display(args.width, args.height)
        # The coverage producer hashes TRADINGLAB_* measurement environment.
        # Record the actual Tk desktop, never manufacture a hosted run identity.
        env_file = os.environ.get("GITHUB_ENV")
        if not args.check_only and env_file:
            with Path(env_file).open("a", encoding="utf-8") as stream:
                stream.write(f"TRADINGLAB_CI_DISPLAY={width}x{height}\n")
    except (OSError, RuntimeError) as exc:
        print(f"GUI display setup failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
