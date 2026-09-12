# Standalone macOS modifier regression harness

This is an **opt-in manual desktop test**, independent of AIOS and MCP. Nothing
here is part of automatic pytest collection. `verify_modifiers.py` compiles and
launches only its sibling `modifier_fixture.swift`, then calls the chosen
repository's public Python APIs. Both executables require `--allow-desktop`.

## Authorization and preparation

Run only after the user explicitly authorizes real desktop testing. The flag
authorizes GUI launch and keyboard/mouse input; paste/full modes also **overwrite
the real clipboard** with synthetic Chinese text. Clipboard contents are never
read for backup or restored. Combo mode does not access the clipboard.

Before starting, save work, arrange an unobstructed main display with at least
660 × 460 visible points, choose the ABC input source, and turn Caps Lock off.
Accessibility permission must already be granted to the chosen Python/runtime;
the harness neither grants permissions nor opens Settings. No screen recording
or screenshot API is used. Keep unrelated windows/overlays away from the fixture.

The fixture first creates **two separate windows A and B on the main display**.
Each has its own text field, plain-click counter, scroll document, and drag strip.
Check both are visible and nonoverlapping, release modifiers, and click
**A + B prepared: start** in A. This prepares A's editor with a caret at the end
of synthetic seed text. Then leave the keyboard and mouse alone. Preparation
times out after three minutes. Do not move B to another screen before this
baseline; full mode performs the cross-screen movements itself.

## Run after approval

Use an existing Python 3.10+ interpreter matching the in-place extension's ABI.
The selected checkout must already have its native extension built in
`src/scapkit_computer_use/screen_capture_kit/`. This harness does not install,
sync, rebuild that extension, or change a project venv. The Swift compiler must
be available through `xcrun`.

From this repository, substitute your interpreter's absolute path:

```sh
uv run --no-project --no-python-downloads --python /absolute/path/to/python tests/manual/verify_modifiers.py --allow-desktop --mode combo
uv run --no-project --no-python-downloads --python /absolute/path/to/python tests/manual/verify_modifiers.py --allow-desktop --mode paste
uv run --no-project --no-python-downloads --python /absolute/path/to/python tests/manual/verify_modifiers.py --allow-desktop --mode full
uv run --no-project --no-python-downloads --python /absolute/path/to/python tests/manual/verify_modifiers.py --allow-desktop --mode lifecycle
```

`combo` / `combo-only` performs just Cmd+A and checks actual field selection.
`paste` / `paste-only` sets synthetic clipboard text and calls `clipboard_paste`
once, checking exact Chinese insertion at the current selection. It issues **no
preparatory combo**. Run reproductions separately: the first leftover modifier
ends a run, so a broken combo cannot contaminate a later paste experiment.
`combo-paste` checks the reported continuous Cmd+A → Chinese paste → other-window
title-click sequence, without inserting a plain key between the operations.

After a successful combo/paste state check, reproduction modes click the other
window's title bar and check actual focus. If residual modifiers are detected,
the run stops before this click and reports focus acceptance as incomplete.

Full mode first clicks the other window's title bar, before a plain key could
mask the original state contamination, then checks plain `x`, click counters,
exact input checks, alternating scroll motion, and measured drag endpoints.
Synthetic menu actions check Command, Command+Shift, Command+Option,
Command+Control, and all four modifiers, using `keyboard_click` and `key_combo`.
It also verifies a plain `keyboard_click_action("x", "down"/"up")` pair. Raw
flags-bearing chords are intentionally outside the high-level lifecycle contract.

`lifecycle` performs 18 shortcut/paste → title-click trials with **no observation
delay between the shortcut return and mouse event creation**. It then checks real
task cancellation, an injected await failure, and high-level shortcuts while each
of the four modifiers is explicitly held through the raw HID API. Those holds
have matching owner-specific releases in test teardown. They model explicit API
ownership, not fingers physically holding a keyboard. This mode also overwrites
the clipboard with synthetic Chinese. The fixture disables its own text correction;
an active Chinese IME can still transform raw key input, so ABC remains required.

With a suitable extended second display, full mode drags B's title bar to that
display, checks B→A→B focus and text changes, and drags B back. This is an actual
bidirectional **window drag**, not a file/data transfer. A second display must
have a distinct, nonoverlapping desktop rectangle and enough room for B; otherwise
this part is explicitly reported as unverified. Coordinates include negative
origins and use global CGEvent **points**, never Retina pixels. Display rectangles,
visible rectangles, scale factors, window frames and control targets are recorded.

