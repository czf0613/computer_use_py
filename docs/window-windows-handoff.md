# Windows window backend handoff — 2026-10-27

This branch implements the seven synchronous `WINDOW_BACKEND_ABI=1` entries.
Windows compilation, synthetic execution, and desktop acceptance are separate
checks. This handoff was prepared on macOS. **The latest completed Windows CI
(40479320493, a5e2c6a) built the library and passed 186 offline tests in each of
six jobs, including free-threaded GIL assertions; all six then failed fixture
compilation. The deadline and fixture-path corrections below await a new CI
result. All real window/UIA/non-interference scenarios remain not_run**.
The Provider receipt records the new workflow run ID, URL, tested SHA and
observed status. Dispatch is not a passing build or test result.

## Fixed inputs and ownership

- Public Python contract: `01b318287d40d9124ae2708eaa3f1fcc6821cbbc`, already
  pinned by Provider. Plan version referenced by that contract remains
  `18304c2b16f562c50e0434b7d41d53fc6ac944a3`.
- Fixture dependency: fetched `refs/heads/codex/provider-window-fixtures-20261027`
  from origin and verified `f03d182beaf14a4d1f40c9d083246eec7ccbadea`; ordinary
  merge commit `c167be24e39b3a96bce7309b97718ebbf3f358ad`. No newer tip selected.
- Output only: `refs/heads/codex/provider-window-windows-20261027` in
  `https://github.com/czf0613/computer_use_py.git`.
- Windows sources, Windows setup fragment, non-publishing Windows workflow,
  Windows synthetic tests and Windows fixture build script changed. Public
  Python API, schema, MCP and macOS implementation are unchanged. The dependency
  merge includes its original macOS fixture files, without local edits.

## Implementation

`window.cpp` validates/copies Python arguments and grants before detaching thread
state. One process-wide dedicated MTA actor owns the registry, COM objects,
registration/removal, WGC items, D3D resources and results. It has at most 64
queued requests and one executing operation. Each call has a finite caller wait;
a deadline suppresses a queued/pre-dispatch action atomically, and dispatched
actions become unknown. An actor that fails to finish within its caller budget
(including the bounded capture cleanup grace below) is quarantined permanently,
never replaced with additional workers. Late results and releases contain only native
objects. Close immediately closes the immutable capsule token, queues cleanup,
and reports incomplete cleanup on timeout/failure. No COM pointer or Python
object crosses to an event callback or arbitrary executor.

`window_runtime.cpp` retains full-width HWNDs, process handles and
`GetProcessTimes` creation marks. A separate message-pumping WinEvent monitor
remains responsive if a provider blocks the MTA actor. Destruction, lost
monitoring, process exit, WGC Closed or invalid UIA roots permanently invalidate
an incarnation; there is no reconnect by handle or title. The monitor and actor
are lazy, bounded process-lifetime threads and pin their module for late callbacks.
The monitor stores weak roots and only updates native atomics.

Capture candidates retain the original `GraphicsCaptureItem` created with
`CreateForWindow`. Each screenshot starts a fresh one-frame free-threaded WGC
pool, explicitly disables cursor capture, polls only that pool, copies validated
BGRA, and closes the frame/session/pool on every path. Optional WIC JPEG encodes
those pixels. There is no screen crop, old-frame return, restore, activation,
input or clipboard fallback. Dimensions, byte budgets, device removal, Closed,
minimized state and movement/size/DPI races are checked. DWM extended frame
bounds and client coordinates are read in a temporary per-monitor DPI context.
Only equal actual image/surface dimensions receive a verified 1:1 physical-pixel
mapping; unmatched dimensions return unavailable mapping, without guessed scales.

Screenshot admission, queueing, WGC capture and encoding share one absolute
steady-clock deadline. WGC no longer starts a fresh timeout after dequeuing.
The caller allows a fixed **one-second completion/cleanup grace** beyond that
deadline; it is not additional time to wait for a frame. Normal no-frame expiry
closes/releases its short-lived resources and returns `capture_timeout`, leaving
the actor usable. If a native call or cleanup has still not returned at the end
of the grace, the caller returns `backend_busy` with native domain
`actor.cleanup_unknown` and `WAIT_TIMEOUT` (258), and quarantines the same actor.
A close that cannot finish within its existing five seconds reports
`actor.cleanup_incomplete`; the Python session is closed with
`cleanup_complete=False`. Late native return does not reset quarantine or
retroactively change that Python close result. No public DTO/schema changed.

