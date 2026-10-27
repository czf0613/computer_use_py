"""Windows ABI 1 native state/ownership tests; no HWNDs, UIA clients or capture.

Synthetic resources enter the actual native registry, dispatch gate, snapshot,
frame and capsule paths. The test-only provider never connects to a desktop.
"""
import asyncio
import concurrent.futures
import gc
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows native extension")


@pytest.fixture
def native():
    from scapkit_computer_use.screen_capture_kit import _scapkit

    assert _scapkit.WINDOW_BACKEND_ABI == 1
    assert hasattr(_scapkit, "_test_window"), "force rebuild with SCAPKIT_TESTING=1"
    return _scapkit


def unwrap(result):
    assert result["ok"], result
    assert set(result) == {"ok", "value"}
    return result["value"]


def failure(result, code):
    assert result["ok"] is False, result
    assert result["error"]["code"] == code
    assert set(result["error"]) == {
        "code", "native_domain", "native_code", "dispatch_state", "retry_hint"
    }
    assert result["error"]["dispatch_state"] == "not_dispatched"


def candidate(native, kind="accessible"):
    return unwrap(native._test_window(kind))[0]


def opened(native, kind="accessible"):
    info = candidate(native, kind)
    result = unwrap(native._window_open(info["ref"], kind))
    assert result["generation"] == info["generation"]
    assert result["process_instance"] == info["process_instance"]
    return result["handle"]


def tree(native, handle, depth=8, nodes=512):
    return unwrap(native._window_inspect(handle, "elements", {"max_depth": depth, "max_nodes": nodes}))


def validate(native, handle, snapshot):
    return native._window_inspect(handle, "validate_snapshot", {
        "tree_epoch": snapshot["tree_epoch"], "geometry_revision": snapshot["geometry_revision"]
    })


@pytest.fixture
def session(native):
    handle = opened(native)
    yield handle
    unwrap(native._window_close(handle))


@pytest.fixture
def grant(native, monkeypatch):
    # Only synthetic targets; the production registry has no approved evidence.
    from scapkit_computer_use import window_profiles

    scope = {
        "platform": "windows", "application_id": "synthetic", "application_version": "1",
        "application_build": "synthetic", "environment": "offline", "backend_build": "synthetic",
        "window_identifier": "fixture.window", "profile_id": "offline",
        "actions": [
            {"identifier": "fixture.text", "role": "UIA.Edit", "action": "set_value"},
            {"identifier": "fixture.increment", "role": "UIA.Button", "action": "invoke"},
        ],
    }
    monkeypatch.setattr(window_profiles, "_validate_authorization", lambda authorization: scope)
    return scope


def action(native, handle, snapshot, index=1, action="set_value"):
    return native._window_action(handle, snapshot["elements"][index]["ref"],
        snapshot["tree_epoch"], snapshot["geometry_revision"], action,
        "synthetic replacement" if action == "set_value" else None, {}, "offline-operation", 5.0)


def test_full_width_candidate_consumption_and_identity(native):
    info = candidate(native)
    assert info["native_id"] == "0xfedcba9876543210"
    result = unwrap(native._window_open(info["ref"], "accessible"))
    failure(native._window_open(info["ref"], "accessible"), "stale_window")
    unwrap(native._window_close(result["handle"]))


def test_concurrent_candidate_consumption(native):
    info = candidate(native)
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: native._window_open(info["ref"], "accessible"), range(16)))
    successes = [result for result in results if result["ok"]]
    assert len(successes) == 1
    for result in results:
        if not result["ok"]:
            failure(result, "stale_window")
    unwrap(native._window_close(successes[0]["value"]["handle"]))


def test_snapshot_bounds_epochs_and_values(native, session):
    snapshot = tree(native, session, depth=0, nodes=1)
    assert len(snapshot["elements"]) == 1
    assert snapshot["elements"][0]["parent_index"] is None
    full = tree(native, session)
    assert len(full["elements"]) == 3
    assert all("value" not in row for row in full["elements"])
    assert full["elements"][1]["bounds"]["x"] == 16
    failure(validate(native, session, snapshot), "stale_element")
    unwrap(validate(native, session, full))


