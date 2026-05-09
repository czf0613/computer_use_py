from typing import Literal
from .types import CaptureHandle, BGRAPack, Point2D, DisplayInfo

def keyboard_click(
    key_code: int, action: Literal["down", "up"], flags: int = 0
) -> None:
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

def list_displays() -> list[DisplayInfo]:
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

def get_mouse_position() -> Point2D:
    """Get the current mouse cursor position.

    Returns a dict with keys 'x' and 'y' representing the cursor location
    in macOS global display point coordinates.
    """
    ...

def move_mouse(x: int, y: int) -> None:
    """Move the mouse cursor to an absolute position.

    Uses CGWarpMouseCursorPosition, which teleports the cursor without
    generating mouse-move delta events. This means applications that rely
    on relative mouse deltas (e.g. FPS games with pointer lock) will NOT
    respond to this call. Use move_mouse_relative for those scenarios.

    Coordinates are in macOS global display point coordinates
    (same coordinate space as get_mouse_position and list_displays).

    Args:
        x: X coordinate in points.
        y: Y coordinate in points.

    Requires Accessibility permissions on macOS.
    """
    ...

def move_mouse_relative(dx: int, dy: int) -> None:
    """Move the mouse cursor by a relative offset, generating delta events.

    Posts a kCGEventMouseMoved event with explicit deltaX/deltaY fields.
    This works with applications that read raw mouse deltas (e.g. games
    with pointer lock like FPS).

    Args:
        dx: Horizontal offset in points (positive = right).
        dy: Vertical offset in points (positive = down).

    Requires Accessibility permissions on macOS.
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

def mouse_scroll(
    direction: Literal["up", "down", "left", "right"], distance: int
) -> None:
    """Scroll the mouse wheel in the given direction.

    The direction describes which way the **content** moves, matching macOS
    natural scrolling. On Windows the implementation will invert internally
    so callers always use the same convention.

    Uses CGEventCreateScrollWheelEvent with kCGScrollEventUnitLine.

    Args:
        direction: "up", "down", "left", or "right".
        distance: Number of lines to scroll (positive).

    Raises:
        ValueError: If direction is not one of the four valid values.

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

def start_capture(display_id: int) -> CaptureHandle:
    """Start capturing a display using ScreenCaptureKit.

    Creates an SCStream targeting the specified display and begins receiving
    frames at 30 FPS in BGRA format. Returns an opaque handle used by
    stop_capture, current_frame_jpg, and current_frame_bgra.

    Args:
        display_id: The CGDirectDisplayID (from list_displays()["id"]).

    Raises:
        OSError: If SCShareableContent lookup or stream start fails.
        ValueError: If the display_id is not found.

    Requires Screen Recording permission on macOS.
    """
    ...

def stop_capture(handle: CaptureHandle) -> None:
    """Stop an active screen capture and release resources.

    Args:
        handle: The handle returned by start_capture.

    Raises:
        OSError: If stopping the stream fails.
    """
    ...

def current_frame_jpg(handle: CaptureHandle, quality: int = 80) -> bytes | None:
    """Get the latest captured frame as JPEG-encoded bytes.

    Returns None if no frame has been captured yet.

    Args:
        handle: The handle returned by start_capture.
        quality: JPEG quality 0-100 (default 80).

    Raises:
        OSError: If JPEG encoding fails.
    """
    ...

def current_frame_bgra(handle: CaptureHandle) -> BGRAPack | None:
    """Get the latest captured frame as raw BGRA pixel data.

    Returns None if no frame has been captured yet. Otherwise returns a dict:
        - "data": bytes — raw BGRA pixel buffer
        - "width": int — frame width in pixels
        - "height": int — frame height in pixels
        - "bytes_per_row": int — stride (may include padding)
    """
    ...
