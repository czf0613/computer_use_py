"""Private, deterministic native DTO fixtures. Never imported by product code."""

import asyncio
import json
import struct
import threading
import time
from copy import deepcopy
from hashlib import sha256

import pytest

from scapkit_computer_use import window
from scapkit_computer_use import window_profiles as profiles


def ok(value=None):
    return {"ok": True, "value": value}


def failure(code, dispatch="not_dispatched"):
    return {
        "ok": False,
        "error": {
            "code": code,
            "native_domain": "fixture",
            "native_code": 17,
            "dispatch_state": dispatch,
            "retry_hint": "never",
        },
    }


def rect(x, y, width, height):
    return {"x": x, "y": y, "width": width, "height": height}


def jpeg_header(width, height):
    # A synthetic SOF fixture for header validation, not encoded pixel evidence.
    return (
        b"\xff\xd8\xff\xc0\x00\x11\x08"
        + struct.pack(">HH", height, width)
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00\xff\xd9"
    )


def candidate_profile(platform="macos"):
    return profiles.BackgroundProfile(
        "fixture.v1",
        platform,
        "fixture.app",
        "1.2",
        "fixture-build",
        "fixture-os/arm64",
        "fixture-library-build",
        "test-window",
        (
            profiles.ProfileAction("text", "text_field", "set_value"),
            profiles.ProfileAction("count", "button", "invoke"),
        ),
    )


def evidence_for(profile):
    return json.dumps(
        {
            "schema_version": 1,
            "profile": profiles._scope(profile),
            "checks": dict.fromkeys(profiles._REQUIRED_CHECKS, "passed"),
            "implementation_commit": "a" * 40,
            "report_sha256": "b" * 64,
        },
        sort_keys=True,
    ).encode()


def approve_for_test(monkeypatch, backend):
    profile = candidate_profile(backend.platform)
    profiles.register_profile(profile)
    evidence = evidence_for(profile)
    # Only tests replace the installed trust root. No public API can do this.
    monkeypatch.setattr(
        profiles,
        "_APPROVED_EVIDENCE_SHA256",
        {profile.profile_id: sha256(evidence).hexdigest()},
    )
    profiles.verify_profile(profile.profile_id, evidence)
    backend.subject = profiles._scope(profile)
    del backend.subject["actions"], backend.subject["profile_id"]
    return profile


class Resource:
    def __init__(self, kind, generation, instance):
        self.kind = kind
        self.generation = generation
        self.instance = instance
        self.used = False
        self.closed = False


