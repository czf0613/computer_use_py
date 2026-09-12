"""Subprocess integration tests with disposable shell profiles and real pipes."""

import asyncio
import base64
import ctypes
import importlib
import json
import os
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import scapkit_computer_use as computer


@pytest.fixture(autouse=True)
def shell_home(tmp_path, monkeypatch):
    if sys.platform == "win32":
        return tmp_path
    import pwd

    home = tmp_path / "shell home"
    home.mkdir()
    (home / ".zprofile").write_text("export SCAPKIT_PROFILE='profile value'\n")
    (home / ".zshrc").write_text("export SCAPKIT_RC='rc value'\necho startup-banner\n")
    monkeypatch.setattr(
        pwd,
        "getpwuid",
        lambda uid: SimpleNamespace(
            pw_dir=str(home), pw_name="scapkit-test", pw_shell="/bin/zsh"
        ),
    )
    return home


async def run_python(code, *args, **kwargs):
    assert hasattr(computer, "run_subprocess"), "missing public subprocess API"
    return await computer.run_subprocess(
        sys.executable, ["-I", "-S", "-c", code, *args], **kwargs
    )


@pytest.mark.asyncio
async def test_returns_exit_code_and_separate_utf8_output():
    result = await run_python(
        "import sys; print('你好 🌍'); print('错误', file=sys.stderr); sys.exit(7)"
    )
    assert result.returncode == 7
    assert result.stdout == "你好 🌍\n"
    assert result.stderr == "错误\n"
    assert tuple(result) == (7, "你好 🌍\n", "错误\n")


@pytest.mark.asyncio
async def test_arguments_are_literal_and_cwd_is_applied(tmp_path):
    args = ["two words", "$(touch injected)", "; touch injected", "", 'a"b', "中文"]
    result = await run_python(
        "import os,json,sys; print(json.dumps([os.getcwd(),sys.argv[1:]]))",
        *args,
        cwd=tmp_path,
    )
    cwd, observed = json.loads(result.stdout)
    assert Path(cwd) == tmp_path
    assert observed == args
    assert not (tmp_path / "injected").exists()


@pytest.mark.asyncio
async def test_pwd_matches_cwd_unless_explicitly_overridden(tmp_path):
    result = await run_python("import os; print(os.environ['PWD'])", cwd=tmp_path)
    assert Path(result.stdout.strip()) == tmp_path
    result = await run_python(
        "import os; print(os.environ['PWD'])",
        cwd=tmp_path,
        env={"PWD": "explicit override"},
    )
    assert result.stdout == "explicit override\n"


@pytest.mark.asyncio
async def test_shell_profiles_and_overrides_exclude_parent_environment(monkeypatch):
    monkeypatch.setenv("SCAPKIT_PARENT_ONLY", "must not propagate")
    monkeypatch.setenv("SCAPKIT_PROFILE", "parent poison")
    result = await run_python(
        "import os,json; print(json.dumps({k:os.getenv(k) for k in "
        "['SCAPKIT_PARENT_ONLY','SCAPKIT_PROFILE','SCAPKIT_RC','SCAPKIT_EXTRA']}))",
        env={"SCAPKIT_RC": "overridden", "SCAPKIT_EXTRA": "one\ntwo=three"},
    )
    assert json.loads(result.stdout) == {
        "SCAPKIT_PARENT_ONLY": None,
        "SCAPKIT_PROFILE": "profile value",
        "SCAPKIT_RC": "overridden",
        "SCAPKIT_EXTRA": "one\ntwo=three",
    }


@pytest.mark.asyncio
async def test_non_stream_stdin_is_eof_and_both_large_pipes_are_drained():
    result = await asyncio.wait_for(
        run_python(
            "import sys; assert sys.stdin.read() == ''; "
            "sys.stdout.write('o'*200000); sys.stderr.write('e'*200000)"
        ),
        10,
    )
    assert result == (0, "o" * 200000, "e" * 200000)


@pytest.mark.asyncio
async def test_stream_returns_before_exit_and_exposes_all_pipes():
    process = await asyncio.wait_for(
        run_python(
            "import sys; print('ready',flush=True); "
            "line=sys.stdin.buffer.readline(); sys.stdout.buffer.write(line); "
            "sys.stderr.write('error channel'); sys.exit(3)",
            use_stream=True,
        ),
        10,
    )
    try:
        assert isinstance(process, computer.SubprocessStream)
        assert process.returncode is None
        assert await asyncio.wait_for(process.stdout.readline(), 3) == "ready\n"
        stdout, stderr = await asyncio.wait_for(process.communicate("你好\n"), 3)
        assert stdout == "你好\n"
        assert stderr == "error channel"
        assert await process.wait() == 3
    finally:
        if process.returncode is None:
            process.kill()
            await process.communicate()


