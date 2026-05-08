from .screen_capture_kit import (
    list_displays,
    get_mouse_position,
    move_mouse,
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
from .screen_capture_kit._scapkit import check_permission as check_permission_c
from typing import Literal
import platform
from asyncio import subprocess

__all__ = [
    "types",
    "check_permission",
    "open_permission_settings",
    "list_displays",
    "get_mouse_position",
    "move_mouse",
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


def check_permission(
    permission_type: Literal["ScreenCapture", "Accessibility"],
) -> bool:
    if platform.system() != "Darwin":
        return True

    return check_permission_c(permission_type)


async def open_permission_settings(
    permission_type: Literal["ScreenCapture", "Accessibility"],
) -> None:
    if platform.system() != "Darwin":
        return

    url = f"x-apple.systempreferences:com.apple.preference.security?Privacy_{permission_type}"
    proc = await subprocess.create_subprocess_exec("open", url)
    await proc.wait()
