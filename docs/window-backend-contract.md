# Window backend ABI 1

Task A freezes this contract against plan commit
`18304c2b16f562c50e0434b7d41d53fc6ac944a3` (code base
`25750d1e82e0d52853f6eba7825de23408fd5e05`). The executable Python boundary is
`window.py`; native DTO declarations are in `screen_capture_kit/_scapkit.pyi`.
This document specifies the **new** API only. Legacy display capture, input,
recording, process and MCP behavior is unchanged.

**Task A has no native window backend or desktop acceptance.** An extension
without all seven entries below and integer `WINDOW_BACKEND_ABI = 1` produces
`backend_unavailable`. Never advertise ABI 1 with placeholders. Fake backends
exist only in tests, injected by monkeypatching a private loader. No public
backend/provider injection, unsafe flag, global input fallback or new base
dependency is provided. Profiles ship with an empty evidence trust root.

## Public Python API

```python
async def list_windows(*, pid: int | None = None) -> list[WindowInfo]: ...
async def list_accessible_windows(pid: int) -> list[AccessibleWindowInfo]: ...
async def open_window(ref: WindowRef) -> WindowSession: ...
async def open_accessible_window(ref: AccessibleWindowRef, *,
                                 profile_id: str | None = None) -> AccessibleWindowSession: ...

# Both kinds of session:
async def capabilities() -> WindowCapabilities: ...
async def elements(*, max_depth: int = 8, max_nodes: int = 512) -> ElementSnapshot: ...
async def element_at(x: float | WindowPoint, y: float | None = None, *,
                     snapshot_id: str) -> ElementRef: ...
async def set_value(element: ElementRef, value: str) -> ActionReceipt: ...
async def invoke(element: ElementRef) -> ActionReceipt: ...
async def aclose() -> None: ...

# WindowSession only:
async def attach_accessibility(ref: AccessibleWindowRef, *, profile_id: str) -> None: ...
async def screenshot(*, format: str = "jpeg", quality: int = 80,
                      timeout_seconds: float = 5.0) -> WindowFrame: ...
```

Sessions support `async with await open_window(ref)` and expose `token`,
`closed`, `cleanup_complete`, and `last_action`. All classes/functions above,
public DTOs, errors and profile APIs have lazy package-level exports. Importing
these Python APIs does not import the desktop extension. A semantic-only
session has no `screenshot` method. Capture open never automatically attaches
accessibility, including on Windows: callers explicitly select both roots.

`WindowRef`, `AccessibleWindowRef`, `ElementRef` and sessions cannot be directly
constructed. References are immutable, non-serializable Python objects. Their
random `.token` strings are diagnostic, **not accepted as selectors**. Enumerated
refs are one-use and expire in 60 seconds, including after a failed open/attach
attempt that reached submission. Roots, sessions and elements are bound to the
creating process and running event loop. MCP must maintain its own bounded,
client-owned token-to-object registry; it must not reconstruct these objects.

Each session admits one operation at a time; concurrent work returns
`backend_busy` instead of accumulating Python waiters. `aclose` excludes new
operations, waits for admitted work, and shares one cleanup result with every
close caller. It marks the Python session closed even when native cleanup
fails; only a successful native close sets `cleanup_complete=True`.
Dropping the session releases its capsule as a last resort, not a substitute
for `async with`/`aclose`.

Each `elements` call invalidates the preceding Python snapshot. A snapshot
expires in 30 seconds; native epochs/geometry are also checked live. Element
objects cannot cross sessions or survive root invalidation. `element_at` only
searches that snapshot after native revalidation: it excludes ancestors of
matching descendants, rejects overlapping sibling candidates with
`ambiguous_element`, and reports `element_not_found` for no hit. It never uses
desktop hit-testing, nearest-element heuristics or pointer movement.

For screenshot coordinates, use `point = frame.to_local(image_x, image_y)`,
then `await session.element_at(point, snapshot_id=tree.snapshot_id)`. The point
retains session, generation, revision and unit for freshness checks. Supplying
two numbers instead explicitly selects root-local coordinates. No coordinate
click/drag/keyboard/clipboard API exists; `coordinate_input` is always unsupported.

## Seven synchronous native entries

Every argument is positional-only. Every result is exactly one of:

```python
{"ok": True, "value": VALUE}
{"ok": False, "error": {
    "code": str, "native_domain": str | None, "native_code": int | str | None,
    "dispatch_state": "not_dispatched" | "accepted" | "unknown",
    "retry_hint": "never" | "refresh" | "after_permission" | "after_close"
}}
```

