"""Real recording lifecycle/clock with synthetic pixels; never create SCStream."""

from concurrent.futures import ThreadPoolExecutor
import gc
import json
import shutil
import subprocess
import time

import pytest

from scapkit_computer_use.screen_capture_kit import _scapkit as native


def make_recording(path, fps=30, mode="normal"):
    assert hasattr(native, "_test_recording"), "build recording hooks with SCAPKIT_TESTING=1"
    return native._test_recording(str(path), fps, mode)


def media_info(path):
    executable = shutil.which("ffprobe")
    if executable is None:
        pytest.skip("optional ffprobe inspection; native writer probe covers media in CI")
    return json.loads(subprocess.check_output([
        executable, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)
    ], text=True))


def test_static_recording_has_full_length_without_new_screen_frames(tmp_path):
    output = tmp_path / "static.mp4"
    handle = make_recording(output, 20)
    # One cached image for the entire recording. The real frame clock submits it.
    native._test_recording_tick(handle, 150_000_000)
    result = native._test_stop_recording_at(handle, 250_000_000)
    assert result["duration_s"] == pytest.approx(0.25, abs=0.0001)
    assert result["size_bytes"] == output.stat().st_size > 0
    assert (result["width"], result["height"], result["fps"]) == (64, 48, 20)
    assert native.stop_recording(handle) == result
    streams = media_info(output)["streams"]
    video = next(stream for stream in streams if stream["codec_type"] == "video")
    assert video["codec_name"] == "h264"
    assert int(video["nb_frames"]) == 5
    assert float(video["duration"]) == pytest.approx(0.25, abs=0.001)
    assert next(stream for stream in streams if stream["codec_type"] == "audio")["codec_name"] == "aac"


@pytest.mark.parametrize("end_ns, frames", [(1_000_000, 1), (50_000_000, 1), (55_000_000, 2)])
def test_stop_keeps_short_last_frame_and_does_not_add_frame_at_exact_boundary(tmp_path, end_ns, frames):
    output = tmp_path / "short.mp4"
    handle = make_recording(output, 20)
    native._test_stop_recording_at(handle, end_ns)
    info = media_info(output)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert int(video["nb_frames"]) == frames
    assert float(video["duration"]) == pytest.approx(end_ns / 1e9, abs=0.001)


def test_concurrent_stop_publishes_once_and_reuses_result(tmp_path):
    output = tmp_path / "concurrent.mp4"
    handle = make_recording(output)
    native._test_recording_tick(handle, 100_000_000)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: native.stop_recording(handle), range(6)))
    assert all(result == results[0] for result in results)
    assert output.stat().st_size == results[0]["size_bytes"]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["concurrent.mp4"]


def test_abort_and_destructor_remove_unfinished_output(tmp_path):
    first = make_recording(tmp_path / "abort.mp4")
    native._test_recording_tick(first, 100_000_000)
    native._abort_recording(first)
    native._abort_recording(first)
    with pytest.raises(OSError):
        native.stop_recording(first)
    second = make_recording(tmp_path / "dropped.mp4")
    native._test_recording_tick(second, 100_000_000)
    del second
    gc.collect()
    deadline = time.monotonic() + 5
    while list(tmp_path.iterdir()) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert list(tmp_path.iterdir()) == []


def test_existing_target_created_during_recording_is_preserved(tmp_path):
    output = tmp_path / "raced.mp4"
    handle = make_recording(output)
    native._test_recording_tick(handle, 100_000_000)
    output.write_bytes(b"owner data")
    with pytest.raises(OSError):
        native._test_stop_recording_at(handle, 150_000_000)
    assert output.read_bytes() == b"owner data"
    assert list(tmp_path.iterdir()) == [output]


def test_real_timer_repeats_synthetic_frame_without_capture_callbacks(tmp_path):
    output = tmp_path / "timer.mp4"
    handle = make_recording(output, 20, "realtime")
    started = time.monotonic()
    time.sleep(0.16)
    before_stop = time.monotonic() - started
    result = native.stop_recording(handle)
    assert before_stop <= result["duration_s"] < before_stop + 0.2
    video = next(s for s in media_info(output)["streams"] if s["codec_type"] == "video")
    assert int(video["nb_frames"]) >= 4
    assert float(video["duration"]) == pytest.approx(result["duration_s"], abs=0.001)


