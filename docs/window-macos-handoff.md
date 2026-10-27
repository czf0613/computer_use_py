# macOS window backend handoff

Implementation phase, 2026-10-27. The public input is exactly
`01b318287d40d9124ae2708eaa3f1fcc6821cbbc`; the separately fetched fixture
dependency is exactly `f03d182beaf14a4d1f40c9d083246eec7ccbadea`, integrated by
ordinary merge. The frozen plan referenced by that contract is
`18304c2b16f562c50e0434b7d41d53fc6ac944a3`. These are distinct inputs.
Delivery is restricted to `refs/heads/codex/provider-window-macos-20261027`.
Independent acceptance of the eventual commit belongs to the upstream reviewer;
the implementation checks below are not an independent review.

## Review recovery from b6a0206

The independently reviewed commit was
`b6a020645fb980db95e4c2bbd6ded72c417f7070`. Its successful compilation and tests
missed three defects. The recovery preserves that history, the original input
and the fixed fixture dependency; no input/branch refetch or worktree reset was
performed. Steer `steer_2d493fb2732c4247a8ff31f68855ddb1` supplied the findings.
No unpublished reviewer artifacts or integration worktree were accessed, and
`tests/manual/verify_window_macos.py` is unchanged in this recovery.

Before changing the production logic, new transport-injection tests reproduced
the defects. The actual command was:

```text
/bin/zsh -lc 'env -u PYTHONHOME -u PYTHONPATH uv run --frozen --python 3.10 python -I tests/window_macos_checks.py --review-repro'
```

It exited **1** with the continuous output `12 failed, 8 passed in 0.55s`.
The failures are retained as red-test evidence, not relabeled as passes:

* Stop failure, output-removal failure and both failures reported successful
  close/finished state and released the capture gate.
* AXChildren count/values failures and invalid-object errors produced a valid
  root-only snapshot despite a missing child.
* The middle traversal could change geometry, retained object, identifier,
  enabled/editable state or actions, yet the returned snapshot was accepted.
  The geometry case reproduced returned x=80 with fingerprint x=10.

The stop transport now independently tracks running-source and attached-output
effects, including late start/stop completions. Tests execute the production
`SWShot`, `SWCaptureCleanup` and `SWClose`; each failure case uses a disposable
isolated Python child with an empty environment, without resetting the native
gate. The AX transport supplies local typed AX proxies and overrides only IPC
boundaries under `SCAPKIT_TESTING`. Targets are deliberately **not synthetic**:
the real validation, `ax_children`, `node_info`, tree traversal, retained element
objects and snapshot validation run. No desktop IPC occurs in these tests.

Production corrections:

* `stopCompleted` means the callback returned; `finished` requires both a
  successful stop and successful output removal. Only the latter releases the
  stream and gate. The flight has a pure-native self-owner until confirmed
  cleanup, so dropping the capsule/session cannot abandon a running source or
  attached output. On failure, close reports `backend_busy` and remains
  incomplete on repeated calls. This is a bounded process-lifetime quarantine
  of at most one stream, not a retry loop; a failed stop/removal intentionally
  prevents further captures in that process. No actual SCK leak was demonstrated
  or claimed by the offline transport test.
* `AXChildren` with a zero count or an unsupported children attribute is a legal
  leaf. Count/value IPC failures, invalid objects, malformed arrays or mismatched
  counts propagate as `stale_element`; they cannot become an empty subtree.
  Explicit max-depth/max-nodes truncation is still valid and tested.
* The returned nodes and retained native objects now generate the fingerprint
  from the same observation, including raw identifier and all exposed geometry/
  action attributes. One subsequent traversal validates that observation.
  There is no unvalidated middle traversal and no automatic retry of a race.
  Stable trees pass; the previously observed A/B/A cases are rejected.

After the fixes, `--review-only` passed **168 tests on Python 3.10.20 and 168 on
Python 3.14.6t**, including 20 new transport cases, all 73 existing native window
tests and the affected public window API/identity/coordinate tests. Import and
tests shared one free-threaded process and ended with
`gil_enabled_after_tests=False`; the isolated stop-probe children assert the
same GIL condition. Target 13.0 and 15.0 strict warnings/availability and Clang
static analysis passed in both testing and production preprocessing modes.
Separate forced production builds and isolated import checks passed on both
Python ABIs: `production_test_hooks=absent; WINDOW_BACKEND_ABI=1`. The 3.14t
production import also asserted the GIL remained disabled. Both interpreters
and loaded extensions were verified to belong to this worktree.