`accessibility.cpp` creates `CUIAutomation8` on the MTA, queries `IUIAutomation2`,
sets connection/transaction timeouts and `AutoSetFocus=false`, and reads it back.
Writes recheck that setting. `ElementFromHandle` is called only at candidate
creation. The original root, elements, RuntimeIds, native HWNDs and raw
AutomationIds remain retained. Live checks compare process/handle/runtime/raw ID
and walk bounded ancestors back to the original root. Trees are bounded preorder
prefixes, never desktop hit tests, and never expose ValuePattern values.
Structure/property events invalidate snapshots; validation also rereads retained
node metadata. Capture/semantic attachment is explicit and proves the same
retained HWND/process incarnation. No capture open implicitly attaches UIA.

The native action entry calls the frozen Python `_validate_authorization` with
attached thread state before any native lock/IPC. The actor then rechecks the
exact subject/control/action, ownership, epoch/geometry, known enabled/password/
editability, minimized/modal state and no-focus policy. ValuePattern SetValue
and InvokePattern Invoke are real, single-dispatch paths. SetValue has bounded
private equality readback; Invoke success remains accepted/effect unknown.
Observed activation into the target revokes the native profile and invalidates
its root; uncertain writes block further writes. This observation is not a
replacement for the full manual non-interference matrix.

## Stable fixture profile identity

Instance identity and profile semantics are deliberately different:

- The native object retains the full original
  `scapkit.fixture.<A1|A2|B>.<UUID>.window` and each control's complete identifier,
  RuntimeId, HWND and root ancestry. All remain live-checked; a replacement
  window/control is never rebound.
- Only the fixture adapter recognizes `ScapkitWindowFixtureV1`, the exact UUID
  grammar and a data-only PE resource matching the frozen fixture source hash
  `b6dab9646f045a2d6cae2932d24b823b89caacb1e99bccd24e55e73efc1f2023`.
  The Windows fixture build script embeds this resource, with no new desktop
  behavior. The expected hash lives in a packaged native header so source builds
  do not depend on pruned `tests/` files.
- Its subject uses application ID `scapkit.fixture.windows.v1`, version `1`,
  SHA-256 of the actual executable for application_build, an OS/architecture
  environment, the Windows native source build hash, and window_identifier
  `fixture.window`. None includes a launch's random UUID.
- Only after exact original-ID/root/type/ancestry checks, and matching the
  root's actual `GetDlgItem` HWND (100/101), do exposed identifiers become
  `fixture.text` / `fixture.increment`, with roles `UIA.Edit` / `UIA.Button`.
  Root identifier is `fixture.window`. Raw complete identifiers stay native.
- Ordinary providers are not normalized and have no fixture subject. Resource
  recognition alone is not authentication or authorization: the profile still
  binds the exact executable build, environment and backend. The frozen
  production evidence allowlist remains empty, so **all unaccepted production
  writes, including fixture writes, are rejected**. No wildcard, unsafe flag,
  caller digest trust root, or DTO/schema change was added.

The later fixture acceptance harness may exercise testing builds with an
isolated private test grant; it must not pin its own report or change production
trust. Only the supplied synthetic-content fixtures are eligible for later
explicitly authorized desktop tests; do not use existing user applications.

## Validation and limits

### First CI failure and correction

