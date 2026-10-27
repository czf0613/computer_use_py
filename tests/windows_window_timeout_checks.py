"""Isolated production-Actor hang checks, using only native synthetic resources.

One process per case: the Actor must remain quarantined even after late cleanup.
No real capture, window, UIA client or input is created. Run via the native tests.
"""
import asyncio
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
    assert mode in {"hung_native", "hung_cleanup"}
    asyncio.run(check(_scapkit, mode))
    if free:
        assert not sys._is_gil_enabled(), "timeout tests enabled GIL"
        print("gil_enabled_after_timeout_tests=False")


if __name__ == "__main__":
    main()