### Compare an isolated 0.0.3 checkout

`--repo` selects a source checkout; `--python` re-executes the harness with an
existing interpreter in isolated Python mode. Neither flag creates a checkout or
changes any environment. A baseline interpreter alone does **not** select the
baseline implementation: supply its checkout with its matching in-place native
build. These options allow running the new harness against an older checkout
without copying harness files there:

```sh
uv run --no-project --no-python-downloads --python /absolute/current/python tests/manual/verify_modifiers.py \
  --allow-desktop --mode combo \
  --repo /absolute/isolated/scapkit-0.0.3-checkout \
  --python /absolute/baseline/python \
  --output /tmp/scapkit-baseline-combo-new
```

Repeat with `--mode paste` and a new output directory. Module paths for the
package, wrapper and native `.so` must resolve inside the **selected checkout**;
an installed AIOS/site-packages copy is rejected. Reports record these paths,
the checkout path and interpreter/version. This checks provenance, not whether
an existing native build is up to date; build the intended revision beforehand.

## Evidence and stopping behavior

Each run uses a unique instance ID, owned child PID, and new output directory
(system temp by default; `--output` must not already exist). `report.json` records
per-step system flags, fixture focus transitions, text/selection, geometry,
shortcut/click/scroll/drag effects and the fixture's event flags/global points.
`state.json`, `compile.log`, `fixture.log`, and the compiled binary remain there.

The Python probe calls `CGEventSourceFlagsState` independently through `ctypes`
for **combined (0)** and **HID (1)** state. It requires
`flags & 0x1e0000 == 0`, not `flags == 0`:

| Modifier | Mask |
| --- | --- |
| Command | `0x100000` |
| Shift | `0x020000` |
| Option | `0x080000` |
| Control | `0x040000` |

Flags are sampled immediately after each completed public API call, before any
subsequent input, and while waiting briefly for observable effects. Requested
modifier bits in a shortcut's delivered events are expected; those bits must not
remain in either system-state table after the call. Other system flag bits are
retained as evidence and do not fail the modifier assertion. Intentional internal
down/up intervals of a compound API call are not independently sampled.

The first unexpected modifier stops input immediately and preserves the first
sample plus fixture state. A short **read-only** delay captures the final event
and selection state. Missing effects, lost focus, stale heartbeat, moved windows,
or changed display geometry also stop the run; there is no coordinate retry.
Cleanup terminates only the child fixture PID. **No Escape, blanket modifier
clearing, unrelated-app cleanup, or clipboard restore is sent.** Residual system
flags are sampled again after fixture termination and intentionally left intact.
Any recovery from a reproduced stuck modifier is a separate user action; the
next run refuses an already-held modifier baseline.

Exit 0 means the selected checks passed, with coverage and skips in the report;
exit 1 means failure/interruption and exit 2 means invalid/missing opt-in arguments.
User-held physical modifiers (including left/right variants and concurrent
press/release) are always listed separately in `unverified_user_held_modifiers`.
Cancellation and raw holds are verified only by `lifecycle`; standalone modifier
key clicks and concurrent physical transitions remain separate acceptance items.
Event/state sampling cannot prove the absence of every transient race.

## Offline validation only

Until real testing is authorized, only parse Python or compile Swift. These
commands do not import the library, run the harness, or launch the fixture:

```sh
uv run --no-project --no-python-downloads --python /absolute/path/to/python -c 'import ast,pathlib; p=pathlib.Path("tests/manual/verify_modifiers.py"); ast.parse(p.read_text(), filename=str(p), feature_version=(3,10)); print("Python syntax OK")'
scapkit_compile_dir=$(mktemp -d /tmp/scapkit-modifiers-compile.XXXXXX)
xcrun swiftc -warnings-as-errors tests/manual/modifier_fixture.swift -o "$scapkit_compile_dir/modifier_fixture"
```

Compile success is not desktop acceptance, modifier-fix verification, or runtime
validation of older macOS, Intel, or a baseline 0.0.3 build. Completed live runs
are documented in [the acceptance record](../../docs/2026-09-12-modifier-validation.md).
