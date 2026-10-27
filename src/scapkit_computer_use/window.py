"""Explicit window capture and background-only semantic control.

Importing this module needs no desktop extension. Operations require native
window ABI 1; there is no emulation or global-input fallback. See
docs/window-backend-contract.md for the native implementation contract.
"""

import asyncio
import os
import secrets
import time
from contextlib import contextmanager
from importlib import import_module
from math import isclose
from threading import Lock

from . import window_profiles as profiles
from .window_errors import ERROR_CODES, WindowError, _error
from .window_types import (
    AccessibleWindowInfo,
    AccessibleWindowRef,
    ActionReceipt,
    ElementInfo,
    ElementRef,
    ElementSnapshot,
    WindowCapabilities,
    WindowCapability,
    WindowFrame,
    WindowInfo,
    WindowPoint,
    WindowRect,
    WindowRef,
    _integer,
    _number,
    _text,
)

_NATIVE_ENTRIES = (
    "_window_enumerate",
    "_window_open",
    "_window_close",
    "_window_attach",
    "_window_screenshot",
    "_window_inspect",
    "_window_action",
)
_MAX_BYTES = 128 * 1024 * 1024
_REF_TTL = 60.0
_SNAPSHOT_TTL = 30.0
_SUPPORT = ("supported", "unsupported", "unknown")
_NOT_DISPATCHED = object()


def _load_native():
    try:
        native = import_module(".screen_capture_kit._scapkit", __package__)
    except (ImportError, OSError):
        raise _error("backend_unavailable", "load_backend") from None
    if (
        type(getattr(native, "WINDOW_BACKEND_ABI", None)) is not int
        or native.WINDOW_BACKEND_ABI != 1
        or not all(callable(getattr(native, name, None)) for name in _NATIVE_ENTRIES)
    ):
        raise _error("backend_unavailable", "load_backend")
    return native


def _choice(value, choices):
    if type(value) is not str or value not in choices:
        raise ValueError("invalid backend enum")
    return value


def _boolean(value):
    if value is not None and type(value) is not bool:
        raise TypeError("expected bool or None")
    return value


def _optional_text(value):
    return None if value is None else _text(value, "text", empty=True)


def _rect(value):
    if type(value) is not dict or set(value) != {"x", "y", "width", "height"}:
        raise ValueError("invalid rectangle")
    return WindowRect(**value)


def _parse(parser, value, operation, token=None):
    try:
        return parser(value)
    except (TypeError, ValueError, KeyError, IndexError, AttributeError, OverflowError):
        raise _error("backend_protocol_error", operation, window_token=token) from None


def _unwrap(raw, operation, token=None, operation_id=None):
    if raw is _NOT_DISPATCHED:
        raise _error(
            "backend_busy", operation, window_token=token, operation_id=operation_id
        )

    def decode(data):
        if type(data) is not dict or type(data.get("ok")) is not bool:
            raise ValueError("invalid envelope")
        if data["ok"]:
            if set(data) != {"ok", "value"}:
                raise ValueError("invalid success envelope")
            return data["value"]
        if set(data) != {"ok", "error"} or type(data["error"]) is not dict:
            raise ValueError("invalid failure envelope")
        failure = data["error"]
        code = _choice(failure["code"], ERROR_CODES)
        domain = _optional_text(failure["native_domain"])
        native_code = failure["native_code"]
        if native_code is not None and type(native_code) not in (int, str):
            raise ValueError("invalid native code")
        if type(native_code) is str:
            _text(native_code, "native_code", 128)
        dispatch = _choice(
            failure["dispatch_state"], ("not_dispatched", "accepted", "unknown")
        )
        retry = _choice(
            failure["retry_hint"],
            ("never", "refresh", "after_permission", "after_close"),
        )
        if dispatch != "not_dispatched":
            retry = "never"
        raise _error(
            code,
            operation,
            window_token=token,
            native_domain=domain,
            native_code=native_code,
            dispatch_state=dispatch,
            retry_hint=retry,
            operation_id=operation_id,
        )

    return _parse(decode, raw, operation, token)


async def _drain(future):
    """Keep the worker owned through repeated cancellation; do not retry it."""
    call = future if isinstance(future, _NativeCall) else None
    if call is not None:
        future = call.future
    cancellation = None
    while True:
        try:
            return await asyncio.shield(future), cancellation
        except asyncio.CancelledError as error:
            if future.cancelled():
                raise
            cancellation = cancellation or error
            if call is not None:
                call.cancel_pending()
        except BaseException as error:
            if cancellation is not None:
                raise cancellation from error
            raise


