"""Native event/state regressions. The compiled receiver cannot post desktop input.

Real CoreGraphics event objects pass through the production C implementation;
only delivery/warping is replaced with an offline source-state receiver. This
does NOT establish WindowServer delivery, actual focus or physical key behavior.
"""

import asyncio
import gc
import importlib.util
import subprocess
import sysconfig
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from scapkit_computer_use import screen_capture_kit as capture

ROOT = Path(__file__).resolve().parents[1]
MASK = 0x1E0000


@pytest.fixture(scope="session")
def native_probe(tmp_path_factory):
    directory = tmp_path_factory.mktemp("input-probe")
    binary = directory / ("_input_probe" + sysconfig.get_config_var("EXT_SUFFIX"))
    build = subprocess.run(
        [
            "xcrun",
            "clang",
            "-bundle",
            "-undefined",
            "dynamic_lookup",
            "-std=c11",
            "-DPY_SSIZE_T_CLEAN",
            "-Werror",
            "-mmacosx-version-min=12.3",
            "-I" + sysconfig.get_path("include"),
            "-I" + str(ROOT / "native_code/osx/include"),
            str(ROOT / "tests/native/input_probe.c"),
            "-framework",
            "ApplicationServices",
            "-framework",
            "CoreGraphics",
            "-o",
            str(binary),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    spec = importlib.util.spec_from_file_location("_input_probe", binary)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def receiver(native_probe, monkeypatch):
    gc.collect()
    native_probe.reset()
    for wrapper, native in {
        "keyboard_click_c": "keyboard_click",
        "mouse_click_action": "mouse_click",
        "mouse_scroll_c": "mouse_scroll",
        "move_mouse_c": "move_mouse",
        "drag_mouse_c": "drag_mouse",
        "get_mouse_position": "get_mouse_position",
        "keyboard_begin_c": "keyboard_begin",
        "keyboard_end_c": "keyboard_end",
    }.items():
        monkeypatch.setattr(capture, wrapper, getattr(native_probe, native))
    return native_probe


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "modifier,flag,operation",
    [
        ("command", 0x100000, "combo"),
        ("shift", 0x20000, "combo"),
        ("option", 0x80000, "combo"),
        ("control", 0x40000, "combo"),
        ("command", 0x100000, "paste"),
    ],
)
async def test_shortcut_releases_source_state_before_followup(
    receiver, modifier, flag, operation
):
    if operation == "paste":
        await capture.clipboard_paste()
    else:
        await capture.key_combo("a", {modifier})
    state = receiver.state()
    assert any(
        kind == 10 and flags & flag for _, kind, flags, _, _ in state["events"]
    ), "shortcut must still carry its modifier"
    assert state["flags"] & MASK == 0, (
        "shortcut contaminated the source's modifier state"
    )
    assert not state["held"], "synthetic key remains down"
    before = len(state["events"])
    await capture.mouse_click("left")
    await capture.keyboard_click("b")
    await capture.mouse_scroll("down", 1)
    await capture.mouse_drag({"x": -50, "y": 40})
    assert all(
        flags & MASK == 0 for _, _, flags, _, _ in receiver.state()["events"][before:]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("modifier", ["command", "shift", "option", "control"])
@pytest.mark.parametrize("exit_path", ["cancel", "error"])
async def test_shortcut_releases_state_when_interrupted(
    receiver, monkeypatch, modifier, exit_path
):
    exception = (
        asyncio.CancelledError()
        if exit_path == "cancel"
        else RuntimeError("await failed")
    )

    async def interrupted_sleep(delay):
        raise exception

    monkeypatch.setattr(
        capture,
        "asyncio",
        SimpleNamespace(
            sleep=interrupted_sleep,
            get_running_loop=asyncio.get_running_loop,
            shield=asyncio.shield,
            CancelledError=asyncio.CancelledError,
        ),
    )
    with pytest.raises(type(exception)):
        await capture.keyboard_click("a", {modifier})
    assert receiver.state()["flags"] & MASK == 0
    assert not receiver.state()["held"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "flag,key", [(0x100000, 55), (0x20000, 56), (0x80000, 58), (0x40000, 59)]
)
@pytest.mark.parametrize("held_by", ["hardware", "raw_api"])
async def test_shortcut_does_not_release_other_sources(receiver, flag, key, held_by):
    if held_by == "hardware":
        receiver.reset(0x20000000 | flag)
    else:
        receiver.keyboard_click(key, "down", flag)
    await capture.key_combo("a", {"command", "shift", "option", "control"})
    gc.collect()
    assert receiver.state()["flags"] & MASK == flag
    assert receiver.state()["live_objects"] == 0
    await capture.mouse_click("left")
    assert receiver.state()["events"][-1][2] & MASK == flag
    if held_by == "raw_api":
        # The explicit hold persists until its owner sends its own release.
        assert any(held_key == key for _, held_key in receiver.state()["held"])
        receiver.keyboard_click(key, "up", 0)
        assert receiver.state()["flags"] & MASK == 0


@pytest.mark.asyncio
async def test_modifiers_survive_main_key_up_then_release_in_reverse_order(receiver):
    await capture.key_combo("a", {"command", "shift"})
    events = receiver.state()["events"]
    assert [(kind, flags & 0x9E0000, key) for _, kind, flags, key, _ in events] == [
        (12, 0x100000, 55),
        (12, 0x120000, 56),
        (10, 0x120000, 0),
        (11, 0x120000, 0),
        (12, 0x100000, 56),
        (12, 0, 55),
    ]
    assert len({source for source, *_ in events}) == 1
    assert events[0][0] not in (0, 1), "shortcut must not own the session or HID table"


@pytest.mark.parametrize("fail_at", range(1, 13))
def test_all_release_events_are_allocated_before_any_input(receiver, fail_at):
    receiver.fail_event(fail_at)
    with pytest.raises(OSError):
        receiver.keyboard_begin(0, 0x9E0000)
    assert receiver.state()["events"] == []
    assert receiver.state()["live_objects"] == 0


@pytest.mark.parametrize("flags", [-1, 2**64, 2**100])
def test_shortcut_flags_cannot_wrap_to_valid_input(receiver, flags):
    with pytest.raises(OverflowError):
        receiver.keyboard_begin(0, flags)
    assert receiver.state()["events"] == []


def test_overlapping_shortcuts_own_independent_states(receiver):
    first = receiver.keyboard_begin(0, 0x100000)
    second = receiver.keyboard_begin(9, 0x100000)
    try:
        receiver.keyboard_end(first)
        # Sources own distinct key state; the global session is last-event state,
        # not a union. Desktop effects of overlapping shortcuts are not atomic.
        assert receiver.state()["held"]
        receiver.keyboard_end(second)
        assert receiver.state()["flags"] & MASK == 0
        assert not receiver.state()["held"]
        count = len(receiver.state()["events"])
        receiver.keyboard_end(second)
        assert len(receiver.state()["events"]) == count
    finally:
        receiver.keyboard_end(first)
        receiver.keyboard_end(second)
    del first, second
    gc.collect()
    assert receiver.state()["live_objects"] == 0


def test_abandoned_stroke_releases_owned_keys(receiver):
    stroke = receiver.keyboard_begin(0, 0x1E0000)
    assert receiver.state()["flags"] & MASK == 0x1E0000
    del stroke
    gc.collect()
    assert receiver.state()["flags"] & MASK == 0
    assert not receiver.state()["held"]
    assert receiver.state()["live_objects"] == 0


def test_closed_stroke_drops_native_objects_before_capsule_collection(receiver):
    stroke = receiver.keyboard_begin(0, 0x100000)
    receiver.keyboard_end(stroke)
    assert receiver.state()["live_objects"] == 0
    receiver.keyboard_end(stroke)


def test_concurrent_release_is_idempotent_and_does_not_free_live_capsule(receiver):
    stroke = receiver.keyboard_begin(0, 0x1E0000)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: receiver.keyboard_end(stroke), range(64)))
    state = receiver.state()
    assert len(state["events"]) == 10
    assert not state["held"]
    assert state["flags"] & MASK == 0
    assert state["live_objects"] == 0


