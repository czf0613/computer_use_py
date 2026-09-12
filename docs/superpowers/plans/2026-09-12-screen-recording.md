# Screen Recording Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to implement and review the independent units, then integrate and verify in this session. Do not commit, push, release, or perform desktop tests.

**Goal:** Record one display and system audio to hardware H.264/AAC MP4 with correct static-screen timing.

**Architecture:** A native stream owner holds the latest frame and drives a fixed-rate host-clock timeline. A separate native writer receives timed video and PCM, owns VideoToolbox and AVAssetWriter, and publishes a completed file without replacing existing data. Async Python functions wait through cancellation cleanup.

**Tech Stack:** CPython C API, Objective-C ARC, ScreenCaptureKit, VideoToolbox, AVFoundation, CoreMedia, pytest.

**Spec:** `docs/superpowers/specs/2026-09-12-screen-recording-design.md`

## Global Constraints

- macOS 13.0+ source, Python 3.10+, free-threaded 3.13/3.14; official wheels remain macOS 15+ arm64.
- H.264 hardware required; no video bitrate or data rate limit property.
  Final user-approved revision: video_quality defaults to 0.75; explicit None leaves encoder quality defaults.
- AAC system audio only, 48000 Hz stereo; microphone never registered or enabled.
- Default fixed 30 fps; positive int32 fps; native-sized even-padded SDR frames.
- No desktop, real system audio, microphone, input, or clipboard operations in automated verification.
- Preserve prior version-baseline edits. No commits or publication. Stop before manual tests for the user to prepare.

## Task 1: Native encoding and file writer

**Files:** create `native_code/osx/include/recording_writer.h`, `native_code/osx/src/recording_writer.m`, `tests/native/recording_writer_probe.m`, `tests/test_recording_writer.py`.

**Interface:** `ScapkitRecordingWriter` is an Objective-C class with the following operations, called serially with Python thread state detached. It internally serializes encoder callbacks and AVAssetWriter work.

```objc
- (nullable instancetype)initWithPath:(NSString *)path width:(int)width height:(int)height fps:(int)fps error:(NSError **)error;
- (BOOL)appendVideo:(CVPixelBufferRef)frame time:(CMTime)time duration:(CMTime)duration error:(NSError **)error;
- (BOOL)appendAudio:(CMSampleBufferRef)sample origin:(CMTime)origin error:(NSError **)error;
- (nullable NSDictionary *)finishAt:(CMTime)endTime error:(NSError **)error;
- (void)cancel;
```

All video times and finish time are relative to zero; audio retains its original host-clock PTS and supplies the same host-clock origin. `finishAt:` returns keys `path`, `size_bytes`, `duration_s`, `width`, `height`, `fps`. The caller guarantees no append calls race finish/cancel. Both finish and cancel must safely release codec, writer, frames and temporary files, including error paths; finish is single-use internally (outer handle caches result). Retain buffers accepted asynchronously. NSError reports failures; never call Python from this unit.

- [x] Write a native synthetic driver and pytest tests before implementation; driver creates 64x48 CVPixelBuffers and 48000 Hz stereo PCM, never SCStream.
- [x] RED: `uv run --no-sync pytest tests/test_recording_writer.py -x` must fail because the real writer does not yet exist.
- [x] Implement hardware-required H.264 with VideoToolbox and video passthrough AVAssetWriterInput with sourceFormatHint, AAC audio, bounded pending work, host-origin audio alignment and silence gaps, unique same-directory temporary output, no-clobber atomic final publication.
- [x] GREEN: inspect/decode synthetic MP4 using ffprobe/ffmpeg when available; always use native AVAssetReader probe for CI assertions so ffmpeg is not required. Assert actual frame count/timestamps, H.264/AAC tracks, decoded audio offsets, file duration including static tails, short final frame, existing-target protection, and cleanup.
- [x] Self-review ownership, callback ordering, finish draining, hardware failures and backpressure; report commands and evidence. No commit.

## Task 2: Python public API and cancellation

**Files:** create `src/scapkit_computer_use/recording.py`, `tests/test_recording.py`; update `src/scapkit_computer_use/__init__.py`, `screen_capture_kit/__init__.py`, `screen_capture_kit/types.py`, `_scapkit.pyi`.

**Consumes:** native `start_recording(display_id: int, output_path: str, fps: int)` returns an opaque capsule; `stop_recording(handle)` returns the result dictionary above; `_abort_recording(handle)` cancels/cleans without publishing a final file.

**Produces:**

```python
async def start_recording(display_id: int, output_path: str | os.PathLike[str], fps: int = 30) -> RecordingHandle: ...
async def stop_recording(handle: RecordingHandle) -> RecordingResult: ...
# frozen dataclass: path: pathlib.Path, size_bytes: int, duration_s: float,
# width: int, height: int, fps: int
```