The successful targeted command is `tests/window_macos_checks.py --review-only`
under the same sanitized uv/Python invocation as the red-test command above;
use the separate 3.14t environment and add `--free-threaded` for that ABI.
`--production --target 13.0` (or `15.0`) runs compiler checks without test hooks;
adding `--analyze` runs static analysis in that production preprocessing mode.

The earlier full legacy suite and fixture desktop evidence below refer to the
original implementation phase. They are not claimed as rerun during this
recovery. No desktop test or continuous human-input acceptance was run for this
fix. Profile trust, public ABI/schema, Windows/MCP and the fixture are unchanged.
Upstream independent review must inspect the newly published SHA.

## Implemented boundary

`WINDOW_BACKEND_ABI=1` registers all seven real entries: enumerate, open, close,
attach, screenshot, inspect and action. `window.m` handles validation, envelopes,
immutable capsule pointers and the native actor. `window_capture.m` implements
ScreenCaptureKit and JPEG; `accessibility.m` implements independent AX roots,
bounded trees, ownership checks and guarded `AXValue`/`AXPress`. The public
Python API, `.pyi`, schemas and MCP were not changed. The macOS source list and
Security framework are registered in setuptools; Windows build/source changes
are only those already present in the fixed dependency.

The dedicated NSThread/CFRunLoop serializes native target/session state. A
bounded admission queue (64), finite call deadlines and AX messaging timeouts
prevent unlimited worker creation. An overdue operation quarantines the actor
until it returns; a timed-out dispatched write also blocks subsequent writes.
No timeout cancels an AX operation already sent to the application, so its
result is explicitly unknown and must not be retried automatically.

Python authorization validation and all Python allocations run with attached
thread state and outside native locks. The actor and frame callbacks hold only
native data; blocking waits run with detached thread state. Capsules retain
their immutable native pointers until destruction. Close is idempotent and
does not free still-reachable session/element pointers. AX observer sources and
their contexts are removed on the actor before release. Element objects retain
their owning session and original AX object; they are never looked up again by
identifier to dispatch a write.

## General single-window capture

Capture enumeration uses `SCShareableContent` with off-screen windows included,
normal window layers and reliable process-start identity from `proc_pidinfo`.
Open retains the originally enumerated `SCWindow`. It does not require AX
permission, an AX root, a fixture identity or a profile. There is no PID/title
heuristic that attaches an AX window. A read-only `CGMainDisplayID()` query
initializes WindowServer access for command-line Python before invoking SCK.

Each screenshot creates a separate short-lived `SCStream` with a
`desktopIndependentWindow` filter on that retained object. Cursor and audio are
disabled; newer shadow/child-window/microphone settings are availability guarded.
The code accepts only a complete BGRA frame with a fresh display timestamp,
copies pixels and metadata from that exact sample, then stops/removes the
output. JPEG uses ImageIO. No display capture, global input, clipboard,
activation or restoration fallback is used by these new paths.

The frame's `contentRect`, `scaleFactor` and `contentScale` determine the affine
image-to-window mapping. Missing/inconsistent metadata yields
`mapping_status=unavailable`, not an invented mapping. The captured image can
still be useful without a mapping. Bounds and process identity are rechecked
before/after capture; observed resize returns `geometry_changed`. Known
minimized windows return `window_minimized`. Without an explicitly attached AX
witness, SCK absence cannot distinguish minimization from disappearance: the
session is conservatively stale. An incomplete/old/blank frame is never returned
as a successful cached screenshot.

Fresh enumeration is used to validate presence, process-start identity and
geometry, never to replace the retained `SCWindow` by another object with the
same numeric ID. Observed disappearance, process replacement, monitoring loss
or stream source errors permanently invalidate that target. A new reference
must be explicitly selected after invalidation.

The SDK `SCShareableContent.h` describes `windowID` as a `CGWindowID` and does not
provide an atomic incarnation token. `SCStream.h` documents asynchronous stop
completion, including stop failures, and `didStopWithError` reports a stopped
stream with its error. These interfaces do not promise a synchronous close
notification for every window or an atomic transaction with an external app.
This implementation does **not** claim to exclude an unobserved close/reuse
(ABA) between observations, or to freeze an external window during capture.
Those limits do not remove ordinary read-only capture. The fixture close probe
tested observable disappearance; it did not prove every stream-close timing.

