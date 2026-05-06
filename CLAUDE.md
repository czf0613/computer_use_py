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

Run all tests:

```bash
uv run pytest -v -s
```

Tests are in the `tests/` directory. Async tests use `pytest-asyncio` with strict mode. Some tests (mouse movement, clicking) require macOS Accessibility permissions granted to the terminal or IDE.

## Project Structure

```
native_code/
  osx/          # macOS C extension source
    include/         # Header files
      display.h
      control.h
    src/
      ext.c          # Module entry point
      display.c      # Display-related functions
      control.c      # Mouse control functions
    CMakeLists.txt   # IDE hints only, NOT for building
  win/          # Windows C extension source (not yet implemented)
    CMakeLists.txt   # IDE hints only, NOT for building
src/
  scapkit_computer_use/
    screen_capture_kit/
      __init__.py    # Public API with type annotations
      _scapkit.pyi   # Type stubs for the C extension
      types.py       # TypedDict definitions (DisplayInfo, Point2D)
```

The `CMakeLists.txt` files are **only** for IDE code navigation and hints (e.g. CLion, VS Code IntelliSense). Do **not** use CMake to build this project. Always build via `setup.py` (which is invoked automatically by `uv` / `pip`).

## Documentation

Write docstrings and type annotations in the `.pyi` stub files, not in C code. Every public function exposed by the C extension must have a corresponding annotated entry in the `.pyi` file so that users and IDEs can understand the API.

## C Code Style

- **No docstrings in C method tables.** Pass `NULL` for the `ml_doc` field in `PyMethodDef`. All documentation belongs in the `.pyi` stub files.
- **Declare variables near first use.** Do not group declarations at the top of a function.
- **Simplify allocation checks.** Functions like `PyList_New`, `Py_BuildValue`, and `PyDict_New` have negligible failure probability on modern machines. Do not write elaborate NULL-check-and-cleanup chains for them. Only check return values from system/OS calls that can realistically fail (e.g. `CGGetActiveDisplayList`).
