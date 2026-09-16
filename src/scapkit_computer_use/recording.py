"""Asynchronous display and system-audio recording API."""

import asyncio
from abc import ABC
from dataclasses import dataclass
from importlib import import_module
import os
import math
from pathlib import Path
from typing import Any, final


__all__ = [
    "RecordingHandle",
    "RecordingResult",
    "start_recording",
    "stop_recording",
]

_UINT32_MAX = 2**32 - 1
_INT32_MAX = 2**31 - 1


@final
class RecordingHandle(ABC):
    """Opaque native recording handle returned by :func:`start_recording`.

    A recording handle is distinct from a screen-capture handle. Call
    :func:`stop_recording` explicitly to finish and publish the MP4 file;
    dropping the handle only performs best-effort resource cleanup.
    """

    pass


@final
@dataclass(frozen=True)
class RecordingResult:
    """Metadata for a successfully finalized recording.

    width/height are output image pixels on both platforms, with right/bottom
    padding to even dimensions when needed; they are not mouse input units.
    """

    path: Path
    size_bytes: int
    duration_s: float
    width: int
    height: int
    fps: int
    frames_written: int | None = None
    frames_dropped: int | None = None


def _load_native() -> Any:
    """Resolve the platform extension only when an operation is called."""
    return import_module(".screen_capture_kit._scapkit", __package__)


def _validate_positive_integer(name: str, value: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")


def _normalize_output_path(output_path: str | os.PathLike[str]) -> Path:
    raw_path = os.fspath(output_path)
    if not isinstance(raw_path, str):
        raise TypeError("output_path must be a string or string-valued PathLike")
    if not raw_path:
        raise ValueError("output_path must not be empty")
    if "\0" in raw_path:
        raise ValueError("output_path must not contain NUL characters")

    path = Path(raw_path).absolute()
    if not path.parent.is_dir():
        raise FileNotFoundError(f"output directory does not exist: {path.parent}")
    if os.path.lexists(path):
        raise FileExistsError(f"output path already exists: {path}")
    return path


async def _wait_through_cancellation(future: asyncio.Future[Any]) -> Any:
    """Wait for an executor future while consuming additional cancellations."""
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            if future.cancelled():
                raise
            continue
    return future.result()


def _result_from_native(data: Any) -> RecordingResult:
    return RecordingResult(
        path=Path(data["path"]).absolute(),
        size_bytes=data["size_bytes"],
        duration_s=data["duration_s"],
        width=data["width"],
        height=data["height"],
        fps=data["fps"],
        frames_written=data.get("frames_written"),
        frames_dropped=data.get("frames_dropped"),
    )


async def start_recording(
    display_id: int,
    output_path: str | os.PathLike[str],
    fps: int = 30,
    *,
    video_quality: float | None = 0.75,
) -> RecordingHandle:
    """Start recording one display and its system audio to an MP4 file.

    Startup waits for the native stream, encoder, writer, and initial
    complete video frame before returning. If startup is cancelled, this call
    waits for the native worker and aborts any late handle before propagating
    cancellation.

    Args:
        display_id: A display ID returned by ``list_displays()``.
        output_path: New MP4 path. Its parent must exist and the target must not.
        fps: Target frame rate. Windows drops frames under encoder overload while
            retaining elapsed media time; macOS retains its fixed-rate policy.
        video_quality: Compression quality from 0.0 to 1.0, default 0.75. Higher
            values preserve more detail. macOS uses VideoToolbox quality;
            Windows maps this to a resolution/frame-rate-dependent bitrate.
            None uses the platform's default policy.

    Returns:
        An opaque handle that must be passed to :func:`stop_recording`.
    """
    _validate_positive_integer("display_id", display_id, _UINT32_MAX)
    _validate_positive_integer("fps", fps, _INT32_MAX)
    if video_quality is not None:
        if isinstance(video_quality, bool) or not isinstance(video_quality, (int, float)):
            raise TypeError("video_quality must be a number")
        if not 0 <= video_quality <= 1 or not math.isfinite(video_quality):
            raise ValueError("video_quality must be finite and between 0.0 and 1.0")
    path = _normalize_output_path(output_path)

    native = _load_native()
    loop = asyncio.get_running_loop()
    arguments = (display_id, str(path), fps, video_quality)
    worker = loop.run_in_executor(None, native.start_recording, *arguments)
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError as cancellation:
        try:
            handle = await _wait_through_cancellation(worker)
        except BaseException as cleanup_error:
            raise cancellation from cleanup_error

        try:
            cleanup = loop.run_in_executor(None, native._abort_recording, handle)
        except Exception as dispatch_error:
            try:
                native._abort_recording(handle)
            except BaseException as cleanup_error:
                raise cancellation from cleanup_error
            raise cancellation from dispatch_error

        try:
            await _wait_through_cancellation(cleanup)
        except asyncio.CancelledError as cleanup_cancellation:
            if not cleanup.cancelled():
                raise cancellation from cleanup_cancellation
            try:
                native._abort_recording(handle)
            except BaseException as cleanup_error:
                raise cancellation from cleanup_error
            raise cancellation
        except BaseException as cleanup_error:
            raise cancellation from cleanup_error
        raise cancellation


async def stop_recording(handle: RecordingHandle) -> RecordingResult:
    """Finish a recording, publish its MP4 file, and return immutable metadata.

    Explicit stop is required to finalize and publish a successful recording.
    If this coroutine is cancelled, it still waits through repeated cancellation
    until native finalization and cleanup complete, then propagates cancellation.
    Native stop is concurrent-safe and idempotent for the same handle.

    Args:
        handle: The opaque handle returned by :func:`start_recording`.
    """
    native = _load_native()
    loop = asyncio.get_running_loop()
    try:
        worker = loop.run_in_executor(None, native.stop_recording, handle)
    except Exception:
        data = native.stop_recording(handle)
    else:
        try:
            data = await asyncio.shield(worker)
        except asyncio.CancelledError as cancellation:
            if worker.cancelled():
                try:
                    native.stop_recording(handle)
                except BaseException as cleanup_error:
                    raise cancellation from cleanup_error
                raise cancellation
            try:
                await _wait_through_cancellation(worker)
            except asyncio.CancelledError as worker_cancellation:
                if not worker.cancelled():
                    raise cancellation from worker_cancellation
                try:
                    native.stop_recording(handle)
                except BaseException as cleanup_error:
                    raise cancellation from cleanup_error
                raise cancellation
            except BaseException as cleanup_error:
                raise cancellation from cleanup_error
            raise cancellation

    return _result_from_native(data)