- [x] Write behavioral fake-native tests for validation before dispatch, PathLike normalization, result conversion, startup cancellation cleanup after late completion, repeated cancellation during stop and error chaining.
- [x] RED: `uv run --no-sync pytest tests/test_recording.py -x`.
- [x] Implement lazy native dispatch; reject bool/non-integer IDs/fps, IDs outside 1..UINT32_MAX, fps outside 1..INT32_MAX, bytes paths, NUL/empty paths, missing parent and existing target before any native side effect.
- [x] Use a shielded executor future; if startup is cancelled, wait for worker completion and call native abort on its returned handle, then preserve cancellation. On stop cancellation, wait for cleanup then re-raise cancellation. Native state provides concurrent/idempotent stop behavior.
- [x] GREEN: run the focused tests and pure-base import regression. Public docstrings explain explicit stop and initial-frame wait. No MCP changes or commit.

## Task 3: Stream ownership and fixed frame clock

**Files:** create `native_code/osx/include/recording.h`, `native_code/osx/src/recording.m`, `tests/native_recording_helpers.h`, `tests/test_native_recording.py`; update `native_code/osx/src/ext.c`, `setup.py`, `native_code/osx/CMakeLists.txt`.

**Consumes:** Task 1 writer methods; implements Task 2 native functions and SCAPKIT_TESTING-only synthetic injection hooks.

- [x] Write tests for native invalid arguments that cannot reach the desktop, synthetic startup/first-frame timing, no-new-frames recording, exact/non-exact stop boundary, concurrent stop, abort and late callbacks.
- [x] RED: force a test-hook build and run `uv run --no-sync pytest tests/test_native_recording.py -x`.
- [x] Implement per-recording native owner, distinct immutable capsule, safe weak delegate, first-frame wait with timeout, timer-driven frame boundaries and one pending frame. Freeze at the single stop request host time; finish on a worker after queue fence and stream stop; repeated stops wait for the same cached result/error.
- [x] Capture NV12 SDR at native even dimensions, show cursor; record all system audio, explicitly disable microphone on supported versions. Frame buffers are retained on the capture queue; no Python allocation under locks or queues. Waiting callers detach Python thread state.
- [x] Add source and framework dependencies to setuptools and IDE hints; no new Python dependency.
- [x] GREEN: synthetic hooks must feed the real timer/state machine and writer while bypassing all display lookup and SCStream creation. Test no-GIL concurrent calls and native error propagation; no commit.

## Task 4: Integration, documentation, review and validation

**Files:** update `README.md`, `docs/development.md`, `docs/releasing.md`, `.github/workflows/ci.yml`, `.github/workflows/release.yml` where explicit test/source lists require it; create `tests/manual/verify_recording.py` and recording instructions under `tests/manual/`.

- [x] Verify Python exports plus public start/stop through synthetic native hooks; ensure base/MCP imports retain their dependency and platform boundaries.
- [x] Add safe tests to CI, keep test hooks absent from production builds and wheels. Document API, timestamps, errors, file publication, cancellation, native resolution, static-screen behavior and hardware requirement.
- [x] Force build under Python 3.10 and 3.14t with `SCAPKIT_TESTING=1` and `CFLAGS=-Werror`; run only affected synthetic recording tests and existing native/capture/async regression tests. Verify import does not enable GIL. Run clang API-availability checks and static analysis for new native code.
- [x] Build production artifacts into temporary directories, verify minos=13.0 and absence of test symbols; check package includes headers/sources. Keep original developer extension ABI intact after multi-version verification.
- [x] Independent review against spec, code, test evidence and error paths; fix substantive findings and rerun covering tests.
- [x] Prepare manual acceptance command using a user-selected display/path and synthetic on-screen/audio fixture, but do not run it. Stop and ask the user to prepare for actual desktop and system-audio testing.

## Execution rulings and progress

- Ruling: run in the current checkout on a task branch, preserving the prior macOS-baseline edits; no clean worktree can contain those uncommitted prerequisites without copying them. No other dirty scope exists.
- Ruling: delegate bounded Python and native-writer units with the fixed interfaces above while the controller implements stream integration; they have disjoint owned files.
- Ruling: synthetic VideoToolbox/AAC encoding is authorized automated verification; real display/audio capture is not.
- Ruling: no commit steps, because repository guidance requires explicit authorization that has not been given.

Completed implementation and synthetic verification. Python 3.10: 151 passed; 3.14t: 150 passed, 1 existing GC-timing skip; GIL stayed disabled. Production sdist/wheel and both ABI imports verified without test hooks. Native availability/static checks passed. Offline modifier probe target aligned to 13.0: 63 passed. All scoped review findings resolved. Real recording remains pending user preparation; see docs/2026-09-12-recording-validation.md.
