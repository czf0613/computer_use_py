# Task A handoff — 2026-10-27

Task A implements the public Python boundary and freezes
[native ABI 1](window-backend-contract.md). **This is not completed native window
support or desktop acceptance.** The current compiled extension correctly
reports `backend_unavailable`; the production profile evidence allowlist is empty.

Fixed input/plan: `18304c2b16f562c50e0434b7d41d53fc6ac944a3`, supplied in the
Provider's pinned input_sync and already HEAD when this work began. Original
code input: `25750d1e82e0d52853f6eba7825de23408fd5e05`. Delivery branch:
`refs/heads/codex/provider-window-contract-20261027`. Consume the exact delivery
commit from the receipt, not a moving branch tip. The subsequently announced
fixture commit `f03d182beaf14a4d1f40c9d083246eec7ccbadea` was not fetched or merged;
it is not an implementation dependency of task A.

## Delivered behavior

* Separate explicit capture/semantic references and sessions; no automatic
  macOS SCWindow/AX pairing or foreground fallback. Seven native entries and
  complete dict/bytes/capsule/error/ownership definitions are frozen in the
  contract and `.pyi`.
* Loop/process ownership, one-use/expiring candidates, session-owned elements,
  snapshot epochs, geometry revisions, bounded input/frame/tree validation,
  JPEG header dimensions, BGRA stride and provenance-preserving coordinates.
* Candidate profile registration plus reviewed, exact-byte SHA-256 evidence
  verification. Exact app/build/OS/backend/root/control/action matching; no
  public boolean/unsafe/digest-injection bypass. Interference revokes a profile.
* One operation per session, suppression before executor admission, draining
  of admitted calls through repeated cancellation, late-open/attach cleanup,
  shared close outcome, and explicit incomplete cleanup. Unknown writes retain
  their receipt, block further writes and never automatically retry.
* Lazy exports; existing desktop/process/recording implementations unchanged.
  No base dependency, native source, setup.py or MCP changes.

## Executed checks

All interpreter/build/test commands used command-local
`env -u PYTHONHOME -u PYTHONPATH`. This handles the Provider App Shell environment
leak without changing the library or global environment. The final check runner
uses `python -I`, checks its interpreter, verifies package and extension paths
point to **this checkout**, confirms no desktop import from the pure Python
window import, checks actual legacy-backend unavailability, and runs only the
three window test files plus `tests/test_process.py` (disposable shell profiles).
It checks the GIL before/after extension import and after pytest on 3.14t.

| Check | Final observed result |
| --- | --- |
| Python 3.10.20 | `114 passed in 1.34s`; isolated=1; this checkout's cpython-310 extension |
| Python 3.14.6 free-threaded | `114 passed in 1.56s`; isolated=1; this checkout's cpython-314t extension; `gil_enabled_after_tests=False` |
| Ruff, new Python modules/tests | `All checks passed!` |
| Existing macOS extension rebuild, both ABIs | `setup.py build_ext --inplace --force`, exit 0; this compiles unchanged native code, not a new window backend |

Reproduce the final test commands after `uv sync` in the selected environment:

```sh
env -u PYTHONHOME -u PYTHONPATH uv run --frozen --python 3.10 python -I tests/window_contract_checks.py
env -u PYTHONHOME -u PYTHONPATH UV_PROJECT_ENVIRONMENT=.cache/window-contract-314t uv run --frozen --python 3.14t python -I tests/window_contract_checks.py --free-threaded
```

For the separate 3.14t environment, initial setup was
`env -u PYTHONHOME -u PYTHONPATH UV_PROJECT_ENVIRONMENT=.cache/window-contract-314t uv sync --python 3.14t`.
Force-build commands used the corresponding `uv run --python ... setup.py
build_ext --inplace --force` with the same environment prefix. Generated
extensions/environments/build directories are ignored and are not delivered.
The incidental uv.lock rewrite from environment resolution was restored; no
dependency change is part of A.

Development runs did reveal failures: a 3.10 Task recreated CancelledError and
lost exception-only metadata, and lint cleanup removed an imported fixture.
The durable receipt assertion and explicit fixture binding correct those issues;
the final runs above passed. No earlier failed run is being reported as passed.
These are implementation self-checks, not task R independent acceptance.

## Downstream work and unrun checks

| Owner / validation | Status and required work |
| --- | --- |
| B macOS native window backend | **not implemented in A**; implement all seven entries, SC capture/AX roots/bridge, native synchronization and offline native tests |
| C Windows native window backend | **not implemented in A**; implement WGC/UIA actor, AutoSetFocus readback, retained identities and native offline tests; Windows compile/runtime **not_run** here |
| D integration | Build registration, MCP client-owned registries/tools/guide, sample apps and authorized acceptance harness remain downstream |
| Desktop capture/control/non-interference | **not_run** on all platforms; no screen acquired, target window operated, OS permission changed or user clipboard read |
| Verified production profiles | None; independent real acceptance and reviewed evidence pins are required before enabling writes |
| R independent review | **not_run**; inspect the selected integration SHA in a separate worktree |
| Older macOS / Intel, Windows 10 / ARM64 desktop, Python 3.13t window checks | **not_run**; no compatibility claims inferred from this host's 3.10/3.14t checks |

Native implementers must not return Python objects from actor/callback threads.
Validate the registry authorization on the attached Python entry thread, copy
native-only state, detach while waiting, and allocate return DTOs only after
reattaching outside native locks. A stuck provider retains native state and
reports incomplete cleanup, rather than freeing a live callback target.

Only the authorized task-A branch is to be committed/pushed. No default-branch
merge, tag, release, deployment, other Agent worktree modification or Provider
call is part of this delivery.
