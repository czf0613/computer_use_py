from importlib import import_module
from .process import SubprocessResult, SubprocessStream, run_subprocess
from typing import Literal, TYPE_CHECKING
import platform
from asyncio import subprocess

if TYPE_CHECKING:
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
]

_DESKTOP_EXPORTS = frozenset(__all__) - {
    "run_subprocess",
    "SubprocessResult",
    "SubprocessStream",
    "check_permission",
    "open_permission_settings",
}
if platform.system() != "Darwin":
    __all__ = [name for name in __all__ if name not in _DESKTOP_EXPORTS]


def __getattr__(name: str):
    # Subprocess execution is pure Python and does not need the macOS extension.
    if name in _DESKTOP_EXPORTS:
        if platform.system() != "Darwin":
            raise NotImplementedError(f"{name} requires the macOS desktop extension")
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
