import ctypes
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jmsbrowser import clipboard


def test_nul_content_is_rejected_before_touching_clipboard(monkeypatch):
    native = Mock()
    monkeypatch.setattr(clipboard, "_copy_windows", native)
    with pytest.raises(ValueError, match="NUL"):
        clipboard.copy_text("first\0last")
    native.assert_not_called()


@pytest.mark.parametrize("platform,environment,available,command", [
    ("darwin", {}, [], ["pbcopy"]),
    ("linux", {"WAYLAND_DISPLAY": "wayland-0"}, ["wl-copy"], ["wl-copy", "--type", "text/plain;charset=utf-8"]),
    ("linux", {"DISPLAY": ":0"}, ["xclip"], ["xclip", "-selection", "clipboard"]),
    ("linux", {"DISPLAY": ":0"}, ["xsel"], ["xsel", "--clipboard", "--input"]),
])
def test_desktop_tools_receive_full_utf8_over_stdin_without_a_shell(monkeypatch, platform, environment, available, command):
    monkeypatch.setattr(clipboard.sys, "platform", platform)
    monkeypatch.setattr(clipboard.os, "environ", environment)
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: name if name in available else None)
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(clipboard.subprocess, "run", run)
    body = "é界🙂\n$(literal) `literal`\n" * 30000
    clipboard.copy_text(body)
    run.assert_called_once_with(command, input=body.encode("utf-8"), stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=3)


@pytest.mark.parametrize("failure", [OSError("unavailable"), subprocess.TimeoutExpired("pbcopy", 3), None])
def test_tool_failure_is_reported(monkeypatch, failure):
    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    monkeypatch.setattr(clipboard.subprocess, "run", Mock(side_effect=failure, return_value=SimpleNamespace(returncode=1)))
    with pytest.raises(RuntimeError, match="Clipboard unavailable"):
        clipboard.copy_text("text")


def test_missing_linux_tool_is_reported(monkeypatch):
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setattr(clipboard.os, "environ", {})
    with pytest.raises(RuntimeError, match="wl-clipboard"):
        clipboard.copy_text("text")


@pytest.fixture
def windows_api(monkeypatch):
    buffer = ctypes.create_string_buffer(1000)
    user = SimpleNamespace(**{name: Mock(return_value=1) for name in
                             ("CreateWindowExW", "DestroyWindow", "OpenClipboard", "EmptyClipboard", "CloseClipboard", "SetClipboardData")})
    kernel = SimpleNamespace(GlobalAlloc=Mock(return_value=42), GlobalLock=Mock(return_value=ctypes.addressof(buffer)),
                             GlobalUnlock=Mock(return_value=1), GlobalFree=Mock(return_value=0))
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kwargs: user if name == "user32" else kernel, raising=False)
    monkeypatch.setattr(clipboard.time, "sleep", Mock())
    return user, kernel, buffer


def test_windows_unicode_memory_ownership_and_cleanup(windows_api):
    user, kernel, buffer = windows_api
    clipboard._copy_windows("é界🙂\nnext\r\nlast")
    expected = "é界🙂\r\nnext\r\nlast\0".encode("utf-16-le")
    assert buffer.raw[:len(expected)] == expected
    assert user.CreateWindowExW.call_args.args[3] == 0  # invisible owner
    kernel.GlobalAlloc.assert_called_once_with(2, len(expected))
    user.SetClipboardData.assert_called_once_with(13, 42)
    kernel.GlobalFree.assert_not_called()  # Windows owns it after success
    user.CloseClipboard.assert_called_once()
    user.DestroyWindow.assert_called_once()


def test_windows_busy_clipboard_retries_without_emptying_it_and_releases_memory(windows_api):
    user, kernel, _ = windows_api
    user.OpenClipboard.return_value = 0
    with pytest.raises(RuntimeError, match="busy"):
        clipboard._copy_windows("text")
    assert user.OpenClipboard.call_count == 10
    user.EmptyClipboard.assert_not_called()
    user.CloseClipboard.assert_not_called()
    kernel.GlobalFree.assert_called_once_with(42)
    user.DestroyWindow.assert_called_once()


def test_windows_failed_transfer_frees_memory_and_closes_clipboard(windows_api, monkeypatch):
    user, kernel, _ = windows_api
    user.SetClipboardData.return_value = 0
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda code: OSError("transfer failed"), raising=False)
    with pytest.raises(OSError, match="transfer failed"):
        clipboard._copy_windows("text")
    kernel.GlobalFree.assert_called_once_with(42)
    user.CloseClipboard.assert_called_once()
    user.DestroyWindow.assert_called_once()
