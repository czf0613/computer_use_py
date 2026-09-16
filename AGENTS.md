# Repository guidance for Codex

## Scope and structure

`scapkit_computer_use` is a Python desktop automation library for CPython **3.10+**,
including free-threaded CPython 3.13/3.14. The library requires macOS
13.0; source builds default to that deployment target and do not restrict CPU
architecture. The release workflow builds wheels for **macOS 15+ arm64** and
**Windows x64 / ARM64**. macOS CI runs on macOS 15 / 26 arm64. Do not confuse
this distribution policy with source
compatibility or claim older macOS / Intel runtime verification. This source branch
also implements a Windows backend targeting recent Windows 10/11 x64 and ARM64.
Desktop runtime acceptance has run on Windows 11 x64 only; do not claim Windows 10
or ARM64 runtime verification before it occurs. See `docs/windows.md` for evidence
and limits. Windows release wheels do not change the macOS wheel deployment target.

- `src/scapkit_computer_use/`: public Python API and async wrappers.
- `src/scapkit_computer_use/process.py`: shell environment bootstrap, process
  lifecycle and incremental text streams; no desktop extension dependency.
- `src/scapkit_computer_use/recording.py`: async recording API and cancellation cleanup.
- `native_code/osx/src/recording.m`: stream ownership, cached frames and recording clock.
- `native_code/osx/src/recording_writer.m`: hardware H.264, AAC and MP4 file lifecycle.
- `src/scapkit_computer_use_mcp/`: optional FastAPI/official MCP SDK v2 server,
  CLI, serialized device operations and packaged `agent_guide.md`.
- `src/scapkit_computer_use/screen_capture_kit/_scapkit.pyi`: native API signatures.
- `native_code/osx/src/`: `ext.c` module init, `control.c` input,
  `display.c` display queries, `capture.m` ScreenCaptureKit and frame encoding.
- `native_code/osx/include/`: native declarations.
- `native_code/windows/`: C++20 Win32 input, WGC/D3D11 capture, WIC JPEG,
  WASAPI loopback and Media Foundation H.264/AAC/MP4. No FFmpeg or vendor SDK.
- `tests/`: automated boundary/synthetic tests and opt-in desktop integration tests.
- `docs/development.md`: build, testing, ownership and compatibility details.
- `docs/releasing.md`: CI and PyPI release instructions.
- `docs/mcp-server.md`: optional installation, HTTP/stdio clients and deployment.

## Work and build

Use `uv` for Python project operations (`uv sync`, `uv run`, `uv build`).
Read `git status` first; preserve unrelated modifications and untracked files.
Build with setuptools, **not CMake**. CMake files are IDE hints only.

```sh
uv sync
uv run setup.py build_ext --inplace --force
```

Force rebuilds when changing Python ABI, native compiler flags or test hooks.
Do not commit generated extensions, build directories or screenshots.
Commit, push, release creation and publication require user authorization;
configuring CI does not itself authorize triggering a release.

## Testing

Default to the affected tests, with synthetic fixtures or mocked native input.
The files `test_capture.py`, `test_mouse.py`, `test_keyboard.py` and
`test_list_displays.py` interact with the real desktop or inspect its state.
Do not run them or the full suite unless the user authorizes desktop testing.
Never grant OS permissions just to make automated tests pass.

```sh
SCAPKIT_TESTING=1 uv run setup.py build_ext --inplace --force
uv run pytest tests/test_native_validation.py tests/test_native_arguments.py tests/test_native_capture.py tests/test_capture_lifecycle.py tests/test_async_safety.py tests/test_process.py
uv run pytest tests/test_recording.py tests/test_recording_integration.py tests/test_native_recording.py tests/test_recording_writer.py
```

Test helpers compile only under `SCAPKIT_TESTING=1`; published wheels must omit
them. No unit/CI test should record the screen, post input or change the clipboard.
Recording tests may encode synthetic pixels and PCM with real codecs; they must
not acquire a real display, system audio, or microphone. Real recording tests
under `tests/manual/` require user preparation and authorization first.
Test native changes on Python 3.10 and a free-threaded build; verify importing
the extension does not enable the GIL. Use compiler warnings and static analysis
in addition to Python tests. A passing synthetic test does not prove real
ScreenCaptureKit behavior or the absence of all leaks.

Subprocess tests use disposable shell profiles and real child processes. Never
copy the current Python process's environment into children; load a clean login
shell environment (Windows: user/system environment block with inheritance off,
then cmd AutoRun) and apply explicit overrides. Drain both outputs during normal
execution; on cancellation close local pipe transports and reap the child without
waiting for descendant-held pipes. Text streams are single-consumer objects tied to
their event loop.