class _NativeCall:
    """A lock protects executor admission even on free-threaded interpreters."""

    def __init__(self, function, args):
        self._lock = Lock()
        self._started = self._canceled = False
        self.future = asyncio.get_running_loop().run_in_executor(
            None, self._run, function, args
        )

    def _run(self, function, args):
        with self._lock:
            if self._canceled:
                return _NOT_DISPATCHED
            self._started = True
        return function(*args)

    def cancel_pending(self):
        with self._lock:
            if not self._started:
                self._canceled = True


def _worker(native, name, *args):
    return _NativeCall(getattr(native, name), args)


def _issue_ref(cls, native_ref, backend, *, owner=None, snapshot_id=None):
    if native_ref is None:
        raise ValueError("missing native reference")
    ref = object.__new__(cls)
    for name, value in {
        "_token": secrets.token_urlsafe(24),
        "_native": native_ref,
        "_backend": backend,
        "_loop": asyncio.get_running_loop(),
        "_expires_at": time.monotonic() + _REF_TTL,
        "_used": False,
        "_info": None,
        "_owner": owner,
        "_snapshot_id": snapshot_id,
        "_process_id": os.getpid(),
    }.items():
        object.__setattr__(ref, name, value)
    return ref


def _candidate(ref, cls, operation):
    if type(ref) is not cls or not hasattr(ref, "_loop"):
        raise TypeError(f"ref must be an issued {cls.__name__}")
    if ref._loop is not asyncio.get_running_loop():
        raise _error("wrong_event_loop", operation)
    if (
        ref._process_id != os.getpid()
        or ref._used
        or time.monotonic() >= ref._expires_at
    ):
        raise _error(
            "stale_window", operation, window_token=ref.token, retry_hint="refresh"
        )


def _info(data, kind, backend):
    platform = _choice(data["platform"], ("macos", "windows"))
    unit = _choice(
        data["coordinate_unit"], ("points",) if platform == "macos" else ("pixels",)
    )
    cls, refcls = (
        (WindowInfo, WindowRef)
        if kind == "capture"
        else (AccessibleWindowInfo, AccessibleWindowRef)
    )
    info = cls(
        ref=_issue_ref(refcls, data["ref"], backend),
        platform=platform,
        native_id=_text(data["native_id"], "native_id", 128),
        pid=_integer(data["pid"], "pid", 1, 2**32 - 1),
        process_instance=_text(data["process_instance"], "process_instance", 256),
        generation=_integer(data["generation"], "generation", 1, 2**63 - 1),
        title=_optional_text(data["title"]),
        bounds=_rect(data["bounds"]),
        coordinate_unit=unit,
        visible=_boolean(data["visible"]),
        minimized=_boolean(data["minimized"]),
        capture_support=_choice(data["capture_support"], _SUPPORT),
        inspect_support=_choice(data["inspect_support"], _SUPPORT),
    )
    object.__setattr__(info.ref, "_info", info)
    return info


async def _enumerate(kind, pid):
    if pid is not None:
        _integer(pid, "pid", 1, 2**32 - 1)
    native = _load_native()
    raw, cancellation = await _drain(
        _worker(native, "_window_enumerate", kind, pid, 256)
    )
    if cancellation is not None:
        # Enumeration capsules own retained references, no streams/actors.
        raise cancellation
    data = _unwrap(
        raw, "list_windows" if kind == "capture" else "list_accessible_windows"
    )

    def parse(rows):
        if type(rows) is not list or len(rows) > 256:
            raise ValueError("invalid window list")
        result = [_info(row, kind, native) for row in rows]
        if pid is not None and any(info.pid != pid for info in result):
            raise ValueError("backend ignored PID filter")
        return result

    return _parse(parse, data, "enumerate")


async def list_windows(*, pid: int | None = None) -> list[WindowInfo]:
    """Enumerate up to 256 explicit capture candidates (60-second, one-use refs)."""
    return await _enumerate("capture", pid)


async def list_accessible_windows(pid: int) -> list[AccessibleWindowInfo]:
    """Enumerate independent semantic roots for an explicit process."""
    _integer(pid, "pid", 1, 2**32 - 1)
    return await _enumerate("accessible", pid)


async def _close_handle(native, handle):
    # Cleanup cannot be canceled while queued. Only acquisition/work calls use
    # cancel_pending; close must reach native even after repeated cancellation.
    future = asyncio.get_running_loop().run_in_executor(
        None, native._window_close, handle
    )
    raw, cancellation = await _drain(future)
    value = _unwrap(raw, "close")
    if value is not None:
        raise _error("backend_protocol_error", "close")
    if cancellation is not None:
        raise cancellation


