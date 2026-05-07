from typing import TypedDict

__all__ = ["DisplayInfo", "Point2D"]


class DisplayInfo(TypedDict):
    """Information about a display."""

    id: int
    """CGDirectDisplayID."""
    x: float
    """X origin in macOS global point coordinates."""
    y: float
    """Y origin in macOS global point coordinates."""
    width: float
    """Width in points (logical, not physical pixels)."""
    height: float
    """Height in points (logical, not physical pixels)."""
    scale_factor: float
    """Retina scaling factor (e.g. 2.0 for HiDPI). Physical pixels = points * scale_factor."""
    is_main: bool
    """Whether this is the main display."""


class Point2D(TypedDict):
    x: int
    y: int
