from typing import TypedDict, final
from abc import ABC
from ..recording import RecordingHandle

__all__ = [
    "DisplayInfo",
    "Point2D",
    "Vector2D",
    "CaptureHandle",
    "RecordingHandle",
    "BGRAPack",
]


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
    """Opaque native PyCapsule returned by start_capture, not constructed in Python.

    Dropping the last reference automatically stops capture and releases native
    resources. Use stop_capture for explicit shutdown: it is idempotent, rejects
    subsequent frames, and makes later frame reads return None. Reads already in
    progress may still return a retained frame. Concurrent native calls preserve
    memory safety, including on free-threaded CPython.
    """

    pass


@final
class BGRAPack(TypedDict):
    """Copied BGRA pixels, independent of capture lifetime.

    Dimensions are in pixels. bytes_per_row is the stride, including any padding;
    data contains bytes_per_row * height bytes.
    """

    data: bytes
    width: int
    height: int
    bytes_per_row: int
