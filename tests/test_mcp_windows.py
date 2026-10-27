"""Real MCP dispatch/lifecycles with the frozen Python API and fake native DTOs.

No desktop, clipboard, OS permission calls, or production evidence registration.
"""

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx2
import pytest
import window_fakes
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from window_fakes import Gate, approve_for_test, failure

from scapkit_computer_use import window
from scapkit_computer_use_mcp.device import Device
from scapkit_computer_use_mcp.server import create_app, create_server
from scapkit_computer_use_mcp.windows import WindowTools

backend = window_fakes.backend


class ForbiddenDesktop:
    def __getattr__(self, name):
        raise AssertionError("legacy desktop API reached: " + name)


@pytest.fixture
def harness(backend, monkeypatch):
    instances = []
    original = WindowTools.__init__

    def init(self):
        original(self)
        instances.append(self)

    monkeypatch.setattr(WindowTools, "__init__", init)
    device = Device(computer=ForbiddenDesktop(), system="Darwin")
    server = create_server(device=device)
    return SimpleNamespace(
        server=server, registry=instances[-1], device=device, registries=instances
    )


async def call(client, name, **kwargs):
    reply = await client.call_tool(name, kwargs)
    data = reply.structured_content
    assert data is not None, reply
    assert json.loads(reply.content[0].text) == data
    return reply, data


async def opened(client, *, capture=False, profile_id=None):
    prefix = "window" if capture else "accessible_window"
    _, listing = await call(
        client, prefix + "_list", **({} if capture else {"pid": 123})
    )
    args = {"ref_token": listing["windows"][0]["ref_token"]}
    if not capture:
        args["profile_id"] = profile_id
    reply, data = await call(client, prefix + "_open", **args)
    assert not reply.is_error, data
    return data["session_token"]


async def tree(client, token):
    reply, data = await call(client, "window_elements", session_token=token)
    assert not reply.is_error, data
    return data


@asynccontextmanager
async def http_client(app, *, mode="legacy", headers=None, terminate_on_close=True):
    async with (
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), headers=headers
        ) as http,
        Client(
            streamable_http_client(
                "http://127.0.0.1/mcp",
                http_client=http,
                terminate_on_close=terminate_on_close,
            ),
            mode=mode,
        ) as client,
    ):
        yield client


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_protocol_ref_session_element_ownership_and_disconnect(
    harness, backend, mode
):
    async with (
        Client(harness.server, mode=mode) as a,
        Client(harness.server, mode=mode) as b,
    ):
        _, listing = await call(a, "window_list")
        ref = listing["windows"][0]["ref_token"]
        denied, err = await call(b, "window_open", ref_token=ref)
        assert denied.is_error and err["code"] == "stale_window"
        good, _capture = await call(a, "window_open", ref_token=ref)
        assert not good.is_error
        again, err = await call(a, "window_open", ref_token=ref)
        assert again.is_error and err["code"] == "stale_window"
        token = await opened(a)
        other = await opened(b)
        for name in ("window_status", "window_capabilities", "window_close"):
            denied, err = await call(b, name, session_token=token)
            assert denied.is_error and err["code"] == "session_closed"
        nodes = await tree(a, token)
        await tree(b, other)
        denied, err = await call(
            b,
            "window_invoke",
            session_token=other,
            element_token=nodes["elements"][2]["element_token"],
        )
        assert denied.is_error and err["code"] == "stale_element"
        # Same-client cross-window refs are equally invalid.
        second = await opened(a)
        await tree(a, second)
        denied, err = await call(
            a,
            "window_invoke",
            session_token=second,
            element_token=nodes["elements"][2]["element_token"],
        )
        assert denied.is_error and err["code"] == "stale_element"
        assert backend.action_count == 0
    assert not harness.registry.clients
    assert all(h.closed for h in backend.handles)