All listed DTO keys are required, even when their value is `None`. Do not
return a bare capsule, `None`, exception string, or legacy `OSError` for a
window operational failure. `TypeError`/`ValueError` before dispatch and Python
allocation failures may use the ordinary exception mechanism. Do not include
window titles, entered text, passwords, clipboard data or arbitrary provider
messages in error fields. `native_domain` identifies an API domain; native codes
are numeric or short symbolic codes. The `operation`, `window_token` and
`operation_id` error attributes are added by Python.

| Native entry | Input | Success VALUE |
| --- | --- | --- |
| `_window_enumerate(kind, pid, limit)` | `kind="capture"/"accessible"`; positive uint32 PID or `None` (None only for capture); limit 1..256 | `list[NativeWindowInfo]`, at most limit; overflow reports `resource_limit`, no silent target substitution |
| `_window_open(ref, kind)` | Correct-kind, unconsumed candidate capsule | `NativeWindowSession` |
| `_window_close(handle)` | Session capsule | `None`; close even if stale, idempotent and concurrent-safe |
| `_window_attach(handle, accessible_ref)` | Capture session with no semantic root; unconsumed semantic candidate | `None`; retain the verified semantic root in this session atomically |
| `_window_screenshot(handle, format, quality, timeout_seconds)` | Capture session; `jpeg`/`bgra`, quality int 0..100, finite timeout (0,30] seconds | `NativeWindowFrame` |
| `_window_inspect(handle, query, options)` | Session plus the discriminated query below | Context, snapshot or `None` |
| `_window_action(handle, element, tree_epoch, geometry_revision, action, value, authorization, operation_id, timeout_seconds)` | Session and owned element capsules; positive int64 epoch/revision; `set_value`/`invoke`; string/None; verified grant; opaque request ID; finite (0,30] seconds (Python uses 5) | `NativeWindowActionReceipt` |

Inspect queries are exhaustive:

* `"capabilities", {}` → `NativeWindowContext`, freshly read identity/state/API support.
* `"elements", {"max_depth": int, "max_nodes": int}` → `NativeWindowSnapshot`.
  Depth is 0..32 (root depth 0); nodes 1..4096. Return an explicitly bounded tree
  prefix, never escape the retained root. Root is always included. No values.
* `"validate_snapshot", {"tree_epoch": int, "geometry_revision": int}` → `None`
  only if the retained root, tree and geometry still match. Otherwise fail.

Booleans must never pass as integers, IDs, revisions, dimensions or coordinates.
All real numbers are finite, bounded to absolute value ≤1e12. Ordinary strings
are UTF-8, no NUL, at most 4096 bytes. Titles/names/identifiers may be empty or
None; profile identifiers must be nonempty. A replacement value may be empty
but is at most 65536 UTF-8 bytes. IDs for frames/operations/native diagnostics
are at most 128 bytes; process-instance marks at most 256. Native enforces the
same bounds **before** allocating, querying unbounded trees, or dispatching.

## Capsule ownership and identity

| Capsule name | Retained state and invalidation |
| --- | --- |
| `scapkit.window_ref.v1` | Capture object + native window incarnation + process-instance evidence + generation; not a bare ID |
| `scapkit.accessible_window_ref.v1` | Independent retained AX/UIA window root + process instance + generation |
| `scapkit.window_session.v1` | Capture and/or semantic target; one incarnation for its whole lifetime; no automatic rebind |
| `scapkit.window_element.v1` | Retained element + originating **session** + root incarnation + tree epoch; never name-based lookup on action |

Native open/attach also atomically consume candidates so direct extension
calls cannot reuse them. Native validates capsule name, session ownership,
process instance, generation, ancestry and live object state. Python checks
are additional routing guards, not an OS security boundary. ID/PID reuse,
destroy notifications, process exit, invalid provider object or lost monitoring
invalidate the original target permanently. An already-running action can
still have an unknown outcome; do not claim cross-process atomicity.

Close marks native state closed before releasing resources, rejects future
operations, and keeps the capsule pointer allocated/immutable until capsule
destruction. Readers retain resources they already own. Allocation failures
after acquiring a stream, actor, root or element must clean up before returning
NULL. A successful open transfers session ownership to its capsule; all
enumeration/element capsules likewise release their retained references on GC.
Python explicitly closes late successful open results and malformed successful
open DTOs that carry a handle. Never return hidden live resources outside the
documented `handle`/`ref` fields.

