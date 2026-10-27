"""Disposable fake-only stdio server for window protocol lifecycle tests."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from window_fakes import FakeBackend

from scapkit_computer_use import window
from scapkit_computer_use_mcp.device import Device
from scapkit_computer_use_mcp.server import create_server


class NoDesktop:
    def __getattr__(self, name):
        raise AssertionError("legacy desktop API reached")


if __name__ == "__main__":
    backend = FakeBackend()
    window._load_native = lambda: backend
    try:
        create_server(device=Device(computer=NoDesktop(), system="Darwin")).run("stdio")
    finally:
        Path(sys.argv[1]).write_text(
            json.dumps(
                {
                    "opened": len(backend.handles),
                    "closed": backend.close_count,
                    "all_closed": all(h.closed for h in backend.handles),
                }
            )
        )
