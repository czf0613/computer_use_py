"""Real MCP protocol and HTTP tests; device operations use an in-memory fake."""

import asyncio
import base64
import importlib
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client


class FakeComputer:
    def __init__(self):
        self.permissions = {"ScreenCapture": True, "Accessibility": True}
        self.cursor = {"x": 0, "y": 0}
        self.clipboard = "original clipboard"
        self.events = []
        self.active_captures = set()
        self.frames = [
            b"\xff\xd8\xff\xc0\x00\x0b\x08\x00\x64\x00\xc8\x01\x01\x11\x00\xff\xd9"
        ]

    def check_permission(self, name):
        return self.permissions[name]

    def list_displays(self):
        return [
            {
                "id": 7,
                "x": -100,
                "y": 30,
                "width": 100,
                "height": 50,
                "scale_factor": 2.0,
                "is_main": True,
            }
        ]

    def get_mouse_position(self):
        return self.cursor.copy()

    async def move_mouse(self, dest, **kwargs):
        self.cursor = dest.copy()
        self.events.append(("move", dest.copy()))
        await asyncio.sleep(0)

    async def mouse_click(self, key):
        self.events.append(("click", key, self.cursor.copy()))

    async def keyboard_click(self, key, modifiers=None):
        self.events.append(("key", key, modifiers))

    async def set_clipboard(self, text):
        self.clipboard = text

    async def get_clipboard(self):
        return self.clipboard

    async def clipboard_paste(self):
        self.events.append(("paste", self.clipboard))

    async def start_capture(self, display_id):
        self.active_captures.add(display_id)
        return display_id

    async def current_frame_jpg(self, handle, quality=80):
        return self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]

    async def stop_capture(self, handle):
        self.active_captures.remove(handle)


@pytest.fixture
def component():
    assert importlib.util.find_spec("scapkit_computer_use_mcp"), (
        "optional MCP package is missing"
    )
    package = importlib.import_module("scapkit_computer_use_mcp")
    backend = importlib.import_module("scapkit_computer_use_mcp.device")
    computer = FakeComputer()
    device = backend.Device(computer=computer, system="Darwin")
    return SimpleNamespace(package=package, computer=computer, device=device)


@pytest.mark.asyncio
async def test_discovery_exposes_instructions_tools_and_agent_guide(component):
    async with Client(
        component.package.create_server(device=component.device), mode="legacy"
    ) as client:
        assert "device_info" in client.instructions
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert {
            "device_info",
            "list_displays",
            "screenshot",
            "click",
            "press_key",
            "type_text",
            "run_subprocess",
        } <= tools.keys()
        assert tools["screenshot"].annotations.read_only_hint
        assert tools["click"].input_schema["properties"]["x"]["type"] == "integer"
        resources = await client.list_resources()
        guide_uri = str(resources.resources[0].uri)
        guide = await client.read_resource(guide_uri)
        assert "points" in guide.contents[0].text
        prompts = await client.list_prompts()
        prompt = await client.get_prompt(prompts.prompts[0].name)
        assert prompt.messages


@pytest.mark.asyncio
async def test_screenshot_returns_image_and_correct_retina_mapping_then_releases_capture(
    component,
):
    async with Client(
        component.package.create_server(device=component.device)
    ) as client:
        result = await client.call_tool("screenshot", {})
    assert not result.is_error
    metadata = result.structured_content
    assert metadata["display"]["x"] == -100
    assert metadata["image_width"] == 200
    assert metadata["image_height"] == 100
    assert metadata["points_per_pixel_x"] == 0.5
    (image,) = [item for item in result.content if item.type == "image"]
    assert image.mime_type == "image/jpeg"
    assert base64.b64decode(image.data) == component.computer.frames[0]
    assert not component.computer.active_captures