@pytest.mark.asyncio
async def test_actual_task_cancellation_releases_modifier_state(receiver):
    task = asyncio.create_task(capture.key_combo("a", {"command", "shift"}))
    await asyncio.sleep(0)
    assert receiver.state()["flags"] & MASK == 0x120000
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert receiver.state()["flags"] & MASK == 0
    assert not receiver.state()["held"]
    assert receiver.state()["live_objects"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key",
    [
        "command",
        "shift",
        "option",
        "control",
        "right_shift",
        "right_option",
        "right_control",
        "fn",
    ],
)
async def test_modifier_named_key_is_balanced(receiver, key):
    await capture.keyboard_click(key)
    assert receiver.state()["flags"] & 0x9E0000 == 0
    assert not receiver.state()["held"]
    assert receiver.state()["live_objects"] == 0


def test_shortcut_waits_for_its_release_to_be_processed(receiver):
    receiver.delay_delivery(4)
    stroke = receiver.keyboard_begin(0, 0x100000)
    receiver.keyboard_end(stroke)
    assert receiver.state()["pending"] == 0, (
        "returned before WindowServer processed its own releases"
    )
    assert receiver.state()["counter_polls"] >= 4
    assert receiver.state()["flags"] & MASK == 0


def test_shortcut_never_writes_its_modifiers_into_hid_state(receiver):
    stroke = receiver.keyboard_begin(0, 0x100000)
    try:
        assert receiver.state()["hid_flags"] & MASK == 0
    finally:
        receiver.keyboard_end(stroke)


