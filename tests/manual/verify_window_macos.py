"""Explicitly authorized macOS fixture probe; never promotes a profile.

Run only after offline checks, with --desktop, on SCAPKIT_TESTING builds.
Launches its own fixture, operates only its PID/root, never reads a clipboard,
never focuses/activates/restores any window, and terminates only its own child.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]


def native_ok(result):
    if not result["ok"]:
        raise RuntimeError(result["error"]["code"] + ":" + str(result["error"]["native_code"]))
    return result["value"]


def state(path):
    return json.loads(path.read_text())


def record(snapshot, slot):
    return next(w for w in snapshot["windows"] if w["slot"] == slot and not w["closed"])


async def until(path, predicate):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        try:
            snapshot = state(path)
            if predicate(snapshot):
                return snapshot
        except (FileNotFoundError, StopIteration):
            pass
        await asyncio.sleep(0.05)
    raise AssertionError("fixture observation deadline exceeded")


async def exercise(report):
    from scapkit_computer_use import window
    from scapkit_computer_use import window_profiles as profiles
    from scapkit_computer_use.screen_capture_kit import _scapkit as native

    assert Path(native.__file__).resolve().parent == ROOT / "src/scapkit_computer_use/screen_capture_kit"
    report["permissions"] = {name: native.check_permission(name) for name in ("ScreenCapture", "Accessibility")}
    # Never request permission, including through a capture call when preflight fails.
    if not report["permissions"]["ScreenCapture"]:
        report["desktop"] = "not_run: ScreenCapture permission unavailable"
        return
    assert hasattr(native, "_test_window_candidate_action"), "requires testing extension"
    binary = ROOT / "build/window-fixtures/window-fixture-macos"
    header = (ROOT / "native_code/osx/include/window_fixture_identity.h").read_text()
    expected = re.search(r'#define SW_FIXTURE_EXECUTABLE "([a-f0-9]{64})"', header).group(1)
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == expected, "fixture executable differs from reviewed pin"
    assert hashlib.sha256((ROOT / "tests/manual/window_fixture_macos.m").read_bytes()).hexdigest() == re.search(
        r'#define SW_FIXTURE_SOURCE "([a-f0-9]{64})"', header).group(1)
    with tempfile.TemporaryDirectory(prefix="scapkit-window-owned-") as temp:
        directory = Path(temp) / "state"
        output = Path(temp) / "fixture.log"
        with output.open("wb") as log:
            # Fresh minimal environment, no inherited Python/shell configuration.
            child = subprocess.Popen([str(binary), "--state-dir", str(directory), "--role", "a", "--background"],
                                     env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"}, stdout=log, stderr=log)
        capture = None
        semantic = None
        try:
            file = directory / "state.json"
            report["phase"] = "fixture_start"
            initial = await until(file, lambda d: len(d["windows"]) == 2)
            print("fixture_ready", flush=True)
            report["phase"] = "capture"
            a1, a2 = record(initial, "A1"), record(initial, "A2")
            report["startup_active"] = initial["active"]
            assert initial["pid"] == child.pid
            end = time.monotonic() + 5
            selected = []
            while time.monotonic() < end and not selected:
                choices = await window.list_windows(pid=child.pid)
                selected = [w for w in choices if w.native_id == a1["native_id"]]
                if not selected:
                    await asyncio.sleep(0.1)
            report["capture_candidates"] = len(choices)
            report["fixture_window_number"] = a1["native_id"]
            assert len(selected) == 1, "fixture window number absent or ambiguous in SCK enumeration"
            print("capture_enumerated", flush=True)
            capture = await window.open_window(selected[0].ref)
            print("capture_opened", flush=True)
            report["capture_open_without_ax_root"] = "passed"
            report["screenshots"] = {}
            for fmt in ("bgra", "jpeg"):
                try:
                    frame = await capture.screenshot(format=fmt)
                    print("screenshot_" + fmt + "_received", flush=True)
                    report["screenshots"][fmt] = {"status": "passed", "width": frame.image_width,
                                                 "height": frame.image_height, "mapping": frame.mapping_status}
                except window.WindowError as error:
                    report["screenshots"][fmt] = {"status": "failed", "code": error.code}
            if report["permissions"]["Accessibility"]:
                report["phase"] = "accessibility"
                references = native_ok(native._window_enumerate("accessible", child.pid, 256))
                selected = [w for w in references if w["native_id"] == a1["accessibility"]["window"]]
                assert len(selected) == 1
                semantic = native_ok(native._window_open(selected[0]["ref"], "accessible"))["handle"]
                context = native_ok(native._window_inspect(semantic, "capabilities", {}))
                assert context["subject"] is not None, "exact fixture/source/bridge identity was not established"
                profile = profiles.BackgroundProfile(profile_id="macos-fixture-candidate-v1", **context["subject"], actions=(
                    profiles.ProfileAction("fixture.text", "AXTextField", "set_value"),
                    profiles.ProfileAction("fixture.increment", "AXButton", "invoke"),
                ))
                assert profiles.register_profile(profile).status == "not_run"
                report["exact_bridge_identity"] = "passed"
                for identifier, action, value in (("fixture.text", "set_value", "SCAPKIT synthetic replacement"),
                                                  ("fixture.increment", "invoke", None)):
                    snapshot = native_ok(native._window_inspect(semantic, "elements", {"max_depth": 8, "max_nodes": 512}))
                    nodes = [e for e in snapshot["elements"] if e["identifier"] == identifier]
                    assert len(nodes) == 1
                    args = (semantic, nodes[0]["ref"], snapshot["tree_epoch"], snapshot["geometry_revision"],
                            action, value, {}, "fixture-candidate-" + action, 5.0)
                    rejected = native._window_action(*args)
                    assert not rejected["ok"] and rejected["error"]["code"] == "background_not_verified"
                    result = native._test_window_candidate_action(*args)
                    report["candidate_" + action] = result
                    if not result["ok"] or result["value"]["dispatch_state"] != "accepted":
                        # An uncertain action is never retried or replaced by a new session.
                        break
                await asyncio.sleep(0.25)
                observed = state(file)
                target, sibling = record(observed, "A1"), record(observed, "A2")
                report["target_effect"] = target["text"] == "SCAPKIT synthetic replacement" and target["count"] == a1["count"] + 1
                report["sibling_unchanged"] = (sibling["text"], sibling["count"]) == (a2["text"], a2["count"])
                # Explicit root selection; first prove a sibling cannot be attached.
                roots = await window.list_accessible_windows(child.pid)
                wrong = next(w for w in roots if w.native_id == a2["accessibility"]["window"])
                try:
                    await capture.attach_accessibility(wrong.ref, profile_id=profile.profile_id)
                except window.WindowError as error:
                    assert error.code == "identity_unverifiable"
                    report["sibling_bridge_rejected"] = "passed"
                else:
                    raise AssertionError("sibling root attached")
                right = next(w for w in roots if w.native_id == a1["accessibility"]["window"])
                await capture.attach_accessibility(right.ref, profile_id=profile.profile_id)
                parsed = await capture.elements()
                assert any(e.identifier == "fixture.text" for e in parsed.elements)
                report["explicit_attachment_public_dto"] = "passed"
                assert profiles.profile_validation(profile.profile_id).status == "not_run"
            else:
                report["semantics"] = "not_run: Accessibility permission unavailable"
            # Close A1 using a fixture-owned signal handler; A2 and the child stay alive.
            child.send_signal(signal.SIGUSR1)
            report["phase"] = "window_close"
            await until(file, lambda d: any(w["slot"] == "A1" and w["closed"] for w in d["windows"]))
            assert child.poll() is None
            try:
                await capture.screenshot(format="bgra")
            except window.WindowError as error:
                assert error.code in ("stale_window", "window_closed")
                report["closed_window_rejected"] = "passed"
            else:
                raise AssertionError("closed window remained readable")
            report["desktop"] = "fixture probe executed; continuous-input acceptance not_run"
        finally:
            if semantic is not None:
                native_ok(native._window_close(semantic))
            if capture is not None:
                await capture.aclose()
                assert capture.cleanup_complete
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            report["owned_process_cleanup"] = child.poll() is not None
            report["fixture_exit_code"] = child.returncode
            if output.stat().st_size:
                report["fixture_diagnostic"] = output.read_text(errors="replace")[-2000:]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--desktop", action="store_true", required=True)
    args = parser.parse_args()
    assert args.desktop and sys.platform == "darwin" and sys.flags.isolated == 1
    assert "PYTHONHOME" not in os.environ and "PYTHONPATH" not in os.environ
    report = {"human_continuous_input": "not_run", "clipboard": "not_accessed", "profile_validation": "not_run"}
    result = 0
    try:
        asyncio.run(exercise(report))
        if any(item.get("status") == "failed" for item in report.get("screenshots", {}).values()):
            result = 1
        if report.get("target_effect") is False or report.get("sibling_unchanged") is False:
            result = 1
    except Exception as error:
        report["failure"] = type(error).__name__ + ": " + str(error)
        result = 1
    print(json.dumps(report, sort_keys=True, ensure_ascii=True))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
