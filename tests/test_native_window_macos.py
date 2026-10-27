"""Real native state/capsule tests with in-memory targets. No desktop acquisition."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import gc
import math
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS native backend")
from scapkit_computer_use.screen_capture_kit import _scapkit as native
from scapkit_computer_use import window

if not hasattr(native, "_test_window"):
    pytest.skip("requires SCAPKIT_TESTING build", allow_module_level=True)


def ok(result):
    assert result["ok"], result
    assert set(result) == {"ok", "value"}
    return result["value"]


def error(result, code):
    assert result["ok"] is False, result
    assert set(result) == {"ok", "error"}
    value = result["error"]
    assert value["code"] == code, value
    assert set(value) == {"code", "native_domain", "native_code", "dispatch_state", "retry_hint"}
    assert value["dispatch_state"] == "not_dispatched"


def candidate(kind="accessible"):
    return ok(native._test_window(kind))[0]


def opened(kind="accessible"):
    return ok(native._window_open(candidate(kind)["ref"], "capture" if kind == "capture" else "accessible"))["handle"]


def tree(handle, depth=8, count=512):
    return ok(native._window_inspect(handle, "elements", {"max_depth": depth, "max_nodes": count}))


def act(handle, snapshot, index=1, value="synthetic", verified=False):
    function = native._window_action if verified else native._test_window_candidate_action
    return function(handle, snapshot["elements"][index]["ref"], snapshot["tree_epoch"], snapshot["geometry_revision"],
                    "set_value" if index == 1 else "invoke", value if index == 1 else None, {}, "offline-operation", 1.0)


def validate(handle, snapshot):
    return native._window_inspect(handle, "validate_snapshot", {key: snapshot[key] for key in ("tree_epoch", "geometry_revision")})


@pytest.fixture
def handle():
    value = opened()
    yield value
    ok(native._window_close(value))


def test_all_seven_entries_and_complete_native_dtos(handle):
    assert type(native.WINDOW_BACKEND_ABI) is int and native.WINDOW_BACKEND_ABI == 1
    context = ok(native._window_inspect(handle, "capabilities", {}))
    assert set(context) == {"generation", "process_instance", "geometry_revision", "identity_status", "capture", "inspect_elements",
                            "set_value", "invoke", "minimized", "modal", "focus_policy", "subject"}
    snapshot = tree(handle)
    assert [n["depth"] for n in snapshot["elements"]] == [0, 1, 1]
    assert all("value" not in n for n in snapshot["elements"])
    assert all(type(n["enabled"]) is bool for n in snapshot["elements"])
    assert ok(act(handle, snapshot))["effect_state"] == "unknown"
    assert ok(act(handle, snapshot, index=2))["dispatch_state"] == "accepted"
    error(native._window_screenshot(handle, "bgra", 80, 1.0), "unsupported_background_operation")


def test_production_authorization_is_called_and_unverified_grant_rejected(handle):
    error(act(handle, tree(handle), verified=True), "background_not_verified")


def test_one_use_candidate_is_atomic_under_concurrent_native_calls():
    ref = candidate()["ref"]
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: native._window_open(ref, "accessible"), range(32)))
    successes = [r for r in results if r["ok"]]
    assert len(successes) == 1
    for r in results:
        if not r["ok"]:
            error(r, "stale_window")
    ok(native._window_close(successes[0]["value"]["handle"]))


def test_candidate_expiration_is_native_and_consumes_reference():
    ref = candidate("expired")["ref"]
    error(native._window_open(ref, "accessible"), "stale_window")
    error(native._window_open(ref, "accessible"), "stale_window")


def test_capsule_kind_and_element_ownership(handle):
    other = opened()
    try:
        snapshot = tree(other)
        error(act(handle, snapshot), "stale_element")
        with pytest.raises(ValueError):
            native._window_open(candidate()["ref"], "capture")
        with pytest.raises(ValueError):
            native._window_close(snapshot["elements"][0]["ref"])
    finally:
        native._window_close(other)


@pytest.mark.parametrize(("mutation", "expected"), [
    ("destroy", "stale_window"), ("process", "stale_window"), ("layout", "stale_element"),
    ("geometry", "stale_geometry"), ("replace", "stale_element"), ("expired", "stale_element"),
])
def test_live_identity_structure_geometry_invalidation(handle, mutation, expected):
    snapshot = tree(handle)
    ok(native._test_window_mutate(handle, mutation))
    error(validate(handle, snapshot), expected)
    error(act(handle, snapshot), expected)
    if mutation == "process":
        ok(native._test_window_mutate(handle, "restore_process"))
        error(native._window_inspect(handle, "capabilities", {}), "stale_window")


def test_new_snapshot_invalidates_old_native_elements(handle):
    before = tree(handle)
    after = tree(handle)
    assert after["tree_epoch"] > before["tree_epoch"]
    error(act(handle, before), "stale_element")
    ok(act(handle, after))


@pytest.mark.parametrize(("mutation", "expected"), [
    ("minimized", "window_minimized"), ("modal", "modal_blocked"), ("unknown_modal", "modal_blocked"),
    ("revoke", "interference_detected"), ("unknown", "outcome_unknown"),
    ("password", "element_not_editable"), ("disabled", "element_not_editable"),
])
def test_actual_native_action_guards(handle, mutation, expected):
    ok(native._test_window_mutate(handle, mutation))
    error(act(handle, tree(handle)), expected)


def test_unverified_app_rejects_even_testing_candidate_entry():
    h = opened("unverified")
    try:
        error(act(h, tree(h)), "background_not_verified")
    finally:
        native._window_close(h)


@pytest.mark.parametrize(("depth", "count", "expected"), [(0, 512, 1), (8, 1, 1), (1, 2, 2), (8, 512, 3)])
def test_bounded_tree_prefix(handle, depth, count, expected):
    assert len(tree(handle, depth, count)["elements"]) == expected


def test_close_is_idempotent_and_excludes_operations(handle):
    snapshot = tree(handle)
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(r["ok"] for r in pool.map(lambda _: native._window_close(handle), range(16)))
    error(native._window_inspect(handle, "capabilities", {}), "session_closed")
    # Owner remains immutable; closure is checked without dereferencing freed root.
    error(act(handle, snapshot), "stale_element")


def test_native_actor_timeout_quarantine_and_late_return(handle):
    error(native._test_window_mutate(handle, "timeout"), "backend_busy")
    error(native._window_inspect(handle, "capabilities", {}), "backend_busy")
    time.sleep(0.2)
    ok(native._window_inspect(handle, "capabilities", {}))


def test_native_resource_ownership_survives_close_and_releases_on_gc():
    gc.collect()
    before = ok(native._test_window("stats"))
    h = opened()
    snapshot = tree(h)
    element = snapshot["elements"][1]["ref"]
    del snapshot
    ok(native._window_close(h))
    del h
    gc.collect()
    during = ok(native._test_window("stats"))
    assert during["sessions"] == before["sessions"] + 1
    assert during["elements"] == before["elements"] + 1
    del element
    gc.collect()
    assert ok(native._test_window("stats")) == before


@pytest.mark.parametrize("scale", [1, 1.25, 2, 3])
@pytest.mark.parametrize("fit", [0.5, 1, 1.5])
def test_same_frame_point_pixel_mapping(scale, fit):
    content_w, content_h = 200 * fit, 100 * fit
    result = native._test_window_mapping(0, 0, content_w, content_h, scale, fit,
                                         math.ceil(content_w * scale), math.ceil(content_h * scale), 200, 100)
    assert result["mapping_status"] == "verified"
    assert result["image_to_local"] == pytest.approx([1 / scale / fit, 0, 0, 1 / scale / fit, 0, 0])
    assert result["local_rect"]["width"] == 200


@pytest.mark.parametrize("args", [
    (0, 0, 200, 100, float("nan"), 1, 400, 200, 200, 100),
    (0, 0, 200, 100, 2, 0, 400, 200, 200, 100),
    (10, 10, 200, 100, 2, 1, 400, 200, 200, 100),
    (0, 0, 200, 100, 2, 1, 400, 200, 300, 100),
])
def test_invalid_metadata_has_no_guessed_transform(args):
    result = native._test_window_mapping(*args)
    assert result["mapping_status"] == "unavailable"
    assert result["image_to_local"] is None


@pytest.mark.parametrize("bad", [True, -1, 0, 257, "1", float("nan")])
def test_enumeration_bounds_reject_before_any_desktop_call(bad):
    with pytest.raises((TypeError, ValueError, OverflowError)):
        native._window_enumerate("capture", None, bad)


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), 0, -1, 31, "5"])
def test_screenshot_timeout_rejects_before_capture(handle, bad):
    with pytest.raises((TypeError, ValueError, OverflowError)):
        native._window_screenshot(handle, "bgra", 80, bad)


def test_strings_booleans_and_snapshot_options_are_checked(handle):
    with pytest.raises(ValueError):
        native._window_inspect(handle, "capabilities\x00", {})
    with pytest.raises(TypeError):
        native._window_inspect(handle, "elements", {"max_depth": True, "max_nodes": 2})
    with pytest.raises(ValueError):
        native._window_screenshot(handle, "png", 80, 1.0)
    with pytest.raises(TypeError):
        native._window_screenshot(handle, "bgra", True, 1.0)
    snapshot = tree(handle)
    with pytest.raises(ValueError):
        act(handle, snapshot, value="x" * 65537)
    with pytest.raises(ValueError):
        act(handle, snapshot, value="a\x00b")


@pytest.mark.asyncio
async def test_real_native_dtos_through_frozen_public_boundary():
    info = window._info(candidate(), "accessible", native)
    async with await window.open_accessible_window(info.ref) as session:
        snapshot = await session.elements()
        assert len(snapshot.elements) == 3
        capabilities = await session.capabilities()
        assert capabilities.coordinate_input.api_support == "unsupported"
        with pytest.raises(window.WindowError, match="background_not_verified"):
            await session.set_value(snapshot.elements[1].ref, "synthetic")
    assert session.closed and session.cleanup_complete


@pytest.mark.asyncio
async def test_public_cancellation_drains_native_close():
    info = window._info(candidate(), "accessible", native)
    session = await window.open_accessible_window(info.ref)
    task = asyncio.create_task(session.aclose())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert session.closed and session.cleanup_complete


def test_capture_is_independent_of_semantic_root_and_profiles():
    h = opened("capture")
    try:
        context = ok(native._window_inspect(h, "capabilities", {}))
        assert context["capture"] == "supported"
        assert context["inspect_elements"] == "unsupported"
        assert context["subject"] is None
    finally:
        native._window_close(h)


@pytest.mark.parametrize("mode", ["idle", "blank", "suspended", "old", "abandoned"])
def test_real_sample_receiver_rejects_incomplete_old_and_closed_frames(mode):
    result = ok(native._test_window_frame(mode))
    assert not result["received"]
    assert result["pixels"] is None and result["jpeg"] is None


def test_real_pixel_receiver_owns_copy_and_real_jpeg_encoder():
    result = ok(native._test_window_frame("complete"))
    assert result["received"]
    assert result["pixels"] == b"\x7f" * (result["stride"] * result["height"])
    assert window._jpeg_dimensions(result["jpeg"]) == (8, 4)


@pytest.mark.parametrize("mode", ["late_before", "late_after"])
def test_late_start_both_race_orders_close_and_release_stream(mode):
    result = ok(native._test_window_frame(mode))
    assert result == {"finished": True, "detached": True, "stream_released": True}


def test_known_minimized_capture_fails_before_acquiring_a_stream():
    h = opened("capture")
    try:
        ok(native._test_window_mutate(h, "minimized"))
        error(native._window_screenshot(h, "bgra", 80, 1.0), "window_minimized")
    finally:
        native._window_close(h)


@pytest.mark.parametrize("pid", [2**31, 2**32 - 1])
def test_abi_uint32_pid_does_not_wrap_to_negative_process(pid):
    assert ok(native._window_enumerate("capture", pid, 1)) == []
    error(native._window_enumerate("accessible", pid, 1), "identity_unverifiable")


@pytest.mark.parametrize("mode", ["close_complete", "close_incomplete"])
def test_session_close_tracks_its_own_late_capture_cleanup(mode):
    result = ok(native._test_window_frame(mode))
    assert result["first_cleanup"] == (mode == "close_complete")
    assert result["retained_until_complete"] == (mode == "close_incomplete")
    assert result["final_cleanup"] and result["stream_released"] and result["detached"]
