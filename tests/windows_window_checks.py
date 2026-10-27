"""CI: one isolated interpreter for import, synthetic tests, and final GIL check."""
import os
import sys
import sysconfig
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    assert sys.platform == "win32"
    assert sys.flags.isolated == 1
    assert not os.environ.get("PYTHONHOME") and not os.environ.get("PYTHONPATH")
    assert Path(sys.prefix).resolve() == root / ".venv"
    free = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
    if free:
        assert not sys._is_gil_enabled()
    import scapkit_computer_use
    from scapkit_computer_use.screen_capture_kit import _scapkit

    assert Path(scapkit_computer_use.__file__).resolve() == root / "src/scapkit_computer_use/__init__.py"
    assert Path(_scapkit.__file__).resolve().parent == root / "src/scapkit_computer_use/screen_capture_kit"
    assert _scapkit.WINDOW_BACKEND_ABI == 1 and hasattr(_scapkit, "_test_window")
    if free:
        assert not sys._is_gil_enabled()
    print(f"python={sys.executable} native={_scapkit.__file__} free_threaded={free}")
    import pytest

    names = ["test_windows_backend.py", "test_async_safety.py", "test_recording.py",
             "test_release_artifacts.py", "test_window_api.py", "test_window_identity.py",
             "test_window_coordinates.py", "test_native_window_windows.py"]
    result = pytest.main([*(str(root / "tests" / name) for name in names), "-q", "-o", "log_cli=false"])
    if free:
        assert not sys._is_gil_enabled(), "tests enabled GIL"
        print("gil_enabled_after_tests=False")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