@pytest.mark.asyncio
async def test_explicit_output_encoding():
    result = await run_python(
        "import os; os.write(1,'你好'.encode('gbk'))", encoding="gbk"
    )
    assert result.stdout == "你好"


@pytest.mark.asyncio
async def test_shell_command_is_an_explicit_executable_and_arguments():
    result = await computer.run_subprocess(
        "/bin/sh",
        ["-c", "printf '%s' \"$SCAPKIT_MESSAGE\""],
        env={"SCAPKIT_MESSAGE": "hello ; literal"},
    )
    assert result == (0, "hello ; literal", "")


@pytest.mark.asyncio
async def test_missing_executable_raises_os_error():
    with pytest.raises(FileNotFoundError):
        await computer.run_subprocess("/nonexistent/scapkit-test-executable")


@pytest.mark.asyncio
async def test_profile_path_is_used_instead_of_parent_path(shell_home, monkeypatch):
    binary_dir = shell_home / "bin with spaces"
    binary_dir.mkdir()
    command = binary_dir / "scapkit-fixture-command"
    command.write_text("#!/bin/sh\nprintf 'profile executable'\n")
    command.chmod(0o755)
    with (shell_home / ".zshrc").open("a") as profile:
        profile.write('export PATH="$HOME/bin with spaces:$PATH"\n')
    monkeypatch.setenv("PATH", "/nonexistent-parent-path")
    result = await computer.run_subprocess("scapkit-fixture-command")
    assert result.stdout == "profile executable"


@pytest.mark.asyncio
async def test_environment_is_reloaded_and_overrides_do_not_leak(shell_home):
    code = "import os; print(os.getenv('SCAPKIT_PROFILE')); print(os.getenv('SCAPKIT_TEMP'))"
    first = await run_python(code, env={"SCAPKIT_TEMP": "first only"})
    (shell_home / ".zprofile").write_text("export SCAPKIT_PROFILE='changed value'\n")
    second = await run_python(code)
    assert first.stdout == "profile value\nfirst only\n"
    assert second.stdout == "changed value\nNone\n"


@pytest.mark.asyncio
async def test_stream_incremental_multibyte_and_newline_decoding():
    process = await run_python(
        "import os,sys; data='你😀\\r\\n第二行\\r末尾'.encode('utf-8'); "
        "[(os.write(1, bytes([b])), sys.stdin.buffer.read(1)) for b in data]",
        use_stream=True,
    )

    async def feed():
        for _ in "你😀\r\n第二行\r末尾".encode():
            process.stdin.write("x")
            await process.stdin.drain()
            await asyncio.sleep(0.001)
        process.stdin.close()

    feeder = asyncio.create_task(feed())
    try:
        assert await asyncio.wait_for(process.stdout.read(1), 3) == "你"
        lines = [line async for line in process.stdout]
        assert lines == ["😀\n", "第二行\n", "末尾"]
        await feeder
        assert await process.stderr.read() == ""
        assert await process.wait() == 0
        assert process.stdout.at_eof()
    finally:
        feeder.cancel()
        await asyncio.gather(feeder, return_exceptions=True)
        if process.returncode is None:
            process.kill()
            await process.communicate()


@pytest.mark.asyncio
async def test_communicate_preserves_text_read_ahead():
    process = await run_python(
        "import sys; sys.stdout.write('head\\n余下内容'); sys.stdout.flush()",
        use_stream=True,
    )
    assert await process.stdout.readline() == "head\n"
    assert await process.communicate() == ("余下内容", "")


@pytest.mark.asyncio
async def test_sized_read_returns_available_prompt_before_child_waits_for_input():
    process = await run_python(
        "import sys; print('ready',end='',flush=True); sys.stdin.read()",
        use_stream=True,
    )
    try:
        assert await asyncio.wait_for(process.stdout.read(4096), 1) == "ready"
    finally:
        await process.communicate()


@pytest.mark.asyncio
async def test_gbk_text_stdin_and_stdout():
    process = await run_python(
        "import sys; data=sys.stdin.buffer.read().decode('gbk'); "
        "sys.stdout.buffer.write(data.upper().encode('gbk'))",
        use_stream=True,
        encoding="gbk",
    )
    assert await process.communicate("你好 abc") == ("你好 ABC", "")


@pytest.mark.asyncio
async def test_closing_unused_utf16_stdin_does_not_send_a_bom():
    process = await run_python(
        "import sys; assert sys.stdin.buffer.read() == b''",
        use_stream=True,
        encoding="utf-16",
    )
    assert await process.communicate() == ("", "")
    assert process.returncode == 0


