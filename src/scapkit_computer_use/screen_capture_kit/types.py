from typing import TypedDict, final
from abc import ABC

__all__ = ["DisplayInfo", "Point2D"]


@final
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


@final
class Point2D(TypedDict):
    x: int
    y: int


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