@pytest.mark.asyncio
async def test_capture_metadata_explicit_bridge_and_provenance(harness, backend):
    async with Client(harness.server, mode="legacy") as client:
        token = await opened(client, capture=True)
        reply, image = await call(client, "window_screenshot", session_token=token)
        assert not reply.is_error
        assert base64.b64decode(reply.content[1].data) == window_fakes.jpeg_header(
            200, 160
        )
        assert (image["image_width"], image["image_height"]) == (200, 160)
        assert image["window_bounds"]["x"] == -200
        assert image["local_rect"]["x"] == 0
        assert image["coordinate_unit"] == "points"
        assert image["image_to_local"] == [0.5, 0, 0, 0.5, 0, 0]
        assert "window_token" not in image and "data" not in image
        error, _ = await call(client, "window_elements", session_token=token)
        assert error.is_error
        _, listing = await call(client, "accessible_window_list", pid=123)
        reply, _ = await call(
            client,
            "window_attach_accessibility",
            session_token=token,
            ref_token=listing["windows"][0]["ref_token"],
            profile_id="not-verified",
        )
        assert not reply.is_error
        nodes = await tree(client, token)
        reply, hit = await call(
            client,
            "window_element_at",
            session_token=token,
            snapshot_id=nodes["snapshot_id"],
            frame_id=image["frame_id"],
            x=30,
            y=30,
        )
        assert not reply.is_error
        assert hit["element_token"] == nodes["elements"][1]["element_token"]
        reply, caps = await call(client, "window_capabilities", session_token=token)
        assert (
            caps["capabilities"]["set_value"]["reason_code"]
            == "background_not_verified"
        )
        reply, err = await call(
            client,
            "window_set_value",
            session_token=token,
            element_token=hit["element_token"],
            value="sensitive input",
        )
        assert reply.is_error and err["code"] == "background_not_verified"
        assert err["operation_id"] and err["dispatch_state"] == "not_dispatched"
        assert "sensitive input" not in str(reply)
        backend.revision += 1
        reply, err = await call(
            client,
            "window_element_at",
            session_token=token,
            snapshot_id=nodes["snapshot_id"],
            frame_id=image["frame_id"],
            x=30,
            y=30,
        )
        assert reply.is_error and err["code"] in {"stale_geometry", "geometry_changed"}
        control = await opened(client)
        reply, err = await call(client, "window_screenshot", session_token=control)
        assert reply.is_error and err["code"] == "capture_unavailable"
    assert backend.action_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stateful,mode", [(False, "legacy"), (False, "2026-07-28"), (True, "2026-07-28")]
)
async def test_http_without_trusted_session_rejects_all_window_tools(
    harness, backend, stateful, mode
):
    app = create_app(device=harness.device, stateless_http=not stateful)
    async with app.router.lifespan_context(app), http_client(app, mode=mode) as client:
        for name, args in [
            ("window_list", {}),
            ("accessible_window_list", {"pid": 123}),
            ("window_open", {"ref_token": "forged"}),
            ("window_status", {"session_token": "forged"}),
            ("window_close", {"session_token": "forged"}),
        ]:
            reply, err = await call(client, name, **args)
            assert reply.is_error and err["code"] == "unsupported_transport"
    assert backend.calls == []