async def _open(ref, kind, profile_id):
    operation = "open_window" if kind == "capture" else "open_accessible_window"
    _candidate(ref, WindowRef if kind == "capture" else AccessibleWindowRef, operation)
    if profile_id is not None:
        _text(profile_id, "profile_id")
    object.__setattr__(ref, "_used", True)
    raw, cancellation = await _drain(
        _worker(ref._backend, "_window_open", ref._native, kind)
    )
    handle = None
    if type(raw) is dict and raw.get("ok") is True and type(raw.get("value")) is dict:
        handle = raw["value"].get("handle")
    try:
        data = _unwrap(raw, operation, ref.token)

        def parse(value):
            if (
                value["handle"] is None
                or type(value["generation"]) is not int
                or value["generation"] != ref._info.generation
                or value["process_instance"] != ref._info.process_instance
            ):
                raise ValueError("open identity differs from enumerated reference")

        _parse(parse, data, operation, ref.token)
        if cancellation is not None:
            raise cancellation
        cls = WindowSession if kind == "capture" else AccessibleWindowSession
        return cls._create(ref, handle, profile_id)
    except BaseException as original:
        if handle is not None:
            try:
                await _close_handle(ref._backend, handle)
            except BaseException as cleanup_error:
                raise original from cleanup_error
        if cancellation is not None and cancellation is not original:
            raise cancellation from original
        raise


async def open_window(ref: WindowRef) -> "WindowSession":
    """Open exactly the enumerated capture object; never auto-attach an AX/UIA root."""
    return await _open(ref, "capture", None)


async def open_accessible_window(
    ref: AccessibleWindowRef,
    *,
    profile_id: str | None = None,
) -> "AccessibleWindowSession":
    """Open an explicit control-only root; writes need a verified profile."""
    return await _open(ref, "accessible", profile_id)


