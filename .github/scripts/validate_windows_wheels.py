"""Validate release wheels without loading their native code on this interpreter."""

import os
import struct
import sys
import zipfile
from email.parser import BytesParser
from pathlib import Path

from packaging.tags import Tag
from packaging.utils import parse_wheel_filename
from packaging.version import Version


def validate(directory: Path, platform: str, version: str) -> None:
    machine = {"win_amd64": 0x8664, "win_arm64": 0xAA64}[platform]
    expected = {
        Tag(f"cp{minor}", f"cp{minor}", platform)
        for minor in ("310", "311", "312", "313", "314")
    } | {Tag(f"cp{minor}", f"cp{minor}t", platform) for minor in ("313", "314")}
    wheels = sorted(directory.glob("*.whl"))
    assert len(wheels) == len(expected), f"Expected seven wheels, found {wheels}"
    found = set()
    required = {
        "scapkit_computer_use/py.typed",
        "scapkit_computer_use/screen_capture_kit/_scapkit.pyi",
        "scapkit_computer_use_mcp/server.py",
        "scapkit_computer_use_mcp/system.py",
        "scapkit_computer_use_mcp/agent_guide.md",
    }
    for wheel in wheels:
        name, wheel_version, _, tags = parse_wheel_filename(wheel.name)
        assert name == "scapkit-computer-use", wheel.name
        assert wheel_version == Version(version), wheel.name
        assert len(tags) == 1 and tags <= expected, wheel.name
        assert not found.intersection(tags), f"Duplicate ABI: {wheel.name}"
        found.update(tags)
        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
            assert required <= names, f"{wheel.name}: missing {required - names}"
            assert not any(name.startswith("tests/") for name in names), wheel.name
            native_paths = [name for name in names if name.endswith(".pyd")]
            assert len(native_paths) == 1, f"{wheel.name}: {native_paths}"
            assert native_paths[0].startswith(
                "scapkit_computer_use/screen_capture_kit/_scapkit."
            ), native_paths
            binary = archive.read(native_paths[0])
            assert binary[:2] == b"MZ", wheel.name
            pe_offset = struct.unpack_from("<I", binary, 0x3C)[0]
            assert binary[pe_offset : pe_offset + 4] == b"PE\0\0", wheel.name
            actual_machine = struct.unpack_from("<H", binary, pe_offset + 4)[0]
            assert actual_machine == machine, (
                f"{wheel.name}: PE machine {actual_machine:#x}, expected {machine:#x}"
            )
            metadata_path, = [name for name in names if name.endswith(".dist-info/METADATA")]
            metadata = BytesParser().parsebytes(archive.read(metadata_path))
            assert metadata["Version"] == version, wheel.name
            assert metadata["Requires-Python"] == ">=3.10", wheel.name
            assert metadata["License-Expression"] == "MIT", wheel.name
    assert found == expected, f"Missing ABIs: {expected - found}"
    print(f"Validated all seven {platform} wheels for {version}")


if __name__ == "__main__":
    validate(Path(sys.argv[1]), os.environ["EXPECTED_PLATFORM"], os.environ["EXPECTED_VERSION"])