MCP dependencies belong only in the `[mcp]` extra. Base imports and installation
must work without them. Run `uv run --extra mcp pytest tests/test_mcp_server.py tests/test_mcp_recording.py`
with fake devices and disposable shell profiles; do not substitute real desktop
calls. Keep one server worker per device and serialize compound input. Always
close per-call capture handles and command pipes on failure/cancellation. Preserve
Host/Origin validation and bearer protection for non-loopback CLI binding. Agent
instructions, the guide resource and prompt share the packaged `agent_guide.md`.
The MCP dependency chain includes CFFI, which rejects CPython 3.13t. Test the
optional server on ordinary Python 3.10+ and free-threaded 3.14+, while retaining
all 3.13t base-library CI checks and wheels. Do not weaken the GIL assertions.
Windows MCP currently also lacks a compatible pywin32 wheel for 3.14t; test its
extra on ordinary x64 Python, and retain the Windows base-library free-threaded checks.
The MCP cryptography dependency lacks a Windows ARM64 wheel. Retain ARM64 base-library
and wheel checks, but omit its optional MCP extra until upstream wheels are available.
Do not build OpenSSL in CI to work around this optional dependency.

Windows offline tests are `tests/test_windows_backend.py`; they use generated
pixels/PCM and event construction without desktop access. The opt-in acceptance
harness `tests/manual/verify_windows.py --desktop` posts real input and records.
Use `/W4` and `SCAPKIT_ANALYZE=1` for MSVC analysis. Windows coordinates are physical
pixels; macOS coordinates remain points. Prefer hardware MF encoding, allow software
fallback, bound application queues, and preserve timestamps when dropping frames.
Screenshot and video dimensions are image pixels on both platforms. MCP's compatibility
fields `points_per_pixel_x/y` mean input units per image pixel, including on Windows;
never apply `ui_scale_factor` to input coordinates. Keep README, tool descriptions and
the packaged agent guide consistent; use actual screenshot dimensions and display origins.

## Native safety and conventions

- Check every `PyArg_ParseTuple` result and every fallible Python allocation.
  `.pyi` files provide static typing, not runtime argument validation.
- Return `NULL` with an exception set on failure. Release partially acquired
  Python, CoreFoundation, CoreVideo, Objective-C and dispatch resources.
- Document ownership at native boundaries. Balance Create/Copy/Retain with Release.
  With ARC, explicitly set strong fields in malloc/calloc structs to `nil`
  before `free`; C allocation does not provide Objective-C field destruction.
- Capture queue owns `stream`, cached frame, stopped state and delegate back-pointer.
  Retain the frame on that queue before reading it elsewhere. Serialize CPU
  pixel-buffer access with the reader mutex. Release Python thread state before
  blocking on native locks/queues or completion handlers. Never allocate Python
  objects while holding native locks: GC callbacks can reenter the extension.
- Capsule pointers remain immutable and allocated until capsule destruction.
  Stop closes a handle idempotently; it must not free a still-reachable pointer.
  Detach callbacks on their delivery queue before freeing native state.
- Use autorelease pools at Objective-C entry points and callbacks; timeout paths
  must remain safe when callbacks arrive later. Never call Python from a dispatch
  callback without an attached Python thread state.
- Never rely on the GIL for shared native state. Keep `Py_GIL_DISABLED` guards
  around free-threaded-only APIs; preserve Python 3.10-compatible APIs and syntax.
- Native method-table documentation fields stay `NULL`. Put native API annotations
  and docstrings in `.pyi`; Python wrapper docstrings belong alongside wrappers.
- Declare variables near use and use braces for every control-flow body.
- Async key/button down operations must release in `finally` on cancellation.
- High-level shortcuts own private CGEventSource state and balanced modifier/key
  events. Private sources alone do not isolate global flags: post at the session
  tap, merge current HID flags on every event, and acknowledge the private source's
  keyboard event counts before returning. Keep acknowledgment waits off the asyncio
  thread and preserve cancellation through cleanup. Preallocate release events before down, and never reset the
  combined session or hardware state to zero. The raw single-event API leaves
  modifier lifecycle ownership with its caller. `tests/test_modifier_lifecycle.py`
  compiles the real C event builder with an offline post/warp receiver; it does
  not prove WindowServer delivery or window focus. Manual acceptance lives under
  `tests/manual/` and requires the user's prepared test environment and approval.
  Concurrent multi-step desktop actions are not atomic; callers serialize them.

## Network

If GitHub access fails, use the user's local proxy for the affected command only,
when available: `http_proxy=http://localhost:7890 https_proxy=http://localhost:7890`.
Do not store tokens or credentials in source, workflow files, logs or documentation.
