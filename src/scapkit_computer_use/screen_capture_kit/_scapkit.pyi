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

def mouse_click() -> None:
    """Perform a left mouse click at the current cursor position.

    Synthesizes a mouse-down followed by mouse-up event at the current
    cursor location using CGEventPost.

    Requires Accessibility permissions on macOS.
    """
    ...
