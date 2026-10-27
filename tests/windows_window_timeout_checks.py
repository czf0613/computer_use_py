"""Isolated production-Actor timeout checks with native synthetic resources.

Queued expiry must preserve a healthy Actor; actual hangs quarantine it for life.
No real capture, window, UIA client or input is created. Run via the native tests.
"""
import asyncio
import concurrent.futures
import sys
import sysconfig
import time
from pathlib import Path


def unwrap(result):
    assert result["ok"], result
    return result["value"]


async def check(native, mode):
    from scapkit_computer_use import window
    from scapkit_computer_use.window_errors import WindowError

    # Feed a real synthetic native candidate through the unchanged Python API.
    info = window._info(unwrap(native._test_window("capture"))[0], "capture", native)
    session = await window.open_window(info.ref)
    handle = session._handle
    unwrap(native._test_window(mode, handle))
    try:
        started = time.monotonic()
        try:
            await session.screenshot(format="bgra", timeout_seconds=0.1)
        except WindowError as error:
            assert error.code == "backend_busy", error
            assert error.native_domain == "actor.cleanup_unknown", error.native_domain
            assert error.native_code == 258  # WAIT_TIMEOUT
            assert error.dispatch_state == "not_dispatched"
        else:
            raise AssertionError("hung capture reported completion")
        assert 0.1 <= time.monotonic() - started < 4
        state = unwrap(native._test_window("capture_probe_state"))["subject"]
        assert state["live"] == "1" and state["completed"] == "0", state
        assert state["blocked"] == ("1" if mode == "hung_native" else "2"), state
        refused = native._test_window("capture")
        assert not refused["ok"] and refused["error"]["code"] == "backend_busy", refused
        try:
            await session.aclose()
        except WindowError as error:
            assert error.code == "backend_busy", error
            assert error.native_domain == "actor.cleanup_incomplete", error.native_domain
        else:
            raise AssertionError("close falsely reported cleanup of a blocked resource")
        assert session.closed and not session.cleanup_complete
        state = unwrap(native._test_window("capture_probe_state"))["subject"]
        assert state["live"] == "1" and state["expired"] == "0", state
    finally:
        # Signal only the synthetic blocker. This never unquarantines the actor.
        unwrap(native._test_window("capture_probe_release"))
    until = time.monotonic() + 3
    while True:
        state = unwrap(native._test_window("capture_probe_state"))["subject"]
        if state["completed"] == "1":
            break
        assert time.monotonic() < until, state
        await asyncio.sleep(0.01)
    assert state["live"] == "0" and state["blocked"] == "0", state
    assert state["expired"] == "0" and state["wrong_thread_release"] == "0", state
    # A cleanup-only call may drain late native ownership. New work remains
    # refused, and the Python session preserves its earlier incomplete result.
    unwrap(native._window_close(handle))
    refused = native._test_window("capture")
    assert not refused["ok"] and refused["error"]["code"] == "backend_busy", refused
    try:
        await session.aclose()
    except WindowError as error:
        assert error.native_domain == "actor.cleanup_incomplete"
    else:
        raise AssertionError("late return changed the original close result")
    assert session.closed and not session.cleanup_complete
    print(f"{mode}: quarantine retained; cleanup_complete=False; late resources released")