@pytest.mark.asyncio
async def test_stateful_http_ownership_delete_and_shutdown(harness, backend):
    app = create_app(device=harness.device, stateless_http=False, token="fixture-token")
    headers = {"Authorization": "Bearer fixture-token"}
    async with app.router.lifespan_context(app):
        async with http_client(app, headers=headers) as a:
            token = await opened(a)
            async with http_client(app, headers=headers) as b:
                reply, err = await call(b, "window_status", session_token=token)
                assert reply.is_error and err["code"] == "session_closed"
                await opened(b)
            for _ in range(100):
                if backend.close_count:
                    break
                await asyncio.sleep(0.01)
            assert backend.close_count == 1
            reply, _ = await call(a, "window_capabilities", session_token=token)
            assert not reply.is_error
        for _ in range(100):
            if backend.close_count == 2:
                break
            await asyncio.sleep(0.01)
        assert backend.close_count == 2
    assert all(h.closed for h in backend.handles)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,args",
    [
        ("window_list", {"pid": True}),
        ("window_list", {"pid": 0}),
        ("window_list", {"pid": "123"}),
        ("window_list", {"pid": 2**32}),
        ("window_list", {"client_id": "forged"}),
        ("window_open", {"ref_token": 1}),
        ("window_open", {"ref_token": ""}),
        ("window_open", {"ref_token": "a" * 129}),
        ("window_screenshot", {"session_token": "x", "quality": True}),
        ("window_screenshot", {"session_token": "x", "quality": 101}),
        ("window_screenshot", {"session_token": "x", "timeout_seconds": 0}),
        ("window_screenshot", {"session_token": "x", "timeout_seconds": "5"}),
        ("window_screenshot", {"session_token": "x", "timeout_seconds": 31}),
        ("window_elements", {"session_token": "x", "max_nodes": 4097}),
        ("window_elements", {"session_token": "x", "max_depth": -1}),
        ("window_elements", {"session_token": "x", "max_nodes": True}),
        (
            "window_element_at",
            {"session_token": "x", "snapshot_id": "s", "x": True, "y": 0},
        ),
        (
            "window_element_at",
            {"session_token": "x", "snapshot_id": "s", "x": 1e13, "y": 0},
        ),
        ("window_set_value", {"session_token": "x", "element_token": "e", "value": 9}),
        (
            "window_set_value",
            {"session_token": "x", "element_token": "e", "value": "v" * 65537},
        ),
        ("accessible_window_open", {"ref_token": "x", "profile_id": True}),
        ("accessible_window_open", {"ref_token": "x", "evidence": "not authority"}),
        ("window_invoke", {"session_token": "x", "element_token": "x", "unsafe": True}),
    ],
)
async def test_invalid_arguments_are_structured_before_dispatch(
    harness, backend, name, args
):
    async with Client(harness.server, mode="legacy") as client:
        reply, err = await call(client, name, **args)
        assert reply.is_error and err["code"] == "invalid_arguments"
        assert err["operation_id"] is None and err["dispatch_state"] == "not_dispatched"
        assert err["retry_hint"] == "never"
    assert backend.calls == []


@pytest.mark.asyncio
async def test_limits_expiry_no_resurrection_and_snapshot_replacement(harness, backend):
    registry = harness.registry
    now = [registry.clock()]
    registry.clock = lambda: now[0]
    registry.CLIENT_SESSIONS = 1
    registry.CLIENT_REFS = 1
    async with Client(harness.server, mode="legacy") as client:
        _, listing = await call(client, "window_list")
        ref = listing["windows"][0]["ref_token"]
        reply, err = await call(client, "window_list")
        assert reply.is_error and err["code"] == "resource_limit"
        now[0] += 61
        reply, err = await call(client, "window_open", ref_token=ref)
        assert reply.is_error and err["code"] == "stale_window"
        await registry.expire()
        token = await opened(client)
        nodes = await tree(client, token)
        new = await tree(client, token)
        reply, err = await call(
            client,
            "window_invoke",
            session_token=token,
            element_token=nodes["elements"][2]["element_token"],
        )
        assert reply.is_error and err["code"] == "stale_element"
        _, listing = await call(client, "window_list")
        reply, err = await call(
            client, "window_open", ref_token=listing["windows"][0]["ref_token"]
        )
        assert reply.is_error and err["code"] == "resource_limit"
        assert len(backend.handles) == 1
        now[0] += 31
        reply, err = await call(
            client,
            "window_invoke",
            session_token=token,
            element_token=new["elements"][2]["element_token"],
        )
        assert reply.is_error and err["code"] == "stale_element"
        now[0] += 301
        reply, err = await call(client, "window_capabilities", session_token=token)
        assert reply.is_error and err["code"] == "session_closed"
        await registry.expire()
        assert backend.close_count == 1
        assert all(not c.sessions and not c.refs for c in registry.clients)