`_window_attach` verifies a **public identity bridge**, not PID/title/size
similarity. macOS ordinary AX roots remain separate from SCWindow capture.
The reference AppKit adapter may prove the same process instance, unique window
instance marker exposed by AXIdentifier, and NSWindow.windowNumber through its
public application interface. Without such proof return `identity_unverifiable`;
multiple candidates return `ambiguous_window`. No private AX window-number API.
Windows verifies the retained HWND incarnation against the explicit UIA root
and retained root comparison/ancestry; it must not select a foreground root.
Attach failure leaves the capture session unbound. Python closes the entire
session after canceled attach or a malformed successful attach result.

## DTO fields

`Rect` is `{x: float, y: float, width: float, height: float}` with positive
dimensions. Global origins may be negative. An absent/zero-size element bound
uses `None`. `unit` is `points` on macOS and `pixels` on Windows. Dimensions of
images are always physical image pixels. No `ui_scale_factor` participates in
conversion.

`NativeWindowInfo`:

```text
ref: candidate capsule                  platform: "macos" | "windows"
native_id: str (diagnostic only)          pid: positive uint32
process_instance: nonempty str          generation: positive int64
title: str | None                       bounds: Rect (global)
coordinate_unit: unit                   visible: bool | None
minimized: bool | None
capture_support, inspect_support: "supported" | "unsupported" | "unknown"
```

Process-instance evidence must be obtained from a reliable process-start
identity (not PID or name); failure is `identity_unverifiable`. Unknown state
remains None, never false. `NativeWindowSession` is exactly `{handle: capsule,
generation: int, process_instance: str}` matching the selected candidate.
Attached sessions retain the original capture generation/process instance in
all outer DTOs; their semantic root also has independently checked native state.

`NativeWindowContext`:

```text
generation, process_instance: session identity
geometry_revision: positive monotonically increasing int64
identity_status: "verified" | "unverified" | "stale"
capture, inspect_elements, set_value, invoke: "supported" | "unsupported" | "unknown"
minimized, modal: bool | None
focus_policy: "no_activation" | "unverified"
subject: NativeWindowSubject | None
```

`NativeWindowSubject` has **exactly** these string fields: `platform`,
`application_id`, `application_version`, `application_build`, `environment`,
`backend_build`, `window_identifier`. `application_id` is a verified application
identity (bundle/signing identity or executable identity), `application_build`
identifies exact executable content/build, `environment` identifies the tested
OS build and architecture, `backend_build` identifies the native implementation
build, and `window_identifier` identifies the verified root class/adapter.
Publish the adapter's deterministic identity convention with its evidence;
do not derive an identity from a mutable title, PID, or caller-supplied string.
If any identity cannot be established, use subject=None and refuse writes.

`focus_policy="no_activation"` means the native path has established all library
preconditions, including Windows IUIAutomation2 AutoSetFocus=false **readback**.
It is not background verification. Python independently computes each public
capability's `background_validation`, `profile_id`, exact `application_version`
and `reason_code` from the registered profile. A global action capability means
some exact rule is verified, not permission to act on every control.

`NativeWindowSnapshot`:

```text
generation, process_instance, geometry_revision: as above
tree_epoch: positive int64, changed on structure/layout invalidation
coordinate_unit: unit                 mapping_status: "verified" | "unavailable"
elements: list[NativeWindowElement]    # root first, topological preorder
```

Each element has `ref` capsule, `identifier: str|None` (stable AXIdentifier or
UIA AutomationId scoped to the root), `role: str`, `name: str|None`,
`bounds: Rect|None` (root-local), `enabled/editable/password: bool|None`,
`actions: list["set_value"|"invoke"]`, `depth: int`, `parent_index: int|None`.
Only index 0 has no parent; each later parent precedes its child, at depth minus
one. No duplicate refs or cycles. Total identifier/role/name bytes ≤1 MiB.
**No element `value` field**, including non-password values, is returned.
Unknown password/enabled/editability state cannot authorize a write.

`NativeWindowFrame`:

```text
generation, process_instance, geometry_revision: as above
frame_id: unique nonempty str          captured_at: monotonic seconds
state: "complete"                     format: "bgra" | "jpeg"
data: immutable bytes                 image_width, image_height: positive int
stride: int for BGRA, None for JPEG    window_bounds: Rect (global surface bounds)
local_rect: Rect | None                image_content_rect_px: Rect | None
client_rect_local: Rect | None         coordinate_unit: unit
image_to_local: (a,b,c,d,tx,ty) | None  mapping_status: "verified" | "unavailable"
```

