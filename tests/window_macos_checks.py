"""Offline macOS window checks; all desktop paths are in the manual harness."""
import argparse
import importlib
import os
from pathlib import Path
import shlex
import subprocess
import sys
import sysconfig

ROOT = Path(__file__).resolve().parents[1]


def compiler_checks(target, analyze, lifecycle_only=False, production=False):
    includes = sysconfig.get_path("include")
    names = ("window.m", "window_capture.m") if lifecycle_only else ("window.m", "window_capture.m", "accessibility.m")
    for name in names:
        command = [
            "xcrun", "clang", "-fobjc-arc", "-fblocks", "-Wall", "-Wextra", "-Werror",
            "-Werror=unguarded-availability", f"-mmacosx-version-min={target}",
            "-DPY_SSIZE_T_CLEAN", *([] if production else ["-DSCAPKIT_TESTING=1"]),
            "-I" + includes, "-Inative_code/osx/include",
        ]
        if analyze:
            command += ["--analyze", "-Xanalyzer", "-analyzer-output=text"]
        else:
            command += ["-fsyntax-only"]
        command += ["native_code/osx/src/" + name]
        print(shlex.join(command), flush=True)
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        print(result.stdout + result.stderr, end="", flush=True)
        if result.returncode or "warning:" in result.stderr:
            return result.returncode or 1
    print(f"{'static_analysis' if analyze else 'strict_warnings'} macos_target={target}: passed")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target")
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--production", action="store_true")
    parser.add_argument("--free-threaded", action="store_true")
    parser.add_argument("--lifecycle-only", action="store_true")
    parser.add_argument("--review-repro", action="store_true")
    parser.add_argument("--review-only", action="store_true")
    args = parser.parse_args()
    assert not ({"PYTHONHOME", "PYTHONPATH"} & os.environ.keys())
    assert sys.flags.isolated == 1
    assert Path(sys.prefix).resolve().is_relative_to(ROOT)
    threaded = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
    assert threaded == args.free_threaded
    if threaded:
        assert not sys._is_gil_enabled()
    if args.target:
        return compiler_checks(args.target, args.analyze, args.lifecycle_only, args.production)
    package = importlib.import_module("scapkit_computer_use")
    assert Path(package.__file__).resolve() == ROOT / "src/scapkit_computer_use/__init__.py"
    importlib.import_module("scapkit_computer_use.window")
    assert "scapkit_computer_use.screen_capture_kit" not in sys.modules
    native = importlib.import_module("scapkit_computer_use.screen_capture_kit._scapkit")
    assert Path(native.__file__).resolve().parent == ROOT / "src/scapkit_computer_use/screen_capture_kit"
    assert native.WINDOW_BACKEND_ABI == 1
    if threaded:
        assert not sys._is_gil_enabled()
    print(f"python={sys.version.split()[0]} free_threaded={threaded} isolated=1")
    print(f"executable={sys.executable}\nnative={native.__file__}")
    if args.production:
        assert not [name for name in dir(native) if name.startswith("_test_")]
        print("production_test_hooks=absent; WINDOW_BACKEND_ABI=1")
        return 0
    assert hasattr(native, "_test_window")
    import pytest
    files = [
        "test_native_window_macos.py", "test_window_macos_review.py", "test_window_api.py", "test_window_identity.py", "test_window_coordinates.py",
        "test_native_validation.py", "test_native_arguments.py", "test_native_capture.py", "test_capture_lifecycle.py",
        "test_async_safety.py", "test_modifier_lifecycle.py", "test_process.py", "test_recording.py",
        "test_recording_integration.py", "test_native_recording.py", "test_recording_writer.py",
    ]
    selection = []
    if args.review_only:
        files = ["test_native_window_macos.py", "test_window_macos_review.py", "test_window_api.py", "test_window_identity.py", "test_window_coordinates.py"]
    if args.review_repro:
        files = ["test_window_macos_review.py"]
    if args.lifecycle_only:
        files = ["test_native_window_macos.py"]
        selection = ["-k", "session_close_tracks or known_minimized_capture or abi_uint32_pid"]
    result = pytest.main([*(str(ROOT / "tests" / f) for f in files), *selection, "-q", "-o", "log_cli=false"])
    if threaded:
        assert not sys._is_gil_enabled()
        print("gil_enabled_after_tests=False")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
