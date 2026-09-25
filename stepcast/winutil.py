"""Windows foreground-window helpers via ctypes (no pywin32 dependency)."""
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32

# Make the process DPI-aware so window rects, click coords (low-level hook),
# and mss pixel grabs all agree in physical pixels under display scaling.
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2
except Exception:  # noqa: BLE001
    try:
        user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass

_GetForegroundWindow = user32.GetForegroundWindow
_GetWindowTextW = user32.GetWindowTextW
_GetWindowTextLengthW = user32.GetWindowTextLengthW
_GetWindowThreadProcessId = user32.GetWindowThreadProcessId

_DWMWA_EXTENDED_FRAME_BOUNDS = 9


def foreground_window_rect() -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) of the focused window in screen pixels.

    Prefers DWM extended frame bounds, which exclude the invisible
    drop-shadow border that GetWindowRect includes on Win10/11.
    """
    hwnd = _GetForegroundWindow()
    if not hwnd:
        return None
    rect = wintypes.RECT()
    try:
        res = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect), ctypes.sizeof(rect))
        if res == 0:
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception:  # noqa: BLE001
        pass
    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return rect.left, rect.top, rect.right, rect.bottom
    return None


def foreground_window_title() -> str:
    """Return the title text of the currently focused window."""
    hwnd = _GetForegroundWindow()
    if not hwnd:
        return ""
    length = _GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    _GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def foreground_is_own_process() -> bool:
    """True when the focused window belongs to this process (our own UI)."""
    import os

    hwnd = _GetForegroundWindow()
    if not hwnd:
        return False
    pid = wintypes.DWORD()
    _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == os.getpid()


def virtual_screen_origin() -> tuple[int, int]:
    """Top-left of the virtual desktop (negative with monitors left/above)."""
    return user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)


def foreground_process_name() -> str:
    """Return the executable name owning the focused window (best effort)."""
    import os

    hwnd = _GetForegroundWindow()
    if not hwnd:
        return ""
    pid = wintypes.DWORD()
    _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
    finally:
        kernel32.CloseHandle(handle)
    return ""
