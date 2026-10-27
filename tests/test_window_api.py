"""Public window behavior with injected DTOs; no desktop, clipboard or permissions."""

import asyncio
import importlib
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import window_fakes
from window_fakes import Gate, approve_for_test, capture, failure, ok, semantic

from scapkit_computer_use import window
from scapkit_computer_use.window_errors import (
    WindowError,
    WindowPermissionError,
    WindowTimeoutError,
)

backend = window_fakes.backend


def test_exports_are_lazy_and_native_absence_is_honest(monkeypatch):
    package = importlib.import_module("scapkit_computer_use")
    for name, module in package._WINDOW_EXPORTS.items():
        assert getattr(package, name) is getattr(
            importlib.import_module(module, package.__name__), name
        )

    def missing(*args):
        raise ModuleNotFoundError("fixture missing extension")

    monkeypatch.setattr(window, "import_module", missing)
    with pytest.raises(WindowError, match="backend_unavailable"):
        asyncio.run(window.list_windows())
    monkeypatch.setattr(
        window, "import_module", lambda *args: SimpleNamespace(WINDOW_BACKEND_ABI=1)
    )
    with pytest.raises(WindowError, match="backend_unavailable"):
        asyncio.run(window.list_windows())
    monkeypatch.setattr(
        window, "import_module", lambda *args: SimpleNamespace(WINDOW_BACKEND_ABI=True)
    )
    with pytest.raises(WindowError, match="backend_unavailable"):
        asyncio.run(window.list_windows())


@pytest.mark.asyncio
async def test_capture_and_control_roots_remain_independent(backend):
    async with await capture() as session:
        caps = await session.capabilities()
        assert caps.capture.api_support == "supported"
        assert caps.inspect_elements.reason_code == "unsupported_background_operation"
        assert caps.coordinate_input.api_support == "unsupported"
        with pytest.raises(WindowError, match="identity_unverifiable"):
            await session.elements()
        backend.bridge = False
        ref = (await window.list_accessible_windows(123))[0].ref
        with pytest.raises(WindowError, match="identity_unverifiable"):
            await session.attach_accessibility(ref, profile_id="not-verified")
        assert not session.closed
        assert not session._has_semantics
    async with await semantic() as control:
        assert not hasattr(control, "screenshot")
        assert (await control.capabilities()).capture.api_support == "unsupported"


@pytest.mark.asyncio
async def test_semantic_actions_need_precise_verified_scope(backend, monkeypatch):
    async with await semantic("fixture.v1") as session:
        tree = await session.elements()
        with pytest.raises(WindowError, match="background_not_verified"):
            await session.set_value(tree.elements[1].ref, "offline value")
        approve_for_test(monkeypatch, backend)
        receipt = await session.set_value(tree.elements[1].ref, "offline value")
        assert (
            receipt.dispatch_state == "accepted" and receipt.effect_state == "unknown"
        )
        assert session.last_action == receipt
        assert (await session.capabilities()).invoke.background_validation == "verified"
        backend.subject["application_version"] = "1.3"
        with pytest.raises(WindowError, match="background_not_verified"):
            await session.invoke(tree.elements[2].ref)
        assert backend.action_count == 1


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"minimized": True}, "window_minimized"),
        ({"modal": True}, "modal_blocked"),
        ({"modal": None}, "unsupported_background_operation"),
        ({"minimized": None}, "unsupported_background_operation"),
        ({"focus_policy": "unverified"}, "unsupported_background_operation"),
        ({"identity_status": "unverified"}, "identity_unverifiable"),
        ({"set_value": "unsupported"}, "unsupported_background_operation"),
    ],
)
@pytest.mark.asyncio
async def test_action_preconditions(backend, monkeypatch, changes, code):
    approve_for_test(monkeypatch, backend)
    backend.context_changes = changes
    async with await semantic("fixture.v1") as session:
        tree = await session.elements()
        with pytest.raises(WindowError, match=code):
            await session.set_value(tree.elements[1].ref, "offline")
        assert session.last_action.dispatch_state == "not_dispatched"
    assert backend.action_count == 0


@pytest.mark.parametrize(
    "change",
    [{"password": True}, {"password": None}, {"enabled": False}, {"editable": False}],
)
@pytest.mark.asyncio
async def test_sensitive_or_uneditable_elements_are_rejected(
    backend, monkeypatch, change
):
    approve_for_test(monkeypatch, backend)
    backend.element_changes[1] = change
    async with await semantic("fixture.v1") as session:
        tree = await session.elements()
        with pytest.raises(WindowError, match="element_not_editable"):
            await session.set_value(tree.elements[1].ref, "offline")
    assert backend.action_count == 0


