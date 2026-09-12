"""Async wrapper regressions using native-call doubles, never desktop input."""

import asyncio
import gc
import threading
import weakref

import pytest

from scapkit_computer_use import screen_capture_kit as capture


@pytest.fixture
def input_events(monkeypatch):
    events = []

    def mouse_button(key, action):
        events.append(("mouse", key, action))

    def keyboard_key(code, action, flags):
        events.append(("keyboard", code, action, flags))

    def move(x, y):
        events.append(("move", x, y))

    monkeypatch.setattr(capture, "mouse_click_action", mouse_button)
    monkeypatch.setattr(capture, "keyboard_click_c", keyboard_key)
    monkeypatch.setattr(capture, "get_mouse_position", lambda: {"x": 0, "y": 0})
    monkeypatch.setattr(capture, "move_mouse_c", move)
    return events


INPUT_OPERATIONS = [
    ("mouse_long_click", ("right", 0.01), ("mouse", "right", "down"), ("mouse", "right", "up")),
    ("mouse_click", ("left",), ("mouse", "left", "down"), ("mouse", "left", "up")),
    ("mouse_drag", ({"x": 15, "y": 15},), ("mouse", "left", "down"), ("mouse", "left", "up")),
    ("keyboard_click", ("a", {"shift"}), ("keyboard", 0, "down", 0x020000), ("keyboard", 0, "up", 0x020000)),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,args,down,up", INPUT_OPERATIONS)
async def test_input_releases_after_cancellation(input_events, operation, args, down, up):
    task = asyncio.create_task(getattr(capture, operation)(*args))
    # Let the real coroutine post down and suspend at its first real sleep.
    await asyncio.sleep(0)
    assert input_events == [down]

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert input_events == [down, up]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,args,down,up", INPUT_OPERATIONS)
async def test_input_releases_after_await_failure(input_events, operation, args, down, up):
    coroutine = getattr(capture, operation)(*args)
    failure = RuntimeError("await failed")
    try:
        # Inject an exception at the real await boundary without mocking asyncio.
        coroutine.send(None)
        assert input_events == [down]
        with pytest.raises(RuntimeError) as caught:
            coroutine.throw(failure)
        assert caught.value is failure
    finally:
        coroutine.close()

    assert input_events == [down, up]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,args,down,up", INPUT_OPERATIONS)
async def test_input_releases_once_on_success(input_events, operation, args, down, up):
    await getattr(capture, operation)(*args)

    assert input_events[0] == down
    assert input_events[-1] == up
    assert input_events.count(down) == input_events.count(up) == 1


@pytest.mark.asyncio
async def test_drag_releases_when_cancelled_during_movement(monkeypatch, input_events):
    loop = asyncio.get_running_loop()

    def cancel_on_move(x, y):
        input_events.append(("move", x, y))
        loop.call_soon(task.cancel)

    monkeypatch.setattr(capture, "move_mouse_c", cancel_on_move)
    task = asyncio.create_task(capture.mouse_drag({"x": 15, "y": 15}))
    with pytest.raises(asyncio.CancelledError):
        await task

    assert input_events[0] == ("mouse", "left", "down")
    assert any(event[0] == "move" for event in input_events)
    assert input_events[-1] == ("mouse", "left", "up")
    assert input_events.count(("mouse", "left", "up")) == 1


@pytest.mark.asyncio
async def test_drag_releases_when_native_movement_fails(monkeypatch, input_events):
    failure = OSError("native move failed")

    def fail_move(x, y):
        raise failure

    monkeypatch.setattr(capture, "move_mouse_c", fail_move)
    with pytest.raises(OSError) as caught:
        await capture.mouse_drag({"x": 15, "y": 15})

    assert caught.value is failure
    assert input_events == [("mouse", "left", "down"), ("mouse", "left", "up")]


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", [0, -0.1, float("nan"), float("inf"), -float("inf")])
async def test_long_click_rejects_invalid_duration_before_down(input_events, duration):
    with pytest.raises(ValueError, match="duration_s"):
        await asyncio.wait_for(capture.mouse_long_click("left", duration), timeout=0.1)

    assert input_events == []


@pytest.mark.asyncio
async def test_cancelled_capture_drops_handle_from_unfinished_worker(monkeypatch):
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    finish = threading.Event()
    released = threading.Event()

    class FakeHandle:
        pass

    def start(display_id):
        assert display_id == 42
        loop.call_soon_threadsafe(started.set)
        if not finish.wait(timeout=3):
            raise TimeoutError("test did not release the worker")
        handle = FakeHandle()
        # Models the native capsule destructor without retaining the handle.
        weakref.finalize(handle, released.set)
        return handle

    monkeypatch.setattr(capture, "start_capture_c", start)
    task = asyncio.create_task(capture.start_capture(42))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        assert not finish.is_set()
    finally:
        finish.set()
        await loop.shutdown_default_executor()

    # Drain result-transfer callbacks and account for deferred no-GIL collection.
    await asyncio.sleep(0)
    gc.collect()
    assert released.is_set()


@pytest.mark.asyncio
async def test_capture_success_keeps_handle_until_caller_drops_it(monkeypatch):
    released = threading.Event()

    class FakeHandle:
        pass

    def start(display_id):
        handle = FakeHandle()
        weakref.finalize(handle, released.set)
        return handle

    monkeypatch.setattr(capture, "start_capture_c", start)
    handle = await capture.start_capture(42)
    assert isinstance(handle, FakeHandle)
    assert not released.is_set()

    del handle
    await asyncio.get_running_loop().shutdown_default_executor()
    await asyncio.sleep(0)
    gc.collect()
    assert released.is_set()