Each dimension ≤16384; width × height × 4 and encoded length ≤128 MiB. BGRA
stride ≥width×4, and `len(data)==stride×height`. JPEG dimensions must match its
SOF header; Python checks framing/SOF metadata, while native owns valid encoding.
`captured_at` uses the same epoch/rate as Python `time.monotonic()` (convert
mach/QPC correctly), inside this call's capture interval, not old cache time.

For verified mapping, `local_rect` fits the window surface and content pixels
fit the actual image. The affine convention is x=a*ix+c*iy+tx, y=b*ix+d*iy+ty.
V1 requires b=c=0 and the transform implied by the rectangles:

```text
a = local_rect.width / image_content_rect_px.width
d = local_rect.height / image_content_rect_px.height
tx = local_rect.x - image_content_rect_px.x*a
ty = local_rect.y - image_content_rect_px.y*d
```

Only content pixels are mappable, with exclusive right/bottom edges. Client
bounds, when present, fit the local window surface. If mapping is unavailable,
`local_rect`, `image_content_rect_px`, `image_to_local` are all None; the image
can still be returned. Native must normalize SCK same-frame metadata; it cannot
label SDK point rectangles as pixels or substitute the enumeration display scale.

Every screenshot owns a fresh short-lived SC stream/WGC pool, receives a new
complete frame, copies bytes, and initiates teardown on **every** success/failure/
timeout. A successful screenshot requires completed teardown. A platform start/
stop completion can arrive after the caller's finite budget: retain the bounded
native capture owner, refuse replacement acquisition, and report incomplete
cleanup (`backend_busy`) until completion. Do not free a live callback or label
that timeout as successful cleanup. On macOS, a returned stop callback alone
does not establish cleanup: both stop and output removal must succeed. Failure
retains the pure-native owner and the one capture gate for the process lifetime;
close remains incomplete and replacement captures are refused, without retries.
The implemented macOS backend still uses one short-lived stream per screenshot,
not a persistent observation stream.
No display cropping, hidden fallback, cursor/audio capture, automatic popup
inclusion, restore/activation, idle/blank/old cached frame substitution. Recheck
identity and geometry before delivery; movement/DPI/resize races fail with
`geometry_changed`, not an internal retry. Minimized targets return
`window_minimized`; unknown state with no fresh frame returns `capture_timeout`.

## Profile trust and dispatch

`register_profile(BackgroundProfile(...))` registers a candidate and returns
`ProfileValidation(status="not_run")`; it cannot enable actions. The immutable
profile contains `profile_id`, all seven Subject fields, and 1..64 exact
`ProfileAction(identifier, role, action)` rules. IDs cannot be rebound to a
different scope. There are no wildcards, version ranges, force flags or boolean
verification arguments.

`verify_profile(profile_id, evidence: bytes)` accepts at most 64 KiB. SHA-256 of
the **exact bytes** must match the installed, source-reviewed
`window_profiles._APPROVED_EVIDENCE_SHA256[profile_id]` trust root. The JSON must
contain:

```text
schema_version: 1                    # integer, not bool
profile: complete exact profile dict # actions serialized as a list
implementation_commit: 40 lowercase hexadecimal characters
report_sha256: 64 lowercase hexadecimal characters
checks: {each required check name: "passed"}
```

Required checks: `target_effect`, `sibling_unchanged`, `foreground_continuity`,
`pointer_continuity`, `keyboard_continuity`, `clipboard_unchanged`,
`forbidden_calls_zero`, `occluded`, `modal_rejected`, `minimized_rejected`,
`stale_rejected`, `cancel_cleanup`. The report hash links the complete audited
logs and scenario evidence. `not_run` or missing checks cannot enable writes.
This is verification of an **audited report's identity**, not automatic proof
of physical non-interference. Adding a pin requires review of the real evidence
and a source change; there is no public API to install trust anchors from user
data. Self-owned apps use the same schema/review boundary. The later authorized
acceptance harness may exercise candidate native code in a testing build, but
must not expose that bypass in production or automatically pin its own report.

Before `_window_action`, Python matches the current native Subject, exact
control identifier/role/action and profile state. It passes:

```python
authorization = {"profile": COMPLETE_PROFILE_DICT, "evidence_sha256": VERIFIED_DIGEST}
```

The native Python entry **must** call
`window_profiles._validate_authorization(authorization)` with attached Python
thread state and no native locks held. This rejects arbitrary dictionaries,
unverified/revoked profiles and modified scopes, returning a fresh verified
scope. Copy that scope and the value into native-only owned storage before
detaching thread state. Immediately before dispatch, the owning native actor
rechecks every Subject field, session/element generation and ancestry, epoch,
geometry, exact control rule, API support, enabled/non-password/editability,
known non-minimized/non-modal state and no-activation policy. Checking Python
capabilities alone is insufficient. The actor also maintains native revocation
state so already-queued operations fail after it observes interference.
Translate a rejected registry grant into a `background_not_verified`,
not_dispatched failure envelope on the Python entry thread, before native locks.

