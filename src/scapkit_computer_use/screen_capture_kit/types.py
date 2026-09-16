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


class _DisplayMetadata(TypedDict, total=False):
    coordinate_unit: str
    ui_scale_factor: float
    pixel_width: int
    pixel_height: int


@final
class DisplayInfo(_DisplayMetadata):
    """Information about a display."""

    id: int
    """macOS CGDirectDisplayID or a process-local Windows display ID."""
    x: int
    """Global X origin: macOS points or Windows physical pixels."""
    y: int
    """Global Y origin: macOS points or Windows physical pixels."""
    width: int
    """Width in the platform's input coordinate units."""
    height: int
    """Height in the platform's input coordinate units."""
    scale_factor: float
    """Physical pixels per input unit; Windows uses 1.0, independently of UI DPI."""
    is_main: bool
    """Whether this is the main display."""


@final
class Point2D(TypedDict):
    """Global mouse position: macOS logical points or Windows physical pixels."""

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

    Dimensions are image pixels on both platforms, not macOS logical points.
    bytes_per_row is the stride, including any padding;
    data contains bytes_per_row * height bytes.
    """

    data: bytes
    width: int
    height: int
    bytes_per_row: int
