"""Run the offline A-stage checks with interpreter/path/GIL assertions.

Use uv's installed project, not PYTHONPATH. This script never calls native
window enumeration or any desktop operation, even after backends are added.
"""

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
        assert free_threaded, "expected a free-threaded interpreter"
    else:
        assert sys.version_info[:2] == (3, 10), "expected Python 3.10"
    if free_threaded:
        assert not sys._is_gil_enabled(), "GIL enabled before importing the package"
    package = importlib.import_module("scapkit_computer_use")
    window = importlib.import_module("scapkit_computer_use.window")
    assert "scapkit_computer_use.screen_capture_kit" not in sys.modules, (
        "window import loaded desktop code"
    )
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
    if not hasattr(native, "WINDOW_BACKEND_ABI"):
        try:
            window._load_native()
        except package.WindowError as error:
            assert error.code == "backend_unavailable"
        else:
            raise AssertionError("legacy native extension advertised window support")
        print("window_native_backend=unavailable (expected for task A)")
    if free_threaded:
        assert not sys._is_gil_enabled(), "native import enabled the GIL"
    print(
        f"python={sys.version.split()[0]} free_threaded={free_threaded} isolated={sys.flags.isolated}"
    )
    print(f"executable={sys.executable}")
    print(f"native={native.__file__}")
    import pytest

    result = pytest.main(
        [
            str(root / "tests/test_window_api.py"),
            str(root / "tests/test_window_identity.py"),
            str(root / "tests/test_window_coordinates.py"),
            str(root / "tests/test_process.py"),
            "-q",
            "-o",
            "log_cli=false",
        ]
    )
    if free_threaded:
        assert not sys._is_gil_enabled(), "tests enabled the GIL"
        print("gil_enabled_after_tests=False")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
