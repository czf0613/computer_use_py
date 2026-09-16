"""Release validation with disposable wheel archives, never native execution."""

import importlib.util
import struct
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "validate_windows_wheels", ROOT / ".github/scripts/validate_windows_wheels.py"
)
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def wheel_set(directory, platform, *, machine=None, missing_guide=False):
    machine = machine or {"win_amd64": 0x8664, "win_arm64": 0xAA64}[platform]
    binary = bytearray(256)
    binary[:2] = b"MZ"
    struct.pack_into("<I", binary, 0x3C, 128)
    binary[128:132] = b"PE\0\0"
    struct.pack_into("<H", binary, 132, machine)
    for interpreter, abi in [
        ("cp310", "cp310"), ("cp311", "cp311"), ("cp312", "cp312"),
        ("cp313", "cp313"), ("cp314", "cp314"),
        ("cp313", "cp313t"), ("cp314", "cp314t"),
    ]:
        path = directory / f"scapkit_computer_use-0.1.2-{interpreter}-{abi}-{platform}.whl"
        with zipfile.ZipFile(path, "w") as archive:
            for name in (
                "scapkit_computer_use/py.typed",
                "scapkit_computer_use/screen_capture_kit/_scapkit.pyi",
                "scapkit_computer_use_mcp/server.py",
                "scapkit_computer_use_mcp/system.py",
            ):
                archive.writestr(name, "")
            if not missing_guide:
                archive.writestr("scapkit_computer_use_mcp/agent_guide.md", "guide")
            archive.writestr(
                f"scapkit_computer_use/screen_capture_kit/_scapkit.{abi}-{platform}.pyd",
                binary,
            )
            archive.writestr(
                "scapkit_computer_use-0.1.2.dist-info/METADATA",
                "Name: scapkit-computer-use\nVersion: 0.1.2\n"
                "Requires-Python: >=3.10\nLicense-Expression: MIT\n",
            )


@pytest.mark.parametrize("platform", ["win_amd64", "win_arm64"])
def test_complete_windows_wheel_set_including_free_threaded_abis(tmp_path, platform):
    wheel_set(tmp_path, platform)
    validator.validate(tmp_path, platform, "0.1.2")


def test_missing_free_threaded_wheel_blocks_release(tmp_path):
    wheel_set(tmp_path, "win_amd64")
    next(tmp_path.glob("*-cp314t-*.whl")).unlink()
    with pytest.raises(AssertionError, match="Expected seven wheels"):
        validator.validate(tmp_path, "win_amd64", "0.1.2")


def test_mislabeled_native_architecture_blocks_release(tmp_path):
    wheel_set(tmp_path, "win_arm64", machine=0x8664)
    with pytest.raises(AssertionError, match="PE machine"):
        validator.validate(tmp_path, "win_arm64", "0.1.2")


def test_stale_version_blocks_release(tmp_path):
    wheel_set(tmp_path, "win_amd64")
    with pytest.raises(AssertionError):
        validator.validate(tmp_path, "win_amd64", "0.1.3")


def test_missing_packaged_guide_blocks_release(tmp_path):
    wheel_set(tmp_path, "win_amd64", missing_guide=True)
    with pytest.raises(AssertionError, match="agent_guide.md"):
        validator.validate(tmp_path, "win_amd64", "0.1.2")


def test_duplicate_abi_cannot_replace_a_missing_wheel(tmp_path):
    wheel_set(tmp_path, "win_amd64")
    wheel = next(tmp_path.glob("*-cp314t-*.whl"))
    wheel.rename(tmp_path / "scapkit_computer_use-0.1.2-1-cp310-cp310-win_amd64.whl")
    with pytest.raises(AssertionError, match="Duplicate ABI"):
        validator.validate(tmp_path, "win_amd64", "0.1.2")