@pytest.mark.asyncio
async def test_unknown_error_and_receipt_do_not_retry(harness, backend, monkeypatch):
    approve_for_test(monkeypatch, backend)
    async with Client(harness.server, mode="legacy") as client:
        token = await opened(client, profile_id="fixture.v1")
        nodes = await tree(client, token)
        backend.action_result = failure("outcome_unknown", "unknown")
        args = {
            "session_token": token,
            "element_token": nodes["elements"][2]["element_token"],
        }
        reply, err = await call(client, "window_invoke", **args)
        assert reply.is_error and err["dispatch_state"] == "unknown"
        assert err["retry_hint"] == "never" and err["operation_id"]
        reply, again = await call(client, "window_invoke", **args)
        assert reply.is_error and again["operation_id"] == err["operation_id"]
        _, state = await call(client, "window_status", session_token=token)
        assert state["last_action"]["operation_id"] == err["operation_id"]
        assert state["last_action"]["dispatch_state"] == "unknown"
        assert backend.action_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["open", "screenshot", "attach", "action"])
async def test_cancel_drains_resources_and_retains_action_id(
    harness, backend, monkeypatch, phase
):
    approve_for_test(monkeypatch, backend)
    gate = Gate()
    async with Client(harness.server, mode="legacy") as client:
        if phase == "open":
            _, listing = await call(client, "window_list")
            name, args = (
                "window_open",
                {"ref_token": listing["windows"][0]["ref_token"]},
            )
        else:
            token = await opened(
                client, capture=phase != "action", profile_id="fixture.v1"
            )
            if phase == "screenshot":
                name, args = "window_screenshot", {"session_token": token}
            elif phase == "attach":
                _, listing = await call(client, "accessible_window_list", pid=123)
                name, args = (
                    "window_attach_accessibility",
                    {
                        "session_token": token,
                        "ref_token": listing["windows"][0]["ref_token"],
                        "profile_id": "fixture.v1",
                    },
                )
            else:
                nodes = await tree(client, token)
                name, args = (
                    "window_invoke",
                    {
                        "session_token": token,
                        "element_token": nodes["elements"][2]["element_token"],
                    },
                )
        setattr(backend, "before_" + phase, gate)
        task = asyncio.create_task(client.call_tool(name, args))
        try:
            await gate.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            # Cancellation is transported as a notification; let the server see it.
            await asyncio.sleep(0.03)
            assert any(c.active for c in harness.registry.clients)
        finally:
            gate.release.set()
        for _ in range(200):
            if not any(c.active for c in harness.registry.clients):
                break
            await asyncio.sleep(0.01)
        assert not any(c.active for c in harness.registry.clients)
        if phase == "action":
            _, state = await call(client, "window_status", session_token=token)
            assert state["last_action"]["operation_id"]
            assert state["last_action"]["dispatch_state"] == "unknown"
            assert backend.action_count == 1
        elif phase in {"open", "attach"}:
            assert all(h.closed for h in backend.handles)
    assert all(h.closed for h in backend.handles)


@pytest.mark.asyncio
async def test_close_race_excludes_work_and_reports_incomplete_cleanup(
    harness, backend
):
    async with Client(harness.server, mode="legacy") as client:
        token = await opened(client, capture=True)
        gate = backend.before_screenshot = Gate()
        shot = asyncio.create_task(
            client.call_tool("window_screenshot", {"session_token": token})
        )
        await gate.wait()
        close = asyncio.create_task(
            client.call_tool("window_close", {"session_token": token})
        )
        await asyncio.sleep(0.03)
        try:
            reply, err = await call(client, "window_capabilities", session_token=token)
            assert reply.is_error and err["code"] == "session_closed"
            assert not close.done()
        finally:
            gate.release.set()
        assert not (await shot).is_error
        assert not (await close).is_error
        reply, state = await call(client, "window_close", session_token=token)
        assert not reply.is_error and state["cleanup_complete"]
        assert backend.close_count == 1
        other = await opened(client)
        backend.close_result = failure("backend_busy")
        reply, err = await call(client, "window_close", session_token=other)
        assert reply.is_error and err["code"] == "backend_busy"
        assert err["closed"] and not err["cleanup_complete"]