class FakeBackend:
    def __init__(self):
        self.platform = "macos"
        self.generation = 1
        self.instance = "process-start-1"
        self.revision = self.epoch = 1
        self.calls = []
        self.handles = []
        self.close_count = 0
        self.action_count = 0
        self.frame_count = 0
        self.bridge = True
        self.subject = None
        self.context_changes = {}
        self.frame_changes = {}
        self.element_changes = {}
        self.action_result = None
        self.close_result = None
        self.before_open = self.before_close = self.before_action = None
        self.before_screenshot = self.before_attach = None
        self.lock = threading.RLock()

    def _identity(self, handle=None):
        return {
            "generation": self.generation if handle is None else handle.generation,
            "process_instance": self.instance if handle is None else handle.instance,
        }

    def _valid(self, handle):
        if handle.closed:
            return failure("window_closed")
        if handle.generation != self.generation or handle.instance != self.instance:
            return failure("stale_window")
        return None

    def _window_enumerate(self, kind, pid, limit):
        with self.lock:
            self.calls.append("enumerate")
            return ok(
                [
                    dict(
                        self._identity(),
                        ref=Resource(kind, self.generation, self.instance),
                        platform=self.platform,
                        native_id="same-native-id",
                        pid=pid or 123,
                        title="same-title",
                        bounds=rect(-200, -100, 100, 80),
                        coordinate_unit="points"
                        if self.platform == "macos"
                        else "pixels",
                        visible=True,
                        minimized=False,
                        capture_support="supported"
                        if kind == "capture"
                        else "unsupported",
                        inspect_support="supported"
                        if kind == "accessible"
                        else "unsupported",
                    )
                ]
            )

    def _window_open(self, ref, kind):
        if self.before_open:
            self.before_open()
        with self.lock:
            self.calls.append("open")
            invalid = self._valid(ref)
            if invalid:
                return invalid
            if ref.used or ref.kind != kind:
                return failure("stale_window")
            ref.used = True
            handle = Resource(kind, self.generation, self.instance)
            self.handles.append(handle)
            return ok(dict(self._identity(handle), handle=handle))

    def _window_close(self, handle):
        if self.before_close:
            self.before_close()
        with self.lock:
            self.calls.append("close")
            if not handle.closed:
                self.close_count += 1
            handle.closed = True
            return self.close_result or ok()

    def _window_attach(self, handle, ref):
        if self.before_attach:
            self.before_attach()
        with self.lock:
            self.calls.append("attach")
            if not self.bridge:
                return failure("identity_unverifiable")
            invalid = self._valid(handle) or self._valid(ref)
            if invalid:
                return invalid
            ref.used = True
            handle.kind = "attached"
            return ok()

    def _window_inspect(self, handle, query, options):
        with self.lock:
            self.calls.append(query)
            invalid = self._valid(handle)
            if invalid:
                return invalid
            if query == "capabilities":
                result = dict(
                    self._identity(handle),
                    geometry_revision=self.revision,
                    identity_status="verified",
                    capture="supported",
                    inspect_elements="supported",
                    set_value="supported",
                    invoke="supported",
                    minimized=False,
                    modal=False,
                    focus_policy="no_activation",
                    subject=deepcopy(self.subject),
                )
                result.update(self.context_changes)
                return ok(result)
            if query == "validate_snapshot":
                if options["tree_epoch"] != self.epoch:
                    return failure("stale_element")
                if options["geometry_revision"] != self.revision:
                    return failure("stale_geometry")
                return ok()
            rows = [
                {
                    "identifier": "root",
                    "role": "window",
                    "bounds": rect(0, 0, 100, 80),
                    "actions": [],
                    "depth": 0,
                    "parent_index": None,
                    "editable": False,
                },
                {
                    "identifier": "text",
                    "role": "text_field",
                    "bounds": rect(10, 10, 50, 20),
                    "actions": ["set_value"],
                    "depth": 1,
                    "parent_index": 0,
                    "editable": True,
                },
                {
                    "identifier": "count",
                    "role": "button",
                    "bounds": rect(10, 40, 30, 20),
                    "actions": ["invoke"],
                    "depth": 1,
                    "parent_index": 0,
                    "editable": False,
                },
            ]
            for index, row in enumerate(rows):
                row.update(
                    ref=Resource("element", handle.generation, handle.instance),
                    name="fixture",
                    enabled=True,
                    password=False,
                )
                row.update(self.element_changes.get(index, {}))
            rows = [row for row in rows if row["depth"] <= options["max_depth"]][
                : options["max_nodes"]
            ]
            return ok(
                dict(
                    self._identity(handle),
                    geometry_revision=self.revision,
                    tree_epoch=self.epoch,
                    coordinate_unit="points" if self.platform == "macos" else "pixels",
                    mapping_status="verified",
                    elements=rows,
                )
            )

    def _window_screenshot(self, handle, format, quality, timeout):
        if self.before_screenshot:
            self.before_screenshot()
        with self.lock:
            self.calls.append("screenshot")
            invalid = self._valid(handle)
            if invalid:
                return invalid
            self.frame_count += 1
            result = dict(
                self._identity(handle),
                frame_id=str(self.frame_count),
                geometry_revision=self.revision,
                captured_at=time.monotonic(),
                state="complete",
                format=format,
                data=bytes(200 * 160 * 4)
                if format == "bgra"
                else jpeg_header(200, 160),
                image_width=200,
                image_height=160,
                stride=800 if format == "bgra" else None,
                window_bounds=rect(-200, -100, 100, 80),
                local_rect=rect(0, 0, 100, 80),
                image_content_rect_px=rect(0, 0, 200, 160),
                client_rect_local=rect(0, 5, 100, 75),
                coordinate_unit="points" if self.platform == "macos" else "pixels",
                image_to_local=(0.5, 0, 0, 0.5, 0, 0),
                mapping_status="verified",
            )
            result.update(self.frame_changes)
            return ok(result)

    def _window_action(
        self,
        handle,
        element,
        epoch,
        revision,
        action,
        value,
        authorization,
        operation_id,
        timeout,
    ):
        if self.before_action:
            self.before_action()
        profiles._validate_authorization(authorization)
        with self.lock:
            self.calls.append("action")
            invalid = self._valid(handle) or self._valid(element)
            if invalid:
                return invalid
            if epoch != self.epoch:
                return failure("stale_element")
            if revision != self.revision:
                return failure("stale_geometry")
            self.action_count += 1
            return self.action_result or ok(
                {
                    "operation_id": operation_id,
                    "dispatch_state": "accepted",
                    "effect_state": "unknown",
                }
            )


@pytest.fixture
def backend(monkeypatch):
    native = FakeBackend()
    monkeypatch.setattr(window, "_load_native", lambda: native)
    monkeypatch.setattr(profiles, "_profiles", {})
    monkeypatch.setattr(profiles, "_validations", {})
    monkeypatch.setattr(profiles, "_APPROVED_EVIDENCE_SHA256", {})
    return native


async def capture():
    return await window.open_window((await window.list_windows())[0].ref)


async def semantic(profile_id=None):
    return await window.open_accessible_window(
        (await window.list_accessible_windows(123))[0].ref, profile_id=profile_id
    )


class Gate:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self):
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError("test gate timed out")

    async def wait(self):
        assert await asyncio.to_thread(self.entered.wait, 2), (
            "worker did not reach gate"
        )
