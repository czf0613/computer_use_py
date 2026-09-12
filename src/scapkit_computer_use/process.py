"""Async processes with a fresh shell environment and incremental text pipes."""

import asyncio
import base64
import codecs
import ctypes
import io
import json
import os
import shlex
import signal
import sys
import uuid
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Literal, NamedTuple, overload


class SubprocessResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


class _TextReader:
    def __init__(self, stream: asyncio.StreamReader, encoding: str, errors: str):
        self._stream = stream
        self._decoder = io.IncrementalNewlineDecoder(
            codecs.getincrementaldecoder(encoding)(errors=errors), translate=True
        )
        self._buffer = ""
        self._eof = False

    async def _fill(self) -> None:
        chunk = await self._stream.read(65536)
        self._eof = not chunk
        self._buffer += self._decoder.decode(chunk, final=self._eof)

    async def read(self, size: int = -1) -> str:
        """Read up to size Unicode characters; -1 reads through EOF."""
        if size < -1:
            raise ValueError("size must be -1 or nonnegative")
        if size == -1:
            chunks = [self._buffer]
            self._buffer = ""
            while not self._eof:
                await self._fill()
                chunks.append(self._buffer)
                self._buffer = ""
            return "".join(chunks)
        while size and not self._buffer and not self._eof:
            await self._fill()
        result, self._buffer = self._buffer[:size], self._buffer[size:]
        return result

    async def readline(self) -> str:
        """Read a line, including its normalized newline, or '' at EOF."""
        while "\n" not in self._buffer and not self._eof:
            await self._fill()
        end = self._buffer.find("\n")
        end = len(self._buffer) if end < 0 else end + 1
        result, self._buffer = self._buffer[:end], self._buffer[end:]
        return result

    def at_eof(self) -> bool:
        return self._eof and not self._buffer

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        line = await self.readline()
        if not line:
            raise StopAsyncIteration
        return line


class _TextWriter:
    def __init__(self, stream: asyncio.StreamWriter, encoding: str, errors: str):
        self._stream = stream
        self._encoder = codecs.getincrementalencoder(encoding)(errors=errors)
        self._closed = False
        self._written = False

    def write(self, text: str) -> None:
        if self._closed:
            raise ValueError("write to closed stdin")
        data = self._encoder.encode(text)
        self._written = True
        self._stream.write(data)

    def writelines(self, lines: Iterable[str]) -> None:
        for line in lines:
            self.write(line)

    async def drain(self) -> None:
        await self._stream.drain()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            tail = self._encoder.encode("", final=True) if self._written else b""
            if tail:
                self._stream.write(tail)
            self._stream.close()

    async def wait_closed(self) -> None:
        await self._stream.wait_closed()


def _kill(process: asyncio.subprocess.Process) -> None:
    try:
        if os.name == "posix":
            # Each child owns a session: inherited pipe handles must close too.
            os.killpg(process.pid, signal.SIGKILL)
        elif process.returncode is None:
            process.kill()
    except ProcessLookupError:
        pass