class _Session:
    def __new__(cls, *args, **kwargs):
        raise TypeError("sessions are created by open_window/open_accessible_window")

    @classmethod
    def _create(cls, ref, handle, profile_id):
        self = object.__new__(cls)
        self._loop = asyncio.get_running_loop()
        self._process_id = os.getpid()
        self._token = secrets.token_urlsafe(24)
        self._native = ref._backend
        self._handle = handle
        self._info = ref._info
        self._profile_id = profile_id
        self._has_semantics = type(ref) is AccessibleWindowRef
        self._closing = self._closed = self._busy = self._uncertain = False
        self._cleanup_complete = False
        self._invalid = None
        self._close_task = self._idle = self._snapshot = None
        self._snapshot_expires = 0.0
        self._geometry_revision = 0
        self._last_frame_id = None
        self._last_action = None
        return self

    @property
    def token(self) -> str:
        return self._token

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def cleanup_complete(self) -> bool:
        """False until native close confirms cleanup (even if locally closed)."""
        return self._cleanup_complete

    @property
    def last_action(self) -> ActionReceipt | None:
        """Last attempted write, including canceled/unknown operations."""
        return self._last_action

    def _check_loop(self, operation):
        if (
            self._loop is not asyncio.get_running_loop()
            or self._process_id != os.getpid()
        ):
            raise _error("wrong_event_loop", operation, window_token=self.token)

    def _check(self, operation):
        self._check_loop(operation)
        if self._closing or self._closed:
            raise _error("session_closed", operation, window_token=self.token)
        if self._invalid is not None:
            raise _error(
                self._invalid, operation, window_token=self.token, retry_hint="refresh"
            )

    @contextmanager
    def _operation(self, operation):
        self._check(operation)
        if self._busy:
            raise _error("backend_busy", operation, window_token=self.token)
        self._busy = True
        self._idle = self._loop.create_future()
        try:
            yield
        finally:
            self._busy = False
            self._idle.set_result(None)

    async def _run(self, name, *args):
        self._check(name)
        return await _drain(_worker(self._native, name, self._handle, *args))

    def _result(self, raw, operation, operation_id=None):
        try:
            return _unwrap(raw, operation, self.token, operation_id)
        except WindowError as error:
            if error.code in {"stale_window", "window_closed", "interference_detected"}:
                self._invalid = error.code
                self._snapshot = None
            if error.code == "interference_detected":
                profiles._revoke_profile(self._profile_id)
            raise

    async def _read(self, entry, operation, parser, *args):
        raw, cancellation = await self._run(entry, *args)
        try:
            value = _parse(parser, self._result(raw, operation), operation, self.token)
        except BaseException as error:
            if cancellation is not None:
                raise cancellation from error
            raise
        if cancellation is not None:
            raise cancellation
        self._check(operation)
        return value

    def _identity(self, data):
        generation = _integer(data["generation"], "generation", 1, 2**63 - 1)
        instance = _text(data["process_instance"], "process_instance", 256)
        if (
            generation != self._info.generation
            or instance != self._info.process_instance
        ):
            self._invalid = "stale_window"
            raise _error(
                "stale_window",
                "identity",
                window_token=self.token,
                retry_hint="refresh",
            )

    def _revision(self, data):
        revision = _integer(
            data["geometry_revision"], "geometry_revision", 1, 2**63 - 1
        )
        if revision < self._geometry_revision:
            raise _error(
                "stale_geometry",
                "geometry",
                window_token=self.token,
                retry_hint="refresh",
            )
        self._geometry_revision = revision
        return revision

    def _context(self, data):
        self._identity(data)
        self._revision(data)
        _choice(data["identity_status"], ("verified", "unverified", "stale"))
        for key in ("capture", "inspect_elements", "set_value", "invoke"):
            _choice(data[key], _SUPPORT)
        _boolean(data["minimized"])
        _boolean(data["modal"])
        _choice(data["focus_policy"], ("no_activation", "unverified"))
        subject = data["subject"]
        if subject is not None:
            keys = {
                "platform",
                "application_id",
                "application_version",
                "application_build",
                "environment",
                "backend_build",
                "window_identifier",
            }
            if type(subject) is not dict or set(subject) != keys:
                raise ValueError("invalid profile subject")
            for key, value in subject.items():
                _text(value, key)
            if subject["platform"] != self._info.platform:
                raise ValueError("inconsistent platform")
        return data

    async def _current_context(self):
        return await self._read(
            "_window_inspect", "capabilities", self._context, "capabilities", {}
        )

    def _capabilities(self, context):
        profile = profiles._matching_profile(self._profile_id, context["subject"])
        validation = (
            profiles.profile_validation(self._profile_id).status
            if self._profile_id is not None
            else "not_run"
        )
        if validation == "verified" and profile is None:
            validation = "not_run"
        version = None if profile is None else profile.application_version

        def capability(action):
            support = context[action]
            if (
                action == "capture"
                and not isinstance(self, WindowSession)
                or action != "capture"
                and not self._has_semantics
            ):
                support = "unsupported"
            reason = None
            status = validation if action in ("set_value", "invoke") else "not_run"
            if support != "supported":
                reason = "unsupported_background_operation"
            elif context["identity_status"] != "verified":
                reason = "identity_unverifiable"
            elif action in ("set_value", "invoke"):
                if context["minimized"] is True:
                    reason = "window_minimized"
                elif context["modal"] is True:
                    reason = "modal_blocked"
                elif (
                    context["minimized"] is not False
                    or context["modal"] is not False
                    or context["focus_policy"] != "no_activation"
                ):
                    reason = "unsupported_background_operation"
                elif self._uncertain:
                    reason = "outcome_unknown"
                elif validation == "failed":
                    reason = "interference_detected"
                elif profile is None or not any(
                    rule.action == action for rule in profile.actions
                ):
                    reason = "background_not_verified"
                    status = "not_run"
            return WindowCapability(
                support,
                context["identity_status"],
                status,
                reason,
                self._profile_id,
                version,
            )

        unsupported = WindowCapability(
            "unsupported",
            context["identity_status"],
            "not_run",
            "unsupported_background_operation",
            None,
            None,
        )
        return WindowCapabilities(
            *(
                capability(action)
                for action in ("capture", "inspect_elements", "set_value", "invoke")
            ),
            coordinate_input=unsupported,
        )

    async def capabilities(self) -> WindowCapabilities:
        """Report support, identity and validation independently; no permission prompts."""
        with self._operation("capabilities"):
            return self._capabilities(await self._current_context())

    def _require_semantics(self, operation):
        if not self._has_semantics:
            raise _error("identity_unverifiable", operation, window_token=self.token)

    async def elements(
        self, *, max_depth: int = 8, max_nodes: int = 512
    ) -> ElementSnapshot:
        """Read a bounded root-local tree; values (including passwords) are omitted.

        Every call invalidates the previous snapshot. References expire after
        30 seconds and are revalidated natively before lookup or dispatch.
        """
        _integer(max_depth, "max_depth", 0, 32)
        _integer(max_nodes, "max_nodes", 1, 4096)
        with self._operation("elements"):
            self._require_semantics("elements")
            self._snapshot = None
            snapshot = await self._read(
                "_window_inspect",
                "elements",
                lambda data: _snapshot(self, data, max_depth, max_nodes),
                "elements",
                {"max_depth": max_depth, "max_nodes": max_nodes},
            )
            self._snapshot = snapshot
            self._snapshot_expires = time.monotonic() + _SNAPSHOT_TTL
            return snapshot

    def _current_snapshot(self, snapshot_id):
        _text(snapshot_id, "snapshot_id", 128)
        snapshot = self._snapshot
        if (
            snapshot is None
            or snapshot.snapshot_id != snapshot_id
            or time.monotonic() >= self._snapshot_expires
        ):
            raise _error(
                "stale_element",
                "snapshot",
                window_token=self.token,
                retry_hint="refresh",
            )
        if snapshot.geometry_revision != self._geometry_revision:
            raise _error(
                "stale_geometry",
                "snapshot",
                window_token=self.token,
                retry_hint="refresh",
            )
        return snapshot

    async def element_at(
        self,
        x: float | WindowPoint,
        y: float | None = None,
        *,
        snapshot_id: str,
    ) -> ElementRef:
        """Resolve a local point inside the bound tree, without posting input.

        Prefer frame.to_local(...) as the first argument to retain image
        provenance. Two numbers instead mean explicit root-local coordinates.
        Ancestors of a matching descendant are excluded; overlapping siblings
        are ambiguous, never chosen by z-order or nearest-point heuristics.
        """
        with self._operation("element_at"):
            self._require_semantics("element_at")
            snapshot = self._current_snapshot(snapshot_id)
            if type(x) is WindowPoint:
                if y is not None:
                    raise TypeError("y must be omitted with WindowPoint")
                point = x
                if (
                    point.window_token != self.token
                    or point.generation != self._info.generation
                ):
                    raise _error("stale_window", "element_at", window_token=self.token)
                if (
                    point.geometry_revision != snapshot.geometry_revision
                    or point.coordinate_unit != snapshot.coordinate_unit
                ):
                    raise _error(
                        "stale_geometry", "element_at", window_token=self.token
                    )
                x, y = point.x, point.y
            x, y = _number(x, "x"), _number(y, "y")
            if snapshot.mapping_status != "verified":
                raise _error(
                    "mapping_unavailable", "element_at", window_token=self.token
                )
            await self._read(
                "_window_inspect",
                "element_at",
                _none,
                "validate_snapshot",
                {
                    "tree_epoch": snapshot.tree_epoch,
                    "geometry_revision": snapshot.geometry_revision,
                },
            )
            matches = {
                i
                for i, elem in enumerate(snapshot.elements)
                if elem.bounds is not None and elem.bounds.contains(x, y)
            }
            ancestors = set()
            for index in matches:
                parent = snapshot.elements[index].parent_index
                while parent is not None:
                    ancestors.add(parent)
                    parent = snapshot.elements[parent].parent_index
            leaves = matches - ancestors
            if len(leaves) != 1:
                code = "element_not_found" if not leaves else "ambiguous_element"
                raise _error(code, "element_at", window_token=self.token)
            return snapshot.elements[leaves.pop()].ref

    def _element(self, ref):
        if type(ref) is not ElementRef or not hasattr(ref, "_owner"):
            raise TypeError("element must be an issued ElementRef")
        if ref._owner != self.token or ref._loop is not self._loop:
            raise _error("stale_element", "element", window_token=self.token)
        snapshot = self._current_snapshot(ref._snapshot_id)
        for info in snapshot.elements:
            if info.ref is ref:
                return info, snapshot
        raise _error("stale_element", "element", window_token=self.token)

    async def set_value(self, element: ElementRef, value: str) -> ActionReceipt:
        """Replace the whole semantic value; never type, append, or paste."""
        _text(value, "value", 65536, empty=True)
        return await self._action(element, "set_value", value)

    async def invoke(self, element: ElementRef) -> ActionReceipt:
        """Invoke one verified control action once; unknown outcomes are not retried."""
        return await self._action(element, "invoke", None)

    async def _action(self, element, action, value):
        with self._operation(action):
            if self._uncertain:
                raise _error(
                    "outcome_unknown",
                    action,
                    window_token=self.token,
                    dispatch_state="unknown",
                    operation_id=self._last_action.operation_id,
                )
            operation_id = secrets.token_urlsafe(24)
            self._last_action = ActionReceipt(operation_id, "not_dispatched", "unknown")
            started = False
            cancellation = None
            try:
                self._require_semantics(action)
                info, snapshot = self._element(element)
                if info.password is not False or info.enabled is not True:
                    raise _error("element_not_editable", action)
                if action == "set_value" and info.editable is not True:
                    raise _error("element_not_editable", action)
                if action not in info.actions:
                    raise _error("unsupported_background_operation", action)
                context = await self._current_context()
                # A capabilities query can reveal a new geometry revision.
                self._current_snapshot(snapshot.snapshot_id)
                capability = getattr(self._capabilities(context), action)
                if capability.reason_code is not None:
                    raise _error(capability.reason_code, action)
                authorization = profiles._authorization(
                    self._profile_id,
                    context["subject"],
                    info.identifier,
                    info.role,
                    action,
                )
                # No await between the final local checks and submission.
                self._check(action)
                started = True
                raw, cancellation = await self._run(
                    "_window_action",
                    element._native,
                    snapshot.tree_epoch,
                    snapshot.geometry_revision,
                    action,
                    value,
                    authorization,
                    operation_id,
                    5.0,
                )
                receipt = _parse(
                    lambda data: _receipt(data, operation_id),
                    self._result(raw, action, operation_id),
                    action,
                    self.token,
                )
                self._last_action = receipt
                if cancellation is not None:
                    # Completion after cancellation is not a safe retry signal.
                    if receipt.dispatch_state != "not_dispatched":
                        self._last_action = ActionReceipt(
                            operation_id, "unknown", "unknown"
                        )
                    raise cancellation
                if receipt.dispatch_state == "unknown":
                    self._uncertain = True
                return receipt
            except asyncio.CancelledError as error:
                if (
                    started
                    and self._last_action.dispatch_state != "not_dispatched"
                    or started
                    and cancellation is None
                ):
                    self._last_action = ActionReceipt(
                        operation_id, "unknown", "unknown"
                    )
                self._uncertain |= self._last_action.dispatch_state == "unknown"
                error.operation_id = operation_id
                error.receipt = self._last_action
                raise
            except BaseException as error:
                dispatch = "not_dispatched"
                if started:
                    dispatch = (
                        error.dispatch_state
                        if isinstance(error, WindowError)
                        else "unknown"
                    )
                    if (
                        isinstance(error, WindowError)
                        and error.code == "backend_protocol_error"
                    ):
                        dispatch = "unknown"
                self._last_action = ActionReceipt(operation_id, dispatch, "unknown")
                self._uncertain |= dispatch != "not_dispatched"
                if isinstance(error, WindowError):
                    error.window_token = self.token
                    error.operation_id = operation_id
                if cancellation is not None:
                    cancellation.operation_id = operation_id
                    cancellation.receipt = self._last_action
                    raise cancellation from error
                if dispatch != "not_dispatched" and (
                    not isinstance(error, WindowError)
                    or error.code != "interference_detected"
                ):
                    raise _error(
                        "outcome_unknown",
                        action,
                        window_token=self.token,
                        dispatch_state=dispatch,
                        operation_id=operation_id,
                        native_domain=getattr(error, "native_domain", None),
                        native_code=getattr(error, "native_code", None),
                    ) from error
                raise

    async def __aenter__(self):
        self._check("enter")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()

    async def _do_close(self):
        try:
            if self._busy:
                await _drain(self._idle)
            await _close_handle(self._native, self._handle)
            self._cleanup_complete = True
        finally:
            self._closed = True
            self._snapshot = None
            self._handle = None

    async def aclose(self) -> None:
        """Close once; concurrent callers share cleanup and any failure.

        Cancellation is preserved after cleanup. If native reports incomplete
        cleanup, closed is True, cleanup_complete is False and the error is
        shared by later close calls. Native owns any still-running actor state.
        """
        self._check_loop("close")
        if self._close_task is None:
            self._closing = True
            self._close_task = self._loop.create_task(self._do_close())
        _, cancellation = await _drain(self._close_task)
        if cancellation is not None:
            raise cancellation