def test_destructor_retains_cleanup_owner_until_delayed_stream_stop(tmp_path):
    # A prior optional ffprobe skip can retain its frame/handle in a traceback
    # until GC. Drain those owners before measuring this recording's lifetime.
    gc.collect()
    deadline = time.monotonic() + 3
    while native._test_recording_owners() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert native._test_recording_owners() == 0
    before = native._test_recording_owners()
    handle = make_recording(tmp_path / "delayed.mp4", mode="stop_gated")
    native._test_recording_tick(handle, 100_000_000)
    del handle
    gc.collect()
    try:
        deadline = time.monotonic() + 3
        while not native._test_recording_stop_gate(False):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert native._test_recording_owners() == before + 1
    finally:
        native._test_recording_stop_gate(True)
    deadline = time.monotonic() + 3
    while native._test_recording_owners() != before and time.monotonic() < deadline:
        time.sleep(0.01)
    assert native._test_recording_owners() == before
    assert list(tmp_path.iterdir()) == []


def test_audio_after_cutoff_is_rejected_before_writer_backlog(tmp_path):
    output = tmp_path / "audio-cutoff.mp4"
    handle = make_recording(output, mode="stop_delayed")
    native._test_recording_tick(handle, 100_000_000)
    with ThreadPoolExecutor(max_workers=1) as pool:
        stopped = pool.submit(native._test_stop_recording_at, handle, 100_000_000)
        deadline = time.monotonic() + 2
        while not native._test_recording_state(handle)["stopping"]:
            assert time.monotonic() < deadline
            time.sleep(0.001)
        # Keep only the portion of this block before T1; drop the far-future block
        # rather than letting it overflow the writer while SCStream is stopping.
        native._test_recording_audio(handle, 95_000_000, 480)
        native._test_recording_audio(handle, 5_000_000_000, 480)
        assert stopped.result(timeout=3)["duration_s"] == pytest.approx(0.1)
    assert output.is_file()


def test_finalization_timeout_prevents_late_file_publication(tmp_path):
    output = tmp_path / "late.mp4"
    handle = make_recording(output, mode="finish_timeout")
    with pytest.raises(TimeoutError):
        native._test_stop_recording_at(handle, 100_000_000)
    time.sleep(0.3)
    assert not output.exists()
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(TimeoutError):
        native.stop_recording(handle)


def test_published_file_wins_timeout_before_result_handoff(tmp_path):
    output = tmp_path / "published.mp4"
    handle = make_recording(output, mode="publish_handoff_timeout")
    result = native._test_stop_recording_at(handle, 100_000_000)
    assert result["size_bytes"] == output.stat().st_size
    assert result["duration_s"] == pytest.approx(0.1)
    assert native.stop_recording(handle) == result


@pytest.mark.parametrize("mode", ["first_frame_timeout", "stream_error", "stop_error", "stop_timeout"])
def test_stream_failures_close_resources_without_publishing(tmp_path, mode):
    output = tmp_path / "failed.mp4"
    if mode == "first_frame_timeout":
        with pytest.raises(TimeoutError):
            make_recording(output, mode=mode)
    else:
        handle = make_recording(output, mode=mode)
        with pytest.raises(OSError):
            native.stop_recording(handle)
        with pytest.raises(OSError):
            native.stop_recording(handle)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("args,error", [
    ((), TypeError), ((True, "out.mp4", 30), TypeError),
    ((0, "out.mp4", 30), ValueError), ((-1, "out.mp4", 30), ValueError),
    ((2**32, "out.mp4", 30), ValueError), ((1.5, "out.mp4", 30), TypeError),
    ((1, "out.mp4", True), TypeError), ((1, "out.mp4", 0), ValueError),
    ((1, "out.mp4", 2**31), ValueError), ((1, b"out.mp4", 30), TypeError),
    ((1, "", 30), ValueError), ((1, "out\x00.mp4", 30), ValueError),
])
def test_native_validation_never_reaches_display_lookup(args, error):
    with pytest.raises(error):
        native.start_recording(*args)


@pytest.mark.parametrize('quality,error', [(True, TypeError), ('0.5', TypeError), (1.5, ValueError), (-1, ValueError), (float('nan'), ValueError), (float('inf'), ValueError)])
def test_native_quality_validation_never_reaches_display_lookup(tmp_path, quality, error):
    with pytest.raises(error):
        native.start_recording(1, str(tmp_path / 'out.mp4'), 30, quality)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("method", ["stop_recording", "_abort_recording"])
def test_recording_rejects_foreign_capsules(method):
    with pytest.raises(ValueError):
        getattr(native, method)(object())
