"""Opt-in standalone macOS modifier reproducer; no desktop work at import time."""

import argparse
import asyncio
import ctypes
import importlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

MODIFIERS = {
    "command": 0x100000,
    "shift": 0x20000,
    "option": 0x80000,
    "control": 0x40000,
}
MASK = sum(MODIFIERS.values())
REPO = Path(__file__).resolve().parents[2]
CHINESE = "中文粘贴验证：你好，世界！"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def load_library(repo):
    """Fail closed on an installed/AIOS package or native binary outside this repo."""
    source = repo / "src"
    sys.path.insert(0, str(source))
    paths = {}
    for name in (
        "scapkit_computer_use",
        "scapkit_computer_use.screen_capture_kit",
        "scapkit_computer_use.screen_capture_kit._scapkit",
    ):
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve()
        expected = source.joinpath(*name.split("."))
        if name.endswith("._scapkit"):
            valid = (
                path.parent == expected.parent
                and path.name.startswith("_scapkit.")
                and path.suffix == ".so"
            )
        else:
            valid = path == expected / "__init__.py"
        require(valid, f"Refusing non-repository module {name}: {path}")
        paths[name] = str(path)
    return importlib.import_module("scapkit_computer_use"), paths


class Flags:
    def __init__(self):
        self.cg = ctypes.CDLL(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        self.read = self.cg.CGEventSourceFlagsState
        self.read.argtypes = [ctypes.c_int32]
        self.read.restype = ctypes.c_uint64

    def sample(self):
        # kCGEventSourceStateCombinedSessionState = 0; HIDSystemState = 1.
        return {
            name: int(self.read(value)) for name, value in (("combined", 0), ("hid", 1))
        }


def center(rect, fraction=0.5):
    return {
        "x": round(rect["x"] + rect["width"] * fraction),
        "y": round(rect["y"] + rect["height"] / 2),
    }


def inside(point, rect):
    return (
        rect["x"] <= point["x"] < rect["x"] + rect["width"]
        and rect["y"] <= point["y"] < rect["y"] + rect["height"]
    )


def overlaps(a, b):
    return (
        a["x"] < b["x"] + b["width"]
        and b["x"] < a["x"] + a["width"]
        and a["y"] < b["y"] + b["height"]
        and b["y"] < a["y"] + a["height"]
    )


def geometry(state):
    return {
        name: (window["frame"], window["controls"])
        for name, window in state["windows"].items()
    }


def inserted(window, text):
    require(
        window["input_focused"] and window["selection"],
        "Input field lost its editor/selection",
    )
    # AppKit uses UTF-16 offsets, including when a user changes synthetic text.
    selection = window["selection"]
    data = window["text"].encode("utf-16-le")
    begin = selection["location"] * 2
    end = begin + selection["length"] * 2
    return (data[:begin] + text.encode("utf-16-le") + data[end:]).decode("utf-16-le")


class Harness:
    def __init__(self, library, flags, process, directory, report):
        self.lib, self.flags, self.process = library, flags, process
        self.directory, self.report = directory, report
        self.last = None
        self.seen_event = 0
        self.scroll_next = {"A": "up", "B": "up"}

    def check_flags(self, label, save=True):
        values = self.flags.sample()
        bad = {name: value & MASK for name, value in values.items() if value & MASK}
        if save or bad:
            self.report["flags"].append(
                {
                    "step": label,
                    "time": time.time(),
                    "raw": values,
                    "masked": {name: value & MASK for name, value in values.items()},
                }
            )
        if bad and "first_modifier_error" not in self.report:
            self.report["first_modifier_error"] = self.report["flags"][-1]
            try:
                self.report["first_modifier_error_fixture"] = self.read()
            except Exception as error:  # noqa: BLE001 - preserve evidence if the fixture fails
                self.report["first_modifier_error_fixture_error"] = str(error)
        require(
            not bad,
            f"STOP: unexpected system modifiers at {label}: {bad}. No reset/retry was sent.",
        )

    def read(self):
        require(
            self.process.poll() is None, "Owned fixture exited; inspect fixture.log"
        )
        state = json.loads((self.directory / "state.json").read_text())
        require(
            state["instance"] == self.report["instance"]
            and state["pid"] == self.process.pid,
            "Fixture identity changed; refusing stale/foreign coordinates",
        )
        require(0 <= time.time() - state["time"] < 2, "Fixture heartbeat is stale")
        return state

    def record(self, label, state):
        fresh = [event for event in state["events"] if event["seq"] > self.seen_event]
        if fresh:
            require(
                fresh[0]["seq"] == self.seen_event + 1,
                "Fixture event ring overflow; evidence incomplete",
            )
            self.report["events"].extend(fresh)
            self.seen_event = fresh[-1]["seq"]
        self.report["steps"].append(
            {
                "step": label,
                **{key: value for key, value in state.items() if key != "events"},
            }
        )

    def live(self, state):
        require(
            state["ready"] and state["active"],
            "Fixture is not ready/active; no further input will be sent",
        )
        require(
            set(state["windows"]) == {"A", "B"}
            and all(w["visible"] for w in state["windows"].values()),
            "Both synthetic windows must remain visible",
        )

    async def prepare(self):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            require(
                self.process.poll() is None,
                "Fixture failed to launch; inspect fixture.log",
            )
            if (self.directory / "state.json").exists():
                state = self.read()
                if state["ready"]:
                    self.check_flags("prepared_baseline")
                    self.live(state)
                    windows = state["windows"]
                    require(
                        all(
                            w["screen_id"] == state["main_display_id"]
                            for w in windows.values()
                        ),
                        "Prepare A and B on the main screen for the baseline",
                    )
                    require(
                        not overlaps(windows["A"]["frame"], windows["B"]["frame"]),
                        "Baseline windows overlap",
                    )
                    require(
                        state["key_window"] == "A" and windows["A"]["input_focused"],
                        "A input must be focused",
                    )
                    self.last = state
                    self.record("prepared_baseline", state)
                    return
            await asyncio.sleep(0.05)
        raise RuntimeError(
            "Preparation timed out: click 'A + B prepared: start' in the owned fixture"
        )

    async def step(
        self,
        label,
        action,
        effect=lambda state: True,
        *,
        focus=None,
        event_mask=0,
        moving=None,
    ):
        self.check_flags(label + ":before")
        before = self.read()
        self.live(before)
        require(
            before["key_window"] == self.last["key_window"],
            f"Unexpected focus change before {label}",
        )
        require(
            geometry(before) == geometry(self.last)
            and before["screens"] == self.last["screens"],
            f"Geometry changed before {label}; stopping instead of reusing coordinates",
        )
        require(
            all(
                abs(before["pointer"][axis] - self.last["pointer"][axis]) <= 2
                for axis in ("x", "y")
            ),
            f"Pointer moved unexpectedly before {label}",
        )
        for name in ("A", "B"):
            require(
                all(
                    before["windows"][name][key] == self.last["windows"][name][key]
                    for key in ("text", "selection", "input_focused")
                ),
                f"Editor state changed unexpectedly before {label}",
            )
        self.record(label + ":before", before)
        start_seq = before["event_seq"]
        await action()
        # Sample immediately, BEFORE any other input (even a harmless plain key).
        self.check_flags(label + ":returned")
        returned = time.time()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            self.check_flags(label + ":settling", save=False)
            after = self.read()
            self.live(after)
            require(
                after["screens"] == before["screens"],
                "Display layout changed during the run",
            )
            for name in ("A", "B"):
                if name != moving:
                    require(
                        geometry(after)[name] == geometry(before)[name],
                        f"Unexpected window {name} movement",
                    )
            # Local event flags complement the independent system-state probes.
            for event in after["events"]:
                if event["seq"] > start_seq and "flags" in event:
                    require(
                        not (event["flags"] & MASK & ~event_mask),
                        f"Unexpected event modifiers at {label}: {event}",
                    )
            if (
                after["time"] >= returned + 0.08
                and effect(after)
                and after["key_window"] == (focus or before["key_window"])
            ):
                self.check_flags(label + ":settled")
                self.last = after
                self.record(label + ":after", after)
                print(f"PASS {label}", flush=True)
                return
            await asyncio.sleep(0.01)
        self.record(label + ":failed_effect", after)
        raise RuntimeError(
            f"No expected effect/focus after {label}; stopping without retry"
        )

    async def point(self, name, control, fraction=0.5):
        point = center(self.last["windows"][name]["controls"][control], fraction)
        require(
            any(
                inside(point, screen["visible_frame"])
                for screen in self.last["screens"]
            ),
            "Target is outside visible display area",
        )
        other = "B" if name == "A" else "A"
        require(
            not inside(point, self.last["windows"][other]["frame"]),
            "Target is overlapped by the other fixture window",
        )
        await self.step(
            f"move:{name}:{control}",
            lambda: self.lib.move_mouse(point, smooth=False),
            lambda state: all(
                abs(state["pointer"][axis] - point[axis]) <= 2 for axis in ("x", "y")
            ),
        )
        return point

    async def click(self, name, control):
        count = self.last["windows"][name]["clicks"]
        await self.point(name, control)

        def effect(state):
            window = state["windows"][name]
            if control == "title":
                return state["key_window"] == name
            return (
                window["input_focused"]
                if control == "input"
                else window["clicks"] == count + 1
            )

        await self.step(
            f"click:{name}:{control}",
            lambda: self.lib.mouse_click("left"),
            effect,
            focus=name,
        )

    async def text(self, label, value, action, event_mask=0):
        name = self.last["key_window"]
        expected = inserted(self.last["windows"][name], value)
        await self.step(
            label,
            action,
            lambda state: state["windows"][name]["text"] == expected,
            event_mask=event_mask,
        )

    async def select_all(self):
        name = self.last["key_window"]
        require(
            self.last["windows"][name]["input_focused"],
            "Select All requires the input editor",
        )
        length = len(self.last["windows"][name]["text"].encode("utf-16-le")) // 2
        require(
            length > 0
            and self.last["windows"][name]["selection"]
            != {"location": 0, "length": length},
            "Select All needs a nonempty, not-already-selected field",
        )
        await self.step(
            "combo:cmd_a",
            lambda: self.lib.key_combo("a", {"command"}),
            lambda state: (
                state["windows"][name]["selection"] == {"location": 0, "length": length}
            ),
            event_mask=MODIFIERS["command"],
        )

    async def plain_down_up(self):
        async def pair():
            self.lib.keyboard_click_action("x", "down")
            try:
                await asyncio.sleep(0.01)
            finally:
                # Matching up belongs to this test sequence; never a modifier reset.
                self.lib.keyboard_click_action("x", "up")

        await self.text("raw_plain_x:down_up", "x", pair)

    async def paste(self):
        await self.step(
            "set_synthetic_clipboard", lambda: self.lib.set_clipboard(CHINESE)
        )
        await self.text(
            "paste:chinese", CHINESE, self.lib.clipboard_paste, MODIFIERS["command"]
        )

    async def plain_checks(self, label):
        # The next input sequence is a title-bar click, before any plain key
        # could hide the old shared-source modifier contamination.
        name = "B" if self.last["key_window"] == "A" else "A"
        await self.click(name, "title")
        await self.click(name, "button")
        await self.click(name, "input")
        await self.text(
            label + ":switched_plain_x", "x", lambda: self.lib.keyboard_click("x")
        )
        old_y = self.last["windows"][name]["scroll_y"]
        await self.point(name, "scroll")
        direction = self.scroll_next[name]
        self.scroll_next[name] = "down" if direction == "up" else "up"
        await self.step(
            label + ":plain_scroll",
            lambda: self.lib.mouse_scroll(direction, 4),
            lambda state: abs(state["windows"][name]["scroll_y"] - old_y) > 1,
        )
        start = await self.point(name, "drag", 0.2)
        dest = center(self.last["windows"][name]["controls"]["drag"], 0.8)
        old = self.last["windows"][name]["drag"]["completed"]

        def dragged(state):
            drag = state["windows"][name]["drag"]
            return drag["completed"] == old + 1 and all(
                abs(drag[point].get(axis, float("inf")) - expected[axis]) <= 4
                for point, expected in (("start", start), ("end", dest))
                for axis in ("x", "y")
            )

        await self.step(
            label + ":plain_drag", lambda: self.lib.mouse_drag(dest), dragged
        )

    async def cross_screen(self):
        state = self.last
        main = next(
            screen
            for screen in state["screens"]
            if screen["id"] == state["main_display_id"]
        )
        frame = state["windows"]["B"]["frame"]
        secondary = next(
            (
                screen
                for screen in state["screens"]
                if screen["id"] != main["id"]
                and not overlaps(screen["frame"], main["frame"])
                and screen["visible_frame"]["width"] >= frame["width"] + 20
                and screen["visible_frame"]["height"] >= frame["height"] + 20
            ),
            None,
        )
        if secondary is None:
            self.report["unverified"].append(
                "Two-display switches and bidirectional window drag: no suitable extended secondary display"
            )
            return
        original = dict(frame)
        area = secondary["visible_frame"]
        destination = {
            "x": area["x"] + (area["width"] - frame["width"]) / 2,
            "y": area["y"] + (area["height"] - frame["height"]) / 2,
        }
        for label, screen_id, origin in (
            ("outbound", secondary["id"], destination),
            ("return", main["id"], original),
        ):
            await self.click("B", "input")
            start = await self.point("B", "title")
            current = self.last["windows"]["B"]["frame"]
            dest = {
                axis: round(start[axis] + origin[axis] - current[axis])
                for axis in ("x", "y")
            }
            await self.step(
                "cross_display_drag:" + label,
                lambda dest=dest: self.lib.mouse_drag(dest),
                lambda value, screen_id=screen_id, origin=origin: (
                    value["windows"]["B"]["screen_id"] == screen_id
                    and all(
                        abs(value["windows"]["B"]["frame"][axis] - origin[axis]) < 15
                        for axis in ("x", "y")
                    )
                ),
                moving="B",
                focus="B",
            )
            self.report["coverage"].append("cross_display_drag:" + label)
            # B -> A -> B with actual focus and typed-text assertions.
            await self.click("B", "input")
            await self.plain_checks("after_cross_display:" + label)
            await self.click("B", "input")
            await self.text(
                "switch_back_to_B:" + label, "x", lambda: self.lib.keyboard_click("x")
            )
        self.report["coverage"].append("two_display_switches:B_to_A_and_A_to_B")

    async def immediate_checks(self):
        """Create the next mouse event immediately after shortcut completion."""
        variants = [
            ("command", {"command"}),
            ("shift", {"command", "shift"}),
            ("option", {"command", "option"}),
            ("control", {"command", "control"}),
            ("all", set(MODIFIERS)),
            ("paste", {"command"}),
        ]
        await self.step("immediate:clipboard", lambda: self.lib.set_clipboard(CHINESE))
        for trial in range(3):
            for label, modifiers in variants:
                name = self.last["key_window"]
                other = "B" if name == "A" else "A"
                await self.click(name, "input")
                window = self.last["windows"][name]
                expected_text = inserted(window, CHINESE) if label == "paste" else None
                old = window["shortcuts"].get(label, 0)
                point = center(self.last["windows"][other]["controls"]["title"])
                start_seq = self.last["event_seq"]

                async def action(
                    label=label, modifiers=modifiers, trial=trial, point=point
                ):
                    if label == "paste":
                        await self.lib.clipboard_paste()
                    else:
                        await self.lib.key_combo("k", modifiers)
                    self.check_flags(f"immediate:{trial}:{label}:shortcut_returned")
                    # No sleep, fixture polling or other keyboard input here.
                    await self.lib.move_mouse(point, smooth=False)
                    await self.lib.mouse_click("left")

                def effect(
                    state, name=name, label=label, expected_text=expected_text, old=old
                ):
                    w = state["windows"][name]
                    return (
                        w["text"] == expected_text
                        if label == "paste"
                        else w["shortcuts"].get(label, 0) == old + 1
                    )

                await self.step(
                    f"immediate:{trial}:{label}",
                    action,
                    effect,
                    focus=other,
                    event_mask=sum(MODIFIERS[m] for m in modifiers),
                )
                mouse_events = [
                    e
                    for e in self.last["events"]
                    if e["seq"] > start_seq and e["kind"] in {"event:1", "event:2"}
                ]
                require(
                    mouse_events and all(e["flags"] & MASK == 0 for e in mouse_events),
                    "Immediate title click inherited shortcut modifiers",
                )
        self.report["coverage"].append("immediate_shortcut_to_title_click:18_trials")

    async def lifecycle_checks(self):
        """Real public-wrapper interruption and explicit raw-HID ownership."""

        async def wait_flags(expected):
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                values = self.flags.sample()
                if all(v & MASK == expected for v in values.values()):
                    return
                await asyncio.sleep(0.001)
            raise RuntimeError(
                f"Raw owned modifier did not reach expected state {expected:#x}: {values}"
            )

        def held_sample(label, expected):
            values = self.flags.sample()
            self.report["flags"].append(
                {"step": label, "raw": values, "expected_mask": expected}
            )
            require(
                all(v & MASK == expected for v in values.values()),
                f"External hold was changed at {label}: {values}",
            )

        for modifier, flag in MODIFIERS.items():
            for exit_path in ("error", "cancel"):

                async def interrupt(modifier=modifier, exit_path=exit_path):
                    if exit_path == "cancel":
                        task = asyncio.create_task(
                            self.lib.key_combo("k", {"command", modifier})
                        )
                        await asyncio.sleep(0)
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                        else:
                            raise AssertionError("Expected interruption to propagate")
                        require(
                            task.cancelled(),
                            "Cancellation was replaced by cleanup failure",
                        )
                    else:
                        wrapper = importlib.import_module(
                            "scapkit_computer_use.screen_capture_kit"
                        )
                        original = wrapper.asyncio

                        async def fail_sleep(delay):
                            await asyncio.sleep(0.025)
                            raise RuntimeError("manual interruption")

                        wrapper.asyncio = SimpleNamespace(
                            sleep=fail_sleep,
                            get_running_loop=asyncio.get_running_loop,
                            shield=asyncio.shield,
                            CancelledError=asyncio.CancelledError,
                        )
                        try:
                            try:
                                await self.lib.key_combo("k", {"command", modifier})
                            except RuntimeError as error:
                                require(
                                    str(error) == "manual interruption",
                                    "Unexpected failure",
                                )
                            else:
                                raise AssertionError(
                                    "Expected interruption to propagate"
                                )
                        finally:
                            wrapper.asyncio = original

                await self.step(
                    f"lifecycle:{modifier}:{exit_path}",
                    interrupt,
                    event_mask=flag | MODIFIERS["command"],
                )
                other = "B" if self.last["key_window"] == "A" else "A"
                await self.click(other, "title")

            async def raw_hold(modifier=modifier, flag=flag):
                self.lib.keyboard_click_action(modifier, "down", {modifier})
                try:
                    await wait_flags(flag)
                    held_sample(f"raw:{modifier}:before_shortcut", flag)
                    await self.lib.key_combo("k", {"command"})
                    held_sample(f"raw:{modifier}:after_shortcut", flag)
                finally:
                    # Matching release for THIS test's raw hold, not a global reset.
                    self.lib.keyboard_click_action(modifier, "up")
                    await wait_flags(0)

            await self.step(
                f"lifecycle:{modifier}:raw_hold",
                raw_hold,
                event_mask=flag | MODIFIERS["command"],
            )
        self.report["coverage"].append(
            "live_error_cancel_and_raw_HID_holds:four_modifiers"
        )
        self.report["unverified"] = [
            "Physical hardware holds and concurrent human presses/releases require human participation",
            "Standalone modifier-key clicks are covered offline only",
        ]

    async def run(self, mode):
        await self.prepare()
        if mode == "lifecycle":
            await self.immediate_checks()
            await self.lifecycle_checks()
            return
        if mode != "paste-only":
            await self.select_all()
            self.report["coverage"].append("key_combo:cmd_a_selection")
        if mode == "full":
            await self.plain_checks("after_combo")
        if mode != "combo-only":
            await self.paste()
            self.report["coverage"].append("clipboard_paste:exact_chinese")
        if mode != "full":
            name = "B" if self.last["key_window"] == "A" else "A"
            await self.click(name, "title")
            self.report["coverage"].append("title_click_after_trigger:actual_focus")
            self.report["unverified"].append(
                "Reproduction mode: full plain-action, modifier-variant, and multiple-display acceptance not run"
            )
            return
        await self.plain_checks("after_paste")
        variants = [
            ("command", {"command"}),
            ("shift", {"command", "shift"}),
            ("option", {"command", "option"}),
            ("control", {"command", "control"}),
            ("all", set(MODIFIERS)),
        ]
        for label, modifiers in variants:
            name = self.last["key_window"]
            old = self.last["windows"][name]["shortcuts"].get(label, 0)
            method = (
                self.lib.keyboard_click if label == "command" else self.lib.key_combo
            )
            await self.step(
                "shortcut:" + label,
                lambda method=method, modifiers=modifiers: method("k", modifiers),
                lambda state, name=name, label=label, old=old: (
                    state["windows"][name]["shortcuts"].get(label, 0) == old + 1
                ),
                event_mask=sum(MODIFIERS[value] for value in modifiers),
            )
            await self.plain_checks("after_shortcut:" + label)
        await self.plain_down_up()
        await self.plain_checks("after_explicit_down_up")
        self.report["coverage"].append(
            "full_same_display:shortcuts_paste_plain_actions_and_paired_down_up"
        )
        await self.cross_screen()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-desktop",
        action="store_true",
        help="Authorize owned GUI launch and real input; paste modes overwrite the clipboard",
    )
    parser.add_argument(
        "--mode",
        choices=(
            "combo",
            "paste",
            "combo-only",
            "paste-only",
            "combo-paste",
            "full",
            "lifecycle",
        ),
        default="full",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=REPO,
        help="Repository checkout supplying src/ and its in-place native extension",
    )
    parser.add_argument(
        "--python",
        type=Path,
        help="Re-execute with this Python executable, without installing or changing any environment",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="New report directory (default: unique directory under system temp)",
    )
    args = parser.parse_args()
    if not args.allow_desktop:
        parser.error(
            "No desktop authorization. Read tests/manual/README.md, then explicitly opt in with --allow-desktop."
        )
    if platform.system() != "Darwin":
        parser.error("This manual harness requires macOS")
    if args.python:
        interpreter = args.python.expanduser().absolute()
        require(
            interpreter.is_file(), f"Python executable does not exist: {interpreter}"
        )
        command = [
            str(interpreter),
            "-I",
            str(Path(__file__).resolve()),
            "--allow-desktop",
            "--mode",
            args.mode,
            "--repo",
            str(args.repo.resolve()),
        ]
        if args.output:
            command += ["--output", str(args.output.resolve())]
        # No ambient site-packages/PYTHONPATH from another project and no recursive --python.
        os.execve(
            str(interpreter),
            command,
            {
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "HOME": str(Path.home()),
                "LANG": "en_US.UTF-8",
            },
        )
    args.mode = {"combo": "combo-only", "paste": "paste-only"}.get(args.mode, args.mode)
    directory = (
        args.output.resolve()
        if args.output
        else Path(tempfile.mkdtemp(prefix="scapkit-modifiers-"))
    )
    if args.output:
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    report = {
        "instance": str(uuid.uuid4()),
        "mode": args.mode,
        "status": "failed",
        "flags": [],
        "steps": [],
        "events": [],
        "coverage": [],
        "modifier_mask": MASK,
        "python": sys.executable,
        "repository": str(args.repo.resolve()),
        "python_version": sys.version,
        "unverified_user_held_modifiers": [
            "Physical left/right modifier holds during a synthetic shortcut/paste",
            "User presses/releases a modifier concurrently with a synthetic action",
        ],
        "unverified": [
            "Cancellation during a held chord, standalone modifier keys, and legacy raw flags-bearing chords: not exercised"
        ],
    }
    process = None
    harness = None
    flags = None
    try:
        flags = Flags()
        baseline = flags.sample()
        report["flags"].append(
            {
                "step": "before_launch",
                "time": time.time(),
                "raw": baseline,
                "masked": {name: value & MASK for name, value in baseline.items()},
            }
        )
        require(
            not any(value & MASK for value in baseline.values()),
            "Modifiers already held before launch; stopped without clearing",
        )
        library, report["module_paths"] = load_library(args.repo.resolve())
        # Fixed minimal environment, never a copy of the Python/AIOS process environment.
        child_env = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(Path.home()),
            "TMPDIR": str(directory),
            "LANG": "en_US.UTF-8",
        }
        binary = directory / "modifier_fixture"
        with (directory / "compile.log").open("w") as log:
            subprocess.run(
                [
                    "/usr/bin/xcrun",
                    "swiftc",
                    str(Path(__file__).with_name("modifier_fixture.swift")),
                    "-o",
                    str(binary),
                ],
                check=True,
                stdout=log,
                stderr=log,
                env=child_env,
            )
        print(
            f"Reports: {directory}\nPrepare BOTH synthetic windows A/B on the main display, unobscured.\n"
            "Use ABC input, Caps Lock off; release modifiers. Click 'A + B prepared: start' in A,\n"
            "then leave the keyboard/mouse alone. Paste modes replace clipboard text with synthetic Chinese.",
            flush=True,
        )
        with (directory / "fixture.log").open("w") as log:
            process = subprocess.Popen(
                [
                    str(binary),
                    "--allow-desktop",
                    str(directory / "state.json"),
                    report["instance"],
                ],
                stdout=log,
                stderr=log,
                env=child_env,
            )
        harness = Harness(library, flags, process, directory, report)
        asyncio.run(harness.run(args.mode))
        report["status"] = "passed_selected_checks"
    except (Exception, KeyboardInterrupt) as error:  # noqa: BLE001 - write report and stop owned child on any failure
        report["error"] = f"{type(error).__name__}: {error}"
        print(report["error"], file=sys.stderr)
    finally:
        if harness is not None:
            # Read-only failure evidence. No Escape, reset, clipboard restore, or UI retry.
            # Allow the fixture's event/selection heartbeat to catch up, with no input.
            time.sleep(0.12)
            report["final_flags"] = harness.flags.sample()
            if any(value & MASK for value in report["final_flags"].values()):
                report["status"] = "failed"
                report.setdefault(
                    "error",
                    "Unexpected modifiers at final read-only sample; no reset was sent",
                )
            try:
                report["final_fixture"] = harness.read()
            except Exception as error:  # noqa: BLE001 - fixture failure is part of the report
                report["final_fixture_error"] = str(error)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if flags is not None:
            report["flags_after_fixture_termination"] = flags.sample()
            if any(
                value & MASK
                for value in report["flags_after_fixture_termination"].values()
            ):
                report["status"] = "failed"
                report.setdefault(
                    "error",
                    "System modifiers remain after owned fixture termination; intentionally not cleared",
                )
        (directory / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
        print(f"{report['status']}: {directory / 'report.json'}", flush=True)
    return 0 if report["status"] == "passed_selected_checks" else 1


if __name__ == "__main__":
    raise SystemExit(main())
