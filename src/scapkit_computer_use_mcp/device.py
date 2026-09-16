"""Serialize access to one physical desktop and own each capture's lifetime."""

import asyncio
import inspect
import platform
from pathlib import Path
from uuid import uuid4

import anyio

import scapkit_computer_use

from .system import coordinate_unit, system_info


async def finish(task):
    """Finish releasing resources despite asyncio or AnyIO cancellation."""
    with anyio.CancelScope(shield=True):
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        return task.result()


def jpeg_size(data: bytes) -> tuple[int, int]:
    """Read native JPEG dimensions without adding an image-processing dependency."""
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("Capture returned invalid JPEG data")
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            break
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        length = int.from_bytes(data[offset : offset + 2], "big")
        if length < 2 or offset + length > len(data):
            break
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            if length < 8:
                break
            height = int.from_bytes(data[offset + 3 : offset + 5], "big")
            width = int.from_bytes(data[offset + 5 : offset + 7], "big")
            if width and height:
                return width, height
            break
        offset += length
    raise ValueError("Capture JPEG has no valid dimensions")


class Device:
    """One device per server/event loop; a backend can be supplied for embedding."""

    def __init__(self, *, computer=None, system: str | None = None):
        self.computer = scapkit_computer_use if computer is None else computer
        self.system = platform.system() if system is None else system
        self.lock = asyncio.Lock()
        self._recording_handle = None
        self._recording_info = {"state": "idle"}
        self._closed = False

    async def start_recording(
        self, output_path, display_id=None, fps=30, video_quality=0.75
    ):
        async with self.lock:
            self.require_desktop("ScreenCapture")
            if self._closed:
                raise ValueError(
                    "This recording service has shut down; reconnect to a running server."
                )
            if self._recording_handle is not None:
                raise ValueError(
                    "A recording is already active. Call recording_status, then stop_recording first."
                )
            displays = self.computer.list_displays()
            display = (
                next((d for d in displays if d["id"] == display_id), None)
                if display_id is not None
                else next((d for d in displays if d["is_main"]), None)
            )
            if display is None:
                raise ValueError(
                    "Display not found. Call list_displays to get a current display_id."
                )
            info = {
                "state": "recording",
                "recording_id": uuid4().hex,
                "display_id": display["id"],
                "path": str(Path(output_path).absolute()),
                "fps": fps,
                "video_quality": video_quality,
            }

            async def acquire():
                handle = await self.computer.start_recording(
                    display["id"], info["path"], fps=fps, video_quality=video_quality
                )
                self._recording_handle = handle
                self._recording_info = info

            startup = asyncio.create_task(acquire())
            try:
                await asyncio.shield(startup)
            except asyncio.CancelledError as cancellation:
                # A cancelled request may already have acquired a native handle.
                # Keep the device locked until startup and finalization complete.
                try:
                    await finish(startup)
                    await finish(asyncio.create_task(self._complete_recording()))
                except Exception as error:
                    raise cancellation from error
                raise
            return self._recording_info.copy()

    async def _complete_recording(self):
        """Called only with the device lock held; consume the current handle once."""
        try:
            result = await self.computer.stop_recording(self._recording_handle)
            self._recording_info = {
                **self._recording_info,
                "state": "completed",
                "result": {
                    "path": str(result.path),
                    "size_bytes": result.size_bytes,
                    "duration_s": result.duration_s,
                    "width": result.width,
                    "height": result.height,
                    "fps": result.fps,
                },
            }
            for field in ("frames_written", "frames_dropped"):
                value = getattr(result, field, None)
                if value is not None:
                    self._recording_info["result"][field] = value
        except Exception as error:
            self._recording_info = {
                **self._recording_info,
                "state": "failed",
                "error": str(error),
            }
            raise
        finally:
            self._recording_handle = None

    async def stop_recording(self, recording_id):
        async with self.lock:
            self.require_desktop()
            if recording_id != self._recording_info.get("recording_id"):
                raise ValueError(
                    "Unknown or expired recording_id. Call recording_status for the current recording."
                )
            if self._recording_handle is not None:
                task = asyncio.create_task(self._complete_recording())
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError as cancellation:
                    try:
                        await finish(task)
                    except Exception as error:
                        raise cancellation from error
                    raise
            if self._recording_info["state"] == "failed":
                raise OSError(self._recording_info["error"])
            return self._recording_info.copy()

    async def recording_status(self):
        async with self.lock:
            self.require_desktop()
            return self._recording_info.copy()

    async def aclose(self):
        """Finalize an active recording when the server lifespan ends."""

        async def close():
            async with self.lock:
                self._closed = True
                if self._recording_handle is not None:
                    await self._complete_recording()

        await finish(asyncio.create_task(close()))

    def require_desktop(self, permission: str | None = None) -> None:
        if self.system not in {"Darwin", "Windows"}:
            raise NotImplementedError(
                "Desktop control requires macOS or Windows; subprocess tools remain available."
            )
        if permission and self.system == "Darwin" and not self.computer.check_permission(permission):
            raise PermissionError(
                f"Missing {permission} permission. Ask the user to enable it for the server's host application "
                "in macOS System Settings > Privacy & Security, then retry. No permission was requested automatically."
            )

    async def system_info(self) -> dict:
        return await asyncio.to_thread(system_info, self.system)

    async def info(self) -> dict:
        async with self.lock:
            permissions = None
            if self.system == "Darwin":
                permissions = {
                    key: self.computer.check_permission(key)
                    for key in ("ScreenCapture", "Accessibility")
                }
            return {
                "platform": self.system,
                "desktop_supported": self.system in {"Darwin", "Windows"},
                "permissions": permissions,
                "coordinate_unit": self.coordinate_unit,
                "command_environment": "fresh system shell environment plus explicit overrides",
            }

    @property
    def coordinate_unit(self):
        return coordinate_unit(self.system)

    async def invoke(self, name, *args, permission=None, **kwargs):
        async with self.lock:
            self.require_desktop(permission)
            function = getattr(self.computer, name)
            if name in {"get_clipboard", "set_clipboard"}:
                return await self._clipboard(function, *args, **kwargs)
            if inspect.iscoroutinefunction(function):
                return await function(*args, **kwargs)
            return await asyncio.to_thread(function, *args, **kwargs)

    async def _clipboard(self, function, *args, **kwargs):
        # pbcopy/pbpaste outlive cancellation of their Python awaiter. Keep the
        # device lock until the operation finishes, then propagate cancellation.
        task = asyncio.create_task(function(*args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await finish(asyncio.gather(task, return_exceptions=True))
            raise

    def point(self, x: int, y: int) -> dict:
        displays = self.computer.list_displays()
        if not any(
            d["x"] <= x < d["x"] + d["width"] and d["y"] <= y < d["y"] + d["height"]
            for d in displays
        ):
            raise ValueError(
                "Point is outside the current displays. Call list_displays and convert screenshot pixels to global points."
            )
        return {"x": x, "y": y}

    async def move(self, x, y):
        async with self.lock:
            self.require_desktop("Accessibility")
            await self.computer.move_mouse(self.point(x, y), smooth=False)

    async def click(self, x, y, button="left", hold_seconds=0):
        async with self.lock:
            self.require_desktop("Accessibility")
            await self.computer.move_mouse(self.point(x, y), smooth=False)
            if hold_seconds:
                await self.computer.mouse_long_click(button, hold_seconds)
            else:
                await self.computer.mouse_click(button)

    async def drag(self, from_x, from_y, to_x, to_y):
        async with self.lock:
            self.require_desktop("Accessibility")
            start, end = self.point(from_x, from_y), self.point(to_x, to_y)
            await self.computer.move_mouse(start, smooth=False)
            await self.computer.mouse_drag(end)

    async def type_text(self, text):
        async with self.lock:
            self.require_desktop("Accessibility")
            await self._clipboard(self.computer.set_clipboard, text)
            await self.computer.clipboard_paste()

    async def screenshot(self, display_id=None, quality=80, timeout_seconds=5):
        async with self.lock:
            self.require_desktop("ScreenCapture")
            displays = self.computer.list_displays()
            display = (
                next((d for d in displays if d["id"] == display_id), None)
                if display_id is not None
                else next((d for d in displays if d["is_main"]), None)
            )
            if display is None:
                raise ValueError(
                    "Display not found. Call list_displays to get a current display_id."
                )
            # Wait for an in-flight native startup on cancellation so the next
            # tool cannot overlap with an abandoned capture startup.
            startup = asyncio.create_task(self.computer.start_capture(display["id"]))
            handle = None
            try:
                handle = await asyncio.shield(startup)

                async def frame():
                    while True:
                        image = await self.computer.current_frame_jpg(handle, quality)
                        if image is not None:
                            return image
                        await asyncio.sleep(0.05)

                try:
                    data = await asyncio.wait_for(frame(), timeout_seconds)
                except asyncio.TimeoutError:
                    raise TimeoutError(
                        "No screen frame arrived before the timeout. Check ScreenCapture permission and whether the display is available."
                    ) from None
                width, height = jpeg_size(data)
                return data, {
                    "display": display,
                    "image_width": width,
                    "image_height": height,
                    "points_per_pixel_x": display["width"] / width,
                    "points_per_pixel_y": display["height"] / height,
                    "coordinate_unit": self.coordinate_unit,
                }
            finally:
                if handle is None:
                    try:
                        handle = await finish(startup)
                    except Exception:  # noqa: BLE001
                        # Startup failed without producing a handle to release.
                        handle = None
                if handle is not None:
                    await finish(
                        asyncio.create_task(self.computer.stop_capture(handle))
                    )
