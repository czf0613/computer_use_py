"""Public async API over the real native writer, with only acquisition replaced."""

import asyncio
from dataclasses import FrozenInstanceError
from pathlib import Path
import threading

import pytest

from scapkit_computer_use import start_recording, stop_recording, RecordingResult
import scapkit_computer_use.recording as api
from scapkit_computer_use.screen_capture_kit import _scapkit as native


class SyntheticDevice:
    @staticmethod
    def start_recording(display_id, output_path, fps, video_quality=0.75):
        # display selection/SCStream are the only replaced boundary. Native
        # timer, retained frames, hardware encoding and MP4 writing remain real.
        return native._test_recording(output_path, fps, "realtime", video_quality)

    stop_recording = staticmethod(native.stop_recording)
    _abort_recording = staticmethod(native._abort_recording)


@pytest.mark.asyncio
@pytest.mark.parametrize('video_quality', [None, 0.0, 0.5, 0.75, 1.0])
async def test_public_api_returns_completed_native_file_and_idempotent_result(tmp_path, monkeypatch, video_quality):
    monkeypatch.setattr(api, "_load_native", lambda: SyntheticDevice)
    path = tmp_path / "public.mp4"
    handle = await start_recording(1, path, fps=20, video_quality=video_quality)
    try:
        await asyncio.sleep(0.08)
    finally:
        results = await asyncio.gather(stop_recording(handle), stop_recording(handle))
    assert results[0] == results[1]
    assert isinstance(results[0], RecordingResult)
    assert results[0].path == Path(path).absolute()
    assert results[0].size_bytes == path.stat().st_size > 0
    assert results[0].duration_s >= 0.08
    assert results[0].fps == 20
    with pytest.raises(FrozenInstanceError):
        results[0].fps = 60


@pytest.mark.asyncio
async def test_cancelled_public_start_aborts_late_real_native_handle(tmp_path, monkeypatch):
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()

    class DelayedDevice(SyntheticDevice):
        @staticmethod
        def start_recording(display_id, output_path, fps, video_quality=0.75):
            handle = native._test_recording(output_path, fps, "normal")
            native._test_recording_tick(handle, 100_000_000)
            loop.call_soon_threadsafe(started.set)
            if not release.wait(3):
                native._abort_recording(handle)
                raise TimeoutError("test startup barrier was not released")
            return handle

    monkeypatch.setattr(api, "_load_native", lambda: DelayedDevice)
    task = asyncio.create_task(start_recording(1, tmp_path / "cancelled.mp4"))
    try:
        await asyncio.wait_for(started.wait(), 3)
        task.cancel()
        await asyncio.sleep(0)
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert list(tmp_path.iterdir()) == []
