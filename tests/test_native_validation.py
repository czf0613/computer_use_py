"""Native boundary tests: never capture the desktop or post input events."""
import subprocess
import sys


def test_invalid_permission_argument_raises_without_crashing():
    result = subprocess.run(
        [sys.executable, "-c", """
from scapkit_computer_use.screen_capture_kit import _scapkit
try:
    _scapkit.check_permission(object())
except TypeError:
    pass
else:
    raise AssertionError('expected TypeError')
"""],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_import_preserves_free_threading():
    result = subprocess.run(
        [sys.executable, "-c", """
import sys, sysconfig
import scapkit_computer_use
if sysconfig.get_config_var('Py_GIL_DISABLED'):
    assert not sys._is_gil_enabled(), 'extension enabled the GIL'
"""],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
