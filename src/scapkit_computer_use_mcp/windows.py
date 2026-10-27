"""Client-owned window API adapter. Never routes through the desktop Device.

SDK 2.2's ServerSession is per request. The narrow compatibility seam below
uses its Connection for stateful HTTP and our per-lifespan owner for stdio.
Unknown SDK/transport shapes fail closed, before acquiring window resources.
"""

import asyncio
import base64
import inspect
import json
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, fields, is_dataclass, replace
from functools import wraps
from typing import Annotated, Any, get_type_hints

import anyio
from mcp.server.connection import Connection
from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import ConfigDict, Field, ValidationError, create_model

from scapkit_computer_use import window
from scapkit_computer_use.window_errors import WindowError

from .device import finish

Token = Annotated[str, Field(strict=True, min_length=1, max_length=128)]
Profile = Annotated[str, Field(strict=True, min_length=1, max_length=4096)]
PID = Annotated[int, Field(strict=True, ge=1, le=2**32 - 1)]
Number = Annotated[float, Field(strict=True, ge=-1e12, le=1e12, allow_inf_nan=False)]


def dto(value, *, omit=()):
    """Serialize DTO fields without copying opaque refs or image buffers."""
    if is_dataclass(value):
        return {
            f.name: dto(getattr(value, f.name))
            for f in fields(value)
            if f.name not in {"ref", "data", "window_token", *omit}
        }
    if isinstance(value, (tuple, list)):
        return [dto(item) for item in value]
    return value


def result(data, *, error=False, image=None):
    content = [TextContent(text=json.dumps(data, allow_nan=False))]
    if image is not None:
        content.append(
            ImageContent(
                data=base64.b64encode(image).decode("ascii"), mimeType="image/jpeg"
            )
        )
    return CallToolResult(content=content, structuredContent=data, isError=error)


def failure_data(code, operation, *, error=None, dispatch="not_dispatched"):
    return {
        "ok": False,
        "code": code,
        "operation": operation,
        "operation_id": getattr(error, "operation_id", None),
        "dispatch_state": getattr(error, "dispatch_state", dispatch),
        "retry_hint": getattr(error, "retry_hint", "never"),
        "native_domain": getattr(error, "native_domain", None),
        "native_code": getattr(error, "native_code", None),
    }


def failure(code, operation, **kwargs):
    return result(failure_data(code, operation, **kwargs), error=True)


def reject(code):
    raise WindowError(code, "registry")


@dataclass(eq=False)
class ClientWindows:
    refs: dict = field(default_factory=dict)
    sessions: dict = field(default_factory=dict)
    active: set = field(default_factory=set)
    busy: bool = False
    closed: bool = False
    close_task: Any = None


@dataclass
class Entry:
    session: Any = None
    capture: bool = False
    touched: float = 0
    task: Any = None
    close_task: Any = None
    close_error: Any = None
    elements: dict = field(default_factory=dict)
    snapshot_id: str | None = None
    snapshot_expires: float = 0
    frame: Any = None
    frame_expires: float = 0


async def settled(coro):
    """Deliver cancellation once to Python, then drain under AnyIO shielding."""
    task = asyncio.create_task(coro)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancellation:
        task.cancel()
        try:
            await finish(task)
        except BaseException as cleanup_error:
            raise cancellation from cleanup_error
        raise