@pytest.mark.asyncio
async def test_concurrent_move_and_click_calls_cannot_interleave(component):
    async with Client(
        component.package.create_server(device=component.device)
    ) as client:
        results = await asyncio.gather(
            *[client.call_tool("click", {"x": x, "y": 40}) for x in (-90, -80)]
        )
    assert all(not result.is_error for result in results)
    assert component.computer.events == [
        ("move", {"x": -90, "y": 40}),
        ("click", "left", {"x": -90, "y": 40}),
        ("move", {"x": -80, "y": 40}),
        ("click", "left", {"x": -80, "y": 40}),
    ]


@pytest.mark.asyncio
async def test_missing_permission_is_actionable_without_input_events(component):
    component.computer.permissions["Accessibility"] = False
    async with Client(
        component.package.create_server(device=component.device)
    ) as client:
        result = await client.call_tool("click", {"x": 0, "y": 40})
    assert result.is_error
    assert "Accessibility" in result.content[0].text
    assert not component.computer.events


@pytest.mark.asyncio
async def test_fastapi_mount_serves_real_mcp_and_rejects_bad_auth_and_origins(
    component,
):
    app = component.package.create_app(device=component.device, token="test-token")
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "test-agent", "version": "1"},
        },
    }
    headers = {"Accept": "application/json, text/event-stream"}
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        assert (
            await client.post("/mcp", json=message, headers=headers)
        ).status_code == 401
        headers["Authorization"] = "Bearer test-token"
        response = await client.post("/mcp", json=message, headers=headers)
        assert response.status_code == 200
        assert "device_info" in response.json()["result"]["instructions"]
        assert (
            await client.post(
                "/mcp",
                json=message,
                headers={**headers, "Origin": "https://evil.example"},
            )
        ).status_code == 403
        assert (
            await client.post(
                "/mcp", json=message, headers={**headers, "Host": "evil.example"}
            )
        ).status_code == 421
        tools = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers=headers,
        )
        assert tools.status_code == 200
        assert tools.json()["result"]["tools"]
        assert (await client.get("/agent-guide", headers=headers)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_official_client_can_call_tools_through_fastapi_http(component, mode):
    app = component.package.create_app(device=component.device, token="test-token")
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            headers={"Authorization": "Bearer test-token"},
        ) as http,
    ):
        transport = streamable_http_client("http://127.0.0.1/mcp", http_client=http)
        async with Client(transport, mode=mode) as client:
            result = await client.call_tool("screenshot", {})
            assert not result.is_error
            assert result.structured_content["image_width"] == 200
            assert any(item.type == "image" for item in result.content)


@pytest.mark.asyncio
async def test_stdio_cli_is_discoverable_without_desktop_access():
    source = str(Path(__file__).resolve().parents[1] / "src")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "scapkit_computer_use_mcp", "--transport", "stdio"],
        env={"PYTHONPATH": source},
    )
    async with Client(params) as client:
        assert "device_info" in client.instructions
        assert "run_subprocess" in {
            tool.name for tool in (await client.list_tools()).tools
        }


@pytest.mark.asyncio
async def test_invalid_coordinates_and_key_modifiers_are_rejected_before_actions(
    component,
):
    async with Client(
        component.package.create_server(device=component.device)
    ) as client:
        for tool, arguments in [
            ("click", {"x": -1000, "y": 40}),
            ("click", {"x": -80.5, "y": 40}),
            ("click", {"x": -80, "y": 40, "hold_seconds": -1}),
            ("press_key", {"key": "a", "modifiers": ["not-a-modifier"]}),
        ]:
            result = await client.call_tool(tool, arguments)
            assert result.is_error
    assert not component.computer.events


@pytest.mark.asyncio
async def test_type_text_pastes_unicode_and_reports_clipboard_change(component):
    async with Client(
        component.package.create_server(device=component.device)
    ) as client:
        result = await client.call_tool("type_text", {"text": "你好 🌍"})
    assert not result.is_error
    assert result.structured_content["clipboard_overwritten"]
    assert component.computer.events == [("paste", "你好 🌍")]
    assert component.computer.clipboard == "你好 🌍"