@pytest.mark.parametrize("start,end", [(0, 0x20000), (0x20000, 0), (0x100000, 0)])
def test_cleanup_uses_current_external_state_not_a_stale_snapshot(receiver, start, end):
    receiver.reset(0x20000000 | start)
    stroke = receiver.keyboard_begin(0, 0x100000)
    receiver.hardware(0x20000000 | end)
    receiver.keyboard_end(stroke)
    assert receiver.state()["hid_flags"] & MASK == end
    assert receiver.state()["flags"] & MASK == end


def test_missing_delivery_acknowledgment_is_reported_without_reposting(receiver):
    receiver.delay_delivery(-1)
    stroke = receiver.keyboard_begin(0, 0x100000)
    with pytest.raises(TimeoutError, match="not acknowledged"):
        receiver.keyboard_end(stroke)
    state = receiver.state()
    assert state["pending"] == 4
    assert state["live_objects"] == 0
    with pytest.raises(TimeoutError, match="not acknowledged"):
        receiver.keyboard_end(stroke)
    assert len(receiver.state()["events"]) == 4


def test_clicking_an_externally_held_modifier_does_not_release_it(receiver):
    receiver.reset(0x20020000)
    stroke = receiver.keyboard_begin(56, 0)
    receiver.keyboard_end(stroke)
    assert receiver.state()["flags"] == 0x20020000
    assert receiver.state()["hid_flags"] == 0x20020000
    assert receiver.state()["events"] == []
    assert receiver.state()["live_objects"] == 0


@pytest.mark.asyncio
async def test_drag_posts_motion_events_between_button_down_and_up(receiver):
    await capture.key_combo("a", {"command"})
    before = len(receiver.state()["events"])
    await capture.mouse_drag({"x": -200, "y": 120})
    events = receiver.state()["events"][before:]
    assert events[0][1] == 1 and events[-1][1] == 2
    assert any(e[1] == 6 for e in events), (
        "cursor warping alone does not drag an AppKit window"
    )
    assert all(e[2] & MASK == 0 for e in events)
    assert receiver.state()["cursor"] == (-200, 120)


@pytest.mark.asyncio
async def test_missing_acknowledgment_does_not_block_the_event_loop(receiver):
    receiver.delay_delivery(-1)
    start = asyncio.get_running_loop().time()
    task = asyncio.create_task(capture.key_combo("a", {"command"}))
    await asyncio.sleep(0.03)
    try:
        assert asyncio.get_running_loop().time() - start < 0.5
        assert not task.done(), "native acknowledgment blocked the loop until timeout"
    finally:
        with pytest.raises(TimeoutError):
            await task


@pytest.mark.asyncio
async def test_cancellation_survives_cleanup_timeout(receiver):
    receiver.delay_delivery(-1)
    task = asyncio.create_task(capture.key_combo("a", {"command"}))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert task.cancelled()
    # Python 3.10 Task.result may wrap cancellation in another CancelledError;
    # the original error and cleanup cause must remain in the exception chain.
    error = caught.value
    chain = []
    while error is not None and all(error is not item for item in chain):
        chain.append(error)
        error = error.__cause__ or error.__context__
    assert any(isinstance(error, TimeoutError) for error in chain)
    assert receiver.state()["live_objects"] == 0


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_owned_release(receiver):
    receiver.delay_delivery(80)
    task = asyncio.create_task(capture.key_combo("a", {"command"}))
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0.02)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert receiver.state()["pending"] == 0
    assert receiver.state()["flags"] & MASK == 0


def test_dropped_main_key_up_cannot_be_hidden_by_modifier_release_or_source_free(
    receiver,
):
    receiver.drop_main_up()
    stroke = receiver.keyboard_begin(0, 0x100000)
    with pytest.raises(TimeoutError, match="not acknowledged"):
        receiver.keyboard_end(stroke)
    state = receiver.state()
    assert state["dropped_main_up"] == 1
    assert state["flags"] & MASK == 0
    assert any(key == 0 for _, key in state["held"])
    assert state["live_objects"] == 0


def test_independent_strokes_can_be_destroyed_concurrently_without_memory_races(
    receiver,
):
    def one_stroke(index):
        stroke = receiver.keyboard_begin(0, 0x100000)
        receiver.keyboard_end(stroke)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(one_stroke, range(64)))
    state = receiver.state()
    assert len(state["events"]) == 64 * 4
    assert not state["overflowed"]
    assert not state["held"]
    assert state["live_objects"] == 0