@pytest.fixture
def incomplete_native_close(backend, monkeypatch):
    """Keep a native resource outstanding, independently of Python closed state."""
    attempts = []

    def install(code):
        def close(handle):
            attempts.append(handle)
            return failure(code)

        monkeypatch.setattr(backend, "_window_close", close)
        return attempts

    return install


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["backend_busy", "outcome_unknown"])
@pytest.mark.parametrize("explicit_close", [False, True])
async def test_incomplete_idle_close_preserves_capacity_and_status(
    harness, backend, incomplete_native_close, code, explicit_close
):
    registry = harness.registry
    registry.CLIENT_SESSIONS = 1
    now = [registry.clock()]
    registry.clock = lambda: now[0]
    attempts = incomplete_native_close(code)
    async with Client(harness.server, mode="legacy") as client:
        token = await opened(client)
        if explicit_close:
            reply, state = await call(client, "window_close", session_token=token)
            assert reply.is_error and state["code"] == code
        now[0] += registry.IDLE_TTL + 1
        await registry.expire()
        _, listing = await call(client, "window_list")
        reply, state = await call(
            client, "window_open", ref_token=listing["windows"][0]["ref_token"]
        )
        assert reply.is_error and state["code"] == "resource_limit"
        reply, state = await call(client, "window_status", session_token=token)
        assert not reply.is_error and state["closed"]
        assert not state["cleanup_complete"]
        reply, state = await call(client, "window_close", session_token=token)
        assert reply.is_error and state["code"] == code
        now[0] += registry.IDLE_TTL + 1
        await registry.expire()
        assert len(attempts) == len(backend.handles) == 1
        assert not backend.handles[0].closed  # No native cleanup confirmation.


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["legacy", "2026-07-28", "http"])
@pytest.mark.parametrize("code", ["backend_busy", "outcome_unknown"])
async def test_disconnect_with_incomplete_close_cannot_reclaim_global_capacity(
    harness, backend, incomplete_native_close, transport, code
):
    registry = harness.registry
    registry.MAX_SESSIONS = 1
    attempts = incomplete_native_close(code)
    app = create_app(device=harness.device, stateless_http=False)
    # create_app owns its own registry, just as a separate server process would.
    if transport == "http":
        registry = harness.registries[-1]
        registry.MAX_SESSIONS = 1

    async with app.router.lifespan_context(app):
        factory = (
            (lambda: http_client(app))
            if transport == "http"
            else (lambda: Client(harness.server, mode=transport))
        )
        async with factory() as client:
            token = await opened(client)
        # HTTP DELETE cleanup runs on the SDK connection task.
        for _ in range(100):
            if attempts:
                break
            await asyncio.sleep(0.01)
        assert len(attempts) == 1
        async with factory() as other:
            _, listing = await call(other, "window_list")
            reply, state = await call(
                other, "window_open", ref_token=listing["windows"][0]["ref_token"]
            )
            assert reply.is_error and state["code"] == "resource_limit"
            reply, state = await call(other, "window_status", session_token=token)
            assert reply.is_error and state["code"] == "session_closed"
        await registry.expire()
        assert len(attempts) == len(backend.handles) == 1
        assert not backend.handles[0].closed
        assert len(registry.clients) == 1
        owner = next(iter(registry.clients))
        assert owner.closed and not owner.refs
        assert not owner.sessions[token].session.cleanup_complete
        # Repeated disconnections cannot grow the retained-owner set either.
        registry.MAX_CLIENTS = 1
        async with factory() as other:
            reply, state = await call(other, "window_list")
            assert reply.is_error and state["code"] == "resource_limit"


@pytest.mark.asyncio
async def test_product_without_native_abi_is_honest(monkeypatch):
    monkeypatch.setattr(window, "import_module", lambda *args: SimpleNamespace())
    async with Client(
        create_server(device=Device(system="Darwin")), mode="legacy"
    ) as client:
        reply, err = await call(client, "window_list")
        assert reply.is_error and err["code"] == "backend_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_real_stdio_pipe_lifecycle_with_fake_backend(tmp_path, mode):
    import sys
    from pathlib import Path

    from mcp.client.stdio import StdioServerParameters

    receipt = tmp_path / "closed.json"
    helper = Path(__file__).with_name("mcp_window_stdio.py")
    async with Client(
        StdioServerParameters(
            command=sys.executable, args=["-I", str(helper), str(receipt)]
        ),
        mode=mode,
    ) as client:
        token = await opened(client)
        nodes = await tree(client, token)
        assert len(nodes["elements"]) == 3
    assert json.loads(receipt.read_text()) == {
        "opened": 1,
        "closed": 1,
        "all_closed": True,
    }