class AccessibleWindowSession(_Session):
    """A control-only semantic root. No image/capture association is implied."""


class WindowSession(_Session):
    """An explicit capture target; semantic access requires a verified bridge."""

    async def attach_accessibility(
        self, ref: AccessibleWindowRef, *, profile_id: str
    ) -> None:
        """Bind an explicit semantic root through a native, public identity bridge.

        PID/title/geometry similarity never establishes this link. Cancellation
        or a malformed successful response closes the session, including any
        late binding. A native failure must leave the session unbound.
        """
        _text(profile_id, "profile_id")
        _candidate(ref, AccessibleWindowRef, "attach_accessibility")
        close_on_failure = False
        try:
            with self._operation("attach_accessibility"):
                if self._has_semantics:
                    raise _error(
                        "backend_busy", "attach_accessibility", window_token=self.token
                    )
                if (
                    ref._backend is not self._native
                    or ref._info.platform != self._info.platform
                    or ref._info.pid != self._info.pid
                    or ref._info.process_instance != self._info.process_instance
                ):
                    raise _error(
                        "identity_unverifiable",
                        "attach_accessibility",
                        window_token=self.token,
                    )
                object.__setattr__(ref, "_used", True)
                close_on_failure = True
                raw, cancellation = await self._run("_window_attach", ref._native)
                try:
                    value = self._result(raw, "attach_accessibility")
                except WindowError as error:
                    if cancellation is not None:
                        raise cancellation from error
                    if error.code != "backend_protocol_error" and cancellation is None:
                        close_on_failure = False
                    raise
                _parse(_none, value, "attach_accessibility", self.token)
                if cancellation is not None:
                    raise cancellation
                self._check("attach_accessibility")
                self._has_semantics = True
                self._profile_id = profile_id
                close_on_failure = False
        except BaseException as original:
            if close_on_failure:
                try:
                    await self.aclose()
                except BaseException as cleanup_error:
                    raise original from cleanup_error
            raise

    async def screenshot(
        self, *, format: str = "jpeg", quality: int = 80, timeout_seconds: float = 5.0
    ) -> WindowFrame:
        """Capture one fresh frame of this window, closing its short-lived stream."""
        if type(format) is not str or format not in ("jpeg", "bgra"):
            raise ValueError("format must be jpeg or bgra")
        _integer(quality, "quality", 0, 100)
        timeout_seconds = _number(timeout_seconds, "timeout_seconds")
        if not 0 < timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be in (0, 30]")
        with self._operation("screenshot"):
            started = time.monotonic()
            frame = await self._read(
                "_window_screenshot",
                "screenshot",
                lambda data: _frame(self, data, format, started),
                format,
                quality,
                timeout_seconds,
            )
            if frame.frame_id == self._last_frame_id:
                raise _error(
                    "capture_unavailable", "screenshot", window_token=self.token
                )
            self._last_frame_id = frame.frame_id
            return frame


