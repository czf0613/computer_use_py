"""Exercise a compiled fixture's offline protocol; never create desktop windows.

Run with uv run --no-project ...; only the standard library is needed.
The probe's synthetic native IDs are deliberately NOT desktop evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid


def windows_environment() -> dict[str, str]:
    """Obtain the user/system block with inheritance off, not os.environ."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    userenv.CreateEnvironmentBlock.argtypes = [ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.BOOL]
    userenv.DestroyEnvironmentBlock.argtypes = [ctypes.c_void_p]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x000A, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    block = ctypes.c_void_p()
    try:
        if not userenv.CreateEnvironmentBlock(ctypes.byref(block), token, False):
            raise ctypes.WinError(ctypes.get_last_error())
        result = {}
        address = block.value
        while True:
            entry = ctypes.wstring_at(address)
            if not entry:
                break
            # Ignore cmd's special per-drive entries (not valid Popen env keys).
            if not entry.startswith("="):
                key, value = entry.split("=", 1)
                result[key] = value
            address += (len(entry) + 1) * ctypes.sizeof(ctypes.c_wchar)
        return result
    finally:
        if block.value:
            userenv.DestroyEnvironmentBlock(block)
        kernel.CloseHandle(token)


def windows_private_acl(path: Path) -> bool:
    """Check a protected DACL containing a single allow ACE for the file owner."""
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    advapi.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(pointer), ctypes.POINTER(pointer), ctypes.POINTER(pointer), ctypes.POINTER(pointer), ctypes.POINTER(pointer)]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.GetSecurityDescriptorControl.argtypes = [pointer, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
    advapi.GetAce.argtypes = [pointer, wintypes.DWORD, ctypes.POINTER(pointer)]
    advapi.EqualSid.argtypes = [pointer, pointer]
    kernel.LocalFree.argtypes = [pointer]
    owner, acl, descriptor = pointer(), pointer(), pointer()
    error = advapi.GetNamedSecurityInfoW(str(path), 1, 5, ctypes.byref(owner), None,
                                        ctypes.byref(acl), None, ctypes.byref(descriptor))
    if error:
        raise ctypes.WinError(error)
    try:
        control, revision = wintypes.WORD(), wintypes.DWORD()
        if not advapi.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not acl.value or not (control.value & 0x1000):
            return False
        # ACL header: revision, reserved, size (WORD), AceCount (WORD).
        if ctypes.c_ushort.from_address(acl.value + 4).value != 1:
            return False
        ace = pointer()
        if not advapi.GetAce(acl, 0, ctypes.byref(ace)):
            raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.c_ubyte.from_address(ace.value).value == 0 and bool(advapi.EqualSid(owner, pointer(ace.value + 8)))
    finally:
        kernel.LocalFree(descriptor)


class FixtureProtocolTests(unittest.TestCase):
    binary: Path
    source: Path

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="scapkit-fixture-offline-")
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name).resolve()
        self.profile = self.parent / "profile"
        self.profile.mkdir(mode=0o700)
        for name in (".zshenv", ".zprofile", ".zshrc", ".zlogin"):
            (self.profile / name).touch(mode=0o600)

    def run_binary(self, *arguments: str, success: bool = True):
        command = [str(self.binary), *map(str, arguments)]
        if sys.platform == "win32":
            # Direct native executable: no shell interpolation/AutoRun is needed.
            environment = windows_environment()
        else:
            # Bootstrap a login shell with disposable user profiles and a clean block.
            # exec keeps the fixture PID equal to Popen.pid for the protocol check.
            environment = {"HOME": str(self.profile), "ZDOTDIR": str(self.profile),
                           "PATH": "/usr/bin:/bin", "TMPDIR": str(self.parent)}
            command = ["/bin/zsh", "-l", "-c", 'exec "$@"', "fixture-probe", *command]
        with subprocess.Popen(command, env=environment, cwd=self.parent,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
            try:
                stdout, stderr = process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                self.fail("offline probe timed out")
        if success:
            self.assertEqual(process.returncode, 0, stderr.decode("utf-8", "replace"))
        else:
            self.assertNotEqual(process.returncode, 0)
        return process.pid, stdout, stderr

    def probe(self, role="a", name="state"):
        directory = self.parent / name
        pid, stdout, _ = self.run_binary("--offline-probe", "--background", "--role", role,
                                         "--state-dir", str(directory))
        state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
        self.assertIn(b"offline probe passed", stdout)
        self.assertEqual(state["pid"], pid)
        self.assertEqual(state["schema_version"], 1)
        self.assertEqual(state["fixture_version"], "window-fixture-v1")
        self.assertEqual(state["build_sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(state["execution"], "offline_probe")
        self.assertEqual(state["desktop_validation"], "not_run")
        self.assertFalse(state["running"])
        self.assertFalse(state["active"])
        self.assertTrue(state["background"])
        self.assertEqual(state["role"], role)
        self.assertEqual(state["snapshot_sequence"], 2)
        self.assertTrue(state["process_start"])
        uuid.UUID(state["process_token"])
        identifiers = set()
        for window in state["windows"]:
            uuid.UUID(window["instance_token"])
            self.assertNotEqual(int(window["native_id"], 0), 0)
            self.assertIsInstance(window["native_id"], str)
            self.assertEqual(len(window["bounds"]), 4)
            for part in ("window", "text", "increment"):
                identifier = window["accessibility"][part]
                self.assertIn(window["instance_token"], identifier)
                self.assertNotIn(identifier, identifiers)
                identifiers.add(identifier)
        return directory, state

    def test_identity_text_and_native_id_reuse(self):
        _, state = self.probe()
        old, sibling, replacement = state["windows"]
        self.assertEqual([item["slot"] for item in state["windows"]], ["A1", "A2", "A1"])
        self.assertTrue(old["closed"])
        self.assertFalse(replacement["closed"])
        self.assertEqual(old["native_id"], replacement["native_id"])
        self.assertNotEqual(old["instance_token"], replacement["instance_token"])
        self.assertEqual(old["text"], 'Synthetic "quotes" \\ slash\n\tUnicode: 窗口 🧪')
        self.assertEqual(old["count"], 2)
        self.assertEqual(sibling["text"], "A2 initial - synthetic only")
        self.assertEqual(sibling["count"], 0)
        self.assertEqual(replacement["count"], 0)
        if sys.platform == "win32":
            self.assertGreater(int(old["native_id"], 0), 2**53)

    def test_independent_b_process_instances(self):
        _, first = self.probe("b", "b-first")
        _, second = self.probe("b", "b-second")
        self.assertEqual([item["slot"] for item in first["windows"]], ["B"])
        self.assertNotEqual(first["process_token"], second["process_token"])
        self.assertNotEqual(first["windows"][0]["instance_token"], second["windows"][0]["instance_token"])

    def test_event_overflow_is_explicit_and_sequences_are_contiguous(self):
        _, state = self.probe()
        events = state["events"]
        self.assertEqual(len(events), state["event_limit"])
        self.assertGreater(state["events_dropped"], 0)
        self.assertEqual(state["events_dropped"] + len(events), state["event_sequence"])
        self.assertEqual([event["seq"] for event in events],
                         list(range(state["events_dropped"] + 1, state["event_sequence"] + 1)))
        self.assertEqual(sorted(event["monotonic_ns"] for event in events),
                         [event["monotonic_ns"] for event in events])
        self.assertLessEqual(events[-1]["monotonic_ns"], state["observed_monotonic_ns"])
        self.assertEqual(sum(sum(window["event_totals"].values()) for window in state["windows"]),
                         state["event_sequence"])

    def test_private_permissions_and_complete_snapshot_replacement(self):
        directory, _ = self.probe()
        self.assertEqual(sorted(path.name for path in directory.iterdir()), ["state.json"])
        for path, expected in ((directory, 0o700), (directory / "state.json", 0o600)):
            if sys.platform == "win32":
                self.assertTrue(windows_private_acl(path))
            else:
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), expected)
                self.assertEqual(path.stat().st_uid, os.getuid())

    def test_existing_directory_is_rejected_without_overwriting(self):
        directory = self.parent / "existing"
        directory.mkdir()
        sentinel = directory / "state.json"
        sentinel.write_text("do not overwrite", encoding="utf-8")
        self.run_binary("--offline-probe", "--state-dir", str(directory), success=False)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "do not overwrite")

    def test_symlink_directory_is_rejected(self):
        target = self.parent / "target"
        target.mkdir()
        link = self.parent / "link"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlink creation unavailable: {error}")
        self.run_binary("--offline-probe", "--state-dir", str(link), success=False)
        self.assertEqual(list(target.iterdir()), [])

    def test_bad_arguments_fail_before_state_creation(self):
        for arguments in (("--role", "invalid"), ("--unknown",), ("--role",), ("--state-dir", "relative")):
            with self.subTest(arguments=arguments):
                self.run_binary("--offline-probe", *arguments, success=False)
        self.assertEqual(sorted(path.name for path in self.parent.iterdir()), ["profile"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    arguments = parser.parse_args()
    FixtureProtocolTests.binary = arguments.binary.resolve(strict=True)
    FixtureProtocolTests.source = (arguments.source or Path(__file__).with_name(
        "window_fixture_windows.cpp" if sys.platform == "win32" else "window_fixture_macos.m")).resolve(strict=True)
    unittest.main(argv=[sys.argv[0]], verbosity=2)
