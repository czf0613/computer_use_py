def list_displays() -> list[dict]:
    """List all active displays with their coordinates, dimensions, and scale factor.

    Returns a list of display info dicts. Coordinates are in the macOS global
    display coordinate space (points), which is the same coordinate space used
    by CGEvent for mouse/keyboard actions. The scale_factor field gives the
    Retina scaling ratio (physical pixels = points * scale_factor).

    Raises:
        OSError: If CGGetActiveDisplayList fails.
    """
    ...
