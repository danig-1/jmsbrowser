"""Copy desktop text without a shell, temporary file or Python dependency."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time


def _copy_windows(text):
    import ctypes
    from ctypes import wintypes as w

    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.CreateWindowExW.argtypes = (w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID)
    user.CreateWindowExW.restype = w.HWND
    user.DestroyWindow.argtypes, user.DestroyWindow.restype = (w.HWND,), w.BOOL
    user.OpenClipboard.argtypes, user.OpenClipboard.restype = (w.HWND,), w.BOOL
    user.EmptyClipboard.argtypes, user.EmptyClipboard.restype = (), w.BOOL
    user.CloseClipboard.argtypes, user.CloseClipboard.restype = (), w.BOOL
    user.SetClipboardData.argtypes, user.SetClipboardData.restype = (w.UINT, w.HANDLE), w.HANDLE
    kernel.GlobalAlloc.argtypes, kernel.GlobalAlloc.restype = (w.UINT, ctypes.c_size_t), w.HGLOBAL
    kernel.GlobalLock.argtypes, kernel.GlobalLock.restype = (w.HGLOBAL,), w.LPVOID
    kernel.GlobalUnlock.argtypes, kernel.GlobalUnlock.restype = (w.HGLOBAL,), w.BOOL
    kernel.GlobalFree.argtypes, kernel.GlobalFree.restype = (w.HGLOBAL,), w.HGLOBAL
    # Use our own invisible message-only window as clipboard owner. A NULL owner
    # can make SetClipboardData fail after EmptyClipboard.
    window = user.CreateWindowExW(0, "STATIC", "", 0, 0, 0, 0, 0, w.HWND(-3), None, None, None)
    if not window:
        raise ctypes.WinError(ctypes.get_last_error())
    memory, opened = None, False
    try:
        # CF_UNICODETEXT has Windows line endings and a final UTF-16 NUL.
        data = (text.replace("\r\n", "\n").replace("\n", "\r\n") + "\0").encode("utf-16-le")
        memory = kernel.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
        if not memory:
            raise ctypes.WinError(ctypes.get_last_error())
        pointer = kernel.GlobalLock(memory)
        if not pointer:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            ctypes.memmove(pointer, data, len(data))
        finally:
            kernel.GlobalUnlock(memory)
        for attempt in range(10):
            if user.OpenClipboard(window):
                opened = True
                break
            time.sleep(0.01)
        if not opened:
            raise RuntimeError("Clipboard is busy; try again.")
        if not user.EmptyClipboard() or not user.SetClipboardData(13, memory):  # CF_UNICODETEXT
            raise ctypes.WinError(ctypes.get_last_error())
        memory = None  # Ownership transferred to Windows.
    finally:
        if opened:
            user.CloseClipboard()
        if memory:
            kernel.GlobalFree(memory)
        user.DestroyWindow(window)


def copy_text(text):
    # Native text clipboards use a NUL terminator. Do not silently drop a suffix.
    if "\0" in text:
        raise ValueError("Content contains a NUL character and cannot be copied as text.")
    if sys.platform == "win32":
        _copy_windows(text)
        return
    if sys.platform == "darwin":
        command, encoding = ["pbcopy"], "utf-8"
    else:
        encoding = "utf-8"
        if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
            command = ["wl-copy", "--type", "text/plain;charset=utf-8"]
        elif os.environ.get("DISPLAY") and shutil.which("xclip"):
            command = ["xclip", "-selection", "clipboard"]
        elif os.environ.get("DISPLAY") and shutil.which("xsel"):
            command = ["xsel", "--clipboard", "--input"]
        else:
            raise RuntimeError("Clipboard unavailable: install wl-clipboard (Wayland) or xclip/xsel (X11).")
    try:
        result = subprocess.run(command, input=text.encode(encoding), stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=3)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("Clipboard unavailable or busy; try again.") from error
    if result.returncode:
        raise RuntimeError("Clipboard unavailable or busy; try again.")