@pytest.mark.parametrize("event,code", [
    ("stale", "stale_window"), ("root_reuse", "stale_window"),
    ("dead_element", "stale_element"), ("layout", "stale_element"), ("move", "stale_element"),
])
def test_original_objects_cannot_rebind(native, session, event, code):
    snapshot = tree(native, session)
    unwrap(native._test_window(event, session))
    failure(validate(native, session, snapshot), code)
    if code == "stale_window":
        failure(native._window_inspect(session, "capabilities", {}), code)


def test_capture_attach_is_explicit_and_atomic(native):
    capture = opened(native, "capture")
    try:
        failure(native._window_inspect(capture, "elements", {"max_depth": 2, "max_nodes": 4}),
                "unsupported_background_operation")
        semantic = candidate(native)
        unwrap(native._window_attach(capture, semantic["ref"]))
        failure(native._window_attach(capture, semantic["ref"]), "stale_window")
        assert len(tree(native, capture)["elements"]) == 3
    finally:
        unwrap(native._window_close(capture))


def test_attach_rejects_same_low_bits_other_hwnd(native):
    capture = opened(native, "capture")
    try:
        unwrap(native._test_window("wrong_hwnd", capture))
        semantic = candidate(native)
        failure(native._window_attach(capture, semantic["ref"]), "identity_unverifiable")
        context = unwrap(native._window_inspect(capture, "capabilities", {}))
        assert context["inspect_elements"] == "unsupported"
    finally:
        unwrap(native._window_close(capture))


def test_unapproved_grant_rejected_before_dispatch(native, session):
    failure(action(native, session, tree(native, session)), "background_not_verified")
    assert unwrap(native._test_window("invokes", session))["operation_id"] == "0"


def test_actual_synthetic_value_and_invoke_paths(native, session, grant):
    result = unwrap(action(native, session, tree(native, session)))
    assert result == {"operation_id": "offline-operation", "dispatch_state": "accepted", "effect_state": "verified"}
    result = unwrap(action(native, session, tree(native, session), 2, "invoke"))
    assert result["effect_state"] == "unknown"
    assert unwrap(native._test_window("invokes", session))["operation_id"] == "1"


@pytest.mark.parametrize("event,code", [
    ("modal", "modal_blocked"), ("password", "element_not_editable"),
    ("readonly", "element_not_editable"), ("unknown_enabled", "element_not_editable"),
    ("autofocus", "unsupported_background_operation"), ("minimize", "window_minimized"),
])
def test_dispatch_preconditions(native, session, grant, event, code):
    unwrap(native._test_window(event, session))
    failure(action(native, session, tree(native, session)), code)


def test_cross_session_element_and_exact_grant(native, session, grant):
    other = opened(native)
    try:
        snapshot = tree(native, session)
        failure(action(native, other, snapshot), "stale_element")
        grant["application_build"] = "different-build"
        failure(action(native, session, snapshot), "background_not_verified")
    finally:
        unwrap(native._window_close(other))


def test_frames_use_actual_pixels_and_unique_ids(native):
    handle = opened(native, "capture")
    try:
        first = unwrap(native._window_screenshot(handle, "bgra", 80, 5))
        second = unwrap(native._window_screenshot(handle, "jpeg", 80, 5))
        assert first["frame_id"] != second["frame_id"]
        assert first["image_width"] == 64 and first["image_height"] == 32
        assert len(first["data"]) == first["stride"] * 32
        assert first["image_to_local"] == (1, 0, 0, 1, 0, 0)
        assert first["window_bounds"]["x"] == -1920
        from scapkit_computer_use.window import _jpeg_dimensions
        assert _jpeg_dimensions(second["data"]) == (64, 32)
        unwrap(native._test_window("unmapped_frame", handle))
        unmatched = unwrap(native._window_screenshot(handle, "bgra", 80, 5))
        assert unmatched["mapping_status"] == "unavailable" and unmatched["image_to_local"] is None
    finally:
        unwrap(native._window_close(handle))


