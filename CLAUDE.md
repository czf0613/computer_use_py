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

## Project Structure

```
native_code/
  osx/          # macOS C extension source
    include/         # Header files
    src/ext.c
    CMakeLists.txt   # IDE hints only, NOT for building
  win/          # Windows C extension source (not yet implemented)
    CMakeLists.txt   # IDE hints only, NOT for building
src/
  scapkit_computer_use/
    screen_capture_kit/
      _scapkit.pyi   # Type stubs for the C extension
```

The `CMakeLists.txt` files are **only** for IDE code navigation and hints (e.g. CLion, VS Code IntelliSense). Do **not** use CMake to build this project. Always build via `setup.py` (which is invoked automatically by `uv` / `pip`).
