"""Windows native tests: generated pixels/audio and offline input only."""
import concurrent.futures
import gc
import sys
import sysconfig
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows extension")


@pytest.fixture
def native():
    from scapkit_computer_use.screen_capture_kit import _scapkit
    if not hasattr(_scapkit, "_test_input"):
        pytest.skip("Rebuild with SCAPKIT_TESTING=1")
    return _scapkit


@pytest.mark.parametrize("direction,delta,flag", [
    ("up", -120, 0x0800), ("down", 120, 0x0800),
    ("left", 120, 0x1000), ("right", -120, 0x1000),
])
def test_wheel_content_direction(native, direction, delta, flag):
    result = native._test_input("wheel", (direction, 1))
    assert result["wheel"] == delta
    assert result["flags"] == flag


@pytest.mark.parametrize("x,y", [(-2560, -1080), (-1, -1), (0, 0), (3839, 2159)])
def test_absolute_virtual_desktop_roundtrip(native, x, y):
    left, top, width, height = -2560, -1080, 6400, 3240
    value = native._test_input("move", (x, y, left, top, 3840, 2160))
    assert value["flags"] & 0xC001 == 0xC001
    assert abs((value["x"] * width // 65536 + left) - x) <= 1
    assert abs((value["y"] * height // 65536 + top) - y) <= 1


def test_copied_pixels_and_concurrent_stop(native):
    pixels = bytes([20, 120, 220, 255]) * 12
    handle = native._test_capture(4, 3, pixels)
    saved = native.current_frame_bgra(handle)
    assert saved == {"data": pixels, "width": 4, "height": 3, "bytes_per_row": 16}
    assert native.current_frame_jpg(handle).startswith(b"\xff\xd8")
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        calls = [pool.submit(native.current_frame_bgra, handle) for _ in range(30)]
        calls += [pool.submit(native.stop_capture, handle) for _ in range(8)]
        for future in calls:
            result = future.result()
            assert result is None or result["data"] == pixels
    assert native.current_frame_bgra(handle) is None
    assert saved["data"] == pixels
    with pytest.raises(ValueError):
        native.current_frame_jpg(handle, 101)


@pytest.mark.parametrize("name", ["stop_capture", "current_frame_bgra", "stop_recording", "_abort_recording", "_keyboard_end"])
def test_wrong_handle_is_rejected(native, name):
    with pytest.raises((ValueError, TypeError)):
        getattr(native, name)(object())


@pytest.mark.parametrize("delay", [0, 90])
def test_software_recording_drops_frames_without_speedup(native, tmp_path, delay):
    path = tmp_path / "recording.data"
    result = native._test_recording(str(path), 321, 239, 30, 0.7, delay, 1)
    assert result["width"] == 322 and result["height"] == 240
    assert result["size_bytes"] == path.stat().st_size
    assert result["duration_s"] == pytest.approx(0.7)
    decoded = native._test_decode(str(path))
    assert (decoded["width"], decoded["height"]) == (322, 240)
    frames = decoded["video"]
    assert len(frames) == result["frames_written"]
    assert frames[0][0] == 0
    assert all(b[0] > a[0] for a, b in zip(frames, frames[1:]))
    assert (frames[-1][0] + frames[-1][1]) / 10_000_000 == pytest.approx(0.7, abs=0.035)
    assert decoded["audio_frames"] / 48000 == pytest.approx(0.7, abs=0.05)
    assert decoded["first_luma"] == pytest.approx(135, abs=5)
    assert decoded["audio_peak"] > 1000
    assert result["frames_written"] + result["frames_dropped"] == 21
    if delay:
        assert result["frames_dropped"] > 0
        assert len(frames) < 15
        assert any(duration > 500_000 for _, duration in frames)
    original = path.read_bytes()
    with pytest.raises(OSError):
        native._test_recording(str(path), 320, 240, 30, 0.1)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp.mp4"))


def test_free_threaded_import_does_not_enable_gil(native):
    if sysconfig.get_config_var("Py_GIL_DISABLED"):
        assert not sys._is_gil_enabled()


@pytest.mark.parametrize("rectangles,start,end", [
    (((0, 0, 3840, 2160), (3840, 1134, 6400, 2734)), (400, 400), (6000, 2500)),
    (((-2560, -500, 0, 1100), (0, 0, 3840, 2160)), (3000, 1500), (-2400, -400)),
    (((0, -1200, 1920, 0), (1000, 0, 3560, 1440)), (150, -1100), (3000, 1200)),
    (((-1000, 0, 0, 1000), (0, 0, 1000, 1000), (1000, 500, 2000, 1500)),
     (-900, 100), (1900, 1400)),
])
def test_cross_display_path_avoids_gaps(native, rectangles, start, end):
    points = [start, *native._test_input("path", (*start, *end, rectangles))]
    assert points[-1] == end
    for a, b in zip(points, points[1:]):
        for step in range(101):
            x, y = (round(a[i] + (b[i] - a[i]) * step / 100) for i in range(2))
            assert any(l <= x < r and t <= y < bot for l, t, r, bot in rectangles)


def test_disconnected_monitor_path_fails_before_input(native):
    rectangles = ((0, 0, 1000, 1000), (1500, 0, 2500, 1000))
    with pytest.raises(ValueError, match="No connected visible path"):
        native._test_input("path", (500, 500, 2000, 500, rectangles))
    with pytest.raises(ValueError, match="outside"):
        native._test_input("path", (500, 500, 1250, 500, rectangles))


def synthetic_handle(native, path):
    return native._test_recording(str(path), 320, 240, 30, 1.0, 0, 1, True)


def test_recording_concurrent_stop_abort_and_destructor(native, tmp_path):
    path = tmp_path / "concurrent.mp4"
    handle = synthetic_handle(native, path)
    time.sleep(0.15)
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        results = list(pool.map(native.stop_recording, [handle] * 8))
    assert all(result == results[0] for result in results)
    assert results[0]["size_bytes"] == path.stat().st_size
    assert native._test_decode(str(path))["audio_peak"] > 1000
    for cleanup in ("abort", "destruct"):
        target = tmp_path / f"{cleanup}.mp4"
        handle = synthetic_handle(native, target)
        if cleanup == "abort":
            native._abort_recording(handle)
            native._abort_recording(handle)
            with pytest.raises(OSError, match="aborted"):
                native.stop_recording(handle)
        del handle
        gc.collect()
        assert not target.exists()
        assert not list(tmp_path.glob("*.tmp.mp4"))


def test_destination_created_during_recording_is_not_replaced(native, tmp_path):
    path = tmp_path / "race.mp4"
    handle = synthetic_handle(native, path)
    path.write_bytes(b"someone else's output")
    with pytest.raises(OSError):
        native.stop_recording(handle)
    assert path.read_bytes() == b"someone else's output"
    assert not list(tmp_path.glob("*.tmp.mp4"))


def test_jpeg_from_an_existing_sta_thread(native):
    import ctypes
    def worker():
        ole32 = ctypes.WinDLL("ole32")
        hr = ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
        assert hr in (0, 1)
        try:
            handle = native._test_capture(4, 4, b"\xff\0\0\xff" * 16)
            assert native.current_frame_jpg(handle).startswith(b"\xff\xd8")
        finally:
            ole32.CoUninitialize()
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        pool.submit(worker).result()