async def _settle(task: asyncio.Task):
    """Finish cleanup even if the caller receives another cancellation."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def _abort(
    process: asyncio.subprocess.Process, task: asyncio.Task | None = None
) -> None:
    """Close our pipe handles as well as reaping the direct child."""
    _kill(process)
    if process.stdin is not None:
        writer = process.stdin.transport
        # Python 3.10's pipe abort is not safe after connection_lost. Still abort
        # a closing pipe that has buffered input, rather than waiting to flush.
        if not writer.is_closing() or writer.get_write_buffer_size():
            writer.abort()
    # High-level Process has no close API. CPython's backing SubprocessTransport
    # owns these pipes; close it so inherited descendant handles cannot hold us
    # in cleanup forever. Covered on every supported CPython ABI in CI.
    process._transport.close()
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await process.wait()


async def _spawn(*args, **kwargs) -> asyncio.subprocess.Process:
    if os.name == "posix":
        kwargs["start_new_session"] = True
    task = asyncio.create_task(asyncio.create_subprocess_exec(*args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            process = await _settle(task)
        except Exception:  # noqa: BLE001, S110
            # Preserve caller cancellation if startup itself failed; no child
            # was returned, so there are no process resources to reclaim here.
            pass
        else:
            await _settle(asyncio.create_task(_abort(process)))
        raise


async def _communicate(
    process: asyncio.subprocess.Process, input: bytes | None = None
) -> tuple[bytes, bytes]:
    task = asyncio.create_task(process.communicate(input))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await _settle(asyncio.create_task(_abort(process, task)))
        raise


class SubprocessStream:
    """A running process with text stdin/stdout/stderr, tied to its event loop.

    Drain stdout and stderr concurrently. wait() alone cannot drain full pipes.
    The caller owns the process after run_subprocess(use_stream=True) returns.
    """

    def __init__(self, process: asyncio.subprocess.Process, encoding: str, errors: str):
        self._process = process
        self.encoding = encoding
        self.stdin = _TextWriter(process.stdin, encoding, errors)
        self.stdout = _TextReader(process.stdout, encoding, errors)
        self.stderr = _TextReader(process.stderr, encoding, errors)

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    async def wait(self) -> int:
        return await self._process.wait()

    def terminate(self) -> None:
        self._process.terminate()

    def kill(self) -> None:
        self._process.kill()

    async def communicate(self, input: str | None = None) -> tuple[str, str]:
        """Write optional text, close stdin, and concurrently drain both outputs."""

        async def send():
            try:
                if input is not None:
                    self.stdin.write(input)
                    await self.stdin.drain()
                self.stdin.close()
                await self.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass

        async def collect():
            tasks = [
                asyncio.create_task(send()),
                asyncio.create_task(self.stdout.read()),
                asyncio.create_task(self.stderr.read()),
            ]
            try:
                _, stdout, stderr = await asyncio.gather(*tasks)
                await self.wait()
                return stdout, stderr
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        task = asyncio.create_task(collect())
        try:
            return await asyncio.shield(task)
        except BaseException:
            await _settle(asyncio.create_task(_abort(self._process, task)))
            raise


def _windows_system_environment() -> dict[str, str]:
    """Read current-user/system settings, explicitly excluding process inheritance."""
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    security.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    security.OpenProcessToken.restype = wintypes.BOOL
    userenv.CreateEnvironmentBlock.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.BOOL,
    ]
    userenv.CreateEnvironmentBlock.restype = wintypes.BOOL
    userenv.DestroyEnvironmentBlock.argtypes = [ctypes.c_void_p]
    userenv.DestroyEnvironmentBlock.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not security.OpenProcessToken(
        kernel.GetCurrentProcess(), 0x0008 | 0x0002, ctypes.byref(token)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    block = ctypes.c_void_p()
    try:
        if not userenv.CreateEnvironmentBlock(ctypes.byref(block), token, False):
            raise ctypes.WinError(ctypes.get_last_error())
        return _decode_windows_environment(block.value)
    finally:
        if block.value:
            userenv.DestroyEnvironmentBlock(block)
        kernel.CloseHandle(token)


def _decode_windows_environment(address: int) -> dict[str, str]:
    result = {}
    while True:
        units = 0
        while ctypes.c_uint16.from_address(address + units * 2).value:
            units += 1
        if not units:
            return result
        item = ctypes.string_at(address, units * 2).decode("utf-16-le", "surrogatepass")
        split = item.find("=", 1)  # Preserve Windows' hidden '=C:' drive entries.
        if split >= 0:
            result[item[:split].upper()] = item[split + 1 :]
        address += (units + 1) * 2


def _default_encoding() -> str:
    if os.name != "nt":
        return "utf-8"
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    for name in ("GetConsoleOutputCP", "GetOEMCP"):
        function = getattr(kernel, name)
        function.argtypes = []
        function.restype = ctypes.c_uint
    return f"cp{kernel.GetConsoleOutputCP() or kernel.GetOEMCP()}"


async def _shell_environment() -> dict[str, str]:
    marker = ("SCAPKIT_ENV_" + uuid.uuid4().hex).encode("ascii")
    probe_key = marker.decode("ascii").upper()
    # This is a new, isolated Python CHILD, after the shell has loaded its files.
    # It serializes that shell's environment, never this library process's env.
    code = (
        "import os,json; e=dict(os.environ); e.pop(" + repr(probe_key) + ",None); "
        "os.write(1,"
        + repr(b"\0" + marker + b"\0")
        + "+json.dumps(e,ensure_ascii=True).encode('ascii')+b'\\0')"
    )
    common = {
        "stdin": asyncio.subprocess.DEVNULL,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    probe_input = None
    if os.name == "nt":
        base = await asyncio.to_thread(_windows_system_environment)
        shell = base.get("COMSPEC") or str(
            Path(base["SYSTEMROOT"]) / "System32" / "cmd.exe"
        )
        base[probe_key] = sys.executable
        encoded = base64.b64encode(code.encode()).decode("ascii")
        # Send the ASCII command on stdin: argv quoting follows CRT rules, not
        # cmd's command-language rules. /v:off protects paths containing '!'.
        # %variable% expands the quoted path once without re-expanding '%'.
        command = (
            '"%'
            + probe_key
            + '%" -I -S -c "import base64;exec(base64.b64decode(\''
            + encoded
            + "'))\""
        )
        probe_input = (command + "\r\nexit %errorlevel%\r\n").encode("ascii")
        common["stdin"] = asyncio.subprocess.PIPE
        process = await _spawn(
            shell,
            "/q",
            "/e:on",
            "/v:off",
            env=base,
            cwd=base.get("USERPROFILE"),
            **common,
        )
    else:
        import pwd

        user = pwd.getpwuid(os.getuid())
        shell = user.pw_shell or "/bin/sh"
        base = {
            "HOME": user.pw_dir,
            "USER": user.pw_name,
            "LOGNAME": user.pw_name,
            "SHELL": shell,
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": "en_US.UTF-8",
        }
        command = "exec " + shlex.join([sys.executable, "-I", "-S", "-c", code])
        process = await _spawn(
            shell, "-i", "-l", "-c", command, env=base, cwd=user.pw_dir, **common
        )
    try:
        stdout, _ = await asyncio.wait_for(_communicate(process, probe_input), 10)
    except asyncio.TimeoutError:
        raise TimeoutError("shell environment initialization timed out") from None
    prefix = b"\0" + marker + b"\0"
    start = stdout.find(prefix)
    if process.returncode != 0 or start < 0:
        raise RuntimeError(
            f"shell environment initialization failed (exit {process.returncode})"
        )
    payload = stdout[start + len(prefix) :].split(b"\0", 1)[0]
    result = json.loads(payload)
    if not isinstance(result, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in result.items()
    ):
        raise RuntimeError("shell returned an invalid environment")
    return (
        {key.upper(): value for key, value in result.items()}
        if os.name == "nt"
        else result
    )


def _windows_executable(
    executable: str, env: Mapping[str, str], cwd: str | None
) -> str:
    """CreateProcess does not search the PATH passed in its child environment."""
    directory = Path(cwd or os.getcwd())
    path = Path(executable)
    if path.is_absolute() or os.path.dirname(executable):
        candidates = [path if path.is_absolute() else directory / path]
    else:
        candidates = [
            Path(folder) / path for folder in env.get("PATH", "").split(";") if folder
        ]
    for candidate in candidates:
        if not candidate.is_absolute():
            candidate = directory / candidate
        # This API executes programs directly; batch files require explicit cmd.
        for option in (
            (candidate, Path(str(candidate) + ".exe"))
            if not candidate.suffix
            else (candidate,)
        ):
            if option.is_file():
                if option.suffix.lower() in (".bat", ".cmd"):
                    raise ValueError(
                        "run batch files through an explicit cmd.exe command"
                    )
                return str(option.resolve())
    raise FileNotFoundError(f"executable not found in shell PATH: {executable}")


@overload
async def run_subprocess(
    executable: str | os.PathLike[str],
    args: Sequence[str | os.PathLike[str]] = (),
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    use_stream: Literal[False] = False,
    *,
    encoding: str | None = None,
    errors: str = "strict",
) -> SubprocessResult: ...


@overload
async def run_subprocess(
    executable: str | os.PathLike[str],
    args: Sequence[str | os.PathLike[str]] = (),
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    use_stream: Literal[True] = True,
    *,
    encoding: str | None = None,
    errors: str = "strict",
) -> SubprocessStream: ...


@overload
async def run_subprocess(
    executable: str | os.PathLike[str],
    args: Sequence[str | os.PathLike[str]] = (),
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    use_stream: bool = False,
    *,
    encoding: str | None = None,
    errors: str = "strict",
) -> SubprocessResult | SubprocessStream: ...


async def run_subprocess(
    executable,
    args=(),
    cwd=None,
    env=None,
    use_stream=False,
    *,
    encoding=None,
    errors="strict",
):
    """Run an argv vector using a fresh system/user shell environment.

    env adds/overrides variables after shell initialization. It never inherits
    os.environ from this Python process. Non-streaming stdin is EOF; nonzero
    exits are returned normally. Streaming returns text pipes and process control.
    macOS defaults to UTF-8; Windows uses the console/OEM code page. Override
    encoding for programs emitting UTF-8, UTF-16 or an ANSI code page on Windows.
    """
    executable = os.fspath(executable)
    if not isinstance(executable, str) or not executable or "\0" in executable:
        raise ValueError("executable must be a nonempty text path without NUL")
    if isinstance(args, (str, bytes)):
        raise TypeError("args must be a sequence of arguments, not a command string")
    arguments = [os.fspath(arg) for arg in args]
    if any(not isinstance(arg, str) or "\0" in arg for arg in arguments):
        raise ValueError("arguments must be text without NUL")
    additions = dict(env) if env is not None else {}
    for key, value in additions.items():
        if not isinstance(key, str) or not key or "=" in key or "\0" in key:
            raise ValueError(
                "environment names must be nonempty text without '=' or NUL"
            )
        if not isinstance(value, str) or "\0" in value:
            raise ValueError("environment values must be text without NUL")
    encoding = _default_encoding() if encoding is None else encoding
    codecs.lookup(encoding)
    codecs.lookup_error(errors)
    # Reject binary transforms (e.g. base64_codec) before launching a child.
    "".encode(encoding, errors)
    b"".decode(encoding, errors)
    directory = os.path.abspath(os.fspath(cwd)) if cwd is not None else os.getcwd()
    environment = await _shell_environment()
    if os.name == "posix":
        environment["PWD"] = directory
    environment.update(
        {k.upper(): v for k, v in additions.items()} if os.name == "nt" else additions
    )
    if os.name == "nt":
        executable = _windows_executable(executable, environment, directory)
    process = await _spawn(
        executable,
        *arguments,
        cwd=directory,
        env=environment,
        stdin=asyncio.subprocess.PIPE if use_stream else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    if use_stream:
        try:
            return SubprocessStream(process, encoding, errors)
        except BaseException:
            await _settle(asyncio.create_task(_abort(process)))
            raise
    stdout, stderr = await _communicate(process)

    def decode(data):
        return data.decode(encoding, errors).replace("\r\n", "\n").replace("\r", "\n")

    return SubprocessResult(process.returncode, decode(stdout), decode(stderr))