@pytest.mark.asyncio
async def test_total_and_client_limits_reserve_pending_opens(harness, backend):
    harness.registry.MAX_SESSIONS = 1
    async with (
        Client(harness.server, mode="legacy") as a,
        Client(harness.server, mode="legacy") as b,
    ):
        _, listing = await call(a, "window_list")
        _, second = await call(b, "window_list")
        gate = backend.before_open = Gate()
        task = asyncio.create_task(
            a.call_tool(
                "window_open", {"ref_token": listing["windows"][0]["ref_token"]}
            )
        )
        await gate.wait()
        try:
            reply, err = await call(
                b, "window_open", ref_token=second["windows"][0]["ref_token"]
            )
            assert reply.is_error and err["code"] == "resource_limit"
        finally:
            gate.release.set()
        assert not (await task).is_error
    assert backend.close_count == 1
    harness.registry.MAX_CLIENTS = 1
    async with (
        Client(harness.server, mode="legacy") as a,
        Client(harness.server, mode="legacy") as b,
    ):
        await opened(a)
        reply, err = await call(b, "window_list")
        assert reply.is_error and err["code"] == "resource_limit"


@pytest.mark.asyncio
async def test_idle_reaper_runs_without_another_call(harness, backend, monkeypatch):
    now = [harness.registry.clock()]
    harness.registry.clock = lambda: now[0]
    harness.registry.SWEEP_SECONDS = 0.01
    close_return = Gate()
    original_close = backend._window_close

    def delayed_close(handle):
        result = original_close(handle)
        # close_count is visible before the worker returns to Python cleanup.
        close_return()
        return result

    monkeypatch.setattr(backend, "_window_close", delayed_close)
    async with Client(harness.server, mode="legacy") as client:
        token = await opened(client)
        now[0] += 301
        try:
            await close_return.wait()
            assert backend.close_count == 1
            entry = next(c.sessions[token] for c in harness.registry.clients if token in c.sessions)
            assert not entry.session.cleanup_complete
        finally:
            close_return.release.set()
        for _ in range(200):
            if backend.close_count == 1 and all(not c.sessions for c in harness.registry.clients):
                break
            await asyncio.sleep(0.01)
        assert backend.close_count == 1
        assert all(not c.sessions for c in harness.registry.clients)
        assert entry.session.cleanup_complete


@pytest.mark.asyncio
async def test_target_process_invalidation_closes_session(harness, backend):
    async with Client(harness.server, mode="legacy") as client:
        token = await opened(client)
        backend.instance = "different-process-instance"
        reply, err = await call(client, "window_capabilities", session_token=token)
        assert reply.is_error and err["code"] == "stale_window"
        assert backend.close_count == 1
        _, state = await call(client, "window_status", session_token=token)
        assert state["closed"] and state["cleanup_complete"]


@pytest.mark.asyncio
async def test_unknown_success_envelope_is_error_and_never_retried(
    harness, backend, monkeypatch
):
    approve_for_test(monkeypatch, backend)
    original = backend._window_action

    def unknown(*args):
        reply = original(*args)
        reply["value"]["dispatch_state"] = "unknown"
        return reply

    monkeypatch.setattr(backend, "_window_action", unknown)
    async with Client(harness.server, mode="legacy") as client:
        token = await opened(client, profile_id="fixture.v1")
        nodes = await tree(client, token)
        args = {
            "session_token": token,
            "element_token": nodes["elements"][2]["element_token"],
        }
        reply, err = await call(client, "window_invoke", **args)
        assert reply.is_error and err["code"] == "outcome_unknown"
        assert err["dispatch_state"] == "unknown" and err["operation_id"]
        await call(client, "window_invoke", **args)
        assert backend.action_count == 1


