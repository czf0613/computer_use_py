"""Async recording API tests using fake native calls, never the real desktop."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import importlib
import os
from pathlib import Path
import sys
import threading

import pytest


def _recording_module():
    try:
        return importlib.import_module("scapkit_computer_use.recording")
    except ModuleNotFoundError:
        pytest.fail("the recording API module has not been implemented")


class FakeNative:
    def __init__(self):
        self.calls = []
        self.handle = object()

    def start_recording(self, display_id, output_path, fps, video_quality=0.75):
        self.calls.append(("start", display_id, output_path, fps, video_quality))
        return self.handle

    def stop_recording(self, handle):
        self.calls.append(("stop", handle))
        return {
            "path": "relative/recording.mp4",
            "size_bytes": 1234,
            "duration_s": 2.5,
            "width": 1920,
            "height": 1080,
            "fps": 30,
        }

    def _abort_recording(self, handle):
        self.calls.append(("abort", handle))


def test_recording_module_exposes_public_api():
    recording = _recording_module()

    assert callable(recording.start_recording)
    assert callable(recording.stop_recording)
    assert recording.RecordingHandle is not None
    assert recording.RecordingResult is not None


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS desktop package")
def test_public_packages_export_the_recording_api():
    recording = _recording_module()
    package = importlib.import_module("scapkit_computer_use")
    desktop = importlib.import_module("scapkit_computer_use.screen_capture_kit")

    for name in (
        "RecordingHandle",
        "RecordingResult",
        "start_recording",
        "stop_recording",
    ):
        assert name in package.__all__
        assert getattr(package, name) is getattr(recording, name)
        assert getattr(desktop, name) is getattr(recording, name)


@pytest.fixture
def fake_native(monkeypatch):
    recording = _recording_module()
    native = FakeNative()
    monkeypatch.setattr(recording, "_load_native", lambda: native)
    return recording, native


class StringPath:
    def __init__(self, value):
        self.value = value

    def __fspath__(self):
        return self.value


@pytest.mark.asyncio
async def test_video_quality_is_validated_and_reaches_native(monkeypatch, tmp_path):
    recording = _recording_module()
    calls = []
    handle = object()

    class Device:
        @staticmethod
        def start_recording(*args):
            calls.append(args)
            return handle

    monkeypatch.setattr(recording, '_load_native', lambda: Device)
    output = tmp_path / 'quality.mp4'
    for quality, error in [(True, TypeError), ('0.5', TypeError), (1.5, ValueError), (-1, ValueError), (float('nan'), ValueError), (float('inf'), ValueError)]:
        with pytest.raises(error):
            await recording.start_recording(1, output, video_quality=quality)
    assert calls == []
    for quality in (None, 0, 0.5, 0.75, 1):
        assert await recording.start_recording(1, output, video_quality=quality) is handle
    assert calls == [(1, str(output), 30, quality) for quality in (None, 0, 0.5, 0.75, 1)]


@pytest.mark.asyncio
async def test_start_rejects_invalid_arguments_before_native_dispatch(
    fake_native, tmp_path
):
    recording, native = fake_native
    valid_path = tmp_path / "valid.mp4"
    existing_path = tmp_path / "existing.mp4"
    existing_path.write_bytes(b"caller data")
    dangling_path = tmp_path / "dangling.mp4"
    dangling_path.symlink_to(tmp_path / "absent.mp4")
    missing_parent_path = tmp_path / "missing" / "recording.mp4"

    cases = [
        ((True, valid_path, 30), TypeError),
        ((1.0, valid_path, 30), TypeError),
        ((0, valid_path, 30), ValueError),
        ((-1, valid_path, 30), ValueError),
        ((2**32, valid_path, 30), ValueError),
        ((1, valid_path, False), TypeError),
        ((1, valid_path, 1.0), TypeError),
        ((1, valid_path, 0), ValueError),
        ((1, valid_path, -1), ValueError),
        ((1, valid_path, 2**31), ValueError),
        ((1, b"recording.mp4", 30), TypeError),
        ((1, StringPath(b"recording.mp4"), 30), TypeError),
        ((1, "", 30), ValueError),
        ((1, StringPath(""), 30), ValueError),
        ((1, "bad\0name.mp4", 30), ValueError),
        ((1, missing_parent_path, 30), FileNotFoundError),
        ((1, existing_path, 30), FileExistsError),
        ((1, dangling_path, 30), FileExistsError),
    ]

    for args, error_type in cases:
        with pytest.raises(error_type):
            await recording.start_recording(*args)

    assert native.calls == []
    assert existing_path.read_bytes() == b"caller data"


@pytest.mark.asyncio
async def test_start_normalizes_string_pathlike_before_dispatch(fake_native, tmp_path):
    recording, native = fake_native
    target = tmp_path / "recording.mp4"

    handle = await recording.start_recording(1, StringPath(str(target)), fps=24)

    assert handle is native.handle
    assert native.calls == [("start", 1, str(target.absolute()), 24, 0.75)]


@pytest.mark.asyncio
async def test_stop_converts_native_result_to_frozen_public_value(fake_native):
    recording, native = fake_native
    handle = object()

    result = await recording.stop_recording(handle)

    assert result == recording.RecordingResult(
        path=(Path.cwd() / "relative/recording.mp4"),
        size_bytes=1234,
        duration_s=2.5,
        width=1920,
        height=1080,
        fps=30,
    )
    assert native.calls == [("stop", handle)]
    with pytest.raises(FrozenInstanceError):
        result.fps = 60


@pytest.mark.asyncio
async def test_start_cancellation_waits_for_late_handle_and_aborts(
    monkeypatch, tmp_path
):
    recording = _recording_module()
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    released = threading.Event()
    aborted = asyncio.Event()
    handle = object()

    class BlockingNative:
        def start_recording(self, display_id, output_path, fps, video_quality=0.75):
            loop.call_soon_threadsafe(started.set)
            assert released.wait(timeout=3), "test did not release startup"
            return handle

        def _abort_recording(self, received):
            assert received is handle
            loop.call_soon_threadsafe(aborted.set)

    monkeypatch.setattr(recording, "_load_native", lambda: BlockingNative())
    task = asyncio.create_task(
        recording.start_recording(1, tmp_path / "cancelled.mp4")
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    released.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    await asyncio.wait_for(aborted.wait(), timeout=1)


@pytest.mark.asyncio
async def test_start_cancellation_chains_abort_failure(monkeypatch, tmp_path):
    recording = _recording_module()
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    released = threading.Event()
    cleanup_failure = OSError("abort failed")
    handle = object()

    class FailingAbortNative:
        def start_recording(self, display_id, output_path, fps, video_quality=0.75):
            loop.call_soon_threadsafe(started.set)
            assert released.wait(timeout=3), "test did not release startup"
            return handle

        def _abort_recording(self, received):
            assert received is handle
            raise cleanup_failure

    monkeypatch.setattr(recording, "_load_native", lambda: FailingAbortNative())

    async def call_and_capture_cause():
        try:
            await recording.start_recording(1, tmp_path / "failed.mp4")
        except asyncio.CancelledError as cancellation:
            return cancellation.__cause__
        pytest.fail("start unexpectedly ignored cancellation")

    task = asyncio.create_task(call_and_capture_cause())
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    released.set()

    assert await asyncio.wait_for(task, timeout=1) is cleanup_failure


@pytest.mark.asyncio
async def test_start_cancellation_falls_back_when_abort_dispatch_fails(
    monkeypatch, tmp_path
):
    recording = _recording_module()
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    released = threading.Event()
    original_dispatch = loop.run_in_executor
    dispatch_failure = RuntimeError("executor unavailable")
    handle = object()
    aborted = []

    class FallbackNative:
        def start_recording(self, display_id, output_path, fps, video_quality=0.75):
            loop.call_soon_threadsafe(started.set)
            assert released.wait(timeout=3), "test did not release startup"
            return handle

        def _abort_recording(self, received):
            aborted.append(received)

    native = FallbackNative()
    dispatches = 0

    def dispatch(executor, function, *args):
        nonlocal dispatches
        dispatches += 1
        if dispatches == 1:
            return original_dispatch(executor, function, *args)
        raise dispatch_failure

    monkeypatch.setattr(recording, "_load_native", lambda: native)
    monkeypatch.setattr(loop, "run_in_executor", dispatch)

    async def call_and_capture_cause():
        try:
            await recording.start_recording(1, tmp_path / "fallback.mp4")
        except asyncio.CancelledError as cancellation:
            return cancellation.__cause__
        pytest.fail("start unexpectedly ignored cancellation")

    task = asyncio.create_task(call_and_capture_cause())
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    released.set()

    cause = await asyncio.wait_for(task, timeout=1)
    assert aborted == [handle]
    assert cause is dispatch_failure


@pytest.mark.asyncio
async def test_stop_waits_through_repeated_cancellation(monkeypatch):
    recording = _recording_module()
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    released = threading.Event()
    finished = asyncio.Event()

    class BlockingNative:
        def stop_recording(self, handle):
            loop.call_soon_threadsafe(started.set)
            assert released.wait(timeout=3), "test did not release stop"
            loop.call_soon_threadsafe(finished.set)
            return {
                "path": "/tmp/repeated-cancel.mp4",
                "size_bytes": 1,
                "duration_s": 0.1,
                "width": 2,
                "height": 2,
                "fps": 30,
            }

    monkeypatch.setattr(recording, "_load_native", lambda: BlockingNative())
    task = asyncio.create_task(recording.stop_recording(object()))
    await asyncio.wait_for(started.wait(), timeout=1)

    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    released.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert finished.is_set()


@pytest.mark.asyncio
async def test_stop_cancellation_chains_native_cleanup_failure(monkeypatch):
    recording = _recording_module()
    loop = asyncio.get_running_loop()
    previous_exception_handler = loop.get_exception_handler()
    started = asyncio.Event()
    released = threading.Event()
    cleanup_failure = OSError("finish failed")

    def ignore_expected_shield_log(current_loop, context):
        # Python 3.14 logs a late exception from a directly shielded Future even
        # when this coroutine subsequently retrieves and chains that exception.
        if (
            context.get("exception") is cleanup_failure
            and context.get("message", "").endswith("exception in shielded future")
        ):
            return
        if previous_exception_handler is not None:
            previous_exception_handler(current_loop, context)
        else:
            current_loop.default_exception_handler(context)

    class FailingStopNative:
        def stop_recording(self, handle):
            loop.call_soon_threadsafe(started.set)
            assert released.wait(timeout=3), "test did not release stop"
            raise cleanup_failure

    monkeypatch.setattr(recording, "_load_native", lambda: FailingStopNative())

    async def call_and_capture_cause():
        try:
            await recording.stop_recording(object())
        except asyncio.CancelledError as cancellation:
            return cancellation.__cause__
        pytest.fail("stop unexpectedly ignored cancellation")

    task = asyncio.create_task(call_and_capture_cause())
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    released.set()

    loop.set_exception_handler(ignore_expected_shield_log)
    try:
        assert await asyncio.wait_for(task, timeout=1) is cleanup_failure
    finally:
        loop.set_exception_handler(previous_exception_handler)


@pytest.mark.asyncio
async def test_stop_falls_back_to_synchronous_cleanup_when_dispatch_fails(
    monkeypatch,
):
    recording = _recording_module()
    loop = asyncio.get_running_loop()
    native = FakeNative()
    dispatch_failure = RuntimeError("executor unavailable")

    def fail_dispatch(executor, function, *args):
        raise dispatch_failure

    monkeypatch.setattr(recording, "_load_native", lambda: native)
    monkeypatch.setattr(loop, "run_in_executor", fail_dispatch)
    handle = object()

    result = await recording.stop_recording(handle)

    assert native.calls == [("stop", handle)]
    assert result.path == Path.cwd() / "relative/recording.mp4"


async def _wait_for_thread_event(event):
    async def wait():
        while not event.is_set():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cleanup_failure",
    [None, OSError("synchronous stop failed")],
    ids=["success", "failure"],
)
async def test_cancelled_queued_stop_still_finalizes_retained_handle(
    monkeypatch, cleanup_failure
):
    recording = _recording_module()
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=1)
    blocker_started = threading.Event()
    blocker_released = threading.Event()
    stop_enqueued = asyncio.Event()
    native_calls = []
    handle = object()

    def block_worker():
        blocker_started.set()
        assert blocker_released.wait(timeout=3), "test did not release executor"

    class FinalizingNative:
        def stop_recording(self, received):
            native_calls.append(received)
            if cleanup_failure is not None:
                raise cleanup_failure
            return {
                "path": "/tmp/cancelled-worker.mp4",
                "size_bytes": 1,
                "duration_s": 0.1,
                "width": 2,
                "height": 2,
                "fps": 30,
            }

    original_dispatch = loop.run_in_executor

    def dispatch(ignored_executor, function, *args):
        future = original_dispatch(executor, function, *args)
        stop_enqueued.set()
        return future

    async def call_and_capture_cause():
        try:
            await recording.stop_recording(handle)
        except asyncio.CancelledError as cancellation:
            return cancellation.__cause__
        pytest.fail("cancelled stop worker unexpectedly returned")

    executor.submit(block_worker)
    try:
        await _wait_for_thread_event(blocker_started)
        monkeypatch.setattr(recording, "_load_native", lambda: FinalizingNative())
        monkeypatch.setattr(loop, "run_in_executor", dispatch)
        task = asyncio.create_task(call_and_capture_cause())
        await asyncio.wait_for(stop_enqueued.wait(), timeout=1)

        executor.shutdown(wait=False, cancel_futures=True)
        cause = await asyncio.wait_for(task, timeout=1)

        assert native_calls == [handle]
        assert cause is cleanup_failure
    finally:
        blocker_released.set()
        executor.shutdown(wait=True, cancel_futures=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cleanup_failure",
    [None, OSError("synchronous abort failed")],
    ids=["success", "failure"],
)
async def test_cancelled_queued_abort_still_cleans_late_start_handle(
    monkeypatch, tmp_path, cleanup_failure
):
    recording = _recording_module()
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=1)
    start_entered = asyncio.Event()
    start_released = threading.Event()
    blocker_started = threading.Event()
    blocker_released = threading.Event()
    abort_enqueued = asyncio.Event()
    aborted = []
    handle = object()

    def block_worker():
        blocker_started.set()
        assert blocker_released.wait(timeout=3), "test did not release executor"

    class AbortingNative:
        def start_recording(self, display_id, output_path, fps, video_quality=0.75):
            loop.call_soon_threadsafe(start_entered.set)
            assert start_released.wait(timeout=3), "test did not release startup"
            return handle

        def _abort_recording(self, received):
            aborted.append(received)
            if cleanup_failure is not None:
                raise cleanup_failure

    native = AbortingNative()
    original_dispatch = loop.run_in_executor
    dispatches = 0

    def dispatch(ignored_executor, function, *args):
        nonlocal dispatches
        dispatches += 1
        if dispatches == 1:
            return original_dispatch(executor, function, *args)
        original_dispatch(executor, block_worker)
        future = original_dispatch(executor, function, *args)
        abort_enqueued.set()
        return future

    async def call_and_capture_cause():
        try:
            await recording.start_recording(1, tmp_path / "cancelled-abort.mp4")
        except asyncio.CancelledError as cancellation:
            return cancellation.__cause__
        pytest.fail("cancelled startup unexpectedly returned")

    try:
        monkeypatch.setattr(recording, "_load_native", lambda: native)
        monkeypatch.setattr(loop, "run_in_executor", dispatch)
        task = asyncio.create_task(call_and_capture_cause())
        await asyncio.wait_for(start_entered.wait(), timeout=1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        start_released.set()
        await asyncio.wait_for(abort_enqueued.wait(), timeout=1)
        await _wait_for_thread_event(blocker_started)

        executor.shutdown(wait=False, cancel_futures=True)
        cause = await asyncio.wait_for(task, timeout=1)

        assert aborted == [handle]
        assert cause is cleanup_failure
    finally:
        start_released.set()
        blocker_released.set()
        executor.shutdown(wait=True, cancel_futures=True)


def test_cancelled_executor_future_does_not_spin_forever():
    import subprocess

    code = """
import asyncio
from scapkit_computer_use import recording

async def check():
    future = asyncio.get_running_loop().create_future()
    future.cancel()
    try:
        await recording._wait_through_cancellation(future)
    except asyncio.CancelledError:
        return
    raise AssertionError('cancelled executor future unexpectedly returned')

asyncio.run(check())
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("cancelled executor future caused an infinite wait")
    assert result.returncode == 0, result.stderr


def test_public_handle_type_is_distinct_and_recording_import_is_native_lazy():
    recording = _recording_module()
    types = importlib.import_module("scapkit_computer_use.screen_capture_kit.types")

    assert recording.RecordingHandle is types.RecordingHandle
    assert recording.RecordingHandle is not types.CaptureHandle

    import subprocess
    code = """
import sys
from scapkit_computer_use import RecordingResult, start_recording
assert RecordingResult is not None
assert callable(start_recording)
assert 'scapkit_computer_use.screen_capture_kit._scapkit' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