def _none(value):
    if value is not None:
        raise ValueError("expected None")


def _receipt(data, operation_id):
    if data["operation_id"] != operation_id:
        raise ValueError("mismatched operation_id")
    dispatch = _choice(
        data["dispatch_state"], ("not_dispatched", "accepted", "unknown")
    )
    effect = _choice(data["effect_state"], ("verified", "unknown"))
    if dispatch != "accepted" and effect != "unknown":
        raise ValueError("unconfirmed dispatch cannot verify an effect")
    return ActionReceipt(operation_id, dispatch, effect)


def _snapshot(session, data, max_depth, max_nodes):
    session._identity(data)
    revision = session._revision(data)
    epoch = _integer(data["tree_epoch"], "tree_epoch", 1, 2**63 - 1)
    unit = _choice(data["coordinate_unit"], (session._info.coordinate_unit,))
    mapping = _choice(data["mapping_status"], ("verified", "unavailable"))
    rows = data["elements"]
    if type(rows) is not list or not 1 <= len(rows) <= max_nodes:
        raise ValueError("invalid tree size")
    snapshot_id = secrets.token_urlsafe(24)
    elements = []
    text_bytes = 0
    native_refs = set()
    for index, row in enumerate(rows):
        parent = row["parent_index"]
        depth = _integer(row["depth"], "depth", 0, max_depth)
        if index == 0:
            if parent is not None or depth != 0:
                raise ValueError("first element must be the root")
        else:
            _integer(parent, "parent_index", 0, index - 1)
            if depth != elements[parent].depth + 1:
                raise ValueError("invalid tree ancestry")
        actions = row["actions"]
        if (
            type(actions) is not list
            or len(actions) > 2
            or any(
                type(a) is not str or a not in ("set_value", "invoke") for a in actions
            )
            or len(set(actions)) != len(actions)
        ):
            raise ValueError("invalid element actions")
        if "value" in row:
            raise ValueError("element values must not cross this boundary")
        identifier, role, name = (
            _optional_text(row["identifier"]),
            _text(row["role"], "role"),
            _optional_text(row["name"]),
        )
        text_bytes += sum(
            len(text.encode("utf-8")) for text in (identifier, role, name) if text
        )
        if text_bytes > 1024 * 1024 or id(row["ref"]) in native_refs:
            raise ValueError("tree exceeds text limit or repeats a reference")
        native_refs.add(id(row["ref"]))
        ref = _issue_ref(
            ElementRef,
            row["ref"],
            session._native,
            owner=session.token,
            snapshot_id=snapshot_id,
        )
        elements.append(
            ElementInfo(
                ref,
                identifier,
                role,
                name,
                None if row["bounds"] is None else _rect(row["bounds"]),
                _boolean(row["enabled"]),
                _boolean(row["editable"]),
                _boolean(row["password"]),
                tuple(actions),
                depth,
                parent,
            )
        )
    return ElementSnapshot(
        session.token,
        snapshot_id,
        session._info.generation,
        epoch,
        revision,
        unit,
        mapping,
        tuple(elements),
    )


