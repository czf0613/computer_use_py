from ._scapkit import (
    get_mouse_position,
    move_mouse as move_mouse_c,
    move_mouse_relative as move_mouse_relative_c,
    mouse_click as mouse_click_action,
    mouse_scroll as mouse_scroll_c,
    keyboard_click as keyboard_click_c,
    start_capture as start_capture_c,
    stop_capture as stop_capture_c,
    current_frame_jpg as current_frame_jpg_c,
    current_frame_bgra as current_frame_bgra_c,
)
from typing import Literal
from .types import Point2D, Vector2D, CaptureHandle, BGRAPack
from .keys import KEY_CODES, MODIFIER_FLAGS, MODIFIER
import asyncio
from asyncio import subprocess


def _resolve_key(key: str) -> int:
    try:
        return KEY_CODES[key]
    except KeyError:
        raise ValueError(
            f"unknown key name '{key}'. See screen_capture_kit.keys.KEY_CODES for valid names."
        )


def _resolve_flags(modifiers: set[MODIFIER] | None) -> int:
    flags = 0
    for mod in modifiers or []:
        flags |= MODIFIER_FLAGS[mod]
    return flags


async def move_mouse(
    dest: Point2D, smooth: bool = True, duration_s: float = 0.5
) -> None:
    """Move the mouse cursor to an absolute position.

    Uses CGWarpMouseCursorPosition which teleports the cursor without
    generating mouse-move delta events. Applications that rely on raw
    mouse deltas (e.g. games with pointer lock) will NOT respond — use
    move_mouse_relative for those scenarios.

    Args:
        dest: Target position {"x": int, "y": int} in point coordinates.
        smooth: If True (default), interpolate with ease-in-out over duration_s.
                If False, teleport instantly.
        duration_s: Duration of the smooth movement in seconds (default 0.5).
    """
    if not smooth:
        move_mouse_c(dest["x"], dest["y"])
        return

    start = get_mouse_position()
    dx = dest["x"] - start["x"]
    dy = dest["y"] - start["y"]
    distance = (dx**2 + dy**2) ** 0.5

    steps = max(int(distance / 15), 5)
    interval = duration_s / steps

    for i in range(1, steps + 1):
        t = i / steps
        ease = t * t * (3 - 2 * t)
        x = int(start["x"] + dx * ease)
        y = int(start["y"] + dy * ease)
        move_mouse_c(x, y)
        await asyncio.sleep(interval)


async def move_mouse_relative(
    vector: Vector2D, smooth: bool = True, duration_s: float = 0.5
) -> None:
    """Move the mouse by a relative offset, generating delta events.

    Unlike move_mouse which uses CGWarpMouseCursorPosition, this posts
    kCGEventMouseMoved events with deltaX/deltaY fields, making it
    compatible with applications that read raw mouse deltas (e.g. games
    with pointer lock).

    Args:
        vector: Relative offset {"dx": int, "dy": int} in points.
        smooth: If True (default), interpolate the movement over duration_s
                with ease-in-out. If False, post a single event immediately.
        duration_s: Duration of the smooth movement in seconds (default 0.5).
    """
    if not smooth:
        move_mouse_relative_c(vector["dx"], vector["dy"])
        return

    total_dx = vector["dx"]
    total_dy = vector["dy"]
    distance = (total_dx**2 + total_dy**2) ** 0.5
    steps = max(int(distance / 15), 5)
    interval = duration_s / steps

    prev_ease = 0.0
    for i in range(1, steps + 1):
        t = i / steps
        ease = t * t * (3 - 2 * t)
        step_dx = int(total_dx * ease) - int(total_dx * prev_ease)
        step_dy = int(total_dy * ease) - int(total_dy * prev_ease)
        if step_dx != 0 or step_dy != 0:
            move_mouse_relative_c(step_dx, step_dy)
        prev_ease = ease
        await asyncio.sleep(interval)


async def mouse_long_click(key: Literal["left", "right"], duration_s: float) -> None:
    """Press and hold a mouse button for a specified duration, then release.

    Args:
        key: "left" or "right" mouse button.
        duration_s: How long to hold the button in seconds (must be > 0).
    """
    assert duration_s > 0

    mouse_click_action(key, "down")
    await asyncio.sleep(duration_s)
    mouse_click_action(key, "up")


async def mouse_click(key: Literal["left", "right"]) -> None:
    """Click a mouse button (press and release with a short delay).

    Args:
        key: "left" or "right" mouse button.
    """
    await mouse_long_click(key, 0.03)