@pytest.mark.asyncio
async def test_screenshot_timeout_releases_capture_and_returns_actionable_error(
    component,
):
    component.computer.frames = [None]
    async with Client(
        component.package.create_server(device=component.device)
    ) as client:
        result = await client.call_tool("screenshot", {"timeout_seconds": 0.1})
    assert result.is_error
    assert "frame" in result.content[0].text.lower()
    assert not component.computer.active_captures


@pytest.mark.asyncio
async def test_cancelling_during_capture_startup_releases_late_handle(component):
    started, release = asyncio.Event(), asyncio.Event()
    original = component.computer.start_capture

    async def slow_start(display_id):
        started.set()
        await release.wait()
        return await original(display_id)

    component.computer.start_capture = slow_start
    task = asyncio.create_task(component.device.screenshot())
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert component.device.lock.locked()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert not component.computer.active_captures
    assert not component.device.lock.locked()


@pytest.mark.asyncio
async def test_windows_does_not_advertise_unimplemented_desktop_tools(component):
    component.device.system = "Windows"
    async with Client(
        component.package.create_server(device=component.device)
    ) as client:
        tools = {tool.name for tool in (await client.list_tools()).tools}
        info = await client.call_tool("device_info", {})
    assert tools == {"device_info", "run_subprocess"}
    assert not info.structured_content["desktop_supported"]
    assert info.structured_content["permissions"] is None


@pytest.fixture
def command_client_config(component, tmp_path, monkeypatch):
    import pwd

    import scapkit_computer_use

    monkeypatch.setattr(
        pwd,
        "getpwuid",
        lambda uid: SimpleNamespace(
            pw_dir=str(tmp_path), pw_name="mcp-fixture", pw_shell="/bin/zsh"
        ),
    )
    (tmp_path / ".zshrc").write_text("export SCAPKIT_MCP_PROFILE=from-profile\n")
    component.device.computer = scapkit_computer_use
    component.device.system = (
        "Windows"  # Test only subprocess, never native desktop APIs.
    )
    return component.package.create_server(device=component.device)


@pytest.mark.asyncio
async def test_command_tool_bounds_both_outputs_and_uses_clean_environment(
    command_client_config, monkeypatch
):
    monkeypatch.setenv("SCAPKIT_MCP_SECRET", "must-not-inherit")
    code = "import os,sys; assert os.getenv('SCAPKIT_MCP_SECRET') is None; assert os.getenv('SCAPKIT_MCP_PROFILE') == 'from-profile'; sys.stdout.write('o'*200000); sys.stderr.write('e'*200000); sys.exit(7)"
    async with Client(command_client_config) as client:
        result = await client.call_tool(
            "run_subprocess",
            {
                "executable": sys.executable,
                "args": ["-I", "-S", "-c", code],
                "max_output_chars": 37,
            },
        )
    assert not result.is_error
    assert result.structured_content == {
        "returncode": 7,
        "stdout": "o" * 37,
        "stderr": "e" * 37,
        "stdout_truncated": True,
        "stderr_truncated": True,
    }


@pytest.mark.asyncio
async def test_command_timeout_reaps_child(command_client_config, tmp_path):
    pidfile = tmp_path / "command.pid"
    code = "import os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)"
    async with Client(command_client_config) as client:
        result = await client.call_tool(
            "run_subprocess",
            {
                "executable": sys.executable,
                "args": ["-I", "-S", "-c", code, str(pidfile)],
                "timeout_seconds": 0.5,
            },
        )
    assert result.is_error
    assert "exceeded" in result.content[0].text
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_cli_rejects_unauthenticated_remote_bind_without_starting_server(
    monkeypatch, capsys
):
    from scapkit_computer_use_mcp.__main__ import main

    monkeypatch.delenv("SCAPKIT_MCP_TOKEN", raising=False)
    with pytest.raises(SystemExit) as error:
        main(["--host", "0.0.0.0"])
    assert error.value.code == 2
    assert "bearer token" in capsys.readouterr().err


