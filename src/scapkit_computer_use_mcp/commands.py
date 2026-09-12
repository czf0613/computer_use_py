"""Bound command output and lifetime at the MCP boundary."""

import asyncio
import os
import signal

from .device import finish


async def run_command(
    computer, executable, args, cwd, env, encoding, timeout_seconds, max_output_chars
):
    async def collect(reader):
        chunks, length, truncated = [], 0, False
        while True:
            text = await reader.read(65536)
            if not text:
                return "".join(chunks), truncated
            remaining = max_output_chars - length
            if len(text) > remaining:
                truncated = True
            if remaining > 0:
                part = text[:remaining]
                chunks.append(part)
                length += len(part)

    async def cleanup(process):
        try:
            if os.name == "posix":
                # The library starts each command in a separate session. Kill
                # its group even when descendants have redirected their pipes.
                os.killpg(process.pid, signal.SIGKILL)
            elif process.returncode is None:
                process.kill()
        except ProcessLookupError:
            pass
        try:
            # A bounded communicate invokes the library's full pipe/group
            # cleanup if descendants keep the descriptors open after kill.
            await asyncio.wait_for(process.communicate(), 0.5)
        except (asyncio.TimeoutError, OSError, UnicodeError):
            pass

    async def execute():
        process = await computer.run_subprocess(
            executable, args, cwd=cwd, env=env, use_stream=True, encoding=encoding
        )
        tasks = []
        complete = False
        try:
            process.stdin.close()
            tasks = [
                asyncio.create_task(collect(process.stdout)),
                asyncio.create_task(collect(process.stderr)),
                asyncio.create_task(process.wait()),
            ]
            stdout, stderr, returncode = await asyncio.gather(*tasks)
            complete = True
            return {
                "returncode": returncode,
                "stdout": stdout[0],
                "stderr": stderr[0],
                "stdout_truncated": stdout[1],
                "stderr_truncated": stderr[1],
            }
        finally:
            for task in tasks:
                task.cancel()
            await finish(asyncio.create_task(_join(tasks)))
            if not complete:
                await finish(asyncio.create_task(cleanup(process)))

    try:
        return await asyncio.wait_for(execute(), timeout_seconds)
    except asyncio.TimeoutError:
        raise TimeoutError(
            f"Command exceeded {timeout_seconds} seconds and was stopped. Check any partial side effects before retrying."
        ) from None


async def _join(tasks):
    await asyncio.gather(*tasks, return_exceptions=True)