@pytest.mark.asyncio
async def test_parameters_fail_before_native_dispatch(backend):
    for pid in (True, 1.2, "1", -1, 0, 2**32):
        with pytest.raises((ValueError, TypeError)):
            await window.list_windows(pid=pid)
    assert not backend.calls
    async with await capture() as session:
        before = list(backend.calls)
        for arguments in (
            {"quality": True},
            {"quality": 101},
            {"format": "png"},
            {"timeout_seconds": float("nan")},
            {"timeout_seconds": float("inf")},
            {"timeout_seconds": 0},
            {"timeout_seconds": True},
            {"timeout_seconds": 31},
        ):
            with pytest.raises((ValueError, TypeError)):
                await session.screenshot(**arguments)
        for arguments in (
            {"max_depth": True},
            {"max_depth": 33},
            {"max_nodes": 4097},
            {"max_nodes": 0},
        ):
            with pytest.raises((ValueError, TypeError)):
                await session.elements(**arguments)
        assert backend.calls == before


@pytest.mark.asyncio
async def test_canceled_open_closes_late_handle_through_repeated_cancels(backend):
    gate = Gate()
    backend.before_open = gate
    task = asyncio.create_task(capture())
    await gate.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    gate.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert backend.close_count == 1 and backend.handles[0].closed


@pytest.mark.asyncio
async def test_malformed_open_still_closes_owned_handle(backend, monkeypatch):
    original = backend._window_open

    def malformed(*args):
        raw = original(*args)
        raw["value"]["generation"] = True
        return raw

    monkeypatch.setattr(backend, "_window_open", malformed)
    with pytest.raises(WindowError, match="backend_protocol_error"):
        await capture()
    assert backend.close_count == 1


@pytest.mark.asyncio
async def test_canceled_attach_closes_late_binding(backend):
    session = await capture()
    ref = (await window.list_accessible_windows(123))[0].ref
    gate = Gate()
    backend.before_attach = gate
    task = asyncio.create_task(
        session.attach_accessibility(ref, profile_id="candidate")
    )
    await gate.wait()
    task.cancel()
    gate.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert session.closed and session.cleanup_complete and backend.close_count == 1


@pytest.mark.asyncio
async def test_close_is_shared_and_cancellation_does_not_abandon_it(backend):
    session = await capture()
    gate = Gate()
    backend.before_close = gate
    first = asyncio.create_task(session.aclose())
    await gate.wait()
    second = asyncio.create_task(session.aclose())
    first.cancel()
    await asyncio.sleep(0)
    first.cancel()
    gate.release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    await session.aclose()
    assert session.closed and session.cleanup_complete and backend.close_count == 1
    with pytest.raises(WindowError, match="session_closed"):
        await session.screenshot()


@pytest.mark.asyncio
async def test_cleanup_failure_is_not_reported_as_complete(backend):
    session = await capture()
    backend.close_result = failure("backend_busy")
    for _ in range(2):
        with pytest.raises(WindowError, match="backend_busy"):
            await session.aclose()
    assert session.closed and not session.cleanup_complete and backend.close_count == 1


@pytest.mark.asyncio
async def test_close_waits_for_capture_and_bounds_concurrent_work(backend):
    session = await capture()
    gate = Gate()
    backend.before_screenshot = gate
    capture_task = asyncio.create_task(session.screenshot())
    await gate.wait()
    with pytest.raises(WindowError, match="backend_busy"):
        await session.capabilities()
    close_task = asyncio.create_task(session.aclose())
    await asyncio.sleep(0)
    capture_task.cancel()
    gate.release.set()
    with pytest.raises(asyncio.CancelledError):
        await capture_task
    await close_task
    assert backend.calls.index("screenshot") < backend.calls.index("close")


@pytest.mark.asyncio
async def test_unknown_action_is_never_retried(backend, monkeypatch):
    approve_for_test(monkeypatch, backend)
    async with await semantic("fixture.v1") as session:
        tree = await session.elements()
        backend.action_result = failure("outcome_unknown", "unknown")
        with pytest.raises(WindowTimeoutError) as error:
            await session.invoke(tree.elements[2].ref)
        assert error.value.operation_id == session.last_action.operation_id
        assert (
            error.value.dispatch_state == "unknown"
            and error.value.retry_hint == "never"
        )
        original_receipt = session.last_action
        with pytest.raises(WindowError, match="outcome_unknown"):
            await session.invoke(tree.elements[2].ref)
        assert session.last_action is original_receipt
        assert backend.action_count == 1


