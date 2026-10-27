"""Isolated macOS offline regression and production-import checks.

All tests use synthetic native hooks, fake devices or disposable child shells.
No enumeration, desktop capture/input, clipboard access or permission requests.
"""

import importlib
import os
import sys
import sysconfig
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    assert sys.platform == "darwin" and sys.flags.isolated == 1
    assert "PYTHONHOME" not in os.environ and "PYTHONPATH" not in os.environ
    free = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
    expected = os.environ.get("PYTHON_VARIANT")
    if expected:
        assert free == expected.endswith("t")
        assert sys.version_info[:2] == tuple(map(int, expected.rstrip("t").split(".")))
    if free:
        assert not sys._is_gil_enabled(), "GIL enabled before imports"
        print("gil_enabled_before_imports=False")
    package = importlib.import_module("scapkit_computer_use")
    window = importlib.import_module("scapkit_computer_use.window")
    assert "scapkit_computer_use.screen_capture_kit" not in sys.modules
    assert (
        Path(package.__file__).resolve()
        == root / "src/scapkit_computer_use/__init__.py"
    )
    assert (
        Path(window.__file__).resolve() == root / "src/scapkit_computer_use/window.py"
    )
    native = importlib.import_module("scapkit_computer_use.screen_capture_kit._scapkit")
    assert (
        Path(native.__file__).resolve().parent
        == root / "src/scapkit_computer_use/screen_capture_kit"
    )
    assert native.WINDOW_BACKEND_ABI == 1
    assert window._load_native() is native
    profiles = importlib.import_module("scapkit_computer_use.window_profiles")
    assert not profiles._APPROVED_EVIDENCE_SHA256
    if free:
        assert not sys._is_gil_enabled(), "native import enabled GIL"
        print("gil_enabled_after_imports=False")
    print(f"python={sys.version.split()[0]} free_threaded={free} isolated=1")
    print(f"native={native.__file__}")
    print("checkout_imports=verified window_abi=1 production_profile_pins=0")
    hooks = [name for name in dir(native) if name.startswith("_test_")]
    if "--production" in sys.argv:
        assert not hooks, hooks
        print("production_test_hooks=absent")
        return 0
    assert hooks and hasattr(native, "_test_window"), "window testing build required"
    import pytest

    names = [
        "test_window_api.py",
        "test_window_identity.py",
        "test_window_coordinates.py",
        "test_native_window_macos.py",
        "test_native_capture.py",
        "test_capture_lifecycle.py",
        "test_native_arguments.py",
        "test_native_validation.py",
        "test_async_safety.py",
        "test_recording.py",
        "test_recording_integration.py",
        "test_native_recording.py",
        "test_recording_writer.py",
        "test_modifier_lifecycle.py",
        "test_release_artifacts.py",
        "test_process.py",
    ]
    result = pytest.main(
        [*(str(root / "tests" / name) for name in names), "-q", "-o", "log_cli=false"]
    )
    if free:
        assert not sys._is_gil_enabled(), "pytest/tests enabled GIL"
        print("gil_enabled_after_tests=False")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