async def mouse_drag(dest: Point2D) -> None:
    """Drag from the current position to a destination (left-button hold + smooth move + release).

    Args:
        dest: Target position {"x": int, "y": int} in point coordinates.
    """
    mouse_click_action("left", "down")
    await asyncio.sleep(0.05)
    await move_mouse(dest)
    await asyncio.sleep(0.05)
    mouse_click_action("left", "up")


async def mouse_scroll(
    direction: Literal["up", "down", "left", "right"], distance: int = 3
) -> None:
    """Scroll the mouse wheel in the given direction.

    Direction describes which way the content moves, matching macOS natural
    scrolling convention. Scrolls one line at a time with short delays.

    Args:
        direction: "up", "down", "left", or "right".
        distance: Number of lines to scroll (default 3).
    """
    for i in range(distance):
        mouse_scroll_c(direction, 1)

        if i > 0 and i % 10 == 0:
            await asyncio.sleep(1)
        else:
            await asyncio.sleep(0.01)


def keyboard_click_action(
    key: str,
    action: Literal["down", "up"],
    modifiers: set[MODIFIER] | None = None,
) -> None:
    """Post a single keyboard key-down or key-up event.

    Args:
        key: Named key (e.g. "a", "return"). See keys.KEY_CODES for valid names.
        action: "down" or "up".
        modifiers: Optional set of modifier keys (e.g. {"command", "shift"}).
    """
    keyboard_click_c(_resolve_key(key), action, _resolve_flags(modifiers))


async def keyboard_click(
    key: str,
    modifiers: set[MODIFIER] | None = None,
) -> None:
    """Press and release a keyboard key (key-down, short delay, key-up).

    Args:
        key: Named key (e.g. "a", "return"). See keys.KEY_CODES for valid names.
        modifiers: Optional set of modifier keys (e.g. {"command", "shift"}).
    """
    flags = _resolve_flags(modifiers)
    code = _resolve_key(key)
    keyboard_click_c(code, "down", flags)
    await asyncio.sleep(0.01)
    keyboard_click_c(code, "up", flags)


async def key_combo(key: str, modifiers: set[MODIFIER]) -> None:
    """Press a key combination (convenience alias for keyboard_click with modifiers).

    Args:
        key: Named key (e.g. "c", "v", "z").
        modifiers: Set of modifier keys (e.g. {"command"}, {"command", "shift"}).
    """
    await keyboard_click(key, modifiers)


async def set_clipboard(text: str) -> None:
    """Copy text to the system clipboard via pbcopy.

    Args:
        text: The text to place on the clipboard.
    """
    proc = await subprocess.create_subprocess_exec(
        "pbcopy",
        stdin=subprocess.PIPE,
    )
    await proc.communicate(input=text.encode("utf-8"))


async def get_clipboard() -> str:
    """Read the current system clipboard text via pbpaste."""
    proc = await subprocess.create_subprocess_exec(
        "pbpaste",
        stdout=subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode("utf-8")


async def clipboard_paste() -> None:
    """Paste clipboard content into the active application (simulates Cmd+V)."""
    await keyboard_click("v", {"command"})


async def start_capture(display_id: int) -> CaptureHandle:
    """Start capturing a display via ScreenCaptureKit. Returns an opaque handle.

    Args:
        display_id: The display ID from list_displays() (CGDirectDisplayID).
    """
    return await asyncio.to_thread(start_capture_c, display_id)


async def stop_capture(handle: CaptureHandle) -> None:
    """Stop an active screen capture and release resources.

    Args:
        handle: The handle returned by start_capture.
    """
    await asyncio.to_thread(stop_capture_c, handle)


async def current_frame_jpg(handle: CaptureHandle, quality: int = 80) -> bytes | None:
    """Get the latest captured frame as JPEG bytes. Returns None if no frame yet.

    Args:
        handle: The handle returned by start_capture.
        quality: JPEG quality 0-100 (default 80).
    """
    return await asyncio.to_thread(current_frame_jpg_c, handle, quality)


async def current_frame_bgra(handle: CaptureHandle) -> BGRAPack | None:
    """Get the latest captured frame as raw BGRA pixel data. Returns None if no frame yet.

    Args:
        handle: The handle returned by start_capture.
    """
    return await asyncio.to_thread(current_frame_bgra_c, handle)