def test_base_import_and_cli_help_do_not_need_optional_dependencies():
    code = """
import importlib.abc, sys
class NoExtra(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in {'fastapi', 'mcp', 'uvicorn', 'pydantic'}:
            raise ModuleNotFoundError('optional dependency blocked', name=fullname)
sys.meta_path.insert(0, NoExtra())
import scapkit_computer_use
import scapkit_computer_use_mcp
assert callable(scapkit_computer_use.run_subprocess)
from scapkit_computer_use_mcp.__main__ import main
main(sys.argv[1:])
"""
    help_result = subprocess.run(
        [sys.executable, "-c", code, "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert help_result.returncode == 0
    assert "--transport" in help_result.stdout
    serve_result = subprocess.run(
        [sys.executable, "-c", code, "--transport", "stdio"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert serve_result.returncode == 2
    assert "scapkit_computer_use[mcp]" in serve_result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["type_text", "write_clipboard"])
async def test_cancelled_clipboard_write_finishes_before_next_input(
    component, operation
):
    started, release = asyncio.Event(), asyncio.Event()
    pending = []
    original = component.computer.set_clipboard

    async def delayed_write(text):
        if text == "A":

            async def commit():
                started.set()
                await release.wait()
                await original(text)

            # Like pbcopy, the external operation outlives cancellation of the
            # coroutine waiting for it. The server must settle it under its lock.
            task = asyncio.create_task(commit())
            pending.append(task)
            await asyncio.shield(task)
        else:
            await original(text)

    component.computer.set_clipboard = delayed_write
    call = (
        component.device.type_text("A")
        if operation == "type_text"
        else component.device.invoke("set_clipboard", "A")
    )
    first = asyncio.create_task(call)
    await started.wait()
    first.cancel()
    second = asyncio.create_task(component.device.type_text("B"))
    try:
        done, _ = await asyncio.wait({second}, timeout=0.05)
        assert not done, (
            "device lock was released before the earlier clipboard write finished"
        )
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await second
        assert component.computer.clipboard == "B"
        assert component.computer.events == [("paste", "B")]
    finally:
        release.set()
        await asyncio.gather(first, second, *pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_command_timeout_stops_descendants_even_with_redirected_pipes(
    command_client_config, tmp_path
):
    pidfile = tmp_path / "descendant.pid"
    code = (
        "import pathlib,subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-I','-S','-c','import time; time.sleep(60)'], "
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(60)"
    )
    async with Client(command_client_config) as client:
        result = await client.call_tool(
            "run_subprocess",
            {
                "executable": sys.executable,
                "args": ["-I", "-S", "-c", code, str(pidfile)],
                "timeout_seconds": 0.5,
            },
        )
    assert result.is_error
    pid = int(pidfile.read_text())
    try:
        deadline = time.monotonic() + 1
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            assert time.monotonic() < deadline, "descendant survived command timeout"
            await asyncio.sleep(0.01)
    finally:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass


@pytest.mark.asyncio
async def test_cleanup_timeout_preserves_original_decode_error(monkeypatch):
    from scapkit_computer_use_mcp import commands

    monkeypatch.setattr(commands, "os", SimpleNamespace(name="nt"), raising=False)

    class Process:
        returncode = None
        stdin = SimpleNamespace(close=lambda: None)

        async def read(self, size):
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte")

        async def wait(self):
            await asyncio.sleep(60)

        async def communicate(self):
            raise asyncio.TimeoutError()

        def kill(self):
            self.returncode = -9

    async def spawn(*args, **kwargs):
        process = Process()
        process.stdout = process.stderr = process
        return process

    with pytest.raises(UnicodeDecodeError):
        await commands.run_command(
            SimpleNamespace(run_subprocess=spawn),
            "fixture",
            [],
            None,
            None,
            None,
            30,
            100,
        )
