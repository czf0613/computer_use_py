from importlib import import_module
from .process import SubprocessResult, SubprocessStream, run_subprocess
from typing import Literal, TYPE_CHECKING
import platform
from asyncio import subprocess

if TYPE_CHECKING:
    from .window import (
        list_windows, open_window, list_accessible_windows, open_accessible_window,
        WindowSession, AccessibleWindowSession,
    )
    from .window_types import (
        WindowRef, AccessibleWindowRef, ElementRef, WindowInfo, AccessibleWindowInfo,
        WindowRect, WindowPoint, WindowFrame, ElementInfo, ElementSnapshot,
        WindowCapability, WindowCapabilities, ActionReceipt,
    )
    from .window_errors import WindowError, WindowPermissionError, WindowTimeoutError
    from .window_profiles import (
        BackgroundProfile, ProfileAction, ProfileValidation,
        register_profile, verify_profile, profile_validation,
    )
    from .recording import RecordingHandle, RecordingResult
    from .screen_capture_kit import (
        move_mouse,
        move_mouse_relative,
        mouse_click,
        mouse_long_click,
        mouse_click_action,
        mouse_scroll,
        mouse_drag,
        keyboard_click,
        keyboard_click_action,
        key_combo,
        set_clipboard,
        get_clipboard,
        clipboard_paste,
        start_capture,
        stop_capture,
        current_frame_jpg,
        current_frame_bgra,
        start_recording,
        stop_recording,
        types,
    )
    from .screen_capture_kit._scapkit import list_displays, get_mouse_position

__all__ = [
    "run_subprocess",
    "SubprocessResult",
    "SubprocessStream",
    "types",
    "check_permission",
    "open_permission_settings",
    "list_displays",
    "get_mouse_position",
    "move_mouse",
    "move_mouse_relative",
    "mouse_click",
    "mouse_long_click",
    "mouse_click_action",
    "mouse_scroll",
    "mouse_drag",
    "keyboard_click",
    "keyboard_click_action",
    "key_combo",
    "set_clipboard",
    "get_clipboard",
    "clipboard_paste",
    "start_capture",
    "stop_capture",
    "current_frame_jpg",
    "current_frame_bgra",
    "RecordingHandle",
    "RecordingResult",
    "start_recording",
    "stop_recording",
]

_DESKTOP_EXPORTS = frozenset(__all__) - {
    "run_subprocess",
    "SubprocessResult",
    "SubprocessStream",
    "check_permission",
    "open_permission_settings",
}
_RECORDING_EXPORTS = frozenset(
    {"RecordingHandle", "RecordingResult", "start_recording", "stop_recording"}
)
if platform.system() not in {"Darwin", "Windows"}:
    __all__ = [name for name in __all__ if name not in _DESKTOP_EXPORTS]

_WINDOW_EXPORTS = {
    **dict.fromkeys(("list_windows", "open_window", "list_accessible_windows",
                     "open_accessible_window", "WindowSession", "AccessibleWindowSession"), ".window"),
    **dict.fromkeys(("WindowRef", "AccessibleWindowRef", "ElementRef", "WindowInfo",
                     "AccessibleWindowInfo", "WindowRect", "WindowPoint", "WindowFrame",
                     "ElementInfo", "ElementSnapshot", "WindowCapability",
                     "WindowCapabilities", "ActionReceipt"), ".window_types"),
    **dict.fromkeys(("WindowError", "WindowPermissionError", "WindowTimeoutError"), ".window_errors"),
    **dict.fromkeys(("BackgroundProfile", "ProfileAction", "ProfileValidation",
                     "register_profile", "verify_profile", "profile_validation"), ".window_profiles"),
}
__all__ += list(_WINDOW_EXPORTS)


def __getattr__(name: str):
    if name in _WINDOW_EXPORTS:
        return getattr(import_module(_WINDOW_EXPORTS[name], __name__), name)
    # Subprocess execution is pure Python and does not need the desktop extension.
    if name in _DESKTOP_EXPORTS:
        if platform.system() not in {"Darwin", "Windows"}:
            raise NotImplementedError(f"{name} requires a macOS or Windows desktop extension")
        if name in _RECORDING_EXPORTS:
            return getattr(import_module(".recording", __name__), name)
        suffix = "._scapkit" if name in {"list_displays", "get_mouse_position"} else ""
        return getattr(import_module(".screen_capture_kit" + suffix, __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


def check_permission(
    permission_type: Literal["ScreenCapture", "Accessibility"],
) -> bool:
    """Check whether the current process has the specified macOS permission.

    Returns True on non-macOS platforms. On macOS, checks Accessibility
    (AXIsProcessTrusted) or ScreenCapture (CGPreflightScreenCaptureAccess).

    Args:
        permission_type: "ScreenCapture" or "Accessibility".
    """
    if permission_type not in {"ScreenCapture", "Accessibility"}:
        raise ValueError("unknown permission type")
    if platform.system() != "Darwin":
        return True

    from .screen_capture_kit._scapkit import check_permission as check_permission_c

    return check_permission_c(permission_type)


async def open_permission_settings(
    permission_type: Literal["ScreenCapture", "Accessibility"],
) -> None:
    """Open the macOS System Preferences pane for the specified permission.

    No-op on non-macOS platforms.

    Args:
        permission_type: "ScreenCapture" or "Accessibility".
    """
    if platform.system() != "Darwin":
        return

    url = f"x-apple.systempreferences:com.apple.preference.security?Privacy_{permission_type}"
    proc = await subprocess.create_subprocess_exec("open", url)
    await proc.wait()