@pytest.mark.asyncio
async def test_foreign_attach_ref_does_not_close_owned_capture(harness, backend):
    async with (
        Client(harness.server, mode="legacy") as a,
        Client(harness.server, mode="legacy") as b,
    ):
        token = await opened(a, capture=True)
        _, listing = await call(b, "accessible_window_list", pid=123)
        reply, err = await call(
            a,
            "window_attach_accessibility",
            session_token=token,
            ref_token=listing["windows"][0]["ref_token"],
            profile_id="not-verified",
        )
        assert reply.is_error and err["code"] == "stale_window"
        reply, state = await call(a, "window_status", session_token=token)
        assert not reply.is_error and not state["closed"]
        assert backend.close_count == 0


@pytest.mark.asyncio
async def test_mapping_unavailable_and_expired_frame_do_not_hit_test(harness, backend):
    now = [harness.registry.clock()]
    harness.registry.clock = lambda: now[0]
    async with Client(harness.server, mode="legacy") as client:
        token = await opened(client, capture=True)
        _, listing = await call(client, "accessible_window_list", pid=123)
        await call(
            client,
            "window_attach_accessibility",
            session_token=token,
            ref_token=listing["windows"][0]["ref_token"],
            profile_id="unverified",
        )
        backend.frame_changes = {
            "mapping_status": "unavailable",
            "local_rect": None,
            "image_content_rect_px": None,
            "image_to_local": None,
        }
        reply, frame = await call(client, "window_screenshot", session_token=token)
        assert not reply.is_error and frame["mapping_status"] == "unavailable"
        nodes = await tree(client, token)
        reply, err = await call(
            client,
            "window_element_at",
            session_token=token,
            snapshot_id=nodes["snapshot_id"],
            frame_id=frame["frame_id"],
            x=30,
            y=30,
        )
        assert reply.is_error and err["code"] == "mapping_unavailable"
        backend.frame_changes = {}
        _, frame = await call(client, "window_screenshot", session_token=token)
        now[0] += 31
        nodes = await tree(client, token)
        reply, err = await call(
            client,
            "window_element_at",
            session_token=token,
            snapshot_id=nodes["snapshot_id"],
            frame_id=frame["frame_id"],
            x=30,
            y=30,
        )
        assert reply.is_error and err["code"] == "stale_geometry"


@pytest.mark.asyncio
async def test_cancelled_close_still_completes_once(harness, backend):
    async with Client(harness.server, mode="legacy") as client:
        token = await opened(client)
        gate = backend.before_close = Gate()
        task = asyncio.create_task(
            client.call_tool("window_close", {"session_token": token})
        )
        try:
            await gate.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0.03)
        finally:
            gate.release.set()
        for _ in range(200):
            if backend.close_count:
                break
            await asyncio.sleep(0.01)
        reply, state = await call(client, "window_close", session_token=token)
        assert not reply.is_error and state["cleanup_complete"]
        assert backend.close_count == 1


@pytest.mark.asyncio
async def test_stateful_http_idle_session_cleanup(harness, backend):
    app = create_app(device=harness.device, stateless_http=False)
    app.state.mcp.session_manager.session_idle_timeout = 0.05
    async with app.router.lifespan_context(app):
        # The SDK deliberately exempts an in-flight SSE GET from idle timeout.
        # Disconnect without DELETE to leave a genuinely idle server session.
        async with http_client(app, terminate_on_close=False) as client:
            await opened(client)
        for _ in range(200):
            if backend.close_count:
                break
            await asyncio.sleep(0.01)
        assert backend.close_count == 1


@pytest.mark.asyncio
async def test_parameter_failures_never_log_tokens_titles_or_values(
    harness, backend, caplog
):
    caplog.set_level("INFO")
    async with Client(harness.server, mode="legacy") as client:
        token = await opened(client)
        nodes = await tree(client, token)
        element = nodes["elements"][1]["element_token"]
        await call(
            client,
            "window_set_value",
            session_token=token,
            element_token=element,
            value={"secret": "private-value-sentinel"},
        )
        await call(
            client,
            "window_set_value",
            session_token=token,
            element_token=element,
            value="private-value-sentinel",
        )
    assert token not in caplog.text and element not in caplog.text
    assert "private-value-sentinel" not in caplog.text
    assert "same-title" not in caplog.text
