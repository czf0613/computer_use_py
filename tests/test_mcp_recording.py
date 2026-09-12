"""Recording tool contracts with fake media acquisition, never the real desktop."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from test_mcp_server import component  # Reuse the fake desktop and server fixture.


@pytest.fixture
def recorder(component):
    computer = component.computer
    computer.recordings = {}
    computer.recording_calls = []
    computer.stop_count = 0

    async def start(display_id, output_path, fps=30, *, video_quality=0.75):
        path = Path(output_path).absolute()
        if path.exists():
            raise FileExistsError(str(path))
        if not path.parent.is_dir():
            raise FileNotFoundError(str(path.parent))
        handle = object()
        computer.recordings[handle] = (path, fps)
        computer.recording_calls.append((display_id, str(path), fps, video_quality))
        return handle

    async def stop(handle):
        path, fps = computer.recordings.pop(handle)
        computer.stop_count += 1
        path.write_bytes(b"fake completed recording")
        return SimpleNamespace(
            path=path, size_bytes=24, duration_s=1.25, width=200, height=100, fps=fps
        )

    computer.start_recording = start
    computer.stop_recording = stop
    return component


@pytest.mark.asyncio
async def test_recording_tools_preserve_defaults_and_leave_other_actions_available(
    recorder, tmp_path
):
    server = recorder.package.create_server(device=recorder.device)
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert {"start_recording", "stop_recording", "recording_status"} <= tools.keys()
        assert tools["recording_status"].annotations.read_only_hint
        initial = await client.call_tool("recording_status", {})
        assert initial.structured_content == {"state": "idle"}
        path = tmp_path / "recording.mp4"
        started = await client.call_tool("start_recording", {"output_path": str(path)})
        assert not started.is_error
        info = started.structured_content
        assert info["state"] == "recording"
        assert info["display_id"] == 7
        assert info["path"] == str(path)
        assert recorder.computer.recording_calls == [(7, str(path), 30, 0.75)]
        action = await asyncio.wait_for(
            client.call_tool("click", {"x": -80, "y": 40}), 1
        )
        assert not action.is_error
        assert recorder.computer.recordings
        args = {"recording_id": info["recording_id"]}
        stopped = await client.call_tool("stop_recording", args)
        again = await client.call_tool("stop_recording", args)
        assert not stopped.is_error
        assert stopped.structured_content == again.structured_content
        assert stopped.structured_content["state"] == "completed"
        assert stopped.structured_content["result"] == {
            "path": str(path),
            "size_bytes": 24,
            "duration_s": 1.25,
            "width": 200,
            "height": 100,
            "fps": 30,
        }
        assert path.read_bytes() == b"fake completed recording"
        assert recorder.computer.stop_count == 1
        assert not recorder.computer.recordings


@pytest.mark.asyncio
@pytest.mark.parametrize("quality", [None, 0.0, 1.0])
async def test_recording_tools_forward_explicit_display_fps_and_quality(
    recorder, tmp_path, quality
):
    async with Client(recorder.package.create_server(device=recorder.device)) as client:
        path = tmp_path / "custom.mp4"
        result = await client.call_tool(
            "start_recording",
            {
                "output_path": str(path),
                "display_id": 7,
                "fps": 24,
                "video_quality": quality,
            },
        )
        assert not result.is_error
        assert recorder.computer.recording_calls == [(7, str(path), 24, quality)]
    # The same MCP server lifespan used by stdio finalizes an unfinished recording.
    assert path.is_file()
    assert not recorder.computer.recordings


@pytest.mark.asyncio
async def test_recording_rejects_invalid_parameters_before_acquisition(
    recorder, tmp_path
):
    async with Client(recorder.package.create_server(device=recorder.device)) as client:
        for override in [
            {"fps": 0},
            {"fps": True},
            {"fps": 1.5},
            {"fps": 2**31},
            {"display_id": 0},
            {"display_id": True},
            {"display_id": 2**32},
            {"video_quality": True},
            {"video_quality": "0.5"},
            {"video_quality": -0.1},
            {"video_quality": 1.1},
            {"output_path": ""},
        ]:
            result = await client.call_tool(
                "start_recording",
                {"output_path": str(tmp_path / "out.mp4"), **override},
            )
            assert result.is_error, override
        for args, message in [({"display_id": 999}, "Display"), ({}, "ScreenCapture")]:
            if not args:
                recorder.computer.permissions["ScreenCapture"] = False
            result = await client.call_tool(
                "start_recording", {"output_path": str(tmp_path / "out.mp4"), **args}
            )
            assert result.is_error
            assert message in result.content[0].text
    assert recorder.computer.recording_calls == []


@pytest.mark.asyncio
async def test_duplicate_start_and_stale_stop_do_not_replace_active_recording(
    recorder, tmp_path
):
    async with Client(recorder.package.create_server(device=recorder.device)) as client:
        starts = await asyncio.gather(
            *[
                client.call_tool(
                    "start_recording", {"output_path": str(tmp_path / f"{i}.mp4")}
                )
                for i in range(2)
            ]
        )
        assert sum(not result.is_error for result in starts) == 1
        first = next(r.structured_content for r in starts if not r.is_error)
        assert len(recorder.computer.recordings) == 1
        wrong = await client.call_tool("stop_recording", {"recording_id": "unknown"})
        assert wrong.is_error
        assert len(recorder.computer.recordings) == 1
        await client.call_tool(
            "stop_recording", {"recording_id": first["recording_id"]}
        )
        second = await client.call_tool(
            "start_recording", {"output_path": str(tmp_path / "second.mp4")}
        )
        assert not second.is_error
        stale = await client.call_tool(
            "stop_recording", {"recording_id": first["recording_id"]}
        )
        assert stale.is_error
        assert len(recorder.computer.recordings) == 1


@pytest.mark.asyncio
async def test_recording_file_error_allows_retry_and_stop_ignores_revoked_permission(
    recorder, tmp_path
):
    path = tmp_path / "existing.mp4"
    path.write_bytes(b"caller data")
    async with Client(recorder.package.create_server(device=recorder.device)) as client:
        error = await client.call_tool("start_recording", {"output_path": str(path)})
        assert error.is_error
        assert path.read_bytes() == b"caller data"
        start = await client.call_tool(
            "start_recording", {"output_path": str(tmp_path / "retry.mp4")}
        )
        assert not start.is_error
        recorder.computer.permissions["ScreenCapture"] = False
        stop = await client.call_tool(
            "stop_recording", {"recording_id": start.structured_content["recording_id"]}
        )
        assert not stop.is_error
    assert not recorder.computer.recordings


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["start", "stop"])
async def test_cancelled_recording_operation_finishes_cleanup_before_unlock(
    recorder, tmp_path, phase
):
    entered, release = asyncio.Event(), asyncio.Event()
    original = getattr(recorder.computer, phase + "_recording")

    async def delayed(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    setattr(recorder.computer, phase + "_recording", delayed)
    path = tmp_path / "cancelled.mp4"
    if phase == "start":
        operation = recorder.device.start_recording(str(path))
    else:
        started = await recorder.device.start_recording(str(path))
        operation = recorder.device.stop_recording(started["recording_id"])
    task = asyncio.create_task(operation)
    try:
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert recorder.device.lock.locked()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert not recorder.device.lock.locked()
    assert not recorder.computer.recordings
    status = await recorder.device.recording_status()
    assert status["state"] == "completed"
    assert status["result"]["path"] == str(path)


@pytest.mark.asyncio
async def test_failed_stop_is_reported_without_retaining_a_dead_recording(
    recorder, tmp_path
):
    original = recorder.computer.stop_recording

    async def fail(handle):
        recorder.computer.recordings.pop(handle)
        raise OSError("encoder failed")

    recorder.computer.stop_recording = fail
    async with Client(recorder.package.create_server(device=recorder.device)) as client:
        start = await client.call_tool(
            "start_recording", {"output_path": str(tmp_path / "failed.mp4")}
        )
        for _ in range(2):
            stop = await client.call_tool(
                "stop_recording",
                {"recording_id": start.structured_content["recording_id"]},
            )
            assert stop.is_error
            assert "encoder failed" in stop.content[0].text
        status = await client.call_tool("recording_status", {})
        assert status.structured_content["state"] == "failed"
        recorder.computer.stop_recording = original
        again = await client.call_tool(
            "start_recording", {"output_path": str(tmp_path / "next.mp4")}
        )
        assert not again.is_error
    assert not recorder.computer.recordings


@pytest.mark.asyncio
async def test_http_recording_survives_requests_and_disconnect_then_closes_on_shutdown(
    recorder, tmp_path
):
    app = recorder.package.create_app(device=recorder.device)
    path = tmp_path / "http.mp4"
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)) as http:
            async with Client(
                streamable_http_client("http://127.0.0.1/mcp", http_client=http)
            ) as client:
                start = await client.call_tool(
                    "start_recording", {"output_path": str(path)}
                )
                assert not start.is_error
            assert recorder.computer.recordings
            async with Client(
                streamable_http_client("http://127.0.0.1/mcp", http_client=http)
            ) as client:
                status = await client.call_tool("recording_status", {})
                assert (
                    status.structured_content["recording_id"]
                    == start.structured_content["recording_id"]
                )
                assert status.structured_content["state"] == "recording"
    assert path.is_file()
    assert not recorder.computer.recordings