@pytest.mark.asyncio
async def test_invalid_output_encoding_is_explicitly_configurable():
    with pytest.raises(UnicodeDecodeError):
        await run_python("import os; os.write(1,b'\\xff')")
    result = await run_python("import os; os.write(1,b'\\xff')", errors="replace")
    assert result.stdout == "\ufffd"


async def wait_for_file(path):
    deadline = time.monotonic() + 5
    while not path.exists():
        assert time.monotonic() < deadline, "child did not start"
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_non_stream_cancellation_reaps_the_child(tmp_path):
    pidfile = tmp_path / "child.pid"
    task = asyncio.create_task(
        run_python(
            "import os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)",
            str(pidfile),
        )
    )
    await wait_for_file(pidfile)
    pid = int(pidfile.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True, "closed-stdin"])
async def test_cancellation_finishes_when_descendant_keeps_pipes_open(
    monkeypatch, tmp_path, streaming
):
    module = importlib.import_module("scapkit_computer_use.process")

    # Emulate Windows' direct-child-only termination with real POSIX pipes.
    def kill_direct_child(process):
        if process.returncode is None:
            process.kill()

    monkeypatch.setattr(module, "_kill", kill_direct_child)
    pidfile = tmp_path / "descendant.pid"
    code = (
        "import subprocess,sys,time,pathlib; "
        "p=subprocess.Popen([sys.executable,'-I','-S','-c','import time; time.sleep(60)']); "
        "pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)"
    )
    if streaming:
        process = await run_python(code, str(pidfile), use_stream=True)
        if streaming == "closed-stdin":
            process.stdin.write("x" * 2000000)
            process.stdin.close()
            task = asyncio.create_task(process.communicate())
        else:
            task = asyncio.create_task(process.communicate("x" * 2000000))
    else:
        task = asyncio.create_task(run_python(code, str(pidfile)))
    await wait_for_file(pidfile)
    descendant = int(pidfile.read_text())
    task.cancel()
    try:
        done, _ = await asyncio.wait({task}, timeout=1)
        assert task in done, "cancellation waited for a descendant's inherited pipes"
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        os.kill(descendant, 9)
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelling_shell_initialization_reaps_shell(shell_home):
    pidfile = shell_home / "shell.pid"
    (shell_home / ".zshrc").write_text('echo $$ > "$HOME/shell.pid"\n/bin/sleep 60\n')
    task = asyncio.create_task(run_python("print('must not start')"))
    await wait_for_file(pidfile)
    pid = int(pidfile.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
async def test_stream_decode_error_does_not_leave_child_or_full_stderr_pipe():
    process = await run_python(
        "import os,time; os.write(1,b'\\xff'); os.close(1); "
        "os.write(2,b'e'*2000000); time.sleep(60)",
        use_stream=True,
    )
    with pytest.raises(UnicodeDecodeError):
        await asyncio.wait_for(process.communicate(), 3)
    assert process.returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(process.pid, 0)


@pytest.mark.asyncio
async def test_concurrent_calls_keep_environments_separate():
    results = await asyncio.gather(
        *[
            run_python(
                "import os; print(os.getenv('SCAPKIT_JOB'))",
                env={"SCAPKIT_JOB": str(i)},
            )
            for i in range(6)
        ]
    )
    assert [result.stdout for result in results] == [f"{i}\n" for i in range(6)]


@pytest.mark.asyncio
async def test_shell_initialization_failure_is_not_silently_ignored(shell_home):
    (shell_home / ".zshrc").write_text("exit 23\n")
    with pytest.raises(RuntimeError, match="shell environment initialization failed"):
        await run_python("print('must not run')")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs,exception",
    [
        ({"args": "-c echo accidental shell"}, TypeError),
        ({"args": ["a\0b"]}, ValueError),
        ({"env": {"BAD=NAME": "value"}}, ValueError),
        ({"env": {"KEY": "value\0"}}, ValueError),
        ({"env": {"KEY": 123}}, ValueError),
        ({"encoding": "not-a-real-codec"}, LookupError),
        ({"encoding": "base64_codec", "use_stream": True}, LookupError),
    ],
)
async def test_invalid_inputs_fail_before_shell_startup(shell_home, kwargs, exception):
    marker = shell_home / "was-started"
    (shell_home / ".zshrc").write_text('touch "$HOME/was-started"\n')
    with pytest.raises(exception):
        await computer.run_subprocess(sys.executable, **kwargs)
    assert not marker.exists()


def test_windows_environment_block_preserves_unicode_and_hidden_drive_entries():
    module = importlib.import_module("scapkit_computer_use.process")
    data = "Path=C:\\工具😀\0USERNAME=用户\0=C:=C:\\工作\0AFTER=present\0\0".encode(
        "utf-16-le"
    )
    block = ctypes.create_string_buffer(data)
    assert module._decode_windows_environment(ctypes.addressof(block)) == {
        "PATH": "C:\\工具😀",
        "USERNAME": "用户",
        "=C:": "C:\\工作",
        "AFTER": "present",
    }