The one-capture-at-a-time native gate remains occupied through late start/stop
completion. A start completing after the caller timed out stops itself. The
session retains a pending capture owner, so close reports `backend_busy` rather
than successful cleanup while completion is outstanding. Callbacks own their
state and never borrow a capsule pointer. Tests cover both late-start race
orders, real sample-buffer ownership and close before/after cleanup. A system
API that never completes can leave this bounded native operation quarantined;
the backend does not spawn replacement streams or claim that cleanup finished.

## AX bridge and stable profile identity

AX enumeration is independent and limited to the explicit PID. Retained roots
require process-start verification, application-window membership and a
destruction observer. Trees have bounded depth/nodes/text, detect cycles and
do not expose values/passwords. Layout events, repeated live tree fingerprints,
geometry revisions and snapshot expiry invalidate element references. AppKit
may expose ignored containers differently in `AXChildren` and `AXParent`:
membership uses the public `AXWindow` attribute with exact root equality when
present, otherwise a bounded parent walk. No private AX window-number API is
used.

Only the exact owned fixture adapter supplies cross-API proof. Its public root
`AXHelp` is a JSON object with schema `scapkit.fixture.bridge.v1`, source hash,
version, PID, process-start identity, process token, window-instance token,
slot, actual `NSWindow.windowNumber`, and Boolean modal/closed fields. The
backend verifies the live executable hash and code validity, exact source and
version metadata, current process identity and raw root `AXIdentifier` before
comparing the declared window number to the retained `SCWindow.windowID`.
Same-PID sibling roots fail attachment. No external JSON path enters the seven
native APIs; the manual harness alone reads its own temporary fixture state.
The backend reads only the explicitly selected executable and its own loaded
extension for build identities; it does not scan applications or state files.

Reviewed pins in `window_fixture_identity.h`:

| Artifact | SHA-256 |
| --- | --- |
| `tests/manual/window_fixture_macos.m` | `826b5520278953797bea9167ed6ab0a5db0293fb3f81762d166c1b16b880d30c` |
| Fixture built by `tests/manual/build_window_fixture_macos.sh` here | `d7f5a799567efe0cde4f44bcff69eac7e725a84ed6e6a28b0f5ecfd2ac66d53e` |

A different compiler/build producing a different executable hash requires
review and an explicit pin update. It is not accepted by path, filename,
bundle ID alone or an unsafe flag. The fixture binary itself is not committed.

Raw identifiers include the root instance token and remain in native objects
for ownership and invalidation. Only after the raw control identifier exactly
matches this verified root's corresponding control does the adapter publish
`fixture.text` or `fixture.increment`. Its stable window semantic identifier is
`fixture.window.v1`; application/version/build and backend-build/environment
remain exact scope fields. Random launch/instance tokens are excluded from
these semantic profile fields, so a fresh instance of the same verified build
does not require a different control rule. Ordinary third-party identifiers
are not normalized. No wildcard or public schema change was introduced.

Production writes still require the frozen Python authorization and exact
native profile scope. The evidence trust root remains empty; unverified
third-party writes are refused. Enabled/editable/password, root membership,
known minimized/modal state, process/generation/geometry and current snapshot
are checked before a single AX dispatch. Observed frontmost/focus interference
revokes the session/profile; this observation does not establish continuous
noninterference. `AXPress` acceptance does not prove effect; the tested fixture
text adapter can verify a set-value result by exact readback. Unknown outcomes
block further writes on that session.

## Executed validation and limits

Host: macOS 26.6.2 (25G83), arm64. Every uv/build/test command used local
`env -u PYTHONHOME -u PYTHONPATH`; test drivers used `python -I` and asserted
interpreter and extension paths were in this worktree. No global environment
or user configuration was changed.

| Check | Actual result |
| --- | --- |
| Forced testing builds, CPython 3.10.20 and 3.14.6t | Passed separately |
| Full affected offline suite, 3.10 | 410 passed |
| Full affected offline suite, 3.14t | 409 passed, 1 skipped; GIL disabled after import and tests in the same process |
| Strict warnings and availability, deployment targets 13.0 and 15.0 | Passed for new native modules |
| Clang static analysis, targets 13.0 and 15.0 | Passed for new native modules |
| Final lifecycle/minimized/PID additions, each Python ABI | 5 passed, 68 deselected; 3.14t GIL still disabled |
| Forced production builds and isolated imports, each Python ABI | Passed; ABI 1 and no `_test_*` methods; 3.14t GIL disabled |
| Authorized owned-fixture desktop probe, 3.10 | Passed on this host with already available ScreenCapture and AX permissions |