Use AX settable value/AXPress or UIA Value/Invoke directly, once. Do not set
focus, raise/restore a window, post keys/messages, move the pointer, or read/write
the clipboard. `set_value` replaces the entire value, not keyboard/IME emulation.
`invoke` is limited to the exact non-navigation/non-popup tested action.

`NativeWindowActionReceipt` is `{operation_id: exact input string,
dispatch_state: "not_dispatched"|"accepted"|"unknown",
effect_state: "verified"|"unknown"}`. Effect is verified only after a specific,
safe state readback; API success alone means accepted/unknown. Verified effect
requires accepted dispatch. Do not log the readback value.

After interference, Python revokes the shared profile permanently for this
process and invalidates the session. Re-registering/re-verifying cannot revive
it; a new reviewed profile version is required. After an unknown write, Python
blocks further writes on the session and retains the uncertain receipt. Reads
remain possible; unknown does not authorize creating a new session to retry.

## Threading, cancellation and errors

Python calls each synchronous entry on an executor worker. That worker arrives
with a Python thread state; it validates inputs/copies them, detaches thread
state for native locks/queue waits/IPC/capture/encoding, obtains a retained
native result snapshot, releases native locks, reattaches thread state, then
allocates the envelope/dicts/bytes/capsules. **No Python object allocation while
holding native locks. No Python API, refcounting, callbacks, exception creation,
or interpreter-owned object access on SC callbacks, AX observer/actor threads,
COM MTA actors or WGC callbacks.** Those threads own only native state; results
travel through bounded native queues/condition variables. This rule includes
timeouts and late completions, not just successful calls.

The Windows actor owns COM creation, operations, event registration/removal and
release on its dedicated non-UI MTA thread. macOS capture queues/AX runloops own
their native resources. Callbacks retain safe native completion state, not a
borrowed Python handle pointer. Capsule destruction enqueues native teardown
or otherwise releases thread state before waiting; it cannot rely on the GIL.
Follow AGENTS.md ARC strong-field clearing, autorelease pool and reader mutex
rules. Native method-table docstrings remain NULL.

All native control waits have finite budgets (5 seconds for open/attach/
enumerate/inspect/close; screenshot/action use their supplied timeout). An
expired queued Windows request is atomically canceled before admission and
reported as not_dispatched; it does not quarantine a different healthy running
request. Only an admitted operation that exceeds its completion budget can
quarantine the actor. An
unresponsive provider may outlive that budget: quarantine its actor, report
`backend_busy`/`outcome_unknown`, and retain only native state until late return.
Queues are bounded (maximum 64); at most one write is in flight per actor.
Do not create replacement threads to bypass a stuck actor, free its state early,
or promise immediate reclamation. Close reports incomplete cleanup explicitly.

Cancellation before Python executor admission suppresses the native call under
a lock and records not_dispatched. Once admitted, Python drains the one native
call through repeated cancellation, then preserves CancelledError. It never
retries. Late open handles are closed; successful screenshot owns no stream on
return, while incomplete teardown retains the quarantined native capture owner;
canceled attach closes the session. A canceled write with no confirmed
non-dispatch is unknown even if it finishes late. `session.last_action` is the
durable operation ID/state query; cancellation exceptions also receive these
attributes at the coroutine boundary, but CPython 3.10 Task can recreate the
exception for an awaiting task. No reliance on exception object identity.
Close itself is never suppressed by cancellation.

Error codes are frozen in `window_errors.ERROR_CODES`; the plan's errors retain
their meanings. Additions for this boundary are `backend_unavailable`,
`backend_protocol_error`, `wrong_event_loop`, `resource_limit`,
`element_not_found`, `ambiguous_element`. Python maps permission denial to
`WindowPermissionError`, capture timeout/unknown outcome to `WindowTimeoutError`,
other operational failures to `WindowError`. Malformed native write results or
unexpected post-submission exceptions become `outcome_unknown`. Accepted/unknown
dispatch always has retry_hint=never. Only confirmed not_dispatched can ever be
a candidate for caller-directed retry; the library performs no retries.

Task B/C must implement this ABI without changing it independently. Task D owns
build registration/MCP integration and real fixture validation. See
[A-stage handoff](window-contract-handoff.md) for actual checks and remaining work.
