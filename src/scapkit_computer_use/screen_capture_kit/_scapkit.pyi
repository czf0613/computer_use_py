"""Native bindings, memory-safe under concurrent and free-threaded use.

Input event sequences are not transactions. Callers must serialize compound
mouse and keyboard operations to prevent interleaving.
Windows coordinates are virtual-desktop physical pixels; macOS uses points.
Windows key codes are VK values and flags come from the platform keys module.
"""

from typing import Literal, TypedDict
from .types import CaptureHandle, RecordingHandle, BGRAPack, Point2D, DisplayInfo

# Window ABI 1 is independent of the legacy desktop API. Until all seven
# entries are implemented, omit WINDOW_BACKEND_ABI and report unavailable.
# Full threading, ownership, validation and failure rules:
# docs/window-backend-contract.md. All arguments below are positional-only.
WINDOW_BACKEND_ABI: Literal[1]

class NativeWindowRect(TypedDict):
    x: float
    y: float
    width: float
    height: float

class NativeWindowIdentity(TypedDict):
    generation: int
    process_instance: str

class NativeWindowInfo(NativeWindowIdentity):
    ref: object  # PyCapsule: scapkit.window_ref.v1 / scapkit.accessible_window_ref.v1
    platform: Literal["macos", "windows"]
    native_id: str  # Diagnostic only; never accepted as a selector.
    pid: int
    title: str | None
    bounds: NativeWindowRect
    coordinate_unit: Literal["points", "pixels"]
    visible: bool | None
    minimized: bool | None
    capture_support: Literal["supported", "unsupported", "unknown"]
    inspect_support: Literal["supported", "unsupported", "unknown"]

class NativeWindowSession(NativeWindowIdentity):
    handle: object  # PyCapsule: scapkit.window_session.v1

class NativeWindowSubject(TypedDict):
    platform: Literal["macos", "windows"]
    application_id: str
    application_version: str
    application_build: str
    environment: str
    backend_build: str
    window_identifier: str

class NativeWindowProfileAction(TypedDict):
    identifier: str
    role: str
    action: Literal["set_value", "invoke"]

class NativeWindowProfile(NativeWindowSubject):
    profile_id: str
    actions: list[NativeWindowProfileAction]

class NativeWindowAuthorization(TypedDict):
    profile: NativeWindowProfile
    evidence_sha256: str

class NativeWindowContext(NativeWindowIdentity):
    geometry_revision: int
    identity_status: Literal["verified", "unverified", "stale"]
    capture: Literal["supported", "unsupported", "unknown"]
    inspect_elements: Literal["supported", "unsupported", "unknown"]
    set_value: Literal["supported", "unsupported", "unknown"]
    invoke: Literal["supported", "unsupported", "unknown"]
    minimized: bool | None
    modal: bool | None
    focus_policy: Literal["no_activation", "unverified"]
    subject: NativeWindowSubject | None

class NativeWindowElement(TypedDict):
    ref: object  # PyCapsule: scapkit.window_element.v1
    identifier: str | None
    role: str
    name: str | None
    bounds: NativeWindowRect | None
    enabled: bool | None
    editable: bool | None
    password: bool | None
    actions: list[Literal["set_value", "invoke"]]
    depth: int
    parent_index: int | None

class NativeWindowSnapshot(NativeWindowIdentity):
    geometry_revision: int
    tree_epoch: int
    coordinate_unit: Literal["points", "pixels"]
    mapping_status: Literal["verified", "unavailable"]
    elements: list[NativeWindowElement]

class NativeWindowFrame(NativeWindowIdentity):
    frame_id: str
    geometry_revision: int
    captured_at: float
    state: Literal["complete"]
    format: Literal["jpeg", "bgra"]
    data: bytes
    image_width: int
    image_height: int
    stride: int | None
    window_bounds: NativeWindowRect
    local_rect: NativeWindowRect | None
    image_content_rect_px: NativeWindowRect | None
    client_rect_local: NativeWindowRect | None
    coordinate_unit: Literal["points", "pixels"]
    image_to_local: tuple[float, float, float, float, float, float] | None
    mapping_status: Literal["verified", "unavailable"]

class NativeWindowActionReceipt(TypedDict):
    operation_id: str
    dispatch_state: Literal["not_dispatched", "accepted", "unknown"]
    effect_state: Literal["verified", "unknown"]

class NativeWindowError(TypedDict):
    code: str  # Exactly a code from window_errors.ERROR_CODES.
    native_domain: str | None
    native_code: int | str | None
    dispatch_state: Literal["not_dispatched", "accepted", "unknown"]
    retry_hint: Literal["never", "refresh", "after_permission", "after_close"]

class NativeWindowFailure(TypedDict):
    ok: Literal[False]
    error: NativeWindowError

class NativeWindowSuccess(TypedDict):
    ok: Literal[True]
    value: (None | list[NativeWindowInfo] | NativeWindowSession | NativeWindowContext
            | NativeWindowSnapshot | NativeWindowFrame | NativeWindowActionReceipt)

def _window_enumerate(
    kind: Literal["capture", "accessible"], pid: int | None, limit: int, /,
) -> NativeWindowSuccess | NativeWindowFailure:
    """Return list[NativeWindowInfo]. Retain original native objects, never raw IDs."""
    ...

def _window_open(ref: object, kind: Literal["capture", "accessible"], /) -> NativeWindowSuccess | NativeWindowFailure:
    """Return NativeWindowSession; consumes a reference once, verifies its incarnation."""
    ...

def _window_close(handle: object, /) -> NativeWindowSuccess | NativeWindowFailure:
    """Return None after cleanup. Idempotent; incomplete cleanup is a structured failure."""
    ...

def _window_attach(handle: object, accessible_ref: object, /) -> NativeWindowSuccess | NativeWindowFailure:
    """Return None after an atomic, verified root attachment. Failure leaves it unbound."""
    ...

def _window_screenshot(
    handle: object, format: Literal["jpeg", "bgra"], quality: int, timeout_seconds: float, /,
) -> NativeWindowSuccess | NativeWindowFailure:
    """Return NativeWindowFrame. Own and close this call's capture stream on every path."""
    ...

def _window_inspect(
    handle: object, query: Literal["capabilities", "elements", "validate_snapshot"],
    options: dict[str, int], /,
) -> NativeWindowSuccess | NativeWindowFailure:
    """Return NativeWindowContext / NativeWindowSnapshot / None, respectively."""
    ...

def _window_action(
    handle: object, element: object, tree_epoch: int, geometry_revision: int,
    action: Literal["set_value", "invoke"], value: str | None,
    authorization: NativeWindowAuthorization, operation_id: str, timeout_seconds: float, /,
) -> NativeWindowSuccess | NativeWindowFailure:
    """Return NativeWindowActionReceipt. Validate the registry grant before native dispatch.

    Call window_profiles._validate_authorization on this Python entry thread,
    with attached thread state and no native locks. Copy its returned scope to
    native-only storage, then recheck live identity, exact scope, control and
    preconditions on the owning actor. No callback or actor may access Python.
    """
    ...

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
