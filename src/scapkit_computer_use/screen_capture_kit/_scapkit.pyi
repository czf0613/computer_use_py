from typing import Literal

def keyboard_click(key_code: int, action: Literal["down", "up"], flags: int = 0) -> None:
    """Low-level: post a single keyboard event by numeric key code.

    This is the raw C binding. Prefer the Python wrapper
    `screen_capture_kit.keyboard_click_action(key, action, modifiers)`
    which accepts key names (e.g. "a", "return") and modifier lists.

    Args:
        key_code: macOS virtual key code (CGKeyCode).
        action: "down" or "up".
        flags: Bitwise OR of CGEventFlags (default 0).

    Raises:
        ValueError: If action is not "down" or "up".

    Requires Accessibility permissions on macOS.
    """
    ...

def list_displays() -> list[dict]:
    """List all active displays with their coordinates, dimensions, and scale factor.

    Returns a list of display info dicts with keys: id, x, y, width, height,
    scale_factor, is_main. Coordinates are in the macOS global display
    coordinate space (points), which is the same coordinate space used by
    CGEvent for mouse/keyboard actions.

    The scale_factor field gives the Retina scaling ratio
    (physical pixels = points * scale_factor).

    Raises:
        OSError: If CGGetActiveDisplayList fails.
    """
    ...

def get_mouse_position() -> dict:
    """Get the current mouse cursor position.

    Returns a dict with keys 'x' and 'y' representing the cursor location
    in macOS global display point coordinates.
    """
    ...

def move_mouse(x: int, y: int) -> None:
    """Move the mouse cursor to the specified position.

    Coordinates are in macOS global display point coordinates
    (same coordinate space as get_mouse_position and list_displays).

    Requires Accessibility permissions on macOS. The system will prompt
    the user to grant access the first time this is called.

    Args:
        x: X coordinate in points.
        y: Y coordinate in points.
    """
    ...

def mouse_click(key: Literal["left", "right"], action: Literal["down", "up"]) -> None:
    """Post a single mouse button event at the current cursor position.

    Args:
        key: Which mouse button — "left" or "right".
        action: Whether to press or release — "down" or "up".

    Raises:
        ValueError: If key is not "left"/"right" or action is not "down"/"up".

    Requires Accessibility permissions on macOS.
    """
    ...

def check_permission(
    permission_type: Literal["ScreenCapture", "Accessibility"],
) -> bool:
    """Check whether the current process has the specified macOS permission.

    Uses AXIsProcessTrusted() for Accessibility and
    CGPreflightScreenCaptureAccess() for ScreenCapture.

    Args:
        permission_type: "ScreenCapture" or "Accessibility".

    Raises:
        ValueError: If permission_type is not a recognized value.
    """
    ...