@pytest.mark.asyncio
async def test_canceled_action_preserves_receipt_and_never_retries(
    backend, monkeypatch
):
    approve_for_test(monkeypatch, backend)
    async with await semantic("fixture.v1") as session:
        tree = await session.elements()
        gate = Gate()
        backend.before_action = gate
        canceled_ids = []

        async def invoke_and_observe():
            try:
                return await session.invoke(tree.elements[2].ref)
            except asyncio.CancelledError as error:
                canceled_ids.append(error.operation_id)
                raise

        task = asyncio.create_task(invoke_and_observe())
        await gate.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        gate.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Python 3.10 Task may recreate CancelledError at the awaiting boundary.
        # The session receipt is the durable source, even across that boundary.
        assert canceled_ids == [session.last_action.operation_id]
        assert session.last_action.dispatch_state == "unknown"
        with pytest.raises(WindowError, match="outcome_unknown"):
            await session.invoke(tree.elements[2].ref)
        assert backend.action_count == 1


@pytest.mark.asyncio
async def test_native_errors_are_structured_and_redacted(backend, monkeypatch):
    monkeypatch.setattr(
        backend, "_window_enumerate", lambda *a: failure("permission_denied")
    )
    with pytest.raises(WindowPermissionError) as error:
        await window.list_windows()
    assert error.value.code == "permission_denied"
    assert error.value.native_domain == "fixture" and error.value.native_code == 17
    assert error.value.dispatch_state == "not_dispatched"
    assert str(error.value) == "list_windows: permission_denied"


@pytest.mark.asyncio
async def test_canceled_queued_action_is_confirmed_not_dispatched(backend, monkeypatch):
    approve_for_test(monkeypatch, backend)
    async with await semantic("fixture.v1") as session:
        tree = await session.elements()
        gate = Gate()
        # Use a dedicated one-worker executor for this call, without replacing
        # the loop's executor or relying on scheduler timing.
        original = window._worker
        with ThreadPoolExecutor(max_workers=1) as executor:
            blocker = executor.submit(gate)

            def queued(native, name, *args):
                if name != "_window_action":
                    return original(native, name, *args)
                call = object.__new__(window._NativeCall)
                from threading import Lock

                call._lock = Lock()
                call._started = call._canceled = False
                call.future = asyncio.wrap_future(
                    executor.submit(call._run, getattr(native, name), args)
                )
                queued.entered.set()
                return call

            queued.entered = asyncio.Event()
            monkeypatch.setattr(window, "_worker", queued)
            task = asyncio.create_task(session.invoke(tree.elements[2].ref))
            await queued.entered.wait()
            task.cancel()
            await asyncio.sleep(0)
            gate.release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            blocker.result()
        assert session.last_action.dispatch_state == "not_dispatched"
        assert backend.action_count == 0
        assert not session._uncertain


@pytest.mark.asyncio
async def test_canceled_failed_attach_preserves_cancel_and_closes(backend):
    session = await capture()
    backend.bridge = False
    gate = Gate()
    backend.before_attach = gate
    ref = (await window.list_accessible_windows(123))[0].ref
    task = asyncio.create_task(
        session.attach_accessibility(ref, profile_id="candidate")
    )
    await gate.wait()
    task.cancel()
    gate.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert session.closed and session.cleanup_complete


@pytest.mark.asyncio
async def test_malformed_write_result_is_unknown_and_not_retried(backend, monkeypatch):
    approve_for_test(monkeypatch, backend)
    backend.action_result = ok(
        {
            "operation_id": "wrong-operation",
            "dispatch_state": "accepted",
            "effect_state": "verified",
        }
    )
    async with await semantic("fixture.v1") as session:
        tree = await session.elements()
        with pytest.raises(WindowTimeoutError, match="outcome_unknown"):
            await session.invoke(tree.elements[2].ref)
        assert session.last_action.dispatch_state == "unknown"
        with pytest.raises(WindowError, match="outcome_unknown"):
            await session.invoke(tree.elements[2].ref)
    assert backend.action_count == 1
