"""Host metadata without desktop access or shell initialization."""

import os
import platform
from typing import Any

from scapkit_computer_use import process


def coordinate_unit(system: str) -> str | None:
    return {"Darwin": "global display points", "Windows": "physical_pixel"}.get(system)


def system_info(system: str) -> dict[str, Any]:
    if system == "Darwin":
        os_version = platform.mac_ver()[0]
    elif system == "Windows":
        os_version = platform.win32_ver()[1]
    else:
        os_version = platform.release()
    windows = system == "Windows"
    return {
        "platform": system,
        "os_name": "macOS" if system == "Darwin" else system,
        "os_version": os_version or None,
        "architecture": platform.machine() or None,
        "desktop_supported": system in {"Darwin", "Windows"},
        "coordinate_unit": coordinate_unit(system),
        "subprocess": {
            "path_style": "windows" if windows else "posix",
            "default_cwd": os.getcwd(),
            # Share the runner's resolver, including Windows console/OEM fallback.
            "default_encoding": process._default_encoding(),
            "arguments_are_literal": True,
            "inherits_server_environment": False,
            "environment_source": (
                "user/system environment block, then cmd AutoRun"
                if windows
                else "clean login + interactive shell from the system account"
            ),
            # This is an invocation example, not the environment bootstrap shell.
            "shell_example": {
                "executable": "cmd.exe" if windows else (
                    "/bin/zsh" if system == "Darwin" else "/bin/sh"
                ),
                "args": ["/c" if windows else "-c", "echo hello"],
            },
        },
    }
