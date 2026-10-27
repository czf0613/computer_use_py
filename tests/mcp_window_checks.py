"""Offline MCP matrix with checkout, isolation and same-process GIL assertions."""

import importlib
import os
import sys
import sysconfig
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    assert "PYTHONHOME" not in os.environ and "PYTHONPATH" not in os.environ
    assert sys.flags.isolated == 1
    free_threaded = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
    if "--free-threaded" in sys.argv:
        assert free_threaded and sys.version_info[:2] == (3, 14)
        assert not sys._is_gil_enabled(), "GIL enabled before imports"
        print("gil_enabled_before_imports=False")
    else:
        assert sys.version_info[:2] == (3, 10)
    import pytest

    for name, relative in [
        ("scapkit_computer_use", "scapkit_computer_use/__init__.py"),
        ("scapkit_computer_use.window", "scapkit_computer_use/window.py"),
        ("scapkit_computer_use_mcp.windows", "scapkit_computer_use_mcp/windows.py"),
    ]:
        module = importlib.import_module(name)
        assert Path(module.__file__).resolve() == root / "src" / relative
    native = importlib.import_module("scapkit_computer_use.screen_capture_kit._scapkit")
    assert (
        Path(native.__file__).resolve().parent
        == root / "src/scapkit_computer_use/screen_capture_kit"
    )
    assert not hasattr(native, "WINDOW_BACKEND_ABI"), (
        "re-evaluate native scope before using this matrix"
    )
    window = importlib.import_module("scapkit_computer_use.window")
    try:
        window._load_native()
    except importlib.import_module(
        "scapkit_computer_use.window_errors"
    ).WindowError as error:
        assert error.code == "backend_unavailable"
    else:
        raise AssertionError("missing ABI was advertised as available")
    profiles = importlib.import_module("scapkit_computer_use.window_profiles")
    assert not profiles._APPROVED_EVIDENCE_SHA256
    if free_threaded:
        assert not sys._is_gil_enabled(), "GIL enabled by imports"
        print("gil_enabled_before_pytest=False")
    print(
        f"python={sys.version.split()[0]} free_threaded={free_threaded} isolated={sys.flags.isolated}"
    )
    print("checkout_imports=verified native_window_backend=unavailable")
    code = pytest.main(
        [
            str(root / "tests/test_mcp_windows.py"),
            str(root / "tests/test_mcp_server.py"),
            str(root / "tests/test_mcp_recording.py"),
            "-q",
            "-o",
            "log_cli=false",
        ]
    )
    if free_threaded:
        assert not sys._is_gil_enabled(), "GIL enabled by pytest/tests"
        print("gil_enabled_after_pytest=False")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
