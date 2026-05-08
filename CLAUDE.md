# CLAUDE.md

## Network Access

This repository is hosted on GitHub, which is not accessible in some countries. If there's a network issue, set the HTTP proxy and HTTPS proxy to `localhost:7890`:

```bash
export http_proxy=http://localhost:7890
export https_proxy=http://localhost:7890
```

## Package Manager

We use `uv` to manage this project. Always use `uv` commands for project operations:

- `uv run` to execute scripts and commands
- `uv add` to add dependencies
- `uv sync` to sync the environment

## Building

Build the C extension in-place before running or testing:

```bash
uv run setup.py build_ext --inplace
```

## Testing

Tests are in the `tests/` directory. Async tests use `pytest-asyncio` with strict mode. Some tests (mouse movement, clicking) require macOS Accessibility permissions granted to the terminal or IDE.

Only run the specific test file(s) relevant to your changes, not the full suite. For example:

```bash
uv run pytest tests/test_capture.py -v -s
```

Only run the full suite (`uv run pytest -v -s`) when explicitly asked or before a commit/push.

## Project Structure

```
native_code/
  osx/          # macOS C/ObjC extension source
    include/
      display.h
      control.h
      capture.h
    src/
      ext.c          # Module entry point
      display.c      # Display-related functions
      control.c      # Mouse/keyboard control functions
      capture.m      # Screen capture (ScreenCaptureKit, ObjC)
    CMakeLists.txt   # IDE hints only, NOT for building
  win/          # Windows C extension source (not yet implemented)
    CMakeLists.txt   # IDE hints only, NOT for building
src/
  scapkit_computer_use/
    __init__.py      # Top-level public API re-exports
    screen_capture_kit/
      __init__.py    # Public API with type annotations
      _scapkit.pyi   # Type stubs for the C extension
      types.py       # TypedDict definitions (DisplayInfo, Point2D, CaptureHandle, BGRAPack)
      keys.py        # Named key map (macOS key codes, modifier flags)
```

The `CMakeLists.txt` files are **only** for IDE code navigation and hints (e.g. CLion, VS Code IntelliSense). Do **not** use CMake to build this project. Always build via `setup.py` (which is invoked automatically by `uv` / `pip`).

## Documentation

Write docstrings and type annotations in the `.pyi` stub files, not in C code. Every public function exposed by the C extension must have a corresponding annotated entry in the `.pyi` file so that users and IDEs can understand the API.

## C Code Style

- **No docstrings in C method tables.** Pass `NULL` for the `ml_doc` field in `PyMethodDef`. All documentation belongs in the `.pyi` stub files.
- **Declare variables near first use.** Do not group declarations at the top of a function.
- **Simplify allocation checks.** Functions like `PyList_New`, `Py_BuildValue`, and `PyDict_New` have negligible failure probability on modern machines. Do not write elaborate NULL-check-and-cleanup chains for them. Only check return values from system/OS calls that can realistically fail (e.g. `CGGetActiveDisplayList`).
- **Do not check `PyArg_ParseTuple` return values.** The `.pyi` stub enforces correct types at the Python level, so callers will not pass wrong arguments. Just call it and use the parsed variables directly.
- **Always use braces for control flow.** Every `if`, `else`, `for`, and `while` body must be wrapped in `{}`, even if it is a single statement.