def check_queued(native):
    from scapkit_computer_use import window_profiles

    def open_native(kind):
        info = unwrap(native._test_window(kind))[0]
        return unwrap(native._window_open(info["ref"], kind))["handle"]

    handles = []
    original_validator = window_profiles._validate_authorization
    try:
        for kind in ("capture", "capture", "accessible"):
            handles.append(open_native(kind))
        active, queued, semantic = handles
        snapshot = unwrap(native._window_inspect(semantic, "elements", {"max_depth": 8, "max_nodes": 8}))
        scope = dict(unwrap(native._window_inspect(semantic, "capabilities", {}))["subject"])
        assert scope["application_id"] == "synthetic"
        scope.update(profile_id="offline", actions=[
            {"identifier": "fixture.increment", "role": "UIA.Button", "action": "invoke"}
        ])
        window_profiles._validate_authorization = lambda authorization: scope

        def invoke(timeout, operation):
            return native._window_action(semantic, snapshot["elements"][2]["ref"],
                snapshot["tree_epoch"], snapshot["geometry_revision"], "invoke", None,
                {}, operation, timeout)

        unwrap(native._test_window("held_frame", active))
        unwrap(native._test_window("no_frame_deadline", queued))
        baseline = unwrap(native._test_window("capture_probe_state"))["subject"]
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            running = pool.submit(native._window_screenshot, active, "bgra", 80, 5.0)
            try:
                until = time.monotonic() + 2
                while True:
                    state = unwrap(native._test_window("capture_probe_state"))["subject"]
                    if state["live"] == "1" and state["blocked"] == "1":
                        break
                    assert time.monotonic() < until, state
                    time.sleep(0.01)
                started = time.monotonic()
                result = native._window_screenshot(queued, "bgra", 80, 0.05)
                assert not result["ok"] and result["error"]["code"] == "capture_timeout", result
                assert result["error"]["native_domain"] == "actor.queue_timeout", result
                assert result["error"]["dispatch_state"] == "not_dispatched", result
                # No frame resources exist for a queued call: no one-second
                # cleanup grace, and no quarantine of the running healthy call.
                assert time.monotonic() - started < 0.8
                result = invoke(0.05, "queued-invoke")
                assert not result["ok"] and result["error"]["code"] == "backend_busy", result
                assert result["error"]["native_domain"] == "actor.queue_timeout", result
                assert result["error"]["dispatch_state"] == "not_dispatched", result
                assert not running.done()
            finally:
                unwrap(native._test_window("capture_probe_release"))
            frame = unwrap(running.result(timeout=3))
            assert frame["image_width"] == 64 and len(frame["data"]) == 64 * 32 * 4
        # Drain behind both canceled jobs before checking their resource/effect
        # state. A canceled queued capture or write must never run late.
        assert unwrap(native._test_window("invokes", semantic))["operation_id"] == "0"
        state = unwrap(native._test_window("capture_probe_state"))["subject"]
        assert state["live"] == "0" and state["expired"] == "0", state
        assert state["wrong_thread_release"] == "0", state
        assert int(state["completed"]) == int(baseline["completed"]) + 1, state
        unwrap(native._test_window("normal_frame", queued))
        assert unwrap(native._window_screenshot(queued, "bgra", 80, 1))["image_width"] == 64
        assert unwrap(invoke(1.0, "following-invoke"))["dispatch_state"] == "accepted"
        assert unwrap(native._test_window("invokes", semantic))["operation_id"] == "1"
    finally:
        window_profiles._validate_authorization = original_validator
        for handle in handles:
            unwrap(native._window_close(handle))
    print("queued_timeouts: canceled capture/write stayed undispatched; actor reusable")


def main():
    assert sys.platform == "win32" and sys.flags.isolated == 1
    root = Path(__file__).resolve().parents[1]
    assert Path(sys.prefix).resolve() == root / ".venv"
    free = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
    if free:
        assert not sys._is_gil_enabled()
    from scapkit_computer_use.screen_capture_kit import _scapkit

    assert Path(_scapkit.__file__).resolve().parent == root / "src/scapkit_computer_use/screen_capture_kit"
    if free:
        assert not sys._is_gil_enabled()
    mode = sys.argv[1]
    assert mode in {"hung_native", "hung_cleanup", "queued_timeouts"}
    if mode == "queued_timeouts":
        check_queued(_scapkit)
    else:
        asyncio.run(check(_scapkit, mode))
    if free:
        assert not sys._is_gil_enabled(), "timeout tests enabled GIL"
        print("gil_enabled_after_timeout_tests=False")


if __name__ == "__main__":
    main()