class WindowTools:
    # Limits include pending opens and incomplete closes. No unbounded waiters.
    MAX_CLIENTS = 32
    MAX_SESSIONS = 64
    CLIENT_SESSIONS = 8
    CLIENT_REFS = 256
    REF_TTL = 60.0
    IDLE_TTL = 300.0
    SNAPSHOT_TTL = 30.0
    SWEEP_SECONDS = 5.0

    def __init__(self):
        self.clients = set()
        self.schemas = {}
        self.clock = time.monotonic
        self._scope_key = object()

    def new_client(self):
        if len(self.clients) >= self.MAX_CLIENTS:
            reject("resource_limit")
        owner = ClientWindows()
        self.clients.add(owner)
        return owner

    @asynccontextmanager
    async def lifespan(self):
        # The SDK enters this once per stdio connection, once per HTTP host.
        # Allocate lazily: discovery/stateless/legacy tools need no window slots.
        scope = {"owner": None}
        stop = asyncio.Event()
        reaper = asyncio.create_task(self._reaper(stop))
        try:
            yield {self._scope_key: scope}
        finally:
            stop.set()
            reaper.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(reaper, return_exceptions=True)
                if scope["owner"] is not None:
                    await self.close_client(scope["owner"])
                # HTTP Connection.exit_stack owns its clients. The server runner
                # closes connections before leaving this lifespan.

    def owner(self, ctx):
        request = ctx.request_context
        session = request.session
        connection = getattr(session, "_connection", None)
        outbound = getattr(session, "_request_outbound", None)
        transport = getattr(outbound, "transport", None)
        kind = getattr(transport, "kind", None)
        if not isinstance(connection, Connection):
            reject("unsupported_transport")
        if kind == "streamable-http" or request.request is not None:
            # Use the SDK-validated Connection, never a raw header/client ID.
            if not connection.session_id:
                reject("unsupported_transport")
            owner = connection.state.get(self._scope_key)
            if owner is None:
                owner = self.new_client()
                connection.state[self._scope_key] = owner
                connection.exit_stack.push_async_callback(self.close_client, owner)
        elif kind in {"stdio", "jsonrpc", "direct"}:
            # Modern stdio makes a Connection per request, but retains lifespan.
            scope = request.lifespan_context.get(self._scope_key)
            if scope is None:
                reject("unsupported_transport")
            owner = scope["owner"]
            if owner is None:
                owner = scope["owner"] = self.new_client()
        else:
            reject("unsupported_transport")
        if owner.closed:
            reject("session_closed")
        return owner

    async def _reaper(self, stop):
        while not stop.is_set():
            await asyncio.sleep(self.SWEEP_SECONDS)
            await self.expire()

    async def expire(self):
        now = self.clock()
        for owner in tuple(self.clients):
            for token, (_, _, deadline) in tuple(owner.refs.items()):
                if deadline <= now:
                    owner.refs.pop(token, None)
            for token, entry in tuple(owner.sessions.items()):
                if entry.snapshot_expires <= now:
                    entry.elements.clear()
                    entry.snapshot_id = None
                if entry.frame_expires <= now:
                    entry.frame = None
                if entry.task is None and now - entry.touched >= self.IDLE_TTL:
                    await self.close_entry(entry)
                    if entry.session.cleanup_complete:
                        owner.sessions.pop(token, None)

    async def close_entry(self, entry):
        async def close():
            if entry.task is not None:
                await asyncio.gather(entry.task, return_exceptions=True)
            entry.elements.clear()
            entry.frame = None
            if entry.session is not None:
                try:
                    await entry.session.aclose()
                except Exception as error:  # noqa: BLE001 - retain failure, close all handles
                    entry.close_error = error
                    # Explicit close returns a structured incomplete result.
                    # Automatic cleanup must still attempt every other handle.

        if entry.close_task is None:
            entry.close_task = asyncio.create_task(close())
        await finish(entry.close_task)

    async def close_client(self, owner):
        async def close():
            owner.closed = True
            owner.refs.clear()
            for task in tuple(owner.active):
                task.cancel()
            await asyncio.gather(*owner.active, return_exceptions=True)
            for token, entry in tuple(owner.sessions.items()):
                await self.close_entry(entry)
                if entry.session.cleanup_complete:
                    owner.sessions.pop(token, None)
            # Native can outlive a disconnected transport. Keep unknown closes
            # charged to both limits, with their original receipt. Python's
            # closed flag, GC, or a new connection cannot confirm reclamation.
            if not owner.sessions:
                self.clients.discard(owner)

        if owner.close_task is None:
            owner.close_task = asyncio.create_task(close())
        await finish(owner.close_task)

    def candidate(self, owner, token, kind):
        item = owner.refs.get(token)
        if item is None or item[1] != kind or item[2] <= self.clock():
            reject("stale_window")
        del owner.refs[token]  # One attempt, never reconstruct a Python ref.
        return item[0]

    def entry(self, owner, token):
        entry = owner.sessions.get(token)
        if entry is None:
            reject("session_closed")
        if (
            entry.task is None
            and entry.close_task is None
            and self.clock() - entry.touched >= self.IDLE_TTL
        ):
            reject("session_closed")  # Reaper owns reclamation; no resurrection.
        entry.touched = self.clock()
        return entry

    @asynccontextmanager
    async def operation(self, entry):
        if entry.close_task is not None or entry.session.closed:
            reject("session_closed")
        if entry.task is not None:
            reject("backend_busy")
        entry.task = asyncio.current_task()
        try:
            yield entry.session
        finally:
            entry.task = None
            entry.touched = self.clock()

    async def enumerate(self, owner, kind, pid):
        if owner.busy:
            reject("backend_busy")
        owner.busy = True
        try:
            infos = await (
                window.list_windows(pid=pid)
                if kind == "capture"
                else window.list_accessible_windows(pid)
            )
            now = self.clock()
            owner.refs = {k: v for k, v in owner.refs.items() if v[2] > now}
            if len(owner.refs) + len(infos) > self.CLIENT_REFS:
                reject("resource_limit")
            rows = []
            for info in infos:
                token = secrets.token_urlsafe(24)
                owner.refs[token] = (info.ref, kind, now + self.REF_TTL)
                rows.append({**dto(info), "ref_token": token})
            return result({"windows": rows, "ref_ttl_seconds": self.REF_TTL})
        finally:
            owner.busy = False

    async def open(self, owner, token, kind, profile_id=None):
        if owner.busy:
            reject("backend_busy")
        # Successful explicit closes can be discarded to admit a new session.
        for old, entry in tuple(owner.sessions.items()):
            if (
                entry.close_task is not None
                and entry.close_task.done()
                and entry.session.cleanup_complete
            ):
                del owner.sessions[old]
        if (
            len(owner.sessions) >= self.CLIENT_SESSIONS
            or sum(len(c.sessions) for c in self.clients) >= self.MAX_SESSIONS
        ):
            reject("resource_limit")
        ref = self.candidate(owner, token, kind)
        session_token = secrets.token_urlsafe(24)
        entry = Entry(
            capture=kind == "capture", touched=self.clock(), task=asyncio.current_task()
        )
        owner.sessions[session_token] = entry  # Reserve before native admission.
        owner.busy = True
        try:
            entry.session = await (
                window.open_window(ref)
                if entry.capture
                else window.open_accessible_window(ref, profile_id=profile_id)
            )
            return result(
                {
                    "session_token": session_token,
                    "kind": kind,
                    "idle_ttl_seconds": self.IDLE_TTL,
                }
            )
        except BaseException:
            owner.sessions.pop(session_token, None)
            raise
        finally:
            owner.busy = False
            entry.task = None

    async def middleware(self, ctx, call_next):
        if ctx.method == "tools/call" and isinstance(ctx.params, dict):
            name = ctx.params.get("name")
            if isinstance(name, str) and name in self.schemas:
                try:
                    self.schemas[name].model_validate(ctx.params.get("arguments", {}))
                except (ValidationError, TypeError, ValueError):
                    # Do not echo entered values or SDK validation exception text.
                    return failure("invalid_arguments", name)
        return await call_next(ctx)

    def register(self, server):
        def tool(*, read_only=False):
            def register(fn):
                hints = get_type_hints(fn, include_extras=True)
                arguments = {}
                for name, param in inspect.signature(fn).parameters.items():
                    if name != "ctx":
                        default = (
                            ...
                            if param.default is inspect.Parameter.empty
                            else param.default
                        )
                        arguments[name] = (hints[name], default)
                self.schemas[fn.__name__] = create_model(
                    fn.__name__ + "Arguments",
                    __config__=ConfigDict(extra="forbid", strict=True),
                    **arguments,
                )

                @wraps(fn)
                async def invoke(*args, **kwargs):
                    async def run():
                        owner = None
                        try:
                            owner = self.owner(kwargs["ctx"])
                            owner.active.add(asyncio.current_task())
                            return await fn(*args, **kwargs)
                        except WindowError as error:
                            if owner is not None and error.code in {
                                "stale_window",
                                "window_closed",
                                "interference_detected",
                            }:
                                entry = owner.sessions.get(kwargs.get("session_token"))
                                if (
                                    entry is not None
                                    and error.window_token == entry.session.token
                                ):
                                    await self.close_entry(entry)
                            return failure(error.code, fn.__name__, error=error)
                        except (TypeError, ValueError):
                            return failure("invalid_arguments", fn.__name__)
                        except Exception:  # noqa: BLE001 - sanitize the protocol boundary
                            # Python's window API normally classifies failures.
                            # Never infer safe non-dispatch for an unexpected write.
                            write = fn.__name__ in {"window_set_value", "window_invoke"}
                            return failure(
                                "outcome_unknown"
                                if write
                                else "backend_protocol_error",
                                fn.__name__,
                                dispatch="unknown" if write else "not_dispatched",
                            )
                        finally:
                            if owner is not None:
                                owner.active.discard(asyncio.current_task())

                    return await settled(run())

                server.tool(
                    annotations=ToolAnnotations(
                        readOnlyHint=read_only,
                        destructiveHint=not read_only,
                        openWorldHint=True,
                    )
                )(invoke)
                return invoke

            return register

        def owned(ctx, token):
            return self.entry(self.owner(ctx), token)

        @tool(read_only=True)
        async def window_list(ctx: Context, pid: PID | None = None) -> CallToolResult:
            """List capture candidates for this client. Refs expire in 60 seconds and permit one open. Requires stdio or stateful HTTP; no global fallback."""
            return await self.enumerate(self.owner(ctx), "capture", pid)

        @tool(read_only=True)
        async def accessible_window_list(ctx: Context, pid: PID) -> CallToolResult:
            """List independent semantic roots for an explicit PID. Control-only roots are not screenshot targets."""
            return await self.enumerate(self.owner(ctx), "accessible", pid)

        @tool()
        async def window_open(ctx: Context, ref_token: Token) -> CallToolResult:
            """Open the exact client-owned capture ref. Does not attach accessibility or authorize background control."""
            return await self.open(self.owner(ctx), ref_token, "capture")

        @tool()
        async def accessible_window_open(
            ctx: Context, ref_token: Token, profile_id: Profile | None = None
        ) -> CallToolResult:
            """Open a control-only root, optionally selecting an installed profile_id. Unverified actions return background_not_verified; no evidence/unsafe override."""
            return await self.open(self.owner(ctx), ref_token, "accessible", profile_id)

        @tool()
        async def window_attach_accessibility(
            ctx: Context, session_token: Token, ref_token: Token, profile_id: Profile
        ) -> CallToolResult:
            """Explicitly attach a client-owned semantic root to this capture session. Python verifies the bridge; profile selection is not authorization."""
            owner = self.owner(ctx)
            entry = self.entry(owner, session_token)
            if not entry.capture:
                reject("unsupported_background_operation")
            async with self.operation(entry) as session:
                ref = self.candidate(owner, ref_token, "accessible")
                await session.attach_accessibility(ref, profile_id=profile_id)
                return result({"ok": True, "session_token": session_token})

        @tool(read_only=True)
        async def window_capabilities(
            ctx: Context, session_token: Token
        ) -> CallToolResult:
            """Query capture, inspection, identity and background validation separately. Screenshot success never authorizes writes. Re-query after errors; never bypass with global tools."""
            async with self.operation(owned(ctx, session_token)) as session:
                return result(
                    {
                        "session_token": session_token,
                        "capabilities": dto(await session.capabilities()),
                    }
                )

        @tool(read_only=True)
        async def window_screenshot(
            ctx: Context,
            session_token: Token,
            quality: Annotated[int, Field(strict=True, ge=0, le=100)] = 80,
            timeout_seconds: Annotated[
                float, Field(strict=True, gt=0, le=30, allow_inf_nan=False)
            ] = 5.0,
        ) -> CallToolResult:
            """Fresh window JPEG plus actual pixel dimensions and root-local affine metadata. No display cropping. Pass frame_id to window_element_at for image pixels; control-only targets reject capture."""
            entry = owned(ctx, session_token)
            if not entry.capture:
                reject("capture_unavailable")
            async with self.operation(entry) as session:
                frame = await session.screenshot(
                    format="jpeg", quality=quality, timeout_seconds=timeout_seconds
                )
                # Keep provenance only; do not retain the potentially large JPEG.
                entry.frame = replace(frame, data=b"")
                entry.frame_expires = self.clock() + self.SNAPSHOT_TTL
                return result(
                    {"session_token": session_token, **dto(frame)}, image=frame.data
                )

        @tool(read_only=True)
        async def window_elements(
            ctx: Context,
            session_token: Token,
            max_depth: Annotated[int, Field(strict=True, ge=0, le=32)] = 8,
            max_nodes: Annotated[int, Field(strict=True, ge=1, le=4096)] = 512,
        ) -> CallToolResult:
            """Bounded root-local tree without values. Replaces prior element tokens; references expire in 30 seconds and belong to this client/session only."""
            entry = owned(ctx, session_token)
            async with self.operation(entry) as session:
                entry.elements.clear()
                entry.snapshot_id = None
                tree = await session.elements(max_depth=max_depth, max_nodes=max_nodes)
                entry.snapshot_id = tree.snapshot_id
                entry.snapshot_expires = self.clock() + self.SNAPSHOT_TTL
                rows = []
                for info in tree.elements:
                    token = secrets.token_urlsafe(24)
                    entry.elements[token] = info.ref
                    rows.append({**dto(info), "element_token": token})
                return result(
                    {
                        "session_token": session_token,
                        **dto(tree, omit=("elements",)),
                        "elements": rows,
                    }
                )

        @tool(read_only=True)
        async def window_element_at(
            ctx: Context,
            session_token: Token,
            snapshot_id: Token,
            x: Number,
            y: Number,
            frame_id: Token | None = None,
        ) -> CallToolResult:
            """Resolve an element, never click. With frame_id x/y are original screenshot pixels (provenance checked); otherwise explicit root-local units. No global coordinates or UI scaling."""
            entry = owned(ctx, session_token)
            async with self.operation(entry) as session:
                if (
                    entry.snapshot_id != snapshot_id
                    or entry.snapshot_expires <= self.clock()
                ):
                    reject("stale_element")
                if frame_id is None:
                    ref = await session.element_at(x, y, snapshot_id=snapshot_id)
                else:
                    frame = entry.frame
                    if (
                        frame is None
                        or frame.frame_id != frame_id
                        or entry.frame_expires <= self.clock()
                    ):
                        reject("stale_geometry")
                    ref = await session.element_at(
                        frame.to_local(x, y), snapshot_id=snapshot_id
                    )
                for token, candidate in entry.elements.items():
                    if candidate is ref:
                        return result(
                            {
                                "session_token": session_token,
                                "snapshot_id": snapshot_id,
                                "element_token": token,
                            }
                        )
                reject("stale_element")

        async def action(ctx, session_token, element_token, action, value=None):
            entry = owned(ctx, session_token)
            async with self.operation(entry) as session:
                ref = entry.elements.get(element_token)
                if ref is None or entry.snapshot_expires <= self.clock():
                    reject("stale_element")
                receipt = await (
                    session.set_value(ref, value)
                    if action == "set_value"
                    else session.invoke(ref)
                )
                if receipt.dispatch_state == "unknown":
                    return result(
                        {
                            **failure_data(
                                "outcome_unknown",
                                "window_" + action,
                                error=receipt,
                                dispatch="unknown",
                            ),
                            "session_token": session_token,
                            **dto(receipt),
                        },
                        error=True,
                    )
                return result({"session_token": session_token, **dto(receipt)})

        @tool()
        async def window_set_value(
            ctx: Context,
            session_token: Token,
            element_token: Token,
            value: Annotated[str, Field(strict=True, max_length=65536)],
        ) -> CallToolResult:
            """Replace the entire semantic value using a verified exact profile. Never type/paste/click. Unknown may have taken effect: do not retry or reopen to retry; query window_status."""
            return await action(ctx, session_token, element_token, "set_value", value)

        @tool()
        async def window_invoke(
            ctx: Context, session_token: Token, element_token: Token
        ) -> CallToolResult:
            """Invoke the exact verified semantic action once. This is not a coordinate click. Unknown outcomes must never be retried, including in a new session."""
            return await action(ctx, session_token, element_token, "invoke")

        @tool(read_only=True)
        async def window_status(ctx: Context, session_token: Token) -> CallToolResult:
            """Read last_action and cleanup state without native calls, including while busy/after cancellation. Incomplete closes retain status for the owning connection; disconnect never makes it accessible to another client."""
            entry = owned(ctx, session_token)
            return result(
                {
                    "session_token": session_token,
                    "closed": entry.session.closed,
                    "cleanup_complete": entry.session.cleanup_complete,
                    "last_action": dto(entry.session.last_action),
                    "busy": entry.task is not None,
                }
            )

        @tool()
        async def window_close(ctx: Context, session_token: Token) -> CallToolResult:
            """Close this session, excluding new work and draining admitted work. Idempotent while its entry is retained. Reports incomplete native cleanup explicitly."""
            entry = owned(ctx, session_token)
            await self.close_entry(entry)
            complete = entry.session.cleanup_complete
            return result(
                {
                    "ok": complete,
                    "session_token": session_token,
                    "closed": entry.session.closed,
                    "cleanup_complete": complete,
                    "last_action": dto(entry.session.last_action),
                    **(
                        {}
                        if complete
                        else failure_data(
                            getattr(entry.close_error, "code", "cleanup_incomplete"),
                            "window_close",
                            error=entry.close_error,
                            dispatch="unknown",
                        )
                    ),
                },
                error=not complete,
            )