The single free-threaded skip is the existing capture test requiring synchronous
GC during Python dict allocation; GIL assertions were not weakened. Offline
checks include old capture, input construction, recording codecs/lifecycle,
async and process regressions, plus native window state/ownership/resource
tests. No real display/input/clipboard operation occurs in those offline tests.
The final five additions were run narrowly after the full suites; the full
suites were not repeated just to produce a new receipt. The production builds
are the final local extensions. These counts must not be combined into a claim
that an unexecuted larger suite passed.

The desktop harness started only the workspace fixture, with a fresh limited
environment and its own temporary state directory. It opened capture before
enumerating any AX roots; received 1080x804 BGRA and JPEG with verified mapping;
verified exact bridge and public DTO parsing; rejected a sibling bridge;
exercised candidate text/increment actions only through `SCAPKIT_TESTING`;
observed target effects and unchanged sibling state; then closed A1 while A2
and the process remained alive and verified the old capture was rejected.
The fixture reported startup active = 0. The harness closed its sessions and
terminated/reaped only its own child (exit 0). No screenshots were saved, no
user clipboard was accessed, no OS permission was requested/granted, and no
production profile was promoted. Fixture supervisor monitoring also exits when
its launcher disappears. The successful desktop run preceded the final narrow
offline close/minimized/PID changes; it was not rerun after those changes.

**Not run:** continuous human keyboard/mouse noninterference, production profile
acceptance, other applications' desktop behavior, minimized/modal/occlusion/
multi-display desktop matrices, Windows, older macOS/Intel runtime, and Python
3.13t window checks. Target 13/15 compilation does not establish runtime
acceptance there. The final commit still needs the upstream isolated-worktree
review requested by the caller.

## Recovered failures (not retroactively counted as passes)

Early strict compilation exposed a CFRunLoop const conversion; static analysis
also found missing defensive null guards. Those were corrected. Two early
synthetic mapping tests failed because their fractional-scale test image sizes
were truncated; rounding the fixture dimensions up preserved the production
mapping guard. The initial fixture-only capture restriction was removed per
steer `steer_b672be9052304e75af1c6fec2fa1c74f`.

Early desktop attempts exposed an AppKit activation-policy return/readback
difference, initial window publication latency, a command-line SCK
`CGS_REQUIRE_INIT` process abort (exit 134), and AX ignored-container ancestry
differences. The fixes respectively use actual policy readback, bounded
readiness enumeration before selecting the reference, public read-only
WindowServer initialization, and exact public AX window membership. The failed
probe's owned process was confirmed gone; subsequent harness runs report owned
cleanup. AX Boolean boxing was also corrected for the frozen DTO types. A final
resource correction retains late capture completion through close rather than
overstating cleanup. Successful subsequent checks are recorded separately from
these historical failures.

## Reproduction

Run from this worktree. Use `UV_PROJECT_ENVIRONMENT=.cache/window-macos-314t`
and `--python 3.14t` for the second ABI, add `--free-threaded` to the check driver,
and force a rebuild whenever changing ABI or test hooks.

```sh
env -u PYTHONHOME -u PYTHONPATH uv sync --frozen --python 3.10
env -u PYTHONHOME -u PYTHONPATH SCAPKIT_TESTING=1 uv run --frozen --python 3.10 setup.py build_ext --inplace --force
env -u PYTHONHOME -u PYTHONPATH uv run --frozen --python 3.10 python -I tests/window_macos_checks.py
env -u PYTHONHOME -u PYTHONPATH uv run --frozen --python 3.10 python -I tests/window_macos_checks.py --target 13.0 --analyze
env -u PYTHONHOME -u PYTHONPATH uv run --frozen --python 3.10 python -I tests/window_macos_checks.py --target 15.0 --analyze
sh tests/manual/build_window_fixture_macos.sh
# Only with explicit desktop authorization and existing permissions:
env -u PYTHONHOME -u PYTHONPATH uv run --frozen --python 3.10 python -I -X faulthandler tests/manual/verify_window_macos.py --desktop
env -u PYTHONHOME -u PYTHONPATH SCAPKIT_TESTING=0 uv run --frozen --python 3.10 setup.py build_ext --inplace --force
env -u PYTHONHOME -u PYTHONPATH uv run --frozen --python 3.10 python -I tests/window_macos_checks.py --production
```

Provider receipts use the actual SDK `commandExecution.command`, including its
shell wrapper, rather than these bare reproduction commands. Per finishing
steer `steer_b554ff567cde4b7c8bb766c0b57ecc56`, successful test evidence is reused
without rerunning tests solely to change receipt formatting. Publication must
be confirmed separately with `git ls-remote origin` on the exact output ref.