[Windows run 40476742074](https://github.com/czf0613/computer_use_py/actions/runs/40476742074)
tested `45459782bfeda5ea4570fabeb40c15f79a159f3a`. All six matrix jobs failed in
`Build with synthetic test hooks`, during the editable build started by `uv sync`.
The shared compiler error was C2664 at `accessibility.cpp:376`: the client
`IUIAutomationValuePattern::SetValue` accepts a BSTR, whereas `wstring::c_str()`
is only a const UTF-16 buffer. No tests, fixture compilation, MCP checks, GIL
import checks or production wheel steps ran in this CI. This is an actual failed
build, not a runner/queue limitation or a synthetic pass.

The correction uses `SysAllocStringLen` with a checked, bounded length. A
non-copyable RAII owner checks allocation failure and calls `SysFreeString` on
normal return and exceptions. `SetValue` borrows this owned input BSTR; its
readback BSTR has a separate owner, including the case where a failed provider
fills the output. No const cast or borrowed `wstring` buffer is substituted.
The shared production helper is also exercised by a testing-only local COM
ValuePattern receiver with actual OLE strings: empty, Unicode/surrogate and
maximum-length replacements, failed writes/readbacks and the null-allocation
rejection path. This test creates no UIA client or desktop provider. It executed
successfully in all six jobs of the second run below.

The complete failed logs were retrieved and checked. They contained only that
C2664 compiler diagnostic, expected setuptools manifest exclusions and D9025
(`/W4` overriding setuptools' `/W3`). No warning was suppressed. Compilation
stopped at the first translation unit, so the remaining native units and
`/analyze` results still require the rerun; absence of their diagnostics is not
evidence they passed. All six matrix entries, existing tests and GIL assertions
are retained.

The first Provider receipt was separately rejected with CHECK_UNVERIFIED because
its command strings used `/bin/zsh -c` instead of the SDK-recorded `/bin/zsh -lc`.
The actual commands were submitted without a manual shell wrapper. Future
receipts use the SDK's full recorded wrapper and verbatim output; this correction
does not relabel the failed CI as successful.

### Second CI failure and deadline correction

[Windows run 40479320493](https://github.com/czf0613/computer_use_py/actions/runs/40479320493)
tested `a5e2c6a35fbaf4db9615fda19d8fcdf35a7c7e81`. Its final conclusion is
**failure**, not pending. All six library builds and offline test steps succeeded
(`186 passed` per job); all three free-threaded jobs printed
`gil_enabled_after_tests=False`. All six fixture compilation steps failed with
C1083: `Cannot open include file: 'build/window-fixtures/fixture-build.h'`.
The optional MCP and production wheel steps were skipped. The full run and
failed-step logs were retrieved; no single job was extrapolated to the matrix.
The build script now resolves the generated header to its absolute filesystem
path and supplies it as one `/FI` argument, avoiding source-directory-relative
include lookup and preserving paths containing spaces. Fixture source and its
recognition hash are unchanged.

Review also found that the old outer Actor timeout started before queueing,
while WGC restarted the same duration when capture began. Normal no-frame expiry
could therefore race the caller into permanent quarantine. The absolute deadline
and fixed cleanup grace above correct this. New tests go through the production
Actor, registry and screenshot entry, using test-only native resource owners:
three real no-frame waits with delayed cleanup must return `capture_timeout`,
release on the actor thread, then accept another screenshot and close. Separate
child processes hold a synthetic native call or its cleanup past the completion
bound. Each must report unknown/incomplete cleanup, refuse new work, release its
late resources only on the actor, and retain quarantine after release. They also
exercise the unchanged Python session's `closed`/`cleanup_complete` behavior.
Child processes use a fresh user/system environment and cmd AutoRun, never a
copy of the parent environment, plus isolated imports and before/after GIL checks.
The synthetic blocker has a 20-second failsafe and the child a 30-second bound;
there is no replacement Actor or test reset of quarantine.

These new timeout tests have not run on Windows at this revision. Local Python
3.10/3.14t syntax checks passed. The first local Ruff check found two import-order
errors in the timeout test files; those were corrected before the passing rerun.
No Windows compiler or desktop result is inferred from local checks.

### Offline coverage

`tests/test_native_window_windows.py` drives actual native capsule consumption,
registry ownership, epoch/geometry checks, action preconditions/dispatch,
frame assembly/encoding, concurrent close, resource GC and actor-thread release.
Its native synthetic provider is compiled only under SCAPKIT_TESTING. A native
race probe exercises the same dispatch/timeout CAS used in production. These
checks create no HWND, UIA client, display capture, input, audio or clipboard
operation. They do not prove UIA provider behavior, WGC delivery, all resource
failure paths, or absence of leaks in a real desktop session.

`tests/windows_window_checks.py` uses `python -I`, asserts the interpreter and
extension belong to the checkout, and asserts the GIL is disabled before import,
after import and after tests in the same free-threaded process. The existing
Windows workflow retains all six matrix entries (x64 3.10, 3.14, 3.13t, 3.14t;
ARM64 3.14, 3.14t) and existing MCP exclusions. It force-builds each ABI with
`/W4` and `/analyze`, runs existing offline checks plus window checks, compiles
the fixture and executes only its `--offline-probe` protocol, then force-rebuilds
without hooks and builds a non-release wheel. No release workflow, tag or release
is created. Build/test steps remove PYTHONHOME/PYTHONPATH locally; no global
configuration changed.

Local macOS preparation: Python 3.10 and 3.14t syntax compilation, Ruff, YAML
matrix validation and diff whitespace checks. The first Ruff check found two
import-order errors in the new runner; these were fixed and the subsequent Ruff
check passed. A local SDK-header search found no Windows headers; this was not a
Windows compilation attempt. Windows build/test results must come from CI.

Still not_run: Windows UIA/WGC fixture desktop behavior, background continuity,
occlusion, minimized/modal/stale desktop scenarios, cross-DPI mapping, Windows
10/ARM64 desktop behavior, real cancellation/late-provider cleanup, and upper
layer independent review. Production profiles have no approved evidence. Native
provider timeout quarantines the single actor for the remainder of the process;
it can leave native state retained until a provider returns. No thread is killed
and no immediate cleanup is promised.

API references consulted: [IUIAutomation2 settings](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomation2),
[WinEvent message-loop/ordering requirements](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwineventhook),
[CreateForWindow](https://learn.microsoft.com/en-us/windows/win32/api/windows.graphics.capture.interop/nf-windows-graphics-capture-interop-igraphicscaptureiteminterop-createforwindow).