def _inside(inner, outer):
    return (
        inner.x >= outer.x
        and inner.y >= outer.y
        and inner.x + inner.width <= outer.x + outer.width + 1e-6
        and inner.y + inner.height <= outer.y + outer.height + 1e-6
    )


def _jpeg_dimensions(data):
    """Check JPEG framing and SOF dimensions, not a second JPEG decoder."""
    if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise ValueError("invalid JPEG framing")
    offset = 2
    while offset < len(data) - 2:
        if data[offset] != 0xFF:
            raise ValueError("invalid JPEG marker")
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in (0xD8, 0xD9, 0xDA, 0x00) or 0xD0 <= marker <= 0xD7:
            raise ValueError("no SOF before image data")
        size = int.from_bytes(data[offset : offset + 2], "big")
        if size < 2 or offset + size > len(data):
            raise ValueError("truncated JPEG segment")
        if marker in (0xC0, 0xC1, 0xC2):
            if size < 8:
                raise ValueError("invalid SOF")
            height = int.from_bytes(data[offset + 3 : offset + 5], "big")
            width = int.from_bytes(data[offset + 5 : offset + 7], "big")
            return width, height
        offset += size
    raise ValueError("missing supported JPEG SOF")


def _frame(session, data, format, started):
    session._identity(data)
    revision = session._revision(data)
    width = _integer(data["image_width"], "image_width", 1, 16384)
    height = _integer(data["image_height"], "image_height", 1, 16384)
    if width * height * 4 > _MAX_BYTES:
        raise ValueError("frame exceeds pixel budget")
    encoded = data["data"]
    if type(encoded) is not bytes or not 1 <= len(encoded) <= _MAX_BYTES:
        raise ValueError("invalid image data")
    _choice(data["format"], (format,))
    stride = data["stride"]
    if format == "bgra":
        _integer(stride, "stride", width * 4, _MAX_BYTES)
        if len(encoded) != stride * height:
            raise ValueError("BGRA byte count differs from stride/height")
    elif stride is not None or _jpeg_dimensions(encoded) != (width, height):
        raise ValueError("JPEG metadata differs from actual image dimensions")
    captured_at = _number(data["captured_at"], "captured_at")
    if not started - 0.001 <= captured_at <= time.monotonic() + 0.001:
        raise ValueError("frame is not from this capture interval")
    bounds = _rect(data["window_bounds"])
    local = None if data["local_rect"] is None else _rect(data["local_rect"])
    content = (
        None
        if data["image_content_rect_px"] is None
        else _rect(data["image_content_rect_px"])
    )
    client = (
        None if data["client_rect_local"] is None else _rect(data["client_rect_local"])
    )
    surface = WindowRect(0, 0, bounds.width, bounds.height)
    if client is not None and not _inside(client, surface):
        raise ValueError("client rect exceeds window surface")
    mapping = _choice(data["mapping_status"], ("verified", "unavailable"))
    matrix = data["image_to_local"]
    if mapping == "verified":
        if local is None or content is None or not _inside(local, surface):
            raise ValueError("missing or out-of-bounds local mapping")
        if not _inside(content, WindowRect(0, 0, width, height)):
            raise ValueError("content rect exceeds image")
        if type(matrix) not in (tuple, list) or len(matrix) != 6:
            raise ValueError("expected six affine coefficients")
        matrix = tuple(_number(value, "image_to_local") for value in matrix)
        sx, sy = local.width / content.width, local.height / content.height
        expected = (sx, 0, 0, sy, local.x - content.x * sx, local.y - content.y * sy)
        if any(
            not isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)
            for a, b in zip(matrix, expected)
        ):
            raise ValueError("affine transform contradicts content rectangles")
    elif local is not None or content is not None or matrix is not None:
        raise ValueError("unavailable mapping must not expose guessed transforms")
    return WindowFrame(
        session.token,
        session._info.generation,
        _text(data["frame_id"], "frame_id", 128),
        revision,
        captured_at,
        _choice(data["state"], ("complete",)),
        format,
        encoded,
        width,
        height,
        stride,
        bounds,
        local,
        content,
        client,
        _choice(data["coordinate_unit"], (session._info.coordinate_unit,)),
        matrix,
        mapping,
    )
