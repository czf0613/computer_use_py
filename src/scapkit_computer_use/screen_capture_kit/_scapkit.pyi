"""Native bindings, memory-safe under concurrent and free-threaded use.

Input event sequences are not transactions. Callers must serialize compound
mouse and keyboard operations to prevent interleaving.
Windows coordinates are virtual-desktop physical pixels; macOS uses points.
Windows key codes are VK values and flags come from the platform keys module.
"""

from typing import Literal
from .types import CaptureHandle, RecordingHandle, BGRAPack, Point2D, DisplayInfo

def _drag_mouse(x: int, y: int) -> None:
    """Internal: post left-button drag motion to an absolute point with deltas.

    Caller owns button down/up. Unlike cursor warping, this delivers motion to
    application drag tracking, including window moves between displays.
    """
    ...

def _keyboard_begin(key_code: int, flags: int) -> object:
    """Internal: allocate a private source and all paired events, then post down.

    On macOS the opaque stroke owns its private modifier/key state. Allocation failure has no
    input effects. Posts at the session tap and preserves current HID flags;
    the raw keyboard_click API still posts at the HID tap with exact caller flags.
    Windows preallocates releases, borrows already-held modifiers, and uses
    SendInput. It does not provide macOS private-source isolation.
    """
    ...

def _keyboard_end(stroke: object) -> None:
    """Internal: release this stroke's keys/modifiers and native objects once.

    Idempotent and safe for concurrent calls on the same stroke. Dropping an
    unfinished stroke also attempts cleanup; prefer deterministic try/finally.
    Waits up to one second for its private source's keyboard event counters. Raises
    TimeoutError on missing acknowledgment; releases are posted once even when
    called again after timeout. It never clears global flags or retries input.
    The acknowledgment behavior above is macOS-specific. Windows confirms
    insertion only and retains failed releases for a subsequent cleanup attempt.
    """
    ...

def keyboard_click(
    key_code: int, action: Literal["down", "up"], flags: int = 0
) -> None:
    """Low-level: post a single keyboard event by numeric key code.

    This is the raw C binding. Prefer the Python wrapper
    `screen_capture_kit.keyboard_click_action(key, action, modifiers)`
    which accepts key names (e.g. "a", "return") and modifier lists.

    Args:
        key_code: macOS CGKeyCode or Windows virtual-key code.
        action: "down" or "up".
        flags: Bitwise OR of the platform's MODIFIER_FLAGS values (default 0).

    This raw API posts exactly one event with the supplied flags. It does not
    acquire/release a shortcut's modifiers automatically. The caller owns every
    down/up and flags transition, including modifier-key events and cancellation.
    Use the high-level keyboard_click/key_combo for a balanced shortcut.
    Windows requires requested modifiers to be already held; it does not attach
    modifier flags to a single SendInput keyboard event.

    Raises:
        ValueError: If action is not "down" or "up".

    Requires Accessibility permissions on macOS.
    """
    ...

def list_displays() -> list[DisplayInfo]:
    """List all active displays with their coordinates, dimensions, and scale factor.

    Returns a list of display info dicts with keys: id, x, y, width, height,
    scale_factor, is_main. Geometry uses global input coordinates: macOS logical
    display points or Windows virtual-desktop physical pixels, matching absolute
    mouse input. Screenshot and video dimensions instead describe image pixels.

    The scale_factor field gives the Retina scaling ratio
    (physical pixels = points * scale_factor).
    Windows uses physical pixels for all four geometry fields, scale_factor=1,
    and a separate ui_scale_factor. Its process-local IDs must not be persisted.

    Raises:
        OSError: If CGGetActiveDisplayList fails.
    """
    ...

def get_mouse_position() -> Point2D:
    """Get the current mouse cursor position.

    Returns a dict with keys 'x' and 'y' representing the cursor location
    in global input coordinates (macOS points; Windows physical pixels).
    """
    ...

def move_mouse(x: int, y: int) -> None:
    """Move the mouse cursor to an absolute position.

    On macOS uses CGWarpMouseCursorPosition, which teleports the cursor without
    generating mouse-move delta events. This means applications that rely
    on relative mouse deltas (e.g. FPS games with pointer lock) will NOT
    respond to this call. Use move_mouse_relative for those scenarios.

    Coordinates are in macOS global display point coordinates
    (same coordinate space as get_mouse_position and list_displays).
    Windows instead submits absolute SendInput motion in virtual-desktop physical
    pixels and rejects positions outside the active display rectangles.

    Args:
        x: Global X in macOS logical points or Windows physical pixels.
        y: Global Y in macOS logical points or Windows physical pixels.

    Requires Accessibility permissions on macOS.
    """
    ...

