from typing import TypedDict, final
from abc import ABC

__all__ = ["DisplayInfo", "Point2D", "Vector2D", "CaptureHandle", "BGRAPack"]


@final
class DisplayInfo(TypedDict):
    """Information about a display."""

    id: int
    """CGDirectDisplayID."""
    x: int
    """X origin in macOS global point coordinates."""
    y: int
    """Y origin in macOS global point coordinates."""
    width: int
    """Width in points (logical, not physical pixels)."""
    height: int
    """Height in points (logical, not physical pixels)."""
    scale_factor: float
    """Retina scaling factor (e.g. 2.0 for HiDPI). Physical pixels = points * scale_factor."""
    is_main: bool
    """Whether this is the main display."""


@final
class Point2D(TypedDict):
    x: int
    y: int


@final
class Vector2D(TypedDict):
    dx: int
    dy: int


@final
class CaptureHandle(ABC):
    """Opaque handle returned by start_capture (PyCapsule)."""

    pass


@final
class BGRAPack(TypedDict):
    data: bytes
    width: int
    height: int
    bytes_per_row: int
