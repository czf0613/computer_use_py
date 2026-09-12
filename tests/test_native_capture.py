"""Synthetic CoreVideo tests. Build with SCAPKIT_TESTING=1; no desktop access."""
import gc
import subprocess
import sys
import sysconfig

import pytest
from scapkit_computer_use.screen_capture_kit import _scapkit


def make_capture():
    assert hasattr(_scapkit, "_test_capture"), "rebuild with SCAPKIT_TESTING=1"
    return _scapkit._test_capture()


def test_synthetic_frame_copy_and_jpeg():
    handle = make_capture()
    frame = _scapkit.current_frame_bgra(handle)
    assert (frame["width"], frame["height"]) == (8, 8)
    assert len(frame["data"]) == frame["bytes_per_row"] * 8
    assert frame["data"][:4] == bytes([32, 64, 128, 255])
    assert _scapkit.current_frame_jpg(handle, 80)[:2] == b"\xff\xd8"
    _scapkit.stop_capture(handle)


def test_stop_is_idempotent_and_rejects_late_frames():
    handle = make_capture()
    _scapkit.stop_capture(handle)
    _scapkit.stop_capture(handle)
    _scapkit._test_update_frame(handle)
    assert _scapkit.current_frame_bgra(handle) is None
    assert _scapkit.current_frame_jpg(handle) is None


@pytest.mark.parametrize("quality", [-1, 101])
def test_invalid_quality(quality):
    handle = make_capture()
    with pytest.raises(ValueError):
        _scapkit.current_frame_jpg(handle, quality)
    _scapkit.stop_capture(handle)


def test_read_update_stop_race_in_subprocess():
    result = subprocess.run([sys.executable, "-X", "faulthandler", "-c", '''
from concurrent.futures import ThreadPoolExecutor
import gc
from scapkit_computer_use.screen_capture_kit import _scapkit as c
for iteration in range(20):
    h = c._test_capture()
    def work(worker):
        for i in range(30):
            if worker == 0:
                c._test_update_frame(h)
            elif worker == 1 and i == 15:
                c.stop_capture(h)
            elif worker % 2:
                c.current_frame_jpg(h, 80)
            else:
                c.current_frame_bgra(h)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(work, range(6)))
    c.stop_capture(h)
    del h
    gc.collect()
'''], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_drop_unstopped_handles():
    # Exercises the capsule destructor with a retained CVPixelBuffer and delegate.
    before = _scapkit._test_live_frames()
    for _ in range(100):
        handle = make_capture()
        del handle
    gc.collect()
    assert _scapkit._test_live_frames() == before


def test_frame_references_are_released_on_replace_and_stop():
    before = _scapkit._test_live_frames()
    handle = make_capture()
    assert _scapkit._test_live_frames() == before + 1
    for _ in range(100):
        _scapkit._test_update_frame(handle)
        assert _scapkit._test_live_frames() == before + 1
    _scapkit.stop_capture(handle)
    assert _scapkit._test_live_frames() == before


@pytest.mark.parametrize("call,args", [
    ("start_capture", ()),
    ("start_capture", (None,)),
    ("stop_capture", ()),
    ("current_frame_bgra", ()),
    ("current_frame_jpg", ()),
    ("current_frame_jpg", (None, "bad")),
])
def test_invalid_capture_arguments(call, args):
    with pytest.raises(TypeError):
        getattr(_scapkit, call)(*args)


@pytest.mark.parametrize("call", ["stop_capture", "current_frame_bgra", "current_frame_jpg"])
def test_invalid_capsule(call):
    with pytest.raises(ValueError):
        getattr(_scapkit, call)(object())


@pytest.mark.parametrize("display_id", [0, -1, 2**32, 2**100])
def test_invalid_display_id_never_starts_capture(display_id):
    with pytest.raises((ValueError, OverflowError)):
        _scapkit.start_capture(display_id)


@pytest.mark.skipif(
    bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
    reason="free-threaded GC does not run synchronously in dict allocation",
)
def test_bgra_gc_callback_can_read_same_handle():
    # Py_BuildValue creates a tracked dict and can invoke arbitrary GC callbacks.
    # Run in a child so a non-recursive native lock regression has a time bound.
    result = subprocess.run([sys.executable, "-X", "faulthandler", "-c", '''
import gc
from scapkit_computer_use.screen_capture_kit import _scapkit as c
h = c._test_capture()
reentered = []
def callback(phase, info):
    if phase == 'start':
        reentered.append(c.current_frame_bgra(h))
gc.collect()
gc.callbacks.append(callback)
frames = []
gc.set_threshold(10, 10, 10)
try:
    for _ in range(1000):
        frames.append(c.current_frame_bgra(h))
finally:
    gc.callbacks.remove(callback)
    gc.set_threshold(700, 10, 10)
    c.stop_capture(h)
assert reentered, 'GC callback did not exercise reentry'
assert all(frame is not None for frame in reentered)
'''], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