@pytest.mark.asyncio
async def test_windows_shell_probe_does_not_export_its_private_environment_key(
    monkeypatch, tmp_path
):
    module = importlib.import_module("scapkit_computer_use.process")
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        module,
        "_windows_system_environment",
        lambda: {
            "COMSPEC": "fixture-cmd.exe",
            "USERPROFILE": str(tmp_path),
            "KEEP": "profile value",
        },
    )

    async def emulate_cmd(executable, *args, env, **kwargs):
        assert executable == "fixture-cmd.exe"
        assert "/v:off" in args and "/d" not in args

        class Cmd:
            returncode = None

            async def communicate(self, input):
                command = input.decode("ascii")
                assert command.endswith("\r\nexit %errorlevel%\r\n")
                encoded = re.search(r"b64decode\('([A-Za-z0-9+/=]+)'\)", command).group(
                    1
                )
                # Windows normalizes environment keys to uppercase in Python.
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-I",
                    "-S",
                    "-c",
                    base64.b64decode(encoded).decode(),
                    env={k.upper(): v for k, v in env.items()},
                    **kwargs,
                )
                result = await process.communicate()
                self.returncode = process.returncode
                return result

        return Cmd()

    monkeypatch.setattr(module, "_spawn", emulate_cmd)
    result = await module._shell_environment()
    assert result["KEEP"] == "profile value"
    assert not any(key.startswith("SCAPKIT_ENV_") for key in result)


def test_windows_program_lookup_uses_supplied_path_and_cwd(tmp_path, monkeypatch):
    module = importlib.import_module("scapkit_computer_use.process")
    executable = tmp_path / "bin" / "program.exe"
    executable.parent.mkdir()
    executable.touch()
    monkeypatch.setenv("PATH", "/wrong/parent/path")
    assert module._windows_executable("program", {"PATH": "bin"}, str(tmp_path)) == str(
        executable
    )
    assert module._windows_executable(
        "./bin/program.exe", {"PATH": ""}, str(tmp_path)
    ) == str(executable)


@pytest.mark.parametrize("fail_create", [False, True])
def test_windows_environment_api_excludes_inheritance_and_releases_resources(
    monkeypatch, fail_create
):
    from ctypes import wintypes

    module = importlib.import_module("scapkit_computer_use.process")
    clean = ctypes.create_string_buffer(
        "Path=C:\\系统\0USERPROFILE=C:\\用户😀\0\0".encode("utf-16-le")
    )
    inherited = ctypes.create_string_buffer(
        "PARENT_SECRET=must-not-inherit\0\0".encode("utf-16-le")
    )
    released = []

    class Function:
        def __init__(self, call):
            self.call = call

        def __call__(self, *args):
            return self.call(*args)

    def open_token(process, access, result):
        ctypes.cast(result, ctypes.POINTER(wintypes.HANDLE))[0] = 1234
        return True

    def create_block(result, token, inherit):
        if fail_create:
            return False
        ctypes.cast(result, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(
            inherited if inherit else clean
        )
        return True

    libraries = {
        "kernel32": SimpleNamespace(
            GetCurrentProcess=Function(lambda: 10),
            CloseHandle=Function(lambda handle: released.append("token") or True),
        ),
        "advapi32": SimpleNamespace(OpenProcessToken=Function(open_token)),
        "userenv": SimpleNamespace(
            CreateEnvironmentBlock=Function(create_block),
            DestroyEnvironmentBlock=Function(
                lambda block: released.append("environment") or True
            ),
        ),
    }
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda name, **kwargs: libraries[name], raising=False
    )
    monkeypatch.setattr(
        ctypes,
        "WinError",
        lambda code: OSError(code, "Windows fixture error"),
        raising=False,
    )
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    if fail_create:
        with pytest.raises(OSError):
            module._windows_system_environment()
        assert released == ["token"]
    else:
        assert module._windows_system_environment() == {
            "PATH": "C:\\系统",
            "USERPROFILE": "C:\\用户😀",
        }
        assert released == ["environment", "token"]


def test_package_import_does_not_require_the_macos_extension(monkeypatch):
    # Run in an isolated interpreter; block native imports rather than changing
    # this test runner's platform or its already-loaded extension.
    import subprocess

    code = """
import importlib.abc, sys
class NoDesktop(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if 'screen_capture_kit' in fullname:
            raise AssertionError('process API must not import desktop bindings')
sys.meta_path.insert(0, NoDesktop())
from scapkit_computer_use import run_subprocess
assert callable(run_subprocess)
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