@pytest.mark.parametrize("event,code", [
    ("resize_during_frame", "geometry_changed"), ("closed_during_frame", "stale_window"),
    ("device_error", "capture_unavailable"), ("capture_timeout", "capture_timeout"),
    ("minimize", "window_minimized"),
])
def test_frame_failures_do_not_substitute_cached_pixels(native, event, code):
    handle = opened(native, "capture")
    try:
        unwrap(native._test_window(event, handle))
        failure(native._window_screenshot(handle, "bgra", 80, 5), code)
    finally:
        unwrap(native._window_close(handle))


def test_capture_deadline_cleans_up_and_keeps_actor_available(native):
    handle = opened(native, "capture")
    try:
        initial = unwrap(native._test_window("capture_probe_state"))["subject"]
        unwrap(native._test_window("no_frame_deadline", handle))
        for index in range(3):
            started = time.monotonic()
            failure(native._window_screenshot(handle, "bgra", 80, 0.1), "capture_timeout")
            assert 0.1 <= time.monotonic() - started < 3
            state = unwrap(native._test_window("capture_probe_state"))["subject"]
            assert state["live"] == "0" and state["blocked"] == "0"
            assert int(state["completed"]) == int(initial["completed"]) + index + 1
            assert state["expired"] == "0" and state["wrong_thread_release"] == "0"
        # This is the same production actor after three real deadline waits and
        # resource destruction, not an immediate synthetic error or a new actor.
        unwrap(native._test_window("normal_frame", handle))
        assert unwrap(native._window_screenshot(handle, "bgra", 80, 1))["image_width"] == 64
    finally:
        unwrap(native._window_close(handle))


def run_timeout_child(mode):
    # Each process has its one real Actor. Isolation is necessary because its
    # quarantine must be permanent; no testing reset or replacement is allowed.
    from scapkit_computer_use.process import _shell_environment

    environment = asyncio.run(_shell_environment())
    environment = {key: value for key, value in environment.items()
                   if key.upper() not in {"PYTHONHOME", "PYTHONPATH"}}
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-I", str(root / "tests/windows_window_timeout_checks.py"), mode],
        cwd=root, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.mark.parametrize("mode", ["hung_native", "hung_cleanup"])
def test_hung_capture_quarantines_production_actor(native, mode):
    assert f"{mode}: quarantine retained; cleanup_complete=False; late resources released" in run_timeout_child(mode)


def test_queued_timeouts_do_not_quarantine_healthy_actor(native):
    assert "queued_timeouts: canceled capture/write stayed undispatched; actor reusable" in run_timeout_child("queued_timeouts")


def test_concurrent_close_and_retained_element(native, session):
    snapshot = tree(native, session)
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: native._window_close(session), range(16)))
    assert all(result == {"ok": True, "value": None} for result in results)
    failure(validate(native, session, snapshot), "session_closed")


def test_capsule_gc_releases_on_actor(native):
    baseline = unwrap(native._test_window("resources"))["subject"]
    handle = opened(native)
    snapshot = tree(native, handle)
    del snapshot, handle
    gc.collect()
    result = unwrap(native._test_window("resources"))["subject"]
    assert result == baseline
    assert result["wrong_thread_release"] == "0"


def test_timeout_dispatch_cas(native):
    unwrap(native._test_window("gate"))


def test_com_value_pattern_uses_owned_bstr(native):
    unwrap(native._test_window("bstr"))


@pytest.mark.parametrize("value", [True, -1, 101, 2**100])
def test_quality_native_bounds(native, value):
    handle = opened(native, "capture")
    try:
        with pytest.raises((TypeError, ValueError, OverflowError)):
            native._window_screenshot(handle, "bgra", value, 5)
    finally:
        unwrap(native._window_close(handle))


@pytest.mark.parametrize("options", [
    {"max_depth": True, "max_nodes": 1}, {"max_depth": 33, "max_nodes": 1},
    {"max_depth": 1, "max_nodes": 4097}, {"max_depth": 1, "max_nodes": 1, "unsafe": True},
])
def test_tree_native_bounds(native, session, options):
    with pytest.raises((TypeError, ValueError, OverflowError)):
        native._window_inspect(session, "elements", options)


@pytest.mark.parametrize("timeout", [True, float("nan"), float("inf"), 0, 31])
def test_timeout_native_bounds(native, session, timeout):
    with pytest.raises((TypeError, ValueError)):
        native._window_screenshot(session, "bgra", 80, timeout)
