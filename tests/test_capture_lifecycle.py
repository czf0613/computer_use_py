"""Fake-stream lifecycle tests; all native operations use synthetic frames."""

import subprocess
import sys
import textwrap

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS extension")


def run_lifecycle_check(source):
    bootstrap = """
        import faulthandler
        import gc
        import resource
        import time
        from concurrent.futures import ThreadPoolExecutor
        from scapkit_computer_use.screen_capture_kit import _scapkit as c

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        faulthandler.dump_traceback_later(15, exit=True)
        assert hasattr(c, '_test_lifecycle_stats'), 'build with SCAPKIT_TESTING=1'

        def assert_closed(handle):
            assert c.current_frame_bgra(handle) is None
            assert c.current_frame_jpg(handle) is None
            c._test_update_frame(handle)
            assert c.current_frame_bgra(handle) is None

        def wait_for(predicate):
            deadline = time.monotonic() + 10
            while not predicate():
                assert time.monotonic() < deadline, c._test_lifecycle_stats()
                time.sleep(0.01)

        def assert_released():
            gc.collect()
            wait_for(lambda: all(c._test_lifecycle_stats()[key] == 0 for key in
                     ('streams', 'delegates', 'callback_blocks', 'frames')))
            stats = c._test_lifecycle_stats()
            assert stats['stop_calls'] == 1, stats
            assert stats['callbacks'] == 1, stats
            assert stats['attached_outputs'] == 0, stats
    """
    result = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c",
         textwrap.dedent(bootstrap) + textwrap.dedent(source)],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=20,
    )
    assert result.returncode == 0, (
        f"child exited with {result.returncode}\n{result.stdout}\n{result.stderr}"
    )


@pytest.mark.parametrize("mode", ["success", "error"])
def test_synchronous_stop_closes_handle_and_releases_stream(mode):
    run_lifecycle_check(
        f"""
        h = c._test_capture({mode!r})
        assert c.current_frame_bgra(h)['width'] == 8
        before = c._test_lifecycle_stats()
        assert before['streams'] == before['delegates'] == before['frames'] == 1
        try:
            c.stop_capture(h)
        except OSError as exc:
            assert {mode!r} == 'error', exc
            assert 'synthetic stop failure' in str(exc), exc
        else:
            assert {mode!r} == 'success'
        assert_closed(h)
        c.stop_capture(h)
        del h
        assert_released()
        """
    )


def test_stop_timeout_allows_reads_and_idempotent_stop_before_late_callback():
    run_lifecycle_check(
        """
        h = c._test_capture('delayed')
        with ThreadPoolExecutor(max_workers=1) as pool:
            stopped = pool.submit(c.stop_capture, h)
            wait_for(lambda: c._test_lifecycle_stats()['stop_calls'] == 1)
            # This thread must run while native stop is still waiting detached.
            assert not stopped.done(), 'stop held Python execution while waiting'
            assert_closed(h)
            c.stop_capture(h)
            try:
                stopped.result(timeout=8)
            except TimeoutError as exc:
                assert 'stopCapture timed out' in str(exc), exc
            else:
                raise AssertionError('expected the production five-second timeout')
        del stopped
        assert_closed(h)
        del h
        # The callback may run after both the stop call and capsule destruction.
        assert_released()
        """
    )


@pytest.mark.parametrize("mode", ["success", "error", "delayed"])
def test_destructor_detaches_output_before_callback_and_releases_owners(mode):
    run_lifecycle_check(
        f"""
        h = c._test_capture({mode!r})
        del h
        gc.collect()
        stats = c._test_lifecycle_stats()
        assert stats['stop_calls'] == 1, stats
        if {mode!r} == 'delayed':
            assert stats['callbacks'] == 0, 'destructor waited for delayed stop'
            assert stats['streams'] == stats['delegates'] == stats['callback_blocks'] == 1
            # The late sample retains the same frame after the handle drops it.
            assert stats['frames'] == 1, stats
            # Reuse/free handle allocations before the retained output is called.
            for _ in range(100):
                other = c._test_capture()
                del other
            gc.collect()
        else:
            assert stats['frames'] == 0, stats
        assert_released()
        """
    )