def move_mouse_relative(dx: int, dy: int) -> None:
    """Move the mouse cursor by a relative offset, generating delta events.

    On macOS posts a kCGEventMouseMoved event with explicit deltaX/deltaY fields.
    This works with applications that read raw mouse deltas (e.g. games
    with pointer lock like FPS).
    Windows submits relative SendInput motion; system pointer acceleration applies.

    Args:
        dx: Horizontal relative delta in platform input units (positive = right).
        dy: Vertical relative delta in platform input units (positive = down).

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
    natural scrolling. On Windows the implementation inverts internally
    so callers always use the same convention.

    Uses CGEventCreateScrollWheelEvent with kCGScrollEventUnitLine.

    Args:
        direction: "up", "down", "left", or "right".
        distance: Lines on macOS, wheel steps on Windows (nonnegative).

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
    Windows returns True for these macOS permission names without probing;
    actual capture/input can still fail due to policy, driver or UIPI restrictions.

    Args:
        permission_type: "ScreenCapture" or "Accessibility".

    Raises:
        ValueError: If permission_type is not a recognized value.
    """
    ...

def start_capture(display_id: int) -> CaptureHandle:
    """Start capturing a display using ScreenCaptureKit or Windows Graphics Capture.

    Creates an SCStream targeting the specified display and begins receiving
    frames at 30 FPS in BGRA format. Returns an opaque handle used by
    stop_capture, current_frame_jpg, and current_frame_bgra.

    Dropping the last reference stops capture and releases native resources,
    including when an async caller cancels and abandons a worker's result.

    Args:
        display_id: An active ID returned by list_displays().

    Raises:
        OSError: If SCShareableContent lookup or stream start fails.
        ValueError: If the display_id is not found.
        TimeoutError: If SCShareableContent lookup or stream start times out.

    Requires Screen Recording permission on macOS.
    """
    ...

def stop_capture(handle: CaptureHandle) -> None:
    """Idempotently stop capture and discard its buffered frame.

    The closed handle rejects new frames and subsequent frame reads return None.
    Reads already in progress may still finish with a retained frame. Repeated
    stops are safe. The handle stays closed even if stream stop fails or times out.

    Args:
        handle: The handle returned by start_capture.

    Raises:
        OSError: If stopping the stream fails.
        TimeoutError: If stopping the stream times out.
    """
    ...

def current_frame_jpg(handle: CaptureHandle, quality: int = 80) -> bytes | None:
    """Get the latest captured frame as JPEG-encoded bytes.

    Returns None if no frame has been captured yet or the handle is stopped.
    A read already in progress may finish with a retained frame after stop;
    the stopped handle accepts no new frames.

    Args:
        handle: The handle returned by start_capture.
        quality: JPEG quality from 0 through 100 inclusive (default 80).

    Raises:
        ValueError: If quality is outside 0..100, even when no frame is available.
        OSError: If pixel-buffer access or JPEG encoding fails.
    """
    ...

def current_frame_bgra(handle: CaptureHandle) -> BGRAPack | None:
    """Get the latest captured frame as raw BGRA pixel data.

    Returns None if no frame has been captured yet or the handle is stopped.
    A read already in progress may finish with a retained frame after stop;
    the stopped handle accepts no new frames. Otherwise returns a dict:
        - "data": bytes — raw BGRA pixel buffer
        - "width": int — frame width in pixels
        - "height": int — frame height in pixels
        - "bytes_per_row": int — stride (may include padding)

    The bytes are a copy and remain valid after capture stops.

    Raises:
        OSError: If pixel-buffer access fails.
    """
    ...

def start_recording(
    display_id: int, output_path: str, fps: int, video_quality: float | None = 0.75
) -> RecordingHandle:
    """Start a display and system-audio recording after its initial video frame.

    The returned recording capsule is distinct from CaptureHandle. The caller
    must pass it to stop_recording to finish and publish the output file.
    Quality defaults to 0.75; explicit None uses the encoder default.
    Windows maps quality to bitrate (None uses 0.75), prefers hardware Media
    Foundation encoding, and drops overdue video frames while preserving time.
    """
    ...

def stop_recording(
    handle: RecordingHandle,
) -> dict[str, str | int | float]:
    """Idempotently finish a recording and return its file metadata."""
    ...

def _abort_recording(handle: RecordingHandle) -> None:
    """Internal: cancel a recording and remove its unpublished temporary file."""
    ...


# Windows-only private helpers; public clipboard wrappers also work on macOS.
def _mouse_path(x: int, y: int, dest_x: int, dest_y: int) -> list[Point2D]:
    """Return waypoints through connected visible monitor rectangles."""
    ...

def set_clipboard(text: str) -> None:
    """Write Unicode clipboard text using a temporary native owner window."""
    ...

def get_clipboard() -> str:
    """Read Unicode clipboard text, or an empty string if unavailable."""
    ...
